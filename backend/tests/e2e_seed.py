"""Seed a data dir for the Playwright smoke test: one synthetic commute.

Usage: python -m backend.tests.e2e_seed /tmp/e2e-data
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

from backend.storage.raw import RawStore
from backend.tests.synth import commute


def seed(data_dir: Path) -> None:
    store = RawStore(data_dir)
    pts, _, _ = commute()
    for i, pt in enumerate(pts):
        store.append(
            "owntracks",
            {
                "received_at": f"2026-06-09T{i // 3600:02d}:{(i // 60) % 60:02d}:{i % 60:02d}+00:00",
                "user": "e2e",
                "device": "e2e",
                "payload": {
                    "_type": "location",
                    "tst": pt.ts,
                    "lat": pt.lat,
                    "lon": pt.lon,
                    "acc": pt.accuracy_m,
                },
            },
        )
    print(f"seeded {len(pts)} points into {data_dir} at {datetime.now(UTC).isoformat()}")


if __name__ == "__main__":
    seed(Path(sys.argv[1]))
