"""Minimal DeepWiki (Devin `ada`) client: submit a query, stream the answer.

This is a reverse-engineered public endpoint with no SLA. Treat it as best-effort:
every call is bounded by a timeout and returns whatever text streamed before the
deadline (or "" on failure) — the responder must degrade gracefully, never hang.
"""

from __future__ import annotations

import json
import logging
import re
import uuid

import httpx
from websockets.sync.client import connect

log = logging.getLogger(__name__)

_POST_URL = "https://api.devin.ai/ada/query"
_WS_URL = "wss://api.devin.ai/ada/ws/query/{qid}"
_TERMINAL = {"done", "complete", "end", "final"}

# The `ada` agent streams its chain-of-thought ("Let me explore the codebase...",
# "The wiki reveals...", "I now have a comprehensive view...") as prose paragraphs
# before the real answer. Drop leading paragraphs that look like that narration.
_REASONING_START = re.compile(
    r"^(let me\b|let's\b|i'?ll\b|i need to\b|i now have\b|i have (now|a)\b"
    r"|first,? (let me|i)\b|now,? let me\b|next,? (let me|i)\b"
    r"|the wiki (already )?(reveals|reveal|shows|show)\b"
    r"|i can now\b|i'?ve (now )?(read|gathered|explored)\b)",
    re.IGNORECASE,
)


def _slugify(text: str) -> str:
    return re.sub(r"(^-|-$)", "", re.sub(r"[^a-z0-9]+", "-", text.lower()))


def strip_preamble(text: str) -> str:
    """Drop the agent's leading chain-of-thought, keep the answer.

    Splits on blank lines and discards leading paragraphs (plus stray `---`
    separators) that begin like agent narration. Stops at the first real
    paragraph. If nothing survives, returns the original text unchanged.
    """
    if not text:
        return text
    paras = re.split(r"\n\s*\n", text)
    idx = 0
    while idx < len(paras):
        head = paras[idx].lstrip().lstrip("#").strip()
        if not head or head == "---":
            idx += 1
            continue
        if _REASONING_START.match(head):
            idx += 1
            continue
        break
    cleaned = "\n\n".join(paras[idx:]).strip()
    return cleaned or text.strip()


def query(repo: str, user_query: str, *, timeout: int = 120, query_id: str | None = None) -> str:
    """Run one DeepWiki deep query against `repo`; return the streamed text (or "")."""
    qid = query_id or f"{_slugify(user_query[:50])}_{uuid.uuid4()}"
    payload = {
        "mode": "deep",
        "user_query": user_query,
        "keywords": [],
        "repo_names": [repo],
        "additional_context": "",
        "query_id": qid,
        "use_notes": False,
        "generate_summary": False,
        "source": "ada.deepwiki_public",
    }
    try:
        resp = httpx.post(
            _POST_URL,
            json=payload,
            headers={"Content-Type": "application/json", "Origin": "https://deepwiki.com"},
            timeout=30,
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - external, no SLA
        log.warning("DeepWiki submit failed for %s: %s", repo, exc)
        return ""

    chunks: list[str] = []
    try:
        with connect(_WS_URL.format(qid=qid), open_timeout=30) as ws:
            while True:
                msg = ws.recv(timeout=timeout)
                try:
                    frame = json.loads(msg)
                except (TypeError, ValueError):
                    continue
                ftype = frame.get("type")
                if ftype == "chunk" and frame.get("data"):
                    chunks.append(frame["data"])
                elif ftype in _TERMINAL:
                    break
    except TimeoutError:
        log.warning("DeepWiki stream timed out for %s after %ss", repo, timeout)
    except Exception as exc:  # noqa: BLE001 - external, no SLA
        log.warning("DeepWiki stream error for %s: %s", repo, exc)

    return strip_preamble("".join(chunks).strip())
