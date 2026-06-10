"""Idempotent SurrealDB writes/reads for the memory backbone (multi-repo).

Record ids are composite arrays so the same GitHub number in different repos never
collides: `issue:[repo, number]`, `pr:[repo, number]`, `comment:[repo, gh_id]`,
`project_item:[repo, gh_id]`, `sync_state:[repo, key]`. Record-addressing helpers
take `(kind, repo, number)`; the repo on the stored models carries through writes.
Re-ingesting an item updates in place; graph edges are DELETE-then-RELATE, keeping
them idempotent, and edge endpoints are full `(kind, repo, number)` triples so an
edge can cross repos.

Semantic search uses an exact cosine scan (`vector::similarity::cosine`) rather than
the HNSW operator: at this corpus size exact is correct and trivially cheap, and it
composes cleanly with a `WHERE repo = $r` scope filter (HNSW + post-filter would
wreck recall). The HNSW index stays defined for future scale.
"""

from __future__ import annotations

from datetime import datetime
from importlib import resources

from surrealdb import RecordID, Surreal

from secretary.github.models import Comment, Issue, PullRequest

_KINDS = ("issue", "pr")

# (kind, repo, number) edge endpoint.
Endpoint = tuple[str, str, int]


def _check_kind(kind: str) -> None:
    if kind not in _KINDS:
        raise ValueError(f"kind must be 'issue' or 'pr', got {kind!r}")


def load_schema() -> str:
    return resources.files("secretary.db").joinpath("schema.surql").read_text()


def apply_schema(db: Surreal) -> None:
    db.query(load_schema())


def upsert_issue(db: Surreal, issue: Issue) -> None:
    db.query(
        "UPSERT type::record('issue', [$repo, $number]) CONTENT $data",
        {"repo": issue.repo, "number": issue.number, "data": issue.model_dump(mode="python")},
    )


def upsert_pr(db: Surreal, pr: PullRequest) -> None:
    db.query(
        "UPSERT type::record('pr', [$repo, $number]) CONTENT $data",
        {"repo": pr.repo, "number": pr.number, "data": pr.model_dump(mode="python")},
    )


def upsert_comment(db: Surreal, comment: Comment, parent_kind: str) -> None:
    _check_kind(parent_kind)
    data = {
        "repo": comment.repo,
        "gh_id": comment.gh_id,
        "author": comment.author,
        "body": comment.body,
        "url": comment.url,
        "created_at": comment.created_at,
        "updated_at": comment.updated_at,
    }
    db.query(
        "UPSERT type::record('comment', [$repo, $id]) CONTENT "
        "object::extend($data, { parent: type::record($pk, [$repo, $pn]) })",
        {
            "repo": comment.repo,
            "id": comment.gh_id,
            "data": data,
            "pk": parent_kind,
            "pn": comment.parent_number,
        },
    )


def upsert_project_item(
    db: Surreal,
    repo: str,
    gh_id: str,
    status: str | None,
    fields: dict,
    content: tuple[str, int] | None,
) -> None:
    data: dict = {"repo": repo, "gh_id": gh_id, "status": status, "fields": fields}
    if content is not None:
        kind, number = content
        db.query(
            "UPSERT type::record('project_item', [$repo, $id]) CONTENT "
            "object::extend($data, { content: type::record($k, [$repo, $n]) })",
            {"repo": repo, "id": gh_id, "data": data, "k": kind, "n": number},
        )
    else:
        db.query(
            "UPSERT type::record('project_item', [$repo, $id]) CONTENT $data",
            {"repo": repo, "id": gh_id, "data": data},
        )


def relate(db: Surreal, source: Endpoint, kind: str, target: Endpoint) -> None:
    """Idempotently write a graph edge `source -kind-> target`.

    Endpoints are `(kind, repo, number)` triples, so edges may cross repos. `kind`
    is whitelisted to be templated into RELATE (the relation table name cannot be
    parameterized). Existing edges between the same endpoints are removed first.
    """
    if kind not in ("relates_to", "mentions"):
        raise ValueError(f"unknown relation kind: {kind!r}")
    src = RecordID(source[0], [source[1], source[2]])
    tgt = RecordID(target[0], [target[1], target[2]])
    db.query(
        f"DELETE {kind} WHERE in = $src AND out = $tgt;"
        f"RELATE $src->{kind}->$tgt;",
        {"src": src, "tgt": tgt},
    )


