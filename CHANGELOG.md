# Changelog — shell-server

All notable changes to **shell-server** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2026-03-13

### Added
- **`.gitignore`**: New file; ignores `__pycache__/`, `.ruff_cache/`, `.venv/`, etc.
- **GitHub Actions CI** (Layer 1): Lint (ruff) + build (uv). No test job — use `tagentacle test` locally.
- **`[build-system]`** in `pyproject.toml`: Added hatchling backend for `uv build` support.

### Fixed
- **`container_runtime.py`**: Defensive `_to_info()` status parsing for podman-py 4.x compatibility.

### Changed
- **`shell_server.py`**: Applied ruff formatting.

## [0.2.0] - 2026-03-03

### Added
- Initial release as standalone package.
- `ShellServer(MCPServerNode)` with `exec_command` MCP tool.
- Three modes: TACL JWT space, static container, local subprocess.
- Per-session cwd tracking.

## [0.1.0] - 2026-03-03

### Added
- Prototype (part of tagentacle v0.4.0 release).
