import pytest

from secretary.serve.routing import TriageTask, build_task

ALLOWED = {"o/r"}


def _payload(**over):
    base = {"repository": {"full_name": "o/r"}, "issue": {"number": 7}}
    base.update(over)
    return base


def test_issue_opened_is_full_triage():
    task = build_task("issues", _payload(action="opened"), ALLOWED)
    assert task == TriageTask(repo="o/r", number=7, action="triage",
                              raw={"number": 7}, raw_kind="issue")


def test_issue_reopened_is_full_triage():
    task = build_task("issues", _payload(action="reopened"), ALLOWED)
    assert task is not None and task.action == "triage"


def test_issue_edited_is_ingest_only():
    task = build_task("issues", _payload(action="edited"), ALLOWED)
    assert task is not None and task.action == "ingest" and task.raw_kind == "issue"


def test_issue_comment_is_ingest_only():
    payload = {"repository": {"full_name": "o/r"},
               "issue": {"number": 7}, "comment": {"id": 99}}
    task = build_task("issue_comment", {**payload, "action": "created"}, ALLOWED)
    assert task is not None and task.action == "ingest" and task.raw_kind == "comment"
    assert task.raw == {"id": 99}
    assert task.number == 7


def test_pull_request_is_ingest_only_and_wraps_raw():
    payload = {"repository": {"full_name": "o/r"},
               "pull_request": {"number": 12, "title": "x"}}
    task = build_task("pull_request", {**payload, "action": "opened"}, ALLOWED)
    assert task is not None and task.action == "ingest" and task.raw_kind == "pr"
    assert task.number == 12
    # Wrapped into issues-listing shape so pipeline.is_pull() routes it as a PR.
    assert task.raw == {"number": 12, "pull_request": {"number": 12, "title": "x"}}


def test_unknown_event_is_ignored():
    assert build_task("star", _payload(action="created"), ALLOWED) is None


def test_unknown_action_is_ignored():
    assert build_task("issues", _payload(action="locked"), ALLOWED) is None


@pytest.mark.parametrize("action", ["closed", "labeled", "unlabeled", "assigned",
                                    "transferred", "deleted"])
def test_ignored_issue_actions_are_no_ops(action):
    # Documented as intentionally ignored: reconcile owns truth for these.
    assert build_task("issues", _payload(action=action), ALLOWED) is None


def test_projects_v2_item_event_is_ignored():
    # Explicitly out of scope for #7.
    payload = {"repository": {"full_name": "o/r"}, "action": "created",
               "projects_v2_item": {"id": 1}}
    assert build_task("projects_v2_item", payload, ALLOWED) is None


def test_unconfigured_repo_is_ignored():
    assert build_task("issues", _payload(action="opened"), {"other/repo"}) is None


def test_repo_full_name_is_normalized_before_matching():
    payload = {"repository": {"full_name": "O/R"}, "issue": {"number": 7},
               "action": "opened"}
    task = build_task("issues", payload, ALLOWED)
    assert task is not None and task.repo == "o/r"


def test_missing_repository_is_ignored():
    assert build_task("issues", {"action": "opened", "issue": {"number": 7}}, ALLOWED) is None
