"""FastAPI adapter (spec §3). Peer to `cli.py`: routers translate
DTO -> use case -> DTO and hold no business logic."""

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import jobs as jobs_module
from app.api.deps import get_repos, real_repos
from app.api.routers import contacts, gmail, jobs, manual, queue, send, stats, thread

ALLOWED_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]

logger = logging.getLogger("app.api")


def _default_gmail_oauth_factory(settings, redirect_uri: str):
    """Production flow. Imported lazily so the offline suite never needs
    `google_auth_oauthlib` installed to build an app."""
    from app.adapters.gmail_oauth import GoogleOAuthFlow

    return GoogleOAuthFlow(settings.gmail_client_secrets_path, redirect_uri)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Task 6: a job left `running` by a crashed process would hold the
    # partial unique index forever otherwise - recovered once at boot,
    # through whichever job factories this app was built with (never a
    # real database in the offline test suite).
    jobs_module.recover_stale_jobs(app)
    yield


def create_app(repos_factory=None, job_session_factory=None, job_runner_factory=None,
               gmail_oauth_factory=None) -> FastAPI:
    app = FastAPI(
        title="YC Auto Apply", docs_url="/api/docs", openapi_url="/api/openapi.json",
        lifespan=_lifespan,
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=ALLOWED_ORIGINS,
        allow_methods=["*"], allow_headers=["*"],
    )
    if repos_factory is not None:
        app.dependency_overrides[get_repos] = repos_factory

    # Task 6: the job thread's own session/runner factories - a seam
    # separate from `get_repos` (never the request's session). Production
    # defaults come from `app.api.jobs`; tests override both here the same
    # way `repos_factory` overrides `get_repos` above. Stored on
    # `app.state` (not baked into the `dependency_overrides` lambdas
    # directly) so a fixture can swap them again on an already-created
    # `app` just by reassigning the attribute - see `job_probe` /
    # `blocking_job` / `failing_job` in `backend/tests/conftest.py`.
    app.state.job_session_factory = job_session_factory or jobs_module._default_session_factory
    app.state.job_runner_factory = job_runner_factory or jobs_module._default_runner_factory
    app.dependency_overrides[jobs.get_job_session_factory] = lambda: app.state.job_session_factory
    app.dependency_overrides[jobs.get_job_runner_factory] = lambda: app.state.job_runner_factory

    # The Google consent flow, on the same kind of seam: the offline
    # suite exercises connect/callback with a fake that needs no browser,
    # no client-secrets file and no network. `gmail_oauth_states` holds
    # the one-shot CSRF states minted by POST /api/gmail/connect - in
    # process memory on purpose, so restarting the API invalidates any
    # consent link that is still open in a stale tab.
    app.state.gmail_oauth_factory = gmail_oauth_factory or _default_gmail_oauth_factory
    app.state.gmail_oauth_states = {}

    app.include_router(stats.router, prefix="/api")
    app.include_router(queue.router, prefix="/api")
    app.include_router(manual.router, prefix="/api")
    app.include_router(send.router, prefix="/api")
    app.include_router(contacts.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")
    app.include_router(thread.router, prefix="/api")
    app.include_router(gmail.router, prefix="/api")

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
