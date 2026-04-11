from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from collections import OrderedDict
from pathlib import Path


_EXIT_COMMANDS = {"EXIT", "QUIT", "SYSTEM"}
_SAVE_LOAD_PATTERN = re.compile(r'^(SAVE|LOAD)\s+"([^"]+)"\s*$', re.IGNORECASE)
_TRAILING_READY_NOISE_RE = re.compile(r"^(Ok)[\uFFFD\x00-\x1f\x7f ]+$")


def _normalize_program_text(source_text: str) -> list[str]:
    normalized = source_text.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for line_number, raw_line in enumerate(normalized.split("\n"), start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue
        if not stripped[0].isdigit():
            raise ValueError(f"line {line_number} missing line number: {stripped[:50]}")
        lines.append(stripped)
    return lines


def _extract_marker_output(stdout_text: str, begin_marker: str, end_marker: str) -> str:
    lines = stdout_text.splitlines()
    captured: list[str] = []
    inside = False
    saw_begin = False
    saw_end = False
    for line in lines:
        stripped = line.strip()
        if not inside:
            if stripped == begin_marker:
                inside = True
                saw_begin = True
            continue
        if stripped == end_marker:
            saw_end = True
            break
        captured.append(line)
    if not (saw_begin and saw_end):
        return stdout_text
    if not captured:
        return ""
    return "\n".join(captured) + "\n"


class DosTextShell:
    def __init__(
        self,
        *,
        shell_name: str,
        runner_path: Path,
        archive_path: str,
        runtime_dir: str,
        home_dir: str,
        file_path: str | None,
        prompt: str,
    ) -> None:
        self.shell_name = shell_name
        self.runner_path = runner_path
        self.archive_path = archive_path
        self.runtime_dir = runtime_dir
        self.home_dir = home_dir
        self.prompt = prompt
        self.program_lines: OrderedDict[int, str] = OrderedDict()
        self.direct_history: list[str] = []
        self._marker_counter = 0
        if file_path is not None:
            self._load_program_file(Path(file_path))

    def run(self) -> int:
        print(
            f"{self.shell_name} text shell. Numbered lines and direct-mode state persist. Ctrl-D exits.",
            flush=True,
        )
        while True:
            try:
                command = input(self.prompt)
            except EOFError:
                print()
                return 0

            if not self._handle_command(command):
                return 0

    def _handle_command(self, command: str) -> bool:
        stripped = command.strip()
        if not stripped:
            return True

        if stripped[0].isdigit():
            self._store_program_line(stripped)
            return True

        upper = stripped.upper()
        if upper in _EXIT_COMMANDS:
            return False
        if upper == "LIST":
            self._print_program_listing()
            return True
        if upper == "NEW":
            self.program_lines.clear()
            self.direct_history.clear()
            return True
        if upper == "RUN":
            self._run_loaded_program()
            return True

        save_load = _SAVE_LOAD_PATTERN.match(stripped)
        if save_load is not None:
            action, raw_path = save_load.groups()
            target_path = Path(raw_path)
            if action.upper() == "SAVE":
                self._save_program_file(target_path)
            else:
                self._load_program_file(target_path)
            return True

        self._run_direct_statement(stripped)
        return True

    def _store_program_line(self, line: str) -> None:
        number_text, _, body = line.partition(" ")
        line_number = int(number_text)
        if not body.strip():
            self.program_lines.pop(line_number, None)
            return
        self.program_lines[line_number] = f"{line_number} {body.strip()}"
        self.program_lines = OrderedDict(sorted(self.program_lines.items()))

    def _print_program_listing(self) -> None:
        for line in self.program_lines.values():
            print(line)

    def _save_program_file(self, path: Path) -> None:
        text = "".join(f"{line}\n" for line in self.program_lines.values())
        path.write_text(text, encoding="ascii")
        print(f"Saved {len(self.program_lines)} line(s) to {path}")

    def _load_program_file(self, path: Path) -> None:
        lines = _normalize_program_text(path.read_text(encoding="ascii"))
        self.program_lines.clear()
        self.direct_history.clear()
        for line in lines:
            self._store_program_line(line)
        print(f"Loaded {len(self.program_lines)} line(s) from {path}")

    def _run_loaded_program(self) -> None:
        if not self.program_lines:
            print("error: no program loaded", file=sys.stderr)
            return
        source_text = "".join(f"{line}\n" for line in self.program_lines.values())
        result = self._run_batch_source(source_text)
        self._emit_batch_result(result)

    def _run_direct_statement(self, statement: str) -> None:
        source_text, begin_marker, end_marker = self._build_direct_source(statement)
        result = self._run_batch_source(source_text)
        self._emit_batch_result(result, begin_marker=begin_marker, end_marker=end_marker)
        if result.returncode == 0:
            self.direct_history.append(statement)

    def _build_direct_source(self, statement: str) -> tuple[str, str, str]:
        self._marker_counter += 1
        begin_marker = f"__CLASSIC_BASIC_BEGIN_{self._marker_counter}__"
        end_marker = f"__CLASSIC_BASIC_END_{self._marker_counter}__"
        lines: list[str] = []
        line_number = 10
        for previous in self.direct_history:
            lines.append(f"{line_number} {previous}")
            line_number += 10
        lines.append(f'{line_number} PRINT "{begin_marker}"')
        line_number += 10
        lines.append(f"{line_number} {statement}")
        line_number += 10
        lines.append(f'{line_number} PRINT "{end_marker}"')
        line_number += 10
        lines.append(f"{line_number} END")
        return "\n".join(lines) + "\n", begin_marker, end_marker

    def _run_batch_source(self, source_text: str) -> subprocess.CompletedProcess[str]:
        with tempfile.NamedTemporaryFile("w", suffix=".bas", encoding="ascii", delete=False) as handle:
            handle.write(source_text)
            temp_path = Path(handle.name)

        try:
            env = os.environ.copy()
            env["CLASSIC_BASIC_DOSEMU_PTY"] = "off"
            result = subprocess.run(
                [
                    "bash",
                    str(self.runner_path),
                    "--archive",
                    self.archive_path,
                    "--runtime",
                    self.runtime_dir,
                    "--home",
                    self.home_dir,
                    "--run",
                    "--file",
                    str(temp_path),
                ],
                cwd=self.runner_path.parent.parent,
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=90,
            )
            return subprocess.CompletedProcess(
                args=result.args,
                returncode=result.returncode,
                stdout=self._decode_process_stream(result.stdout),
                stderr=self._decode_process_stream(result.stderr),
            )
        finally:
            temp_path.unlink(missing_ok=True)

    def _emit_batch_result(
        self,
        result: subprocess.CompletedProcess[str],
        *,
        begin_marker: str | None = None,
        end_marker: str | None = None,
    ) -> None:
        stdout_text = self._clean_batch_stdout(result.stdout)
        if begin_marker is not None and end_marker is not None:
            stdout_text = _extract_marker_output(stdout_text, begin_marker, end_marker)

        if stdout_text:
            sys.stdout.write(stdout_text)
            if not stdout_text.endswith("\n"):
                sys.stdout.write("\n")
            sys.stdout.flush()

        stderr_text = self._clean_batch_stderr(result.stderr)
        if result.returncode != 0 and stderr_text:
            print(stderr_text, file=sys.stderr)
        elif result.returncode == 0 and stderr_text:
            print(stderr_text, file=sys.stderr)

    def _clean_batch_stderr(self, stderr_text: str) -> str:
        filtered: list[str] = []
        for raw_line in stderr_text.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            if stripped.startswith(f"Prepared {self.shell_name} runtime at "):
                continue
            if stripped.startswith("Prepared dosemu HOME at "):
                continue
            if stripped.startswith(f"{self.shell_name} executable: "):
                continue
            filtered.append(stripped)
        return "\n".join(filtered)

    def _clean_batch_stdout(self, stdout_text: str) -> str:
        if not stdout_text:
            return ""
        lines = stdout_text.splitlines(keepends=True)
        for index, raw_line in enumerate(lines):
            line_ending = ""
            line_body = raw_line
            if raw_line.endswith("\r\n"):
                line_body = raw_line[:-2]
                line_ending = "\r\n"
            elif raw_line.endswith("\n") or raw_line.endswith("\r"):
                line_body = raw_line[:-1]
                line_ending = raw_line[-1]
            match = _TRAILING_READY_NOISE_RE.match(line_body)
            if match is not None:
                lines[index] = match.group(1) + line_ending
        return "".join(lines)

    @staticmethod
    def _decode_process_stream(data: str | bytes | None) -> str:
        if data is None:
            return ""
        if isinstance(data, str):
            return data
        return data.decode("utf-8", errors="replace")
