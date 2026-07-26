from scripts.validate_pr_commits import validate_commit_messages
from scripts.validate_release_notes import _split_messages, validate_release_notes

OVERRIDE = """
BEGIN_COMMIT_OVERRIDE
fix(audit): serialize ledger read-modify-write cycles

feat(cli): confirm irreversible project delete
END_COMMIT_OVERRIDE
"""


def test_single_commit_pr_owes_no_override() -> None:
    ok, _ = validate_release_notes("Just a description.", commit_count=1, require_above=1)

    assert ok


def test_multi_commit_pr_without_an_override_is_rejected() -> None:
    ok, messages = validate_release_notes("Just a description.", commit_count=29, require_above=1)

    assert not ok
    assert any("squashes 29 commits" in message for message in messages)


def test_override_block_splits_into_one_entry_per_message() -> None:
    ok, messages = validate_release_notes(OVERRIDE, commit_count=29, require_above=1)

    assert ok
    assert "2 changelog entr" in messages[0]


def test_github_default_squash_body_shape_is_rejected() -> None:
    # This is the shape GitHub writes for `COMMIT_MESSAGES`, and the exact
    # reason v3.0.0 rendered one line from 98 commits: bulleted subjects with
    # no blank lines between them.  release-please's splitter never fires, so
    # the whole thing collapses into a single entry.
    body = """
BEGIN_COMMIT_OVERRIDE
* fix(audit): serialize ledger read-modify-write cycles
* feat(cli): confirm irreversible project delete
END_COMMIT_OVERRIDE
"""

    assert len(_split_messages(body.split("BEGIN_COMMIT_OVERRIDE")[1].split("END_COMMIT_OVERRIDE")[0])) == 1

    ok, messages = validate_release_notes(body, commit_count=2, require_above=1)

    assert not ok
    assert any("not a Conventional Commit subject" in message for message in messages)


def test_blank_line_after_breaking_change_is_rejected() -> None:
    # The v3.0.0 bug: release-please ends the note at the blank line, so all
    # eight breaking bullets were silently dropped.
    body = """
BEGIN_COMMIT_OVERRIDE
refactor(cli)!: drop duplicate snapshot commands

BREAKING CHANGE: the operator surface changed.

- `portfolio status` is gone; use `status`.
END_COMMIT_OVERRIDE
"""

    ok, messages = validate_release_notes(body, commit_count=2, require_above=1)

    assert not ok
    assert any("blank line directly after" in message for message in messages)


def test_breaking_change_bullets_on_the_next_line_are_accepted() -> None:
    body = """
BEGIN_COMMIT_OVERRIDE
refactor(cli)!: drop duplicate snapshot commands
BREAKING CHANGE: the operator surface changed.
- `portfolio status` is gone; use `status`.
END_COMMIT_OVERRIDE
"""

    ok, _ = validate_release_notes(body, commit_count=2, require_above=1)

    assert ok


def test_unsupported_type_in_an_override_entry_is_rejected() -> None:
    body = "BEGIN_COMMIT_OVERRIDE\nwip(cli): halfway there\nEND_COMMIT_OVERRIDE"

    ok, messages = validate_release_notes(body, commit_count=2, require_above=1)

    assert not ok
    assert any("unsupported type 'wip'" in message for message in messages)


def test_commit_subjects_must_be_conventional() -> None:
    ok, messages = validate_commit_messages(["fix(ci): pin the digest", "quick wip fix"])

    assert not ok
    assert any("'quick wip fix' is not a Conventional Commit subject." in message for message in messages)


def test_conventional_commit_subjects_pass() -> None:
    ok, _ = validate_commit_messages(["fix(ci): pin the digest", "docs: explain the gate"])

    assert ok


def test_column_zero_bullet_in_a_commit_body_is_rejected() -> None:
    # c2249b6 shipped exactly this and release-please dropped the whole commit
    # from v3.0.2 without reporting anything.  The parser reads the '-' as the
    # commit type and fails on the space that follows it.
    ok, messages = validate_commit_messages(["ci: add validators\n\n- one thing\n- another thing"])

    assert not ok
    assert any("Markdown bullet at column 0" in message for message in messages)


def test_indented_bullets_in_a_commit_body_are_accepted() -> None:
    ok, _ = validate_commit_messages(["ci: add validators\n\n  - one thing\n  - another thing"])

    assert ok
