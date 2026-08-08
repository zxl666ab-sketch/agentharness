"""Minimal FastAPI control plane for the procurement workbench."""

from __future__ import annotations

import asyncio
import ipaddress
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
from agentharness.api.execution import (
    WebRunSupervisor,
)
from agentharness.api.procurement import procurement_router
from agentharness.api.reporting import (
    build_run_report,
    build_run_timeline,
    build_usage_summary,
)
from agentharness.harness import Harness
from agentharness.procurement import ProcurementService
from agentharness.procurement.agent import ProcurementAgent

LEASE_SWEEP_INTERVAL_S = 30.0
STREAM_IDLE_TIMEOUT_S = 120.0
MAX_REQUEST_BODY_BYTES = 32 * 1024 * 1024
logger = logging.getLogger(__name__)


def _is_loopback(host: str) -> bool:
    """True for localhost / loopback literals; mirrors web_main without importing it."""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def _sweep_expired_leases(harness: Harness, interval_s: float | None = None) -> None:
    period = LEASE_SWEEP_INTERVAL_S if interval_s is None else interval_s
    while True:
        await asyncio.sleep(period)
        try:
            recovered = await asyncio.to_thread(harness.storage.recover_expired_run_leases)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a failed sweep cannot stop the Web process
            logger.warning("Failed to sweep expired run leases", exc_info=True)
            continue
        if recovered:
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
    packaged = Path(__file__).resolve().parents[1] / "web_dist"
    source = Path(__file__).resolve().parents[3] / "web" / "dist"
    if web_dist:
        return Path(web_dist)
    return packaged if (packaged / "index.html").is_file() else source


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
) -> FastAPI:
    owns_harness = harness is None
    runtime = harness or Harness(data_dir=data_dir)
    supervisor = WebRunSupervisor(
        runtime,
        workspace_roots=workspace_roots,
        execution_enabled=execution_enabled,
    )
    procurement = ProcurementService(runtime)
    procurement_agent = ProcurementAgent(
        runtime,
        procurement,
        approval_broker=supervisor.approvals,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        sweeper = asyncio.create_task(_sweep_expired_leases(runtime))
        try:
            yield
        finally:
            sweeper.cancel()
            with suppress(asyncio.CancelledError):
                await sweeper
            await procurement_agent.aclose()
            await supervisor.aclose()
            if owns_harness:
                await runtime.aclose()

    app = FastAPI(
        title="采价台采购询价 API",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.harness = runtime
    app.state.run_supervisor = supervisor
    app.state.procurement_service = procurement
    app.state.procurement_agent = procurement_agent
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
            allow_methods=["GET", "HEAD", "OPTIONS", "POST"],
            allow_headers=["Content-Type", "Last-Event-ID"],
        )

    def allowed_write(request: Request) -> bool:
        if request.method != "POST":
            return False
        path = request.url.path
        if path == "/api/procurement/conversations":
            return True
        if path == "/api/procurement/demo" or path == "/api/procurement/demo/clean":
            return True
        if path == "/api/procurement/config":
            return True
        if path == "/api/procurement/requests" or path.startswith("/api/procurement/requests/"):
            return True
        return False

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        return response

    @app.middleware("http")
    async def restrict_writes(request: Request, call_next):  # type: ignore[no-untyped-def]
        if (
            request.method == "POST"
            and request.url.path.startswith("/api/procurement/")
            and not supervisor.execution_enabled
        ):
            return JSONResponse(
                {"detail": "Web execution is disabled for this server"},
                status_code=403,
            )
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not allowed_write(request):
            return JSONResponse(
                {"detail": "Write method is not allowed for this resource"},
                status_code=405,
                headers={"Allow": "GET, HEAD, OPTIONS"},
            )
        return await call_next(request)

    @app.middleware("http")
    async def limit_request_body(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Reject oversized request bodies before pydantic reads them into memory."""
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_REQUEST_BODY_BYTES:
                    return JSONResponse(
                        {"detail": "Request body too large"}, status_code=413
                    )
            except ValueError:
                # Malformed header: let the server handle it downstream.
                pass
        return await call_next(request)

    app.include_router(procurement_router(procurement, procurement_agent))

    @app.get("/api/health")
    async def health(response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return redactor.redact_public_obj(
            {
                "service": "agentharness",
                "status": "ok",
                "backend_version": __version__,
                "api_schema_version": API_SCHEMA_VERSION,
                "api_capabilities": list(API_CAPABILITIES),
                "web_build_id": web_build_id,
                "server_started_at": server_started_at,
                "data_dir": str(runtime.data_dir.resolve()),
                "max_global_seq": runtime.storage.max_global_seq(),
            }
        )

    @app.get("/api/runs")
    async def runs(
        session_id: str | None = Query(None),
        status: str | None = Query(None),
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        items = runtime.storage.runs.list_runs(
            session_id=session_id or None,
            status=status or None,
            limit=limit,
            offset=offset,
        )
        total = runtime.storage.runs.count_runs(
            session_id=session_id or None,
            status=status or None,
        )
        return public_redact(
            {
                "items": items,
                "total": total,
                "offset": offset,
                "has_more": offset + len(items) < total,
            }
        )

    @app.get("/api/metrics/summary")
    async def metrics_summary() -> dict[str, Any]:
        return public_redact(build_usage_summary(runtime))

    @app.get("/api/runs/{run_id}")
    async def run(run_id: str) -> dict[str, Any]:
        row = runtime.get_run(run_id)
        if row is None:
            raise HTTPException(404, "run not found")
        return public_redact(row)

    @app.get("/api/runs/{run_id}/report")
    async def run_report(run_id: str) -> dict[str, Any]:
        report = build_run_report(runtime, run_id)
        if report is None:
            raise HTTPException(404, "run not found")
        return public_redact(report)

    @app.get("/api/runs/{run_id}/timeline")
    async def run_timeline(
        run_id: str,
        limit: int = Query(1_000, ge=10, le=5_000),
    ) -> dict[str, Any]:
        timeline = build_run_timeline(runtime, run_id, limit=limit)
        if timeline is None:
            raise HTTPException(404, "run not found")
        return public_redact(timeline)

    @app.get("/api/runs/{run_id}/events")
    async def run_events(
        run_id: str,
        offset: int = Query(0, ge=0, le=1_000_000),
        limit: int = Query(500, ge=1, le=2_000),
    ) -> dict[str, Any]:
        """Paginated timeline used when a run has more events than the report window."""
        if runtime.get_run(run_id) is None:
            raise HTTPException(404, "run not found")
        items = runtime.get_events(run_id=run_id, limit=offset + limit)[offset:]
        total = runtime.count_events(run_id)
        return public_redact(
            {
                "items": [item.model_dump(mode="json") for item in items],
                "total": total,
                "offset": offset,
                "has_more": offset + len(items) < total,
            }
        )

    @app.get("/api/runs/{run_id}/messages")
    async def messages(run_id: str) -> list[dict[str, Any]]:
        if runtime.get_run(run_id) is None:
            raise HTTPException(404, "run not found")
        return public_redact(
            [
                item.model_dump(
                    mode="json",
                    exclude={
                        "provider_response_id",
                        "provider_run_id",
                        "provider_phase",
                    },
                )
                for item in runtime.get_run_messages(run_id)
            ]
        )

    @app.get("/api/runs/{run_id}/tool-invocations")
    async def tool_invocations(run_id: str) -> list[dict[str, Any]]:
        if runtime.get_run(run_id) is None:
            raise HTTPException(404, "run not found")
        return public_redact(
            [
                item.model_dump(mode="json")
                for item in runtime.list_tool_invocations(run_id)
            ]
        )

    @app.get("/api/runs/{run_id}/approvals")
    async def approvals(run_id: str) -> list[dict[str, Any]]:
        """Expose procurement approval evidence as a read-only audit view."""
        if runtime.get_run(run_id) is None:
            raise HTTPException(404, "run not found")
        return public_redact(runtime.list_approvals(run_id))

    @app.get("/api/runs/{run_id}/checkpoint")
    async def checkpoint(run_id: str) -> dict[str, Any] | None:
        if runtime.get_run(run_id) is None:
            raise HTTPException(404, "run not found")
        value = runtime.get_checkpoint(run_id)
        return public_redact(value.model_dump(mode="json")) if value else None

    @app.get("/api/artifacts/{artifact_id}")
    async def artifact(artifact_id: str) -> dict[str, Any]:
        value = runtime.get_artifact(artifact_id)
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
        text = runtime.storage.artifacts.get_text(value["sha256"])
        if text is not None:
            payload["content"] = redactor.redact_public_text(text[:100_000])
        return public_redact(payload)

    @app.get("/api/artifacts/{artifact_id}/raw")
    async def raw_artifact(artifact_id: str) -> Response:
        value = runtime.get_artifact(artifact_id)
        if value is None:
            raise HTTPException(404, "artifact not found")
        content = runtime.storage.artifacts.get_bytes(value["sha256"])
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
        after: int | None = Query(None, ge=0),
    ) -> StreamingResponse:
        max_seq = runtime.storage.max_global_seq()
        # Clamp an out-of-range after value so a stale client cannot request a
        # cursor beyond the current event frontier and wedge the stream.
        cursor = max_seq if after is None else min(after, max_seq)
        if last_event_id:
            with suppress(ValueError):
                cursor = max(cursor, int(last_event_id))

        async def generate():  # type: ignore[no-untyped-def]
            nonlocal cursor
            idle_seconds = 0.0
            while not await request.is_disconnected():
                rows = runtime.get_events(after_global_seq=cursor, limit=200)
                if rows:
                    idle_seconds = 0.0
                    for event in rows:
                        cursor = max(cursor, event.global_seq)
                        payload = public_redact(event.model_dump(mode="json"))
                        yield (
                            f"id: {event.global_seq}\n"
                            f"event: {event.type}\n"
                            f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
                        )
                    await asyncio.sleep(0.05)
                else:
                    yield ": heartbeat\n\n"
                    idle_seconds += 0.25
                    if (
                        request.headers.get("x-test-short-stream") == "1"
                        and idle_seconds > 0.75
                    ):
                        break
                    if idle_seconds >= STREAM_IDLE_TIMEOUT_S:
                        break
                    await asyncio.sleep(0.25)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    if dist.is_dir() and (dist / "index.html").is_file():
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
    allow_remote_execution: bool = False,
) -> None:
    # Mirror web_main.validate_bind() without importing web_main (which would
    # create an import cycle): refuse a non-loopback bind unless the operator
    # explicitly opts in and accepts the auth-proxy duty.
    if not _is_loopback(host) and not allow_remote_execution:
        raise SystemExit(
            '拒绝启动：非回环绑定必须显式使用 --allow-remote-execution，'
            '并在前面部署认证代理；否则所有接口（含报价原件与 SSE 事件）'
            '都会在无认证下被网络访问。'
        )
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
