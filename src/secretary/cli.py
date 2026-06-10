"""Command-line entrypoint for the memory backbone (multi-repo)."""

from __future__ import annotations

import logging
import signal
import threading

import typer

from secretary.config import Settings, get_settings, normalize_repo
from secretary.db import repo as db_repo
from secretary.db.connection import surreal
from secretary.embeddings.embedder import LocalEmbedder
from secretary.embeddings.service import embed_pending
from secretary.github.client import GitHubClient
from secretary.ingest import pipeline, reconcile
from secretary.labeler import apply as labeler_apply
from secretary.labeler.judge import anthropic_membership_judge
from secretary.steward import run as steward_run
from secretary.steward.board import GraphQLBoard
from secretary.organizer import plan as organizer_plan
from secretary.organizer import writer as organizer_writer
from secretary.organizer.judge import LLMJudge
from secretary.organizer.render import render as render_plan
from secretary.responder import responder
from secretary.semantic.related import find_related
from secretary.sources.polling import PollingSource

app = typer.Typer(add_completion=False, help="OpenCouncil memory backbone sync.")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _resolve_repo(settings: Settings, repo: str | None) -> str:
    """Pick the repo for a single-item command: explicit --repo, else the default.

    With several repos configured and no --repo, fall back to the first but warn,
    so an ambiguous invocation is at least visible.
    """
    if repo:
        return normalize_repo(repo)
    repos = settings.repo_list
    if len(repos) > 1:
        logging.getLogger("secretary.cli").warning(
            "multiple repos configured; defaulting to %s (pass --repo to choose)", repos[0]
        )
    return repos[0]


def _resolve_judge(settings: Settings, force: bool) -> tuple[LLMJudge | None, str | None]:
    """Decide whether the LLM judge runs, returning (judge, warning).

    The judge is requested by --judge or SECRETARY_JUDGE_ENABLED. When requested with no
    ANTHROPIC_API_KEY every score() call would abstain silently, so we disable it up
    front and return a warning the caller can surface, rather than running a no-op judge.
    """
    if not (force or settings.judge_enabled):
        return None, None
    if not settings.anthropic_api_key:
        return None, (
            "judge requested but ANTHROPIC_API_KEY is not set — running metrics-only"
        )
    return LLMJudge(settings), None


@app.command("init-db")
def init_db() -> None:
    """Apply the SurrealDB schema (idempotent)."""
    _setup_logging()
    settings = get_settings()
    with surreal(settings) as db:
        db_repo.apply_schema(db)
    typer.echo("schema applied")


@app.command()
def backfill() -> None:
    """Full ingest of every configured repo (issues, PRs, comments, cross-refs, projects)."""
    _setup_logging()
    settings = get_settings()
    with surreal(settings) as db:
        db_repo.apply_schema(db)
        for repo in settings.repo_list:
            with GitHubClient(settings, repo=repo) as client:
                report = reconcile.backfill(db, client, repo)
            typer.echo(f"backfill {repo}: {report}")
        # Final, order-independent pass once every repo is indexed.
        links = pipeline.link_cross_repo_mentions(db)
        typer.echo(f"cross-repo mention edges: {links}")


@app.command(name="reconcile")
def run_reconcile() -> None:
    """Incremental ingest of every configured repo since its last watermark."""
    _setup_logging()
    settings = get_settings()
    with surreal(settings) as db:
        db_repo.apply_schema(db)
        for repo in settings.repo_list:
            with GitHubClient(settings, repo=repo) as client:
                report = reconcile.reconcile(db, client, repo)
            typer.echo(f"reconcile {repo}: {report}")
        links = pipeline.link_cross_repo_mentions(db)
        typer.echo(f"cross-repo mention edges: {links}")


@app.command()
def embed() -> None:
    """Compute and store embeddings for issues/PRs that lack one (across all repos)."""
    _setup_logging()
    settings = get_settings()
    embedder = LocalEmbedder()
    with surreal(settings) as db:
        db_repo.apply_schema(db)
        counts = embed_pending(db, embedder)
    typer.echo(f"embedded: {counts}")


