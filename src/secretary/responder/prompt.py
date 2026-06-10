"""Build the DeepWiki query for an issue, seeded with reranked related items."""

from __future__ import annotations

from secretary.semantic.reranker import RelatedItem

_BODY_LIMIT = 3000
_MAX_RELATED_HINTS = 6


def build_query(target: dict, related: list[RelatedItem]) -> str:
    lines = [f"GitHub issue #{target['number']}: {target.get('title', '')}", ""]
    body = (target.get("body") or "").strip()
    if body:
        lines += [body[:_BODY_LIMIT], ""]

    target_repo = target.get("repo", "")
    hints = [r for r in related if r.category != "weak_match"][:_MAX_RELATED_HINTS]
    if hints:
        lines.append("Possibly related prior issues/PRs (for your awareness):")
        for r in hints:
            ref = f"{r.repo}#{r.number}" if r.repo and r.repo != target_repo else f"#{r.number}"
            lines.append(f"- {r.kind} {ref} ({r.category}, {r.state}): {r.title}")
        lines.append("")

    lines += [
        "Please answer concisely, grounded in the actual codebase:",
        "1. Relevant code context: which files/modules/functions this likely touches.",
        "2. Complexity assessment for resolving this issue.",
        "3. Edge cases and risks to watch for.",
        "4. Open questions that should be clarified before implementation.",
    ]
    return "\n".join(lines)
