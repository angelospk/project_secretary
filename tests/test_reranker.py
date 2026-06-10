from secretary.semantic.reranker import (
    CONCEPTUAL_CONTEXT,
    DUPLICATE,
    HISTORICAL_REFERENCE,
    IMPLEMENTATION_OVERLAP,
    WEAK_MATCH,
    classify,
)


def _t(title="Add email notifications", labels=None, milestone=None):
    return {"title": title, "labels": labels or [], "milestone": milestone}


def _c(number=2, title="Email notifications broken", state="open", labels=None, milestone=None):
    return {"number": number, "title": title, "state": state, "labels": labels or [], "milestone": milestone}


def test_weak_match_when_far():
    item = classify(_t(), "issue", _c(), dist=0.85, has_edge=False)
    assert item.category == WEAK_MATCH


def test_duplicate_when_close_shared_labels_and_title():
    item = classify(
        _t(labels=["notifications"]),
        "issue",
        _c(title="Add email notifications", labels=["notifications"]),
        dist=0.25,
        has_edge=False,
    )
    assert item.category == DUPLICATE
    assert any(s.startswith("labels:") for s in item.signals)


def test_historical_when_closed():
    item = classify(_t(), "issue", _c(state="closed", title="totally different words here"), dist=0.50, has_edge=False)
    assert item.category == HISTORICAL_REFERENCE


def test_implementation_overlap_for_open_pr():
    item = classify(_t(), "pr", _c(state="open", title="totally different words here"), dist=0.50, has_edge=False)
    assert item.category == IMPLEMENTATION_OVERLAP


def test_conceptual_for_open_issue_moderate():
    item = classify(_t(), "issue", _c(state="open", title="totally different words here"), dist=0.50, has_edge=False)
    assert item.category == CONCEPTUAL_CONTEXT


def test_graph_edge_overrides_to_historical_when_closed():
    item = classify(_t(), "pr", _c(state="closed"), dist=0.95, has_edge=True)
    assert item.category == HISTORICAL_REFERENCE
    assert "graph-edge" in item.signals


def test_confidence_boosted_by_signals():
    base = classify(_t(), "issue", _c(), dist=0.40, has_edge=False)
    boosted = classify(
        _t(labels=["x"], milestone="v1"),
        "issue",
        _c(labels=["x"], milestone="v1"),
        dist=0.40,
        has_edge=True,
    )
    assert boosted.confidence > base.confidence
