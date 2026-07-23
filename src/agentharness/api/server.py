"""FastAPI readonly API + SSE — GET only, bind 127.0.0.1 by default."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agentharness.harness import Harness


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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "HEAD", "OPTIONS"],
        allow_headers=["*"],
    )

    # Reject all write methods globally
    @app.middleware("http")
    async def reject_writes(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            # Allow OPTIONS for CORS preflight
            return JSONResponse(
                {"detail": "Write methods are not allowed (readonly API)"},
                status_code=405,
                headers={"Allow": "GET, HEAD, OPTIONS"},
            )
        return await call_next(request)

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return redactor.redact_obj({
            "service": "agentharness",
            "status": "ok",
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
    ) -> list[dict[str, Any]]:
        return redactor.redact_obj(
            harness.list_runs(session_id=session_id, limit=limit)
        )

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        run = harness.get_run(run_id)
        if not run:
            raise HTTPException(404, "run not found")
        return redactor.redact_obj(run)

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

    @app.get("/api/runs/{run_id}/approvals")
    async def run_approvals(run_id: str) -> list[dict[str, Any]]:
        if not harness.get_run(run_id):
            raise HTTPException(404, "run not found")
        return redactor.redact_obj(harness.list_approvals(run_id))

    @app.get("/api/runs/{run_id}/checkpoint")
    async def run_checkpoint(run_id: str) -> dict[str, Any]:
        if not harness.get_run(run_id):
            raise HTTPException(404, "run not found")
        checkpoint = harness.get_checkpoint(run_id)
        if checkpoint is None:
            raise HTTPException(404, "checkpoint not found")
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
        """SSE stream by global_seq with Last-Event-ID, replay, dedupe, heartbeat."""
        start_seq = 0
        if after is not None:
            start_seq = after
        elif last_event_id:
            try:
                start_seq = int(last_event_id)
            except ValueError:
                start_seq = 0

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
    packaged_dist = Path(__file__).resolve().parents[1] / "web_dist"
    source_dist = Path(__file__).resolve().parents[3] / "web" / "dist"
    if web_dist:
        dist = Path(web_dist)
    elif (packaged_dist / "index.html").is_file():
        dist = packaged_dist
    else:
        dist = source_dist
    if dist.is_dir() and (dist / "index.html").exists():
        assets = dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(dist / "index.html")

        @app.get("/{full_path:path}")
        async def spa(full_path: str) -> Response:
            # API already handled; serve SPA for UI routes
            if full_path.startswith("api/"):
                raise HTTPException(404)
            candidate = dist / full_path
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

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
