"""Lease-bound, read-only ACP calls under the enrolled desktop user's login.

Installed with the fixed remote gate. No model-supplied command, environment,
user identity or adapter path is accepted. This is not a general sudo bridge.
"""

import ctypes
import json
import os
import pwd
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, cast

ROOT = Path("/var/lib/kai-computer")
CLIENT = Path("/opt/kai-computer/acp_client.mjs")
MAX_OUTPUT = 48 * 1024


def arguments(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.keys() - {"agent", "prompt", "cwd", "timeout_seconds"}:
        raise ValueError("ACP request has unsupported fields")
    if payload.get("agent") not in {"claude", "codex"}:
        raise ValueError("ACP agent must be claude or codex")
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 6000:
        raise ValueError("ACP prompt is invalid")
    duration = payload.get("timeout_seconds", 120)
    if type(duration) is not int or not 10 <= duration <= 180:
        raise ValueError("ACP duration must be 10–180 seconds")
    cwd = payload.get("cwd")
    if cwd is not None and (not isinstance(cwd, str) or not Path(cwd).is_absolute()):
        raise ValueError("ACP cwd must be an authorized absolute path")
    return {"agent": payload["agent"], "prompt": prompt, "cwd": cwd,
            "timeout_seconds": duration}


def authorized_directory(cwd: str | None, roots: list[str]) -> str:
    if not roots:
        raise PermissionError("ACP has no authorized projects")
    path = Path(cwd or roots[0]).resolve(strict=True)
    if not path.is_dir() or not any(path.is_relative_to(Path(root).resolve()) for root in roots):
        raise PermissionError("ACP project is not authorized")
    return str(path)


def current_lease(state: dict[str, Any], request: dict[str, Any]) -> bool:
    return (state.get("active") is True
            and type(request.get("epoch")) is int
            and state.get("epoch") == request["epoch"]
            and isinstance(request.get("lease_id"), str)
            and re.fullmatch(r"[A-Za-z0-9_-]{1,160}", request["lease_id"]) is not None
            and state.get("lease_id") == request["lease_id"]
            and state.get("expires_at", 0) > time.time())


def run(config: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    if request.keys() - {"agent", "prompt", "cwd", "timeout_seconds",
                         "epoch", "lease_id", "expires_at"}:
        raise ValueError("ACP lease request is invalid")
    payload = arguments({k: v for k, v in request.items()
                         if k not in {"epoch", "lease_id", "expires_at"}})
    settings = config["acp"]
    payload["cwd"] = authorized_directory(payload["cwd"], settings["allowed_roots"])
    payload["install_dir"] = settings["install_dir"]
    account = pwd.getpwuid(config["desktop_uid"])
    if account.pw_uid == 0:
        raise PermissionError("ACP cannot run as root")

    def check() -> None:
        if not current_lease(json.loads((ROOT / "state.json").read_bytes()), request):
            raise PermissionError("ACP execution lease was lost")

    check()
    env = {"HOME": account.pw_dir, "USER": account.pw_name, "LOGNAME": account.pw_name,
           "LANG": "C.UTF-8",
           "PATH": account.pw_dir + "/.npm-global/bin:/usr/local/bin:/usr/bin:/bin"}
    libc = ctypes.CDLL(None, use_errno=True)

    def drop_user() -> None:
        if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
            os._exit(125)
        os.setgroups([])
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)

    def interrupted(_signum: int, _frame: object) -> None:
        raise SystemExit(129)

    signal.signal(signal.SIGHUP, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    with tempfile.TemporaryFile() as output:
        child = subprocess.Popen(
            [settings["node_binary"], str(CLIENT)], stdin=subprocess.PIPE, stdout=output,
            stderr=subprocess.DEVNULL, env=env, cwd=payload["cwd"],
            start_new_session=True, preexec_fn=drop_user,
        )
        try:
            assert child.stdin is not None
            child.stdin.write(json.dumps(payload).encode())
            child.stdin.close()
            deadline = time.monotonic() + payload["timeout_seconds"]
            while child.poll() is None:
                check()
                if time.monotonic() >= deadline:
                    return {"ok": False, "code": "ACP_TIMEOUT", "retry": "unsafe"}
                if output.tell() > MAX_OUTPUT:
                    return {"ok": False, "code": "ACP_OUTPUT_TOO_LARGE"}
                time.sleep(0.25)
            check()
            output.seek(0)
            body = output.read(MAX_OUTPUT + 1)
            if len(body) > MAX_OUTPUT:
                raise ValueError("ACP result exceeds size limit")
            raw: object = json.loads(body)
            if not isinstance(raw, dict):
                raise ValueError("ACP result is malformed")
            result = cast(dict[str, Any], raw)
            if type(result.get("ok")) is not bool:
                raise ValueError("ACP result is malformed")
            return result
        finally:
            # Includes the adapter and its app-server/CLI descendants, not
            # any existing user terminal or unrelated coding session.
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
