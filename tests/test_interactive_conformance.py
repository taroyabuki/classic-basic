from __future__ import annotations

import os
import pty
import select
import shutil
import signal
import subprocess
import time
import unittest
from pathlib import Path
from typing import Callable


ROOT_DIR = Path(__file__).resolve().parents[1]
BASICS = (
    "6502",
    "basic80",
    "nbasic",
    "n88basic",
    "fm7basic",
    "fm11basic",
    "gwbasic",
    "msxbasic",
    "qbasic",
    "grantsbasic",
)


CONFORMANCE_MATRIX: dict[str, dict[str, str]] = {
    "ctrl_d_exit": {
        "6502": "pass",
        "basic80": "pass",
        "nbasic": "pass",
        "n88basic": "pass",
        "fm7basic": "pass",
        "fm11basic": "pass",
        "gwbasic": "pass",
        "msxbasic": "pass",
        "qbasic": "pass",
        "grantsbasic": "pass",
    },
    "faithful_ascii_input": {
        "6502": "pass",
        "basic80": "pass",
        "nbasic": "pass",
        "n88basic": "pass",
        "fm7basic": "pass",
        "fm11basic": "pass",
        "gwbasic": "pass",
        "msxbasic": "pass",
        "qbasic": "pass",
        "grantsbasic": "pass",
    },
    "output_settles_once": {
        "6502": "n/a",
        "basic80": "pass",
        "nbasic": "pass",
        "n88basic": "pass",
        "fm7basic": "pass",
        "fm11basic": "pass",
        "gwbasic": "pass",
        "msxbasic": "pass",
        "qbasic": "pass",
        "grantsbasic": "pass",
    },
    "no_duplicate_interactive_output": {
        "6502": "n/a",
        "basic80": "pass",
        "nbasic": "pass",
        "n88basic": "pass",
        "fm7basic": "pass",
        "fm11basic": "pass",
        "gwbasic": "pass",
        "msxbasic": "pass",
        "qbasic": "pass",
        "grantsbasic": "pass",
    },
    "low_input_latency": {
        "6502": "pass",
        "basic80": "pass",
        "nbasic": "pass",
        "n88basic": "pass",
        "fm7basic": "pass",
        "fm11basic": "pass",
        "gwbasic": "pass",
        "msxbasic": "pass",
        "qbasic": "pass",
        "grantsbasic": "pass",
    },
    "no_orphan_process": {
        "6502": "n/a",
        "basic80": "pass",
        "nbasic": "n/a",
        "n88basic": "pass",
        "fm7basic": "pass",
        "fm11basic": "pass",
        "gwbasic": "pass",
        "msxbasic": "pass",
        "qbasic": "pass",
        "grantsbasic": "n/a",
    },
    "text_only_interactive_shell": {
        "6502": "n/a",
        "basic80": "n/a",
        "nbasic": "n/a",
        "n88basic": "n/a",
        "fm7basic": "n/a",
        "fm11basic": "n/a",
        "gwbasic": "pass",
        "msxbasic": "n/a",
        "qbasic": "pass",
        "grantsbasic": "n/a",
    },
    "interactive_eval": {
        "6502": "pass",
        "basic80": "pass",
        "nbasic": "pass",
        "n88basic": "pass",
        "fm7basic": "pass",
        "fm11basic": "pass",
        "gwbasic": "pass",
        "msxbasic": "pass",
        "qbasic": "pass",
        "grantsbasic": "pass",
    },
    "interactive_repeated_eval": {
        "6502": "pass",
        "basic80": "pass",
        "nbasic": "pass",
        "n88basic": "pass",
        "fm7basic": "pass",
        "fm11basic": "pass",
        "gwbasic": "pass",
        "msxbasic": "pass",
        "qbasic": "pass",
        "grantsbasic": "pass",
    },
    "interactive_backspace_rendering": {
        "6502": "n/a",
        "basic80": "n/a",
        "nbasic": "n/a",
        "n88basic": "n/a",
        "fm7basic": "n/a",
        "fm11basic": "n/a",
        "gwbasic": "n/a",
        "msxbasic": "n/a",
        "qbasic": "n/a",
        "grantsbasic": "pass",
    },
}


