"""FastAPI app factory for the rewrite backend."""

import asyncio
import contextlib
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse

from backend.config import Settings, load_settings
from backend.engine.runner import EngineRunner
from backend.ingest.passthrough import Passthrough
from backend.api.labels import make_labels_router
from backend.api.optimizer import make_optimizer_router
from backend.api.trips import make_trips_router
from backend.health.routes import make_health_router
from backend.ingest.routes import make_ingest_router
from backend.jobs.daily import run_daily
from backend.sources.framework import sources_from_settings
from backend.sources.njt import NjtTokenManager, njt_specs_from_settings
from backend.sources.poller import start_njt_pollers, start_pollers
from backend.storage.archive import Archiver
from backend.storage.raw import RawStore


def compute_daily_recommendation(settings, store) -> None:
    """Compute and persist today's outbound recommendation. No-op when the
    optimizer isn't configured."""
    if not (settings.commute_source and settings.board_stop_id and settings.alight_stop_id):
        return
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from backend.api.optimizer import _hhmm_to_local_s, _shape
    from backend.optimizer.params import OptimizerParams
    from backend.optimizer.recommend import recommend

    today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    rec = recommend(
        store,
        direction="outbound",
        source=settings.commute_source,
        board_stop=settings.board_stop_id,
        alight_stop=settings.alight_stop_id,
        service_date=today.replace("-", ""),
        arrive_by_local_s=_hhmm_to_local_s(settings.arrive_by_local),
        access_distance_m=settings.access_distance_m,
        egress_distance_m=settings.egress_distance_m,
        params=OptimizerParams(),
    )
    store.write_recommendation(today.replace("-", ""), "outbound", _shape(rec, today))


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runner = await asyncio.to_thread(EngineRunner.start, settings)
        archiver = Archiver(settings)
        task = asyncio.create_task(run_daily(archiver.run, hour_utc=settings.archive_hour_utc))
        # run_daily at 09:00 UTC (≈ 05:00 ET) — computed before the morning commute window
        rec_task = asyncio.create_task(
            run_daily(
                lambda: compute_daily_recommendation(settings, app.state.runner.store),
                hour_utc=9,
            )
        )
        # follow_redirects: GTFS mirrors commonly 301 to CDNs — without this the
        # framework would archive redirect stubs forever
        source_client = httpx.AsyncClient(follow_redirects=True)
        source_tasks = start_pollers(
            source_client, sources_from_settings(settings), app.state.raw_store
        )
        njt_specs = njt_specs_from_settings(settings)
        njt_tasks: list[asyncio.Task] = []
        if njt_specs:
            njt_manager = NjtTokenManager(
                settings.njt_api_base,
                settings.njt_username,
                settings.njt_password,
                settings.data_dir,
            )
            njt_tasks = start_njt_pollers(
                source_client, njt_manager, njt_specs, app.state.raw_store
            )
        yield
        for st in source_tasks + njt_tasks:
            st.cancel()
        for st in source_tasks + njt_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await st
        await source_client.aclose()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        rec_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await rec_task
        await app.state.passthrough.aclose()
        app.state.runner.close()

    app = FastAPI(title="commute-tracker backend", lifespan=lifespan)
    app.state.settings = settings
    app.state.raw_store = RawStore(settings.data_dir)
    app.state.passthrough = Passthrough(settings.passthrough_url)
    app.include_router(make_ingest_router())
    app.include_router(make_health_router())
    app.include_router(make_trips_router())
    app.include_router(make_labels_router())
    app.include_router(make_optimizer_router())

    if settings.frontend_build_dir is not None and settings.frontend_build_dir.is_dir():
        build_dir = settings.frontend_build_dir

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> FileResponse:
            target = (build_dir / path).resolve()
            if path and target.is_file() and target.is_relative_to(build_dir.resolve()):
                return FileResponse(target)
            return FileResponse(build_dir / "index.html")

    return app


app = create_app()
