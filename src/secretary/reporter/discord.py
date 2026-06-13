"""Optional Discord delivery for the digest — one HTTP call, no bot account.

The payload shape is pure and testable; posting is a single httpx call bounded by a
timeout. Discord caps a message `content` at 2000 chars, so the body is truncated with a
pointer to the full digest issue. A failed POST is logged, never fatal.
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

_DISCORD_LIMIT = 2000
_POST_TIMEOUT = 15


def discord_payload(title: str, body: str) -> dict:
    """A Discord webhook JSON payload. Truncates to Discord's 2000-char content limit."""
    content = f"**{title}**\n{body}"
    if len(content) > _DISCORD_LIMIT:
        content = content[: _DISCORD_LIMIT - 4].rstrip() + "\n…"
    return {"content": content}


def post_discord(webhook: str, payload: dict) -> bool:
    """POST the payload to a Discord webhook. Returns success; never raises."""
    try:
        resp = httpx.post(webhook, json=payload, timeout=_POST_TIMEOUT)
        resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001 - delivery is best-effort
        log.warning("Discord digest delivery failed: %s", exc)
        return False
