#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT_DIR}/scripts/basic-file-common.sh"

DEFAULT_QBASIC_DIR="${CLASSIC_BASIC_QBASIC_DIR:-${ROOT_DIR}/downloads/qbasic}"
DEFAULT_QBASIC_ARCHIVE="${CLASSIC_BASIC_QBASIC_ARCHIVE:-${DEFAULT_QBASIC_DIR}/qbasic-1.1.zip}"
DEFAULT_QBASIC_EXE="${CLASSIC_BASIC_QBASIC_EXE:-${DEFAULT_QBASIC_DIR}/QBASIC.EXE}"
DEFAULT_QBASIC_RUNTIME="${CLASSIC_BASIC_QBASIC_RUNTIME:-/tmp/qbasic-dosemu}"
DEFAULT_QBASIC_HOME="${CLASSIC_BASIC_QBASIC_HOME:-/tmp/qbasic-home}"
DEFAULT_QBASIC_PROGRAM_NAME="${CLASSIC_BASIC_QBASIC_PROGRAM_NAME:-RUNFILE.BAS}"
DEFAULT_QBASIC_BOOT_NAME="${CLASSIC_BASIC_QBASIC_BOOT_NAME:-BOOT.BAS}"
DEFAULT_QBASIC_CAPTURE_NAME="${CLASSIC_BASIC_QBASIC_CAPTURE_NAME:-CBATCH.TXT}"

WINWORLD_PAGE_URL="https://winworldpc.com/download/c5a11f1d-c386-c592-7311-c3a5c28f1352"
WINWORLD_MIRROR_URL="https://winworldpc.com/download/c5a11f1d-c386-c592-7311-c3a5c28f1352/from/c39ac2af-c381-c2bf-1b25-11c3a4e284a2"

die() {
  echo "error: $*" >&2
  exit 2
}

ensure_qbasic_tools() {
  dosemu_command >/dev/null 2>&1 || die "dosemu or dosemu2 is not installed"
  command -v curl >/dev/null 2>&1 || die "curl is required"
  command -v 7z >/dev/null 2>&1 || die "7z is required"
  command -v mcopy >/dev/null 2>&1 || die "mtools is required"
}

download_qbasic_archive() {
  local archive_path="$1"
  local archive_dir
  local cookie_jar

  [[ -f "${archive_path}" ]] && return 0

  archive_dir="$(dirname "${archive_path}")"
  mkdir -p "${archive_dir}"
  cookie_jar="$(mktemp)"

  curl -fsSL -c "${cookie_jar}" "${WINWORLD_PAGE_URL}" >/dev/null
  curl -fL -b "${cookie_jar}" -c "${cookie_jar}" "${WINWORLD_MIRROR_URL}" -o "${archive_path}"
  rm -f "${cookie_jar}"
}

extract_qbasic_exe() {
  local archive_path="$1"
  local exe_path="$2"
  local exe_dir
  local temp_dir
  local disk_image

  [[ -f "${exe_path}" ]] && return 0
  [[ -f "${archive_path}" ]] || die "archive not found: ${archive_path}"

  exe_dir="$(dirname "${exe_path}")"
  mkdir -p "${exe_dir}"
  temp_dir="$(mktemp -d)"

  7z x -y "-o${temp_dir}" "${archive_path}" >/dev/null
  disk_image="$(find "${temp_dir}" -type f -iname '*.img' | head -n 1)"
  [[ -n "${disk_image}" ]] || die "could not find DOS disk image in ${archive_path}"

  mcopy -o -i "${disk_image}" ::QBASIC.EXE "${exe_path}" >/dev/null
  rm -rf "${temp_dir}"
}

prepare_qbasic_runtime() {
  local archive_path="$1"
  local exe_path="$2"
  local runtime_dir="$3"
  local home_dir="$4"

  ensure_qbasic_tools
  download_qbasic_archive "${archive_path}"
  extract_qbasic_exe "${archive_path}" "${exe_path}"

  mkdir -p /tmp/dosemu2_0 "${runtime_dir}/drive_c" "${home_dir}/.dosemu" "${home_dir}/.config/pulse"
  cp -f "${exe_path}" "${runtime_dir}/drive_c/QBASIC.EXE"
  # Copy exitemu so DOS batch files can terminate dosemu after completion.
  # Use the bundled copy first; fall back to system installation.
  local exitemu_bundled="${ROOT_DIR}/scripts/exitemu.com"
  local exitemu_system
  exitemu_system="$(find /usr/share/dosemu -name 'exitemu.com' 2>/dev/null | head -1)"
  if [[ -f "${exitemu_bundled}" ]]; then
    cp -f "${exitemu_bundled}" "${runtime_dir}/drive_c/EXITEMU.COM"
  elif [[ -f "${exitemu_system}" ]]; then
    cp -f "${exitemu_system}" "${runtime_dir}/drive_c/EXITEMU.COM"
  fi

  chmod 1777 /tmp/dosemu2_0
  chmod 777 "${runtime_dir}" "${runtime_dir}/drive_c" "${home_dir}" "${home_dir}/.dosemu" "${home_dir}/.config" "${home_dir}/.config/pulse"

  echo "Prepared QBasic runtime at ${runtime_dir}" >&2
  echo "Prepared dosemu HOME at ${home_dir}" >&2
  echo "QBasic executable: ${exe_path}" >&2
}

