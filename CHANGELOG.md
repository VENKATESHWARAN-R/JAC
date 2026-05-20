# Changelog

All notable changes to JAC are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned

- Phase 2 — project memory write-back via summarizer minion
- Phase 3 — minion factory and YAML templates

## [0.1.0] - 2026-05-21

First **alpha** release (Phase 1 + Phase 1.5). Pre-1.0 API; expect breaking changes.

### Added

- Interactive REPL with rich rendering, status spinner, and Logfire tracing
- Layered config (`~/.jac/config.yaml`, `<repo>/.agents/config.yaml`) and `jac init`
- AGENTS.md auto-loading (repo root + `~/.jac/AGENTS.md`)
- Filesystem, search, and shell tools with `reason:` on every call
- Human-in-the-loop approval for mutating file ops and shell
- Session persistence under `<repo>/.agents/sessions/`; `jac --resume`, `jac sessions`
- Exchange-aware message history sliding window (`ProcessHistory`)
- Multi-provider profiles and secrets backends (`jac profiles`, `jac keys`)
- `--profile` flag; `--model` override; keyring / dotenv / env-only backends

### Requirements

- Python 3.13+
- Provider API keys (via `jac init` / `jac keys` / env)

[Unreleased]: https://github.com/VENKATESHWARAN-R/JAC/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/VENKATESHWARAN-R/JAC/releases/tag/v0.1.0
