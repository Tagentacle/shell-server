# shell-server

MCP Server providing `exec_command` — a single tool for shell execution. Supports three modes:

- **TACL mode** (recommended): `auth_required=True` — each agent's JWT carries a `space` claim identifying its container. One shell-server serves all agents, routing dynamically.
- **Static container mode**: `TARGET_CONTAINER=xxx` — all commands go to one fixed container.
- **Local mode** (default): runs commands on the host via `subprocess`.

Reading files, writing files, listing directories — these are all just shell commands (`cat`, `tee`, `ls`).

## MCP Tools

| Tool | Description |
|---|---|
| `exec_command` | Execute a shell command (maintains `cwd` across calls) |

## Quick Start

```bash
cd shell-server

# Local mode (default) — execute on host
python shell_server.py

# Static container mode — execute inside a fixed container
TARGET_CONTAINER=agent_space_1 python shell_server.py

# TACL mode — dynamic routing from JWT space claim (production)
SHELL_AUTH_REQUIRED=true python shell_server.py

# Install container backend (pick one)
pip install podman   # Podman (recommended)
pip install docker   # Docker
```

The MCP endpoint will be available at `http://127.0.0.1:8300/mcp`.

## Container Resolution

When `exec_command` is called, the target is resolved in this order:

1. **TACL JWT `space` claim** — if `auth_required=True` and the caller's JWT contains a `space` field, that value is used as the container name.
2. **Static `TARGET_CONTAINER`** — fallback if no `space` in JWT.
3. **Local subprocess** — if no container is resolved at all.

This means a single shell-server instance can serve many agents, each routed to their own container based on their TACL credential.

## Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SHELL_AUTH_REQUIRED` | `false` | Enable TACL JWT authentication (recommended) |
| `TARGET_CONTAINER` | _(none)_ | Static container fallback. Omit for local mode |
| `MCP_PORT` | `8300` | HTTP port for MCP endpoint |
| `TAGENTACLE_DAEMON_URL` | `tcp://127.0.0.1:19999` | Daemon address |
| `CONTAINER_RUNTIME` | _(auto-detect)_ | Force `podman` or `docker` backend |
| `CONTAINER_HOST` | _(system default)_ | Podman daemon socket URL (container modes only) |
| `DOCKER_HOST` | _(system default)_ | Docker daemon socket URL (container modes only) |

### Bringup Config

```toml
# TACL mode (production) — space comes from JWT
[nodes.shell_server]
pkg = "shell-server"
config = { mcp_port = 8300, auth_required = true }

# Static container mode (dev)
[nodes.shell_server]
pkg = "shell-server"
config = { target_container = "agent_space_1", mcp_port = 8300 }

# Local mode
[nodes.shell_server]
pkg = "shell-server"
config = { mcp_port = 8300 }
```

## How It Works

```
# TACL mode: JWT space → container routing
Agent A (space=container_1) ──MCP──► shell-server ──container exec──► container_1
Agent B (space=container_2) ──MCP──► shell-server ──container exec──► container_2

# Static mode
Agent ──MCP──► shell-server ──container exec──► fixed container

# Local mode
Agent ──MCP──► shell-server ──subprocess──► host shell
```

- **TACL space binding**: When admin registers an agent via `PermissionMCPServerNode.register_agent`, they specify a `space` (e.g. container name). This gets embedded in the JWT. Shell-server reads `CallerIdentity.space` per request.
- **cwd tracking**: `exec_command` maintains a working directory per session (keyed by space/container). `cd /workspace` persists for subsequent commands.
- **Runtime lazy-init**: The container runtime client is only created on the first container exec, not at startup.
- **Runtime optional**: `podman` or `docker` Python package is only needed for container modes.

## License

MIT
