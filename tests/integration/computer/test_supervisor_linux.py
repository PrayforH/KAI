"""Run only inside a disposable Linux container with its own process namespace.

    KAI_COMPUTER_SUPERVISOR_TEST=1 python -m unittest discover -s tests/integration/computer

No host process or production sandbox is a valid test target.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from uuid import uuid4

from harness.computer.supervisor import Guard, Token


class FixtureGuard(Guard):
    def replace_state(self, state: dict[str, Any]) -> None:
        with self._locked():
            self._save(state)


@unittest.skipUnless(
    sys.platform == "linux"
    and os.geteuid() == 0
    and os.getenv("KAI_COMPUTER_SUPERVISOR_TEST") == "1",
    "requires an explicitly isolated root Linux test container",
)
class SupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="kai-supervisor-test-")
        self.base = Path(self.directory.name)
        self.base.chmod(0o755)
        self.home = self.base / "home"
        self.home.mkdir(mode=0o700)
        os.chown(self.home, 28000, 28000)
        self.guard = FixtureGuard(self.base / "guard", home=str(self.home))
        self.token = Token(1, "computer_lease_" + uuid4().hex)
        self.guard.activate(self.token, time.time() + 30)

    def tearDown(self) -> None:
        state = json.loads((self.guard.root / "state.json").read_text())
        token = Token(state["epoch"], state["lease_id"])
        self.guard.deactivate(token)
        # Reap root watchdog children owned by this test runner when possible.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if pid == 0:
                time.sleep(0.02)
        self.directory.cleanup()

    def execute(self, program: str, *, token: Token | None = None, timeout: float = 5) -> int:
        return self.guard.execute(
            token or self.token,
            {
                "argv": [sys.executable, "-c", program],
                "cwd": str(self.home),
                "timeout_seconds": timeout,
            },
        )

    def daemon(self) -> int:
        target = self.home / "daemon.pid"
        script = (
            "import os,time; pid=os.fork(); "
            "os._exit(0) if pid else None; os.setsid(); "
            f"open({str(target)!r},'w').write(str(os.getpid())); time.sleep(60)"
        )
        self.assertEqual(self.execute(script), 0)
        deadline = time.monotonic() + 3
        while not target.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(target.exists())
        for path in Path("/proc").iterdir():
            if not path.name.isdecimal():
                continue
            try:
                value = (path / "status").read_text()
                if "Uid:\t28000\t" in value and "State:\tZ" not in value:
                    return int(path.name)
            except FileNotFoundError:
                continue
        raise AssertionError("daemon was not visible to its parent namespace")

    def assert_stopped(self, pid: int) -> None:
        path = Path(f"/proc/{pid}/status")
        try:
            content = path.read_text()
        except FileNotFoundError:
            return
        state = next(line for line in content.splitlines() if line.startswith("State:"))
        self.assertIn("Z", state)

    def wait_exited(self, pid: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                process = Path(f"/proc/{pid}/stat").read_text()
                if process.rsplit(")", 1)[1].lstrip().startswith("Z"):
                    return True
            except FileNotFoundError:
                return True
            time.sleep(0.01)
        return False

    def test_model_command_runs_unprivileged_and_cannot_read_control_state(self) -> None:
        result = self.home / "result.json"
        source = (
            "import os,json; "
            f"p={str(self.guard.root / 'state.json')!r}; "
            "readable=os.access(p,os.R_OK); "
            f"open({str(result)!r},'w').write(json.dumps([os.geteuid(),readable,os.environ['HOME']]))"
        )
        self.assertEqual(self.execute(source), 0)
        self.assertEqual(json.loads(result.read_text()), [28000, False, str(self.home)])

    def test_deactivation_stops_a_daemon_that_escaped_its_process_group(self) -> None:
        pid = self.daemon()
        self.guard.deactivate(self.token)
        self.assert_stopped(pid)
        with self.assertRaises(RuntimeError):
            self.execute("raise SystemExit(0)")

    def test_deactivation_stops_threads_even_after_their_group_leader_exited(self) -> None:
        counter = self.home / "thread.counter"
        child = (
            "import ctypes,threading,time,os; from pathlib import Path; "
            f"p=Path({str(counter)!r}); q=p.with_suffix('.next'); "
            "\ndef work():\n"
            " while True:\n  q.write_text(str(time.monotonic())); "
            "os.replace(q,p); time.sleep(.01)\n"
            "threading.Thread(target=work).start(); ctypes.CDLL(None).pthread_exit(None)"
        )
        source = (
            "import subprocess,sys,time; from pathlib import Path; "
            f"subprocess.Popen([sys.executable,'-c',{child!r}],start_new_session=True, "
            "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
            f"p=Path({str(counter)!r}); "
            "\nfor _ in range(100):\n if p.exists(): break\n time.sleep(.01)"
        )
        self.assertEqual(self.execute(source), 0)
        self.assertTrue(counter.exists())
        self.guard.deactivate(self.token)
        final = counter.read_text()
        time.sleep(0.15)
        self.assertEqual(counter.read_text(), final)

    def test_new_epoch_drains_old_processes_before_accepting_a_new_writer(self) -> None:
        pid = self.daemon()
        next_token = Token(2, "computer_lease_" + uuid4().hex)
        self.guard.activate(next_token, time.time() + 30)
        self.assert_stopped(pid)
        with self.assertRaises(RuntimeError):
            self.guard.deactivate(self.token)
        with self.assertRaises(RuntimeError):
            self.guard.renew(self.token, time.time() + 30)
        with self.assertRaises(RuntimeError):
            self.execute("raise SystemExit(0)")
        self.assertEqual(self.execute("raise SystemExit(0)", token=next_token), 0)

    def test_expiry_watchdog_stops_daemons_after_command_has_already_exited(self) -> None:
        token = Token(2, "computer_lease_" + uuid4().hex)
        self.guard.activate(token, time.time() + 0.7)
        self.token = token
        pid = self.daemon()
        state = json.loads((self.guard.root / "state.json").read_text())
        self.assertTrue(self.wait_exited(state["namespace"]["pid"], 4))
        self.guard.deactivate(token)
        self.assert_stopped(pid)
        with self.assertRaises(RuntimeError):
            self.guard.renew(token, time.time() + 30)

    def test_lost_namespace_cannot_silently_resume_an_execution(self) -> None:
        state = json.loads((self.guard.root / "state.json").read_text())
        os.kill(state["namespace"]["pid"], signal.SIGKILL)
        self.assertTrue(self.wait_exited(state["namespace"]["pid"], 2))
        with self.assertRaises(RuntimeError):
            self.guard.renew(self.token, time.time() + 35)

    def test_closed_epoch_cannot_be_reactivated_by_delayed_control_request(self) -> None:
        self.guard.deactivate(self.token)
        with self.assertRaises(RuntimeError):
            self.guard.activate(self.token, time.time() + 30)

    def test_namespace_identity_records_the_actual_kernel_boot(self) -> None:
        state = json.loads((self.guard.root / "state.json").read_text())
        actual = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        self.assertEqual(state["namespace"]["boot_id"], actual)

    def test_live_legacy_metadata_is_fenced_without_stopping_an_unverified_namespace(self) -> None:
        state = json.loads((self.guard.root / "state.json").read_text())
        original = state["namespace"].pop("boot_id")
        self.guard.replace_state(state)
        try:
            with self.assertRaisesRegex(RuntimeError, "physical recovery"):
                self.execute("raise SystemExit(0)")
            self.assertFalse(self.wait_exited(state["namespace"]["pid"], 0.05))
        finally:
            state["namespace"]["boot_id"] = original
            self.guard.replace_state(state)

    def test_one_use_credentials_are_removed_from_private_spool(self) -> None:
        path = self.guard.root / "spool" / (uuid4().hex + ".json")
        path.write_text(json.dumps({"environment": {"TEST_ONLY_SECRET": "private-fixture"}}))
        path.chmod(0o600)
        value = self.guard.consume(path)
        self.assertEqual(value["environment"]["TEST_ONLY_SECRET"], "private-fixture")
        self.assertFalse(path.exists())
        with self.assertRaises(ValueError):
            self.guard.consume(self.home / "request.json")

    def test_timeout_drains_old_writers_and_allows_a_valid_lease_to_continue(self) -> None:
        self.assertEqual(self.execute("import time; time.sleep(60)", timeout=0.2), 124)
        self.assertEqual(self.execute("raise SystemExit(0)"), 0)


if __name__ == "__main__":
    unittest.main()
