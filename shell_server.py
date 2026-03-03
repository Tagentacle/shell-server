"""
Tagentacle Shell Server: MCP Server for Shell Execution.

An MCPServerNode that provides a single ``exec_command`` tool. Can operate
in two modes depending on startup configuration:

  - **Local mode** (default): executes commands via subprocess on the host.
  - **Container mode** (``TARGET_CONTAINER=xxx``): executes via ``docker exec``.

Key design:
  - Single tool: exec_command
  - Maintains per-session ``cwd`` state (simulates a persistent shell)
  - Container is an optional startup parameter, not a hard requirement
  - Can be TACL-protected: ``auth_required=True``
"""

import asyncio
import logging
import os
import subprocess
from typing import Annotated, Any, Dict, Optional

from pydantic import Field

from tagentacle_py_mcp import MCPServerNode

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ShellServer(MCPServerNode):
    """MCP Server providing exec_command.

    Execution backend is determined at startup:
      - No container specified → local subprocess
      - Container specified → docker exec

    Container can be set via:
      1. Constructor ``target_container`` parameter
      2. ``TARGET_CONTAINER`` environment variable
      3. ``target_container`` in bringup config
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
            description="Shell execution MCP server (local or container)",
            auth_required=auth_required,
        )
        self._target_container = (
            target_container or os.environ.get("TARGET_CONTAINER") or None
        )
        self._docker = None  # Lazy-init only when container mode
        # Per-session cwd tracking: session_key -> cwd path
        self._session_cwd: Dict[str, str] = {}

    @property
    def container_mode(self) -> bool:
        """Whether this server targets a Docker container."""
        return self._target_container is not None

    @property
    def target_container(self) -> Optional[str]:
        return self._target_container

    def _get_cwd(self, session_key: str) -> str:
        return self._session_cwd.get(session_key, os.getcwd() if not self.container_mode else "/")

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

    def _exec_container(self, command: str, workdir: str) -> tuple:
        """Execute via docker exec. Returns (exit_code, stdout, stderr)."""
        container = self._docker.containers.get(self._target_container)
        exit_code, output = container.exec_run(
            ["sh", "-c", command],
            workdir=workdir,
            demux=True,
        )
        stdout = output[0].decode("utf-8", errors="replace") if output[0] else ""
        stderr = output[1].decode("utf-8", errors="replace") if output[1] else ""
        return exit_code, stdout, stderr

    def _exec(self, command: str, workdir: str) -> tuple:
        """Route to the appropriate backend."""
        if self.container_mode:
            return self._exec_container(command, workdir)
        else:
            return self._exec_local(command, workdir)

    # ── Lifecycle ────────────────────────────────────────────────────

    def on_configure(self, config: Dict[str, Any]):
        """Register MCP tools. Connect to Docker only if container mode."""
        if "target_container" in config:
            self._target_container = config["target_container"]

        # Only connect to Docker if targeting a container
        if self.container_mode:
            try:
                import docker
                from docker.errors import DockerException
                docker_url = config.get("docker_url") or os.environ.get("DOCKER_HOST")
                if docker_url:
                    self._docker = docker.DockerClient(base_url=docker_url)
                else:
                    self._docker = docker.from_env()
                self._docker.ping()
                logger.info(f"Container mode: targeting '{self._target_container}'")
            except (ImportError, DockerException) as e:
                logger.error(f"Container mode requested but Docker unavailable: {e}")
                raise
        else:
            logger.info("Local mode: executing commands on host")

        # ── Register MCP Tool ───────────────────────────────────────

        @self.mcp.tool(description="Execute a shell command. Maintains cwd across calls.")
        def exec_command(
            command: Annotated[str, Field(description="Shell command to execute")],
            cwd: Annotated[Optional[str], Field(description="Override working directory for this command")] = None,
        ) -> str:
            session_key = self._target_container or "_local_"
            effective_cwd = cwd or self._get_cwd(session_key)

            try:
                exit_code, stdout, stderr = self._exec(command, effective_cwd)
            except Exception as e:
                return f"Execution error: {e}"

            # Track cwd if command contains 'cd'
            stripped = command.strip()
            if stripped.startswith("cd ") or stripped == "cd":
                try:
                    _, new_cwd, _ = self._exec(
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
        """Clean up Docker client if used."""
        if self._docker:
            try:
                self._docker.close()
            except Exception:
                pass
            self._docker = None
        logger.info("Shell server shut down.")


async def main():
    port = int(os.environ.get("MCP_PORT", "8300"))
    target = os.environ.get("TARGET_CONTAINER")
    auth = os.environ.get("SHELL_AUTH_REQUIRED", "").lower() in ("1", "true", "yes")

    node = ShellServer(
        mcp_port=port,
        auth_required=auth,
        target_container=target,
    )

    config = {}
    if target:
        config["target_container"] = target

    await node.bringup(config)

    mode = f"container '{target}'" if target else "local"
    logger.info(f"Shell Server ready at {node.mcp_url} (mode: {mode})")
    await node.spin()


if __name__ == "__main__":
    asyncio.run(main())