def _runtime_env() -> dict[str, str]:
    env = os.environ.copy()
    pythonpath = str(ROOT_DIR / "src")
    env["PYTHONPATH"] = pythonpath if "PYTHONPATH" not in env else f"{pythonpath}:{env['PYTHONPATH']}"
    return env


def _pty_available() -> bool:
    try:
        master_fd, slave_fd = pty.openpty()
    except OSError:
        return False
    os.close(master_fd)
    os.close(slave_fd)
    return True


def _dosemu_available() -> bool:
    return shutil.which("dosemu") is not None or shutil.which("dosemu2") is not None


def _mame_available() -> bool:
    return shutil.which("mame") is not None or Path("/usr/games/mame").is_file()


def _runtime_file(*parts: str) -> bool:
    return (ROOT_DIR.joinpath(*parts)).is_file()


def _runtime_dir(*parts: str) -> bool:
    return (ROOT_DIR.joinpath(*parts)).is_dir()


def _run_pty_interactive(
    command: list[str],
    *,
    ready_needle: bytes,
    input_bytes: bytes,
    success_needles: tuple[bytes, ...],
    exit_bytes: bytes = b"\x04",
    timeout: float = 30.0,
) -> tuple[bytes, int]:
    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(
        command,
        cwd=ROOT_DIR,
        env=_runtime_env(),
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        start_new_session=True,
    )
    os.close(slave_fd)

    try:
        output = bytearray()
        output.extend(_read_until(master_fd, ready_needle, timeout=timeout))
        os.write(master_fd, input_bytes)
        for needle in success_needles:
            output.extend(_read_until(master_fd, needle, timeout=timeout))
        if exit_bytes:
            os.write(master_fd, exit_bytes)
            proc.wait(timeout=10)
        return bytes(output), proc.returncode or 0
    finally:
        os.close(master_fd)
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=10)


def _run_pipe_interactive(
    command: list[str],
    *,
    input_bytes: bytes,
    timeout: float = 60.0,
) -> tuple[bytes, int]:
    result = subprocess.run(
        command,
        cwd=ROOT_DIR,
        env=_runtime_env(),
        input=input_bytes,
        capture_output=True,
        timeout=timeout,
    )
    return result.stdout + result.stderr, result.returncode


def _run_pty_interactive_steps(
    command: list[str],
    *,
    ready_needle: bytes,
    steps: tuple[tuple[bytes, tuple[bytes, ...]], ...],
    exit_bytes: bytes = b"\x04",
    timeout: float = 30.0,
) -> tuple[bytes, int]:
    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(
        command,
        cwd=ROOT_DIR,
        env=_runtime_env(),
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        start_new_session=True,
    )
    os.close(slave_fd)

    try:
        output = bytearray()
        output.extend(_read_until(master_fd, ready_needle, timeout=timeout))
        for input_bytes, success_needles in steps:
            os.write(master_fd, input_bytes)
            for needle in success_needles:
                output.extend(_read_until(master_fd, needle, timeout=timeout))
        if exit_bytes:
            os.write(master_fd, exit_bytes)
            proc.wait(timeout=10)
        return bytes(output), proc.returncode or 0
    finally:
        os.close(master_fd)
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=10)