def pr_exists(db: Surreal, repo: str, number: int) -> bool:
    res = db.query(
        "SELECT id FROM type::record('pr', [$repo, $n])", {"repo": repo, "n": number}
    )
    return bool(res)


def issue_exists(db: Surreal, repo: str, number: int) -> bool:
    res = db.query(
        "SELECT id FROM type::record('issue', [$repo, $n])", {"repo": repo, "n": number}
    )
    return bool(res)


def iter_bodies(db: Surreal, kind: str) -> list[dict]:
    """All rows of `kind` with their body text: {repo, number, body}."""
    _check_kind(kind)
    res = db.query(f"SELECT repo, number, body FROM {kind}")
    return res or []


def set_embedding(db: Surreal, kind: str, repo: str, number: int, vector: list[float]) -> None:
    _check_kind(kind)
    db.query(
        "UPDATE type::record($kind, [$repo, $n]) SET embedding = $e",
        {"kind": kind, "repo": repo, "n": number, "e": vector},
    )


def fetch_unembedded(db: Surreal, kind: str, repo: str | None = None) -> list[dict]:
    """Rows of `kind` lacking an embedding: {repo, number, title, body}.

    `repo=None` spans every repo (default for the embed batch); pass a repo to scope.
    """
    _check_kind(kind)
    scope = "AND repo = $repo " if repo is not None else ""
    res = db.query(
        f"SELECT repo, number, title, body FROM {kind} "
        f"WHERE embedding IS NONE {scope}ORDER BY repo, number",
        {"repo": repo},
    )
    return res or []


def similar(
    db: Surreal,
    kind: str,
    vector: list[float],
    k: int = 5,
    repo: str | None = None,
) -> list[dict]:
    """Nearest neighbours of `vector` by exact cosine distance.

    `repo=None` searches across all repos (cross-repo memory); pass a repo to scope
    to one. Returns enough metadata for the reranker to classify in one round-trip.
    """
    _check_kind(kind)
    scope = "AND repo = $repo " if repo is not None else ""
    res = db.query(
        f"SELECT repo, number, title, state, labels, milestone, "
        f"(1 - vector::similarity::cosine(embedding, $q)) AS dist "
        f"FROM {kind} WHERE embedding IS NOT NONE {scope}"
        f"ORDER BY dist LIMIT {int(k)}",
        {"q": vector, "repo": repo},
    )
    return res or []


def get_embedding(db: Surreal, kind: str, repo: str, number: int) -> list[float] | None:
    _check_kind(kind)
    res = db.query(
        "SELECT embedding FROM type::record($kind, [$repo, $n])",
        {"kind": kind, "repo": repo, "n": number},
    )
    return res[0].get("embedding") if res else None


def get_meta(db: Surreal, kind: str, repo: str, number: int) -> dict | None:
    _check_kind(kind)
    res = db.query(
        "SELECT repo, number, title, body, state, labels, milestone "
        "FROM type::record($kind, [$repo, $n])",
        {"kind": kind, "repo": repo, "n": number},
    )
    return res[0] if res else None


def _parse_record(rid: object) -> Endpoint | None:
    """`RecordID -> (kind, repo, number)` for composite issue/pr endpoints.

    The SDK's RecordID exposes the composite id as `.id` (a `[repo, number]` list);
    its repr says `record_id=...` but no such attribute exists, so we read `.id`.
    """
    table = getattr(rid, "table_name", None)
    ident = getattr(rid, "id", None)
    if table and isinstance(ident, (list, tuple)) and len(ident) == 2:
        try:
            return (str(table), str(ident[0]), int(ident[1]))
        except (TypeError, ValueError):
            return None
    return None


