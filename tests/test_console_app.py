"""Console app: auth gating, CSRF-guarded writes, escaping, security headers."""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient

from secretary.config import Settings
from secretary.console import app as app_mod
from secretary.console import auth
from secretary.db import repo as db_repo


class _DummyCM:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


@pytest.fixture()
def kv(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(app_mod, "surreal", lambda s: _DummyCM())
    monkeypatch.setattr(db_repo, "kv_get", lambda db, repo, key: store.get(key))
    monkeypatch.setattr(db_repo, "kv_set", lambda db, repo, key, value: store.__setitem__(key, value))
    monkeypatch.setattr(db_repo, "get_watermark", lambda db, repo, key: datetime(2026, 6, 13, tzinfo=timezone.utc))
    monkeypatch.setattr(db_repo, "counts", lambda db, repo: {"issues": 10, "open_issues": 4, "prs": 3})
    monkeypatch.setattr(db_repo, "event_dates", lambda db, repo, since: {"opened": [], "closed": [], "merged": []})
    monkeypatch.setattr(db_repo, "label_counts", lambda db, repo: {"<script>bad</script>": 2, "bug": 5})
    return store


def _client(password: str = "", **kw):
    settings = Settings(github_repo="o/r", console_session_secret="sign-me",
                        console_password=password, **kw)
    return TestClient(app_mod.build_app(settings))


# --- public read views --------------------------------------------------------

def test_status_page_renders(kv):
    r = _client().get("/")
    assert r.status_code == 200
    assert "10" in r.text and "Service status" in r.text


def test_security_headers_present(kv):
    r = _client().get("/")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_cluster_name_is_escaped(kv):
    r = _client().get("/clusters")
    assert "<script>bad</script>" not in r.text  # autoescaped
    assert "&lt;script&gt;" in r.text


# --- viewer-only mode (no password) ------------------------------------------

def test_viewer_only_hides_admin_surface(kv):
    c = _client()  # no password
    assert c.get("/login").status_code == 404
    assert c.post("/admin/label", data={"key": "k", "name": "n"}).status_code == 404
    assert c.get("/admin").status_code == 404


# --- admin auth + CSRF --------------------------------------------------------

def _admin_client(kv):
    pw_hash = auth.hash_password("s3cret")
    c = _client(password=pw_hash)
    return c


def test_login_flow_and_csrf_guarded_write(kv):
    c = _admin_client(kv)
    assert c.get("/login").status_code == 200

    # wrong password is rejected
    bad = c.post("/login", data={"password": "nope"}, follow_redirects=False)
    assert bad.status_code == 401

    # correct password establishes an admin session
    ok = c.post("/login", data={"password": "s3cret"}, follow_redirects=False)
    assert ok.status_code == 303

    # a write without a CSRF token is refused even with a valid session
    no_csrf = c.post("/admin/label", data={"key": "bug", "name": "Bugs"}, follow_redirects=False)
    assert no_csrf.status_code == 403

    # grab the real CSRF token from the admin form, then the write succeeds + persists
    admin_page = c.get("/admin")
    token = re.search(r'name="csrf" value="([^"]+)"', admin_page.text).group(1)
    saved = c.post("/admin/label", data={"key": "bug", "name": "Bugs", "csrf": token},
                   follow_redirects=False)
    assert saved.status_code == 303
    assert kv["console.label"] == {"bug": "Bugs"}


def test_admin_write_rejected_without_session(kv):
    c = _admin_client(kv)  # admin enabled but not logged in
    r = c.post("/admin/label", data={"key": "bug", "name": "Bugs", "csrf": "x"},
               follow_redirects=False)
    assert r.status_code == 403


def test_next_release_write_persists(kv):
    c = _admin_client(kv)
    c.post("/login", data={"password": "s3cret"}, follow_redirects=False)
    token = re.search(r'name="csrf" value="([^"]+)"', c.get("/admin").text).group(1)
    r = c.post("/admin/next-release",
               data={"categories": "bugs, perf", "items": "issue 12\npr 34", "csrf": token},
               follow_redirects=False)
    assert r.status_code == 303
    assert kv["console.next_release"] == {
        "categories": ["bugs", "perf"],
        "items": [{"kind": "issue", "number": 12}, {"kind": "pr", "number": 34}],
    }