stage_qbasic_program() {
  local source_path="$1"
  local runtime_dir="$2"
  local program_name="${3:-${DEFAULT_QBASIC_PROGRAM_NAME}}"

  normalize_basic_program "${source_path}" "${runtime_dir}/drive_c/${program_name}"
  printf '10 RUN "%s"\r\n' "${program_name}" >"${runtime_dir}/drive_c/${DEFAULT_QBASIC_BOOT_NAME}"
  printf '%s\n' "${program_name}"
}

run_qbasic() {
  local runtime_dir="$1"
  local home_dir="$2"
  local dos_command="$3"

  # When running interactively (stdin is a TTY), use -kt (keyboard from TTY)
  # and prefer a clean PTY via 'script'; fall back to 'expect' when nested PTY
  # allocation is denied by the environment.
  # When running non-interactively (subprocess/pipe), use -ks (keyboard from stdin)
  # with stdin redirected from /dev/null so dosemu exits cleanly after -E cmd
  # completes without hanging on /dev/tty.
  if [[ -t 0 ]]; then
    local dosemu_args=(
      --Flocal_dir "${runtime_dir}"
      --Fdrive_c "${runtime_dir}/drive_c"
      -o "${runtime_dir}/dosemu.log"
      -t
      -kt
      -I '$_sound = (off)'
      -I '$_layout = "us"'
      -I '$_cpu_vm = "emulated"'
      -E "${dos_command}"
    )
    exec_dosemu_interactive "${home_dir}" "${runtime_dir}/dosemu.log" "${dosemu_args[@]}"
  fi

  local dosemu_args=(
    --Flocal_dir "${runtime_dir}"
    --Fdrive_c "${runtime_dir}/drive_c"
    -o "${runtime_dir}/dosemu.log"
    -t
    -ks
    -I '$_sound = (off)'
    -I '$_layout = "us"'
    -I '$_cpu_vm = "emulated"'
    -E "${dos_command}"
  )
  exec_dosemu "${home_dir}" "${dosemu_args[@]}" \
    < /dev/null 2>>"${runtime_dir}/dosemu.log"
}

