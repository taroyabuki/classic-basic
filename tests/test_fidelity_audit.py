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


def _timeout_available() -> bool:
    return shutil.which("timeout") is not None


def _mame_available() -> str | None:
    for candidate in (shutil.which("mame"), "/usr/games/mame"):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def _run_mame_verifyroms(*, rompath: Path, driver: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    mame = _mame_available()
    if mame is None:
        raise FileNotFoundError("mame executable is not installed")
    return _run([mame, "-rompath", str(rompath), "-verifyroms", driver], timeout=timeout)



def _grantsbasic_runtime_available() -> bool:
    return (ROOT_DIR / "downloads" / "grants-basic" / "rom.bin").exists()


def _n88basic_runtime_available() -> bool:
    return (ROOT_DIR / "downloads" / "n88basic" / "roms").is_dir() and (
        ROOT_DIR / "vendor" / "quasi88" / "quasi88.sdl2"
    ).is_file()


def _nbasic_runtime_available() -> bool:
    return (ROOT_DIR / "downloads" / "pc8001" / "N80_11.rom").is_file()


def _fm7basic_runtime_available() -> bool:
    return _mame_available() is not None and (ROOT_DIR / "downloads" / "fm7basic" / "mame-roms" / "fm77av").is_dir()


def _fm11basic_runtime_available() -> bool:
    return shutil.which("wine") is not None and (
        ROOT_DIR / "downloads" / "fm11basic" / "staged" / "emulator-x86" / "Fm11.exe"
    ).is_file() and (
        ROOT_DIR / "downloads" / "fm11basic" / "staged" / "disks" / "fb2hd00.img"
    ).is_file() and (
        ROOT_DIR / "downloads" / "fm11basic" / "staged" / "roms"
    ).is_dir()


_DEMO_BATCH_EXCLUDED = {"fm7basic"}
_DEMO_BATCH_TIMEOUT_OVERRIDES = {
    "6502": 60,
    "basic80": 90,
    "grantsbasic": 90,
    "msxbasic": 90,
    "gwbasic": 90,
    "qbasic": 90,
}
_DEMO_BATCH_AVAILABILITY_OVERRIDES = {
    "grantsbasic": _grantsbasic_runtime_available,
    "gwbasic": _dosemu_available,
    "msxbasic": lambda: shutil.which("openmsx") is not None,
    "nbasic": _nbasic_runtime_available,
    "n88basic": _n88basic_runtime_available,
    "qbasic": _dosemu_available,
}


def _demo_batch_cases() -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    for runner in sorted((ROOT_DIR / "run").glob("*.sh")):
        name = runner.stem
        demo = ROOT_DIR / "demo" / f"{name}.bas"
        if name in _DEMO_BATCH_EXCLUDED or not demo.is_file():
            continue
        cases.append(
            {
                "name": name,
                "command": ["bash", str(runner.relative_to(ROOT_DIR)), "--run", "--file", str(demo.relative_to(ROOT_DIR))],
                "timeout": _DEMO_BATCH_TIMEOUT_OVERRIDES.get(name, 90),
                "available": _DEMO_BATCH_AVAILABILITY_OVERRIDES.get(name, lambda: True),
                "snippets": ("HELLO, WORLD", "INTEGER 14", "PI"),
            }
        )
    return cases


def _infinite_loop_batch_cases() -> list[dict[str, object]]:
    test_program = TEST_DATA_DIR / "test.bas"
    return [
        {"name": "6502", "command": ["timeout", "3", "bash", "run/6502.sh", "--run", "--file", str(test_program)], "available": lambda: True},
        {"name": "basic80", "command": ["timeout", "3", "bash", "run/basic80.sh", "--run", "--file", str(test_program)], "available": lambda: True},
        {"name": "grantsbasic", "command": ["timeout", "3", "bash", "run/grantsbasic.sh", "--run", "--file", str(test_program)], "available": _grantsbasic_runtime_available},
        {"name": "nbasic", "command": ["timeout", "3", "bash", "run/nbasic.sh", "--run", "--file", str(test_program)], "available": _nbasic_runtime_available},
        {"name": "n88basic", "command": ["timeout", "3", "bash", "run/n88basic.sh", "--run", "--file", str(test_program)], "available": _n88basic_runtime_available},
        {"name": "msxbasic", "command": ["timeout", "3", "bash", "run/msxbasic.sh", "--run", "--file", str(test_program)], "available": lambda: shutil.which("openmsx") is not None},
        {"name": "gwbasic", "command": ["timeout", "3", "bash", "run/gwbasic.sh", "--run", "--file", str(test_program)], "available": _dosemu_available},
        {"name": "qbasic", "command": ["timeout", "3", "bash", "run/qbasic.sh", "--run", "--file", str(test_program)], "available": _dosemu_available},
        {"name": "fm7basic", "command": ["timeout", "3", "bash", "run/fm7basic.sh", "--run", "--file", str(test_program)], "available": _fm7basic_runtime_available},
        {"name": "fm11basic", "command": ["timeout", "3", "bash", "run/fm11basic.sh", "--run", "--file", str(test_program)], "available": _fm11basic_runtime_available},
    ]


class FidelityAuditTests(unittest.TestCase):
    def test_fm7basic_fm77av_romset_matches_local_mame(self) -> None:
        mame = _mame_available()
        if mame is None:
            self.skipTest("mame is not installed")
        rompath = ROOT_DIR / "downloads" / "fm7basic" / "mame-roms"
        romset_dir = rompath / "fm77av"
        if not romset_dir.is_dir():
            self.skipTest("FM-77AV ROM set is not prepared")

        result = _run_mame_verifyroms(rompath=rompath, driver="fm77av")
        self.assertEqual(
            result.returncode,
            0,
            f"{mame} rejected the prepared fm77av ROM set.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    def test_demo_batch_contract_across_runtimes(self) -> None:
        ran_any = False
        for case in _demo_batch_cases():
            if not case["available"]():
                continue
            ran_any = True
            with self.subTest(runtime=case["name"]):
                result = _run(case["command"], timeout=case["timeout"])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(result.stdout.strip(), "expected non-empty batch output")
                for snippet in case["snippets"]:
                    self.assertIn(snippet, result.stdout)
        if not ran_any:
            self.skipTest("no demo batch runtimes are installed")

    def test_infinite_loop_batch_does_not_exit_cleanly(self) -> None:
        if not _timeout_available():
            self.skipTest("timeout command is not installed")
        ran_any = False
        for case in _infinite_loop_batch_cases():
            if not case["available"]():
                continue
            ran_any = True
            with self.subTest(runtime=case["name"]):
                result = _run(case["command"], timeout=10)
                self.assertEqual(result.returncode, 124, result.stdout + result.stderr)
        if not ran_any:
            self.skipTest("no infinite-loop batch runtimes are installed")

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

    def test_6502_demo_batch_exits_even_when_stdin_is_a_tty(self) -> None:
        import pty
        import select
        import signal
        import time

        if not _pty_available():
            self.skipTest("no PTY devices available for 6502 TTY batch test")

        env = os.environ.copy()
        pythonpath = str(ROOT_DIR / "src")
        env["PYTHONPATH"] = pythonpath if "PYTHONPATH" not in env else f"{pythonpath}:{env['PYTHONPATH']}"

        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            ["bash", "run/6502.sh", "--run", "--file", "demo/6502.bas"],
            cwd=ROOT_DIR,
            env=env,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            start_new_session=True,
        )
        os.close(slave_fd)

        output = bytearray()
        deadline = time.time() + 20
        try:
            while time.time() < deadline:
                if proc.poll() is not None:
                    break
                readable, _, _ = select.select([master_fd], [], [], 0.5)
                if readable:
                    try:
                        chunk = os.read(master_fd, 4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    output.extend(chunk)
            if proc.poll() is None:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=5)
                self.fail("6502 batch mode stayed interactive when stdin was a TTY")
            proc.wait(timeout=5)
        finally:
            os.close(master_fd)

        stdout = output.decode("latin-1", errors="replace")
        self.assertEqual(proc.returncode, 0, stdout)
        self.assertIn("HELLO, WORLD", stdout)
        self.assertIn("INTEGER 14", stdout)
        self.assertIn("PI 3.14159", stdout)

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


    def test_grantsbasic_demo_batch(self) -> None:
        if not _grantsbasic_runtime_available():
            self.skipTest("grantsbasic runtime is not installed")
        result = _run(["bash", "run/grantsbasic.sh", "--run", "--file", "demo/grantsbasic.bas"], timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Z80 BASIC Ver 4.7b", result.stdout)
        self.assertIn("HELLO, WORLD", result.stdout)
        self.assertIn("INTEGER 14", result.stdout)
        self.assertIn("PI 3.14159", result.stdout)

    def test_nbasic_frac2_varptr_probe_returns_c6(self) -> None:
        if not _nbasic_runtime_available():
            self.skipTest("N-BASIC runtime is not available")
        result = _run(
            ["bash", "run/nbasic.sh", "--run", "--file", str(TEST_DATA_DIR / "nbasic_frac2_varptr_probe.bas")]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [" 3.141592653589793 ", "C6"],
        )

    def test_nbasic_demo_batch(self) -> None:
        if not _nbasic_runtime_available():
            self.skipTest("N-BASIC runtime is not available")
        result = _run(["bash", "run/nbasic.sh", "--run", "--file", "demo/nbasic.bas"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("HELLO, WORLD", result.stdout)
        self.assertIn("INTEGER 14", result.stdout)
        self.assertIn("PI 3.141592979431152", result.stdout)

    def test_nbasic_pi_probe(self) -> None:
        if not _nbasic_runtime_available():
            self.skipTest("N-BASIC runtime is not available")
        result = _run(["bash", "run/nbasic.sh", "--run", "--file", str(TEST_DATA_DIR / "nbasic_pi_probe.bas")])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("194  104  33  162  218  15  73  130", result.stdout)
        self.assertIn("\n 0 \n", result.stdout)

    def test_n88basic_long_decimal_literals_batch(self) -> None:
        if not _n88basic_runtime_available():
            self.skipTest("N88-BASIC runtime is not available")
        result = _run(
            ["bash", "run/n88basic.sh", "--run", "--file", str(TEST_DATA_DIR / "n88basic_long_decimal_probe.bas")],
            timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("3.14159265358979323#", result.stdout)
        self.assertIn("3.14159265358979323846#", result.stdout)

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
        self.assertTrue(result.stdout.strip(), "expected some batch output from the long MSX probe")

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
        if not _nbasic_runtime_available():
            self.skipTest("N-BASIC runtime is not available")

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
        # Confirm the text shell accepts a direct command, runs it through the
        # real QBasic batch backend, and exits cleanly on Ctrl-D.
        script = "\n".join([
            "spawn bash run/qbasic.sh",
            "set timeout 60",
            "expect {",
            '    "QBasic> " {}',
            "    timeout     { exit 1 }",
            "}",
            'send "PRINT 2+2\\r"',
            "expect {",
            '    "4" {',
            r'        send "\004"',
            "        expect eof",
            "        exit 0",
            "    }",
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
