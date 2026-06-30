"""Command-line entrypoint for the memory backbone (multi-repo)."""

from __future__ import annotations

import logging
import signal
import threading
from datetime import datetime, timezone

import typer

from secretary.config import Settings, get_settings, normalize_repo
from secretary.console.auth import hash_password
from secretary.db import migrate as db_migrate
from secretary.db import repo as db_repo
from secretary.db.connection import surreal
from secretary.embeddings.embedder import LocalEmbedder
from secretary.embeddings.service import embed_pending
from secretary.github.client import GitHubClient
from secretary.ingest import pipeline, reconcile
from secretary import llm
from secretary.labeler import apply as labeler_apply
from secretary.labeler.bootstrap import propose_taxonomy
from secretary.labeler.judge import default_membership_judge
from secretary.steward import run as steward_run
from secretary.steward.board import GraphQLBoard
from secretary.organizer import drift as organizer_drift
from secretary.organizer import plan as organizer_plan
from secretary.organizer.judge import LLMJudge
from secretary.organizer.render import render as render_plan
from secretary.gardener import garden as gardener_garden
from secretary.gardener.judge import default_supersede_judge
from secretary.qa import tools as qa_tools
from secretary.reporter import report as reporter_report
from secretary.reporter.notes import build_notes
from secretary.responder import responder
from secretary.semantic.related import find_related
from secretary.serve.server import serve as serve_webhooks
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

    The judge is requested by --judge or SECRETARY_JUDGE_ENABLED. When requested but the
    configured provider has no credentials (API key, or a CLI command for the cli
    provider), every score() call would abstain silently, so we disable it up front and
    return a warning the caller can surface, rather than running a no-op judge.
    """
    if not (force or settings.judge_enabled):
        return None, None
    if not llm.credentials_ready(settings):
        return None, (
            f"judge requested but {llm.requirement_hint(settings)} is not set "
            f"(provider={settings.judge_provider}) — running metrics-only"
        )
    return LLMJudge(settings), None


@app.command("init-db")
def init_db() -> None:
    """Apply the SurrealDB schema and any pending migrations (idempotent)."""
    _setup_logging()
    settings = get_settings()
    with surreal(settings) as db:
        applied = db_migrate.ensure_schema(db)
    typer.echo(f"schema applied; migrations: {applied or 'none (up to date)'}")


@app.command()
def migrate() -> None:
    """Apply the schema + run pending forward migrations, then report (idempotent).

    Refuses if the database was written by a newer secretary than this build (a
    migration is recorded that this version does not ship), so an accidental
    downgrade fails loudly instead of corrupting data.
    """
    _setup_logging()
    settings = get_settings()
    with surreal(settings) as db:
        applied = db_migrate.ensure_schema(db)
    typer.echo(f"migrations applied: {applied or 'none (up to date)'}")


@app.command()
def backfill() -> None:
    """Full ingest of every configured repo (issues, PRs, comments, cross-refs, projects)."""
    _setup_logging()
    settings = get_settings()
    with surreal(settings) as db:
        applied = db_migrate.ensure_schema(db)
        if applied:
            typer.echo(f"migrations applied: {applied}")
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
        applied = db_migrate.ensure_schema(db)
        if applied:
            typer.echo(f"migrations applied: {applied}")
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
    force: bool = typer.Option(False, help="rewrite even if the plan fingerprint is unchanged"),
) -> None:
    """Assemble a milestone into a release plan (dry-run by default).

    Reads the milestone's members plus the graph/semantic layers, then prints (or with
    --write, posts) themes, dependency order, suggested adds, gaps, and a priority
    ranking. The LLM judge runs when --judge is passed or SECRETARY_JUDGE_ENABLED=true.

    With --write the plan is fingerprinted: an unchanged milestone is skipped (no judge
    calls, no rewrite) unless --force is passed. The same short-circuit runs every poll
    cycle for SECRETARY_PLAN_MILESTONES.
    """
    _setup_logging()
    settings = get_settings()
    repo_name = _resolve_repo(settings, repo)
    embedder = LocalEmbedder()
    judge_obj, judge_warning = _resolve_judge(settings, force=judge)
    if judge_warning:
        typer.echo(judge_warning, err=True)
    with surreal(settings) as db:
        if not write:
            release = organizer_plan.build(
                db, embedder, settings, repo_name, milestone, judge=judge_obj
            )
            if not release.ordered:
                typer.echo(f"no issues assigned to milestone {milestone!r} in {repo_name}")
                raise typer.Exit(1)
            typer.echo(render_plan(release))
            return
        with GitHubClient(settings, repo=repo_name) as client:
            result = organizer_drift.maintain_plan(
                db, embedder, client, settings, repo_name, milestone,
                judge=judge_obj, write=True, force=force,
            )
    typer.echo(result.message)


@app.command(name="taxonomy-suggest")
def taxonomy_suggest(
    repo: str | None = typer.Option(None, help="owner/name (defaults to the configured repo)"),
    min_count: int = typer.Option(3, "--min-count", help="min issues carrying a label to propose it"),
    examples: int = typer.Option(3, "--examples", help="example issue numbers to seed each category"),
) -> None:
    """Propose a starter taxonomy (TOML) from the repo's existing labels — never writes.

    A cold-start helper: turns the labels a repo already uses into a first-draft taxonomy
    you edit (fill the descriptions) and save to SECRETARY_TAXONOMY_PATH. Generic workflow
    labels (bug/enhancement/…) are skipped. Redirect the output to a file to keep it.
    """
    _setup_logging()
    settings = get_settings()
    repo_name = _resolve_repo(settings, repo)
    with surreal(settings) as db:
        issues = db_repo.open_issues(db, repo_name)
    toml_text = propose_taxonomy(issues, min_count=min_count, examples_per=examples)
    if not toml_text:
        typer.echo(
            f"no non-generic label is used by >= {min_count} open issues in {repo_name}; "
            "nothing to propose (lower --min-count to widen the net)",
            err=True,
        )
        return
    typer.echo(toml_text)


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
    judge_fn = default_membership_judge(settings) if judge_obj is not None else None

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
def ask(
    question: str,
    repo: str | None = typer.Option(None, help="scope to owner/name (default: all indexed repos)"),
    raw: bool = typer.Option(False, help="print structured hits only; never call the LLM"),
    k: int | None = typer.Option(None, "-k", help="number of vector hits (default SECRETARY_QA_TOP_K)"),
) -> None:
    """Answer a question from backlog memory (retrieval first, LLM synthesis optional).

    Searches every indexed repo by default (cross-repo memory); pass --repo to scope.
    Without an LLM configured (SECRETARY_QA_MODEL), or with --raw, prints the structured
    hits the answer would be grounded on.
    """
    _setup_logging()
    settings = get_settings()
    embedder = LocalEmbedder()
    hits, synthesized = qa_tools.answer_question(
        settings, embedder, question, repo=repo, k=k, raw=raw
    )
    if synthesized is not None:
        typer.echo(synthesized)
        typer.echo("")
    if not hits:
        typer.echo("no indexed items match (memory is only as fresh as the last sync)")
        return
    typer.echo(f"— grounded on {len(hits)} indexed items —")
    for h in hits:
        score = f"{h.score:.2f}" if h.score is not None else " edge"
        typer.echo(f"  {h.why:6} {score}  {h.ref.kind} {h.ref.repo}#{h.ref.number}  {h.title}")


@app.command()
def garden(
    repo: str | None = typer.Option(None, help="owner/name (defaults to the configured repo)"),
    apply: bool = typer.Option(False, help="write per gardener_mode (report section / advisory comments)"),
) -> None:
    """Propose stale-issue closures with evidence (dry-run by default; never closes).

    Checks each open issue for: a merged PR that fixed it, a duplicate, a closed item that
    superseded it, or dormancy. Without --apply it prints the findings. With --apply it
    honors SECRETARY_GARDENER_MODE (report maintains a "Backlog gardening" issue; comment
    also leaves one advisory per finding) and the human veto.
    """
    _setup_logging()
    settings = get_settings()
    repo_name = _resolve_repo(settings, repo)
    if apply and settings.gardener_mode == "off":
        typer.echo("gardener_mode=off; set report|comment to apply", err=True)
        raise typer.Exit(1)
    embedder = LocalEmbedder()
    judge_obj, judge_warning = _resolve_judge(settings, force=False)
    if judge_warning:
        typer.echo(judge_warning, err=True)
    judge_fn = default_supersede_judge(settings) if judge_obj is not None else None
    now = datetime.now(timezone.utc)

    with surreal(settings) as db:
        client = GitHubClient(settings, repo=repo_name) if apply else None
        try:
            findings = gardener_garden.run_gardener(
                db, embedder, client, settings, repo_name,
                now=now, judge=judge_fn, apply=apply,
            )
        finally:
            if client is not None:
                client.close()

    if not findings:
        typer.echo("no findings — the backlog looks tidy")
        return
    for f in sorted(findings, key=lambda x: (x.signal, x.issue)):
        flag = " (borderline)" if f.confidence == "borderline" else ""
        typer.echo(f"{f.signal:20} #{f.issue}{flag}  {f.summary}")


@app.command()
def digest(
    repo: str | None = typer.Option(None, help="owner/name (defaults to the configured repo)"),
    since: str | None = typer.Option(None, help="window start YYYY-MM-DD (default: last digest watermark)"),
    apply: bool = typer.Option(False, help="write the managed section + Discord, then advance the watermark"),
) -> None:
    """Render the maintainer digest for the window since the last one (dry-run by default).

    Activity counts, notable new issues, new duplicate pairs, plan drift, gardener
    findings (if enabled), and secretary health. With --apply it writes the managed
    section on the "Secretary digest" issue, posts to Discord if configured, and advances
    the watermark; an empty window writes nothing.
    """
    _setup_logging()
    settings = get_settings()
    repo_name = _resolve_repo(settings, repo)
    since_dt = None
    if since:
        try:
            since_dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            typer.echo("--since must be YYYY-MM-DD", err=True)
            raise typer.Exit(2) from None
    embedder = LocalEmbedder()
    now = datetime.now(timezone.utc)
    with surreal(settings) as db:
        client = GitHubClient(settings, repo=repo_name) if apply else None
        try:
            _, body = reporter_report.run_digest(
                db, embedder, client, settings, repo_name,
                now=now, since=since_dt, apply=apply,
            )
        finally:
            if client is not None:
                client.close()
    typer.echo(body)


@app.command()
def notes(
    milestone: str,
    repo: str | None = typer.Option(None, help="owner/name (defaults to the configured repo)"),
    style: str = typer.Option("dev", help="dev (mechanical) | user (reword titles via the LLM)"),
    output: str | None = typer.Option(None, "-o", "--output", help="write to FILE instead of stdout"),
) -> None:
    """Draft release notes for a milestone from its closed issues + merged PRs.

    Grouped by organizer theme. `dev` is mechanical (no LLM); `user` rewords titles into
    user-facing language via the configured provider (rewording only — the list is fixed).
    """
    _setup_logging()
    settings = get_settings()
    if style not in ("dev", "user"):
        typer.echo("--style must be 'dev' or 'user'", err=True)
        raise typer.Exit(2)
    repo_name = _resolve_repo(settings, repo)
    complete = None
    if style == "user":
        if llm.credentials_ready(settings):
            complete = llm.make_complete(settings)
        else:
            typer.echo(
                f"user style needs {llm.requirement_hint(settings)}; falling back to dev",
                err=True,
            )
            style = "dev"  # no creds → mechanical draft, not a half-user run
    with surreal(settings) as db:
        text = build_notes(db, settings, repo_name, milestone, style=style, complete=complete)
    if output:
        with open(output, "w", encoding="utf-8") as fh:
            fh.write(text)
        typer.echo(f"wrote {output}")
    else:
        typer.echo(text)


@app.command()
def mcp() -> None:
    """Run a read-only MCP server over stdio (e.g. for `claude mcp add`).

    Exposes search_backlog / get_item / related / release_plan. No tool writes — an
    agent with this server attached can learn anything and change nothing. stdio means
    no port and no auth: the server inherits this process's filesystem and env.
    """
    try:
        from secretary.qa.mcp_server import serve_mcp
    except ImportError as exc:
        typer.echo(
            "the 'mcp' extra is not installed — run: uv sync --extra mcp",
            err=True,
        )
        raise typer.Exit(1) from exc
    _setup_logging()  # logs to stderr; the MCP JSON-RPC stream owns stdout
    settings = get_settings()
    serve_mcp(settings)


@app.command()
def console() -> None:
    """Serve the read-mostly web console (insights + light management).

    Public viewer; admin gated by SECRETARY_CONSOLE_PASSWORD (a scrypt hash — generate it
    with `secretary console-hash`). Empty password ⇒ viewer-only, no admin surface. Binds
    SECRETARY_CONSOLE_HOST:PORT; put it behind a TLS-terminating proxy to expose it.
    """
    try:
        from secretary.console.app import serve_console
    except ImportError as exc:
        typer.echo(
            "the 'console' extra is not installed — run: uv sync --extra console",
            err=True,
        )
        raise typer.Exit(1) from exc
    _setup_logging()
    settings = get_settings()
    serve_console(settings)


@app.command(name="console-hash")
def console_hash(
    password: str = typer.Option(
        ..., prompt=True, hide_input=True, confirmation_prompt=True,
        help="the admin password to hash",
    ),
) -> None:
    """Hash a console admin password (scrypt) for SECRETARY_CONSOLE_PASSWORD."""
    typer.echo(hash_password(password))


@app.command()
def run() -> None:
    """Run the polling source forever (reconcile every configured repo each interval)."""
    _setup_logging()
    settings = get_settings()
    source = PollingSource()
    interval = settings.poll_interval_seconds
    repos = settings.repo_list
    log = logging.getLogger("secretary.run")

    plan_milestones = settings.plan_milestone_list
    embedder = LocalEmbedder() if plan_milestones else None
    plan_judge, plan_warning = _resolve_judge(settings, force=False)
    if plan_milestones and plan_warning:
        log.warning("%s", plan_warning)

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
                    db_migrate.ensure_schema(db)
                    report = source.run_once(db, client, repo)
                    for milestone in plan_milestones:
                        result = organizer_drift.maintain_plan(
                            db, embedder, client, settings, repo, milestone,
                            judge=plan_judge, write=True,
                        )
                        log.info("plan %s/%s: %s", repo, milestone, result.message)
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


@app.command()
def serve() -> None:
    """Receive GitHub webhooks and run triage for the named item in seconds.

    A latency optimization alongside `secretary run`: a freshly opened issue gets its
    duplicate check, enrichment comment, and label suggestions immediately, instead of
    waiting for the next reconcile interval. The reconcile loop still owns ingestion —
    a missed or duplicated webhook costs latency only, never correctness.

    Binds SECRETARY_WEBHOOK_HOST:PORT and verifies X-Hub-Signature-256. Refuses to start
    without SECRETARY_WEBHOOK_SECRET. Exposure (smee.io / Cloudflare Tunnel / reverse
    proxy) is the operator's choice — see docs/deployment-webhook.md.
    """
    _setup_logging()
    settings = get_settings()
    serve_webhooks(settings)


def main() -> None:
    app()
