"""Runtime configuration loaded from environment / .env."""

from __future__ import annotations

import math
from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_kv_floats(raw: str) -> dict[str, float]:
    """Parse a `key=value,key=value` string into a `{key: float}` map.

    Keys are lowercased and stripped; blank chunks are skipped. A malformed chunk
    (no `=`, or a non-finite/non-numeric value) raises ValueError so misconfiguration
    is loud.
    """
    out: dict[str, float] = {}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        key, sep, value = chunk.partition("=")
        if not sep or not key.strip():
            raise ValueError(f"expected key=value, got {chunk!r}")
        try:
            parsed = float(value)
        except ValueError as exc:
            raise ValueError(f"{chunk!r}: value is not a number") from exc
        if not math.isfinite(parsed):
            raise ValueError(f"{chunk!r}: value must be finite")
        out[key.strip().lower()] = parsed
    return out


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

    # Ingest GitHub-native issue dependencies (blocked-by) and sub-issues via GraphQL.
    # Off ⇒ regex-over-body behavior is unchanged; the native edge tables stay empty.
    native_dependencies: bool = False

    # Cross-repo related-history policy: comma-separated `ownerA/nameA+ownerB/nameB`
    # pairs that may link across repos on weaker signals. Repos not paired here only
    # ever produce cross-repo links on an explicit edge.
    related_repo_pairs: str = ""

    # --- Organizer (subsystem #4) --------------------------------------------
    # Label applied to generated release-plan issues (and skipped as a candidate).
    plan_issue_label: str = "release-plan"
    # Priority component weights and label→rank map (key=value,…). Weights are
    # validated >= 0 and normalized to sum to 1 in the scorer, so scores are in [0,1].
    priority_weights: str = "react=0.25,dep=0.3,engage=0.15,label=0.2,fresh=0.1,judge=0.0"
    priority_labels: str = "p0=1.0,p1=0.8,p2=0.5,p3=0.2,critical=1.0,bug=0.4"
    # Milestones the poll loop keeps as living release plans (comma-separated, explicit
    # opt-in — empty means the loop never auto-plans). Each cycle re-fingerprints the
    # milestone's members + config and rewrites the plan issue only when it actually
    # changed. The `plan` CLI command works on any milestone regardless of this list.
    plan_milestones: str = ""
    # Suggested-add expansion: max cosine distance to count, cap, and what to skip.
    expand_threshold: float = 0.45
    expand_max: int = 10
    expand_include_closed: bool = False
    expand_cross_repo: bool = False
    # Optional LLM judge (off by default). When enabled and a key is present, each
    # candidate is scored 0–1 against the rubric and blended in via the `judge` weight.
    judge_enabled: bool = False
    judge_model: str = "claude-haiku-4-5-20251001"
    judge_rubric: str = "Rate user impact, alignment with the release theme, and effort/risk."
    # Enough headroom for "SCORE: <n>\nWHY: <one short sentence>" — 16 truncated WHY.
    judge_max_tokens: int = 64
    # Which backend runs the judge: anthropic | openai | gemini | cli. `cli` shells out
    # to a local command (no API key); the rest call that provider's HTTP API.
    judge_provider: str = "anthropic"
    # For judge_provider=cli: the command to run, prompt fed on stdin (or in place of a
    # `{prompt}` token). E.g. "claude -p", "gemini", "ollama run llama3.2", "codex exec".
    judge_cli_command: str = ""
    judge_cli_timeout: int = 60
    # OpenAI-compatible base URL (also covers vLLM / LM Studio / OpenRouter / local).
    openai_base_url: str = "https://api.openai.com/v1"
    # Provider API keys, read from bare (non-SECRETARY_-prefixed) env vars.
    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")
    gemini_api_key: str = Field(
        default="", validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY")
    )

    @field_validator("judge_provider")
    @classmethod
    def _validate_judge_provider(cls, v: str) -> str:
        provider = v.strip().lower()
        if provider not in ("anthropic", "openai", "gemini", "cli"):
            raise ValueError(
                f"judge_provider must be anthropic|openai|gemini|cli, got {v!r}"
            )
        return provider

    # --- Labeler (subsystem #5) ----------------------------------------------
    # Path to the maintainer-owned thematic taxonomy (TOML). Empty disables the labeler.
    taxonomy_path: str = ""
    # suggest: write a "Label suggestions" section. auto: apply labels via REST.
    labeler_mode: str = "suggest"
    # Cosine-distance bands: <= accept is confident (auto-applies in auto mode);
    # accept < d <= review is borderline (asks the judge); > review stays silent.
    labeler_accept: float = 0.35
    labeler_review: float = 0.50

    @field_validator("labeler_mode")
    @classmethod
    def _validate_labeler_mode(cls, v: str) -> str:
        mode = v.strip().lower()
        if mode not in ("suggest", "auto"):
            raise ValueError(f"labeler_mode must be 'suggest' or 'auto', got {v!r}")
        return mode

    # --- Project steward (subsystem #6) --------------------------------------
    # Cumulative trust ladder: report (writes nothing) -> place (adds items) ->
    # sync (also writes Status/score). Roll forward one rung at a time.
    steward_mode: str = "report"
    # Write the organizer's ranking into the real Priority single-select (only-when-
    # empty, bucketed) instead of the informational score field. Off by default.
    steward_fill_priority: bool = False
    # Board field names (overridable per deployment).
    status_field: str = "Status"
    score_field: str = "Secretary score"
    priority_field: str = "Priority"

    @field_validator("steward_mode")
    @classmethod
    def _validate_steward_mode(cls, v: str) -> str:
        mode = v.strip().lower()
        if mode not in ("report", "place", "sync"):
            raise ValueError(f"steward_mode must be report|place|sync, got {v!r}")
        return mode

    @property
    def priority_weight_map(self) -> dict[str, float]:
        weights = parse_kv_floats(self.priority_weights)
        negative = {k: v for k, v in weights.items() if v < 0}
        if negative:
            raise ValueError(f"priority weights must be >= 0; got {negative}")
        return weights

    @property
    def priority_label_map(self) -> dict[str, float]:
        return parse_kv_floats(self.priority_labels)

    @property
    def plan_milestone_list(self) -> list[str]:
        """Milestones the poll loop maintains, de-duplicated, order preserved."""
        seen: dict[str, None] = {}
        for chunk in self.plan_milestones.split(","):
            name = chunk.strip()
            if name:
                seen.setdefault(name, None)
        return list(seen)

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
