"""FastAPI app factory for the rewrite backend."""

import asyncio
import contextlib
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from backend.config import Settings, load_settings
from backend.engine.runner import EngineRunner
from backend.ingest.passthrough import Passthrough
from backend.api.trips import make_trips_router
from backend.health.routes import make_health_router
from backend.ingest.routes import make_ingest_router
from backend.jobs.daily import run_daily
from backend.sources.framework import sources_from_settings
from backend.sources.poller import start_pollers
from backend.storage.archive import Archiver
from backend.storage.raw import RawStore


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runner = await asyncio.to_thread(EngineRunner.start, settings)
        archiver = Archiver(settings)
        task = asyncio.create_task(run_daily(archiver.run, hour_utc=settings.archive_hour_utc))
        # follow_redirects: GTFS mirrors commonly 301 to CDNs — without this the
        # framework would archive redirect stubs forever
        source_client = httpx.AsyncClient(follow_redirects=True)
        source_tasks = start_pollers(
            source_client, sources_from_settings(settings), app.state.raw_store
        )
        yield
        for st in source_tasks:
            st.cancel()
        for st in source_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await st
        await source_client.aclose()
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
    app.include_router(make_trips_router())
    return app


app = create_app()
