import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from ..application.api import Application
from .assets import configure_asset_types


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(max_length=100)
    params: dict = Field(default_factory=dict)


class AgentRequest(BaseModel):
    project_id: str
    content: str = Field(min_length=1, max_length=16000)
    question_id: str | None = None
    attachment_ids: list[str] = Field(default_factory=list, max_length=6)
    verification_milestone: str | None = None


def create_app(application: Application, dist: Path | None = None):
    configure_asset_types()
    app = FastAPI(title="EvoGraph local API", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        allowed_hosts = {"127.0.0.1", "localhost", "testserver"}
        if request.url.hostname not in allowed_hosts:
            return JSONResponse({"detail": "Invalid host"}, status_code=403)
        if request.url.path.startswith("/api"):
            origin = request.headers.get("origin")
            if origin and origin not in {
                "http://127.0.0.1:5173",
                "http://localhost:5173",
                "http://127.0.0.1:8765",
                "http://localhost:8765",
                str(request.base_url).rstrip("/"),
            }:
                return JSONResponse({"detail": "Invalid origin"}, status_code=403)
            if int(request.headers.get("content-length", "0")) > 12_000_000:
                return JSONResponse({"detail": "Request too large"}, status_code=413)
        return await call_next(request)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.post("/api/command")
    async def command(payload: Command):
        return await application.dispatch(payload.action, payload.params)

    @app.post("/api/agent/stream")
    async def agent_stream(payload: AgentRequest):
        async def events():
            async for event in application.agent.stream(**payload.model_dump()):
                yield json.dumps(event, ensure_ascii=False) + "\n"

        return StreamingResponse(
            events(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    if dist and dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app
