from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
TEST_DATA_DIR = ROOT_DIR / "tests" / "data"


def _run(command: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    pythonpath = str(ROOT_DIR / "src")
    env["PYTHONPATH"] = pythonpath if "PYTHONPATH" not in env else f"{pythonpath}:{env['PYTHONPATH']}"
    return subprocess.run(
        command,
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _run_interactive(
    command: list[str], *, stdin_text: str, timeout: int = 30
) -> tuple[str, int]:
    """Run a command with stdin input; kill after timeout if needed. Returns (stdout, returncode)."""
    import os
    import signal

    env = os.environ.copy()
    pythonpath = str(ROOT_DIR / "src")
    env["PYTHONPATH"] = pythonpath if "PYTHONPATH" not in env else f"{pythonpath}:{env['PYTHONPATH']}"
    proc = subprocess.Popen(
        command,
        cwd=ROOT_DIR,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, _ = proc.communicate(input=stdin_text, timeout=timeout)
        return stdout, proc.returncode
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        stdout, _ = proc.communicate()
        return stdout, -1


def _pty_available() -> bool:
    import os
    import pty

    try:
        master_fd, slave_fd = pty.openpty()
    except OSError:
        return False
    os.close(master_fd)
    os.close(slave_fd)
    return True


def _dosemu_available() -> bool:
    return shutil.which("dosemu") is not None or shutil.which("dosemu2") is not None


class FidelityAuditTests(unittest.TestCase):
    def test_6502_exec_smoke(self) -> None:
        result = _run(["bash", "run/6502.sh", "--exec", "PRINT 2+2"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OSI 6502 BASIC VERSION 1.0 REV 3.2", result.stdout)
        self.assertIn(" 4 ", result.stdout)

    def test_6502_demo_batch(self) -> None:
        result = _run(["bash", "run/6502.sh", "--run", "--file", "demo/6502.bas"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("HELLO, WORLD", result.stdout)
        self.assertIn("INTEGER 14", result.stdout)
        self.assertIn("PI 3.14159", result.stdout)

    def test_6502_pi_probe(self) -> None:
        result = _run(["bash", "run/6502.sh", "--run", "--file", str(TEST_DATA_DIR / "6502_pi_probe.bas")])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("130  73  15  219", result.stdout)
        self.assertRegex(result.stdout, r"\n 0 \n 0 \n")

    def test_basic80_demo_batch(self) -> None:
        result = _run(["bash", "run/basic80.sh", "--run", "--file", "demo/basic80.bas"], timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("HELLO, WORLD", result.stdout)
        self.assertIn("INTEGER 14", result.stdout)
        self.assertIn("PI 3.141592979431152", result.stdout)

    def test_basic80_pi_probe(self) -> None:
        result = _run(["bash", "run/basic80.sh", "--run", "--file", str(TEST_DATA_DIR / "basic80_pi_probe.bas")])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("BASIC-85 Rev. 5.29", result.stdout)
        self.assertIn("194  104  33  162  218  15  73  130", result.stdout)
        self.assertIn("\n 0 \n", result.stdout)

    def test_nbasic_frac2_varptr_probe_returns_c6(self) -> None:
        result = _run(
            ["bash", "run/nbasic.sh", "--run", "--file", str(TEST_DATA_DIR / "nbasic_frac2_varptr_probe.bas")]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [" 3.141592653589793 ", "C6"],
        )

    def test_nbasic_demo_batch(self) -> None:
        result = _run(["bash", "run/nbasic.sh", "--run", "--file", "demo/nbasic.bas"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("HELLO, WORLD", result.stdout)
        self.assertIn("INTEGER 14", result.stdout)
        self.assertIn("PI 0", result.stdout)

    def test_nbasic_pi_probe(self) -> None:
        result = _run(["bash", "run/nbasic.sh", "--run", "--file", str(TEST_DATA_DIR / "nbasic_pi_probe.bas")])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("194  104  33  162  218  15  73  130", result.stdout)
        self.assertIn("\n 0 \n", result.stdout)

    def test_msx_demo_batch(self) -> None:
        if shutil.which("openmsx") is None:
            self.skipTest("openmsx is not installed")
        result = _run(["bash", "run/msxbasic.sh", "--run", "--file", "demo/msxbasic.bas"], timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            ["HELLO, WORLD", "INTEGER 14", "PI 3.1415926535898"],
        )

    def test_msx_interactive_smoke(self) -> None:
        if shutil.which("openmsx") is None:
            self.skipTest("openmsx is not installed")
        import os
        import signal
        import time

        env = os.environ.copy()
        pythonpath = str(ROOT_DIR / "src")
        env["PYTHONPATH"] = pythonpath if "PYTHONPATH" not in env else f"{pythonpath}:{env['PYTHONPATH']}"
        proc = subprocess.Popen(
            ["bash", "run/msxbasic.sh"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=ROOT_DIR,
            env=env,
            start_new_session=True,
        )
        assert proc.stdin is not None
        assert proc.stdout is not None
        output = bytearray()
        deadline = time.time() + 30
        while time.time() < deadline:
            chunk = proc.stdout.read1(4096)
            if chunk:
                output.extend(chunk)
                if b"MSX-BASIC ready" in output:
                    proc.stdin.write(b"\x04")
                    proc.stdin.flush()
                    break
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait()
        self.assertIn(b"MSX-BASIC ready", output)

    def test_msx_pi_probe(self) -> None:
        if shutil.which("openmsx") is None:
            self.skipTest("openmsx is not installed")
        result = _run(
            ["bash", "run/msxbasic.sh", "--run", "--file", str(TEST_DATA_DIR / "msxbasic_pi_probe.bas")],
            timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.strip().startswith("4131415926535898"), result.stdout)
        self.assertGreaterEqual(result.stdout.count("\n0"), 3, result.stdout)

    def test_basic80_exec_smoke(self) -> None:
        """basic80 interactive: AUTOEXEC launches MBASIC; feed commands via stdin."""
        stdout, _ = _run_interactive(
            ["bash", "run/basic80.sh"],
            stdin_text="PRINT 2+2\r\nSYSTEM\r\n",
            timeout=30,
        )
        self.assertIn("BASIC-85", stdout)
        self.assertIn(" 4 ", stdout)

    def test_gwbasic_exec_smoke(self) -> None:
        if not _dosemu_available():
            self.skipTest("dosemu not installed")
        script = "\n".join([
            "spawn bash run/gwbasic.sh",
            "set timeout 60",
            'expect "Ok"',
            r'send "PRINT \"ANS=\";2+2\r"',
            "expect {",
            '    "ANS= 4" { send "SYSTEM\\r"; expect eof }',
            "    timeout   { exit 1 }",
            "}",
        ])
        result = _run(["expect", "-c", script], timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout[-500:])

    def test_gwbasic_demo_batch(self) -> None:
        if not _dosemu_available():
            self.skipTest("dosemu not installed")
        result = _run(["bash", "run/gwbasic.sh", "--run", "--file", "demo/gwbasic.bas"], timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("HELLO, WORLD", result.stdout)
        self.assertIn("INTEGER 14", result.stdout)
        self.assertIn("PI", result.stdout)

    def test_gwbasic_pi_probe(self) -> None:
        if not _dosemu_available():
            self.skipTest("dosemu not installed")
        result = _run(["bash", "run/gwbasic.sh", "--run", "--file", str(TEST_DATA_DIR / "gwbasic_pi_probe.bas")], timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("194 104 33 162 218 15 73 130", result.stdout)
        self.assertIn(" 0", result.stdout)

    def _run_nbasic_interactive(self, commands: list[bytes]) -> bytes:
        """Helper: boot N-BASIC via pty, send commands sequentially, return output."""
        import os
        import pty
        import select
        import signal
        import time

        if not _pty_available():
            self.skipTest("no PTY devices available for N-BASIC interactive test")

        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            ["bash", "run/nbasic.sh"],
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            cwd=ROOT_DIR, start_new_session=True,
        )
        os.close(slave_fd)
        output = b""
        cmd_index = 0
        deadline = time.time() + 30
        while time.time() < deadline:
            r, _, _ = select.select([master_fd], [], [], 0.5)
            if r:
                try:
                    chunk = os.read(master_fd, 4096)
                    output += chunk
                    # Send next command when we see an "Ok" prompt
                    ok_count = output.count(b"Ok")
                    if cmd_index < len(commands) and ok_count > cmd_index:
                        time.sleep(0.2)
                        os.write(master_fd, commands[cmd_index])
                        cmd_index += 1
                    if cmd_index >= len(commands) and ok_count > cmd_index:
                        break
                except OSError:
                    break
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait()
        os.close(master_fd)
        return output

    def test_nbasic_interactive_smoke(self) -> None:
        """N-BASIC ROM interactive mode: pty → Ok banner → PRINT 4 → ' 4 '."""
        output = self._run_nbasic_interactive([b"PRINT 4\r"])
        self.assertIn(b"Ok", output)
        self.assertIn(b" 4 ", output)

    def test_nbasic_arithmetic_expression(self) -> None:
        """N-BASIC: PRINT 2+2 → ' 4 ' (operator tokenisation via ROM table)."""
        output = self._run_nbasic_interactive([b"PRINT 2+2\r"])
        self.assertIn(b" 4 ", output, f"Expected ' 4 ' in output, got: {output!r}")

    def test_qbasic_exec_smoke(self) -> None:
        if not _dosemu_available():
            self.skipTest("dosemu not installed")
        # Confirm the QBasic IDE starts, shows the Immediate window, and
        # accepts keyboard input.  Startup flow:
        #   ESC       – dismiss "Welcome to MS-DOS QBasic" dialog
        #   Tab+CR    – move to <Cancel> and dismiss QBASIC.HLP not-found dialog
        #               (Tab+CR is a no-op in the edit window when no dialog)
        #   F6        – switch focus to the Immediate window
        #   SYSTEM+CR – execute SYSTEM from the Immediate window to exit
        # F6 is sent as the VT100 escape sequence \x1b[17~.
        script = "\n".join([
            "spawn bash run/qbasic.sh",
            "set timeout 60",
            "expect {",
            '    "Immediate" {}',
            "    timeout     { exit 1 }",
            "}",
            "sleep 0.5",
            r'send "\x1b"',
            "sleep 0.5",
            r'send "\t\r"',
            "sleep 0.3",
            r'send "\x1b\[17~"',
            "sleep 0.3",
            r'send "SYSTEM\r"',
            "expect {",
            "    eof     { exit 0 }",
            "    timeout { exit 1 }",
            "}",
        ])
        result = _run(["expect", "-c", script], timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout[-300:])

    def test_qbasic_demo_batch(self) -> None:
        if not _dosemu_available():
            self.skipTest("dosemu not installed")
        result = _run(["bash", "run/qbasic.sh", "--run", "--file", "demo/qbasic.bas"], timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("HELLO, WORLD", result.stdout)
        self.assertIn("INTEGER 14", result.stdout)
        self.assertIn("PI", result.stdout)

    def test_qbasic_pi_probe(self) -> None:
        if not _dosemu_available():
            self.skipTest("dosemu not installed")
        result = _run(["bash", "run/qbasic.sh", "--run", "--file", str(TEST_DATA_DIR / "qbasic_pi_probe.bas")], timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("24 45 68 84 251 33 9 64", result.stdout)
        self.assertIn("\n0\n0", result.stdout)
