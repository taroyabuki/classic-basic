from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="ascii")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _copy_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def _copy_repo_files(root: Path, relpaths: list[str]) -> None:
    for relpath in relpaths:
        _copy_file(ROOT_DIR / relpath, root / relpath)


def _run(command: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


class SetupScriptTests(unittest.TestCase):
    def test_setup_6502_creates_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_repo_files(
                temp_path,
                [
                    "setup/6502.sh",
                    "scripts/6502-basic-common.sh",
                ],
            )
            rom_path = temp_path / "third_party/msbasic/osi.bin"
            rom_path.parent.mkdir(parents=True, exist_ok=True)
            rom_path.write_bytes(b"rom")
            workspace_path = temp_path / "workspace"

            result = _run(
                ["bash", "setup/6502.sh", "--workspace", str(workspace_path)],
                cwd=temp_path,
                env=os.environ.copy(),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(workspace_path.is_dir())
            self.assertIn(f"Prepared 6502 BASIC workspace at {workspace_path}", result.stdout)

    def test_setup_basic80_bootstraps_runcpm_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_repo_files(
                temp_path,
                [
                    "setup/basic80.sh",
                    "scripts/basic-file-common.sh",
                    "scripts/runcpm-common.sh",
                ],
            )
            mbasic_path = temp_path / "downloads/MBASIC.COM"
            mbasic_path.parent.mkdir(parents=True, exist_ok=True)
            mbasic_path.write_bytes(b"mbasic")

            bin_dir = temp_path / "bin"
            git_log = temp_path / "git.log"
            _write_executable(
                bin_dir / "git",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                printf '%s\\n' "$*" >>"{git_log}"
                if [[ "$1" == "clone" ]]; then
                  dest="${{@: -1}}"
                  mkdir -p "$dest/.git" "$dest/DISK" "$dest/CCP" "$dest/RunCPM"
                  python3 - "$dest/DISK/A0.ZIP" <<'PY'
import pathlib
import sys
import zipfile

path = pathlib.Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(path, "w") as archive:
    archive.writestr("A/0/README.TXT", "demo\\n")
PY
                  printf 'ccp\\n' >"$dest/CCP/CCP-TEST"
                  printf '#!/usr/bin/env bash\\nexit 0\\n' >"$dest/RunCPM/RunCPM"
                  chmod +x "$dest/RunCPM/RunCPM"
                  exit 0
                fi
                exit 1
                """,
            )
            _write_executable(bin_dir / "make", "#!/usr/bin/env bash\nexit 0\n")

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            runtime_path = temp_path / "runtime"
            result = _run(
                ["bash", "setup/basic80.sh", "--runtime", str(runtime_path)],
                cwd=temp_path,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((runtime_path / "RunCPM").exists())
            self.assertTrue((runtime_path / "A/0/MBASIC.COM").exists())
            self.assertFalse((runtime_path / "AUTOEXEC.TXT").exists())
            self.assertIn("clone https://github.com/MockbaTheBorg/RunCPM", git_log.read_text(encoding="ascii"))

    def test_setup_nbasic_installs_rom_from_argument(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_repo_files(temp_path, ["setup/nbasic.sh"])
            source_rom = temp_path / "source.rom"
            source_rom.write_bytes(b"rom")

            result = _run(["bash", "setup/nbasic.sh", str(source_rom)], cwd=temp_path, env=os.environ.copy())

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((temp_path / "downloads/pc8001/N80_11.rom").read_bytes(), b"rom")

    def test_setup_msxbasic_extracts_expected_rom(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_repo_files(temp_path, ["setup/msxbasic.sh"])
            bin_dir = temp_path / "bin"
            archive_path = temp_path / "downloads/blueMSXv282full.exe"
            sha = "1c85dac5536fa3ba6f2cb70deba02ff680b34ac6cc787d2977258bd663a99555"

            _write_executable(
                bin_dir / "curl",
                """#!/usr/bin/env bash
                set -euo pipefail
                out=""
                while [[ $# -gt 0 ]]; do
                  if [[ "$1" == "--output" || "$1" == "-o" ]]; then
                    out="$2"
                    shift 2
                    continue
                  fi
                  shift
                done
                printf 'archive' >"${out}"
                """,
            )
            _write_executable(
                bin_dir / "7z",
                """#!/usr/bin/env bash
                set -euo pipefail
                cmd="$1"
                shift
                outdir=""
                for arg in "$@"; do
                  case "$arg" in
                    -o*)
                      outdir="${arg#-o}"
                      ;;
                  esac
                done
                if [[ "$cmd" == "e" && "$*" == *" bluemsx.msi"* ]]; then
                  mkdir -p "${outdir}"
                  : >"${outdir}/bluemsx.msi"
                  exit 0
                fi
                if [[ "$cmd" == "l" ]]; then
                  cat <<'EOF'
-------------------
..... 123 0 payload.cab
-------------------
EOF
                  exit 0
                fi
                if [[ "$cmd" == "e" && "$*" == *" payload.cab"* ]]; then
                  mkdir -p "${outdir}"
                  : >"${outdir}/payload.cab"
                  exit 0
                fi
                if [[ "$cmd" == "x" ]]; then
                  mkdir -p "${outdir}"
                  python3 - "${outdir}/rom.bin" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
path.write_bytes(b"\0" * 32768)
PY
                  exit 0
                fi
                exit 1
                """,
            )
            _write_executable(
                bin_dir / "sha256sum",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                printf '{sha}  %s\\n' "$1"
                """,
            )
            _write_executable(
                bin_dir / "find",
                """#!/usr/bin/env bash
                set -euo pipefail
                if [[ "$1" == *"/cab" ]]; then
                  printf '%s\\0' "$1/rom.bin"
                  exit 0
                fi
                exec /usr/bin/find "$@"
                """,
            )
            _write_executable(bin_dir / "openmsx", "#!/usr/bin/env bash\nexit 0\n")

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            result = _run(["bash", "setup/msxbasic.sh", "--archive", str(archive_path)], cwd=temp_path, env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((temp_path / "downloads/msx/msx1-basic-bios.rom").exists())
            self.assertTrue((temp_path / "downloads/msx/vg8020-20_basic-bios1.rom").exists())
            self.assertIn("Installed", result.stdout)

    def test_setup_gwbasic_prepares_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_repo_files(
                temp_path,
                [
                    "setup/gwbasic.sh",
                    "scripts/basic-file-common.sh",
                    "scripts/gwbasic-common.sh",
                    "scripts/exitemu.com",
                ],
            )
            archive_path = temp_path / "downloads/gwbasic/gwbasic-3.23.7z"
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            archive_path.write_bytes(b"archive")
            bin_dir = temp_path / "bin"
            _write_executable(bin_dir / "dosemu", "#!/usr/bin/env bash\nexit 0\n")
            _write_executable(bin_dir / "curl", "#!/usr/bin/env bash\nexit 0\n")
            _write_executable(
                bin_dir / "7z",
                """#!/usr/bin/env bash
                set -euo pipefail
                outdir=""
                for arg in "$@"; do
                  case "$arg" in
                    -o*)
                      outdir="${arg#-o}"
                      ;;
                  esac
                done
                mkdir -p "${outdir}"
                : >"${outdir}/disk.img"
                """,
            )
            _write_executable(
                bin_dir / "mcopy",
                """#!/usr/bin/env bash
                set -euo pipefail
                out="${@: -1}"
                printf 'exe' >"${out}"
                """,
            )

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            runtime_path = temp_path / "gwbasic-runtime"
            home_path = temp_path / "gwbasic-home"
            result = _run(
                [
                    "bash",
                    "setup/gwbasic.sh",
                    "--archive",
                    str(archive_path),
                    "--runtime",
                    str(runtime_path),
                    "--home",
                    str(home_path),
                ],
                cwd=temp_path,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((runtime_path / "drive_c/GWBASIC.EXE").exists())
            self.assertTrue((runtime_path / "drive_c/EXITEMU.COM").exists())
            self.assertIn("Prepared GW-BASIC runtime", result.stderr)

    def test_setup_qbasic_prepares_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_repo_files(
                temp_path,
                [
                    "setup/qbasic.sh",
                    "scripts/basic-file-common.sh",
                    "scripts/qbasic-common.sh",
                    "scripts/exitemu.com",
                ],
            )
            archive_path = temp_path / "downloads/qbasic/qbasic-1.1.zip"
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            archive_path.write_bytes(b"archive")
            bin_dir = temp_path / "bin"
            _write_executable(bin_dir / "dosemu", "#!/usr/bin/env bash\nexit 0\n")
            _write_executable(bin_dir / "curl", "#!/usr/bin/env bash\nexit 0\n")
            _write_executable(
                bin_dir / "7z",
                """#!/usr/bin/env bash
                set -euo pipefail
                outdir=""
                for arg in "$@"; do
                  case "$arg" in
                    -o*)
                      outdir="${arg#-o}"
                      ;;
                  esac
                done
                mkdir -p "${outdir}"
                : >"${outdir}/disk.img"
                """,
            )
            _write_executable(
                bin_dir / "mcopy",
                """#!/usr/bin/env bash
                set -euo pipefail
                out="${@: -1}"
                printf 'exe' >"${out}"
                """,
            )

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            runtime_path = temp_path / "qbasic-runtime"
            home_path = temp_path / "qbasic-home"
            result = _run(
                [
                    "bash",
                    "setup/qbasic.sh",
                    "--archive",
                    str(archive_path),
                    "--runtime",
                    str(runtime_path),
                    "--home",
                    str(home_path),
                ],
                cwd=temp_path,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((runtime_path / "drive_c/QBASIC.EXE").exists())
            self.assertTrue((runtime_path / "drive_c/EXITEMU.COM").exists())
            self.assertIn("Prepared QBasic runtime", result.stderr)
