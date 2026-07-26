## Summary

- 

## Acceptance

- [ ] User workflow or operator behavior is clear.
- [ ] Tests, docs, OpenAPI, and the tach module graph are updated when affected.
- [ ] `just verify` passes.

## Release notes

A squash merge keeps this PR's title and discards every other commit subject,
so a multi-commit PR must say here what shipped. Delete this section if the PR
is a single commit; otherwise fill in the block, which release-please reads
verbatim to build the changelog.

Rules the CI check enforces, because release-please fails silently on both:
the type must start at column 0 (no `*` or `-` bullets, no indentation), entries
are separated by a blank line, and a `BREAKING CHANGE:` note must have its
bullets on the very next line — a blank line there ends the note and drops them.

```
BEGIN_COMMIT_OVERRIDE
fix(audit): serialize ledger read-modify-write cycles

feat(cli): confirm irreversible project delete and repo remove
BREAKING CHANGE: `project delete` requires `--yes` from any non-TTY caller.
- `repo remove` and `--all-projects` writes are covered by the same rule.
END_COMMIT_OVERRIDE
```

## Runtime

- [ ] Docker impact checked, or not applicable.
