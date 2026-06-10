"""Runtime configuration loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_repo(value: str) -> str:
    """Canonical `owner/name`: lowercased, validated. The repo is an immutable key."""
    v = value.strip().lower()
    owner, _, name = v.partition("/")
    if not owner or not name or "/" in name:
        raise ValueError(f"repo must be in 'owner/name' form, got {value!r}")
    return v


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="SECRETARY_", extra="ignore"
    )

    # GitHub. Configure the repo(s) to index: `github_repos` (comma-separated
    # owner/name) is the multi-repo list; `github_repo` is the single-repo
    # shorthand. Set at least one. Empty means "unset".
    github_token: str = ""
    github_repo: str = ""
    github_repos: str = ""
    github_api_url: str = "https://api.github.com"

    # SurrealDB
    surreal_url: str = "ws://127.0.0.1:8000/rpc"
    surreal_user: str = "root"
    surreal_pass: str = "root"
    surreal_ns: str = "secretary"
    surreal_db: str = "secretary"

    # Polling
    poll_interval_seconds: int = 300

    # DeepWiki (optional context source for the responder). Best-effort, no SLA.
    deepwiki_timeout_seconds: int = 120

    # Cross-repo related-history policy: comma-separated `ownerA/nameA+ownerB/nameB`
    # pairs that may link across repos on weaker signals. Repos not paired here only
    # ever produce cross-repo links on an explicit edge.
    related_repo_pairs: str = ""

    @property
    def repo_list(self) -> list[str]:
        """All repos to index, normalized and de-duplicated (order preserved)."""
        raw = self.github_repos.strip()
        repos = raw.split(",") if raw else ([self.github_repo] if self.github_repo else [])
        seen: dict[str, None] = {}
        for r in repos:
            if r.strip():
                seen.setdefault(normalize_repo(r), None)
        if not seen:
            raise ValueError(
                "no repo configured; set SECRETARY_GITHUB_REPOS (or SECRETARY_GITHUB_REPO)"
            )
        return list(seen)

    @property
    def related_repo_pair_set(self) -> set[frozenset[str]]:
        pairs: set[frozenset[str]] = set()
        for chunk in self.related_repo_pairs.split(","):
            a, _, b = chunk.partition("+")
            if a.strip() and b.strip():
                pairs.add(frozenset({normalize_repo(a), normalize_repo(b)}))
        return pairs

    @field_validator("github_repo")
    @classmethod
    def _validate_repo(cls, v: str) -> str:
        return normalize_repo(v) if v.strip() else ""

    @property
    def repo_owner(self) -> str:
        return self.repo_list[0].split("/", 1)[0]

    @property
    def repo_name(self) -> str:
        return self.repo_list[0].split("/", 1)[1]


@lru_cache
def get_settings() -> Settings:
    return Settings()
