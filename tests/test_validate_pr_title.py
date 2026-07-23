from scripts.validate_pr_title import validate_pr_title


def test_accepts_releasable_conventional_titles() -> None:
    assert validate_pr_title("fix: repair release automation")[0]
    assert validate_pr_title("feat(cli): add repository discovery")[0]
    assert validate_pr_title("feat(cli)!: remove legacy selector syntax")[0]
    assert validate_pr_title("refactor(audit/runbook): remove stale helpers")[0]


def test_rejects_nonconventional_titles() -> None:
    ok, message = validate_pr_title("Update stuff")

    assert not ok
    assert "must look like" in message


def test_rejects_types_release_please_will_not_bump() -> None:
    ok, message = validate_pr_title("wip: try something")

    assert not ok
    assert "Unsupported Conventional Commit type 'wip'" in message


def test_rejects_empty_description() -> None:
    ok, message = validate_pr_title("fix: ")

    assert not ok
    assert "must look like" in message
