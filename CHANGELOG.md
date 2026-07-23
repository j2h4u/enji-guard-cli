# Changelog

## [2.2.13](https://github.com/j2h4u/enji-guard-cli/compare/v2.2.12...v2.2.13) (2026-07-23)


### Fixes

* **audit:** start current-head runs ([#180](https://github.com/j2h4u/enji-guard-cli/issues/180)) ([ac7e408](https://github.com/j2h4u/enji-guard-cli/commit/ac7e4082ba2e4b1eb3e5901035922d38694a7ff7))

## [2.2.12](https://github.com/j2h4u/enji-guard-cli/compare/v2.2.11...v2.2.12) (2026-07-23)


### Fixes

* **audit:** expose only active runs ([#178](https://github.com/j2h4u/enji-guard-cli/issues/178)) ([31ebc50](https://github.com/j2h4u/enji-guard-cli/commit/31ebc50dd555697779c3c0e2012863c7bb49f43c))

## [2.2.11](https://github.com/j2h4u/enji-guard-cli/compare/v2.2.10...v2.2.11) (2026-07-23)


### Fixes

* **audit:** expose current-head readiness ([#176](https://github.com/j2h4u/enji-guard-cli/issues/176)) ([ecda863](https://github.com/j2h4u/enji-guard-cli/commit/ecda8630dbeac0f7256367b686108a4cd5d39ed1))

## [2.2.10](https://github.com/j2h4u/enji-guard-cli/compare/v2.2.9...v2.2.10) (2026-07-23)


### Fixes

* **auth:** persist only auth cookies ([34144e1](https://github.com/j2h4u/enji-guard-cli/commit/34144e167f937d1b3c091f68a3a502ce9710a011))

## [2.2.9](https://github.com/j2h4u/enji-guard-cli/compare/v2.2.8...v2.2.9) (2026-07-23)


### Fixes

* **cli:** show audit mental model without args ([8767c2c](https://github.com/j2h4u/enji-guard-cli/commit/8767c2cc08894716509b267ea7231fecd5d4fd3d))


### Documentation

* **agents:** clarify Enji audit status checks ([827b08c](https://github.com/j2h4u/enji-guard-cli/commit/827b08ca268386b0ffdb83aaec9716ca7ce9b9a3))

## [2.2.8](https://github.com/j2h4u/enji-guard-cli/compare/v2.2.7...v2.2.8) (2026-07-23)


### Fixes

* **docker:** use available MCP host port ([26746e2](https://github.com/j2h4u/enji-guard-cli/commit/26746e2b71097a7a7cd0bcd31ce6b66b770e46e4))

## [2.2.7](https://github.com/j2h4u/enji-guard-cli/compare/v2.2.6...v2.2.7) (2026-07-23)


### Fixes

* **docker:** avoid MCP host port collision ([0a0f6a9](https://github.com/j2h4u/enji-guard-cli/commit/0a0f6a92711b262345626f0d3f4a4aa28bf7738a))

## [2.2.6](https://github.com/j2h4u/enji-guard-cli/compare/v2.2.5...v2.2.6) (2026-07-23)


### Fixes

* **ci:** require releasable PR titles ([#165](https://github.com/j2h4u/enji-guard-cli/issues/165)) ([60afcef](https://github.com/j2h4u/enji-guard-cli/commit/60afcef312a5580ac5d5951b08a3fd8874e766d4))

## [2.2.5](https://github.com/j2h4u/enji-guard-cli/compare/v2.2.4...v2.2.5) (2026-07-23)


### Fixes

* **release:** treat maintenance commits as releasable ([#163](https://github.com/j2h4u/enji-guard-cli/issues/163)) ([08a04c7](https://github.com/j2h4u/enji-guard-cli/commit/08a04c7d8bb256c7e68597ef6d011818a45a2649))


### Refactoring

* remove verified dead code ([#162](https://github.com/j2h4u/enji-guard-cli/issues/162)) ([c4ae8dd](https://github.com/j2h4u/enji-guard-cli/commit/c4ae8ddf39910e1d1b31e309e5dbcb616fd02742))

## [2.2.4](https://github.com/j2h4u/enji-guard-cli/compare/v2.2.3...v2.2.4) (2026-07-23)


### Fixes

* **auth:** make cookie rotation crash-consistent ([#159](https://github.com/j2h4u/enji-guard-cli/issues/159)) ([72bcdb3](https://github.com/j2h4u/enji-guard-cli/commit/72bcdb332fd3fb2ef6fb65a0d137c9296292b71a))
* **compose:** require build provenance ([#161](https://github.com/j2h4u/enji-guard-cli/issues/161)) ([a4a2d37](https://github.com/j2h4u/enji-guard-cli/commit/a4a2d3788e70cefcb38dcfc8e01f615a413dba83))

## [2.2.3](https://github.com/j2h4u/enji-guard-cli/compare/v2.2.2...v2.2.3) (2026-07-22)


### Fixes

* pin GHCR compose project name ([#154](https://github.com/j2h4u/enji-guard-cli/issues/154)) ([933bf75](https://github.com/j2h4u/enji-guard-cli/commit/933bf755c95ef0fcba3ce979d22b15ac19821996))

## [2.2.2](https://github.com/j2h4u/enji-guard-cli/compare/v2.2.1...v2.2.2) (2026-07-22)


### Fixes

* **container:** refresh trixie base for liblzma security update ([#151](https://github.com/j2h4u/enji-guard-cli/issues/151)) ([880350d](https://github.com/j2h4u/enji-guard-cli/commit/880350d3dc7729ef21024dc09d6f9203ed4329a0))

## [2.2.1](https://github.com/j2h4u/enji-guard-cli/compare/v2.2.0...v2.2.1) (2026-07-22)


### Fixes

* **audit:** parse authoritative task detail contract ([#148](https://github.com/j2h4u/enji-guard-cli/issues/148)) ([7665c35](https://github.com/j2h4u/enji-guard-cli/commit/7665c35d24db9a9f84f18578987e69d47fd2e494))
* honor explicit health listener options, scan candidate images before publishing, remove dead legacy surfaces with RUF100 enforcement, and pass typed audit updates through the application facade ([#150](https://github.com/j2h4u/enji-guard-cli/issues/150)) ([6a539a7](https://github.com/j2h4u/enji-guard-cli/commit/6a539a7f1af3c616e01288864de1b7bf8629f61f))

## [2.2.0](https://github.com/j2h4u/enji-guard-cli/compare/v2.1.0...v2.2.0) (2026-07-22)


### Features

* read previous audit reports during active runs ([#146](https://github.com/j2h4u/enji-guard-cli/issues/146)) ([1d9b70c](https://github.com/j2h4u/enji-guard-cli/commit/1d9b70cb80d525526f22414d8d3711781fd44709))


### Fixes

* **audit:** derive readability from audited results ([#144](https://github.com/j2h4u/enji-guard-cli/issues/144)) ([ffe5375](https://github.com/j2h4u/enji-guard-cli/commit/ffe53756b572b6a71793e84bc179763807bfbd3a))
* warn when stale report has active replacement ([#147](https://github.com/j2h4u/enji-guard-cli/issues/147)) ([cfc5acb](https://github.com/j2h4u/enji-guard-cli/commit/cfc5acbe1bded20f57b3643f3913ea82a385e2c2))

## [2.1.0](https://github.com/j2h4u/enji-guard-cli/compare/v2.0.0...v2.1.0) (2026-07-21)


### Features

* add GitLab repository discovery ([#142](https://github.com/j2h4u/enji-guard-cli/issues/142)) ([d10dc33](https://github.com/j2h4u/enji-guard-cli/commit/d10dc334f071d377c3d9788bfe0fe1871ad10586))

## [2.0.0](https://github.com/j2h4u/enji-guard-cli/compare/v1.1.0...v2.0.0) (2026-07-21)


### ⚠ BREAKING CHANGES

* repository selectors now require the provider-neutral form provider@host:path; legacy host:path selectors are no longer accepted.

### Features

* adopt provider-neutral repository identity ([#140](https://github.com/j2h4u/enji-guard-cli/issues/140)) ([72f88cf](https://github.com/j2h4u/enji-guard-cli/commit/72f88cfd400a7f9dcf26f3969a29f33903b37774))


### Fixes

* reconcile audit runs and pooled shutdown ([#138](https://github.com/j2h4u/enji-guard-cli/issues/138)) ([b5fa419](https://github.com/j2h4u/enji-guard-cli/commit/b5fa41999bb371fee77317f00e6807a86d7dd0b0))

## [1.1.0](https://github.com/j2h4u/enji-guard-cli/compare/v1.0.0...v1.1.0) (2026-07-21)


### Features

* expose CLI build provenance ([#136](https://github.com/j2h4u/enji-guard-cli/issues/136)) ([a4a0b0e](https://github.com/j2h4u/enji-guard-cli/commit/a4a0b0ef107e27fb5924e6943f91a376d9bfdb84))

## [1.0.0](https://github.com/j2h4u/enji-guard-cli/compare/v0.6.2...v1.0.0) (2026-07-21)


### ⚠ BREAKING CHANGES

* **cli:** The public CLI and JSON contract now follow the audit-first model. Human and agent workflows should use the current README scenarios; this release intentionally does not preserve the legacy report-shaped surface.

### Features

* add audit catalog and session resilience ([50c8e6e](https://github.com/j2h4u/enji-guard-cli/commit/50c8e6e0e4abd30b3c681d6d4446f9e06375e663))
* **auth:** make credential recovery automatic and immediate ([e4de712](https://github.com/j2h4u/enji-guard-cli/commit/e4de7127f6ffe2f743475d7141f448254baa17c3))
* **cli:** adopt an audit-first operator model ([e4de712](https://github.com/j2h4u/enji-guard-cli/commit/e4de7127f6ffe2f743475d7141f448254baa17c3))


### Fixes

* **runtime:** harden readiness and release smoke verification ([e4de712](https://github.com/j2h4u/enji-guard-cli/commit/e4de7127f6ffe2f743475d7141f448254baa17c3))


### Performance

* **cli:** pool transport and bound portfolio fanout ([e4de712](https://github.com/j2h4u/enji-guard-cli/commit/e4de7127f6ffe2f743475d7141f448254baa17c3))

## [0.6.2](https://github.com/j2h4u/enji-guard-cli/compare/v0.6.1...v0.6.2) (2026-07-12)


### Fixes

* harden QA configuration drift checks ([#127](https://github.com/j2h4u/enji-guard-cli/issues/127)) ([22d4cf1](https://github.com/j2h4u/enji-guard-cli/commit/22d4cf1558327147649770cc80be7fdb1511796f))

## [0.6.1](https://github.com/j2h4u/enji-guard-cli/compare/v0.6.0...v0.6.1) (2026-07-12)


### Fixes

* simplify report language output ([#125](https://github.com/j2h4u/enji-guard-cli/issues/125)) ([ae7afe4](https://github.com/j2h4u/enji-guard-cli/commit/ae7afe47a333ca00ff2b45bc75bbbcf878ce77a1))

## [0.6.0](https://github.com/j2h4u/enji-guard-cli/compare/v0.5.0...v0.6.0) (2026-07-12)


### Features

* manage report language ([#123](https://github.com/j2h4u/enji-guard-cli/issues/123)) ([bf37e14](https://github.com/j2h4u/enji-guard-cli/commit/bf37e14d9f0693521df9e4dc8ff67bd292563de2))

## [0.5.0](https://github.com/j2h4u/enji-guard-cli/compare/v0.4.1...v0.5.0) (2026-07-12)


### Features

* add autofix management ([#121](https://github.com/j2h4u/enji-guard-cli/issues/121)) ([b8f9a47](https://github.com/j2h4u/enji-guard-cli/commit/b8f9a472a86300ae7d9824a0c1d5ffac667aecd4))

## [0.4.1](https://github.com/j2h4u/enji-guard-cli/compare/v0.4.0...v0.4.1) (2026-07-11)


### Fixes

* retain rotated auth across storage failures ([#118](https://github.com/j2h4u/enji-guard-cli/issues/118)) ([5d21457](https://github.com/j2h4u/enji-guard-cli/commit/5d2145765d9f387fb018894ea3eda3c27b3d9cd4))

## [0.4.0](https://github.com/j2h4u/enji-guard-cli/compare/v0.3.12...v0.4.0) (2026-07-11)


### Features

* discover audits from live Enji catalog ([#115](https://github.com/j2h4u/enji-guard-cli/issues/115)) ([94f6a09](https://github.com/j2h4u/enji-guard-cli/commit/94f6a093b2150ecbfd42a15c1d9a5b2ffa1b691f))
* migrate audit schedules to auto-runs ([#117](https://github.com/j2h4u/enji-guard-cli/issues/117)) ([b696033](https://github.com/j2h4u/enji-guard-cli/commit/b6960339ae57c561771424eeaa20f17fbccb1295))

## [0.3.12](https://github.com/j2h4u/enji-guard-cli/compare/v0.3.11...v0.3.12) (2026-07-06)


### Fixes

* address fresh audit cleanup findings ([06fd2a0](https://github.com/j2h4u/enji-guard-cli/commit/06fd2a0c0adf9c09f8a19d483c4073227ee8528f))
* address fresh Enji audit followups ([463ef1a](https://github.com/j2h4u/enji-guard-cli/commit/463ef1a41cda9a5b15c43c9feb378818ba001949))
* disable source builds for runtime deps ([9831ae6](https://github.com/j2h4u/enji-guard-cli/commit/9831ae684e1dca15f63670c8817f898a0caf8e86))
* gate container publish to trusted workflow runs ([#108](https://github.com/j2h4u/enji-guard-cli/issues/108)) ([e6f8df1](https://github.com/j2h4u/enji-guard-cli/commit/e6f8df1938f96cf5d9f34ea6cb48ed105c47c198))
* remove confirmed dead code ([3b300b1](https://github.com/j2h4u/enji-guard-cli/commit/3b300b16bccf50cdbfd192d25375b2b688bd459e))
* remove inline lint suppressions ([#107](https://github.com/j2h4u/enji-guard-cli/issues/107)) ([7e10fa7](https://github.com/j2h4u/enji-guard-cli/commit/7e10fa71894159095aa2636ea6719a0c5f84b0cc))

## [0.3.11](https://github.com/j2h4u/enji-guard-cli/compare/v0.3.10...v0.3.11) (2026-07-05)


### Fixes

* split report read and summary output ([e1bf2b4](https://github.com/j2h4u/enji-guard-cli/commit/e1bf2b453d4da56916224c1da11c306020bf490d))

## [0.3.10](https://github.com/j2h4u/enji-guard-cli/compare/v0.3.9...v0.3.10) (2026-07-05)


### Fixes

* make audit starts deterministic ([1d40f16](https://github.com/j2h4u/enji-guard-cli/commit/1d40f161e6807f7c91f68a813e979f1be2013105))

## [0.3.9](https://github.com/j2h4u/enji-guard-cli/compare/v0.3.8...v0.3.9) (2026-07-04)


### Fixes

* avoid duplicate audit starts from task links ([#83](https://github.com/j2h4u/enji-guard-cli/issues/83)) ([1f73b0e](https://github.com/j2h4u/enji-guard-cli/commit/1f73b0ebe37f1cf9b198ec468b1f4e393d674481))

## [0.3.8](https://github.com/j2h4u/enji-guard-cli/compare/v0.3.7...v0.3.8) (2026-07-04)


### Fixes

* make mutating CLI commands retry-safe ([#81](https://github.com/j2h4u/enji-guard-cli/issues/81)) ([a2661f3](https://github.com/j2h4u/enji-guard-cli/commit/a2661f356ecd519b2a6c0f5a164128289ff301f4))

## [0.3.7](https://github.com/j2h4u/enji-guard-cli/compare/v0.3.6...v0.3.7) (2026-07-04)


### Fixes

* start recon from repo add ([#79](https://github.com/j2h4u/enji-guard-cli/issues/79)) ([c60386f](https://github.com/j2h4u/enji-guard-cli/commit/c60386ff36338617ce32194d9fed475140162340))

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
