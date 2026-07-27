from scripts.check_tach_interfaces import Import, dead_patterns, module_paths, stray_catch_alls

CONSUMER = "enji_guard_cli.application"
TARGET = "enji_guard_cli.audit"


def _config(expose: list[str], visibility: list[str] | None = None) -> dict[str, object]:
    interface: dict[str, object] = {"from": [TARGET], "expose": expose}
    if visibility is not None:
        interface["visibility"] = visibility
    return {"interfaces": [interface]}


def test_a_pattern_matched_by_a_real_import_is_alive() -> None:
    config = _config(["ports\\..*"], [CONSUMER])
    imports = [Import(CONSUMER, TARGET, "ports.AuditGatewayPort")]

    assert dead_patterns(config, imports) == []


def test_a_pattern_nothing_imports_is_reported() -> None:
    config = _config(["ports\\..*", "freshness\\..*"], [CONSUMER])
    imports = [Import(CONSUMER, TARGET, "ports.AuditGatewayPort")]

    assert [pattern for _, _, pattern in dead_patterns(config, imports)] == ["freshness\\..*"]


def test_an_import_from_a_consumer_the_interface_does_not_govern_does_not_revive_a_pattern() -> None:
    # The interface only governs `application`.  An import made by the wiring
    # root travels under its own interface, so it must not be counted here --
    # otherwise a pattern stays alive for a consumer that never uses it.
    config = _config(["ledger\\..*"], [CONSUMER])
    imports = [Import("enji_guard_cli.composition", TARGET, "ledger.AuditLedger")]

    assert [pattern for _, _, pattern in dead_patterns(config, imports)] == ["ledger\\..*"]


def test_an_interface_without_visibility_governs_every_consumer() -> None:
    config = _config(["ledger\\..*"])
    imports = [Import("enji_guard_cli.composition", TARGET, "ledger.AuditLedger")]

    assert dead_patterns(config, imports) == []


def test_a_top_level_pattern_does_not_match_a_submodule_import() -> None:
    # `[^.]+` is the "seam only" pattern.  A deep import must not keep it alive,
    # or the distinction it encodes would be meaningless.
    config = _config(["[^.]+"], [CONSUMER])
    imports = [Import(CONSUMER, TARGET, "ports.AuditGatewayPort")]

    assert [pattern for _, _, pattern in dead_patterns(config, imports)] == ["[^.]+"]


def test_every_dead_pattern_is_reported_not_just_the_first() -> None:
    config = _config(["one\\..*", "two\\..*"], [CONSUMER])

    assert len(dead_patterns(config, [])) == 2


def test_wiring_may_expose_everything() -> None:
    config: dict[str, object] = {
        "interfaces": [
            {"from": [TARGET], "visibility": ["enji_guard_cli.composition"], "expose": [".*"]},
        ]
    }

    assert stray_catch_alls(config) == []


def test_a_catch_all_outside_wiring_is_rejected() -> None:
    # `.*` matches every import, so the dead-pattern rule can never flag it.
    # Widening an interface this way is the cheapest way to defeat the model.
    config: dict[str, object] = {"interfaces": [{"from": [TARGET], "visibility": [CONSUMER], "expose": [".*"]}]}

    assert stray_catch_alls(config) == [(TARGET, [CONSUMER])]


def test_a_catch_all_with_no_visibility_is_rejected() -> None:
    config: dict[str, object] = {"interfaces": [{"from": [TARGET], "expose": [".*"]}]}

    assert stray_catch_alls(config) == [(TARGET, ["*"])]


def test_ownership_comes_from_the_declared_graph_not_from_counting_dots() -> None:
    # `delivery.cli` and `delivery.mcp` are sibling modules under a package that
    # is not itself a module.  Taking the first two segments would attribute
    # both to a non-existent `delivery` module, and every interface declared on
    # either would be reported dead.
    config: dict[str, object] = {
        "modules": [
            {"path": "enji_guard_cli.delivery.cli"},
            {"path": "enji_guard_cli.delivery.mcp"},
            {"path": "enji_guard_cli.audit"},
        ]
    }

    assert module_paths(config)[0] == "enji_guard_cli.delivery.cli"
    assert "enji_guard_cli.delivery" not in module_paths(config)


def test_longest_declared_prefix_wins() -> None:
    config: dict[str, object] = {"modules": [{"path": "enji_guard_cli.delivery.mcp"}, {"path": "enji_guard_cli.audit"}]}
    ordered = module_paths(config)

    assert ordered.index("enji_guard_cli.delivery.mcp") < ordered.index("enji_guard_cli.audit")
