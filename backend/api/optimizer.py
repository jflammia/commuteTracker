"""Optimizer API: what-if (GET /api/optimizer) + daily recommendation
(GET /api/recommendation). Both reuse recommend() over the live derived store.

Local time is America/New_York; the API converts local-seconds-of-day to ISO
timestamps on the given service date for display.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request

from backend.optimizer.params import OptimizerParams
from backend.optimizer.recommend import recommend

_NY = ZoneInfo("America/New_York")


def _hhmm_to_local_s(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 3600 + int(m) * 60


def _local_s_to_iso(service_date: str, local_s: int) -> str:
    y, mo, d = int(service_date[:4]), int(service_date[5:7]), int(service_date[8:10])
    midnight = datetime(y, mo, d, tzinfo=_NY)
    return (midnight + timedelta(seconds=local_s)).isoformat()


def _service_date_yyyymmdd(date_str: str) -> str:
    return date_str.replace("-", "")


def _shape(rec: dict, date_str: str) -> dict:
    out = dict(rec)
    out["arrive_by_local"] = (
        f"{rec['arrive_by_local_s'] // 3600:02d}:{(rec['arrive_by_local_s'] % 3600) // 60:02d}"
    )
    out["options"] = [
        {
            **o,
            "leave_by": _local_s_to_iso(date_str, o["leave_by_local_s"]),
            "scheduled_dep": _local_s_to_iso(date_str, o["scheduled_dep_local_s"]),
            "scheduled_arr": _local_s_to_iso(date_str, o["scheduled_arr_local_s"]),
            "p50_arrive": _local_s_to_iso(date_str, o["p50_arr_local_s"]),
            "p90_arrive": _local_s_to_iso(date_str, o["p90_arr_local_s"]),
        }
        for o in rec["options"]
    ]
    return out


def make_optimizer_router() -> APIRouter:
    router = APIRouter()

    def _require_config(settings):
        if not (settings.commute_source and settings.board_stop_id and settings.alight_stop_id):
            raise HTTPException(
                status_code=409,
                detail="optimizer not configured: set CT_COMMUTE_SOURCE, "
                "CT_BOARD_STOP_ID, CT_ALIGHT_STOP_ID",
            )

    @router.get("/api/optimizer")
    async def whatif(request: Request, date: str, arrive_by: str | None = None) -> dict:
        settings = request.app.state.settings
        _require_config(settings)
        arrive_local = _hhmm_to_local_s(arrive_by or settings.arrive_by_local)
        rec = recommend(
            request.app.state.runner.store,
            direction="outbound",
            source=settings.commute_source,
            board_stop=settings.board_stop_id,
            alight_stop=settings.alight_stop_id,
            service_date=_service_date_yyyymmdd(date),
            arrive_by_local_s=arrive_local,
            access_distance_m=settings.access_distance_m,
            egress_distance_m=settings.egress_distance_m,
            params=OptimizerParams(),
        )
        return _shape(rec, date)

    @router.get("/api/recommendation")
    async def recommendation(request: Request) -> dict:
        settings = request.app.state.settings
        _require_config(settings)
        today = datetime.now(_NY).strftime("%Y-%m-%d")
        store = request.app.state.runner.store
        cached = store.recommendation(today.replace("-", ""), "outbound")
        if cached is not None:
            return cached
        arrive_local = _hhmm_to_local_s(settings.arrive_by_local)
        rec = recommend(
            store,
            direction="outbound",
            source=settings.commute_source,
            board_stop=settings.board_stop_id,
            alight_stop=settings.alight_stop_id,
            service_date=today.replace("-", ""),
            arrive_by_local_s=arrive_local,
            access_distance_m=settings.access_distance_m,
            egress_distance_m=settings.egress_distance_m,
            params=OptimizerParams(),
        )
        shaped = _shape(rec, today)
        store.write_recommendation(today.replace("-", ""), "outbound", shaped)
        return shaped

    return router
