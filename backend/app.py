"""FastAPI app factory for the rewrite backend."""

import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.config import Settings, load_settings
from backend.engine.runner import EngineRunner
from backend.ingest.passthrough import Passthrough
from backend.health.routes import make_health_router
from backend.ingest.routes import make_ingest_router
from backend.jobs.daily import run_daily
from backend.storage.archive import Archiver
from backend.storage.raw import RawStore


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runner = await asyncio.to_thread(EngineRunner.start, settings)
        archiver = Archiver(settings)
        task = asyncio.create_task(run_daily(archiver.run, hour_utc=settings.archive_hour_utc))
        yield
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await app.state.passthrough.aclose()
        app.state.runner.close()

    app = FastAPI(title="commute-tracker backend", lifespan=lifespan)
    app.state.settings = settings
    app.state.raw_store = RawStore(settings.data_dir)
    app.state.passthrough = Passthrough(settings.passthrough_url)
    app.include_router(make_ingest_router())
    app.include_router(make_health_router())
    return app


app = create_app()
