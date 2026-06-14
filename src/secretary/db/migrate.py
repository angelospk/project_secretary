"""Forward-only schema migrations on top of the idempotent base schema (subsystem #2).

`repo.apply_schema` ensures the current declarative shape (`DEFINE ... IF NOT EXISTS`)
and is safe to run every time. Migrations are the deltas idempotence cannot express —
field retypes, index rebuilds, data backfills. Each runs exactly once, in lexical id
order, recorded in the `schema_migration` ledger table.

The ledger *is* the schema version: the set of applied ids. Forward-only — if the DB
has applied a migration this build does not ship, the database was written by a newer
secretary, so we refuse rather than risk silent corruption. That fail-fast guard is
what lets a downstream consumer auto-update (pin a tag, let Renovate bump it) without
a quiet break: an accidental downgrade stops loudly instead of mangling data.

Versioning contract: additive changes go in `schema.surql` (auto-applied, patch/minor
bump). Anything destructive — retype, drop, reindex, re-embed, backfill — ships as a
numbered migration file here and warrants a minor/major bump plus a release note.
"""

from __future__ import annotations

from pathlib import Path

from surrealdb import Surreal

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def migrations_dir() -> Path:
    return _MIGRATIONS_DIR


def _files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.surql"))


def available_ids(directory: Path | None = None) -> list[str]:
    """Migration ids (filename stems) bundled in `directory`, in apply order."""
    return [p.stem for p in _files(directory or _MIGRATIONS_DIR)]


def applied_ids(db: Surreal) -> set[str]:
    """The ids already recorded in the ledger."""
    rows = db.query("SELECT name FROM schema_migration") or []
    return {row["name"] for row in rows}


def run_migrations(db: Surreal, directory: Path | None = None) -> list[str]:
    """Apply pending migrations in order; return the ids applied this call.

    Raises RuntimeError if the DB has applied a migration this build doesn't know
    about (the database is newer than this secretary).
    """
    directory = directory or _MIGRATIONS_DIR
    files = _files(directory)
    available = {p.stem for p in files}
    applied = applied_ids(db)

    unknown = applied - available
    if unknown:
        raise RuntimeError(
            "database is newer than this secretary: it has migrations this build does "
            f"not ship ({sorted(unknown)}). Upgrade secretary, or run an older DB."
        )

    done: list[str] = []
    for path in files:
        if path.stem in applied:
            continue
        body = path.read_text(encoding="utf-8")
        # Body + ledger record run in one transaction: either the migration applies
        # and is recorded, or neither happens — never a migration without its ledger
        # entry. The record id is `schema_migration:<stem>`, so a second attempt to
        # record the same migration fails on the primary key (no duplicate rows).
        db.query(
            "BEGIN TRANSACTION;\n"
            f"{body}\n"
            "CREATE type::record('schema_migration', $id) "
            "SET name = $id, applied_at = time::now();\n"
            "COMMIT TRANSACTION;",
            {"id": path.stem},
        )
        done.append(path.stem)
    return done


def ensure_schema(db: Surreal, directory: Path | None = None) -> list[str]:
    """Ensure the current schema shape, then apply any pending migrations.

    The single entry point for "bring this DB to the schema this build expects" —
    used by `init-db`, `backfill`, `reconcile`, and `secretary migrate`.
    """
    from secretary.db import repo

    repo.apply_schema(db)
    return run_migrations(db, directory)
