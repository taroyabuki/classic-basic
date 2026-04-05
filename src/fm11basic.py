#!/usr/bin/env python3

from __future__ import annotations

import argparse
import fcntl
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_SCRIPT = REPO_ROOT / "scripts" / "bootstrap_fm11basic_assets.sh"
EMULATOR_EXE = REPO_ROOT / "downloads" / "fm11basic" / "staged" / "emulator-x86" / "Fm11.exe"
FBASIC_DISK = REPO_ROOT / "downloads" / "fm11basic" / "staged" / "disks" / "fb2hd00.img"
ROM_STAGE_DIR = REPO_ROOT / "downloads" / "fm11basic" / "staged" / "roms"
DISPLAY_BASE = 99
CACHE_ROOT = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "fm11-basic"
BASE_WINE_PREFIX = CACHE_ROOT / "wine-prefix-base"
BASE_WINE_PREFIX_LOCK = CACHE_ROOT / "wine-prefix-base.lock"


class BasicRuntimeError(RuntimeError):
    pass


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise BasicRuntimeError(f"missing required tool: {name}")


def read_basic_file(path: Path) -> str:
    data = path.read_bytes()
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise BasicRuntimeError(f"{path} must be ASCII-only") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n")


def reject_unsupported_run_input(source: str) -> None:
    upper = source.upper()
    if re.search(r"(^|[^A-Z])(LINE[ \t]+INPUT|INPUT)([^A-Z]|$)", upper):
        raise BasicRuntimeError("INPUT-style program input is unsupported in --run mode")


def windows_path(path: Path) -> str:
    return "Z:" + str(path).replace("/", "\\")


def run_command(args, *, env=None, cwd=None, capture_output=False, check=True):
    return subprocess.run(
        args,
        env=env,
        cwd=cwd,
        check=check,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
    )


