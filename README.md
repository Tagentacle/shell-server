# shell-server

MCP Server providing `exec_command` — a single tool for shell execution. Supports two modes:

- **Local mode** (default): runs commands on the host via `subprocess`
- **Container mode**: runs commands inside a Docker container via `docker exec`

Reading files, writing files, listing directories — these are all just shell commands (`cat`, `tee`, `ls`).

## MCP Tools

| Tool | Description |
|---|---|
| `exec_command` | Execute a shell command (maintains `cwd` across calls) |

## Quick Start

```bash
cd shell-server
uv sync

# Local mode (default) — execute on host
python shell_server.py

# Container mode — execute inside a Docker container
TARGET_CONTAINER=agent_space_1 python shell_server.py

# Install docker extra for container mode
uv sync --extra container
```

The MCP endpoint will be available at `http://127.0.0.1:8300/mcp`.

## Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `TARGET_CONTAINER` | _(none)_ | Container name/id to target. Omit for local mode |
| `MCP_PORT` | `8300` | HTTP port for MCP endpoint |
| `SHELL_AUTH_REQUIRED` | `false` | Require TACL JWT authentication |
| `TAGENTACLE_DAEMON_URL` | `tcp://127.0.0.1:19999` | Daemon address |
| `DOCKER_HOST` | _(system default)_ | Docker daemon socket URL (container mode only) |

### Bringup Config

```toml
# Local mode
[nodes.shell_server]
pkg = "shell-server"
config = { mcp_port = 8300 }

# Container mode
[nodes.shell_server]
pkg = "shell-server"
config = { target_container = "agent_space_1", mcp_port = 8300 }
```

## How It Works

```
# Local mode
Agent ──MCP──► shell-server ──subprocess──► host shell

# Container mode
Agent ──MCP──► shell-server ──docker exec──► container
```

- **Dual mode**: No `TARGET_CONTAINER` → local subprocess; with `TARGET_CONTAINER` → docker exec. Determined at startup.
- **cwd tracking**: `exec_command` maintains a working directory per session. `cd /workspace` persists for subsequent commands.
- **TACL support**: Set `SHELL_AUTH_REQUIRED=true` to require JWT auth.
- **Docker optional**: The `docker` Python package is only needed for container mode (`uv sync --extra container`).

## License

MIT
