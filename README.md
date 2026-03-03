# shell-server

MCP Server node providing shell execution tools for Tagentacle containers. Agents connect via MCP (Streamable HTTP) and can run commands, read/write files, and list directories inside a target Docker container.

## MCP Tools

| Tool | Description |
|---|---|
| `exec_command` | Execute a shell command in the target container (maintains `cwd` across calls) |
| `read_file` | Read a file from the container |
| `write_file` | Write content to a file in the container |
| `list_dir` | List directory contents |

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
  │ exec_command   │ sh -c "ls -la"
  │ read_file      │ cat /path/to/file
  │ write_file     │ printf '...' > /path
  │ list_dir       │ ls -la /dir
```

- **cwd tracking**: `exec_command` maintains a per-container working directory across calls. Running `cd /workspace` changes the cwd for subsequent commands.
- **TACL support**: Set `SHELL_AUTH_REQUIRED=true` to require JWT auth. Only agents with valid credentials (issued by `PermissionMCPServerNode`) can use the tools.
- **Container override**: Each tool accepts an optional `container` parameter to target a different container per-call.

## License

MIT
