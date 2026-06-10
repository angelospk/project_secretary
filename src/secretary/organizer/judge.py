"""Optional LLM judge for prioritization.

Off by default. When enabled, each candidate is scored 0–1 against a configurable
rubric and blended into the priority score by the `judge` weight. The model call is
isolated behind an injectable `complete(prompt) -> str` so the scoring/parsing logic
is unit-testable without the network; the default backend is the Anthropic Messages
API over the existing httpx dependency (no new hard dependency).

Caching (in `plan.py`) keys on the full tuple (item, updated_at, model,
prompt_version, rubric_hash) per the Codex review, so a model or rubric change
correctly invalidates.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable

import httpx

from secretary.config import Settings

log = logging.getLogger(__name__)

PROMPT_VERSION = "v1"

_SCORE_RE = re.compile(r"SCORE\s*[:=]\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
_WHY_RE = re.compile(r"WHY\s*[:=]\s*(.+)", re.IGNORECASE)


def rubric_hash(rubric: str) -> str:
    return hashlib.sha1(rubric.strip().encode("utf-8")).hexdigest()[:12]


def build_prompt(title: str, body: str | None, rubric: str) -> str:
    excerpt = (body or "").strip()[:1500]
    return (
        "You are triaging GitHub issues for a release. Score the issue's priority "
        "from 0 (skip) to 10 (must-do) against this rubric:\n"
        f"{rubric.strip()}\n\n"
        f"Issue title: {title}\n"
        f"Issue body:\n{excerpt or '(no body)'}\n\n"
        "Respond in exactly two lines:\n"
        "SCORE: <number 0-10>\n"
        "WHY: <one short sentence>"
    )


def parse_score(raw: str) -> tuple[float, str]:
    """Parse `SCORE:`/`WHY:` output into (0..1 score, one-line reason)."""
    score_match = _SCORE_RE.search(raw or "")
    why_match = _WHY_RE.search(raw or "")
    score = float(score_match.group(1)) / 10.0 if score_match else 0.0
    score = min(max(score, 0.0), 1.0)
    why = why_match.group(1).strip() if why_match else (raw or "").strip()[:200]
    return score, why


class LLMJudge:
    def __init__(self, settings: Settings, *, complete: Callable[[str], str] | None = None):
        self._settings = settings
        self._complete = complete or self._anthropic_complete

    @property
    def model(self) -> str:
        return self._settings.judge_model

    def score(self, title: str, body: str | None, rubric: str) -> tuple[float, str]:
        """Best-effort priority score; a failed call yields a neutral 0.0."""
        try:
            raw = self._complete(build_prompt(title, body, rubric))
        except Exception as exc:  # noqa: BLE001 - the judge is advisory, never fatal
            log.warning("judge call failed for %r: %s", title, exc)
            return 0.0, "judge unavailable"
        return parse_score(raw)

    def _anthropic_complete(self, prompt: str) -> str:
        key = self._settings.anthropic_api_key
        if not key:
            raise RuntimeError("judge enabled but ANTHROPIC_API_KEY is not set")
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self._settings.judge_model,
                "max_tokens": self._settings.judge_max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        blocks = resp.json().get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
