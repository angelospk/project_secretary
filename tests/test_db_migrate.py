"""Forward-only schema-migration runner (subsystem #2 / versioning contract).

Unit tests drive a fake db so they run without a server; one gated integration test
exercises a real SurrealDB the same way the other integration suites do.
"""

from __future__ import annotations

import pytest

from secretary.db import migrate


class FakeDB:
    """Minimal stand-in: a `schema_migration` ledger + a record of what ran, in order."""

    def __init__(self, applied: list[str] | None = None) -> None:
        self.applied = list(applied or [])
        self.calls: list[tuple[str, str]] = []  # (migration id, full transaction sql), in order

    def query(self, sql: str, params: dict | None = None):
        s = sql.strip()
        if s.startswith("SELECT name FROM schema_migration"):
            return [{"name": n} for n in self.applied]
        # One transactioned apply per migration: body + ledger record together.
        if "schema_migration" in s and "CREATE" in s:
            assert params is not None
            self.calls.append((params["id"], s))
            self.applied.append(params["id"])
            return []
        raise AssertionError(f"unexpected query: {s!r}")


def _write(directory, name: str, body: str = "-- noop") -> None:
    (directory / name).write_text(body)


def test_available_ids_sorted_lexically(tmp_path):
    _write(tmp_path, "0002_b.surql")
    _write(tmp_path, "0001_a.surql")
    _write(tmp_path, "0010_c.surql")
    _write(tmp_path, "notes.md")  # non-.surql ignored
    assert migrate.available_ids(tmp_path) == ["0001_a", "0002_b", "0010_c"]


def test_missing_directory_is_empty(tmp_path):
    assert migrate.available_ids(tmp_path / "nope") == []
    assert migrate.run_migrations(FakeDB(), tmp_path / "nope") == []


def test_runs_pending_in_order_then_records(tmp_path):
    _write(tmp_path, "0001_a.surql", "DEFINE FIELD x ON t TYPE int;")
    _write(tmp_path, "0002_b.surql", "DEFINE FIELD y ON t TYPE int;")
    db = FakeDB()

    applied = migrate.run_migrations(db, tmp_path)

    assert applied == ["0001_a", "0002_b"]
    # one transactioned apply per migration, in order, each carrying its own body.
    assert [mid for mid, _ in db.calls] == ["0001_a", "0002_b"]
    assert "DEFINE FIELD x ON t TYPE int;" in db.calls[0][1]
    assert "DEFINE FIELD y ON t TYPE int;" in db.calls[1][1]
    assert "BEGIN TRANSACTION" in db.calls[0][1]
    assert "COMMIT TRANSACTION" in db.calls[0][1]


def test_skips_already_applied_and_is_idempotent(tmp_path):
    _write(tmp_path, "0001_a.surql")
    _write(tmp_path, "0002_b.surql")
    db = FakeDB(applied=["0001_a"])

    assert migrate.run_migrations(db, tmp_path) == ["0002_b"]
    # second run: nothing pending
    assert migrate.run_migrations(db, tmp_path) == []


def test_refuses_when_db_has_unknown_migration(tmp_path):
    """A migration applied in the DB that this build doesn't ship ⇒ DB is newer ⇒ refuse."""
    _write(tmp_path, "0001_a.surql")
    db = FakeDB(applied=["0001_a", "9999_from_the_future"])

    with pytest.raises(RuntimeError, match="newer"):
        migrate.run_migrations(db, tmp_path)


# --- gated integration: real SurrealDB ----------------------------------------


@pytest.fixture()
def live_db():
    from secretary.config import Settings
    from secretary.db.connection import surreal

    settings = Settings(
        github_repo="owner/app",
        surreal_url="ws://127.0.0.1:8000/rpc",
        surreal_user="root",
        surreal_pass="root",
        surreal_ns="opencouncil",
        surreal_db="secretary_migrate_itest",
    )
    try:
        cm = surreal(settings)
        conn = cm.__enter__()
    except Exception:  # noqa: BLE001
        pytest.skip("no SurrealDB server reachable on 127.0.0.1:8000")
    try:
        conn.query("REMOVE TABLE IF EXISTS schema_migration;")
        yield conn
    finally:
        cm.__exit__(None, None, None)


def test_integration_real_run_records_and_is_idempotent(tmp_path, live_db):
    from secretary.db import repo

    repo.apply_schema(live_db)  # defines schema_migration table
    _write(tmp_path, "0001_extra.surql",
           "DEFINE FIELD IF NOT EXISTS extra ON issue TYPE option<string>;")

    assert migrate.run_migrations(live_db, tmp_path) == ["0001_extra"]
    assert migrate.applied_ids(live_db) == {"0001_extra"}
    # idempotent
    assert migrate.run_migrations(live_db, tmp_path) == []
