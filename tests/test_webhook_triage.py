from secretary.config import Settings
from secretary.serve import triage as triage_mod
from secretary.serve.routing import TriageTask


class Recorder:
    def __init__(self):
        self.calls = []

    def patch(self, monkeypatch):
        monkeypatch.setattr(triage_mod.pipeline, "ingest_issue_or_pr",
                            lambda db, repo, client, raw: self.calls.append(("ingest_item", raw)) or "issue")
        monkeypatch.setattr(triage_mod.pipeline, "ingest_comment",
                            lambda db, repo, raw, prs: self.calls.append(("ingest_comment", raw)))
        monkeypatch.setattr(triage_mod, "embed_pending",
                            lambda db, embedder: self.calls.append(("embed",)) or {})
        monkeypatch.setattr(triage_mod.responder, "apply_comment",
                            lambda *a, **k: self.calls.append(("enrich",)) or "created comment")
        monkeypatch.setattr(triage_mod.labeler_apply, "run_labeler",
                            lambda *a, **k: self.calls.append(("labels", k.get("numbers"))) or [])

    @property
    def kinds(self):
        return [c[0] for c in self.calls]


def _settings(**over):
    over.setdefault("taxonomy_path", "x.toml")
    return Settings(github_repo="o/r", **over)


def test_full_triage_runs_ingest_embed_enrich_labels(monkeypatch):
    rec = Recorder(); rec.patch(monkeypatch)
    task = TriageTask("o/r", 7, "triage", {"number": 7}, "issue")
    triage_mod.run_task(task, db=None, embedder=object(), settings=_settings(), client=object())
    assert rec.kinds == ["ingest_item", "embed", "enrich", "labels"]
    assert rec.calls[-1] == ("labels", {7})  # scoped to the one issue


def test_ingest_only_event_skips_triage(monkeypatch):
    rec = Recorder(); rec.patch(monkeypatch)
    task = TriageTask("o/r", 7, "ingest", {"number": 7}, "issue")
    triage_mod.run_task(task, db=None, embedder=object(), settings=_settings(), client=object())
    assert rec.kinds == ["ingest_item"]


def test_comment_event_uses_ingest_comment(monkeypatch):
    rec = Recorder(); rec.patch(monkeypatch)
    task = TriageTask("o/r", 7, "ingest", {"id": 99}, "comment")
    triage_mod.run_task(task, db=None, embedder=object(), settings=_settings(), client=object())
    assert rec.kinds == ["ingest_comment"]


def test_serve_triage_false_is_ingest_only(monkeypatch):
    rec = Recorder(); rec.patch(monkeypatch)
    task = TriageTask("o/r", 7, "triage", {"number": 7}, "issue")
    triage_mod.run_task(task, db=None, embedder=object(),
                        settings=_settings(serve_triage=False), client=object())
    assert rec.kinds == ["ingest_item"]  # triage suppressed globally


def test_no_taxonomy_skips_labels_but_still_enriches(monkeypatch):
    rec = Recorder(); rec.patch(monkeypatch)
    task = TriageTask("o/r", 7, "triage", {"number": 7}, "issue")
    triage_mod.run_task(task, db=None, embedder=object(),
                        settings=_settings(taxonomy_path=""), client=object())
    assert rec.kinds == ["ingest_item", "embed", "enrich"]  # no labels step


# --- PR-routing evaluation test (the trick Codex flagged: prove a webhook PR payload
#     reaches client.get_pull, not the issue path) -------------------------------
class FakeClient:
    """Captures which fetch the ingest pipeline chose for a PR-shaped raw."""
    def __init__(self):
        self.get_pull_calls = []
        self.get_issue_calls = []

    def get_pull(self, number):
        self.get_pull_calls.append(number)
        return {"number": number, "title": "x", "state": "open", "user": {"login": "a"},
                "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
                "labels": [], "body": "", "merged_at": None, "html_url": ""}

    def get_issue(self, number):
        self.get_issue_calls.append(number)
        return {"number": number}


def test_pull_request_task_routes_through_get_pull_not_get_issue(monkeypatch):
    # Use the REAL pipeline.ingest_issue_or_pr so is_pull() actually runs; stub only the
    # DB writes it performs. This proves the {number, pull_request} wrapper routes as a PR.
    from secretary.ingest import pipeline
    from secretary.db import repo as db_repo

    monkeypatch.setattr(db_repo, "upsert_pr", lambda db, pr: None)
    monkeypatch.setattr(db_repo, "relate", lambda *a, **k: None)
    client = FakeClient()
    raw = {"number": 12, "pull_request": {"number": 12, "title": "x"}}
    task = TriageTask("o/r", 12, "ingest", raw, "pr")
    triage_mod.run_task(task, db=None, embedder=object(), settings=_settings(), client=client)
    assert client.get_pull_calls == [12]
    assert client.get_issue_calls == []


# --- WebhookSource.handle delegation (Task 8) ---------------------------------

def test_webhook_source_handle_delegates_to_triage(monkeypatch):
    from secretary.sources.webhook import WebhookSource

    captured = {}
    monkeypatch.setattr(
        "secretary.sources.webhook.run_task",
        lambda task, db, embedder, settings, client: captured.update(task=task),
    )
    src = WebhookSource(_settings(), embedder=object())
    payload = {"action": "opened", "repository": {"full_name": "o/r"},
               "issue": {"number": 7}}
    src.handle(db=None, client=object(), event="issues", payload=payload)
    assert captured["task"].number == 7
    assert captured["task"].action == "triage"


def test_webhook_source_handle_ignores_unrouted_event(monkeypatch):
    from secretary.sources.webhook import WebhookSource

    called = []
    monkeypatch.setattr(
        "secretary.sources.webhook.run_task",
        lambda *a, **k: called.append(1),
    )
    src = WebhookSource(_settings(), embedder=object())
    src.handle(db=None, client=object(), event="star",
               payload={"action": "created", "repository": {"full_name": "o/r"}})
    assert called == []  # unrouted events are a no-op