def _read_until(master_fd: int, needle: bytes, *, timeout: float) -> bytes:
    deadline = time.time() + timeout
    output = bytearray()
    while time.time() < deadline:
        ready, _, _ = select.select([master_fd], [], [], 0.1)
        if not ready:
            continue
        try:
            chunk = os.read(master_fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        output.extend(chunk)
        if needle in output:
            return bytes(output)
    raise AssertionError(f"timed out waiting for {needle!r}; got tail {bytes(output[-200:])!r}")


def _6502_runtime_available() -> bool:
    return _pty_available() and _runtime_file("third_party", "msbasic", "osi.bin")


def _basic80_runtime_available() -> bool:
    return _pty_available() and _runtime_file("downloads", "MBASIC.COM") and _runtime_file(
        "third_party",
        "RunCPM",
        "RunCPM",
        "RunCPM",
    )


def _nbasic_runtime_available() -> bool:
    return _pty_available() and _runtime_file("downloads", "pc8001", "N80_11.rom")


def _n88basic_runtime_available() -> bool:
    return (
        _pty_available()
        and _runtime_dir("downloads", "n88basic", "roms")
        and _runtime_file("vendor", "quasi88", "quasi88.sdl2")
    )


def _fm7basic_runtime_available() -> bool:
    return bool(os.environ.get("DISPLAY")) and _mame_available() and _runtime_dir(
        "downloads",
        "fm7basic",
        "mame-roms",
        "fm77av",
    )


def _fm11basic_runtime_available() -> bool:
    return all(
        (
            shutil.which("Xvfb"),
            shutil.which("xdpyinfo"),
            shutil.which("wine"),
            shutil.which("xdotool"),
            shutil.which("xclip"),
        )
    ) and _runtime_file("downloads", "fm11basic", "staged", "emulator-x86", "Fm11.exe") and _runtime_file(
        "downloads",
        "fm11basic",
        "staged",
        "disks",
        "fb2hd00.img",
    )


def _gwbasic_runtime_available() -> bool:
    return _pty_available() and _dosemu_available() and _runtime_file("downloads", "gwbasic", "GWBASIC.EXE")


def _msxbasic_runtime_available() -> bool:
    return _pty_available() and shutil.which("openmsx") is not None and _runtime_file(
        "downloads",
        "msx",
        "msx1-basic-bios.rom",
    )


def _qbasic_runtime_available() -> bool:
    return _pty_available() and _dosemu_available() and _runtime_file("downloads", "qbasic", "QBASIC.EXE")


def _grantsbasic_runtime_available() -> bool:
    return _pty_available() and _runtime_file("downloads", "grants-basic", "rom.bin")


def _run_6502_interactive_eval() -> tuple[bytes, int]:
    return _run_pty_interactive(
        ["bash", "run/6502.sh"],
        ready_needle=b"OK",
        input_bytes=b"PRINT 2+2\r",
        success_needles=(b" 4 ",),
    )


def _run_basic80_interactive_eval() -> tuple[bytes, int]:
    return _run_pty_interactive(
        ["bash", "run/basic80.sh"],
        ready_needle=b"Ok",
        input_bytes=b"PRINT 2+2\r",
        success_needles=(b" 4 ",),
    )


def _run_nbasic_interactive_eval() -> tuple[bytes, int]:
    return _run_pty_interactive(
        ["bash", "run/nbasic.sh"],
        ready_needle=b"Ok",
        input_bytes=b"PRINT 2+2\r",
        success_needles=(b" 4 ",),
    )


def _run_n88basic_interactive_eval() -> tuple[bytes, int]:
    return _run_pty_interactive(
        ["bash", "run/n88basic.sh"],
        ready_needle=b"Ok",
        input_bytes=b"PRINT 2+2\r",
        success_needles=(b"4",),
    )


def _run_fm7basic_interactive_eval() -> tuple[bytes, int]:
    return _run_pipe_interactive(
        ["bash", "run/fm7basic.sh"],
        input_bytes=b"PRINT 2+2\n",
        timeout=90.0,
    )


def _run_fm11basic_interactive_eval() -> tuple[bytes, int]:
    return _run_pipe_interactive(
        ["bash", "run/fm11basic.sh"],
        input_bytes=b"PRINT 2+2\n",
        timeout=90.0,
    )


def _run_gwbasic_interactive_eval() -> tuple[bytes, int]:
    return _run_pty_interactive(
        ["bash", "run/gwbasic.sh"],
        ready_needle=b"Ctrl-D exits.",
        input_bytes=b"PRINT 2+2\r",
        success_needles=(b"4",),
    )


def _run_msxbasic_interactive_eval() -> tuple[bytes, int]:
    return _run_pty_interactive(
        ["bash", "run/msxbasic.sh"],
        ready_needle=b"MSX-BASIC ready",
        input_bytes=b"PRINT 2+2\r",
        success_needles=(b"4",),
    )


def _run_qbasic_interactive_eval() -> tuple[bytes, int]:
    return _run_pty_interactive(
        ["bash", "run/qbasic.sh"],
        ready_needle=b"QBasic> ",
        input_bytes=b"PRINT 2+2\r",
        success_needles=(b"4",),
    )


def _run_grantsbasic_interactive_eval() -> tuple[bytes, int]:
    return _run_pty_interactive(
        ["bash", "run/grantsbasic.sh"],
        ready_needle=b"Ok",
        input_bytes=b"PRINT 2+2\r",
        success_needles=(b" 4",),
    )


def _run_6502_interactive_repeated_eval() -> tuple[bytes, int]:
    return _run_pty_interactive_steps(
        ["bash", "run/6502.sh"],
        ready_needle=b"OK",
        steps=(
            (b"PRINT 2+2\r", (b" 4 ",)),
            (b"PRINT 2-3\r", (b"-1",)),
        ),
    )


def _run_basic80_interactive_repeated_eval() -> tuple[bytes, int]:
    return _run_pty_interactive_steps(
        ["bash", "run/basic80.sh"],
        ready_needle=b"Ok",
        steps=(
            (b"PRINT 2+2\r", (b" 4 ",)),
            (b"PRINT 2-3\r", (b"-1",)),
        ),
    )


def _run_nbasic_interactive_repeated_eval() -> tuple[bytes, int]:
    return _run_pty_interactive_steps(
        ["bash", "run/nbasic.sh"],
        ready_needle=b"Ok",
        steps=(
            (b"PRINT 2+2\r", (b" 4 ",)),
            (b"PRINT 2-3\r", (b"-1",)),
        ),
    )


def _run_n88basic_interactive_repeated_eval() -> tuple[bytes, int]:
    return _run_pty_interactive_steps(
        ["bash", "run/n88basic.sh"],
        ready_needle=b"Ok",
        steps=(
            (b"PRINT 2+2\r", (b"4",)),
            (b"PRINT 2-3\r", (b"-1",)),
        ),
    )


def _run_fm7basic_interactive_repeated_eval() -> tuple[bytes, int]:
    return _run_pipe_interactive(
        ["bash", "run/fm7basic.sh"],
        input_bytes=b"PRINT 2+2\nPRINT 2-3\n",
        timeout=90.0,
    )


def _run_fm11basic_interactive_repeated_eval() -> tuple[bytes, int]:
    return _run_pipe_interactive(
        ["bash", "run/fm11basic.sh"],
        input_bytes=b"PRINT 2+2\nPRINT 2-3\n",
        timeout=90.0,
    )


def _run_gwbasic_interactive_repeated_eval() -> tuple[bytes, int]:
    return _run_pty_interactive_steps(
        ["bash", "run/gwbasic.sh"],
        ready_needle=b"Ctrl-D exits.",
        steps=(
            (b"PRINT 2+2\r", (b"4",)),
            (b"PRINT 2-3\r", (b"-1",)),
        ),
    )


def _run_msxbasic_interactive_repeated_eval() -> tuple[bytes, int]:
    return _run_pty_interactive_steps(
        ["bash", "run/msxbasic.sh"],
        ready_needle=b"MSX-BASIC ready",
        steps=(
            (b"PRINT 2+2\r", (b"4",)),
            (b"PRINT 2-3\r", (b"-1",)),
        ),
    )


def _run_qbasic_interactive_repeated_eval() -> tuple[bytes, int]:
    return _run_pty_interactive_steps(
        ["bash", "run/qbasic.sh"],
        ready_needle=b"QBasic> ",
        steps=(
            (b"PRINT 2+2\r", (b"4",)),
            (b"PRINT 2-3\r", (b"-1",)),
        ),
    )


def _run_grantsbasic_interactive_repeated_eval() -> tuple[bytes, int]:
    return _run_pty_interactive_steps(
        ["bash", "run/grantsbasic.sh"],
        ready_needle=b"Ok",
        steps=(
            (b"PRINT 2+2\r", (b" 4",)),
            (b"PRINT 2-3\r", (b"-1",)),
        ),
    )


def _run_grantsbasic_backspace_render() -> tuple[bytes, int]:
    return _run_pty_interactive(
        ["bash", "run/grantsbasic.sh"],
        ready_needle=b"Ok",
        input_bytes=b"AB\x7f",
        success_needles=(b"AB\x08 \x08",),
    )


INTERACTIVE_EVAL_CASES: dict[str, dict[str, object]] = {
    "6502": {
        "available": _6502_runtime_available,
        "runner": _run_6502_interactive_eval,
        "expected": (b"OSI 6502 BASIC", b" 4 "),
    },
    "basic80": {
        "available": _basic80_runtime_available,
        "runner": _run_basic80_interactive_eval,
        "expected": (b"BASIC-85", b" 4 "),
    },
    "nbasic": {
        "available": _nbasic_runtime_available,
        "runner": _run_nbasic_interactive_eval,
        "expected": (b"Ok", b" 4 "),
    },
    "n88basic": {
        "available": _n88basic_runtime_available,
        "runner": _run_n88basic_interactive_eval,
        "expected": (b"NEC N-88 BASIC", b"4"),
    },
    "fm7basic": {
        "available": _fm7basic_runtime_available,
        "runner": _run_fm7basic_interactive_eval,
        "expected": (b"READY", b"4"),
    },
    "fm11basic": {
        "available": _fm11basic_runtime_available,
        "runner": _run_fm11basic_interactive_eval,
        "expected": (b"4",),
    },
    "gwbasic": {
        "available": _gwbasic_runtime_available,
        "runner": _run_gwbasic_interactive_eval,
        "expected": (b"GW-BASIC text shell", b"4"),
    },
    "msxbasic": {
        "available": _msxbasic_runtime_available,
        "runner": _run_msxbasic_interactive_eval,
        "expected": (b"MSX-BASIC ready", b"4"),
    },
    "qbasic": {
        "available": _qbasic_runtime_available,
        "runner": _run_qbasic_interactive_eval,
        "expected": (b"QBasic", b"4"),
    },
    "grantsbasic": {
        "available": _grantsbasic_runtime_available,
        "runner": _run_grantsbasic_interactive_eval,
        "expected": (b"Z80 BASIC Ver 4.7b", b" 4"),
    },
}


INTERACTIVE_REPEATED_EVAL_CASES: dict[str, dict[str, object]] = {
    "6502": {
        "available": _6502_runtime_available,
        "runner": _run_6502_interactive_repeated_eval,
        "expected": (b"OSI 6502 BASIC", b" 4 ", b"-1"),
    },
    "basic80": {
        "available": _basic80_runtime_available,
        "runner": _run_basic80_interactive_repeated_eval,
        "expected": (b"BASIC-85", b" 4 ", b"-1"),
    },
    "nbasic": {
        "available": _nbasic_runtime_available,
        "runner": _run_nbasic_interactive_repeated_eval,
        "expected": (b"Ok", b" 4 ", b"-1"),
    },
    "n88basic": {
        "available": _n88basic_runtime_available,
        "runner": _run_n88basic_interactive_repeated_eval,
        "expected": (b"NEC N-88 BASIC", b"4", b"-1"),
    },
    "fm7basic": {
        "available": _fm7basic_runtime_available,
        "runner": _run_fm7basic_interactive_repeated_eval,
        "expected": (b"READY", b"4", b"-1"),
    },
    "fm11basic": {
        "available": _fm11basic_runtime_available,
        "runner": _run_fm11basic_interactive_repeated_eval,
        "expected": (b"4", b"-1"),
    },
    "gwbasic": {
        "available": _gwbasic_runtime_available,
        "runner": _run_gwbasic_interactive_repeated_eval,
        "expected": (b"GW-BASIC text shell", b"4", b"-1"),
    },
    "msxbasic": {
        "available": _msxbasic_runtime_available,
        "runner": _run_msxbasic_interactive_repeated_eval,
        "expected": (b"MSX-BASIC ready", b"4", b"-1"),
    },
    "qbasic": {
        "available": _qbasic_runtime_available,
        "runner": _run_qbasic_interactive_repeated_eval,
        "expected": (b"QBasic", b"4", b"-1"),
    },
    "grantsbasic": {
        "available": _grantsbasic_runtime_available,
        "runner": _run_grantsbasic_interactive_repeated_eval,
        "expected": (b"Z80 BASIC Ver 4.7b", b" 4", b"-1"),
    },
}


BACKSPACE_RENDER_CASES: dict[str, dict[str, object]] = {
    "grantsbasic": {
        "available": _grantsbasic_runtime_available,
        "runner": _run_grantsbasic_backspace_render,
        "expected": (b"AB\x08 \x08",),
    }
}


class InteractiveConformanceTests(unittest.TestCase):
    def test_every_category_explicitly_covers_all_basics(self) -> None:
        for category, statuses in CONFORMANCE_MATRIX.items():
            with self.subTest(category=category):
                self.assertEqual(set(statuses), set(BASICS))

    def test_every_status_is_pass_or_na(self) -> None:
        for category, statuses in CONFORMANCE_MATRIX.items():
            for basic, status in statuses.items():
                with self.subTest(category=category, basic=basic):
                    self.assertIn(status, {"pass", "n/a"})

    def test_ctrl_d_exit_is_required_for_all_basics(self) -> None:
        self.assertTrue(all(status == "pass" for status in CONFORMANCE_MATRIX["ctrl_d_exit"].values()))

    def test_text_only_shell_is_enabled_only_for_qbasic_and_gwbasic(self) -> None:
        enabled = {
            basic
            for basic, status in CONFORMANCE_MATRIX["text_only_interactive_shell"].items()
            if status == "pass"
        }
        self.assertEqual(enabled, {"gwbasic", "qbasic"})

    def test_no_orphan_process_is_not_marked_na_for_external_emulators(self) -> None:
        for basic in ("basic80", "n88basic", "fm7basic", "fm11basic", "gwbasic", "msxbasic", "qbasic"):
            with self.subTest(basic=basic):
                self.assertEqual(CONFORMANCE_MATRIX["no_orphan_process"][basic], "pass")


def _make_interactive_eval_test(basic: str) -> Callable[[InteractiveConformanceTests], None]:
    def test(self: InteractiveConformanceTests) -> None:
        status = CONFORMANCE_MATRIX["interactive_eval"][basic]
        if status == "n/a":
            self.skipTest(f"interactive_eval is not applicable to {basic}")

        case = INTERACTIVE_EVAL_CASES[basic]
        available = case["available"]
        if not callable(available) or not available():
            self.skipTest(f"{basic} interactive runtime is not available")

        runner = case["runner"]
        assert callable(runner)
        output, returncode = runner()
        decoded = output.decode("latin-1", errors="replace")
        self.assertEqual(returncode, 0, decoded[-2000:])
        for expected in case["expected"]:
            self.assertIn(expected, output, decoded[-2000:])

    return test


def _make_backspace_render_test(basic: str) -> Callable[[InteractiveConformanceTests], None]:
    def test(self: InteractiveConformanceTests) -> None:
        status = CONFORMANCE_MATRIX["interactive_backspace_rendering"][basic]
        if status == "n/a":
            self.skipTest(f"interactive_backspace_rendering is not applicable to {basic}")

        case = BACKSPACE_RENDER_CASES[basic]
        available = case["available"]
        if not callable(available) or not available():
            self.skipTest(f"{basic} interactive runtime is not available")

        runner = case["runner"]
        assert callable(runner)
        output, returncode = runner()
        decoded = output.decode("latin-1", errors="replace")
        self.assertEqual(returncode, 0, decoded[-2000:])
        for expected in case["expected"]:
            self.assertIn(expected, output, decoded[-2000:])

    return test


def _make_interactive_repeated_eval_test(basic: str) -> Callable[[InteractiveConformanceTests], None]:
    def test(self: InteractiveConformanceTests) -> None:
        status = CONFORMANCE_MATRIX["interactive_repeated_eval"][basic]
        if status == "n/a":
            self.skipTest(f"interactive_repeated_eval is not applicable to {basic}")

        case = INTERACTIVE_REPEATED_EVAL_CASES[basic]
        available = case["available"]
        if not callable(available) or not available():
            self.skipTest(f"{basic} interactive runtime is not available")

        runner = case["runner"]
        assert callable(runner)
        output, returncode = runner()
        decoded = output.decode("latin-1", errors="replace")
        self.assertEqual(returncode, 0, decoded[-2000:])
        for expected in case["expected"]:
            self.assertIn(expected, output, decoded[-2000:])

    return test


for _basic in BASICS:
    setattr(
        InteractiveConformanceTests,
        f"test_{_basic}_interactive_eval_conformance",
        _make_interactive_eval_test(_basic),
    )

for _basic in BASICS:
    setattr(
        InteractiveConformanceTests,
        f"test_{_basic}_interactive_repeated_eval_conformance",
        _make_interactive_repeated_eval_test(_basic),
    )

for _basic in BASICS:
    if CONFORMANCE_MATRIX["interactive_backspace_rendering"][_basic] != "pass":
        continue
    setattr(
        InteractiveConformanceTests,
        f"test_{_basic}_interactive_backspace_rendering",
        _make_backspace_render_test(_basic),
    )


if __name__ == "__main__":
    unittest.main()
