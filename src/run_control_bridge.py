#!/usr/bin/env python3
"""
run_control_bridge.py - QUASI88 Control Bridge Client

This module implements the RunControlSession class for communicating
with the QUASI88 dedicated control bridge.

Protocol commands:
    LOAD <path> [ASCII]   - Load BASIC file into memory
    QUEUE <string>        - Queue keyboard input for injection
    GO                    - Start BASIC execution
    STATE                 - Query current emulator state
    WAIT <timeout_ms>     - Poll until state change or timeout
    QUIT                  - Stop emulator

Response format:
    OK <FIELD=VALUE...>
    ERR <CODE> <FIELD=VALUE...>
    STATE <NAME> MODE=<mode> ROM=<rom_id>
"""

import os
import re
import socket
import time

# Error codes (match run_control_bridge.h)
RUN_ERR_OK = 0
RUN_ERR_TIMEOUT = 1
RUN_ERR_UNKNOWN = 2
RUN_ERR_UNSUPPORTED_ROM = 3
RUN_ERR_HOOK_UNCONFIGURED = 4
RUN_ERR_FILE_NOT_FOUND = 5
RUN_ERR_QUEUE_FULL = 6
RUN_ERR_PROTOCOL = 7
RUN_ERR_NOT_IN_MONITOR = 8


