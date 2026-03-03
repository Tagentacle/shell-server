# shell-server

MCP Server node providing `exec_command` for Tagentacle containers. Agents connect via MCP (Streamable HTTP) and execute shell commands inside a target Docker container. Reading files, writing files, listing directories — these are all just shell commands (`cat`, `tee`, `ls`).

## MCP Tools

| Tool | Description |
|---|---|
| `exec_command` | Execute a shell command in the target container (maintains `cwd` across calls) |

## Quick Start

```bash
# Install dependencies
cd shell-server
uv sync

# Run with a specific target container
TARGET_CONTAINER=agent_space_1 python shell_server.py

# Or via tagentacle CLI
TARGET_CONTAINER=agent_space_1 tagentacle run .
```

The MCP endpoint will be available at `http://127.0.0.1:8300/mcp`.

## Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `TARGET_CONTAINER` | _(none)_ | Default container to target |
| `MCP_PORT` | `8300` | HTTP port for MCP endpoint |
| `SHELL_AUTH_REQUIRED` | `false` | Require TACL JWT authentication |
| `TAGENTACLE_DAEMON_URL` | `tcp://127.0.0.1:19999` | Daemon address |
| `DOCKER_HOST` | _(system default)_ | Docker daemon socket URL |

### Bringup Config

```toml
[nodes.shell_server]
pkg = "shell-server"
config = { target_container = "agent_space_1", mcp_port = 8300 }
```

## How It Works

```
Agent ──MCP──► shell-server ──docker exec──► container
  │                │
  │ exec_command   │ sh -c "<any command>"
```

- **cwd tracking**: `exec_command` maintains a per-container working directory across calls. Running `cd /workspace` changes the cwd for subsequent commands.
- **TACL support**: Set `SHELL_AUTH_REQUIRED=true` to require JWT auth. Only agents with valid credentials (issued by `PermissionMCPServerNode`) can use the tool.
- **Container override**: `exec_command` accepts an optional `container` parameter to target a different container per-call.

## License

MIT
