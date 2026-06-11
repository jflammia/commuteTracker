from fastapi import APIRouter, HTTPException, Request


def make_trips_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/trips")
    async def list_trips(request: Request, limit: int = 50) -> list[dict]:
        return request.app.state.runner.store.list_trips(limit=limit)

    @router.get("/api/trips/{trip_id}")
    async def trip_detail(request: Request, trip_id: str) -> dict:
        detail = request.app.state.runner.store.get_trip(trip_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="trip not found")
        return detail

    return router
