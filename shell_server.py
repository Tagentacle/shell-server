"""
Tagentacle Shell Server: MCP Server for Shell Execution.

An MCPServerNode that provides a single ``exec_command`` tool.  Supports
three execution modes:

  - **TACL mode** (``auth_required=True``, recommended for production):
    Each agent's JWT carries a ``space`` claim identifying its isolated
    execution environment (container).  One shell-server instance
    serves multiple agents, dynamically routing commands based on the
    caller's ``space``.

  - **Static container mode** (``TARGET_CONTAINER=xxx``):
    All commands are routed to a single, fixed container.
    Useful for development / single-agent setups.

  - **Local mode** (default):
    Commands run on the host via ``subprocess``.

Key design:
  - Single tool: exec_command
  - Maintains per-session ``cwd`` state (simulates a persistent shell)
  - TACL ``space`` claim is the primary container resolution mechanism
  - Container runtime (Podman/Docker) is optional — only needed when containers are used
"""

import asyncio
import logging
import os
import subprocess
from typing import Annotated, Any, Dict, Optional

from pydantic import Field

from tagentacle_py_mcp import MCPServerNode
from tagentacle_py_mcp.auth import get_caller_identity

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ShellServer(MCPServerNode):
    """MCP Server providing exec_command with dynamic container routing.

    Container resolution order:
      1. TACL JWT ``space`` claim (per-request, from caller identity)
      2. Static ``target_container`` (startup config / env)
      3. Local subprocess (fallback when no container is specified)
    """

    def __init__(
        self,
        node_id: str = "shell_server",
        *,
        mcp_port: int = 8300,
        auth_required: bool = False,
        target_container: Optional[str] = None,
    ):
        super().__init__(
            node_id,
            mcp_name="shell-server",
            mcp_port=mcp_port,
            description="Shell execution MCP server (TACL / container / local)",
            auth_required=auth_required,
        )
        self._static_container = (
            target_container or os.environ.get("TARGET_CONTAINER") or None
        )
        self._runtime = None  # Lazy-init when first container exec is needed
        # Per-session cwd tracking: session_key -> cwd path
        self._session_cwd: Dict[str, str] = {}

    def _resolve_space(self) -> Optional[str]:
        """Resolve the target container for the current request.

        Priority: TACL JWT space > static startup config.
        Returns None for local execution.
        """
        # 1. Dynamic: read from TACL JWT
        caller = get_caller_identity()
        if caller and caller.space:
            return caller.space
        # 2. Static fallback
        return self._static_container

    def _get_cwd(self, session_key: str) -> str:
        return self._session_cwd.get(
            session_key,
            "/" if session_key != "_local_" else os.getcwd(),
        )

    def _set_cwd(self, session_key: str, cwd: str):
        self._session_cwd[session_key] = cwd

    # ── Execution backends ───────────────────────────────────────────

    def _exec_local(self, command: str, workdir: str) -> tuple:
        """Execute via local subprocess. Returns (exit_code, stdout, stderr)."""
        try:
            result = subprocess.run(
                ["sh", "-c", command],
                cwd=workdir,
                capture_output=True,
                timeout=120,
            )
            return (
                result.returncode,
                result.stdout.decode("utf-8", errors="replace"),
                result.stderr.decode("utf-8", errors="replace"),
            )
        except subprocess.TimeoutExpired:
            return (1, "", "Command timed out (120s)")
        except FileNotFoundError:
            return (1, "", f"Working directory not found: {workdir}")
        except Exception as e:
            return (1, "", str(e))

    def _exec_container(self, container_name: str, command: str, workdir: str) -> tuple:
        """Execute via container exec. Returns (exit_code, stdout, stderr)."""
        self._ensure_runtime()
        result = self._runtime.exec(container_name, command, workdir=workdir)
        return result.exit_code, result.stdout, result.stderr

    def _exec(self, space: Optional[str], command: str, workdir: str) -> tuple:
        """Route to the appropriate backend based on resolved space."""
        if space:
            return self._exec_container(space, command, workdir)
        else:
            return self._exec_local(command, workdir)

    def _ensure_runtime(self):
        """Lazily initialize container runtime on first container exec."""
        if self._runtime is not None:
            return
        try:
            from container_runtime import ContainerRuntime
            self._runtime = ContainerRuntime.connect()
            logger.info(f"Container runtime connected: {self._runtime.backend} (lazy init)")
        except Exception as e:
            raise RuntimeError(f"Container runtime unavailable: {e}") from e

    # ── Lifecycle ────────────────────────────────────────────────────

    def on_configure(self, config: Dict[str, Any]):
        """Register MCP tools."""
        if "target_container" in config:
            self._static_container = config["target_container"]

        # ── Register MCP Tool ───────────────────────────────────────

        @self.mcp.tool(description=(
            "Execute a shell command. "
            "Target is resolved from TACL space claim, "
            "static container config, or local host. "
            "Maintains cwd across calls."
        ))
        def exec_command(
            command: Annotated[str, Field(description="Shell command to execute")],
            cwd: Annotated[Optional[str], Field(description="Override working directory for this command")] = None,
        ) -> str:
            space = self._resolve_space()
            session_key = space or "_local_"
            effective_cwd = cwd or self._get_cwd(session_key)

            try:
                exit_code, stdout, stderr = self._exec(space, command, effective_cwd)
            except Exception as e:
                return f"Execution error: {e}"

            # Track cwd if command contains 'cd'
            stripped = command.strip()
            if stripped.startswith("cd ") or stripped == "cd":
                try:
                    _, new_cwd, _ = self._exec(
                        space,
                        f"cd {effective_cwd} && {stripped} && pwd",
                        effective_cwd,
                    )
                    resolved = new_cwd.strip()
                    if resolved:
                        self._set_cwd(session_key, resolved)
                except Exception:
                    pass

            # Truncate large output
            max_len = 64 * 1024
            if len(stdout) > max_len:
                stdout = stdout[:max_len] + "\n... (truncated)"
            if len(stderr) > max_len:
                stderr = stderr[:max_len] + "\n... (truncated)"

            parts = []
            if stdout:
                parts.append(stdout)
            if stderr:
                parts.append(f"[stderr]\n{stderr}")
            if exit_code != 0:
                parts.append(f"[exit_code: {exit_code}]")

            return "\n".join(parts) if parts else "(no output)"

        # Call super AFTER registering tools
        super().on_configure(config)

    def on_shutdown(self):
        """Clean up container runtime client if used."""
        if self._runtime:
            self._runtime.close()
            self._runtime = None
        logger.info("Shell server shut down.")


async def main():
    port = int(os.environ.get("MCP_PORT", "8300"))
    static = os.environ.get("TARGET_CONTAINER")
    auth = os.environ.get("SHELL_AUTH_REQUIRED", "").lower() in ("1", "true", "yes")

    node = ShellServer(
        mcp_port=port,
        auth_required=auth,
        target_container=static,
    )

    config = {}
    if static:
        config["target_container"] = static

    await node.bringup(config)

    if auth:
        logger.info(f"Shell Server ready at {node.mcp_url} (TACL auth, space from JWT)")
    elif static:
        logger.info(f"Shell Server ready at {node.mcp_url} (static container: {static})")
    else:
        logger.info(f"Shell Server ready at {node.mcp_url} (local mode)")
    await node.spin()


if __name__ == "__main__":
    asyncio.run(main())
