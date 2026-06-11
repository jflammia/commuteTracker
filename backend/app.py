"""FastAPI app factory for the rewrite backend."""

from fastapi import FastAPI

from backend.config import Settings, load_settings
from backend.ingest.passthrough import Passthrough
from backend.ingest.routes import make_ingest_router
from backend.storage.raw import RawStore


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="commute-tracker backend")
    app.state.settings = settings
    app.state.raw_store = RawStore(settings.data_dir)
    app.state.passthrough = Passthrough(settings.passthrough_url)
    app.include_router(make_ingest_router())
    return app


app = create_app()
