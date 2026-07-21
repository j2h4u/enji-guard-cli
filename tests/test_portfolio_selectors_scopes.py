# pyright: basic

import pytest

from enji_guard_cli.portfolio.errors import PortfolioNotFoundError
from enji_guard_cli.portfolio.models import (
    ProjectRef,
    RepositoryIdentity,
    RepositoryIdentitySource,
    RepositoryProvider,
    RepositoryRef,
)
from enji_guard_cli.portfolio.repositories import reconcile_repository, same_upstream_repository
from enji_guard_cli.portfolio.scopes import MutationScope
from enji_guard_cli.portfolio.selectors import parse_repository_selector, resolve_project, resolve_repository


def test_selectors_and_explicit_scope() -> None:
    projects = (ProjectRef("p1", "Pets"),)
    assert resolve_project(projects, "pets").project_id == "p1"
    assert MutationScope.from_args(all_repos=True, project="p1").kind == "all_repos"
    identity = parse_repository_selector("github@github.com:acme/cat")
    assert identity == RepositoryIdentity(RepositoryProvider.GITHUB, "acme/cat", "github.com")
    assert (
        resolve_repository(
            (
                RepositoryRef(
                    "r1",
                    "p1",
                    "Pets",
                    identity,
                    web_url="https://example.test/repository",
                    provider_repo_id="provider-test",
                ),
            ),
            "github@github.com:acme/cat",
        ).repo_id
        == "r1"
    )
    with pytest.raises(ValueError):
        MutationScope.from_args()
    with pytest.raises(ValueError):
        resolve_repository(
            (
                RepositoryRef(
                    "r1",
                    "p1",
                    "Pets",
                    identity,
                    web_url="https://example.test/repository",
                    provider_repo_id="provider-test",
                ),
            ),
            "nope",
        )


@pytest.mark.parametrize("selector", ["github@github.com:acme/cat", "GITHUB@GITHUB.COM:acme/cat"])
def test_repository_selector_is_case_insensitive(selector: str) -> None:
    identity = RepositoryIdentity(RepositoryProvider.GITHUB, "acme/cat", "github.com")
    assert (
        resolve_repository(
            (
                RepositoryRef(
                    "r1",
                    "p1",
                    "Pets",
                    identity,
                    web_url="https://example.test/repository",
                    provider_repo_id="provider-test",
                ),
            ),
            selector,
        ).repo_id
        == "r1"
    )


def test_repository_lookup_key_is_provider_aware() -> None:
    github = RepositoryRef(
        "r1",
        "p1",
        "Pets",
        RepositoryIdentity(RepositoryProvider.GITHUB, "Acme/Cat", "github.com"),
        web_url="https://example.test/repository",
        provider_repo_id="provider-test",
    )
    gitlab = RepositoryRef(
        "r2",
        "p1",
        "Pets",
        RepositoryIdentity(RepositoryProvider.GITLAB, "Acme/Cat", "gitlab.example"),
        web_url="https://example.test/repository",
        provider_repo_id="provider-test",
    )
    assert resolve_repository((github,), "github@github.com:acme/cat").repo_id == "r1"
    with pytest.raises(PortfolioNotFoundError):
        resolve_repository((github,), "github@github.com:other/cat")
    with pytest.raises(PortfolioNotFoundError):
        resolve_repository((github,), "gitlab@github.com:Acme/Cat")
    assert github.identity is not None
    assert gitlab.identity is not None
    assert github.identity.canonical_key != gitlab.identity.canonical_key


def test_stable_read_identity_is_distinct_from_operator_lookup() -> None:
    repository = RepositoryRef(
        "r1",
        "p1",
        "Pets",
        RepositoryIdentity(RepositoryProvider.GITHUB, "Acme/Cat", "github.com"),
        provider_repo_id="provider-123",
        web_url="https://example.test/repository",
    )
    assert repository.stable_identity_key == ("provider", "github", "github.com", "provider-123")


def test_stable_read_identity_survives_provider_rename() -> None:
    before = RepositoryRef(
        "r1",
        "p1",
        "Pets",
        RepositoryIdentity(RepositoryProvider.GITHUB, "acme/old-name", "github.com"),
        provider_repo_id="provider-123",
        web_url="https://example.test/repository",
    )
    after = RepositoryRef(
        "r1",
        "p1",
        "Pets",
        RepositoryIdentity(RepositoryProvider.GITHUB, "acme/new-name", "github.com"),
        provider_repo_id="provider-123",
        web_url="https://example.test/repository",
    )
    assert same_upstream_repository(before, after)
    assert reconcile_repository(before, after) is after