@app.command()
def related(
    number: int,
    repo: str | None = typer.Option(None, help="owner/name (defaults to the configured repo)"),
    k: int = 5,
    include_weak: bool = False,
) -> None:
    """Show classified related issues/PRs (cross-repo semantic search + reranker)."""
    _setup_logging()
    settings = get_settings()
    repo_name = _resolve_repo(settings, repo)
    embedder = LocalEmbedder()
    with surreal(settings) as db:
        kind = "pr" if db_repo.pr_exists(db, repo_name, number) else "issue"
        target = db_repo.get_meta(db, kind, repo_name, number)
        if target is None:
            typer.echo(f"{repo_name}#{number} not found")
            raise typer.Exit(1)
        items = find_related(
            db, embedder, repo_name, number, k=k,
            include_weak=include_weak, pair_set=settings.related_repo_pair_set,
        )
    typer.echo(f"Related to {repo_name}#{number} ({target.get('title')!r}):")
    for it in items:
        why = f"  [{', '.join(it.signals)}]" if it.signals else ""
        ref = f"{it.repo}#{it.number}" if it.repo and it.repo != repo_name else f"#{it.number}"
        typer.echo(
            f"  {it.kind} {ref:<18} {it.category:<22} "
            f"conf={it.confidence:.2f} dist={it.dist:.3f}  {it.title}{why}"
        )


@app.command()
def enrich(
    number: int,
    repo: str | None = typer.Option(None, help="owner/name (defaults to the configured repo)"),
    write: bool = False,
    target: str = "comment",
    force: bool = False,
) -> None:
    """Build an issue's enrichment (dry-run by default).

    --write posts it live; --target comment (default, any contributor) or body
    (Greptile-style, needs triage rights).
    """
    _setup_logging()
    settings = get_settings()
    repo_name = _resolve_repo(settings, repo)
    embedder = LocalEmbedder()
    with surreal(settings) as db:
        if not write:
            result = responder.enrich(db, embedder, settings, repo_name, number)
            typer.echo(result.section)
            return
        if target not in ("comment", "body"):
            typer.echo("--target must be 'comment' or 'body'")
            raise typer.Exit(2)
        with GitHubClient(settings, repo=repo_name) as client:
            if target == "comment":
                msg = responder.apply_comment(
                    client, db, embedder, settings, repo_name, number, force=force
                )
            else:
                msg = responder.apply_to_github(
                    client, db, embedder, settings, repo_name, number, force=force
                )
    typer.echo(msg)


@app.command()
def plan(
    milestone: str,
    repo: str | None = typer.Option(None, help="owner/name (defaults to the configured repo)"),
    judge: bool = typer.Option(False, help="force the LLM judge on for this run"),
    write: bool = typer.Option(False, help="create/update the 'Release plan' issue on GitHub"),
) -> None:
    """Assemble a milestone into a release plan (dry-run by default).

    Reads the milestone's members plus the graph/semantic layers, then prints (or with
    --write, posts) themes, dependency order, suggested adds, gaps, and a priority
    ranking. The LLM judge runs when --judge is passed or SECRETARY_JUDGE_ENABLED=true.
    """
    _setup_logging()
    settings = get_settings()
    repo_name = _resolve_repo(settings, repo)
    embedder = LocalEmbedder()
    judge_obj, judge_warning = _resolve_judge(settings, force=judge)
    if judge_warning:
        typer.echo(judge_warning, err=True)
    with surreal(settings) as db:
        release = organizer_plan.build(
            db, embedder, settings, repo_name, milestone, judge=judge_obj
        )
        if not release.ordered:
            typer.echo(f"no issues assigned to milestone {milestone!r} in {repo_name}")
            raise typer.Exit(1)
        if not write:
            typer.echo(render_plan(release))
            return
        with GitHubClient(settings, repo=repo_name) as client:
            msg = organizer_writer.write_plan(client, db, settings, release)
    typer.echo(msg)


