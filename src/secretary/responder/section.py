"""Managed issue-body section: safe, idempotent, human-edit-aware.

Per the Codex review, editing a human's issue body is risky. We guard it with:
- versioned, checksummed markers: `<!-- oc-secretary:v1 issue=N context=HASH -->`
- the checksum covers the section content, so a human editing inside the block is
  detectable (recomputed hash != marker hash) and we refuse to overwrite.
- exactly one managed block; re-runs replace it in place, never duplicate.
"""

from __future__ import annotations

import hashlib
import re

VERSION = "v1"

_MARKER_RE = re.compile(
    r"<!-- oc-secretary:(?P<ver>\S+) issue=(?P<num>\d+) context=(?P<hash>\w+) -->\n"
    r"(?P<body>.*?)"
    r"\n<!-- /oc-secretary -->",
    re.DOTALL,
)


def checksum(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def build_section(issue_number: int, content_md: str) -> str:
    """Wrap rendered content in checksummed markers."""
    h = checksum(content_md)
    return (
        f"<!-- oc-secretary:{VERSION} issue={issue_number} context={h} -->\n"
        f"{content_md}\n"
        f"<!-- /oc-secretary -->"
    )


def extract(issue_body: str | None):
    """Return the regex match for an existing managed block, or None."""
    if not issue_body:
        return None
    return _MARKER_RE.search(issue_body)


def was_human_edited(issue_body: str | None) -> bool:
    """True if a managed block exists but its content no longer matches its checksum."""
    m = extract(issue_body)
    if not m:
        return False
    return checksum(m.group("body")) != m.group("hash")


def upsert(issue_body: str | None, issue_number: int, content_md: str) -> str:
    """Insert or replace the managed block; returns the new body."""
    section = build_section(issue_number, content_md)
    body = issue_body or ""
    m = extract(body)
    if m:
        return body[: m.start()] + section + body[m.end() :]
    return f"{body.rstrip()}\n\n{section}" if body.strip() else section
