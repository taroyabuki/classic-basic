from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
RUNNER = ROOT_DIR / "run/msxbasic.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content), encoding="ascii")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class MsxBasicRunnerTests(unittest.TestCase):
    def test_file_flag_loads_program_interactively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            bin_dir = temp_path / "bin"
            bin_dir.mkdir()
            log_path = temp_path / "python3.log"
            _write_executable(bin_dir / "openmsx", "#!/usr/bin/env bash\nexit 0\n")
            _write_executable(
                bin_dir / "python3",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                printf '%s\\n' "$*" >"{log_path}"
                """,
            )

            program = temp_path / "prog.bas"
            program.write_text("10 PRINT 42\n", encoding="ascii")

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"

            result = subprocess.run(
                ["bash", str(RUNNER), "--file", str(program)],
                cwd=ROOT_DIR,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                log_path.read_text(encoding="ascii").strip(),
                f"-m msx_basic.cli --interactive {program}",
            )

    def test_run_flag_executes_program_in_batch_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            bin_dir = temp_path / "bin"
            bin_dir.mkdir()
            log_path = temp_path / "python3.log"
            _write_executable(bin_dir / "openmsx", "#!/usr/bin/env bash\nexit 0\n")
            _write_executable(
                bin_dir / "python3",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                printf '%s\\n' "$*" >"{log_path}"
                """,
            )

            program = temp_path / "prog.bas"
            program.write_text("10 PRINT 42\n", encoding="ascii")

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"

            result = subprocess.run(
                ["bash", str(RUNNER), "--run", "--file", str(program)],
                cwd=ROOT_DIR,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                log_path.read_text(encoding="ascii").strip(),
                f"-m msx_basic.cli {program}",
            )

    def test_run_flag_with_timeout_exports_batch_timeout_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            bin_dir = temp_path / "bin"
            bin_dir.mkdir()
            log_path = temp_path / "python3.log"
            env_log_path = temp_path / "python3.env.log"
            _write_executable(bin_dir / "openmsx", "#!/usr/bin/env bash\nexit 0\n")
            _write_executable(
                bin_dir / "python3",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                printf '%s\\n' "$*" >"{log_path}"
                printf '%s\\n' "${{CLASSIC_BASIC_MSX_BATCH_TIMEOUT:-}}" >"{env_log_path}"
                """,
            )

            program = temp_path / "prog.bas"
            program.write_text("10 PRINT 42\n", encoding="ascii")

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"

            result = subprocess.run(
                ["bash", str(RUNNER), "--run", "--file", str(program), "--timeout", "17.5"],
                cwd=ROOT_DIR,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                log_path.read_text(encoding="ascii").strip(),
                f"-m msx_basic.cli {program}",
            )
            self.assertEqual(env_log_path.read_text(encoding="ascii").strip(), "17.5")


if __name__ == "__main__":
    unittest.main()