class RunControlSession:
    """
    RunControlSession - Dedicated control bridge client for --run mode.

    This class implements the protocol for communicating with the QUASI88
    control bridge, which is separate from monitor stdin.
    """

    def __init__(self, control_socket_path=None):
        """
        Initialize the control session.

        @control_socket_path: Path to Unix domain socket for control channel.
                             If None, uses a default location.
        """
        self.control_socket_path = control_socket_path or os.environ.get(
            "N88BASIC_CONTROL_SOCKET",
            "/tmp/n88basic-control.sock",
        )
        self.socket = None
        self.transcript = []
        self._line_buffer = ""  # Leftover buffer for line-oriented reading

    def connect(self):
        """
        Connect to the control bridge via Unix domain socket.

        Returns:
            True if connection successful, False otherwise.
        """
        try:
            self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.socket.settimeout(5.0)
            self.socket.connect(self.control_socket_path)
            return True
        except (socket.error, OSError):
            # Connection failed - bridge may not be available yet
            try:
                self.socket.close()
            except Exception:
                pass
            self.socket = None
            return False

    def disconnect(self):
        """Disconnect from the control bridge."""
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
            self.socket = None

    def _recv_line(self, timeout=5.0):
        """
        Receive a single line from socket with line-oriented framing.
        
        Handles partial reads and preserves leftover data in buffer.
        Returns one complete line (without newline) or None on disconnect/error.
        
        @timeout: Maximum seconds to wait for data
        
        Returns:
            Line string without trailing newline, or None on error/disconnect
        """
        if not self.socket:
            return None

        while True:
            # Check if we have a complete line in buffer
            if '\n' in self._line_buffer:
                line, self._line_buffer = self._line_buffer.split('\n', 1)
                return line
            
            # Need to read more data
            try:
                self.socket.settimeout(timeout)
                chunk = self.socket.recv(4096)
                if not chunk:
                    # Connection closed - do NOT return partial line as complete
                    # Partial frame must stay buffered; return None to signal incomplete read
                    return None
                self._line_buffer += chunk.decode(errors='ignore')

            except socket.timeout:
                # No data available - return what we have if complete line
                if '\n' in self._line_buffer:
                    line, self._line_buffer = self._line_buffer.split('\n', 1)
                    return line
                return None
            except socket.error:
                return None

    def _recv_all(self, timeout=5.0):
        """
        Receive all available data from socket.
        
        Reads until no more data is available or timeout occurs.
        Handles partial reads and multiple response fragments.
        
        Returns:
            Complete response string, or None on timeout/error.
        """
        if not self.socket:
            return None
        
        self.socket.settimeout(timeout)
        chunks = []
        buffer = ""
        
        while True:
            try:
                chunk = self.socket.recv(4096)
                if not chunk:
                    # Connection closed
                    break
                buffer += chunk.decode(errors='ignore')
                chunks.append(buffer)
                buffer = ""
                
                # Check if we have a complete response (ends with newline)
                if '\n' in ''.join(chunks):
                    break
                    
            except socket.timeout:
                # No more data available
                break
            except socket.error:
                break
        
        return ''.join(chunks).strip()

    def _send_command(self, command):
        """
        Send a command to the control bridge and receive response.

        Uses line-oriented protocol: one command per line, one response per line.

        @command: Command string (e.g., "LOAD /path/to/file.bas ASCII")

        Returns:
            (ok, response_dict) where:
                ok = True/False indicating success
                response_dict = {"code": ..., "fields": {...}}
        """
        if not self.socket:
            return False, {"code": RUN_ERR_PROTOCOL, "fields": {"reason": "not_connected"}}

        try:
            self.socket.sendall((command + "\n").encode())
            # Log the raw command to transcript
            self.transcript.append(command)
            line = self._recv_line(timeout=5.0)
            if not line:
                return False, {"code": RUN_ERR_PROTOCOL, "fields": {"reason": "no_response"}}
            # Log the raw response line to transcript (preserving wire format)
            self.transcript.append(line)
            return self._parse_response(line)
        except socket.error as e:
            return False, {"code": RUN_ERR_PROTOCOL, "fields": {"reason": str(e)}}

    def _parse_response(self, response):
        """
        Parse a bridge response into structured form.

        Supports both symbolic error codes (ERR RUN_ERR_TIMEOUT) and
        numeric error codes (ERR 1).

        @response: Raw response string from bridge

        Returns:
            (ok, response_dict)
        """
        parts = response.split()
        if not parts:
            return False, {"code": RUN_ERR_PROTOCOL, "fields": {"reason": "empty_response"}}

        first = parts[0].upper()

        if first == "OK":
            fields = {}
            for part in parts[1:]:
                if "=" in part:
                    key, val = part.split("=", 1)
                    fields[key] = val
            return True, {"code": RUN_ERR_OK, "fields": fields}
        elif first == "ERR":
            code = RUN_ERR_PROTOCOL
            fields = {}

            # Parse error code - supports both symbolic (RUN_ERR_TIMEOUT) and numeric (1)
            if len(parts) >= 2:
                code_str = parts[1]
                # Check for symbolic error code pattern RUN_ERR_*
                if code_str.startswith("RUN_ERR_"):
                    error_name = code_str.replace("RUN_ERR_", "")
                    error_map = {
                        "OK": RUN_ERR_OK,
                        "TIMEOUT": RUN_ERR_TIMEOUT,
                        "UNKNOWN": RUN_ERR_UNKNOWN,
                        "UNSUPPORTED_ROM": RUN_ERR_UNSUPPORTED_ROM,
                        "HOOK_UNCONFIGURED": RUN_ERR_HOOK_UNCONFIGURED,
                        "FILE_NOT_FOUND": RUN_ERR_FILE_NOT_FOUND,
                        "QUEUE_FULL": RUN_ERR_QUEUE_FULL,
                        "PROTOCOL": RUN_ERR_PROTOCOL,
                        "NOT_IN_MONITOR": RUN_ERR_NOT_IN_MONITOR,
                    }
                    code = error_map.get(error_name, RUN_ERR_PROTOCOL)
                    # Store symbolic token in fields for hybrid ERR frames
                    fields["symbol"] = code_str
                else:
                    # Try numeric code - look ahead for possible symbolic token
                    code_match = re.search(r'(\d+)', code_str)
                    code = int(code_match.group(1)) if code_match else RUN_ERR_PROTOCOL
                    
                    # Check if there's a symbolic token after the numeric code
                    for part in parts[2:]:
                        if part.startswith("RUN_ERR_"):
                            fields["symbol"] = part
                            break

            # Parse remaining fields - fields start at index 2 (after ERR and code)
            for part in parts[2:]:
                if "=" in part:
                    key, val = part.split("=", 1)
                    fields[key] = val
            return False, {"code": code, "fields": fields}
        elif first == "STATE":
            state_name = parts[1] if len(parts) > 1 else "UNKNOWN"
            fields = {"state": state_name}
            for part in parts[2:]:
                if "=" in part:
                    key, val = part.split("=", 1)
                    fields[key] = val
            return True, {"code": RUN_ERR_OK, "fields": fields}
        else:
            return False, {"code": RUN_ERR_PROTOCOL, "fields": {"reason": f"unknown_response: {response}"}}

    def send_command(self, cmd_name, *args):
        """
        Send a protocol command.

        @cmd_name: Command name (LOAD, QUEUE, GO, STATE, WAIT, QUIT)
        @args: Command arguments

        Returns:
            (ok, response_dict) from _parse_response
        """
        command = f"{cmd_name} {' '.join(str(a) for a in args)}"
        return self._send_command(command)

    def wait(self, timeout_ms):
        """Wait for a runtime state transition using the bridge's WAIT command."""
        return self.send_command("WAIT", max(int(timeout_ms), 1))

    def load(self, filepath, fmt="ASCII"):
        """Load a BASIC file."""
        return self.send_command("LOAD", filepath, fmt)

    def queue(self, sequence):
        """Queue keyboard input for injection."""
        return self.send_command("QUEUE", sequence)

    def go(self):
        """Start BASIC execution."""
        return self.send_command("GO")

    def state(self):
        """Query current state."""
        return self.send_command("STATE")

    def quit(self):
        """Stop emulator."""
        return self.send_command("QUIT")

    def textscr(self):
        """Capture the current text screen via the bridge."""
        ok, resp = self.send_command("TEXTSCR")
        if not ok:
            return ok, resp

        text_hex = resp.get("fields", {}).get("TEXTHEX", "")
        try:
            text = bytes.fromhex(text_hex).decode("utf-8", errors="replace")
        except ValueError:
            return False, {"code": RUN_ERR_PROTOCOL, "fields": {"reason": "invalid_texthex"}}

        resp["fields"]["text"] = text
        return True, resp

    def log_transcript(self, msg):
        """Log message to transcript."""
        self.transcript.append(msg)

    def save_transcript(self, path):
        """Save transcript to file."""
        with open(path, 'w') as f:
            f.write('\n'.join(self.transcript) + '\n')


