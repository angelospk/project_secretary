"""Render a ReleasePlan to the markdown that goes inside the managed plan section."""

from __future__ import annotations

from secretary.organizer.models import PriorityScore, ReleasePlan

_WARNING_ICON = {
    "gap": "⚠️ gap",
    "done": "✅ done",
    "duplicate": "🔁 duplicate",
    "stale_critical": "🕸️ stale",
}


def _ref(repo: str, number: int, plan_repo: str) -> str:
    return f"{repo}#{number}" if repo and repo != plan_repo else f"#{number}"


def _score_breakdown(score: PriorityScore) -> str:
    parts = [f"{k} {v:.2f}" for k, v in score.components.items() if v > 0]
    return ", ".join(parts) if parts else "no signal"


def render(plan: ReleasePlan) -> str:
    lines: list[str] = [f"## Release plan: {plan.milestone}", ""]

    if not plan.ordered:
        lines.append("_No issues are assigned to this milestone yet._")
        return "\n".join(lines)

    lines.append(f"**{len(plan.ordered)} items.** Dependency order, then priority below.")
    lines.append("")

    lines.append("### Themes")
    for theme in plan.themes:
        refs = ", ".join(f"#{i.number}" for i in theme.items)
        lines.append(f"- **{theme.name}** — {refs}")
    lines.append("")

    lines.append("### Suggested order")
    for i, item in enumerate(plan.ordered, 1):
        dep = f" (after {', '.join(f'#{n}' for n in sorted(item.depends_on))})" if item.depends_on else ""
        lines.append(f"{i}. {_ref(item.repo, item.number, plan.repo)} {item.title}{dep}")
    lines.append("")

    lines.append("### Priority" + (" (incl. LLM judge)" if plan.judged else ""))
    for item, score in plan.ranked:
        why = f"  _{score.judge_reason}_" if score.judge_reason else ""
        lines.append(
            f"- **{score.total:.2f}** {_ref(item.repo, item.number, plan.repo)} "
            f"{item.title} — [{_score_breakdown(score)}]{why}"
        )
    lines.append("")

    if plan.suggested_adds:
        lines.append("### Suggested adds (not in milestone)")
        for add in plan.suggested_adds:
            lines.append(
                f"- {_ref(add.repo, add.number, plan.repo)} {add.title} "
                f"— {add.reason} (dist {add.dist:.2f})"
            )
        lines.append("")

    if plan.warnings:
        lines.append("### Coherence")
        for w in plan.warnings:
            lines.append(f"- {_WARNING_ICON.get(w.kind, w.kind)}: {w.message}")
        lines.append("")

    return "\n".join(lines).rstrip()