def test_enji_identity_survives_native_id_transition_and_rename() -> None:
    before = RepositoryRef(
        "r1",
        "p1",
        "Pets",
        RepositoryIdentity(RepositoryProvider.GITHUB, "acme/old-name", "github.com"),
        provider_repo_id="r1",
        identity_source=RepositoryIdentitySource.ENJI,
        web_url="https://example.test/repository",
    )
    after = RepositoryRef(
        "r1",
        "p1",
        "Pets",
        RepositoryIdentity(RepositoryProvider.GITHUB, "acme/new-name", "github.com"),
        provider_repo_id="native-1",
        web_url="https://example.test/repository",
    )
    assert same_upstream_repository(before, after)


def test_different_enji_records_are_not_same_without_native_ids() -> None:
    left = RepositoryRef(
        "r1",
        "p1",
        "Pets",
        RepositoryIdentity(RepositoryProvider.GITHUB, "acme/cat", "github.com"),
        provider_repo_id="r1",
        identity_source=RepositoryIdentitySource.ENJI,
        web_url="https://example.test/repository",
    )
    right = RepositoryRef(
        "r2",
        "p1",
        "Pets",
        RepositoryIdentity(RepositoryProvider.GITHUB, "acme/cat", "github.com"),
        provider_repo_id="r2",
        identity_source=RepositoryIdentitySource.ENJI,
        web_url="https://example.test/repository",
    )
    assert not same_upstream_repository(left, right)


@pytest.mark.parametrize(
    ("provider", "host"),
    [(RepositoryProvider.GITLAB, "github.com"), (RepositoryProvider.GITHUB, "git.example")],
)
def test_enji_identity_does_not_cross_provider_namespace(provider: RepositoryProvider, host: str) -> None:
    left = RepositoryRef(
        "r1",
        "p1",
        "Pets",
        RepositoryIdentity(RepositoryProvider.GITHUB, "acme/cat", "github.com"),
        provider_repo_id="r1",
        identity_source=RepositoryIdentitySource.ENJI,
        web_url="https://example.test/repository",
    )
    right = RepositoryRef(
        "r1",
        "p1",
        "Pets",
        RepositoryIdentity(provider, "acme/cat", host),
        provider_repo_id="r1",
        identity_source=RepositoryIdentitySource.ENJI,
        web_url="https://example.test/repository",
    )
    assert not same_upstream_repository(left, right)


def test_stable_identity_key_includes_namespace() -> None:
    identity = RepositoryIdentity(RepositoryProvider.GITHUB, "acme/cat", "github.com")
    native = RepositoryRef(
        "r1", "p1", "Pets", identity, provider_repo_id="same", web_url="https://example.test/repository"
    )
    enji = RepositoryRef(
        "r1",
        "p1",
        "Pets",
        identity,
        provider_repo_id="same",
        identity_source=RepositoryIdentitySource.ENJI,
        web_url="https://example.test/repository",
    )
    assert native.stable_identity_key != enji.stable_identity_key


def test_native_ids_match_across_enji_records_but_namespaces_do_not() -> None:
    native_left = RepositoryRef(
        "r1",
        "p1",
        "Pets",
        RepositoryIdentity(RepositoryProvider.GITHUB, "acme/cat", "github.com"),
        provider_repo_id="native-1",
        web_url="https://example.test/repository",
    )
    native_right = RepositoryRef(
        "r2",
        "p1",
        "Pets",
        RepositoryIdentity(RepositoryProvider.GITHUB, "acme/cat-renamed", "github.com"),
        provider_repo_id="native-1",
        web_url="https://example.test/repository",
    )
    assert same_upstream_repository(native_left, native_right)

    enji_same_text = RepositoryRef(
        "r3",
        "p1",
        "Pets",
        RepositoryIdentity(RepositoryProvider.GITHUB, "acme/cat", "github.com"),
        provider_repo_id="native-1",
        identity_source=RepositoryIdentitySource.ENJI,
        web_url="https://example.test/repository",
    )
    assert not same_upstream_repository(enji_same_text, native_right)


@pytest.mark.parametrize("selector", ["acme/cat", "github@github.com:acme"])
def test_repository_selector_rejects_legacy_or_malformed(selector: str) -> None:
    with pytest.raises(ValueError):
        parse_repository_selector(selector)
