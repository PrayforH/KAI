"""Per-run Kubernetes/gVisor SandboxProvider driven by kubectl.

The worker owns Kubernetes credentials. Run pods receive neither a service-account
token nor provider/model credentials in their PodSpec; short-lived runtime secrets
are framed over the exec stream immediately before the Claude process starts.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import shutil
import tarfile
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Protocol, cast
from uuid import uuid4

from claude_agent_sdk import ClaudeAgentOptions

from harness.core.models import Run
from harness.runtime.daytona_transport import DaytonaClaudeTransport, RemoteClaudeSession
from harness.sandbox.base import SandboxCommandResult, SandboxHandle, SandboxIsolation
from harness.sandbox.claude_cli import (
    banner_matches,
    version_pin,
    version_text,
)

_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DNS_LABEL = re.compile(r"[^a-z0-9-]+")
_MANAGED_LABEL = "claude-agent-harness"


class KubernetesSandboxError(RuntimeError):
    """Safe infrastructure error that never triggers a Local fallback."""


@dataclass(frozen=True)
class KubernetesPodRecord:
    name: str
    expires_at: datetime


class KubernetesClient(Protocol):
    async def create(self, resources: Mapping[str, object]) -> None: ...

    async def wait_ready(self, pod_name: str, *, timeout_seconds: float) -> None: ...

    async def upload_archive(self, pod_name: str, destination: str, content: bytes) -> None: ...

    async def download_archive(
        self, pod_name: str, source: str, *, max_bytes: int
    ) -> bytes: ...

    def remote_session(self, pod_name: str) -> RemoteClaudeSession: ...

    async def delete(self, pod_name: str, network_policy_name: str) -> None: ...

    async def list_managed_pods(self) -> Sequence[KubernetesPodRecord]: ...


def _safe_dns_name(value: str, *, prefix: str = "harness") -> str:
    normalized = _DNS_LABEL.sub("-", value.lower()).strip("-")
    normalized = normalized[:40].rstrip("-") or "run"
    suffix = base64.b32encode(value.encode()).decode().lower().rstrip("=")[:10]
    # Leave room for the longest managed resource suffix ("-egress").
    return f"{prefix}-{normalized}-{suffix}"[:55].rstrip("-")


def build_run_resources(
    *,
    pod_name: str,
    namespace: str,
    run: Run,
    image: str,
    runtime_class_name: str,
    service_account_name: str,
    cpu_millis: int,
    memory_mib: int,
    disk_mib: int,
    expires_at: datetime,
    egress_gateway_namespace: str,
    egress_gateway_selector: Mapping[str, str],
    egress_gateway_port: int,
    egress_proxy_url: str,
    dns_namespace: str,
) -> dict[str, object]:
    """Build the immutable Pod and its default-deny, gateway-only NetworkPolicy."""

    if not egress_gateway_selector or not egress_proxy_url:
        raise ValueError("Kubernetes sandbox requires a registered egress gateway")
    labels = {
        "app.kubernetes.io/managed-by": _MANAGED_LABEL,
        "harness.sh/run": pod_name,
    }
    annotations = {
        "harness.sh/run-id": run.run_id,
        "harness.sh/session-id": run.session_id,
        "harness.sh/tenant-id": run.tenant_id,
        "harness.sh/expires-at": expires_at.astimezone(UTC).isoformat(),
    }
    resources: list[dict[str, object]] = [
        {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": pod_name,
                "namespace": namespace,
                "labels": labels,
                "annotations": annotations,
            },
            "spec": {
                "runtimeClassName": runtime_class_name,
                "restartPolicy": "Never",
                "automountServiceAccountToken": False,
                "enableServiceLinks": False,
                "serviceAccountName": service_account_name,
                "terminationGracePeriodSeconds": 10,
                "securityContext": {
                    "runAsNonRoot": True,
                    "runAsUser": 1000,
                    "runAsGroup": 1000,
                    "fsGroup": 1000,
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "containers": [
                    {
                        "name": "runtime",
                        "image": image,
                        "imagePullPolicy": "IfNotPresent",
                        "command": ["sleep", "infinity"],
                        "workingDir": "/workspace",
                        "env": [
                            {"name": "HOME", "value": "/workspace/home"},
                            {"name": "TMPDIR", "value": "/tmp"},
                            {"name": "HTTP_PROXY", "value": egress_proxy_url},
                            {"name": "HTTPS_PROXY", "value": egress_proxy_url},
                            {"name": "NO_PROXY", "value": "localhost,127.0.0.1"},
                        ],
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
                            "privileged": False,
                            "readOnlyRootFilesystem": True,
                            "capabilities": {"drop": ["ALL"]},
                        },
                        "resources": {
                            "requests": {
                                "cpu": f"{cpu_millis}m",
                                "memory": f"{memory_mib}Mi",
                                "ephemeral-storage": f"{disk_mib}Mi",
                            },
                            "limits": {
                                "cpu": f"{cpu_millis}m",
                                "memory": f"{memory_mib}Mi",
                                "ephemeral-storage": f"{disk_mib}Mi",
                            },
                        },
                        "volumeMounts": [
                            {"name": "workspace", "mountPath": "/workspace"},
                            {"name": "tmp", "mountPath": "/tmp"},
                        ],
                    }
                ],
                "volumes": [
                    {"name": "workspace", "emptyDir": {"sizeLimit": f"{disk_mib}Mi"}},
                    {"name": "tmp", "emptyDir": {"sizeLimit": "256Mi"}},
                ],
            },
        },
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": f"{pod_name}-egress",
                "namespace": namespace,
                "labels": labels,
            },
            "spec": {
                "podSelector": {"matchLabels": {"harness.sh/run": pod_name}},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [],
                "egress": [
                    {
                        "to": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {
                                        "kubernetes.io/metadata.name": dns_namespace
                                    }
                                },
                                "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
                            }
                        ],
                        "ports": [
                            {"protocol": "UDP", "port": 53},
                            {"protocol": "TCP", "port": 53},
                        ],
                    },
                    {
                        "to": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {
                                        "kubernetes.io/metadata.name": egress_gateway_namespace
                                    }
                                },
                                "podSelector": {
                                    "matchLabels": dict(egress_gateway_selector)
                                },
                            }
                        ],
                        "ports": [
                            {"protocol": "TCP", "port": egress_gateway_port}
                        ],
                    },
                ],
            },
        },
    ]
    # Create the deny policy before the matching Pod so there is no permissive
    # network window while the Kubernetes API processes the resource list.
    resources.reverse()
    return {"apiVersion": "v1", "kind": "List", "items": resources}


class KubectlRemoteSession:
    def __init__(
        self,
        *,
        kubectl_argv: Sequence[str],
        namespace: str,
        pod_name: str,
        container: str = "runtime",
    ) -> None:
        self._kubectl_argv = tuple(kubectl_argv)
        self._namespace = namespace
        self._pod_name = pod_name
        self._container = container
        self._process: asyncio.subprocess.Process | None = None
        self._end_input_marker = f"__HARNESS_END_INPUT_{uuid4().hex}__"

    async def stage_config(
        self, remote_directory: str, files: dict[str, bytes]
    ) -> None:
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            for relative, content in files.items():
                relative_path = PurePosixPath(relative)
                if relative_path.is_absolute() or ".." in relative_path.parts:
                    raise ValueError(
                        "remote Claude config path escaped config directory"
                    )
                info = tarfile.TarInfo(relative_path.as_posix())
                info.size = len(content)
                info.mode = 0o600
                tar.addfile(info, io.BytesIO(content))
        command = [
            *self._kubectl_argv,
            "-n",
            self._namespace,
            "exec",
            "-i",
            self._pod_name,
            "-c",
            self._container,
            "--",
            "sh",
            "-c",
            'rm -rf -- "$1" && mkdir -p -- "$1" && tar -C "$1" -xf -',
            "harness-config",
            remote_directory,
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.communicate(archive.getvalue())
        if process.returncode != 0:
            raise KubernetesSandboxError(
                "failed to stage remote Claude config directory"
            )

    async def start(self, argv: list[str], cwd: str, env: dict[str, str]) -> None:
        if not argv:
            raise ValueError("remote command argv must not be empty")
        environment_lines: list[str] = []
        for key, value in sorted(env.items()):
            if _ENVIRONMENT_NAME.fullmatch(key) is None:
                raise ValueError(f"invalid remote environment variable name: {key}")
            encoded = base64.b64encode(value.encode()).decode("ascii")
            environment_lines.append(f"{key}={encoded}")
        argument_lines = [base64.b64encode(value.encode()).decode("ascii") for value in argv]
        environment_marker = f"__HARNESS_END_ENV_{uuid4().hex}__"
        argument_marker = f"__HARNESS_END_ARGV_{uuid4().hex}__"
        wrapper = (
            'env_marker="$1"; arg_marker="$2"; input_marker="$3"; cwd="$4"; shift 4; '
            'cd -- "$cwd" || exit 72; '
            "while IFS= read -r env_line; do "
            '[ "$env_line" = "$env_marker" ] && break; '
            'key="${env_line%%=*}"; encoded="${env_line#*=}"; '
            'value="$(printf "%s" "$encoded" | base64 -d)" || exit 70; '
            'export "$key=$value"; done; '
            "while IFS= read -r encoded; do "
            '[ "$encoded" = "$arg_marker" ] && break; '
            'value="$(printf "%s" "$encoded" | base64 -d)" || exit 70; '
            'set -- "$@" "$value"; done; '
            '[ "$#" -gt 0 ] || exit 64; '
            'mcp_config_path=""; '
            'if [ -n "${HARNESS_CLAUDE_MCP_CONFIG:-}" ]; then '
            'mcp_config_path="$(mktemp)"; chmod 600 "$mcp_config_path"; '
            'printf "%s" "$HARNESS_CLAUDE_MCP_CONFIG" > "$mcp_config_path"; '
            'unset HARNESS_CLAUDE_MCP_CONFIG; set -- "$@" --mcp-config "$mcp_config_path"; fi; '
            "trap '[ -z \"$mcp_config_path\" ] || rm -f -- \"$mcp_config_path\"' EXIT; "
            "while IFS= read -r line; do "
            'if [ "$line" = "$input_marker" ]; then break; fi; '
            'printf "%s\\n" "$line"; done | "$@"'
        )
        command = [
            *self._kubectl_argv,
            "-n",
            self._namespace,
            "exec",
            "-i",
            self._pod_name,
            "-c",
            self._container,
            "--",
            "bash",
            "-o",
            "pipefail",
            "-c",
            wrapper,
            "harness-stdin",
            environment_marker,
            argument_marker,
            self._end_input_marker,
            cwd,
        ]
        self._process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        frames = [*environment_lines, environment_marker, *argument_lines, argument_marker]
        await self.write("\n".join(frames) + "\n")

    async def write(self, data: str) -> None:
        if self._process is None or self._process.stdin is None:
            raise KubernetesSandboxError("Kubernetes exec session is not running")
        self._process.stdin.write(data.encode())
        await self._process.stdin.drain()

    async def end_input(self) -> None:
        if self._process is not None:
            await self.write(f"{self._end_input_marker}\n")

    async def read_stdout(self) -> bytes | None:
        if self._process is None or self._process.stdout is None:
            return None
        return (chunk if (chunk := await self._process.stdout.read(65_536)) else None)

    async def read_stderr(self) -> bytes | None:
        if self._process is None or self._process.stderr is None:
            return None
        return (chunk if (chunk := await self._process.stderr.read(65_536)) else None)

    async def wait(self) -> int:
        return 1 if self._process is None else await self._process.wait()

    async def terminate(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.stdin is not None and not process.stdin.is_closing():
            process.stdin.close()
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.wait()


class KubectlKubernetesClient:
    """Minimal Kubernetes client using the audited kubectl binary and worker RBAC."""

    def __init__(
        self,
        *,
        namespace: str,
        kubectl_path: str = "kubectl",
        kubeconfig: str | None = None,
        context: str | None = None,
        command_timeout_seconds: float = 120,
    ) -> None:
        self._namespace = namespace
        argv = [kubectl_path]
        if kubeconfig:
            argv.extend(["--kubeconfig", kubeconfig])
        if context:
            argv.extend(["--context", context])
        self._argv = tuple(argv)
        self._command_timeout_seconds = command_timeout_seconds

    async def _run(
        self,
        *arguments: str,
        input_bytes: bytes | None = None,
        timeout_seconds: float | None = None,
        max_output_bytes: int | None = None,
    ) -> bytes:
        process = await asyncio.create_subprocess_exec(
            *self._argv,
            *arguments,
            stdin=asyncio.subprocess.PIPE if input_bytes is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        async def communicate() -> tuple[bytes, bytes]:
            if max_output_bytes is None:
                return await process.communicate(input_bytes)
            if input_bytes is not None:
                raise ValueError("bounded kubectl output cannot also stream input")
            assert process.stdout is not None and process.stderr is not None
            stderr_task = asyncio.create_task(process.stderr.read())
            chunks: list[bytes] = []
            total = 0
            try:
                while chunk := await process.stdout.read(65_536):
                    total += len(chunk)
                    if total > max_output_bytes:
                        if process.returncode is None:
                            process.kill()
                        await process.wait()
                        raise KubernetesSandboxError(
                            "Kubernetes output archive exceeds collection limit"
                        )
                    chunks.append(chunk)
                await process.wait()
                return b"".join(chunks), await stderr_task
            finally:
                if not stderr_task.done():
                    stderr_task.cancel()

        try:
            stdout, _ = await asyncio.wait_for(
                communicate(),
                timeout=timeout_seconds or self._command_timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            raise KubernetesSandboxError("Kubernetes operation timed out") from None
        if process.returncode != 0:
            raise KubernetesSandboxError("Kubernetes operation failed")
        return stdout

    async def create(self, resources: Mapping[str, object]) -> None:
        await self._run("create", "-f", "-", input_bytes=json.dumps(resources).encode())

    async def wait_ready(self, pod_name: str, *, timeout_seconds: float) -> None:
        await self._run(
            "-n",
            self._namespace,
            "wait",
            "--for=condition=Ready",
            f"pod/{pod_name}",
            f"--timeout={max(1, int(timeout_seconds))}s",
            timeout_seconds=timeout_seconds + 5,
        )

    async def upload_archive(self, pod_name: str, destination: str, content: bytes) -> None:
        await self._run(
            "-n",
            self._namespace,
            "exec",
            "-i",
            pod_name,
            "-c",
            "runtime",
            "--",
            "tar",
            "-C",
            destination,
            "-xf",
            "-",
            input_bytes=content,
        )

    async def download_archive(
        self, pod_name: str, source: str, *, max_bytes: int
    ) -> bytes:
        return await self._run(
            "-n",
            self._namespace,
            "exec",
            pod_name,
            "-c",
            "runtime",
            "--",
            "tar",
            "-C",
            source,
            "-cf",
            "-",
            ".",
            max_output_bytes=max_bytes,
        )

    def remote_session(self, pod_name: str) -> RemoteClaudeSession:
        return KubectlRemoteSession(
            kubectl_argv=self._argv,
            namespace=self._namespace,
            pod_name=pod_name,
        )

    async def delete(self, pod_name: str, network_policy_name: str) -> None:
        await self._run(
            "-n",
            self._namespace,
            "delete",
            f"pod/{pod_name}",
            f"networkpolicy/{network_policy_name}",
            "--ignore-not-found=true",
            "--wait=false",
        )

    async def list_managed_pods(self) -> Sequence[KubernetesPodRecord]:
        raw = await self._run(
            "-n",
            self._namespace,
            "get",
            "pods",
            "-l",
            f"app.kubernetes.io/managed-by={_MANAGED_LABEL}",
            "-o",
            "json",
        )
        parsed = json.loads(raw)
        records: list[KubernetesPodRecord] = []
        for item in parsed.get("items", []):
            metadata = item.get("metadata", {})
            value = metadata.get("annotations", {}).get("harness.sh/expires-at")
            name = metadata.get("name")
            if isinstance(name, str) and isinstance(value, str):
                records.append(
                    KubernetesPodRecord(name=name, expires_at=datetime.fromisoformat(value))
                )
        return records


def _workspace_archive(root: Path, *, max_bytes: int, max_members: int) -> bytes:
    buffer = io.BytesIO()
    count = 0
    total = 0
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                continue
            count += 1
            if count > max_members:
                raise ValueError("Kubernetes workspace exceeds collection member limit")
            relative = path.relative_to(root).as_posix()
            if path.is_file():
                total += path.stat().st_size
                if total > max_bytes:
                    raise ValueError("Kubernetes workspace exceeds collection size limit")
            archive.add(path, arcname=relative, recursive=False)
    return buffer.getvalue()


def _extract_workspace_archive(
    content: bytes, root: Path, *, max_bytes: int, max_members: int
) -> None:
    try:
        archive = tarfile.open(fileobj=io.BytesIO(content), mode="r:*")
    except tarfile.TarError:
        raise ValueError("invalid Kubernetes workspace archive") from None
    total = 0
    with archive:
        members = archive.getmembers()
        if len(members) > max_members:
            raise ValueError("Kubernetes workspace exceeds collection member limit")
        for member in members:
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("unsafe Kubernetes workspace archive member")
            parts = tuple(part for part in relative.parts if part not in {"", "."})
            if not parts:
                continue
            target = root.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise ValueError("unsafe Kubernetes workspace archive member")
            total += member.size
            if total > max_bytes:
                raise ValueError("Kubernetes workspace exceeds collection size limit")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("invalid Kubernetes workspace archive")
            data = source.read(max_bytes + 1)
            if len(data) != member.size:
                raise ValueError("invalid Kubernetes workspace archive")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)


class KubernetesSandboxProvider:
    def __init__(
        self,
        *,
        client: KubernetesClient,
        namespace: str,
        image: str,
        runtime_class_name: str = "gvisor",
        service_account_name: str = "harness-sandbox",
        local_root: Path | None = None,
        remote_workspace: str = "/workspace",
        cli_version: str = "",
        cli_path: str = "/usr/local/bin/claude",
        ttl_seconds: int = 3600,
        ready_timeout_seconds: float = 120,
        cpu_millis: int = 2000,
        memory_mib: int = 4096,
        disk_mib: int = 20_480,
        egress_gateway_namespace: str,
        egress_gateway_selector: Mapping[str, str],
        egress_gateway_port: int = 3128,
        egress_proxy_url: str,
        dns_namespace: str = "kube-system",
        max_collect_bytes: int = 512 * 1024 * 1024,
        max_collect_members: int = 10_000,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not image or "@sha256:" not in image:
            raise ValueError("Kubernetes sandbox image must be pinned by digest")
        if any(value <= 0 for value in (ttl_seconds, cpu_millis, memory_mib, disk_mib)):
            raise ValueError("Kubernetes sandbox resource limits must be positive")
        self._client = client
        self._namespace = namespace
        self._image = image
        self._runtime_class_name = runtime_class_name
        self._service_account_name = service_account_name
        self._local_root = local_root
        self._remote_workspace = remote_workspace
        self._cli_version = cli_version
        self._cli_path = cli_path
        self._ttl_seconds = ttl_seconds
        self._ready_timeout_seconds = ready_timeout_seconds
        self._cpu_millis = cpu_millis
        self._memory_mib = memory_mib
        self._disk_mib = disk_mib
        self._egress_gateway_namespace = egress_gateway_namespace
        self._egress_gateway_selector = dict(egress_gateway_selector)
        self._egress_gateway_port = egress_gateway_port
        self._egress_proxy_url = egress_proxy_url
        self._dns_namespace = dns_namespace
        self._max_collect_bytes = max_collect_bytes
        self._max_collect_members = max_collect_members
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self._owned: set[str] = set()

    async def provision(self, run: Run) -> SandboxHandle:
        pod_name = _safe_dns_name(f"{run.run_id}-{run.fencing_token}")
        expires_at = self._clock() + timedelta(seconds=self._ttl_seconds)
        resources = build_run_resources(
            pod_name=pod_name,
            namespace=self._namespace,
            run=run,
            image=self._image,
            runtime_class_name=self._runtime_class_name,
            service_account_name=self._service_account_name,
            cpu_millis=self._cpu_millis,
            memory_mib=self._memory_mib,
            disk_mib=self._disk_mib,
            expires_at=expires_at,
            egress_gateway_namespace=self._egress_gateway_namespace,
            egress_gateway_selector=self._egress_gateway_selector,
            egress_gateway_port=self._egress_gateway_port,
            egress_proxy_url=self._egress_proxy_url,
            dns_namespace=self._dns_namespace,
        )
        path = Path(tempfile.mkdtemp(prefix=f"{pod_name}-", dir=self._local_root))
        self._owned.add(pod_name)
        try:
            await self._client.create(resources)
            await self._client.wait_ready(
                pod_name, timeout_seconds=self._ready_timeout_seconds
            )
        except Exception:
            if pod_name in self._owned:
                with suppress(Exception):
                    await self._client.delete(pod_name, f"{pod_name}-egress")
                self._owned.discard(pod_name)
            shutil.rmtree(path, ignore_errors=True)
            raise

        def transport_factory(raw_options: object) -> object:
            options = cast(ClaudeAgentOptions, raw_options)
            options.env = {
                **options.env,
                "CLAUDE_CONFIG_DIR": "/tmp/harness-claude-config",
            }
            return DaytonaClaudeTransport(
                session=self._client.remote_session(pod_name),
                options=options,
                remote_workspace=self._remote_workspace,
                cli_path=self._cli_path,
            )

        return SandboxHandle(
            sandbox_id=pod_name,
            path=path,
            provider="kubernetes-gvisor",
            isolation_level=SandboxIsolation.CONTAINER,
            remote_workspace=self._remote_workspace,
            runtime_transport_factory=transport_factory,
        )

    async def prepare(self, handle: SandboxHandle) -> None:
        version = await self.execute(handle, (self._cli_path, "--version"))
        if version.exit_code != 0 or not banner_matches(
            version_text(version.stdout, version.stderr), version_pin(self._cli_version)
        ):
            raise KubernetesSandboxError("Kubernetes sandbox Claude CLI version mismatch")
        content = _workspace_archive(
            handle.path,
            max_bytes=self._max_collect_bytes,
            max_members=self._max_collect_members,
        )
        await self._client.upload_archive(
            handle.sandbox_id, self._remote_workspace, content
        )

    async def execute(
        self,
        handle: SandboxHandle,
        argv: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float = 30,
    ) -> SandboxCommandResult:
        if not argv:
            raise ValueError("sandbox command argv must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("sandbox command timeout must be positive")
        session = self._client.remote_session(handle.sandbox_id)
        await session.start(list(argv), self._remote_workspace, dict(environment or {}))
        try:
            await session.end_input()

            async def read_stdout() -> bytes:
                chunks: list[bytes] = []
                while (chunk := await session.read_stdout()) is not None:
                    chunks.append(chunk)
                return b"".join(chunks)

            async def read_stderr() -> bytes:
                chunks: list[bytes] = []
                while (chunk := await session.read_stderr()) is not None:
                    chunks.append(chunk)
                return b"".join(chunks)

            stdout, stderr, exit_code = await asyncio.wait_for(
                asyncio.gather(read_stdout(), read_stderr(), session.wait()),
                timeout=timeout_seconds,
            )
        finally:
            await session.terminate()
        return SandboxCommandResult(
            exit_code=exit_code,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
        )

    async def collect(self, handle: SandboxHandle) -> None:
        content = await self._client.download_archive(
            handle.sandbox_id,
            self._remote_workspace,
            max_bytes=(
                self._max_collect_bytes
                + self._max_collect_members * 1024
                + 10_240
            ),
        )
        _extract_workspace_archive(
            content,
            handle.path,
            max_bytes=self._max_collect_bytes,
            max_members=self._max_collect_members,
        )

    async def destroy(self, handle: SandboxHandle) -> None:
        try:
            if handle.sandbox_id in self._owned:
                await self._client.delete(
                    handle.sandbox_id, f"{handle.sandbox_id}-egress"
                )
        finally:
            self._owned.discard(handle.sandbox_id)
            shutil.rmtree(handle.path, ignore_errors=True)

    async def reap_expired(self) -> int:
        now = self._clock()
        expired = [
            record
            for record in await self._client.list_managed_pods()
            if record.expires_at <= now
        ]
        for record in expired:
            await self._client.delete(record.name, f"{record.name}-egress")
            self._owned.discard(record.name)
        return len(expired)

    async def active_count(self) -> int:
        return len(await self._client.list_managed_pods())
