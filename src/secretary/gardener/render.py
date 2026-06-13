"""Render gardener findings into the managed "Backlog gardening" section.

Stable-ordered (by signal strength, then issue number) and stable-keyed so the section
diffs cleanly between runs: identical findings render byte-identical, so `section.upsert`
is a no-op when nothing changed.
"""

from __future__ import annotations

from secretary.gardener.signals import SIGNAL_ORDER, BORDERLINE, Finding

_HEADINGS = {
    "probably_fixed": "Probably fixed",
    "probably_duplicate": "Probably duplicate",
    "probably_superseded": "Probably superseded",
    "dormant": "Dormant",
}

INTRO = (
    "Backlog hygiene proposed by the secretary, with evidence. **These are suggestions, "
    "not actions** — nothing here is closed automatically. Add "
    "`<!-- gardener: keep -->` to an issue body to silence it permanently."
)


def _sort_key(f: Finding) -> tuple[int, int]:
    return (SIGNAL_ORDER.index(f.signal), f.issue)


def render(findings: list[Finding]) -> str:
    """Markdown body for the managed section (without the section markers)."""
    if not findings:
        return f"{INTRO}\n\n_No findings — the backlog looks tidy._"
    lines = [INTRO]
    ordered = sorted(findings, key=_sort_key)
    current: str | None = None
    for f in ordered:
        if f.signal != current:
            current = f.signal
            lines.append(f"\n### {_HEADINGS.get(f.signal, f.signal)}")
        flag = " _(borderline)_" if f.confidence == BORDERLINE else ""
        lines.append(f"\n- **#{f.issue}** — {f.summary}{flag}")
        for ev in f.evidence:
            lines.append(f"  - {ev}")
        lines.append(f"  - _Suggested:_ {f.suggestion}")
    return "\n".join(lines)
