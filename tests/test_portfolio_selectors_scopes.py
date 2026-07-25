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


def _repo(
    repo_id: str,
    locator: str,
    *,
    project: tuple[str, str] = ("p1", "Pets"),
    provider: RepositoryProvider = RepositoryProvider.GITHUB,
    host: str = "github.com",
) -> RepositoryRef:
    project_id, project_name = project
    return RepositoryRef(
        repo_id,
        project_id,
        project_name,
        RepositoryIdentity(provider, locator, host),
        web_url="https://example.test/repository",
        provider_repo_id=f"provider-{repo_id}",
    )


def test_bare_locator_resolves_when_unambiguous_within_project_scope() -> None:
    targets = (
        _repo("repo_dd992a5c", "j2h4u/mcp-telegram", project=("p9", "MCP Integrations")),
        _repo("repo_other", "j2h4u/mcp-strava", project=("p9", "MCP Integrations")),
        _repo("repo_clash", "j2h4u/mcp-telegram"),
    )
    assert resolve_repository(targets, "j2h4u/mcp-telegram", project="MCP Integrations").repo_id == "repo_dd992a5c"


def test_bare_locator_is_case_insensitive_for_github() -> None:
    targets = (_repo("r1", "Acme/Cat"),)
    assert resolve_repository(targets, "acme/CAT").repo_id == "r1"


def test_ambiguous_bare_locator_names_candidates_and_refuses_to_guess() -> None:
    targets = (
        _repo("r1", "acme/cat"),
        _repo("r2", "acme/cat", project=("p2", "Dogs")),
    )
    with pytest.raises(ValueError, match="repo selector is ambiguous") as excinfo:
        resolve_repository(targets, "acme/cat")
    message = str(excinfo.value)
    assert "github@github.com:acme/cat in Pets (r1)" in message
    assert "github@github.com:acme/cat in Dogs (r2)" in message


def test_exact_repo_id_wins_over_a_loose_locator_match() -> None:
    targets = (
        _repo("acme/cat", "other/name"),
        _repo("r2", "acme/cat"),
    )
    assert resolve_repository(targets, "acme/cat").repo_id == "acme/cat"


def test_full_canonical_selector_still_wins_over_a_loose_locator_match() -> None:
    targets = (
        _repo("r1", "acme/cat", provider=RepositoryProvider.GITLAB, host="gitlab.example"),
        _repo("r2", "acme/cat"),
    )
    assert resolve_repository(targets, "gitlab@gitlab.example:acme/cat").repo_id == "r1"


def test_failed_repository_lookup_names_candidates() -> None:
    targets = (_repo("repo_dd992a5c", "j2h4u/mcp-telegram"),)
    with pytest.raises(PortfolioNotFoundError) as excinfo:
        resolve_repository(targets, "j2h4u/mcp-telegramm")
    message = str(excinfo.value)
    assert "repo selector matched no repos: j2h4u/mcp-telegramm" in message
    assert "github@github.com:j2h4u/mcp-telegram in Pets (repo_dd992a5c)" in message


def test_failed_repository_lookup_suggests_nothing_without_a_close_relative() -> None:
    targets = tuple(_repo(f"r{index}", f"acme/name-{index}") for index in range(25))
    with pytest.raises(PortfolioNotFoundError) as excinfo:
        resolve_repository(targets, "totally/unrelated-thing")
    message = str(excinfo.value)
    assert "no close match; run `status` to list them" in message
    assert "did you mean" not in message
    assert "github@github.com:" not in message


def test_failed_repository_lookup_points_at_the_scoped_listing_command() -> None:
    targets = (_repo("r1", "acme/cat", project=("p9", "MCP Integrations")),)
    with pytest.raises(PortfolioNotFoundError) as excinfo:
        resolve_repository(targets, "totally/unrelated-thing", project="MCP Integrations")
    assert "run `status --project MCP Integrations` to list them" in str(excinfo.value)


def test_failed_repository_lookup_caps_the_suggestion_list() -> None:
    targets = tuple(_repo(f"r{index}", f"acme/mcp-telegram-{index}") for index in range(10))
    with pytest.raises(PortfolioNotFoundError) as excinfo:
        resolve_repository(targets, "acme/mcp-telegram")
    message = str(excinfo.value)
    assert message.count("github@github.com:") == 3


def test_repository_suggestions_do_not_fuzzy_match_opaque_repo_ids() -> None:
    targets = (_repo("repo_dd992a5c-6a9a-4449-a3a2-eb4d82e578b1", "acme/cat"),)
    with pytest.raises(PortfolioNotFoundError) as excinfo:
        resolve_repository(targets, "repo_dd992a5c-6a9a-4449-a3a2-eb4d82e578b2")
    assert "no close match" in str(excinfo.value)


def test_failed_repository_lookup_reports_an_empty_scope() -> None:
    with pytest.raises(PortfolioNotFoundError, match="no repositories in scope; run `status` to list them"):
        resolve_repository((), "acme/cat")
