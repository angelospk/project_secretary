"""Optional LLM synthesis over retrieved hits — grounded, cited, never free-floating.

Retrieval (`retrieve.query`) is the substance; this is a thin, optional layer that turns
hits into prose. It is strictly grounded: the prompt carries only the retrieved items,
instructs the model to cite item numbers and answer only from them, and states the
answer rests on N indexed items (the memory is only as fresh as the last reconcile). No
model configured ⇒ callers stay in raw mode; we never fabricate an answer here.
"""

from __future__ import annotations

from collections.abc import Callable

from secretary.config import Settings
from secretary.qa.retrieve import Hit


def synthesis_enabled(settings: Settings) -> bool:
    """Whether LLM synthesis can run: a model is named and its provider has creds."""
    if not settings.qa_model.strip():
        return False
    from secretary import llm  # local import keeps retrieval import-light

    return llm.credentials_ready(settings, settings.qa_provider_resolved)


def _ref(hit: Hit) -> str:
    return f"{hit.ref.repo}#{hit.ref.number}"


def build_prompt(question: str, hits: list[Hit]) -> str:
    """A grounded prompt: the question plus the retrieved items, citation-constrained."""
    lines = [
        "You answer questions about a software project's issue/PR backlog.",
        f"Answer the question using ONLY the {len(hits)} indexed items below. "
        "Cite the item numbers you rely on (e.g. #42). If the items do not contain "
        "the answer, say so plainly — do not guess. Begin your answer by noting it is "
        f"based on {len(hits)} indexed items (as of the last sync).",
        "",
        f"Question: {question}",
        "",
        "Indexed items:",
    ]
    for hit in hits:
        state = f", {hit.state}" if hit.state else ""
        lines.append(f"- {_ref(hit)} ({hit.ref.kind}{state}): {hit.title}")
        if hit.snippet:
            lines.append(f"    {hit.snippet}")
    return "\n".join(lines)


def answer(
    settings: Settings,
    question: str,
    hits: list[Hit],
    *,
    complete: Callable[[str], str] | None = None,
) -> str:
    """Generate a grounded answer. Caller guarantees `synthesis_enabled` (or injects
    `complete` in tests). With no hits, returns a no-evidence answer without an LLM call.
    """
    if not hits:
        return "No indexed items match that question (the memory is only as fresh as the last sync)."
    if complete is None:
        from secretary import llm

        complete = llm.make_complete(
            settings,
            provider=settings.qa_provider_resolved,
            model=settings.qa_model,
            max_tokens=settings.qa_max_tokens,
        )
    return complete(build_prompt(question, hits)).strip()