@app.command()
def labels(
    repo: str | None = typer.Option(None, help="owner/name (defaults to the configured repo)"),
    all_issues: bool = typer.Option(False, "--all", help="classify already-labeled issues too"),
    apply: bool = typer.Option(False, help="act per mode (apply labels / post suggestions)"),
) -> None:
    """Classify issues into the taxonomy and suggest or apply labels (dry-run by default).

    Without --apply this prints the classification table and writes nothing. With --apply
    it honors SECRETARY_LABELER_MODE (suggest posts a report issue; auto applies labels)
    and the trust rules (additive only; human-removed labels are never re-applied).
    """
    _setup_logging()
    settings = get_settings()
    if not settings.taxonomy_path:
        typer.echo("no taxonomy configured; set SECRETARY_TAXONOMY_PATH", err=True)
        raise typer.Exit(1)
    repo_name = _resolve_repo(settings, repo)
    embedder = LocalEmbedder()
    judge_obj, judge_warning = _resolve_judge(settings, force=False)
    if judge_warning:
        typer.echo(judge_warning, err=True)
    judge_fn = anthropic_membership_judge(settings) if judge_obj is not None else None

    with surreal(settings) as db:
        client = GitHubClient(settings, repo=repo_name) if apply else None
        try:
            results = labeler_apply.run_labeler(
                db, embedder, client, settings, repo_name,
                include_labeled=all_issues, apply=apply, judge=judge_fn,
            )
        finally:
            if client is not None:
                client.close()

    if not results:
        typer.echo("no labels to suggest")
        return
    for r in sorted(results, key=lambda x: (x.action, x.dist)):
        typer.echo(f"{r.action:9} #{r.number}  {r.label}  dist={r.dist:.3f}")


@app.command()
def steward(
    repo: str | None = typer.Option(None, help="owner/name (defaults to the configured repo)"),
    milestone: str | None = typer.Option(None, help="source the priority ranking from this milestone"),
    apply: bool = typer.Option(False, help="write to the board (honored only in sync mode)"),
) -> None:
    """Sync board Status from linked PRs and surface the organizer's priority (dry-run by default).

    Status comes only from linked PRs and moves forward only. With --milestone the
    issues in that milestone also get a priority/score. Writes happen only with --apply
    and SECRETARY_STEWARD_MODE=sync; human-edited fields are vetoed permanently.
    """
    _setup_logging()
    settings = get_settings()
    repo_name = _resolve_repo(settings, repo)
    writes = apply and settings.steward_mode == "sync"

    with surreal(settings) as db:
        client = GitHubClient(settings, repo=repo_name) if writes else None
        board = GraphQLBoard(client, settings) if client is not None else None
        try:
            ranked: list[tuple[int, float]] | None = None
            if milestone:
                release = organizer_plan.build(
                    db, LocalEmbedder(), settings, repo_name, milestone
                )
                ranked = [(item.number, score.total) for item, score in release.ranked]
            actions = steward_run.run_steward(
                db, board, settings, repo_name, ranked=ranked, apply=apply
            )
        finally:
            if client is not None:
                client.close()

    if not actions:
        typer.echo("no board actions")
        return
    for a in actions:
        typer.echo(f"{a.kind:9} #{a.number}  {a.field} = {a.value}")


@app.command()
def run() -> None:
    """Run the polling source forever (reconcile every configured repo each interval)."""
    _setup_logging()
    settings = get_settings()
    source = PollingSource()
    interval = settings.poll_interval_seconds
    repos = settings.repo_list
    log = logging.getLogger("secretary.run")

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    log.info("polling every %ss against %s", interval, ", ".join(repos))
    while not stop.is_set():
        for repo in repos:
            if stop.is_set():
                break
            try:
                with surreal(settings) as db, GitHubClient(settings, repo=repo) as client:
                    db_repo.apply_schema(db)
                    report = source.run_once(db, client, repo)
                log.info("cycle done for %s: %s", repo, report)
            except Exception:  # noqa: BLE001 - one repo failing must not kill the loop
                log.exception("sync cycle failed for %s; will retry next interval", repo)
        if not stop.is_set():
            try:
                with surreal(settings) as db:
                    pipeline.link_cross_repo_mentions(db)
            except Exception:  # noqa: BLE001 - linking is best-effort
                log.exception("cross-repo mention linking failed; will retry next interval")
        stop.wait(interval)
    log.info("shutdown signal received; exiting")


def main() -> None:
    app()