class RuntimeSession:
    def __init__(self) -> None:
        self.tempdir = Path(tempfile.mkdtemp(prefix="fm11-basic-"))
        self.wine_prefix = self.tempdir / "wine-prefix"
        self.runtime_emulator_dir = self.tempdir / "emulator"
        self.display: str | None = None
        self.xvfb_proc: subprocess.Popen[str] | None = None
        self.wine_proc: subprocess.Popen[str] | None = None
        self.window_id: str | None = None

    @property
    def env(self):
        env = os.environ.copy()
        env["DISPLAY"] = self.display or ""
        env["WINEPREFIX"] = str(self.wine_prefix)
        return env

    def start(self) -> None:
        self._start_xvfb()
        self._prepare_wine_prefix()
        self._seed_registry()
        self._prepare_emulator()
        self.wine_proc = subprocess.Popen(
            ["wine", str(self.runtime_emulator_dir / "Fm11.exe")],
            cwd=str(self.runtime_emulator_dir),
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.window_id = self._wait_for_window()

    def close(self) -> None:
        if self.wine_proc is not None and self.wine_proc.poll() is None:
            try:
                os.killpg(self.wine_proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.wine_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.wine_proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if self.xvfb_proc is not None and self.xvfb_proc.poll() is None:
            self.xvfb_proc.terminate()
            try:
                self.xvfb_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.xvfb_proc.kill()
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def _seed_registry(self) -> None:
        reg_base = r"HKCU\Software\Culti\FM11\FM11"
        commands = [
            ["wine", "reg", "add", reg_base, "/v", "2HD0", "/t", "REG_SZ", "/d", windows_path(FBASIC_DISK), "/f"],
            ["wine", "reg", "add", reg_base, "/v", "DipSW", "/t", "REG_DWORD", "/d", "0x9f", "/f"],
        ]
        for command in commands:
            run_command(command, env=self.env, capture_output=True)

    def _prepare_wine_prefix(self) -> None:
        base_prefix = self._ensure_base_wine_prefix()
        if self.wine_prefix.exists():
            shutil.rmtree(self.wine_prefix)
        shutil.copytree(base_prefix, self.wine_prefix, symlinks=True)

    def _ensure_base_wine_prefix(self) -> Path:
        CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        with BASE_WINE_PREFIX_LOCK.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            system_reg = BASE_WINE_PREFIX / "system.reg"
            if not system_reg.is_file():
                if BASE_WINE_PREFIX.exists():
                    shutil.rmtree(BASE_WINE_PREFIX)
                BASE_WINE_PREFIX.mkdir(parents=True, exist_ok=True)
                env = os.environ.copy()
                env["DISPLAY"] = self.display or ""
                env["WINEPREFIX"] = str(BASE_WINE_PREFIX)
                run_command(["wineboot", "-i"], env=env, capture_output=True)
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        return BASE_WINE_PREFIX

    def _wait_for_window(self) -> str:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            result = run_command(
                ["xdotool", "search", "--name", "FUJITSU MICRO 11"],
                env=self.env,
                capture_output=True,
                check=False,
            )
            ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            if ids:
                return ids[0]
            time.sleep(0.5)
        raise BasicRuntimeError("emulator window did not appear")

    def _start_xvfb(self) -> None:
        for display_no in range(DISPLAY_BASE, DISPLAY_BASE + 20):
            display = f":{display_no}"
            proc = subprocess.Popen(
                ["Xvfb", display, "-screen", "0", "1280x1024x24"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.5)
            if proc.poll() is not None:
                continue
            test_env = os.environ.copy()
            test_env["DISPLAY"] = display
            probe = run_command(["xdpyinfo"], env=test_env, capture_output=True, check=False)
            if probe.returncode == 0:
                self.display = display
                self.xvfb_proc = proc
                return
            proc.terminate()
            proc.wait(timeout=5)
        raise BasicRuntimeError("failed to start Xvfb")

    def _prepare_emulator(self) -> None:
        self.runtime_emulator_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(EMULATOR_EXE, self.runtime_emulator_dir / "Fm11.exe")
        for rom_path in ROM_STAGE_DIR.glob("*.rom"):
            shutil.copy2(rom_path, self.runtime_emulator_dir / rom_path.name)

    def key(self, *keys: str) -> None:
        run_command(["xdotool", "key", "--window", self.window_id or "", *keys], env=self.env, capture_output=True)

    def set_clipboard(self, text: str) -> None:
        try:
            subprocess.run(
                ["xclip", "-selection", "clipboard", "-i"],
                env=self.env,
                check=True,
                text=True,
                input=text,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError as exc:
            raise BasicRuntimeError("failed to load text into the X clipboard") from exc

    def paste_text(self, text: str) -> None:
        self.set_clipboard(text)
        self.key("Alt+e", "p")
        time.sleep(0.2)

    def copy_screen(self) -> str:
        geometry = run_command(
            ["xdotool", "getwindowgeometry", "--shell", self.window_id or ""],
            env=self.env,
            capture_output=True,
        ).stdout
        info = {}
        for line in geometry.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                info[key] = value
        x_pos = int(info["X"])
        y_pos = int(info["Y"])
        width = int(info["WIDTH"])
        height = int(info["HEIGHT"])

        start_x = str(x_pos + 2)
        start_y = str(y_pos + 24)
        end_x = str(x_pos + width - 2)
        end_y = str(y_pos + height - 2)

        run_command(["xdotool", "mousemove", start_x, start_y], env=self.env, capture_output=True)
        run_command(["xdotool", "mousedown", "1"], env=self.env, capture_output=True)
        run_command(["xdotool", "mousemove", end_x, end_y], env=self.env, capture_output=True)
        run_command(["xdotool", "mouseup", "1"], env=self.env, capture_output=True)
        time.sleep(0.1)
        self.key("Alt+e", "c")
        time.sleep(0.1)
        result = run_command(
            ["xclip", "-selection", "clipboard", "-o"],
            env=self.env,
            capture_output=True,
            check=False,
        )
        return result.stdout.replace("\r\n", "\n").replace("\r", "\n")

    def wait_for_text(self, needle: str, timeout: float = 30.0) -> str:
        deadline = time.monotonic() + timeout
        latest = ""
        while time.monotonic() < deadline:
            latest = self.copy_screen()
            if needle in latest:
                return latest
            time.sleep(0.5)
        raise BasicRuntimeError(f"timed out waiting for {needle!r}")


def normalize_screen_text(screen: str):
    normalized = []
    for raw_line in screen.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        if not raw_line:
            normalized.append("")
            continue
        weird_chars = sum(1 for char in raw_line if ord(char) < 32 or ord(char) > 126)
        if weird_chars and weird_chars / len(raw_line) > 0.2:
            continue
        clean_line = "".join(char if 32 <= ord(char) <= 126 else " " for char in raw_line).rstrip()
        alnum_count = sum(1 for char in clean_line if char.isalnum())
        if weird_chars and alnum_count < 4:
            continue
        if clean_line.startswith("File  Edit  Disk  Tape  Help"):
            continue
        if clean_line.startswith("FUJITSU FM-11"):
            continue
        normalized.append(clean_line)

    while normalized and normalized[-1] == "":
        normalized.pop()

    deduped = []
    for line in normalized:
        if deduped and deduped[-1] == line and line != "":
            continue
        deduped.append(line)
    return deduped


def is_noise_line(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and len(stripped) == 1 and stripped.islower() and line != stripped


def screen_appended_lines(before_lines, after_lines):
    max_overlap = min(len(before_lines), len(after_lines))
    for overlap in range(max_overlap, -1, -1):
        if before_lines[-overlap:] == after_lines[:overlap]:
            return after_lines[overlap:]
    return after_lines


def filter_new_lines(lines, sent_text: str):
    echoed = {line for line in sent_text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if line}
    filtered = []
    for line in lines:
        if line in echoed:
            continue
        if line == "Ready":
            continue
        if is_noise_line(line):
            continue
        filtered.append(line)
    while filtered and filtered[0] == "":
        filtered.pop(0)
    while filtered and filtered[-1] == "":
        filtered.pop()
    return filtered


def emit_lines(lines) -> None:
    if not lines:
        return
    sys.stdout.write("\n".join(lines) + "\n")
    sys.stdout.flush()


def wait_for_ready(session: RuntimeSession, previous: str, timeout: float = 20.0) -> str:
    deadline = time.monotonic() + timeout
    latest = previous
    while time.monotonic() < deadline:
        latest = session.copy_screen()
        if "\nReady" in latest or latest.strip().endswith("Ready"):
            return latest
        time.sleep(0.5)
    raise BasicRuntimeError("timed out waiting for BASIC prompt")


def collect_run_output(session: RuntimeSession, previous_lines, sent_text: str, timeout: float = 30.0):
    deadline = time.monotonic() + timeout
    latest = previous_lines
    emitted = []
    seen = set(previous_lines)
    time.sleep(0.2)
    raw_screen = session.copy_screen()
    latest = normalize_screen_text(raw_screen)
    initial_lines = filter_new_lines(screen_appended_lines(previous_lines, latest), sent_text)
    for line in initial_lines:
        if line in seen:
            continue
        seen.add(line)
        emitted.append(line)
    if "\nReady" in raw_screen or raw_screen.strip().endswith("Ready"):
        return latest, emitted
    stagnant_since = None
    while time.monotonic() < deadline:
        raw_screen = session.copy_screen()
        current = normalize_screen_text(raw_screen)
        if current != latest:
            new_lines = filter_new_lines(screen_appended_lines(latest, current), sent_text)
            for line in new_lines:
                if line in seen:
                    continue
                seen.add(line)
                emitted.append(line)
            latest = current
            stagnant_since = None
        else:
            if stagnant_since is None:
                stagnant_since = time.monotonic()

        if "\nReady" in raw_screen or raw_screen.strip().endswith("Ready"):
            return current, emitted

        if stagnant_since is not None and time.monotonic() - stagnant_since >= 3:
            stripped_lines = [line.rstrip() for line in current if line.strip()]
            if stripped_lines and stripped_lines[-1].endswith("?"):
                raise BasicRuntimeError("program requested unsupported input during --run execution")

        time.sleep(0.4)
    raise BasicRuntimeError("timed out waiting for BASIC program completion")


def bootstrap_assets() -> None:
    result = run_command(["bash", str(BOOTSTRAP_SCRIPT)], cwd=str(REPO_ROOT), capture_output=True, check=False)
    if result.returncode != 0:
        raise BasicRuntimeError(result.stderr.strip() or "asset bootstrap failed")
    if not EMULATOR_EXE.is_file():
        raise BasicRuntimeError(f"missing staged emulator: {EMULATOR_EXE}")
    if not FBASIC_DISK.is_file():
        raise BasicRuntimeError(f"missing staged F-BASIC disk image: {FBASIC_DISK}")


def handle_startup_prompts(session: RuntimeSession) -> str:
    screen = session.wait_for_text("How many 1MB disk drives", timeout=30)
    for prompt in (
        "How many 1MB disk drives",
        "How many 320KB disk drives",
        "How many disk files",
    ):
        if prompt not in screen:
            screen = session.wait_for_text(prompt, timeout=15)
        session.key("2", "Return")
        time.sleep(0.6)
        screen = session.copy_screen()
    return wait_for_ready(session, screen, timeout=30)


def run_basic(args) -> int:
    require_tool("Xvfb")
    require_tool("xdpyinfo")
    require_tool("wine")
    require_tool("xdotool")
    require_tool("xclip")
    bootstrap_assets()

    source_text = None
    if args.file:
        source_text = read_basic_file(Path(args.file))
        if args.run:
            reject_unsupported_run_input(source_text)

    interactive_tty = sys.stdin.isatty() and not args.run

    session = RuntimeSession()
    try:
        if interactive_tty:
            print("Starting FM-11 F-BASIC...", file=sys.stderr, flush=True)
        session.start()
        raw_screen = handle_startup_prompts(session)
        screen_lines = normalize_screen_text(raw_screen)
        if interactive_tty:
            emit_lines(screen_lines)

        if source_text is not None:
            for program_line in source_text.split("\n"):
                if program_line == "":
                    continue
                before = raw_screen
                session.paste_text(program_line + "\n")
                time.sleep(0.5)
                raw_screen = wait_for_ready(session, before, timeout=20)
                screen_lines = normalize_screen_text(raw_screen)
            if interactive_tty:
                emit_lines(["Ready"])

        if args.run:
            session.paste_text("RUN\n")
            _, lines = collect_run_output(session, screen_lines, "RUN\n", timeout=30)
            emit_lines(lines)
            return 0

        for raw_line in sys.stdin:
            line = raw_line.rstrip("\n")
            before = raw_screen
            before_lines = screen_lines
            session.paste_text(line + "\n")
            time.sleep(0.5)
            raw_screen = wait_for_ready(session, before, timeout=20)
            screen_lines = normalize_screen_text(raw_screen)
            lines = filter_new_lines(screen_appended_lines(before_lines, screen_lines), line + "\n")
            emit_lines(lines)
        return 0
    finally:
        session.close()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if args.run and not args.file:
        parser.error("--run requires --file")
    return args


def main() -> int:
    try:
        return run_basic(parse_args())
    except BasicRuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
