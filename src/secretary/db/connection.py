"""SurrealDB connection helper."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from surrealdb import Surreal

from secretary.config import Settings


@contextmanager
def surreal(settings: Settings) -> Iterator[Surreal]:
    """Open an authenticated SurrealDB connection scoped to the configured ns/db."""
    db = Surreal(settings.surreal_url)
    db.signin({"username": settings.surreal_user, "password": settings.surreal_pass})
    db.use(settings.surreal_ns, settings.surreal_db)
    try:
        yield db
    finally:
        db.close()
