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


def _write_noop_fbasic_apt_tools(bin_dir: Path) -> None:
    _write_executable(
        bin_dir / "apt-cache",
        """#!/usr/bin/env bash
        set -euo pipefail
        exit 1
        """,
    )
    _write_executable(
        bin_dir / "apt-get",
        """#!/usr/bin/env bash
        set -euo pipefail
        if [[ "$1" == "update" ]]; then
          exit 0
        fi
        exit 1
        """,
    )
    _write_executable(
        bin_dir / "dpkg",
        """#!/usr/bin/env bash
        set -euo pipefail
        if [[ "$1" == "--print-foreign-architectures" ]]; then
          exit 0
        fi
        if [[ "$1" == "--add-architecture" && "$2" == "i386" ]]; then
          exit 0
        fi
        exit 1
        """,
    )


def _write_fake_mingw_compiler(path: Path) -> None:
    _write_executable(
        path,
        """#!/usr/bin/env bash
        set -euo pipefail
        out=""
        while [[ $# -gt 0 ]]; do
          case "$1" in
            -o)
              out="$2"
              shift 2
              ;;
            *)
              shift
              ;;
          esac
        done
        [[ -n "${out}" ]]
        : >"${out}"
        """,
    )


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
    def test_setup_6502_verifies_assets(self) -> None:
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

            result = _run(
                ["bash", "setup/6502.sh"],
                cwd=temp_path,
                env=os.environ.copy(),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Verified 6502 BASIC assets", result.stdout)
            self.assertIn(f"OSI BASIC ROM: {rom_path}", result.stdout)

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

    def test_setup_nbasic_downloads_neo_kobe_rom(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_repo_files(temp_path, ["setup/nbasic.sh"])
            bin_dir = temp_path / "bin"
            archive_path = temp_path / "downloads/pc8001/neo-kobe-emulator-pack-2013-08-17.7z"
            curl_log = temp_path / "curl.log"
            _write_executable(
                bin_dir / "curl",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                out=""
                url=""
                while [[ $# -gt 0 ]]; do
                  case "$1" in
                    --output|-o)
                      out="$2"
                      shift 2
                      ;;
                    *)
                      url="$1"
                      shift
                      ;;
                  esac
                done
                printf '%s\\n' "${{url}}" >>"{curl_log}"
                printf 'archive' >"${{out}}"
                """,
            )
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
                printf 'rom' >"${outdir}/PC-8001(1.1).rom"
                """,
            )

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            result = _run(["bash", "setup/nbasic.sh"], cwd=temp_path, env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(archive_path.read_bytes(), b"archive")
            self.assertEqual((temp_path / "downloads/pc8001/N80_11.rom").read_bytes(), b"rom")
            self.assertIn("archive.org/download/neo-kobe-emulator-pack-2013-08-17.7z", curl_log.read_text(encoding="ascii"))

    def test_setup_n88basic_installs_roms_from_argument(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_repo_files(temp_path, ["setup/n88basic.sh"])
            rom_source_dir = temp_path / "source-roms"
            rom_source_dir.mkdir()
            for name in (
                "n88.rom",
                "n88_0.rom",
                "n88_1.rom",
                "n88_2.rom",
                "n88_3.rom",
                "n80.rom",
                "disk.rom",
                "KANJI1.ROM",
                "KANJI2.ROM",
                "font.rom",
            ):
                (rom_source_dir / name).write_bytes(name.encode("ascii"))

            quasi88_bin = temp_path / "vendor/quasi88/quasi88.sdl2"
            quasi88_bin.parent.mkdir(parents=True, exist_ok=True)
            quasi88_bin.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="ascii")
            quasi88_bin.chmod(quasi88_bin.stat().st_mode | stat.S_IXUSR)

            result = _run(["bash", "setup/n88basic.sh", str(rom_source_dir)], cwd=temp_path, env=os.environ.copy())

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((temp_path / "downloads/n88basic/roms/N88.ROM").read_bytes(), b"n88.rom")
            self.assertEqual((temp_path / "downloads/n88basic/roms/FONT.ROM").read_bytes(), b"font.rom")

    def test_setup_n88basic_downloads_neo_kobe_roms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_repo_files(temp_path, ["setup/n88basic.sh"])
            bin_dir = temp_path / "bin"
            archive_path = temp_path / "downloads/n88basic/neo-kobe-emulator-pack-2013-08-17.7z"
            curl_log = temp_path / "curl.log"
            _write_executable(
                bin_dir / "curl",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                out=""
                url=""
                while [[ $# -gt 0 ]]; do
                  case "$1" in
                    --output|-o)
                      out="$2"
                      shift 2
                      ;;
                    *)
                      url="$1"
                      shift
                      ;;
                  esac
                done
                printf '%s\\n' "${{url}}" >>"{curl_log}"
                printf 'archive' >"${{out}}"
                """,
            )
            _write_executable(
                bin_dir / "7z",
                """#!/usr/bin/env bash
                set -euo pipefail
                outdir=""
                member="${@: -1}"
                for arg in "$@"; do
                  case "$arg" in
                    -o*)
                      outdir="${arg#-o}"
                      ;;
                  esac
                done
                mkdir -p "${outdir}"
                base="$(basename "${member}")"
                printf '%s' "${base}" >"${outdir}/${base}"
                """,
            )

            quasi88_bin = temp_path / "vendor/quasi88/quasi88.sdl2"
            quasi88_bin.parent.mkdir(parents=True, exist_ok=True)
            quasi88_bin.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="ascii")
            quasi88_bin.chmod(quasi88_bin.stat().st_mode | stat.S_IXUSR)

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            result = _run(["bash", "setup/n88basic.sh"], cwd=temp_path, env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(archive_path.read_bytes(), b"archive")
            self.assertEqual((temp_path / "downloads/n88basic/roms/N88.ROM").read_bytes(), b"n88.rom")
            self.assertEqual((temp_path / "downloads/n88basic/roms/KANJI1.ROM").read_bytes(), b"KANJI1.ROM")
            self.assertIn("archive.org/download/neo-kobe-emulator-pack-2013-08-17.7z", curl_log.read_text(encoding="ascii"))

    def test_setup_fm7basic_downloads_neo_kobe_roms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_repo_files(
                temp_path,
                [
                    "setup/fm7basic.sh",
                    "scripts/fm7basic-common.sh",
                ],
            )
            bin_dir = temp_path / "bin"
            archive_path = temp_path / "downloads/fm7basic/neo-kobe-emulator-pack-2013-08-17.7z"
            curl_log = temp_path / "curl.log"
            _write_executable(
                bin_dir / "curl",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                out=""
                url=""
                while [[ $# -gt 0 ]]; do
                  case "$1" in
                    --output|-o)
                      out="$2"
                      shift 2
                      ;;
                    *)
                      url="$1"
                      shift
                      ;;
                  esac
                done
                printf '%s\\n' "${{url}}" >>"{curl_log}"
                printf 'archive' >"${{out}}"
                """,
            )
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
                mkdir -p "${outdir}/Fujitsu FM-7/xm7_3460/Win32"
                printf 'fbasic' >"${outdir}/Fujitsu FM-7/xm7_3460/Win32/FBASIC30.ROM"
                printf 'subsysc' >"${outdir}/Fujitsu FM-7/xm7_3460/Win32/SUBSYS_C.ROM"
                printf 'init' >"${outdir}/Fujitsu FM-7/xm7_3460/Win32/INITIATE.ROM"
                printf 'suba' >"${outdir}/Fujitsu FM-7/xm7_3460/Win32/SUBSYS_A.ROM"
                printf 'subb' >"${outdir}/Fujitsu FM-7/xm7_3460/Win32/SUBSYS_B.ROM"
                printf 'subcg' >"${outdir}/Fujitsu FM-7/xm7_3460/Win32/SUBSYSCG.ROM"
                printf 'kanji1' >"${outdir}/Fujitsu FM-7/xm7_3460/Win32/KANJI1.ROM"
                """,
            )
            _write_executable(
                bin_dir / "mame",
                """#!/usr/bin/env bash
                set -euo pipefail
                if [[ "$1" == "-rompath" && "$3" == "-verifyroms" && "$4" == "fm77av" ]]; then
                  exit 0
                fi
                exit 1
                """,
            )

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            result = _run(["bash", "setup/fm7basic.sh"], cwd=temp_path, env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(archive_path.read_bytes(), b"archive")
            self.assertFalse((temp_path / "downloads/fm7basic/mame-roms/fm7").exists())
            self.assertEqual((temp_path / "downloads/fm7basic/mame-roms/fm77av/initiate.rom").read_bytes(), b"init")
            self.assertEqual((temp_path / "downloads/fm7basic/mame-roms/fm77av/fbasic30.rom").read_bytes(), b"fbasic")
            self.assertEqual((temp_path / "downloads/fm7basic/mame-roms/fm77av/kanji.rom").read_bytes(), b"kanji1")
            self.assertIn("archive.org/download/neo-kobe-emulator-pack-2013-08-17.7z", curl_log.read_text(encoding="ascii"))

    def test_setup_fm7basic_installs_mame_via_apt_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_repo_files(
                temp_path,
                [
                    "setup/fm7basic.sh",
                    "scripts/fm7basic-common.sh",
                ],
            )
            source_dir = temp_path / "fm7-assets"
            source_dir.mkdir()
            for name in ("FBASIC30.ROM", "INITIATE.ROM", "SUBSYS_A.ROM", "SUBSYS_B.ROM", "SUBSYS_C.ROM", "SUBSYSCG.ROM", "KANJI1.ROM"):
                (source_dir / name).write_bytes(name.encode("ascii"))

            bin_dir = temp_path / "bin"
            apt_log = temp_path / "apt.log"
            mame_bin = bin_dir / "mame"
            _write_executable(
                bin_dir / "apt-cache",
                """#!/usr/bin/env bash
                set -euo pipefail
                if [[ "$1" == "policy" && "$2" == "mame" ]]; then
                  cat <<'EOF'
mame:
  Installed: (none)
  Candidate: 0.264+dfsg.1-1
  Version table:
     0.264+dfsg.1-1 500
EOF
                  exit 0
                fi
                exit 1
                """,
            )
            _write_executable(
                bin_dir / "apt-get",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                printf '%s\\n' "$*" >>"{apt_log}"
                if [[ "$1" == "update" ]]; then
                  exit 0
                fi
                if [[ "$1" == "install" && "$2" == "-y" && "$3" == "mame" ]]; then
                  cat >"{mame_bin}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exit 0
EOF
                  chmod +x "{mame_bin}"
                  exit 0
                fi
                exit 1
                """,
            )

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env["CLASSIC_BASIC_MAME"] = str(mame_bin)
            result = _run(
                ["bash", "setup/fm7basic.sh", "--rom-dir", str(source_dir)],
                cwd=temp_path,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                apt_log.read_text(encoding="ascii").splitlines(),
                ["update", "install -y mame"],
            )
            self.assertTrue(mame_bin.exists())

    def test_setup_fm11basic_bootstraps_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_repo_files(
                temp_path,
                [
                    "setup/fm11basic.sh",
                    "scripts/fm11basic-common.sh",
                    "scripts/bootstrap_fm11basic_assets.sh",
                ],
            )

            bin_dir = temp_path / "bin"
            curl_log = temp_path / "curl.log"
            archive_dir = temp_path / "archives"
            archive_dir.mkdir()

            _write_executable(
                bin_dir / "curl",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                out=""
                url=""
                while [[ $# -gt 0 ]]; do
                  case "$1" in
                    --output|-o)
                      out="$2"
                      shift 2
                      ;;
                    *)
                      url="$1"
                      shift
                      ;;
                  esac
                done
                printf '%s\\n' "${{url}}" >>"{curl_log}"
                case "${{url}}" in
                  http://nanno.bf1.jp/softlib/program/fm11.zip)
                    cp "{archive_dir / 'fm11_x86.zip'}" "${{out}}"
                    ;;
                  https://archive.org/download/mame-0.221-roms-merged/fm11.zip)
                    cp "{archive_dir / 'fm11_roms.zip'}" "${{out}}"
                    ;;
                  http://nanno.bf1.jp/softlib/man/fm11/fb2hd00.img)
                    cp "{archive_dir / 'fb2hd00.img'}" "${{out}}"
                    ;;
                  *)
                    exit 1
                    ;;
                esac
                """,
            )
            _write_executable(bin_dir / "python3", "#!/usr/bin/env bash\nexit 0\n")
            _write_executable(bin_dir / "Xvfb", "#!/usr/bin/env bash\nexit 0\n")
            _write_executable(bin_dir / "xdpyinfo", "#!/usr/bin/env bash\nexit 0\n")
            _write_executable(bin_dir / "xdotool", "#!/usr/bin/env bash\nexit 0\n")
            _write_executable(bin_dir / "xclip", "#!/usr/bin/env bash\nexit 0\n")
            _write_executable(bin_dir / "wine", "#!/usr/bin/env bash\nexit 0\n")
            _write_noop_fbasic_apt_tools(bin_dir)

            disk_bytes = b"fm11-disk"
            (archive_dir / "fb2hd00.img").write_bytes(disk_bytes)

            with zipfile.ZipFile(archive_dir / "fm11_x86.zip", "w") as archive:
                archive.writestr("Fm11.exe", "exe\n")
            with zipfile.ZipFile(archive_dir / "fm11_roms.zip", "w") as archive:
                archive.writestr("boot6809.rom", "6809\n")
                archive.writestr("boot8088.rom", "8088\n")
                archive.writestr("kanji.rom", "kanji\n")
                archive.writestr("subsys.rom", "subsys\n")
                archive.writestr("subsys_e.rom", "subsys_e\n")

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env["CLASSIC_BASIC_FM11_EMULATOR_SHA256"] = subprocess.check_output(
                ["sha256sum", str(archive_dir / "fm11_x86.zip")], text=True
            ).split()[0]
            env["CLASSIC_BASIC_FM11_DISK_SHA256"] = subprocess.check_output(
                ["sha256sum", str(archive_dir / "fb2hd00.img")], text=True
            ).split()[0]

            result = _run(["bash", "setup/fm11basic.sh"], cwd=temp_path, env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((temp_path / "downloads/fm11basic/staged/emulator-x86/Fm11.exe").read_text(encoding="ascii"), "exe\n")
            self.assertEqual((temp_path / "downloads/fm11basic/staged/roms/boot6809.rom").read_text(encoding="ascii"), "6809\n")
            self.assertEqual((temp_path / "downloads/fm11basic/staged/disks/fb2hd00.img").read_bytes(), disk_bytes)
            self.assertIn("http://nanno.bf1.jp/softlib/program/fm11.zip", curl_log.read_text(encoding="ascii"))
            self.assertIn("https://archive.org/download/mame-0.221-roms-merged/fm11.zip", curl_log.read_text(encoding="ascii"))
            self.assertIn("Run with: ./run/fm11basic.sh", result.stdout)

    def test_setup_fm7basic_fails_when_verifyroms_rejects_romset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_repo_files(
                temp_path,
                [
                    "setup/fm7basic.sh",
                    "scripts/fm7basic-common.sh",
                ],
            )
            source_dir = temp_path / "fm7-assets"
            source_dir.mkdir()
            for name in ("FBASIC30.ROM", "INITIATE.ROM", "SUBSYS_A.ROM", "SUBSYS_B.ROM", "SUBSYS_C.ROM", "SUBSYSCG.ROM", "KANJI1.ROM"):
                (source_dir / name).write_bytes(name.encode("ascii"))

            bin_dir = temp_path / "bin"
            _write_executable(
                bin_dir / "mame",
                """#!/usr/bin/env bash
                set -euo pipefail
                exit 1
                """,
            )

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            result = _run(["bash", "setup/fm7basic.sh", "--rom-dir", str(source_dir)], cwd=temp_path, env=env)

            self.assertEqual(result.returncode, 2)
            self.assertIn("prepared fm77av ROM set does not match this MAME build", result.stderr)


    def test_setup_msxbasic_extracts_expected_rom(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_repo_files(temp_path, ["setup/msxbasic.sh"])
            bin_dir = temp_path / "bin"
            archive_path = temp_path / "downloads/neo-kobe-emulator-pack-2013-08-17.7z"
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
                outdir=""
                for arg in "$@"; do
                  case "$arg" in
                    -o*)
                      outdir="${arg#-o}"
                      ;;
                  esac
                done
                mkdir -p "${outdir}"
                python3 - "${outdir}/vg8020-20_basic-bios1.rom" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
path.write_bytes(b"\0" * 32768)
PY
                """,
            )
            _write_executable(
                bin_dir / "sha256sum",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                printf '{sha}  %s\\n' "$1"
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
            template_path = temp_path / "fdppconf.sys"
            template_path.write_text(
                "devicehigh=dosemu\\ems.sys\n"
                "devicehigh=dosemu\\cdrom.sys\n"
                "devicehigh=dosemu\\emufs.sys /all\n",
                encoding="ascii",
            )
            env["CLASSIC_BASIC_DOSEMU_FDPPCONF_TEMPLATE"] = str(template_path)
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
            fdppconf = (home_path / ".dosemu/fdppconf.sys").read_text(encoding="ascii")
            self.assertNotIn("devicehigh=dosemu\\cdrom.sys", fdppconf)
            self.assertIn("devicehigh=dosemu\\emufs.sys /all", fdppconf)
            self.assertIn("Prepared GW-BASIC runtime", result.stderr)


    def test_setup_grantsbasic_builds_rom_from_original_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_repo_files(temp_path, ["setup/grantsbasic.sh"])
            shutil.copytree(ROOT_DIR / "src" / "grants_basic", temp_path / "src" / "grants_basic")

            def ihex_record(address: int, data: bytes, record_type: int = 0) -> str:
                payload = bytes([len(data), (address >> 8) & 0xFF, address & 0xFF, record_type]) + data
                checksum = ((-(sum(payload))) & 0xFF)
                return ":" + payload.hex().upper() + f"{checksum:02X}" + "\n"

            archive_path = temp_path / "grant-searle-rom.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "ROM.HEX",
                    ihex_record(0, b"\x01\x02\x03\x04") + ":00000001FF\n",
                )
                archive.writestr("INTMINI.HEX", ":00000001FF\n")
                archive.writestr("BASIC.HEX", ":00000001FF\n")
                archive.writestr("INTMINI.ASM", "; intmini\n")
                archive.writestr("BASIC.ASM", "; basic\n")

            result = _run(
                ["bash", "setup/grantsbasic.sh", "--rom-zip", str(archive_path)],
                cwd=temp_path,
                env=os.environ.copy(),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            download_dir = temp_path / "downloads" / "grants-basic"
            self.assertEqual((download_dir / "rom.bin").read_bytes(), b"\x01\x02\x03\x04")
            self.assertEqual((download_dir / "ROM.HEX").read_text(encoding="ascii"), ":0400000001020304F2\n:00000001FF\n")
            self.assertTrue((download_dir / "INTMINI.HEX").exists())
            self.assertTrue((download_dir / "BASIC.HEX").exists())
            self.assertTrue((download_dir / "INTMINI.ASM").exists())
            self.assertTrue((download_dir / "BASIC.ASM").exists())
            self.assertIn("rom.bin sha1", result.stderr)

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
            template_path = temp_path / "fdppconf.sys"
            template_path.write_text(
                "devicehigh=dosemu\\ems.sys\n"
                "devicehigh=dosemu\\cdrom.sys\n"
                "devicehigh=dosemu\\emufs.sys /all\n",
                encoding="ascii",
            )
            env["CLASSIC_BASIC_DOSEMU_FDPPCONF_TEMPLATE"] = str(template_path)
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
            fdppconf = (home_path / ".dosemu/fdppconf.sys").read_text(encoding="ascii")
            self.assertNotIn("devicehigh=dosemu\\cdrom.sys", fdppconf)
            self.assertIn("devicehigh=dosemu\\emufs.sys /all", fdppconf)
            self.assertIn("Prepared QBasic runtime", result.stderr)
