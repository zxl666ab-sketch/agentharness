"""FastAPI observer API + SSE with one narrow manual-grade action."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agentharness import __version__
from agentharness.api.compatibility import API_CAPABILITIES, API_SCHEMA_VERSION
from agentharness.harness import Harness


class GradeRequest(BaseModel):
    mode: Literal["deterministic", "ai"] = "deterministic"


def _dev_cors_origins() -> list[str]:
    """Explicit dev origins from AGENTHARNESS_CORS_ORIGINS (comma-separated).

    Empty by default: the web UI is served same-origin, so no cross-origin access
    is granted in production. A wildcard (``*``) is never used — cross-origin is an
    explicit, operator-set opt-in only.
    """
    import os

    raw = os.environ.get("AGENTHARNESS_CORS_ORIGINS", "")
    return [o.strip() for o in raw.split(",") if o.strip()]


def _resolve_web_dist(web_dist: Path | str | None) -> Path:
    packaged_dist = Path(__file__).resolve().parents[1] / "web_dist"
    source_dist = Path(__file__).resolve().parents[3] / "web" / "dist"
    if web_dist:
        return Path(web_dist)
    if (packaged_dist / "index.html").is_file():
        return packaged_dist
    return source_dist


def _load_web_build_id(dist: Path) -> str | None:
    """Read once at process startup so an in-place Web rebuild becomes detectable."""
    try:
        payload = json.loads((dist / "build-meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    build_id = payload.get("web_build_id") if isinstance(payload, dict) else None
    return build_id if isinstance(build_id, str) and build_id.strip() else None


def create_app(
    harness: Harness | None = None,
    data_dir: str | Path | None = None,
    web_dist: Path | str | None = None,
) -> FastAPI:
    owns_harness = harness is None
    if harness is None:
        harness = Harness(data_dir=data_dir)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            if owns_harness:
                await harness.aclose()

    app = FastAPI(
        title="Agent Harness Console API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.harness = harness
    redactor = harness.redactor
    grade_lock = asyncio.Lock()
    dist = _resolve_web_dist(web_dist)
    web_build_id = _load_web_build_id(dist)
    server_started_at = datetime.now(UTC).isoformat()

    # Same-origin by default (no CORS headers). Cross-origin access is only granted
    # to explicit dev origins set via AGENTHARNESS_CORS_ORIGINS — never a wildcard.
    cors_origins = _dev_cors_origins()
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["GET", "HEAD", "OPTIONS", "POST"],
            allow_headers=["*"],
        )

    # Reject all write methods globally except the one narrow, deterministic
    # manual-grade action used by the local Inspector.
    @app.middleware("http")
    async def reject_writes(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            path = request.url.path
            is_grade = (
                request.method == "POST"
                and path.startswith("/api/runs/")
                and path.endswith("/grade")
                and len(path.removeprefix("/api/runs/").removesuffix("/grade").strip("/")) > 0
            )
            if is_grade:
                return await call_next(request)
            # Allow OPTIONS for CORS preflight
            return JSONResponse(
                {"detail": "Write methods are not allowed (readonly API)"},
                status_code=405,
                headers={"Allow": "GET, HEAD, OPTIONS"},
            )
        return await call_next(request)

    @app.get("/api/health")
    async def health(response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return redactor.redact_obj({
            "service": "agentharness",
            "status": "ok",
            "backend_version": __version__,
            "api_schema_version": API_SCHEMA_VERSION,
            "api_capabilities": list(API_CAPABILITIES),
            "web_build_id": web_build_id,
            "server_started_at": server_started_at,
            "data_dir": str(harness.data_dir.expanduser().resolve()),
            "max_global_seq": harness.storage.max_global_seq(),
        })

    @app.get("/api/sessions")
    async def sessions(limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
        # list_sessions is enriched with latest_status / latest_run_id for left column
        return redactor.redact_obj(harness.list_sessions(limit=limit))

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, Any]:
        sess = harness.get_session(session_id)
        if not sess:
            raise HTTPException(404, "session not found")
        return redactor.redact_obj(sess)

    @app.get("/api/sessions/{session_id}/transcript")
    async def session_transcript(session_id: str) -> list[dict[str, Any]]:
        sess = harness.get_session(session_id)
        if not sess:
            raise HTTPException(404, "session not found")
        turns = harness.get_session_transcript(session_id)
        return redactor.redact_obj([t.model_dump(mode="json") for t in turns])

    @app.get("/api/runs")
    async def runs(
        session_id: str | None = None,
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ) -> list[dict[str, Any]]:
        return redactor.redact_obj(
            harness.list_runs(session_id=session_id, limit=limit, offset=offset)
        )

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        run = harness.get_run(run_id)
        if not run:
            raise HTTPException(404, "run not found")
        return redactor.redact_obj(run)

    @app.get("/api/runs/{run_id}/evaluation")
    async def run_evaluation(run_id: str) -> dict[str, Any]:
        try:
            payload = harness.get_run_evaluation(run_id)
        except KeyError:
            raise HTTPException(404, "run not found") from None
        return redactor.redact_obj(payload)

    @app.post("/api/runs/{run_id}/grade")
    async def grade_run(
        run_id: str, body: GradeRequest | None = None
    ) -> dict[str, Any]:
        mode = body.mode if body is not None else "deterministic"
        try:
            async with grade_lock:
                eval_payload = await harness.grade_run_async(run_id, mode=mode)
        except KeyError:
            raise HTTPException(404, "run not found") from None
        except TimeoutError as exc:
            raise HTTPException(504, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc
        except ValueError as exc:
            if mode == "ai" and str(exc).startswith("AI judge"):
                raise HTTPException(502, str(exc)) from exc
            raise HTTPException(400, str(exc)) from exc
        run = harness.get_run(run_id)
        return redactor.redact_obj({"eval": eval_payload, "run": run})

    @app.get("/api/runs/{run_id}/events")
    async def run_events(
        run_id: str,
        after: int = Query(0, ge=0),
        limit: int = Query(500, ge=1, le=5000),
    ) -> list[dict[str, Any]]:
        events = harness.get_events(run_id=run_id, after_global_seq=after, limit=limit)
        return redactor.redact_obj([e.model_dump(mode="json") for e in events])

    @app.get("/api/runs/{run_id}/tree")
    async def run_tree(run_id: str) -> list[dict[str, Any]]:
        tree = harness.get_run_tree(run_id)
        if not tree:
            raise HTTPException(404, "run not found")
        return redactor.redact_obj(tree)

    @app.get("/api/runs/{run_id}/messages")
    async def run_messages(run_id: str) -> list[dict[str, Any]]:
        if not harness.get_run(run_id):
            raise HTTPException(404, "run not found")
        messages = harness.get_run_messages(run_id)
        return redactor.redact_obj(
            [message.model_dump(mode="json") for message in messages]
        )

    @app.get("/api/runs/{run_id}/contexts")
    async def run_contexts(run_id: str) -> list[dict[str, Any]]:
        if not harness.get_run(run_id):
            raise HTTPException(404, "run not found")
        return redactor.redact_obj(harness.get_context_manifests(run_id))

    @app.get("/api/runs/{run_id}/approvals")
    async def run_approvals(run_id: str) -> list[dict[str, Any]]:
        if not harness.get_run(run_id):
            raise HTTPException(404, "run not found")
        return redactor.redact_obj(harness.list_approvals(run_id))

    @app.get("/api/runs/{run_id}/checkpoint")
    async def run_checkpoint(run_id: str) -> dict[str, Any] | None:
        if not harness.get_run(run_id):
            raise HTTPException(404, "run not found")
        checkpoint = harness.get_checkpoint(run_id)
        if checkpoint is None:
            # A newly-created or externally-seeded run may legitimately have no
            # checkpoint yet. It is optional run state, not a missing resource.
            return None
        return redactor.redact_obj(checkpoint.model_dump(mode="json"))

    @app.get("/api/artifacts/{artifact_id}")
    async def get_artifact(artifact_id: str) -> dict[str, Any]:
        art = harness.get_artifact(artifact_id)
        if not art:
            raise HTTPException(404, "artifact not found")
        # Do not return raw path content with secrets — summary + meta only unless text fetch
        meta = {
            "id": art["id"],
            "sha256": art["sha256"],
            "content_type": art.get("content_type"),
            "size_bytes": art.get("size_bytes"),
            "summary": redactor.redact_text(art.get("summary") or ""),
            "created_at": art.get("created_at"),
        }
        text = harness.storage.artifacts.get_text(art["sha256"])
        if text is not None:
            meta["content"] = redactor.redact_text(text[:100_000])
        return redactor.redact_obj(meta)

    @app.get("/api/stream")
    async def stream(
        request: Request,
        last_event_id: str | None = Header(None, alias="Last-Event-ID"),
        after: int | None = Query(None),
    ) -> StreamingResponse:
        """SSE stream by global_seq with Last-Event-ID, replay, dedupe, heartbeat.

        Resume-point priority (highest wins):
          1. ``Last-Event-ID`` header — sent automatically by the browser EventSource on
             reconnect (from the ``id:`` lines we emit). Always takes precedence: the stream
             resumes at ``max(after, Last-Event-ID)`` so a reconnect never rewinds behind, nor
             re-replays, events the client already saw.
          2. ``after`` query param — explicit resume cursor for the initial connection.
          3. Neither present — start at the current head (``max_global_seq``), i.e. live-only,
             so a fresh client is not flooded with full history.

        When both ``after`` and ``Last-Event-ID`` are present they are reconciled with
        ``max(...)`` rather than one silently overriding the other; this keeps an idle
        reconnect (stale ``after``, fresh ``Last-Event-ID``) from replaying old events.
        """
        if after is None and not last_event_id:
            start_seq = harness.storage.max_global_seq()
        else:
            start_seq = after or 0
        if last_event_id:
            try:
                # Last-Event-ID wins on conflict: never resume behind what the client acked.
                start_seq = max(start_seq, int(last_event_id))
            except ValueError:
                pass

        async def event_generator():
            cursor = start_seq
            idle_beats = 0
            while True:
                if await request.is_disconnected():
                    break
                events = harness.get_events(after_global_seq=cursor, limit=200)
                if events:
                    idle_beats = 0
                    for ev in events:
                        cursor = max(cursor, ev.global_seq)
                        payload = redactor.redact_obj(ev.model_dump(mode="json"))
                        data = json.dumps(payload, ensure_ascii=False, default=str)
                        yield f"id: {ev.global_seq}\nevent: {ev.type}\ndata: {data}\n\n"
                    await asyncio.sleep(0.05)
                else:
                    # heartbeat
                    yield ": heartbeat\n\n"
                    idle_beats += 1
                    # ASGI test clients may never signal disconnect; cap idle stream
                    if idle_beats > 3 and request.headers.get("x-test-short-stream") == "1":
                        break
                    await asyncio.sleep(0.25)


        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # Static web UI if built
    if dist.is_dir() and (dist / "index.html").exists():
        assets = dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(
                dist / "index.html",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )

        @app.get("/{full_path:path}")
        async def spa(full_path: str) -> Response:
            # API already handled; serve SPA for UI routes
            if full_path.startswith("api/"):
                raise HTTPException(404)
            candidate = dist / full_path
            if candidate.is_file():
                return FileResponse(candidate)
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
) -> None:
    import uvicorn

    app = create_app(harness=harness, data_dir=data_dir)
    uvicorn.run(app, host=host, port=port, log_level="info")
