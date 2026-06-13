import hashlib
import hmac
import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from secretary.config import Settings
from secretary.serve.server import build_handler

SECRET = "hooky"


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


class FakePool:
    def __init__(self, accept=True):
        self.accept = accept
        self.submitted = []

    def submit(self, task):
        self.submitted.append(task)
        return self.accept


def _server(pool, *, secret=SECRET):
    settings = Settings(github_repo="o/r", webhook_secret=secret,
                        webhook_path="/webhook")
    handler = build_handler(settings, pool, {"o/r"})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, httpd.server_address[1]


def _post(port, body: bytes, *, event="issues", sign=True, path="/webhook"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"X-GitHub-Event": event, "Content-Type": "application/json"}
    if sign:
        headers["X-Hub-Signature-256"] = _sign(body)
    conn.request("POST", path, body=body, headers=headers)
    resp = conn.getresponse()
    resp.read()
    conn.close()
    return resp.status


@pytest.fixture
def opened_body():
    return json.dumps({
        "action": "opened",
        "repository": {"full_name": "o/r"},
        "issue": {"number": 7},
    }).encode()


def test_signed_issue_opened_returns_202_and_enqueues(opened_body):
    pool = FakePool()
    httpd, port = _server(pool)
    try:
        assert _post(port, opened_body) == 202
        assert len(pool.submitted) == 1
        assert pool.submitted[0].number == 7
        assert pool.submitted[0].action == "triage"
    finally:
        httpd.shutdown()


def test_bad_signature_returns_401_and_does_not_enqueue(opened_body):
    pool = FakePool()
    httpd, port = _server(pool)
    try:
        assert _post(port, opened_body, sign=False) == 401
        assert pool.submitted == []
    finally:
        httpd.shutdown()


def test_ping_returns_200():
    pool = FakePool()
    httpd, port = _server(pool)
    try:
        body = json.dumps({"zen": "hi"}).encode()
        # ping must be signed too.
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/webhook", body=body, headers={
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": _sign(body),
        })
        assert conn.getresponse().status == 200
    finally:
        httpd.shutdown()


def test_unhandled_event_returns_204(opened_body):
    pool = FakePool()
    httpd, port = _server(pool)
    try:
        assert _post(port, opened_body, event="star") == 204
        assert pool.submitted == []
    finally:
        httpd.shutdown()


def test_wrong_path_returns_404(opened_body):
    pool = FakePool()
    httpd, port = _server(pool)
    try:
        assert _post(port, opened_body, path="/nope") == 404
    finally:
        httpd.shutdown()


def test_queue_full_returns_503(opened_body):
    pool = FakePool(accept=False)
    httpd, port = _server(pool)
    try:
        assert _post(port, opened_body) == 503
    finally:
        httpd.shutdown()
