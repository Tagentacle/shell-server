"""
Tagentacle Shell Server: MCP Server for Container Shell Execution.

An MCPServerNode that provides file and shell tools targeting a specific
Docker container. Agents connect to this server via MCP (Streamable HTTP)
and can execute commands, read/write files, and list directories inside
the target container.

Key design:
  - Maintains per-session ``cwd`` state (simulates a persistent shell)
  - Uses ``docker exec`` under the hood
  - Can be TACL-protected: ``auth_required=True``
  - Target container is configured via env or bringup config

MCP Tools:
  exec_command  — Execute a shell command in the container
  read_file     — Read a file from the container
  write_file    — Write content to a file in the container
  list_dir      — List directory contents in the container
"""

import asyncio
import logging
import os
from typing import Annotated, Any, Dict, Optional

from pydantic import Field

import docker
from docker.errors import DockerException, NotFound, APIError

from tagentacle_py_mcp import MCPServerNode

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ShellServer(MCPServerNode):
    """MCP Server providing shell tools for a target container.

    The target container is determined by:
      1. ``TARGET_CONTAINER`` environment variable
      2. ``target_container`` in bringup config
      3. Explicit ``container`` parameter in tool calls (per-call override)
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
            description="Shell execution tools for Tagentacle containers",
            auth_required=auth_required,
        )
        self._target_container = target_container
        self._docker: Optional[docker.DockerClient] = None
        # Per-session cwd tracking: session_key -> cwd path
        self._session_cwd: Dict[str, str] = {}

    @property
    def target_container(self) -> str:
        """Resolve the default target container name/id."""
        return (
            self._target_container
            or os.environ.get("TARGET_CONTAINER", "")
        )

    def _resolve_container(self, explicit: Optional[str] = None) -> str:
        """Resolve container: explicit param > default target."""
        cid = explicit or self.target_container
        if not cid:
            raise ValueError(
                "No target container specified. Set TARGET_CONTAINER env var, "
                "provide 'target_container' in config, or pass 'container' parameter."
            )
        return cid

    def _get_cwd(self, session_key: str) -> str:
        """Get current working directory for a session."""
        return self._session_cwd.get(session_key, "/")

    def _set_cwd(self, session_key: str, cwd: str):
        """Set current working directory for a session."""
        self._session_cwd[session_key] = cwd

    def _docker_exec(
        self,
        container_name: str,
        command: list,
        workdir: Optional[str] = None,
    ) -> tuple:
        """Execute command in container. Returns (exit_code, stdout, stderr)."""
        container = self._docker.containers.get(container_name)
        exit_code, output = container.exec_run(
            command,
            workdir=workdir,
            demux=True,
        )
        stdout = output[0].decode("utf-8", errors="replace") if output[0] else ""
        stderr = output[1].decode("utf-8", errors="replace") if output[1] else ""
        return exit_code, stdout, stderr

    # ── Lifecycle ────────────────────────────────────────────────────

    def on_configure(self, config: Dict[str, Any]):
        """Register MCP tools and connect to Docker."""
        # Resolve target container from config
        if "target_container" in config:
            self._target_container = config["target_container"]

        # Connect to Docker
        docker_url = config.get("docker_url") or os.environ.get("DOCKER_HOST")
        try:
            if docker_url:
                self._docker = docker.DockerClient(base_url=docker_url)
            else:
                self._docker = docker.from_env()
            self._docker.ping()
            logger.info("Docker connected for shell-server.")
        except DockerException as e:
            logger.error(f"Failed to connect to Docker: {e}")
            raise

        # ── Register MCP Tools ──────────────────────────────────────

        @self.mcp.tool(description="Execute a shell command in the target container. Maintains cwd across calls.")
        def exec_command(
            command: Annotated[str, Field(description="Shell command to execute")],
            container: Annotated[Optional[str], Field(description="Target container name/id (optional, uses default)")] = None,
            cwd: Annotated[Optional[str], Field(description="Override working directory for this command")] = None,
        ) -> str:
            try:
                cid = self._resolve_container(container)
            except ValueError as e:
                return str(e)

            # Session key — in a real deployment, derive from MCP session/caller
            session_key = cid
            effective_cwd = cwd or self._get_cwd(session_key)

            try:
                exit_code, stdout, stderr = self._docker_exec(
                    cid,
                    ["sh", "-c", command],
                    workdir=effective_cwd,
                )
            except NotFound:
                return f"Error: Container '{cid}' not found"
            except APIError as e:
                return f"Docker API error: {e}"

            # Track cwd if command was 'cd'
            stripped = command.strip()
            if stripped.startswith("cd "):
                # Resolve the new cwd
                try:
                    _, new_cwd, _ = self._docker_exec(
                        cid, ["sh", "-c", f"cd {effective_cwd} && {stripped} && pwd"],
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

        @self.mcp.tool(description="Read a file from the target container")
        def read_file(
            path: Annotated[str, Field(description="Absolute path to the file inside the container")],
            container: Annotated[Optional[str], Field(description="Target container name/id (optional)")] = None,
        ) -> str:
            try:
                cid = self._resolve_container(container)
            except ValueError as e:
                return str(e)

            try:
                exit_code, stdout, stderr = self._docker_exec(
                    cid, ["cat", path]
                )
                if exit_code != 0:
                    return f"Error reading {path}: {stderr}"
                # Truncate
                if len(stdout) > 128 * 1024:
                    stdout = stdout[:128 * 1024] + "\n... (truncated)"
                return stdout
            except NotFound:
                return f"Error: Container '{cid}' not found"
            except APIError as e:
                return f"Docker API error: {e}"

        @self.mcp.tool(description="Write content to a file in the target container")
        def write_file(
            path: Annotated[str, Field(description="Absolute path to write inside the container")],
            content: Annotated[str, Field(description="File content to write")],
            container: Annotated[Optional[str], Field(description="Target container name/id (optional)")] = None,
        ) -> str:
            try:
                cid = self._resolve_container(container)
            except ValueError as e:
                return str(e)

            try:
                # Use printf to handle content safely, writing via sh
                # Escape single quotes in content
                escaped = content.replace("'", "'\\''")
                exit_code, stdout, stderr = self._docker_exec(
                    cid,
                    ["sh", "-c", f"mkdir -p \"$(dirname '{path}')\" && printf '%s' '{escaped}' > '{path}'"],
                )
                if exit_code != 0:
                    return f"Error writing {path}: {stderr}"
                return f"Written {len(content)} bytes to {path}"
            except NotFound:
                return f"Error: Container '{cid}' not found"
            except APIError as e:
                return f"Docker API error: {e}"

        @self.mcp.tool(description="List directory contents in the target container")
        def list_dir(
            path: Annotated[Optional[str], Field(description="Directory path (default: current cwd)")] = None,
            container: Annotated[Optional[str], Field(description="Target container name/id (optional)")] = None,
        ) -> str:
            try:
                cid = self._resolve_container(container)
            except ValueError as e:
                return str(e)

            session_key = cid
            target_dir = path or self._get_cwd(session_key)

            try:
                exit_code, stdout, stderr = self._docker_exec(
                    cid, ["ls", "-la", target_dir]
                )
                if exit_code != 0:
                    return f"Error listing {target_dir}: {stderr}"
                return stdout
            except NotFound:
                return f"Error: Container '{cid}' not found"
            except APIError as e:
                return f"Docker API error: {e}"

        # Call super AFTER registering tools (MCPServerNode reads port config)
        super().on_configure(config)

    def on_shutdown(self):
        """Clean up Docker client."""
        if self._docker:
            try:
                self._docker.close()
            except Exception:
                pass
            self._docker = None
        logger.info("Shell server shut down.")


async def main():
    port = int(os.environ.get("MCP_PORT", "8300"))
    target = os.environ.get("TARGET_CONTAINER", "")
    auth = os.environ.get("SHELL_AUTH_REQUIRED", "").lower() in ("1", "true", "yes")

    node = ShellServer(
        mcp_port=port,
        auth_required=auth,
        target_container=target or None,
    )

    config = {}
    if target:
        config["target_container"] = target

    await node.bringup(config)
    logger.info(
        f"Shell Server ready at {node.mcp_url} "
        f"(target: {node.target_container or '<per-call>'})"
    )
    await node.spin()


if __name__ == "__main__":
    asyncio.run(main())