def probe_bridge(control_socket_path=None):
    """
    Probe bridge availability by attempting a handshake.

    Tries to connect and send a minimal command to verify the bridge works.
    Returns (success, details) tuple.
    """
    socket_path = control_socket_path or "/tmp/n88basic-control.sock"

    try:
        session = RunControlSession(socket_path)
        if not session.connect():
            return False, {"reason": "connect_failed", "socket_path": socket_path}

        # Try a quick state query as handshake
        ok, result = session.state()
        session.disconnect()

        if ok:
            return True, {"reason": "handshake_ok", "state": result}
        else:
            return False, {"reason": "handshake_failed", "result": result}

    except Exception as e:
        return False, {"reason": "exception", "error": str(e)}


if __name__ == "__main__":
    import argparse
    import sys
    import json

    parser = argparse.ArgumentParser(description="QUASI88 Control Bridge Client")
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Probe bridge availability and exit with result as JSON"
    )
    parser.add_argument(
        "--control-socket",
        type=str,
        default=None,
        help="Path to control socket (default: /tmp/n88basic-control.sock)"
    )
    args = parser.parse_args()

    if args.probe:
        success, details = probe_bridge(args.control_socket)
        result = {"success": success, "details": details}
        print(json.dumps(result, indent=2))
        sys.exit(0 if success else 1)
    else:
        parser.print_help()
        sys.exit(1)
