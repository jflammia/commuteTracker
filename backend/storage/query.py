"""DuckDB view over the parquet archive plus today's raw JSONL tail."""

import duckdb

from backend.config import Settings

# Explicit column types for read_json — user is a reserved word, quoted below.
_RAW_COLUMNS = (
    "{'received_at': 'TIMESTAMPTZ', 'user': 'VARCHAR', 'device': 'VARCHAR', 'payload': 'VARCHAR'}"
)


class EventQuery:
    """Query layer over the Parquet archive and raw JSONL tail for a single stream.

    Intended to be created once per process or request scope.  Each instance
    owns a private in-memory DuckDB connection whose lifetime matches the
    lifetime of its relations — do not share instances across threads or
    requests.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._con = duckdb.connect()

    def events(self, stream: str) -> duckdb.DuckDBPyRelation:
        """Return a relation spanning the Parquet archive and the raw JSONL tail.

        The ``payload`` column is typed JSON so callers can use ``->>'$.key'``
        extraction directly on the relation.

        ``stream`` comes from internal callers (STREAMS) only — not user input;
        validate before ever exposing externally.
        """
        archive_dir = self._settings.data_dir / "archive" / stream
        raw_dir = self._settings.data_dir / "raw" / stream

        archive_glob = str(archive_dir / "**" / "data.parquet")
        raw_glob = str(raw_dir / "*.jsonl")

        parts = []

        if list(archive_dir.glob("**/data.parquet")):
            parts.append(
                f'SELECT received_at, "user", device, json(payload) AS payload, '
                f"'archive' AS source "
                f"FROM read_parquet('{archive_glob}')"
            )

        if list(raw_dir.glob("*.jsonl")):
            parts.append(
                f'SELECT received_at, "user", device, json(payload) AS payload, '
                f"'raw' AS source "
                f"FROM read_json('{raw_glob}', format='newline_delimited', "
                f"columns={_RAW_COLUMNS})"
            )

        if not parts:
            parts.append(
                "SELECT * FROM (VALUES ("
                "NULL::TIMESTAMPTZ, NULL::VARCHAR, NULL::VARCHAR, "
                "NULL::JSON, NULL::VARCHAR"
                ')) t(received_at, "user", device, payload, source) WHERE false'
            )

        return self._con.sql(" UNION ALL ".join(parts))

    def sql(self, query: str, **relations: duckdb.DuckDBPyRelation) -> duckdb.DuckDBPyRelation:
        """Execute a SQL query, optionally binding named relations into scope.

        Each kwarg name is registered on the connection for the duration of this
        call only.  Names are always unregistered in a ``finally`` block so they
        cannot leak into subsequent calls.  Because DuckDB relations are lazily
        evaluated, the result is materialized via ``.execute()`` before the names
        are removed — the returned relation is fully independent of the registered
        names and safe to consume after this method returns.
        """
        for name, rel in relations.items():
            self._con.register(name, rel)
        try:
            return self._con.sql(query).execute()
        finally:
            for name in relations:
                self._con.unregister(name)