def neighbors(db: Surreal, kind: str, repo: str, number: int) -> set[Endpoint]:
    """Items linked to (kind, repo, number) via any explicit edge, both directions.

    Returns (kind, repo, number) triples — repo is part of the identity because
    issue and PR numbers collide across repos, and edges may cross repos.
    """
    src = RecordID(kind, [repo, number])
    found: set[Endpoint] = set()
    for rel in ("relates_to", "mentions"):
        rows = db.query(
            f"SELECT in, out FROM {rel} WHERE in = $r OR out = $r", {"r": src}
        )
        for row in rows or []:
            for endpoint in (row.get("in"), row.get("out")):
                parsed = _parse_record(endpoint)
                if parsed and parsed != (kind, repo, number):
                    found.add(parsed)
    return found


_MEMBER_FIELDS = (
    "repo, number, title, body, state, labels, milestone, "
    "reactions, comments_count, created_at, updated_at"
)


def milestone_members(db: Surreal, repo: str, milestone: str) -> list[dict]:
    """Issues and PRs in `repo` assigned to `milestone`, each tagged with its `kind`.

    Reads everything the organizer needs (engagement counts, timestamps, body for
    dependency parsing) in one pass per kind. No GitHub calls — milestone membership
    is already ingested on every item.
    """
    out: list[dict] = []
    for kind in _KINDS:
        rows = db.query(
            f"SELECT {_MEMBER_FIELDS} FROM {kind} WHERE repo = $repo AND milestone = $m",
            {"repo": repo, "m": milestone},
        )
        for row in rows or []:
            out.append({**row, "kind": kind})
    return out


def milestone_embeddings(
    db: Surreal, repo: str, milestone: str
) -> dict[tuple[str, int], list[float]]:
    """Stored embeddings of every milestone member, keyed by (kind, number).

    One query per kind instead of a `get_embedding` round-trip per member — the
    organizer needs every member's vector twice (gaps + expand), so this removes the
    per-member N+1.
    """
    out: dict[tuple[str, int], list[float]] = {}
    for kind in _KINDS:
        rows = db.query(
            f"SELECT number, embedding FROM {kind} "
            "WHERE repo = $repo AND milestone = $m AND embedding IS NOT NONE",
            {"repo": repo, "m": milestone},
        )
        for row in rows or []:
            emb = row.get("embedding")
            if emb:
                out[(kind, int(row["number"]))] = emb
    return out


def find_issue_by_title_and_label(
    db: Surreal, repo: str, title: str, label: str
) -> int | None:
    """Number of the issue in `repo` with this exact title carrying `label`, if any.

    Used to adopt a pre-existing release-plan issue on the organizer's first run.
    """
    rows = db.query(
        "SELECT number FROM issue WHERE repo = $repo AND title = $t AND $label IN labels",
        {"repo": repo, "t": title, "label": label},
    )
    return int(rows[0]["number"]) if rows else None


def kv_get(db: Surreal, repo: str, key: str) -> object | None:
    """Read an organizer bookkeeping value (plan-issue number, judge cache entry)."""
    res = db.query(
        "SELECT value FROM type::record('organizer_kv', [$repo, $k])",
        {"repo": repo, "k": key},
    )
    return res[0].get("value") if res else None


def kv_set(db: Surreal, repo: str, key: str, value: object) -> None:
    db.query(
        "UPSERT type::record('organizer_kv', [$repo, $k]) CONTENT { value: $v }",
        {"repo": repo, "k": key, "v": value},
    )


def get_watermark(db: Surreal, repo: str, key: str) -> datetime | None:
    res = db.query(
        "SELECT last_synced_at FROM type::record('sync_state', [$repo, $k])",
        {"repo": repo, "k": key},
    )
    if res and isinstance(res, list) and res:
        return res[0].get("last_synced_at")
    return None


def set_watermark(db: Surreal, repo: str, key: str, ts: datetime) -> None:
    db.query(
        "UPSERT type::record('sync_state', [$repo, $k]) CONTENT { last_synced_at: $ts }",
        {"repo": repo, "k": key, "ts": ts},
    )
