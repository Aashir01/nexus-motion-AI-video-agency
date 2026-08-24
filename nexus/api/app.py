"""FastAPI application factory."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from nexus.api.routers import assets, auth, billing, models, studio, system
from nexus.config import settings
from nexus.db.session import create_all, get_engine
from nexus.providers.registry import close_all
from nexus.util.errors import NexusError
from nexus.util.ids import new_id
from nexus.util.logging import bind, configure_logging, get_logger, unbind
from nexus.worker.bus import bus

log = get_logger(__name__)

DESCRIPTION = """
Nexus Motion turns a one-paragraph brief into a **10-15 minute drama** with a
consistent cast, then hands you the file.

* **Character consistency** — a show bible with canonical reference portraits per
  character, fed as image conditioning into every keyframe, then image-to-video
  for every shot.
* **Model routing** — one API over Anthropic, OpenAI, Google, fal, Replicate,
  Runway, ElevenLabs and more, with automatic fallback from flagship models all
  the way down to free tiers and an offline engine.
* **Resumable productions** — a 130-shot episode checkpoints continuously and
  restarts where it stopped.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    log.info("api_starting", extra={"environment": settings.environment,
                                    "storage": settings.storage_backend})
    if settings.database_url.startswith("sqlite"):
        # Postgres deployments run Alembic; sqlite is the zero-setup dev path.
        await create_all()
    if settings.is_production and settings.secret_key == "dev-insecure-secret-change-me":
        raise RuntimeError("SECRET_KEY must be set to a real value in production")
    yield
    await close_all()
    await bus.aclose()
    await get_engine().dispose()
    log.info("api_stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version="2.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or new_id("req")
        token = bind(request_id=request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            unbind(token)
        elapsed = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        if not request.url.path.startswith(("/assets", "/health")):
            log.info("http_request", extra={
                "method": request.method, "path": request.url.path,
                "status": response.status_code, "duration_ms": round(elapsed, 1),
                "org_id": getattr(request.state, "org_id", None),
            })
        return response

    @app.exception_handler(NexusError)
    async def nexus_error_handler(_: Request, exc: NexusError):
        return JSONResponse(status_code=exc.http_status, content=exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError):
        # Pydantic puts the original exception object in `ctx`, which is not
        # JSON-serialisable — flatten to the parts a client can act on.
        detail = [
            {"field": ".".join(str(p) for p in err.get("loc", ())[1:]) or "body",
             "message": err.get("msg", "invalid value"),
             "type": err.get("type", "value_error")}
            for err in exc.errors()[:20]
        ]
        return JSONResponse(status_code=422, content={
            "error": {"code": "validation_error", "message": "request body failed validation",
                      "detail": detail}
        })

    @app.exception_handler(Exception)
    async def unhandled_handler(_: Request, exc: Exception):
        log.exception("unhandled_error")
        message = str(exc) if not settings.is_production else "internal server error"
        return JSONResponse(status_code=500, content={
            "error": {"code": "internal_error", "message": message}
        })

    api = "/api/v1"
    app.include_router(system.router)
    app.include_router(auth.router, prefix=api)
    app.include_router(studio.router, prefix=api)
    app.include_router(models.router, prefix=api)
    app.include_router(billing.router, prefix=api)
    app.include_router(assets.router)

    # The built dashboard ships inside the image; when it is present the API
    # serves it, so a single container is a complete deployment.
    static_dir = Path(__file__).resolve().parents[2] / "static"
    if static_dir.is_dir():
        app.mount("/app", StaticFiles(directory=static_dir, html=True), name="dashboard")

        @app.get("/", include_in_schema=False)
        async def spa_root():
            return FileResponse(static_dir / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        async def root():
            return {
                "service": settings.app_name,
                "version": "2.0.0",
                "docs": "/docs",
                "api": api,
                "dashboard": "run the frontend with `npm run dev` in ./frontend",
            }

    return app


app = create_app()