run_qbasic_file() {
  local runtime_dir="$1"
  local home_dir="$2"
  local program_name="$3"
  local capture_name="${DEFAULT_QBASIC_CAPTURE_NAME}"
  local capture_path="${runtime_dir}/drive_c/${capture_name}"
  # No timeout by default. If a timeout is configured and CBATCH.TXT already
  # has output when it expires, dosemu hung *after* QBasic finished — treat
  # that as success.
  local timeout_spec="${CLASSIC_BASIC_DOSEMU_BATCH_TIMEOUT:-}"
  local status=0

  # Write a DOS batch file: run QBasic then exit dosemu.
  # Try EXITEMU first (dosemu2-specific); EXIT is a FreeDOS built-in fallback
  # (SHELL_ALLOW_EXIT=1 is set by dosemu2's fdppconf.sys). Belt-and-suspenders:
  # if EXITEMU works dosemu exits immediately; if not, EXIT closes COMMAND.COM.
  local batch_path="${runtime_dir}/drive_c/RUNQB.BAT"
  printf '@ECHO OFF\r\nQBASIC /RUN %s > %s\r\nEXITEMU\r\nEXIT\r\n' \
    "${program_name}" "${capture_name}" > "${batch_path}"

  local out_txt_path
  out_txt_path="$(find "${runtime_dir}/drive_c" -maxdepth 1 -iname 'out.txt' 2>/dev/null | head -1)"
  rm -f "${capture_path}" "${out_txt_path}"
  local preload
  preload="$(dosemu_preload_value)"
  local dosemu_bin
  dosemu_bin="$(dosemu_direct_bin)"
  local full_cmd=(env HOME="${home_dir}")
  [[ -n "${preload}" ]] && full_cmd+=(LD_PRELOAD="${preload}")
  full_cmd+=(
    "${dosemu_bin}"
    --Flocal_dir "${runtime_dir}"
    --Fdrive_c "${runtime_dir}/drive_c"
    -o "${runtime_dir}/dosemu.log"
    -t
  )
  if dosemu_batch_use_pty; then
    full_cmd+=(-kt)
  else
    full_cmd+=(-ks)
  fi
  full_cmd+=(
    -I '$_sound = (off)'
    -I '$_layout = "us"'
    -I '$_cpu_vm = "emulated"'
    -E "RUNQB"
  )

  local terminal_output
  terminal_output="$(mktemp)"
  if dosemu_batch_use_pty; then
    run_dosemu_in_pty "${timeout_spec}" "${runtime_dir}/dosemu.log" "${full_cmd[@]}" || status=$?
    rm -f "${terminal_output}"
    terminal_output=""
  else
    # Raise nproc limit: dosemu2.bin spawns ~20 threads; if RLIMIT_NPROC is
    # tight the clone() syscall fails and dosemu2 deadlocks on a futex.
    ulimit -u unlimited 2>/dev/null || true
    # Watchdog approach: run dosemu in background, poll for CBATCH.TXT, then
    # kill dosemu as soon as output is ready (don't wait for EXITEMU/EXIT/EOF).
    # Use setsid so the dosemu wrapper and dosemu2.bin share a process group
    # (PGID = dosemu_pid).  This lets kill -- -PID terminate the entire tree.
    setsid "${full_cmd[@]}" \
      < /dev/null > "${terminal_output}" 2>>"${runtime_dir}/dosemu.log" &
    local dosemu_pid=$!
    local max_ms=0
    if [[ -n "${timeout_spec}" && "${timeout_spec}" != "0" ]]; then
      max_ms="$(timeout_spec_to_milliseconds "${timeout_spec}")"
    fi
    local elapsed_ms=0
    local timed_out=false
    while true; do
      if ! kill -0 "${dosemu_pid}" 2>/dev/null; then
        wait "${dosemu_pid}" 2>/dev/null || status=$?
        break
      fi
      if [[ -f "${capture_path}" && -s "${capture_path}" ]]; then
        sleep 0.3
        kill -- -"${dosemu_pid}" 2>/dev/null || true
        wait "${dosemu_pid}" 2>/dev/null || true
        break
      fi
      if (( max_ms > 0 && elapsed_ms >= max_ms )); then
        # Capture diagnostics for all processes in the tree
        {
          echo "--- dosemu timeout diagnostic (pid=${dosemu_pid}) ---"
          echo "  caller ulimit -u: $(ulimit -u 2>/dev/null)"
          echo "  /proc/sys/kernel/threads-max: $(cat /proc/sys/kernel/threads-max 2>/dev/null)"
          echo "  /proc/sys/kernel/pid_max: $(cat /proc/sys/kernel/pid_max 2>/dev/null)"
          for pid in "${dosemu_pid}" $(cat /proc/${dosemu_pid}/task/${dosemu_pid}/children 2>/dev/null); do
            echo "  --- pid=${pid} ---"
            grep -E '^(Name|Pid|Threads|VmRSS):' /proc/${pid}/status 2>/dev/null | sed 's/^/    /'
            awk '/Max processes/{print "    RLIMIT_NPROC: " $0}' /proc/${pid}/limits 2>/dev/null
            for wf in /proc/${pid}/task/*/wchan; do
              tid="${wf%/wchan}"; tid="${tid##*/}"
              echo "    tid=${tid} wchan=$(cat "${wf}" 2>/dev/null)"
              echo "    tid=${tid} stack:"
              sed 's/^/      /' /proc/${pid}/task/${tid}/stack 2>/dev/null || true
            done
          done
          echo "--- end diagnostic ---"
        } >> "${runtime_dir}/dosemu.log" 2>/dev/null || true
        kill -- -"${dosemu_pid}" 2>/dev/null || true
        wait "${dosemu_pid}" 2>/dev/null || true
        timed_out=true
        break
      fi
      sleep 0.2
      elapsed_ms=$(( elapsed_ms + 200 ))
    done
    cat "${terminal_output}" >> "${runtime_dir}/dosemu.log"
    if [[ "${timed_out}" == true ]]; then
      if [[ -f "${capture_path}" && -s "${capture_path}" ]]; then
        status=0
      else
        status=124
        echo "error: qbasic did not complete within ${timeout_spec}; QBasic may not have run (set --timeout or CLASSIC_BASIC_DOSEMU_BATCH_TIMEOUT to adjust)" >&2
        echo "diagnostic info appended to ${runtime_dir}/dosemu.log" >&2
      fi
    fi
  fi

  if [[ -f "${capture_path}" ]] && [[ -s "${capture_path}" ]]; then
    python3 "${ROOT_DIR}/src/dos_batch_filter.py" <"${capture_path}"
  else
    out_txt_path="$(find "${runtime_dir}/drive_c" -maxdepth 1 -iname 'out.txt' 2>/dev/null | head -1)"
    if [[ -n "${out_txt_path}" ]] && [[ -s "${out_txt_path}" ]]; then
      # out.txt written by BASIC via OPEN "OUT.TXT" FOR OUTPUT; skip terminal fallback.
      python3 "${ROOT_DIR}/src/dos_batch_filter.py" <"${out_txt_path}"
    elif [[ -n "${terminal_output}" ]] && [[ -s "${terminal_output}" ]]; then
      # Last resort: dosemu terminal rendering (when EMUFS writes are unavailable).
      python3 "${ROOT_DIR}/src/dos_batch_filter.py" <"${terminal_output}"
    fi
    out_txt_path=""  # already handled above
  fi
  [[ -n "${terminal_output}" ]] && rm -f "${terminal_output}"

  if [[ -n "${out_txt_path}" ]] && [[ -s "${out_txt_path}" ]]; then
    python3 "${ROOT_DIR}/src/dos_batch_filter.py" <"${out_txt_path}"
  fi
  return "${status}"
}
