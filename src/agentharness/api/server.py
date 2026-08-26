"""Minimal FastAPI control plane for the Web-first Agent Runtime."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agentharness import __version__
from agentharness.api.compatibility import API_CAPABILITIES, API_SCHEMA_VERSION
from agentharness.api.internal_agent import internal_agent_router
from agentharness.api.reporting import build_run_report
from agentharness.config import resolve_internal_token
from agentharness.harness import Harness

LEASE_SWEEP_INTERVAL_S = 30.0

logger = logging.getLogger("agentharness.api.server")


async def _sweep_expired_leases(harness: Harness, interval_s: float | None = None) -> None:
    period = LEASE_SWEEP_INTERVAL_S if interval_s is None else interval_s
    while True:
        await asyncio.sleep(period)
        try:
            recovered = await asyncio.to_thread(harness.storage.recover_expired_run_leases)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a failed sweep cannot stop the Web process
            # 静默吞掉会让租约恢复"看似在工作实则一直失败"，必须留痕
            logger.exception("lease sweep failed; expired run leases not recovered")
            continue
        if recovered:
            logger.info("recovered expired run leases: %s", recovered)
            harness.recovered_run_ids = list(
                dict.fromkeys([*harness.recovered_run_ids, *recovered])
            )


def _contained_file(root: Path, relative: str) -> Path | None:
    if not relative or "\x00" in relative:
        return None
    candidate = Path(relative)
    if candidate.is_absolute() or candidate.drive or candidate.root:
        return None
    try:
        resolved_root = root.resolve()
        resolved = (resolved_root / candidate).resolve()
    except OSError:
        return None
    if resolved_root not in resolved.parents:
        return None
    try:
        if resolved.is_symlink() or not resolved.is_file():
            return None
    except OSError:
        return None
    return resolved


def _dev_cors_origins() -> list[str]:
    return [
        value.strip()
        for value in os.environ.get("AGENTHARNESS_CORS_ORIGINS", "").split(",")
        if value.strip()
    ]


def _resolve_web_dist(web_dist: Path | str | None) -> Path:
    source = Path(__file__).resolve().parents[3] / "web" / "dist"
    if web_dist:
        return Path(web_dist)
    return source


def _load_web_build_id(dist: Path) -> str | None:
    try:
        payload = json.loads((dist / "build-meta.json").read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None
    value = payload.get("web_build_id") if isinstance(payload, dict) else None
    return value if isinstance(value, str) and value.strip() else None


def create_app(
    harness: Harness | None = None,
    data_dir: str | Path | None = None,
    web_dist: Path | str | None = None,
    *,
    workspace_roots: list[str | Path] | None = None,
    execution_enabled: bool = True,
    internal_only: bool | None = None,
) -> FastAPI:
    owns_harness = harness is None
    runtime = harness or Harness(data_dir=data_dir)
    internal_mode = (
        os.environ.get("AGENTHARNESS_INTERNAL_ONLY", "").strip().lower()
        in {"1", "true", "yes", "on"}
        if internal_only is None
        else internal_only
    )
    # 共享解析：空/未设置 env 时回退进程内随机 token（弱默认值已移除，空 token 无法通过认证）
    internal_token = resolve_internal_token()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        sweeper = asyncio.create_task(_sweep_expired_leases(runtime))
        try:
            yield
        finally:
            sweeper.cancel()
            with suppress(asyncio.CancelledError):
                await sweeper
            if owns_harness:
                await runtime.aclose()

    app = FastAPI(
        title="采价台采购询价 API",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.harness = runtime
    redactor = runtime.redactor
    public_redact = redactor.redact_public_obj
    dist = _resolve_web_dist(web_dist)
    web_build_id = _load_web_build_id(dist)
    server_started_at = datetime.now(UTC).isoformat()

    origins = _dev_cors_origins()
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "HEAD", "OPTIONS", "POST", "PUT", "DELETE"],
            allow_headers=["Content-Type", "Last-Event-ID"],
        )

    def allowed_write(request: Request) -> bool:
        path = request.url.path
        if path == "/internal/v1/commands":
            return True
        if path == "/internal/v1/config":
            return True
        return False

    @app.middleware("http")
    async def restrict_writes(request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        needs_internal_token = path.startswith("/internal/v1/") or (
            internal_mode and path != "/api/health"
        )
        if needs_internal_token and not hmac.compare_digest(
            # encode 后比较：非 ASCII 头会让 compare_digest 抛 TypeError 变 500，
            # 拒绝语义应稳定为 401
            request.headers.get("X-Agent-Internal-Token", "").encode("utf-8"),
            internal_token.encode("utf-8"),
        ):
            return JSONResponse(
                {
                    "code": "invalid_internal_token",
                    "message": "X-Agent-Internal-Token is required",
                    "status": 401,
                },
                status_code=401,
            )
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not allowed_write(request):
            return JSONResponse(
                {"detail": "Write method is not allowed for this resource"},
                status_code=405,
                headers={"Allow": "GET, HEAD, OPTIONS"},
            )
        return await call_next(request)

    app.include_router(internal_agent_router(runtime))

    @app.get("/api/health")
    async def health(response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        # SQLite 为同步存储：读路径统一 to_thread，避免写锁竞争时阻塞事件循环
        max_global_seq = await asyncio.to_thread(runtime.storage.max_global_seq)
        return redactor.redact_obj(
            {
                "service": "agentharness",
                "status": "ok",
                "backend_version": __version__,
                "api_schema_version": API_SCHEMA_VERSION,
                "api_capabilities": list(API_CAPABILITIES),
                "web_build_id": web_build_id,
                "server_started_at": server_started_at,
                "data_dir": str(runtime.data_dir.resolve()),
                "max_global_seq": max_global_seq,
                "internal_only": internal_mode,
            }
        )

    @app.get("/api/runtime")
    async def runtime_info(response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        run_count = await asyncio.to_thread(lambda: len(runtime.list_runs()))
        return public_redact(
            {
                "mode": "procurement_control_plane",
                "execution_enabled": False,
                "providers": sorted(runtime.providers),
                "tools": sorted(runtime.tools),
                "runs": run_count,
            }
        )

    @app.get("/api/sessions")
    async def sessions(limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(runtime.list_sessions, limit=limit)
        return public_redact(rows)

    @app.get("/api/sessions/{session_id}/transcript")
    async def transcript(session_id: str) -> list[dict[str, Any]]:
        if await asyncio.to_thread(runtime.get_session, session_id) is None:
            raise HTTPException(404, "session not found")
        items = await asyncio.to_thread(runtime.get_session_transcript, session_id)
        return public_redact([item.model_dump(mode="json") for item in items])

    @app.get("/api/runs")
    async def runs(
        session_id: str | None = None,
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(
            runtime.list_runs, session_id=session_id, limit=limit, offset=offset
        )
        return public_redact(rows)

    @app.get("/api/runs/{run_id}")
    async def run(run_id: str) -> dict[str, Any]:
        row = await asyncio.to_thread(runtime.get_run, run_id)
        if row is None:
            raise HTTPException(404, "run not found")
        return public_redact(row)

    @app.get("/api/runs/{run_id}/report")
    async def run_report(run_id: str) -> dict[str, Any]:
        # build_run_report 拉 10k 事件 + 逐 artifact/invocation 查询，长 Run 秒级耗时，
        # 必须离开事件循环执行
        report = await asyncio.to_thread(build_run_report, runtime, run_id)
        if report is None:
            raise HTTPException(404, "run not found")
        return public_redact(report)

    @app.get("/api/runs/{run_id}/messages")
    async def messages(run_id: str) -> list[dict[str, Any]]:
        if await asyncio.to_thread(runtime.get_run, run_id) is None:
            raise HTTPException(404, "run not found")
        items = await asyncio.to_thread(runtime.get_run_messages, run_id)
        return public_redact([item.model_dump(mode="json") for item in items])

    @app.get("/api/runs/{run_id}/events")
    async def events(
        run_id: str,
        after: int = Query(0, ge=0),
        limit: int = Query(500, ge=1, le=5_000),
    ) -> list[dict[str, Any]]:
        if await asyncio.to_thread(runtime.get_run, run_id) is None:
            raise HTTPException(404, "run not found")
        items = await asyncio.to_thread(
            runtime.get_events, run_id=run_id, after_global_seq=after, limit=limit
        )
        return public_redact([item.model_dump(mode="json") for item in items])

    @app.get("/api/runs/{run_id}/approvals")
    async def approvals(run_id: str) -> list[dict[str, Any]]:
        if await asyncio.to_thread(runtime.get_run, run_id) is None:
            raise HTTPException(404, "run not found")
        rows = await asyncio.to_thread(runtime.list_approvals, run_id)
        return public_redact(rows)

    @app.get("/api/runs/{run_id}/tool-invocations")
    async def tool_invocations(run_id: str) -> list[dict[str, Any]]:
        if await asyncio.to_thread(runtime.get_run, run_id) is None:
            raise HTTPException(404, "run not found")
        items = await asyncio.to_thread(runtime.list_tool_invocations, run_id)
        return public_redact([item.model_dump(mode="json") for item in items])

    @app.get("/api/tool-invocations/{invocation_id}")
    async def tool_invocation(invocation_id: str) -> dict[str, Any]:
        item = await asyncio.to_thread(runtime.get_tool_invocation, invocation_id)
        if item is None:
            raise HTTPException(404, "tool invocation not found")
        payload = item.model_dump(mode="json")
        payload["attempts_audit"] = await asyncio.to_thread(
            runtime.list_tool_attempts, invocation_id
        )
        return public_redact(payload)

    @app.get("/api/runs/{run_id}/checkpoint")
    async def checkpoint(run_id: str) -> dict[str, Any] | None:
        if await asyncio.to_thread(runtime.get_run, run_id) is None:
            raise HTTPException(404, "run not found")
        value = await asyncio.to_thread(runtime.get_checkpoint, run_id)
        return public_redact(value.model_dump(mode="json")) if value else None

    @app.get("/api/artifacts/{artifact_id}")
    async def artifact(artifact_id: str) -> dict[str, Any]:
        value = await asyncio.to_thread(runtime.get_artifact, artifact_id)
        if value is None:
            raise HTTPException(404, "artifact not found")
        payload: dict[str, Any] = {
            "id": value["id"],
            "sha256": value["sha256"],
            "content_type": value.get("content_type"),
            "size_bytes": value.get("size_bytes"),
            "summary": redactor.redact_public_text(value.get("summary") or ""),
            "created_at": value.get("created_at"),
        }
        text = await asyncio.to_thread(runtime.storage.artifacts.get_text, value["sha256"])
        if text is not None:
            payload["content"] = redactor.redact_public_text(text[:100_000])
        return public_redact(payload)

    @app.get("/api/artifacts/{artifact_id}/raw")
    async def raw_artifact(artifact_id: str) -> Response:
        value = await asyncio.to_thread(runtime.get_artifact, artifact_id)
        if value is None:
            raise HTTPException(404, "artifact not found")
        content = await asyncio.to_thread(
            runtime.storage.artifacts.get_bytes, value["sha256"]
        )
        if content is None:
            raise HTTPException(404, "artifact content not found")
        content_type = str(value.get("content_type") or "application/octet-stream")
        extension = {
            "application/pdf": ".pdf",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
            "application/json": ".json",
            "text/plain": ".txt",
        }.get(content_type, ".bin")
        disposition = "inline" if content_type in {"application/pdf", "application/json", "text/plain"} else "attachment"
        return Response(
            content=content,
            media_type=content_type,
            headers={
                "Content-Disposition": f'{disposition}; filename="artifact-{artifact_id[:12]}{extension}"',
                "Content-Security-Policy": "sandbox",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/stream")
    async def stream(
        request: Request,
        last_event_id: str | None = Header(None, alias="Last-Event-ID"),
        after: int | None = Query(None),
    ) -> StreamingResponse:
        cursor = (
            after if after is not None else await asyncio.to_thread(runtime.storage.max_global_seq)
        )
        if last_event_id:
            with suppress(ValueError):
                cursor = max(cursor, int(last_event_id))

        async def generate():  # type: ignore[no-untyped-def]
            queue: asyncio.Queue[Any] = asyncio.Queue()
            loop = asyncio.get_running_loop()

            def on_event(event: Any) -> None:
                # 引擎在事件循环线程内回调；Kafka 线程等场景用 threadsafe 投递
                loop.call_soon_threadsafe(queue.put_nowait, event)

            unsubscribe = runtime.subscribe_events(on_event)
            nonlocal cursor
            idle = 0
            try:
                # 1) 先补齐游标之后已有的事件（分页拉完）
                rows = await asyncio.to_thread(
                    runtime.get_events, after_global_seq=cursor, limit=200
                )
                while rows:
                    for event in rows:
                        cursor = max(cursor, event.global_seq)
                        payload = public_redact(event.model_dump(mode="json"))
                        yield (
                            f"id: {event.global_seq}\n"
                            f"event: {event.type}\n"
                            f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
                        )
                    if len(rows) < 200:
                        break
                    rows = await asyncio.to_thread(
                        runtime.get_events, after_global_seq=cursor, limit=200
                    )
                # 2) 实时推送为主，心跳期做一次 DB 补偿轮询兜底（订阅间隙不丢事件）
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=2.0)
                    except TimeoutError:
                        yield ": heartbeat\n\n"
                        idle += 1
                        if idle > 3 and request.headers.get("x-test-short-stream") == "1":
                            break
                        rows = await asyncio.to_thread(
                            runtime.get_events, after_global_seq=cursor, limit=200
                        )
                        for event in rows:
                            cursor = max(cursor, event.global_seq)
                            payload = public_redact(event.model_dump(mode="json"))
                            yield (
                                f"id: {event.global_seq}\n"
                                f"event: {event.type}\n"
                                f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
                            )
                        continue
                    idle = 0
                    if event.global_seq <= cursor:
                        continue
                    cursor = event.global_seq
                    payload = public_redact(event.model_dump(mode="json"))
                    yield (
                        f"id: {event.global_seq}\n"
                        f"event: {event.type}\n"
                        f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
                    )
            finally:
                unsubscribe()

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    if not internal_mode and dist.is_dir() and (dist / "index.html").is_file():
        assets = dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(
                dist / "index.html",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )

        root = dist.resolve()

        @app.get("/{full_path:path}")
        async def spa(full_path: str) -> Response:
            if full_path.startswith("api/"):
                raise HTTPException(404)
            safe = _contained_file(root, full_path)
            if safe is not None:
                return FileResponse(safe)
            return FileResponse(
                dist / "index.html",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )

    return app


def serve(
    data_dir: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8741,
    harness: Harness | None = None,
    *,
    workspace_roots: list[str | Path] | None = None,
    execution_enabled: bool = True,
) -> None:
    import uvicorn

    uvicorn.run(
        create_app(
            harness=harness,
            data_dir=data_dir,
            workspace_roots=workspace_roots,
            execution_enabled=execution_enabled,
        ),
        host=host,
        port=port,
        log_level="info",
    )
