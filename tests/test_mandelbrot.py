from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
MANDELBROT_DIR = ROOT_DIR / "demo" / "mandelbrot"


def _run(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
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


def _dosemu_available() -> bool:
    return shutil.which("dosemu") is not None or shutil.which("dosemu2") is not None


def _mame_available() -> str | None:
    for candidate in (shutil.which("mame"), "/usr/games/mame"):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


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


def _normalize_output(stdout: str) -> str:
    text = stdout.replace("\r", "")
    if not text:
        return text
    return text.rstrip("\n") + "\n"


def _load_expected(path: Path) -> str:
    return _normalize_output(path.read_text(encoding="utf-8"))


class MandelbrotRuntimeTests(unittest.TestCase):
    maxDiff = None

    def test_mandelbrot_full_text_contract_across_runtimes(self) -> None:
        expected = _load_expected(MANDELBROT_DIR / "expected.txt")
        cases = [
            {
                "name": "6502",
                "available": lambda: True,
                "command": ["bash", "run/6502.sh", "--run", "--file", "demo/mandelbrot/asciiart.bas"],
                "timeout": 240,
                "returncodes": {0},
            },
            {
                "name": "basic80",
                "available": lambda: True,
                "command": ["bash", "run/basic80.sh", "--run", "--file", "demo/mandelbrot/asciiart.bas"],
                "timeout": 180,
                "returncodes": {0},
            },
            {
                "name": "grantsbasic",
                "available": _grantsbasic_runtime_available,
                "command": [
                    "bash",
                    "run/grantsbasic.sh",
                    "--max-steps",
                    "2000000",
                    "--run",
                    "--file",
                    "demo/mandelbrot/asciiart-grantsbasic.bas",
                    "--timeout",
                    "300",
                ],
                "timeout": 330,
                "returncodes": {0},
            },
            {
                "name": "nbasic",
                "available": _nbasic_runtime_available,
                "command": ["bash", "run/nbasic.sh", "--run", "--file", "demo/mandelbrot/asciiart-nbasic.bas"],
                "timeout": 240,
                "returncodes": {0},
            },
            {
                "name": "n88basic",
                "available": _n88basic_runtime_available,
                "command": [
                    "bash",
                    "run/n88basic.sh",
                    "--run",
                    "--file",
                    "demo/mandelbrot/asciiart.bas",
                    "--timeout",
                    "1080",
                ],
                "timeout": 1200,
                "returncodes": {0},
            },
            {
                "name": "fm7basic",
                "available": _fm7basic_runtime_available,
                "command": ["bash", "run/fm7basic.sh", "--run", "--file", "demo/mandelbrot/asciiart-fm7.bas", "--timeout", "180"],
                "timeout": 420,
                "returncodes": {0},
            },
            {
                "name": "fm11basic",
                "available": _fm11basic_runtime_available,
                "command": [
                    "bash",
                    "run/fm11basic.sh",
                    "--run",
                    "--file",
                    "demo/mandelbrot/asciiart.bas",
                    "--timeout",
                    "480",
                ],
                "timeout": 540,
                "returncodes": {0},
            },
            {
                "name": "gwbasic",
                "available": _dosemu_available,
                "command": ["bash", "run/gwbasic.sh", "--run", "--file", "demo/mandelbrot/asciiart.bas", "--timeout", "120"],
                "timeout": 120,
                "returncodes": {0},
            },
            {
                "name": "msxbasic",
                "available": lambda: shutil.which("openmsx") is not None,
                "command": [
                    "bash",
                    "run/msxbasic.sh",
                    "--run",
                    "--file",
                    "demo/mandelbrot/asciiart.bas",
                    "--timeout",
                    "480",
                ],
                "timeout": 540,
                "returncodes": {0},
            },
            {
                "name": "qbasic",
                "available": _dosemu_available,
                "command": ["bash", "run/qbasic.sh", "--run", "--file", "demo/mandelbrot/asciiart.bas", "--timeout", "120"],
                "timeout": 120,
                "returncodes": {0},
            },
        ]

        ran_any = False
        for case in cases:
            if not case["available"]():
                continue
            ran_any = True
            with self.subTest(runtime=case["name"]):
                result = _run(case["command"], timeout=case["timeout"])
                self.assertIn(result.returncode, case["returncodes"], result.stderr)
                self.assertEqual(_normalize_output(result.stdout), expected, result.stdout)
        if not ran_any:
            self.skipTest("no mandelbrot runtimes are installed")
