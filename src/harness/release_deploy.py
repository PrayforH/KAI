"""Build-free Docker Compose deployment with local release-state rollback."""

from __future__ import annotations

import fcntl
import os
import stat
import subprocess
import tempfile
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

from harness.release import ReleaseManifest, image_for, load_release_manifest

CommandRunner = Callable[[Sequence[str], Mapping[str, str]], None]


def _run(command: Sequence[str], environment: Mapping[str, str]) -> None:
    subprocess.run(command, check=True, env=dict(environment))


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(source.read_bytes())
    temporary.replace(target)


class ReleaseComposeDeployer:
    def __init__(
        self,
        *,
        repository_root: Path,
        compose_env_file: Path,
        state_root: Path,
        environment_name: str,
        runner: CommandRunner = _run,
    ) -> None:
        if environment_name not in {"test", "canary", "production"}:
            raise ValueError("environment must be test, canary, or production")
        if not compose_env_file.is_file():
            raise ValueError("the pre-provisioned Compose environment file is unavailable")
        file_mode = stat.S_IMODE(compose_env_file.stat().st_mode)
        if file_mode & 0o077:
            raise ValueError(
                "the Compose environment file must not be accessible by group or others"
            )
        self._root = repository_root.resolve()
        self._env_file = compose_env_file.resolve()
        self._state = state_root.resolve() / environment_name
        self._runner = runner

    @contextmanager
    def _lock(self) -> Generator[None]:
        self._state.mkdir(parents=True, exist_ok=True)
        with (self._state / ".lock").open("a+", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _environment(self, manifest: ReleaseManifest) -> dict[str, str]:
        environment = dict(os.environ)
        api = image_for(manifest, "api")
        web = image_for(manifest, "web")
        sandbox = image_for(manifest, "sandbox")
        environment.update(
            {
                "HARNESS_API_IMAGE": f"{api.reference}@{api.digest}",
                "HARNESS_WEB_IMAGE": f"{web.reference}@{web.digest}",
                "HARNESS_SANDBOX_IMAGE": f"{sandbox.reference}@{sandbox.digest}",
            }
        )
        return environment

    def _compose(self) -> list[str]:
        return [
            "docker",
            "compose",
            "--env-file",
            str(self._env_file),
            "-f",
            str(self._root / "deploy/docker-compose/compose.yaml"),
            "-f",
            str(self._root / "deploy/docker-compose/compose.release.yaml"),
        ]

    def _pull(self, environment: Mapping[str, str]) -> None:
        for key in ("HARNESS_API_IMAGE", "HARNESS_WEB_IMAGE", "HARNESS_SANDBOX_IMAGE"):
            self._runner(("docker", "pull", environment[key]), environment)

    def _activate(self, manifest: ReleaseManifest, *, migrate: bool, seed: bool) -> None:
        environment = self._environment(manifest)
        self._pull(environment)
        compose = self._compose()
        if migrate:
            self._runner((*compose, "run", "--rm", "migrate"), environment)
        self._runner(
            (
                *compose,
                "up",
                "-d",
                "--no-build",
                "--wait",
                "postgres",
                "redis",
                "minio",
                "minio-init",
                "api",
                "worker",
                "web",
            ),
            environment,
        )
        if seed:
            self._runner((*compose, "run", "--rm", "seed"), environment)

    def apply(self, manifest_path: Path) -> ReleaseManifest:
        with self._lock():
            manifest = load_release_manifest(manifest_path)
            current = self._state / "current.json"
            previous = self._state / "previous.json"
            candidate = self._state / "candidate.json"
            failed = self._state / "failed.json"
            if current.is_file():
                _atomic_copy(current, previous)
            _atomic_copy(manifest_path, candidate)
            try:
                self._activate(manifest, migrate=True, seed=True)
            except Exception:
                # Preserve the manifest that actually failed. The active pointer is
                # not advanced until activation and seeding have both succeeded.
                _atomic_copy(candidate, failed)
                raise
            _atomic_copy(candidate, current)
            candidate.unlink(missing_ok=True)
        return manifest

    def rollback(self) -> ReleaseManifest:
        with self._lock():
            current = self._state / "current.json"
            previous = self._state / "previous.json"
            if not previous.is_file():
                raise ValueError("no previous verified release is available for rollback")
            manifest = load_release_manifest(previous)
            failed = self._state / "failed.json"
            candidate = self._state / "candidate.json"
            if candidate.is_file():
                _atomic_copy(candidate, failed)
            elif current.is_file():
                _atomic_copy(current, failed)
            # Database migrations are expand/contract and remain forward-compatible;
            # image rollback never performs an automatic destructive downgrade.
            self._activate(manifest, migrate=False, seed=False)
            _atomic_copy(previous, current)
            candidate.unlink(missing_ok=True)
        return manifest
