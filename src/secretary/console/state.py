"""Console-owned state in `organizer_kv` under the `console.` namespace.

The console never rewrites the human-owned taxonomy TOML or the deployment's `.env`; its
three write actions persist here as namespaced JSON. The Discord webhook is deliberately
NOT stored here — it is a secret and stays in env; the console edits only the non-secret
digest schedule (enabled, interval).
"""

from __future__ import annotations

from surrealdb import Surreal

from secretary.config import Settings
from secretary.db import repo as db_repo

_LABEL_KEY = "console.label"
_NEXT_RELEASE_KEY = "console.next_release"
_DIGEST_KEY = "console.digest"


# --- 1. cluster/category display-name overrides -------------------------------

def get_label_overrides(db: Surreal, repo: str) -> dict[str, str]:
    """All console display-name overrides for this repo: {original_key: display_name}."""
    stored = db_repo.kv_get(db, repo, _LABEL_KEY)
    return dict(stored) if isinstance(stored, dict) else {}


def set_label(db: Surreal, repo: str, key: str, name: str) -> None:
    """Override the human-readable name shown for a taxonomy category / cluster key."""
    key = key.strip()
    name = name.strip()
    if not key or not name:
        raise ValueError("both key and name are required")
    overrides = get_label_overrides(db, repo)
    overrides[key] = name
    db_repo.kv_set(db, repo, _LABEL_KEY, overrides)


# --- 2. next-release definition -----------------------------------------------

def get_next_release(db: Surreal, repo: str) -> dict:
    stored = db_repo.kv_get(db, repo, _NEXT_RELEASE_KEY)
    if not isinstance(stored, dict):
        return {"categories": [], "items": []}
    return {"categories": list(stored.get("categories") or []),
            "items": list(stored.get("items") or [])}


def set_next_release(db: Surreal, repo: str, categories: list[str], items: list[dict]) -> None:
    """Persist the next-release definition (console-only surfacing for v1)."""
    clean_cats = [str(c).strip() for c in categories if str(c).strip()]
    clean_items: list[dict] = []
    for it in items:
        kind = str(it.get("kind", "")).strip()
        if kind not in ("issue", "pr"):
            raise ValueError(f"item kind must be 'issue' or 'pr', got {kind!r}")
        number = int(it.get("number", 0))
        if number <= 0:
            raise ValueError("item number must be a positive integer")
        clean_items.append({"kind": kind, "number": number})
    db_repo.kv_set(db, repo, _NEXT_RELEASE_KEY,
                   {"categories": clean_cats, "items": clean_items})


# --- 3. digest schedule overrides (non-secret only) ---------------------------

def get_digest_overrides(db: Surreal, repo: str) -> dict:
    stored = db_repo.kv_get(db, repo, _DIGEST_KEY)
    return dict(stored) if isinstance(stored, dict) else {}


def set_digest(db: Surreal, repo: str, enabled: bool, interval_days: int) -> None:
    if int(interval_days) < 1:
        raise ValueError("interval_days must be >= 1")
    db_repo.kv_set(db, repo, _DIGEST_KEY,
                   {"enabled": bool(enabled), "interval_days": int(interval_days)})


def effective_digest(settings: Settings, overrides: dict) -> dict:
    """Merge console overrides over Settings defaults. The webhook stays env-only."""
    return {
        "enabled": bool(overrides.get("enabled", settings.digest_enabled)),
        "interval_days": int(overrides.get("interval_days", settings.digest_interval_days)),
        "discord_webhook_set": bool(settings.digest_discord_webhook),  # masked: presence only
    }
