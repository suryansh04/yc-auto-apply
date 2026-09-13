"""FastAPI adapter (spec §3). Peer to `cli.py`: routers translate
DTO -> use case -> DTO and hold no business logic."""

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.deps import get_repos, real_repos
from app.api.routers import contacts, manual, queue, send, stats

ALLOWED_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]

logger = logging.getLogger("app.api")


def create_app(repos_factory=None) -> FastAPI:
    app = FastAPI(title="YC Auto Apply", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(
        CORSMiddleware, allow_origins=ALLOWED_ORIGINS,
        allow_methods=["*"], allow_headers=["*"],
    )
    if repos_factory is not None:
        app.dependency_overrides[get_repos] = repos_factory
    app.include_router(stats.router, prefix="/api")
    app.include_router(queue.router, prefix="/api")
    app.include_router(manual.router, prefix="/api")
    app.include_router(send.router, prefix="/api")
    app.include_router(contacts.router, prefix="/api")

    @app.exception_handler(Exception)
    async def _unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        # Never put a traceback (which can carry an API key or other
        # secret embedded in a repr) on the wire - it goes to the log,
        # keyed by a request id the caller can quote back to us.
        request_id = str(uuid.uuid4())
        logger.exception("unhandled error (request_id=%s)", request_id)
        return JSONResponse(
            status_code=500,
            content={"detail": "internal error", "request_id": request_id},
        )

    return app


app = create_app()
