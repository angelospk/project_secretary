"""The change-source abstraction.

A `ChangeSource` decides *which* GitHub objects need ingesting; the shared
`ingest` pipeline owns *how* they are written. Polling is the only concrete
source today; a webhook receiver slots in later without touching the pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from surrealdb import Surreal

from secretary.github.client import GitHubClient
from secretary.ingest.reconcile import SyncReport


class ChangeSource(ABC):
    @abstractmethod
    def run_once(self, db: Surreal, client: GitHubClient, repo: str) -> SyncReport:
        """Ingest whatever this source currently considers dirty for `repo`."""
        raise NotImplementedError
