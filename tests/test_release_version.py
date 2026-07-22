import pytest
from scripts.release_version import VersionResolutionError, resolve_package_version

SHA = "03857d9920bef008b8225b9152e01c066f9eff1a"


def test_tagged_release_uses_release_tag() -> None:
    assert resolve_package_version("v2.2.4", SHA, ["v2.2.3"]) == "2.2.4"


def test_untagged_build_uses_latest_reachable_semver_with_sha_metadata() -> None:
    assert resolve_package_version("", SHA, ["v1.9.0", "v2.2.3", "v2.1.9", "not-a-version"]) == "2.2.3+sha.03857d9920be"


@pytest.mark.parametrize(
    ("release_tag", "base_tags"),
    [("", []), ("v2.2", ["v2.2.3"]), ("release-2.2.3", ["v2.2.3"]), ("v0.0.0", ["v2.2.3"])],
)
def test_invalid_or_missing_base_version_fails(release_tag: str, base_tags: list[str]) -> None:
    with pytest.raises(VersionResolutionError):
        resolve_package_version(release_tag, SHA, base_tags)


def test_invalid_source_sha_fails() -> None:
    with pytest.raises(VersionResolutionError):
        resolve_package_version("", "not-a-sha", ["v2.2.3"])
