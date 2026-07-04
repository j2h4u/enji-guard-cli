# Changelog

## [0.3.6](https://github.com/j2h4u/enji-guard-cli/compare/v0.3.5...v0.3.6) (2026-07-04)


### Fixes

* activate existing repos before recon ([#77](https://github.com/j2h4u/enji-guard-cli/issues/77)) ([fe12c73](https://github.com/j2h4u/enji-guard-cli/commit/fe12c738f0cf7565227cd47b829f54531e522868))

## [0.3.5](https://github.com/j2h4u/enji-guard-cli/compare/v0.3.4...v0.3.5) (2026-07-04)


### Fixes

* clarify repo add and remove surface ([#75](https://github.com/j2h4u/enji-guard-cli/issues/75)) ([545645c](https://github.com/j2h4u/enji-guard-cli/commit/545645ca9015ba05c7ed74529814773675256366))

## [0.3.4](https://github.com/j2h4u/enji-guard-cli/compare/v0.3.3...v0.3.4) (2026-07-04)


### Fixes

* label supervisor telemetry provenance ([#73](https://github.com/j2h4u/enji-guard-cli/issues/73)) ([7136771](https://github.com/j2h4u/enji-guard-cli/commit/7136771c600ef7356b6e10b4ce3e895f32c91dc1))

## [0.3.3](https://github.com/j2h4u/enji-guard-cli/compare/v0.3.2...v0.3.3) (2026-07-04)


### Fixes

* add telemetry provenance ([#71](https://github.com/j2h4u/enji-guard-cli/issues/71)) ([e206dbc](https://github.com/j2h4u/enji-guard-cli/commit/e206dbc5b9d6e5bdf68fb1a79f7bf4f177ffeda1))

## [0.3.2](https://github.com/j2h4u/enji-guard-cli/compare/v0.3.1...v0.3.2) (2026-07-04)


### Fixes

* block non-empty project deletion ([#69](https://github.com/j2h4u/enji-guard-cli/issues/69)) ([42ea78e](https://github.com/j2h4u/enji-guard-cli/commit/42ea78e5edb5bd91f7b403abae07b500dcc2c467))

## [0.3.1](https://github.com/j2h4u/enji-guard-cli/compare/v0.3.0...v0.3.1) (2026-07-04)


### Fixes

* prevent duplicate repo connects ([#67](https://github.com/j2h4u/enji-guard-cli/issues/67)) ([4d3b2d5](https://github.com/j2h4u/enji-guard-cli/commit/4d3b2d507da7c27b9f828a1ebd02ccebccc30e98))

## [0.3.0](https://github.com/j2h4u/enji-guard-cli/compare/v0.2.0...v0.3.0) (2026-07-04)


### Features

* add audit preflight and CLI telemetry ([#61](https://github.com/j2h4u/enji-guard-cli/issues/61)) ([3b8ad5a](https://github.com/j2h4u/enji-guard-cli/commit/3b8ad5a8fda70fcbb44b1ddd121eca25bda99441))
* add telemetry sink foundation ([#63](https://github.com/j2h4u/enji-guard-cli/issues/63)) ([d4c5f60](https://github.com/j2h4u/enji-guard-cli/commit/d4c5f60fd75938adda3da87aff66fe14f3771e06))
* share agent journey telemetry ([#62](https://github.com/j2h4u/enji-guard-cli/issues/62)) ([1c516c2](https://github.com/j2h4u/enji-guard-cli/commit/1c516c22f5519e8c4b66297732b385981e2594c6))
* split report and audit task status ([#64](https://github.com/j2h4u/enji-guard-cli/issues/64)) ([d2231ac](https://github.com/j2h4u/enji-guard-cli/commit/d2231acdf2d51e679699b191f5c484adce70e9bd))


### Fixes

* fail closed before backend readiness succeeds ([#57](https://github.com/j2h4u/enji-guard-cli/issues/57)) ([c05551c](https://github.com/j2h4u/enji-guard-cli/commit/c05551c0e6e3e4787f6a459ffadde41aeba294d0))
* harden refresh cookie rotation ([#55](https://github.com/j2h4u/enji-guard-cli/issues/55)) ([b42d47a](https://github.com/j2h4u/enji-guard-cli/commit/b42d47aca8d35208aa44f619812103d08d7eaa51))
* sanitize report markdown terminal output ([#59](https://github.com/j2h4u/enji-guard-cli/issues/59)) ([84ba55e](https://github.com/j2h4u/enji-guard-cli/commit/84ba55e81f22d279324fdc0af2fcfce1ca860b42))

## [0.2.0](https://github.com/j2h4u/enji-guard-cli/compare/v0.1.0...v0.2.0) (2026-07-03)


### Features

* add backend readiness health ([bf82b2e](https://github.com/j2h4u/enji-guard-cli/commit/bf82b2ecc4caf129625eb6b49041776956732305))


### Fixes

* keep background task failures contained ([ebe2fcc](https://github.com/j2h4u/enji-guard-cli/commit/ebe2fcc3d264c32679a7b0485a31ba193146d418))

## [0.1.0] - 2026-07-03

### Features

- Add a Docker-first Enji Guard service with CLI, MCP runtime, persistent cookie
  refresh, and telemetry.
- Add repository, project, report, schedule, email, and wait workflows for
  agent-operated Enji Guard usage.
- Add reconstructed Enji OpenAPI contract coverage and strict local/CI quality
  gates.

### Fixes

- Handle unavailable report snapshots in batch reads without aborting readable
  reports.
- Keep the Docker image default loopback-safe while compose deployments publish
  MCP only on host loopback.

[0.1.0]: https://github.com/j2h4u/enji-guard-cli/releases/tag/v0.1.0
