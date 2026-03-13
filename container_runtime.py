"""
Tagentacle Container Runtime Abstraction.

Provides a unified interface for Docker and Podman backends.
Auto-detects which runtime is available — prefers Podman over Docker.

Usage:
    from container_runtime import ContainerRuntime

    rt = ContainerRuntime.connect()        # Auto-detect
    rt = ContainerRuntime.connect(backend="podman")  # Explicit
    rt = ContainerRuntime.connect(url="unix:///run/podman/podman.sock")

    container = rt.create("alpine", name="test", command="sleep 3600")
    rt.exec(container.id, ["echo", "hello"])
    rt.stop(container.id)
    rt.remove(container.id)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


@dataclass
class ContainerInfo:
    """Normalized container metadata."""

    id: str
    short_id: str
    name: str
    image: str
    status: str
    labels: Dict[str, str]


@dataclass
class ExecResult:
    """Result of executing a command in a container."""

    exit_code: int
    stdout: str
    stderr: str


class ContainerRuntime:
    """Unified container runtime wrapping Docker or Podman Python SDK.

    Both SDKs follow the same API shape (Docker SDK compatibility is a
    design goal of podman-py), so this class mostly handles:
    - Backend selection and connection
    - Import-time detection
    - Normalizing minor API differences
    """

    def __init__(self, client: Any, backend_name: str):
        self._client = client
        self._backend = backend_name

    @property
    def backend(self) -> str:
        """Return 'podman' or 'docker'."""
        return self._backend

    @property
    def client(self) -> Any:
        """Access the raw SDK client for advanced operations."""
        return self._client

    # ── Factory ──────────────────────────────────────────────────────

    @classmethod
    def connect(
        cls,
        url: Optional[str] = None,
        backend: Optional[str] = None,
    ) -> "ContainerRuntime":
        """Connect to a container runtime.

        Backend resolution order:
        1. Explicit ``backend`` param ("podman" or "docker")
        2. ``CONTAINER_RUNTIME`` environment variable
        3. Auto-detect: try Podman SDK → Docker SDK

        The ``url`` parameter maps to the daemon socket, e.g.:
        - ``unix:///run/podman/podman.sock`` (Podman)
        - ``unix:///var/run/docker.sock`` (Docker)
        - ``tcp://localhost:2375``
        """
        chosen = backend or os.environ.get("CONTAINER_RUNTIME", "").lower()

        if chosen == "podman":
            return cls._connect_podman(url)
        elif chosen == "docker":
            return cls._connect_docker(url)
        elif chosen:
            raise ValueError(f"Unknown backend '{chosen}'. Use 'podman' or 'docker'.")

        # Auto-detect: prefer Podman
        for attempt in (cls._connect_podman, cls._connect_docker):
            try:
                rt = attempt(url)
                logger.info(f"Container runtime auto-detected: {rt.backend}")
                return rt
            except Exception:
                continue

        raise RuntimeError(
            "No container runtime available. "
            "Install podman + podman-py, or docker + docker-py. "
            "See: pip install podman  OR  pip install docker"
        )

    @classmethod
    def _connect_podman(cls, url: Optional[str] = None) -> "ContainerRuntime":
        try:
            from podman import PodmanClient
            from podman.errors import APIError  # noqa: F401 — verify import works
        except ImportError:
            raise RuntimeError("podman-py not installed: pip install podman")

        kwargs = {}
        base_url = url or os.environ.get("CONTAINER_HOST")
        if base_url:
            kwargs["base_url"] = base_url

        client = PodmanClient(**kwargs)
        client.ping()
        logger.info("Connected to Podman")
        return cls(client, "podman")

    @classmethod
    def _connect_docker(cls, url: Optional[str] = None) -> "ContainerRuntime":
        try:
            import docker
        except ImportError:
            raise RuntimeError("docker-py not installed: pip install docker")

        base_url = url or os.environ.get("DOCKER_HOST")
        if base_url:
            client = docker.DockerClient(base_url=base_url)
        else:
            client = docker.from_env()

        client.ping()
        logger.info("Connected to Docker")
        return cls(client, "docker")

    # ── Info ─────────────────────────────────────────────────────────

    def info(self) -> Dict[str, Any]:
        """Get runtime daemon info."""
        return self._client.info()

    def ping(self) -> bool:
        """Ping the daemon."""
        return self._client.ping()

    # ── Container operations ─────────────────────────────────────────

    def create(
        self,
        image: str,
        *,
        name: Optional[str] = None,
        command: Optional[Union[str, List[str]]] = None,
        environment: Optional[Dict[str, str]] = None,
        volumes: Optional[Dict] = None,
        network_mode: str = "host",
        labels: Optional[Dict[str, str]] = None,
        detach: bool = True,
        **kwargs,
    ) -> ContainerInfo:
        """Create and start a container. Returns ContainerInfo."""
        container = self._client.containers.run(
            image,
            command=command,
            name=name,
            environment=environment or {},
            volumes=volumes or {},
            network_mode=network_mode,
            labels=labels or {},
            detach=detach,
            stdin_open=True,
            **kwargs,
        )
        return self._to_info(container)

    def stop(self, container_id: str, timeout: int = 10):
        """Stop a container."""
        container = self._client.containers.get(container_id)
        container.stop(timeout=timeout)

    def remove(self, container_id: str, force: bool = False):
        """Remove a container."""
        container = self._client.containers.get(container_id)
        container.remove(force=force)

    def list(
        self,
        all: bool = False,
        filters: Optional[Dict] = None,
    ) -> List[ContainerInfo]:
        """List containers. Returns list of ContainerInfo."""
        containers = self._client.containers.list(all=all, filters=filters or {})
        return [self._to_info(c) for c in containers]

    def inspect(self, container_id: str) -> Dict[str, Any]:
        """Get low-level container attributes."""
        container = self._client.containers.get(container_id)
        return container.attrs

    def exec(
        self,
        container_id: str,
        command: Union[str, List[str]],
        *,
        workdir: Optional[str] = None,
        environment: Optional[Dict[str, str]] = None,
    ) -> ExecResult:
        """Execute a command inside a container."""
        if isinstance(command, str):
            cmd = ["sh", "-c", command]
        else:
            cmd = command

        container = self._client.containers.get(container_id)
        exit_code, output = container.exec_run(
            cmd,
            workdir=workdir,
            environment=environment,
            demux=True,
        )
        stdout = output[0].decode("utf-8", errors="replace") if output[0] else ""
        stderr = output[1].decode("utf-8", errors="replace") if output[1] else ""
        return ExecResult(exit_code=exit_code, stdout=stdout, stderr=stderr)

    def get(self, container_id: str):
        """Get the raw container object (for advanced SDK operations)."""
        return self._client.containers.get(container_id)

    # ── Cleanup ──────────────────────────────────────────────────────

    def close(self):
        """Close the connection to the daemon."""
        try:
            self._client.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _to_info(container) -> ContainerInfo:
        """Convert a raw SDK container object to our normalized dataclass."""
        image_str = ""
        try:
            tags = container.image.tags
            image_str = tags[0] if tags else str(container.image.id[:12])
        except Exception:
            image_str = "unknown"

        return ContainerInfo(
            id=container.id,
            short_id=container.short_id,
            name=container.name,
            image=image_str,
            status=container.status,
            labels=dict(container.labels or {}),
        )
