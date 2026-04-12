from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="ascii")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _copy_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


class BasicRunnerTests(unittest.TestCase):

    def test_run_fm11basic_invokes_python_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_file(ROOT_DIR / "run/fm11basic.sh", temp_path / "run/fm11basic.sh")
            _copy_file(ROOT_DIR / "src/fm11basic.py", temp_path / "src/fm11basic.py")

            bin_dir = temp_path / "bin"
            python_log = temp_path / "python.log"
            _write_executable(
                bin_dir / "python3",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                printf '%s\\n' "$*" >"{python_log}"
                """,
            )

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            result = subprocess.run(
                ["bash", "run/fm11basic.sh", "--run", "--file", "demo.bas"],
                cwd=temp_path,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            logged = python_log.read_text(encoding="ascii")
            self.assertIn("-m fm11basic --run --file demo.bas", logged)

    def test_short_run_fm11basic_invokes_python_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_file(ROOT_DIR / "run/fm11basic.sh", temp_path / "run/fm11basic.sh")
            _copy_file(ROOT_DIR / "src/fm11basic.py", temp_path / "src/fm11basic.py")

            bin_dir = temp_path / "bin"
            python_log = temp_path / "python.log"
            _write_executable(
                bin_dir / "python3",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                printf '%s\\n' "$*" >"{python_log}"
                """,
            )

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            result = subprocess.run(
                ["bash", "run/fm11basic.sh", "-r", "-f", "demo.bas"],
                cwd=temp_path,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            logged = python_log.read_text(encoding="ascii")
            self.assertIn("-m fm11basic -r -f demo.bas", logged)

    def test_run_fm11basic_forwards_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_file(ROOT_DIR / "run/fm11basic.sh", temp_path / "run/fm11basic.sh")
            _copy_file(ROOT_DIR / "src/fm11basic.py", temp_path / "src/fm11basic.py")

            bin_dir = temp_path / "bin"
            python_log = temp_path / "python.log"
            _write_executable(
                bin_dir / "python3",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                printf '%s\\n' "$*" >"{python_log}"
                """,
            )

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            result = subprocess.run(
                ["bash", "run/fm11basic.sh", "--run", "--file", "demo.bas", "--timeout", "17.5"],
                cwd=temp_path,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            logged = python_log.read_text(encoding="ascii")
            self.assertIn("-m fm11basic --run --file demo.bas --timeout 17.5", logged)

    def test_run_qbasic_help_does_not_trigger_shell_command_substitution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_file(ROOT_DIR / "run/qbasic.sh", temp_path / "run/qbasic.sh")
            _copy_file(ROOT_DIR / "scripts/qbasic-common.sh", temp_path / "scripts/qbasic-common.sh")
            _copy_file(ROOT_DIR / "scripts/basic-file-common.sh", temp_path / "scripts/basic-file-common.sh")

            result = subprocess.run(
                ["bash", "run/qbasic.sh", "--help"],
                cwd=temp_path,
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Usage:", result.stdout)
            self.assertIn("'/RUN'", result.stdout)
            self.assertEqual(result.stderr, "")

    def test_run_fm7basic_without_file_invokes_interactive_helper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_file(ROOT_DIR / "run/fm7basic.sh", temp_path / "run/fm7basic.sh")
            _copy_file(ROOT_DIR / "scripts/fm7basic-common.sh", temp_path / "scripts/fm7basic-common.sh")
            _copy_file(ROOT_DIR / "src/fm7basic_cli.py", temp_path / "src/fm7basic_cli.py")
            _copy_file(ROOT_DIR / "src/fbasic_batch.py", temp_path / "src/fbasic_batch.py")

            romset = temp_path / "downloads/fm7basic/mame-roms/fm77av"
            romset.mkdir(parents=True)
            for name in ("initiate.rom", "fbasic30.rom", "subsys_a.rom", "subsys_b.rom", "subsys_c.rom", "subsyscg.rom", "kanji.rom"):
                (romset / name).write_bytes(b"rom")

            bin_dir = temp_path / "bin"
            python_log = temp_path / "python.log"
            _write_executable(
                bin_dir / "python3",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                printf '%s\\n' "$*" >"{python_log}"
                """,
            )
            _write_executable(bin_dir / "mame", "#!/usr/bin/env bash\nexit 0\n")

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            result = subprocess.run(
                ["bash", "run/fm7basic.sh", "-window"],
                cwd=temp_path,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            logged = python_log.read_text(encoding="ascii")
            self.assertIn("-m fm7basic_cli", logged)
            self.assertIn("--driver fm77av", logged)
            self.assertIn(f"--rompath {temp_path / 'downloads/fm7basic/mame-roms'}", logged)
            self.assertIn("-midiprovider none", logged)
            self.assertNotIn("--file", logged)
            self.assertNotIn("--run", logged)
            self.assertIn("-window", logged)

    def test_run_fm7basic_with_file_invokes_interactive_helper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_file(ROOT_DIR / "run/fm7basic.sh", temp_path / "run/fm7basic.sh")
            _copy_file(ROOT_DIR / "scripts/fm7basic-common.sh", temp_path / "scripts/fm7basic-common.sh")
            _copy_file(ROOT_DIR / "src/fm7basic_cli.py", temp_path / "src/fm7basic_cli.py")
            _copy_file(ROOT_DIR / "src/fbasic_batch.py", temp_path / "src/fbasic_batch.py")

            romset = temp_path / "downloads/fm7basic/mame-roms/fm77av"
            romset.mkdir(parents=True)
            for name in ("initiate.rom", "fbasic30.rom", "subsys_a.rom", "subsys_b.rom", "subsys_c.rom", "subsyscg.rom", "kanji.rom"):
                (romset / name).write_bytes(b"rom")

            bin_dir = temp_path / "bin"
            python_log = temp_path / "python.log"
            _write_executable(
                bin_dir / "python3",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                printf '%s\\n' "$*" >"{python_log}"
                """,
            )
            _write_executable(bin_dir / "mame", "#!/usr/bin/env bash\nexit 0\n")

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            result = subprocess.run(
                ["bash", "run/fm7basic.sh", "--file", "demo.bas", "-window"],
                cwd=temp_path,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            logged = python_log.read_text(encoding="ascii")
            self.assertIn("-m fm7basic_cli", logged)
            self.assertIn("-midiprovider none", logged)
            self.assertIn("--file demo.bas", logged)
            self.assertNotIn("--run", logged)
            self.assertIn("-window", logged)

    def test_run_fm7basic_with_short_file_invokes_interactive_helper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_file(ROOT_DIR / "run/fm7basic.sh", temp_path / "run/fm7basic.sh")
            _copy_file(ROOT_DIR / "scripts/fm7basic-common.sh", temp_path / "scripts/fm7basic-common.sh")
            _copy_file(ROOT_DIR / "src/fm7basic_cli.py", temp_path / "src/fm7basic_cli.py")
            _copy_file(ROOT_DIR / "src/fbasic_batch.py", temp_path / "src/fbasic_batch.py")

            romset = temp_path / "downloads/fm7basic/mame-roms/fm77av"
            romset.mkdir(parents=True)
            for name in ("initiate.rom", "fbasic30.rom", "subsys_a.rom", "subsys_b.rom", "subsys_c.rom", "subsyscg.rom", "kanji.rom"):
                (romset / name).write_bytes(b"rom")

            bin_dir = temp_path / "bin"
            python_log = temp_path / "python.log"
            _write_executable(
                bin_dir / "python3",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                printf '%s\\n' "$*" >"{python_log}"
                """,
            )
            _write_executable(bin_dir / "mame", "#!/usr/bin/env bash\nexit 0\n")

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            result = subprocess.run(
                ["bash", "run/fm7basic.sh", "-f", "demo.bas", "-window"],
                cwd=temp_path,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            logged = python_log.read_text(encoding="ascii")
            self.assertIn("-m fm7basic_cli", logged)
            self.assertIn("--file demo.bas", logged)
            self.assertNotIn("--run", logged)
            self.assertIn("-window", logged)

    def test_run_fm7basic_batch_invokes_python_helper_with_fm77av_driver(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_file(ROOT_DIR / "run/fm7basic.sh", temp_path / "run/fm7basic.sh")
            _copy_file(ROOT_DIR / "scripts/fm7basic-common.sh", temp_path / "scripts/fm7basic-common.sh")
            _copy_file(ROOT_DIR / "src/fbasic_batch.py", temp_path / "src/fbasic_batch.py")
            _copy_file(ROOT_DIR / "src/fm7basic_cli.py", temp_path / "src/fm7basic_cli.py")

            romset = temp_path / "downloads/fm7basic/mame-roms/fm77av"
            romset.mkdir(parents=True)
            for name in ("initiate.rom", "fbasic30.rom", "subsys_a.rom", "subsys_b.rom", "subsys_c.rom", "subsyscg.rom", "kanji.rom"):
                (romset / name).write_bytes(b"rom")

            bin_dir = temp_path / "bin"
            python_log = temp_path / "python.log"
            _write_executable(
                bin_dir / "python3",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                printf '%s\\n' "$*" >"{python_log}"
                """,
            )
            _write_executable(bin_dir / "mame", "#!/usr/bin/env bash\nexit 0\n")

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            result = subprocess.run(
                ["bash", "run/fm7basic.sh", "--run", "--file", "demo.bas", "--timeout", "17.5", "-window"],
                cwd=temp_path,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            logged = python_log.read_text(encoding="ascii")
            self.assertIn("-m fm7basic_cli", logged)
            self.assertIn("--driver fm77av", logged)
            self.assertIn(f"--rompath {temp_path / 'downloads/fm7basic/mame-roms'}", logged)
            self.assertIn("-midiprovider none", logged)
            self.assertIn("--file demo.bas", logged)
            self.assertIn("--run", logged)
            self.assertIn("--timeout 17.5", logged)
            self.assertIn("-window", logged)

    def test_run_fm7basic_batch_accepts_short_run_and_file_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_file(ROOT_DIR / "run/fm7basic.sh", temp_path / "run/fm7basic.sh")
            _copy_file(ROOT_DIR / "scripts/fm7basic-common.sh", temp_path / "scripts/fm7basic-common.sh")
            _copy_file(ROOT_DIR / "src/fbasic_batch.py", temp_path / "src/fbasic_batch.py")
            _copy_file(ROOT_DIR / "src/fm7basic_cli.py", temp_path / "src/fm7basic_cli.py")

            romset = temp_path / "downloads/fm7basic/mame-roms/fm77av"
            romset.mkdir(parents=True)
            for name in ("initiate.rom", "fbasic30.rom", "subsys_a.rom", "subsys_b.rom", "subsys_c.rom", "subsyscg.rom", "kanji.rom"):
                (romset / name).write_bytes(b"rom")

            bin_dir = temp_path / "bin"
            python_log = temp_path / "python.log"
            _write_executable(
                bin_dir / "python3",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                printf '%s\\n' "$*" >"{python_log}"
                """,
            )
            _write_executable(bin_dir / "mame", "#!/usr/bin/env bash\nexit 0\n")

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            result = subprocess.run(
                ["bash", "run/fm7basic.sh", "-r", "-f", "demo.bas", "--timeout", "17.5", "-window"],
                cwd=temp_path,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            logged = python_log.read_text(encoding="ascii")
            self.assertIn("-m fm7basic_cli", logged)
            self.assertIn("--file demo.bas", logged)
            self.assertIn("--run", logged)
            self.assertIn("--timeout 17.5", logged)
            self.assertIn("-window", logged)
