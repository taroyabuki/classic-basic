from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def _write_executable(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content), encoding="ascii")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class DosRunnerWrapperTests(unittest.TestCase):
    maxDiff = None

    def test_qbasic_plain_launch_uses_interactive_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, logs = self._make_test_env(temp_path)
            env.update(
                {
                    "CLASSIC_BASIC_QBASIC_ARCHIVE": str(temp_path / "downloads/qbasic/qbasic-1.1.zip"),
                    "CLASSIC_BASIC_QBASIC_EXE": str(temp_path / "downloads/qbasic/QBASIC.EXE"),
                    "PYTHONPATH": str(ROOT_DIR / "src"),
                }
            )

            result, runtime_dir = self._run_wrapper(
                env=env,
                runtime_name="qbasic-runtime",
                home_name="qbasic-home",
                runner=ROOT_DIR / "run/qbasic.sh",
                source_name="unused.bas",
                run_mode=False,
                with_file=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            dosemu_args = logs["dosemu"].read_text(encoding="ascii").splitlines()
            self.assertEqual(len(dosemu_args), 1)
            self.assertIn("-E qbasic", dosemu_args[0])
            self.assertIn(f"--Fdrive_c {runtime_dir / 'drive_c'}", dosemu_args[0])

    def test_qbasic_file_launch_stages_program_for_interactive_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, logs = self._make_test_env(temp_path)
            env.update(
                {
                    "CLASSIC_BASIC_QBASIC_ARCHIVE": str(temp_path / "downloads/qbasic/qbasic-1.1.zip"),
                    "CLASSIC_BASIC_QBASIC_EXE": str(temp_path / "downloads/qbasic/QBASIC.EXE"),
                    "PYTHONPATH": str(ROOT_DIR / "src"),
                }
            )

            result, runtime_dir = self._run_wrapper(
                env=env,
                runtime_name="qbasic-runtime",
                home_name="qbasic-home",
                runner=ROOT_DIR / "run/qbasic.sh",
                source_name="qbasic.bas",
                run_mode=False,
                source_text='10 PRINT "X"\n20 END\n',
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            dosemu_args = logs["dosemu"].read_text(encoding="ascii").splitlines()
            self.assertEqual(len(dosemu_args), 1)
            self.assertIn("-E qbasic RUNFILE.BAS", dosemu_args[0])
            self.assertEqual(
                (runtime_dir / "drive_c" / "RUNFILE.BAS").read_bytes(),
                b'10 PRINT "X"\r\n20 END\r\n',
            )

    def test_qbasic_short_file_launch_stages_program_for_interactive_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, logs = self._make_test_env(temp_path)
            env.update(
                {
                    "CLASSIC_BASIC_QBASIC_ARCHIVE": str(temp_path / "downloads/qbasic/qbasic-1.1.zip"),
                    "CLASSIC_BASIC_QBASIC_EXE": str(temp_path / "downloads/qbasic/QBASIC.EXE"),
                    "PYTHONPATH": str(ROOT_DIR / "src"),
                }
            )

            result, runtime_dir = self._run_wrapper(
                env=env,
                runtime_name="qbasic-runtime",
                home_name="qbasic-home",
                runner=ROOT_DIR / "run/qbasic.sh",
                source_name="qbasic.bas",
                run_mode=False,
                source_text='10 PRINT "X"\n20 END\n',
                use_short_aliases=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            dosemu_args = logs["dosemu"].read_text(encoding="ascii").splitlines()
            self.assertEqual(len(dosemu_args), 1)
            self.assertIn("-E qbasic RUNFILE.BAS", dosemu_args[0])
            self.assertEqual(
                (runtime_dir / "drive_c" / "RUNFILE.BAS").read_bytes(),
                b'10 PRINT "X"\r\n20 END\r\n',
            )

    def test_qbasic_file_launch_accepts_cr_line_endings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, _ = self._make_test_env(temp_path)
            env.update(
                {
                    "CLASSIC_BASIC_QBASIC_ARCHIVE": str(temp_path / "downloads/qbasic/qbasic-1.1.zip"),
                    "CLASSIC_BASIC_QBASIC_EXE": str(temp_path / "downloads/qbasic/QBASIC.EXE"),
                    "PYTHONPATH": str(ROOT_DIR / "src"),
                }
            )

            result, runtime_dir = self._run_wrapper(
                env=env,
                runtime_name="qbasic-runtime",
                home_name="qbasic-home",
                runner=ROOT_DIR / "run/qbasic.sh",
                source_name="qbasic.bas",
                run_mode=False,
                source_bytes=b'10 PRINT "X"\r20 END\r',
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (runtime_dir / "drive_c" / "RUNFILE.BAS").read_bytes(),
                b'10 PRINT "X"\r\n20 END\r\n',
            )

    def test_qbasic_run_requires_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, _ = self._make_test_env(temp_path)
            env.update(
                {
                    "CLASSIC_BASIC_QBASIC_ARCHIVE": str(temp_path / "downloads/qbasic/qbasic-1.1.zip"),
                    "CLASSIC_BASIC_QBASIC_EXE": str(temp_path / "downloads/qbasic/QBASIC.EXE"),
                    "PYTHONPATH": str(ROOT_DIR / "src"),
                }
            )

            result, _ = self._run_wrapper(
                env=env,
                runtime_name="qbasic-runtime",
                home_name="qbasic-home",
                runner=ROOT_DIR / "run/qbasic.sh",
                source_name="unused.bas",
                with_file=False,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("--run requires --file PROGRAM.bas", result.stderr)

    def test_qbasic_timeout_requires_run_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, _ = self._make_test_env(temp_path)
            env.update(
                {
                    "CLASSIC_BASIC_QBASIC_ARCHIVE": str(temp_path / "downloads/qbasic/qbasic-1.1.zip"),
                    "CLASSIC_BASIC_QBASIC_EXE": str(temp_path / "downloads/qbasic/QBASIC.EXE"),
                    "PYTHONPATH": str(ROOT_DIR / "src"),
                }
            )

            result, _ = self._run_wrapper(
                env=env,
                runtime_name="qbasic-runtime",
                home_name="qbasic-home",
                runner=ROOT_DIR / "run/qbasic.sh",
                source_name="unused.bas",
                run_mode=False,
                with_file=False,
                extra_args=["--timeout", "12"],
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("--timeout requires --run --file PROGRAM.bas", result.stderr)

    def test_qbasic_file_and_command_are_mutually_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, _ = self._make_test_env(temp_path)
            env.update(
                {
                    "CLASSIC_BASIC_QBASIC_ARCHIVE": str(temp_path / "downloads/qbasic/qbasic-1.1.zip"),
                    "CLASSIC_BASIC_QBASIC_EXE": str(temp_path / "downloads/qbasic/QBASIC.EXE"),
                    "PYTHONPATH": str(ROOT_DIR / "src"),
                }
            )

            result, _ = self._run_wrapper(
                env=env,
                runtime_name="qbasic-runtime",
                home_name="qbasic-home",
                runner=ROOT_DIR / "run/qbasic.sh",
                source_name="qbasic.bas",
                run_mode=False,
                extra_args=["--command", "edit"],
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("--file cannot be combined with --command", result.stderr)

    def test_qbasic_run_file_rejects_program_input_before_starting_dosemu(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, logs = self._make_test_env(temp_path)
            env.update(
                {
                    "CLASSIC_BASIC_QBASIC_ARCHIVE": str(temp_path / "downloads/qbasic/qbasic-1.1.zip"),
                    "CLASSIC_BASIC_QBASIC_EXE": str(temp_path / "downloads/qbasic/QBASIC.EXE"),
                    "PYTHONPATH": str(ROOT_DIR / "src"),
                }
            )

            result, runtime_dir = self._run_wrapper(
                env=env,
                runtime_name="qbasic-runtime",
                home_name="qbasic-home",
                runner=ROOT_DIR / "run/qbasic.sh",
                source_name="input.bas",
                source_text='10 INPUT "N";A\n20 END\n',
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("program input is not supported in --run mode", result.stderr)
            self.assertFalse(logs["dosemu"].exists())
            self.assertEqual(
                (runtime_dir / "drive_c" / "RUNFILE.BAS").read_bytes(),
                b'10 INPUT "N";A\r\n20 END\r\n',
            )

    def test_qbasic_short_run_file_rejects_program_input_before_starting_dosemu(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, logs = self._make_test_env(temp_path)
            env.update(
                {
                    "CLASSIC_BASIC_QBASIC_ARCHIVE": str(temp_path / "downloads/qbasic/qbasic-1.1.zip"),
                    "CLASSIC_BASIC_QBASIC_EXE": str(temp_path / "downloads/qbasic/QBASIC.EXE"),
                    "PYTHONPATH": str(ROOT_DIR / "src"),
                }
            )

            result, runtime_dir = self._run_wrapper(
                env=env,
                runtime_name="qbasic-runtime",
                home_name="qbasic-home",
                runner=ROOT_DIR / "run/qbasic.sh",
                source_name="input.bas",
                source_text='10 INPUT "N";A\n20 END\n',
                use_short_aliases=True,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("program input is not supported in --run mode", result.stderr)
            self.assertFalse(logs["dosemu"].exists())
            self.assertEqual(
                (runtime_dir / "drive_c" / "RUNFILE.BAS").read_bytes(),
                b'10 INPUT "N";A\r\n20 END\r\n',
            )

    def test_gwbasic_plain_launch_uses_interactive_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, logs = self._make_test_env(temp_path)
            env.update(
                {
                    "CLASSIC_BASIC_GWBASIC_ARCHIVE": str(temp_path / "downloads/gwbasic/gwbasic-3.23.7z"),
                    "CLASSIC_BASIC_GWBASIC_EXE": str(temp_path / "downloads/gwbasic/GWBASIC.EXE"),
                }
            )

            result, runtime_dir = self._run_wrapper(
                env=env,
                runtime_name="gwbasic-runtime",
                home_name="gwbasic-home",
                runner=ROOT_DIR / "run/gwbasic.sh",
                source_name="unused.bas",
                run_mode=False,
                with_file=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            dosemu_args = logs["dosemu"].read_text(encoding="ascii").splitlines()
            self.assertEqual(len(dosemu_args), 1)
            self.assertIn("-E gwbasic", dosemu_args[0])
            self.assertIn(f"--Fdrive_c {runtime_dir / 'drive_c'}", dosemu_args[0])

    def test_gwbasic_file_launch_stages_program_for_interactive_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, logs = self._make_test_env(temp_path)
            env.update(
                {
                    "CLASSIC_BASIC_GWBASIC_ARCHIVE": str(temp_path / "downloads/gwbasic/gwbasic-3.23.7z"),
                    "CLASSIC_BASIC_GWBASIC_EXE": str(temp_path / "downloads/gwbasic/GWBASIC.EXE"),
                }
            )

            result, runtime_dir = self._run_wrapper(
                env=env,
                runtime_name="gwbasic-runtime",
                home_name="gwbasic-home",
                runner=ROOT_DIR / "run/gwbasic.sh",
                source_name="gwbasic.bas",
                run_mode=False,
                source_text='10 PRINT "X"\n20 END\n',
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            dosemu_args = logs["dosemu"].read_text(encoding="ascii").splitlines()
            self.assertEqual(len(dosemu_args), 1)
            self.assertIn("-E gwbasic RUNFILE.BAS", dosemu_args[0])
            self.assertEqual(
                (runtime_dir / "drive_c" / "RUNFILE.BAS").read_bytes(),
                b'10 PRINT "X"\r\n20 END\r\n',
            )

    def test_gwbasic_short_file_launch_stages_program_for_interactive_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, logs = self._make_test_env(temp_path)
            env.update(
                {
                    "CLASSIC_BASIC_GWBASIC_ARCHIVE": str(temp_path / "downloads/gwbasic/gwbasic-3.23.7z"),
                    "CLASSIC_BASIC_GWBASIC_EXE": str(temp_path / "downloads/gwbasic/GWBASIC.EXE"),
                }
            )

            result, runtime_dir = self._run_wrapper(
                env=env,
                runtime_name="gwbasic-runtime",
                home_name="gwbasic-home",
                runner=ROOT_DIR / "run/gwbasic.sh",
                source_name="gwbasic.bas",
                run_mode=False,
                source_text='10 PRINT "X"\n20 END\n',
                use_short_aliases=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            dosemu_args = logs["dosemu"].read_text(encoding="ascii").splitlines()
            self.assertEqual(len(dosemu_args), 1)
            self.assertIn("-E gwbasic RUNFILE.BAS", dosemu_args[0])
            self.assertEqual(
                (runtime_dir / "drive_c" / "RUNFILE.BAS").read_bytes(),
                b'10 PRINT "X"\r\n20 END\r\n',
            )

    def test_gwbasic_file_launch_accepts_cr_line_endings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, _ = self._make_test_env(temp_path)
            env.update(
                {
                    "CLASSIC_BASIC_GWBASIC_ARCHIVE": str(temp_path / "downloads/gwbasic/gwbasic-3.23.7z"),
                    "CLASSIC_BASIC_GWBASIC_EXE": str(temp_path / "downloads/gwbasic/GWBASIC.EXE"),
                }
            )

            result, runtime_dir = self._run_wrapper(
                env=env,
                runtime_name="gwbasic-runtime",
                home_name="gwbasic-home",
                runner=ROOT_DIR / "run/gwbasic.sh",
                source_name="gwbasic.bas",
                run_mode=False,
                source_bytes=b'10 PRINT "X"\r20 END\r',
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (runtime_dir / "drive_c" / "RUNFILE.BAS").read_bytes(),
                b'10 PRINT "X"\r\n20 END\r\n',
            )

    def test_gwbasic_run_requires_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, _ = self._make_test_env(temp_path)
            env.update(
                {
                    "CLASSIC_BASIC_GWBASIC_ARCHIVE": str(temp_path / "downloads/gwbasic/gwbasic-3.23.7z"),
                    "CLASSIC_BASIC_GWBASIC_EXE": str(temp_path / "downloads/gwbasic/GWBASIC.EXE"),
                }
            )

            result, _ = self._run_wrapper(
                env=env,
                runtime_name="gwbasic-runtime",
                home_name="gwbasic-home",
                runner=ROOT_DIR / "run/gwbasic.sh",
                source_name="unused.bas",
                with_file=False,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("--run requires --file PROGRAM.bas", result.stderr)

    def test_gwbasic_timeout_requires_run_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, _ = self._make_test_env(temp_path)
            env.update(
                {
                    "CLASSIC_BASIC_GWBASIC_ARCHIVE": str(temp_path / "downloads/gwbasic/gwbasic-3.23.7z"),
                    "CLASSIC_BASIC_GWBASIC_EXE": str(temp_path / "downloads/gwbasic/GWBASIC.EXE"),
                }
            )

            result, _ = self._run_wrapper(
                env=env,
                runtime_name="gwbasic-runtime",
                home_name="gwbasic-home",
                runner=ROOT_DIR / "run/gwbasic.sh",
                source_name="unused.bas",
                run_mode=False,
                with_file=False,
                extra_args=["--timeout", "12"],
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("--timeout requires --run --file PROGRAM.bas", result.stderr)

    def test_gwbasic_run_file_rejects_program_input_before_starting_dosemu(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, logs = self._make_test_env(temp_path)
            env.update(
                {
                    "CLASSIC_BASIC_GWBASIC_ARCHIVE": str(temp_path / "downloads/gwbasic/gwbasic-3.23.7z"),
                    "CLASSIC_BASIC_GWBASIC_EXE": str(temp_path / "downloads/gwbasic/GWBASIC.EXE"),
                }
            )

            result, runtime_dir = self._run_wrapper(
                env=env,
                runtime_name="gwbasic-runtime",
                home_name="gwbasic-home",
                runner=ROOT_DIR / "run/gwbasic.sh",
                source_name="input.bas",
                source_text='10 INPUT "N";A\n20 END\n',
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("program input is not supported in --run mode", result.stderr)
            self.assertFalse(logs["dosemu"].exists())
            self.assertEqual(
                (runtime_dir / "drive_c" / "RUNFILE.BAS").read_bytes(),
                b'10 INPUT "N";A\r\n20 END\r\n',
            )

    def test_gwbasic_short_run_file_rejects_program_input_before_starting_dosemu(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, logs = self._make_test_env(temp_path)
            env.update(
                {
                    "CLASSIC_BASIC_GWBASIC_ARCHIVE": str(temp_path / "downloads/gwbasic/gwbasic-3.23.7z"),
                    "CLASSIC_BASIC_GWBASIC_EXE": str(temp_path / "downloads/gwbasic/GWBASIC.EXE"),
                }
            )

            result, runtime_dir = self._run_wrapper(
                env=env,
                runtime_name="gwbasic-runtime",
                home_name="gwbasic-home",
                runner=ROOT_DIR / "run/gwbasic.sh",
                source_name="input.bas",
                source_text='10 INPUT "N";A\n20 END\n',
                use_short_aliases=True,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("program input is not supported in --run mode", result.stderr)
            self.assertFalse(logs["dosemu"].exists())
            self.assertEqual(
                (runtime_dir / "drive_c" / "RUNFILE.BAS").read_bytes(),
                b'10 INPUT "N";A\r\n20 END\r\n',
            )

    def test_gwbasic_file_run_collects_redirected_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, logs = self._make_test_env(temp_path)
            env.update(
                {
                    "CLASSIC_BASIC_GWBASIC_ARCHIVE": str(temp_path / "downloads/gwbasic/gwbasic-3.23.7z"),
                    "CLASSIC_BASIC_GWBASIC_EXE": str(temp_path / "downloads/gwbasic/GWBASIC.EXE"),
                    "FAKE_DOS_CAPTURE_CONTENT": (
                        "Running dosemu2 with root privs is recommended with: dosemu -s\r\n"
                        "FOUND  245850922 / 78256779\r\n"
                        "TARGET\r\n"
                        " 24  45  68  84  251  33  9  64\r\n"
                    ),
                    "FAKE_EXPECT_FAIL_ON_CALL": "1",
                    "FAKE_SCRIPT_FAIL_ON_CALL": "1",
                    "CLASSIC_BASIC_DOSEMU_PTY": "off",
                }
            )

            result, runtime_dir = self._run_wrapper(
                env=env,
                runtime_name="gwbasic-runtime",
                home_name="gwbasic-home",
                runner=ROOT_DIR / "run/gwbasic.sh",
                source_name="gwbasic.bas",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    "FOUND 245850922 / 78256779",
                    "TARGET",
                    "24 45 68 84 251 33 9 64",
                ],
            )

            dosemu_args = logs["dosemu"].read_text(encoding="ascii").splitlines()
            self.assertEqual(len(dosemu_args), 1)
            self.assertNotIn("-dumb", dosemu_args[0])
            self.assertIn("-ks", dosemu_args[0])
            self.assertIn(f"--Fdrive_c {runtime_dir / 'drive_c'}", dosemu_args[0])
            self.assertIn("-E RUNBAS", dosemu_args[0])
            self.assertFalse(logs["expect"].exists())
            self.assertFalse(logs["script"].exists())

    def test_qbasic_file_run_collects_redirected_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, logs = self._make_test_env(temp_path)
            env.update(
                {
                    "CLASSIC_BASIC_QBASIC_ARCHIVE": str(temp_path / "downloads/qbasic/qbasic-1.1.zip"),
                    "CLASSIC_BASIC_QBASIC_EXE": str(temp_path / "downloads/qbasic/QBASIC.EXE"),
                    "FAKE_DOS_CAPTURE_CONTENT": (
                        "Immediate\r\n"
                        "FOUND  657408909 / 209259755\r\n"
                        "TARGET\r\n"
                        " 194  104  33  162  218  15  73  130\r\n"
                    ),
                    "FAKE_EXPECT_FAIL_ON_CALL": "1",
                    "FAKE_SCRIPT_FAIL_ON_CALL": "1",
                    "CLASSIC_BASIC_DOSEMU_PTY": "off",
                }
            )

            result, runtime_dir = self._run_wrapper(
                env=env,
                runtime_name="qbasic-runtime",
                home_name="qbasic-home",
                runner=ROOT_DIR / "run/qbasic.sh",
                source_name="qbasic.bas",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    "FOUND 657408909 / 209259755",
                    "TARGET",
                    "194 104 33 162 218 15 73 130",
                ],
            )

            dosemu_args = logs["dosemu"].read_text(encoding="ascii").splitlines()
            self.assertEqual(len(dosemu_args), 1)
            self.assertNotIn("-dumb", dosemu_args[0])
            self.assertIn("-ks", dosemu_args[0])
            self.assertIn(f"--Fdrive_c {runtime_dir / 'drive_c'}", dosemu_args[0])
            self.assertIn("-E RUNQB", dosemu_args[0])
            self.assertFalse(logs["expect"].exists())
            self.assertFalse(logs["script"].exists())
            self.assertEqual(
                (runtime_dir / "drive_c" / "BOOT.BAS").read_text(encoding="ascii"),
                '10 RUN "RUNFILE.BAS"\n',
            )

    def test_gwbasic_file_run_collects_out_txt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, _ = self._make_test_env(temp_path)
            env.update(
                {
                    "CLASSIC_BASIC_GWBASIC_ARCHIVE": str(temp_path / "downloads/gwbasic/gwbasic-3.23.7z"),
                    "CLASSIC_BASIC_GWBASIC_EXE": str(temp_path / "downloads/gwbasic/GWBASIC.EXE"),
                    "FAKE_DOS_OUT_TXT_CONTENT": (
                        "BEST\r\n"
                        " 194  104  33  162  218  15  73  130 \r\n"
                        "HIGH 11\r\n"
                    ),
                    "CLASSIC_BASIC_DOSEMU_PTY": "off",
                }
            )

            result, _ = self._run_wrapper(
                env=env,
                runtime_name="gwbasic-runtime",
                home_name="gwbasic-home",
                runner=ROOT_DIR / "run/gwbasic.sh",
                source_name="gwbasic.bas",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertEqual(
                result.stdout.splitlines(),
                ["BEST", "194 104 33 162 218 15 73 130", "HIGH 11"],
            )

    def test_qbasic_file_run_collects_out_txt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, _ = self._make_test_env(temp_path)
            env.update(
                {
                    "CLASSIC_BASIC_QBASIC_ARCHIVE": str(temp_path / "downloads/qbasic/qbasic-1.1.zip"),
                    "CLASSIC_BASIC_QBASIC_EXE": str(temp_path / "downloads/qbasic/QBASIC.EXE"),
                    "FAKE_DOS_OUT_TXT_CONTENT": (
                        "BEST\r\n"
                        " 24  45  68  84  251  33  9  64 \r\n"
                        "LOW NONE\r\n"
                    ),
                    "CLASSIC_BASIC_DOSEMU_PTY": "off",
                }
            )

            result, _ = self._run_wrapper(
                env=env,
                runtime_name="qbasic-runtime",
                home_name="qbasic-home",
                runner=ROOT_DIR / "run/qbasic.sh",
                source_name="qbasic.bas",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertEqual(
                result.stdout.splitlines(),
                ["BEST", "24 45 68 84 251 33 9 64", "LOW NONE"],
            )

    def test_gwbasic_file_run_forces_non_pty_for_quiet_batch_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, logs = self._make_test_env(temp_path)
            env.update(
                {
                    "CLASSIC_BASIC_GWBASIC_ARCHIVE": str(temp_path / "downloads/gwbasic/gwbasic-3.23.7z"),
                    "CLASSIC_BASIC_GWBASIC_EXE": str(temp_path / "downloads/gwbasic/GWBASIC.EXE"),
                    "FAKE_DOS_CAPTURE_CONTENT": "PTY OUTPUT\r\n",
                    "CLASSIC_BASIC_DOSEMU_PTY": "on",
                }
            )

            result, _ = self._run_wrapper(
                env=env,
                runtime_name="gwbasic-runtime",
                home_name="gwbasic-home",
                runner=ROOT_DIR / "run/gwbasic.sh",
                source_name="gwbasic.bas",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), ["PTY OUTPUT"])
            self.assertEqual(result.stderr, "")
            self.assertFalse(logs["script"].exists(), "batch run should avoid PTY noise")

    def test_gwbasic_file_run_avoids_auto_pty_for_quiet_batch_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, logs = self._make_test_env(temp_path)
            env.update(
                {
                    "CLASSIC_BASIC_GWBASIC_ARCHIVE": str(temp_path / "downloads/gwbasic/gwbasic-3.23.7z"),
                    "CLASSIC_BASIC_GWBASIC_EXE": str(temp_path / "downloads/gwbasic/GWBASIC.EXE"),
                    "FAKE_DOS_CAPTURE_CONTENT": "AUTO PTY\r\n",
                }
            )

            result, _ = self._run_wrapper(
                env=env,
                runtime_name="gwbasic-runtime",
                home_name="gwbasic-home",
                runner=ROOT_DIR / "run/gwbasic.sh",
                source_name="gwbasic.bas",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), ["AUTO PTY"])
            self.assertEqual(result.stderr, "")
            self.assertFalse(logs["script"].exists(), "batch run should avoid PTY noise")

    def test_gwbasic_timeout_option_exports_batch_timeout_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, logs = self._make_test_env(temp_path)
            timeout_env_log = temp_path / "dosemu-timeout-env.log"
            env.update(
                {
                    "CLASSIC_BASIC_GWBASIC_ARCHIVE": str(temp_path / "downloads/gwbasic/gwbasic-3.23.7z"),
                    "CLASSIC_BASIC_GWBASIC_EXE": str(temp_path / "downloads/gwbasic/GWBASIC.EXE"),
                    "FAKE_DOS_CAPTURE_CONTENT": "TIMEOUT TEST\r\n",
                    "FAKE_DOSEMU_TIMEOUT_ENV_LOG": str(timeout_env_log),
                    "CLASSIC_BASIC_DOSEMU_PTY": "off",
                }
            )

            result, _ = self._run_wrapper(
                env=env,
                runtime_name="gwbasic-runtime",
                home_name="gwbasic-home",
                runner=ROOT_DIR / "run/gwbasic.sh",
                source_name="gwbasic.bas",
                extra_args=["--timeout", "17.5"],
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), ["TIMEOUT TEST"])
            self.assertEqual(timeout_env_log.read_text(encoding="ascii").strip(), "17.5")
            self.assertTrue(logs["dosemu"].exists())

    def test_gwbasic_file_run_falls_back_to_terminal_output(self) -> None:
        """When CBATCH.TXT is absent (e.g. EMUFS writes fail), the wrapper
        should fall back to filtering dosemu's own terminal output."""
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, _ = self._make_test_env(temp_path)
            env.update(
                {
                    "CLASSIC_BASIC_GWBASIC_ARCHIVE": str(temp_path / "downloads/gwbasic/gwbasic-3.23.7z"),
                    "CLASSIC_BASIC_GWBASIC_EXE": str(temp_path / "downloads/gwbasic/GWBASIC.EXE"),
                    # No FAKE_DOS_CAPTURE_CONTENT → CBATCH.TXT will not be created.
                    # Terminal output simulates dosemu rendering noise + BASIC output.
                    "FAKE_DOSEMU_STDOUT": (
                        "dosemu2 2.0pre9\r\n"
                        "Welcome to dosemu2!\r\n"
                        "FOUND  245850922 / 78256779\r\n"
                        "TARGET\r\n"
                        " 24  45  68  84  251  33  9  64\r\n"
                    ),
                    "CLASSIC_BASIC_DOSEMU_PTY": "off",
                }
            )

            result, _ = self._run_wrapper(
                env=env,
                runtime_name="gwbasic-runtime",
                home_name="gwbasic-home",
                runner=ROOT_DIR / "run/gwbasic.sh",
                source_name="gwbasic.bas",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    "FOUND 245850922 / 78256779",
                    "TARGET",
                    "24 45 68 84 251 33 9 64",
                ],
            )

    def test_wrapper_returns_dosemu_failure_after_printing_captured_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, _ = self._make_test_env(temp_path)
            env.update(
                {
                    "CLASSIC_BASIC_GWBASIC_ARCHIVE": str(temp_path / "downloads/gwbasic/gwbasic-3.23.7z"),
                    "CLASSIC_BASIC_GWBASIC_EXE": str(temp_path / "downloads/gwbasic/GWBASIC.EXE"),
                    "FAKE_DOS_CAPTURE_CONTENT": "RESULT\r\n",
                    "FAKE_DOSEMU_EXIT_STATUS": "7",
                    "CLASSIC_BASIC_DOSEMU_PTY": "off",
                }
            )

            result, _ = self._run_wrapper(
                env=env,
                runtime_name="gwbasic-runtime",
                home_name="gwbasic-home",
                runner=ROOT_DIR / "run/gwbasic.sh",
                source_name="gwbasic.bas",
            )

            self.assertEqual(result.returncode, 7)
            self.assertEqual(result.stdout.splitlines(), ["RESULT"])

    def _make_test_env(self, temp_path: Path) -> tuple[dict[str, str], dict[str, Path]]:
        bin_dir = temp_path / "bin"
        bin_dir.mkdir()

        logs = {
            "dosemu": temp_path / "dosemu-args.log",
            "expect": temp_path / "expect.log",
            "script": temp_path / "script.log",
        }

        self._write_fake_commands(bin_dir)
        self._prepare_downloads(temp_path)

        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env["CLASSIC_BASIC_DOSEMU_SEM_SHIM"] = "off"
        env["FAKE_DOSEMU_ARGS_LOG"] = str(logs["dosemu"])
        env["FAKE_EXPECT_LOG"] = str(logs["expect"])
        env["FAKE_SCRIPT_LOG"] = str(logs["script"])
        return env, logs

    def _prepare_downloads(self, temp_path: Path) -> None:
        qbasic_dir = temp_path / "downloads/qbasic"
        gwbasic_dir = temp_path / "downloads/gwbasic"
        qbasic_dir.mkdir(parents=True)
        gwbasic_dir.mkdir(parents=True)
        (qbasic_dir / "qbasic-1.1.zip").write_bytes(b"archive")
        (qbasic_dir / "QBASIC.EXE").write_bytes(b"exe")
        (gwbasic_dir / "gwbasic-3.23.7z").write_bytes(b"archive")
        (gwbasic_dir / "GWBASIC.EXE").write_bytes(b"exe")

    def _write_fake_commands(self, bin_dir: Path) -> None:
        _write_executable(
            bin_dir / "dosemu",
            """#!/usr/bin/env bash
            set -euo pipefail
            printf '%s\\n' "$*" >>"${FAKE_DOSEMU_ARGS_LOG}"
            if [[ -n "${FAKE_DOSEMU_TIMEOUT_ENV_LOG:-}" ]]; then
              printf '%s\\n' "${CLASSIC_BASIC_DOSEMU_BATCH_TIMEOUT:-}" >>"${FAKE_DOSEMU_TIMEOUT_ENV_LOG}"
            fi
            drive_c=''
            dos_command=''
            while [[ $# -gt 0 ]]; do
              case "$1" in
                --Fdrive_c)
                  drive_c="$2"
                  shift 2
                  ;;
                -E)
                  dos_command="$2"
                  shift 2
                  ;;
                *)
                  shift
                  ;;
              esac
            done
            if [[ -n "${FAKE_DOS_CAPTURE_CONTENT:-}" ]]; then
              capture_name=""
              if [[ "${dos_command}" == *' > '* ]]; then
                capture_name="${dos_command##* > }"
              elif [[ -n "${drive_c}" && -f "${drive_c}/${dos_command}.BAT" ]]; then
                capture_name=$(grep -oi '> [A-Z0-9.]*' "${drive_c}/${dos_command}.BAT" | head -1 | cut -c3-)
              fi
              if [[ -n "${capture_name}" ]]; then
                printf '%s' "${FAKE_DOS_CAPTURE_CONTENT}" >"${drive_c}/${capture_name}"
              fi
            fi
            if [[ -n "${FAKE_DOS_OUT_TXT_CONTENT:-}" ]]; then
              printf '%s' "${FAKE_DOS_OUT_TXT_CONTENT}" >"${drive_c}/out.txt"
            fi
            printf '%s' "${FAKE_DOSEMU_STDOUT:-}"
            exit "${FAKE_DOSEMU_EXIT_STATUS:-0}"
            """,
        )
        _write_executable(
            bin_dir / "expect",
            """#!/usr/bin/env bash
            set -euo pipefail
            if [[ "${FAKE_EXPECT_FAIL_ON_CALL:-0}" == "1" ]]; then
              echo "unexpected expect invocation" >&2
              exit 97
            fi
            printf 'expect\\n' >>"${FAKE_EXPECT_LOG}"
            cat >/dev/null
            exit 0
            """,
        )
        _write_executable(
            bin_dir / "script",
            """#!/usr/bin/env bash
            set -euo pipefail
            if [[ "${FAKE_SCRIPT_FAIL_ON_CALL:-0}" == "1" ]]; then
              echo "unexpected script invocation" >&2
              exit 98
            fi
            printf '%s\\n' "$*" >>"${FAKE_SCRIPT_LOG}"
            # Parse -c CMD to simulate PTY execution of the command.
            cmd_arg=''
            while [[ $# -gt 0 ]]; do
              case "$1" in
                -c) cmd_arg="$2"; shift 2 ;;
                *)  shift ;;
              esac
            done
            if [[ -n "${cmd_arg}" ]]; then
              exec bash -lc "${cmd_arg}"
            fi
            exit 0
            """,
        )
        for command_name in ("curl", "7z", "mcopy"):
            _write_executable(
                bin_dir / command_name,
                f"""#!/usr/bin/env bash
                set -euo pipefail
                echo "{command_name} should not be called in wrapper tests" >&2
                exit 99
                """,
            )

    def _run_wrapper(
        self,
        *,
        env: dict[str, str],
        runtime_name: str,
        home_name: str,
        runner: Path,
        source_name: str,
        extra_args: list[str] | None = None,
        run_mode: bool = True,
        with_file: bool = True,
        source_text: str = "10 PRINT 42\n",
        source_bytes: bytes | None = None,
        use_short_aliases: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        runtime_dir = Path(env["FAKE_DOSEMU_ARGS_LOG"]).parent / runtime_name
        home_dir = Path(env["FAKE_DOSEMU_ARGS_LOG"]).parent / home_name
        source_path = runtime_dir.parent / source_name
        if source_bytes is None:
            source_path.write_text(source_text, encoding="ascii")
        else:
            source_path.write_bytes(source_bytes)
        command = [
            "bash",
            str(runner),
            "--runtime",
            str(runtime_dir),
            "--home",
            str(home_dir),
        ]
        if run_mode:
            command.append("-r" if use_short_aliases else "--run")
        if with_file:
            command.extend(["-f" if use_short_aliases else "--file", str(source_path)])
        if extra_args:
            command.extend(extra_args)

        result = subprocess.run(
            command,
            cwd=ROOT_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return result, runtime_dir


if __name__ == "__main__":
    unittest.main()
