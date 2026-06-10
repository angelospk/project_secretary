import pytest
from pydantic import ValidationError

from secretary.config import Settings


def test_repo_owner_and_name():
    s = Settings(github_repo="acme/widgets")
    assert s.repo_owner == "acme"
    assert s.repo_name == "widgets"


def test_repo_list_multi_normalizes_and_dedupes():
    s = Settings(github_repos="Acme/Widgets, acme/widgets, Other/Repo")
    assert s.repo_list == ["acme/widgets", "other/repo"]


@pytest.mark.parametrize("bad", ["noslash", "owner/", "/name", "a/b/c"])
def test_invalid_repo_rejected(bad):
    with pytest.raises(ValidationError):
        Settings(github_repo=bad)


def test_empty_config_raises_on_repo_list():
    # Empty is allowed as "unset", but resolving the repo list must fail loudly.
    s = Settings(github_repo="", github_repos="")
    with pytest.raises(ValueError):
        _ = s.repo_list


def test_related_repo_pairs_parse():
    s = Settings(related_repo_pairs="acme/api+acme/worker, x/y+z/w")
    assert frozenset({"acme/api", "acme/worker"}) in s.related_repo_pair_set
    assert frozenset({"x/y", "z/w"}) in s.related_repo_pair_set
