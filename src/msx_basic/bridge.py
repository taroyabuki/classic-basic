"""openMSX control interface bridge.

Communicates with openMSX via the XML-based -control stdio protocol.
Each command is sent as ``<command>tcl_code</command>\\n``.
Replies arrive as ``<reply result="ok">output</reply>`` or
``<reply result="nok">error</reply>``.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from queue import Empty, Queue
from typing import Iterator


_SCREEN_CURSOR_MARKER = "\n@@CURSOR_ROW@@"


class OpenMSXError(RuntimeError):
    """Raised when openMSX returns a ``result="nok"`` reply."""


class OpenMSXBridge:
    """Start and communicate with an openMSX process via -control stdio."""

    POLL_INTERVAL = 0.08  # seconds between screen polls
    STARTUP_SETTLE_DELAY = 0.5
    STARTUP_POWER_TIMEOUT = 5.0
    STARTUP_ATTEMPTS = 3
    STARTUP_RETRY_DELAY = 1.0

    def __init__(
        self,
        machine: str = "C-BIOS_MSX1",
        extra_rom_path: str | None = None,
        extra_args: list[str] | None = None,
    ) -> None:
        self._machine = machine
        self._extra_rom_path = extra_rom_path
        self._extra_args = extra_args or []
        self._proc: subprocess.Popen | None = None
        self._reply_queue: Queue[str] = Queue()
        self._reader_thread: threading.Thread | None = None
        self._buf = ""  # accumulates raw bytes from stdout

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        last_error: Exception | None = None
        for attempt in range(1, self.STARTUP_ATTEMPTS + 1):
            try:
                self._start_once()
                return
            except FileNotFoundError:
                self.stop()
                raise
            except Exception as exc:
                last_error = exc
                self.stop()
                if attempt >= self.STARTUP_ATTEMPTS:
                    break
                time.sleep(self.STARTUP_RETRY_DELAY * attempt)
        assert last_error is not None
        raise RuntimeError(
            f"openMSX startup failed after {self.STARTUP_ATTEMPTS} attempts"
        ) from last_error

    def _start_once(self) -> None:
        env = os.environ.copy()
        env["SDL_VIDEODRIVER"] = "dummy"
        env["SDL_AUDIODRIVER"] = "dummy"
        env.pop("DISPLAY", None)

        cmd = ["openmsx", "-machine", self._machine, "-control", "stdio"]
        if self._extra_rom_path:
            # Add the directory to openMSX filepool so ROMs are found there
            cmd += ["-command", f"filepool add -type system_rom -path {self._extra_rom_path}"]
        cmd += self._extra_args

        self._reply_queue = Queue()
        self._buf = ""
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,  # unbuffered: read(N) returns as soon as any bytes arrive
            env=env,
        )
        self._reader_thread = threading.Thread(
            target=self._read_loop, daemon=True, name="openmsx-reader"
        )
        self._reader_thread.start()

        # With -control stdio, openMSX sets parseStatus=CONTROL so it does NOT
        # automatically power up the machine.  We must do it explicitly.
        time.sleep(self.STARTUP_SETTLE_DELAY)
        self.command("set power on", timeout=self.STARTUP_POWER_TIMEOUT)

    def stop(self) -> None:
        if self._proc is None:
            return
        try:
            self._send_raw("exit")
            self._proc.wait(timeout=3)
        except Exception:
            pass
        finally:
            if self._proc.poll() is None:
                self._proc.terminate()
            self._proc = None
            self._reader_thread = None
            self._reply_queue = Queue()
            self._buf = ""

    def __enter__(self) -> "OpenMSXBridge":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()

    # ------------------------------------------------------------------ #
    # Commands                                                             #
    # ------------------------------------------------------------------ #

    def command(self, tcl_code: str, timeout: float = 5.0) -> str:
        """Send a Tcl command and return the reply string."""
        self._send_raw(tcl_code)
        try:
            result = self._reply_queue.get(timeout=timeout)
        except Empty:
            raise TimeoutError(f"No reply for command: {tcl_code!r}") from None
        return result

    def command_nowait(self, tcl_code: str) -> None:
        """Send a Tcl command without waiting for a reply."""
        self._send_raw(tcl_code)

    def get_screen_state(self, timeout: float = 2.0) -> tuple[list[str], int]:
        """Return the MSX screen and cursor row from one Tcl command."""
        raw = self.command(
            'set s [get_screen]; append s "\n@@CURSOR_ROW@@" [peek 0xF3DC]; set s',
            timeout=timeout,
        )
        screen_raw, marker, cursor_raw = raw.rpartition(_SCREEN_CURSOR_MARKER)
        if not marker:
            screen_raw = raw
            cursor_row = 0
        else:
            try:
                cursor_row = int(cursor_raw.strip())
            except ValueError:
                cursor_row = 0
        lines = screen_raw.split("\n") if screen_raw else []
        active_rows = {cursor_row}
        if cursor_row > 0:
            active_rows.add(cursor_row - 1)
        result: list[str] = []
        for row, line in enumerate(lines[:24]):
            if row in active_rows:
                result.append(line)
            else:
                result.append(line.rstrip())
        while len(result) < 24:
            result.append("")
        return result, cursor_row

    def get_screen(self, timeout: float = 2.0) -> list[str]:
        """Return the MSX text-mode screen as a list of 24 stripped strings."""
        lines, _ = self.get_screen_state(timeout=timeout)
        return lines

    def get_cursor_row(self) -> int:
        """Return the current cursor row (0-based) from MSX system RAM."""
        _, cursor_row = self.get_screen_state(timeout=1.0)
        return cursor_row

    def peek16(self, address: int, timeout: float = 1.0) -> int:
        """Return a 16-bit little-endian value from MSX memory."""
        return int(self.command(f"peek16 0x{address:04X}", timeout=timeout))

    def type_text(
        self,
        text: str,
        *,
        via_keybuf: bool = False,
        timeout: float | None = None,
    ) -> None:
        """Type text into the MSX (sends key-press events).

        Literal CR (``\\r``) in *text* is sent as the Enter key, ``\\b`` as
        Backspace.  The string is forwarded to openMSX via the Tcl ``type``
        command which performs the actual key mapping.
        """
        # Build a Tcl double-quoted string for the 'type' command.
        # Step 1: escape literal backslashes (must come first).
        escaped = text.replace("\\", "\\\\")
        # Step 2: map real control characters to Tcl two-char escape sequences.
        #         Tcl interprets \r as CR, \n as LF, \b as backspace inside "".
        escaped = escaped.replace("\r", "\\r")
        escaped = escaped.replace("\n", "\\n")
        escaped = escaped.replace("\b", "\\b")
        # Step 3: escape Tcl metacharacters valid inside double-quoted strings.
        escaped = escaped.replace('"', '\\"')
        escaped = escaped.replace("[", "\\[")
        escaped = escaped.replace("$", "\\$")
        command = "type_via_keybuf" if via_keybuf else "type"
        command_timeout = timeout if timeout is not None else (15.0 if via_keybuf else 5.0)
        self.command(f'{command} "{escaped}"', timeout=command_timeout)

    def wait_for_boot(self, timeout: float = 8.0, poll: float = 0.25) -> bool:
        """Block until MSX-BASIC's Ok prompt is visible, or ``timeout`` expires.

        Returns True if BASIC prompt was found, False on timeout.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(poll)
            try:
                lines = self.get_screen(timeout=1.0)
            except TimeoutError:
                continue
            for line in lines:
                if line.strip() == "Ok":
                    return True
        return False

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _send_raw(self, tcl_code: str) -> None:
        assert self._proc is not None, "Bridge not started"
        assert self._proc.stdin is not None
        self._proc.stdin.write(f"<command>{tcl_code}</command>\n".encode())
        self._proc.stdin.flush()

    def _read_loop(self) -> None:
        """Background thread: read bytes from openMSX stdout, parse XML elements."""
        proc = self._proc
        assert proc is not None
        stdout = proc.stdout
        assert stdout is not None
        buf = ""
        while True:
            chunk = stdout.read(512)
            if not chunk:
                break
            try:
                text = chunk.decode("latin-1")
            except Exception:
                continue
            buf += text
            buf = self._parse_buf(buf)

    def _parse_buf(self, buf: str) -> str:
        """Extract complete XML child elements from buf, enqueue replies, return leftover."""
        while True:
            start = buf.find("<")
            if start == -1:
                return ""

            # Skip whitespace/newlines before the tag
            buf = buf[start:]

            tag_end = buf.find(">")
            if tag_end == -1:
                return buf  # Incomplete tag, wait for more data

            tag_inner = buf[1:tag_end]

            # Closing tag – skip it
            if tag_inner.startswith("/"):
                buf = buf[tag_end + 1:]
                continue

            # Self-closing element (unlikely here but safe)
            if tag_inner.endswith("/"):
                buf = buf[tag_end + 1:]
                continue

            tag_name = tag_inner.split()[0]

            # The <openmsx-output> wrapper is not a real element to process
            if tag_name == "openmsx-output":
                buf = buf[tag_end + 1:]
                continue

            # Find closing tag for this element
            close_tag = f"</{tag_name}>"
            close_pos = buf.find(close_tag, tag_end + 1)
            if close_pos == -1:
                return buf  # Element not yet complete

            element_text = buf[: close_pos + len(close_tag)]
            buf = buf[close_pos + len(close_tag):]
            self._handle_element(element_text, tag_name)

    def _handle_element(self, text: str, tag_name: str) -> None:
        if tag_name == "reply":
            try:
                elem = ET.fromstring(text)
                if elem.get("result") == "ok":
                    self._reply_queue.put(elem.text or "")
                else:
                    # nok - still enqueue so command() doesn't hang
                    self._reply_queue.put("")
            except ET.ParseError:
                self._reply_queue.put("")
        # log and update elements are ignored
