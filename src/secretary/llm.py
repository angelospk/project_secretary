"""Provider-agnostic single-shot LLM completion for the judges.

One `complete(prompt) -> str` callable, selected by `judge_provider`:

- `anthropic` / `openai` / `gemini`: an HTTP call over the existing httpx dependency,
  reading that provider's API key. `openai` also covers any OpenAI-compatible endpoint
  via `openai_base_url` (vLLM, LM Studio, OpenRouter, a local server, …).
- `cli`: shell out to a configured local command — `claude -p`, `gemini`,
  `ollama run <model>`, `codex exec`, anything — passing the prompt on stdin (or in
  place of a `{prompt}` token). No API key at all.

The judges' parsers (`SCORE:`/`WHY:`, `YES`/`NO`) tolerate surrounding noise, so a CLI
that wraps the model's answer in banners still works. Every backend raises on failure;
the judge treats a raised call as an abstention.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
from collections.abc import Callable

import httpx

from secretary.config import Settings

log = logging.getLogger(__name__)

_HTTP_TIMEOUT = 30


def anthropic_complete(settings: Settings, prompt: str) -> str:
    key = settings.anthropic_api_key
    if not key:
        raise RuntimeError("judge_provider=anthropic but ANTHROPIC_API_KEY is not set")
    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": settings.judge_model,
            "max_tokens": settings.judge_max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    blocks = resp.json().get("content", [])
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def openai_complete(settings: Settings, prompt: str) -> str:
    key = settings.openai_api_key
    if not key:
        raise RuntimeError("judge_provider=openai but OPENAI_API_KEY is not set")
    resp = httpx.post(
        f"{settings.openai_base_url.rstrip('/')}/chat/completions",
        headers={"authorization": f"Bearer {key}", "content-type": "application/json"},
        json={
            "model": settings.judge_model,
            "max_tokens": settings.judge_max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    choices = resp.json().get("choices", [])
    if not choices:
        return ""
    return choices[0].get("message", {}).get("content", "") or ""


def gemini_complete(settings: Settings, prompt: str) -> str:
    key = settings.gemini_api_key
    if not key:
        raise RuntimeError(
            "judge_provider=gemini but GEMINI_API_KEY / GOOGLE_API_KEY is not set"
        )
    resp = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.judge_model}:generateContent",
        headers={"x-goog-api-key": key, "content-type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": settings.judge_max_tokens},
        },
        timeout=_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    candidates = resp.json().get("candidates", [])
    if not candidates:
        return ""
    parts = candidates[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts)


def cli_complete(settings: Settings, prompt: str) -> str:
    """Run a local CLI, feeding it the prompt, and return its stdout.

    The command is split with shell-like quoting. If it contains a `{prompt}` token the
    prompt replaces that token (for CLIs that take the prompt as an argument); otherwise
    the prompt is written to the command's stdin (the robust default — no length or
    escaping limits).
    """
    command = settings.judge_cli_command.strip()
    if not command:
        raise RuntimeError("judge_provider=cli but SECRETARY_JUDGE_CLI_COMMAND is not set")
    args = shlex.split(command)
    timeout = settings.judge_cli_timeout
    try:
        if "{prompt}" in args:
            args = [prompt if a == "{prompt}" else a for a in args]
            proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        else:
            proc = subprocess.run(
                args, input=prompt, capture_output=True, text=True, timeout=timeout
            )
    except FileNotFoundError as exc:
        raise RuntimeError(f"judge CLI not found: {args[0]!r}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"judge CLI {args[0]!r} timed out after {timeout}s") from exc
    if proc.returncode != 0:
        raise RuntimeError(
            f"judge CLI {args[0]!r} failed (exit {proc.returncode}): "
            f"{(proc.stderr or '').strip()[:200]}"
        )
    return proc.stdout


_BACKENDS: dict[str, Callable[[Settings, str], str]] = {
    "anthropic": anthropic_complete,
    "openai": openai_complete,
    "gemini": gemini_complete,
    "cli": cli_complete,
}

PROVIDERS = tuple(_BACKENDS)

# What each provider needs configured, for credential checks and the CLI warning.
_REQUIREMENT = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY / GOOGLE_API_KEY",
    "cli": "SECRETARY_JUDGE_CLI_COMMAND",
}


def make_complete(
    settings: Settings,
    *,
    provider: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
) -> Callable[[str], str]:
    """A `complete(prompt) -> str` bound to a provider.

    Defaults come from the judge config (`judge_provider`/`judge_model`/
    `judge_max_tokens`); each can be overridden so another consumer (e.g. the Q&A
    synthesizer) runs its own model and token budget without borrowing the judge's. The
    backends read `judge_model`/`judge_max_tokens` off Settings, so when an override is
    given we hand them a copy with just those fields set.
    """
    resolved = (provider or settings.judge_provider).strip().lower()
    if resolved not in _BACKENDS:
        raise ValueError(f"unknown provider {resolved!r}")
    if resolved != settings.judge_provider.strip().lower() and model is None:
        # A model name is provider-specific; switching provider while inheriting the
        # judge's model would silently send a cross-provider name. Demand an explicit one.
        raise ValueError("changing provider requires an explicit model")
    backend = _BACKENDS[resolved]
    bound = settings
    if model is not None or max_tokens is not None:
        bound = settings.model_copy(
            update={
                "judge_model": model if model is not None else settings.judge_model,
                "judge_max_tokens": (
                    max_tokens if max_tokens is not None else settings.judge_max_tokens
                ),
            }
        )
    return lambda prompt: backend(bound, prompt)


def credentials_ready(settings: Settings, provider: str | None = None) -> bool:
    """Whether a provider has what it needs to run (key or CLI command).

    Defaults to the judge provider; pass `provider` to check a different one (the Q&A
    layer resolves its own provider that may differ from the judge's).
    """
    provider = provider or settings.judge_provider
    if provider == "anthropic":
        return bool(settings.anthropic_api_key)
    if provider == "openai":
        return bool(settings.openai_api_key)
    if provider == "gemini":
        return bool(settings.gemini_api_key)
    if provider == "cli":
        return bool(settings.judge_cli_command.strip())
    return False


def requirement_hint(settings: Settings, provider: str | None = None) -> str:
    return _REQUIREMENT.get(provider or settings.judge_provider, "judge credentials")
