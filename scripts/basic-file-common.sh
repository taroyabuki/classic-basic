#!/usr/bin/env bash
set -euo pipefail

canonical_basic_filename() {
  local source_path="$1"

  python3 - "${source_path}" <<'PY'
import pathlib
import re
import sys

name = pathlib.Path(sys.argv[1]).name
stem = pathlib.Path(name).stem or "PROGRAM"
stem = re.sub(r"[^A-Za-z0-9]", "_", stem).upper()[:8] or "PROGRAM"
print(f"{stem}.BAS")
PY
}

normalize_basic_program() {
  local source_path="$1"
  local destination_path="$2"

  [[ -f "${source_path}" ]] || {
    echo "error: program not found: ${source_path}" >&2
    exit 2
  }

  python3 - "${source_path}" "${destination_path}" <<'PY'
import pathlib
import sys

source_path = pathlib.Path(sys.argv[1])
destination_path = pathlib.Path(sys.argv[2])

data = source_path.read_bytes()
text = data.decode("ascii")
text = text.replace("\r\n", "\n").replace("\r", "\n")

destination_path.parent.mkdir(parents=True, exist_ok=True)
destination_path.write_bytes(text.replace("\n", "\r\n").encode("ascii"))
PY
}

dosemu_needs_sem_shim() {
  case "${CLASSIC_BASIC_DOSEMU_SEM_SHIM:-auto}" in
    1|on|true|yes)
      return 0
      ;;
    0|off|false|no)
      return 1
      ;;
  esac

  python3 - <<'PY'
import ctypes
import errno
import os

libc = ctypes.CDLL(None, use_errno=True)
libc.sem_open.restype = ctypes.c_void_p
libc.sem_open.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
libc.sem_close.argtypes = [ctypes.c_void_p]
libc.sem_unlink.argtypes = [ctypes.c_char_p]

name = f"/classic_basic_probe_{os.getpid()}".encode("ascii")
ptr = libc.sem_open(name, os.O_CREAT | os.O_EXCL, 0o600, 1)
err = ctypes.get_errno()
if ptr and ptr != ctypes.c_void_p(-1).value:
    libc.sem_close(ctypes.c_void_p(ptr))
    libc.sem_unlink(name)
    raise SystemExit(1)
if err == errno.EEXIST:
    raise SystemExit(1)
raise SystemExit(0)
PY
}

ensure_dosemu_sem_shim() {
  local source_path="${ROOT_DIR}/src/dosemu_named_sem_shim.c"
  local build_dir="${ROOT_DIR}/build"
  local output_path="${build_dir}/dosemu_named_sem_shim.so"

  [[ -f "${source_path}" ]] || {
    echo "error: dosemu semaphore shim source is missing: ${source_path}" >&2
    exit 2
  }

  mkdir -p "${build_dir}"
  if [[ ! -f "${output_path}" || "${source_path}" -nt "${output_path}" ]]; then
    cc -shared -fPIC -O2 -pthread -o "${output_path}" "${source_path}" -ldl
  fi
  printf '%s\n' "${output_path}"
}

dosemu_preload_value() {
  local shim_path

  if ! dosemu_needs_sem_shim; then
    return 0
  fi

  shim_path="$(ensure_dosemu_sem_shim)"
  if [[ -n "${LD_PRELOAD:-}" ]]; then
    printf '%s:%s\n' "${shim_path}" "${LD_PRELOAD}"
    return 0
  fi
  printf '%s\n' "${shim_path}"
}

dosemu_command() {
  local cmd

  cmd="$(command -v dosemu 2>/dev/null || true)"
  if [[ -n "${cmd}" ]]; then
    printf '%s\n' "${cmd}"
    return 0
  fi

  cmd="$(command -v dosemu2 2>/dev/null || true)"
  if [[ -n "${cmd}" ]]; then
    printf '%s\n' "${cmd}"
    return 0
  fi

  return 1
}

# Returns the path to dosemu2.bin if directly callable, bypassing the bash
# wrapper.  Calling dosemu2.bin directly avoids the wrapper's -t handling
# (exec 4>&2 2>~/.dosemu/stderr.log.$BASHPID) which silently redirects
# dosemu2.bin's stderr away from our dosemu.log and is lost on kill.
# Falls back to 'dosemu' (PATH lookup) when a custom dosemu is in PATH
# (e.g., test mocks) or dosemu2.bin is not installed at the standard path.
dosemu_direct_bin() {
  local bin="/usr/libexec/dosemu2/dosemu2.bin"
  local dosemu_in_path
  dosemu_in_path="$(dosemu_command 2>/dev/null || true)"
  # Only bypass the wrapper when 'dosemu' in PATH is the standard system
  # wrapper that would invoke dosemu2.bin anyway; preserve any custom dosemu
  # (such as test mocks) by falling back to PATH lookup.
  if [[ -x "${bin}" && "${dosemu_in_path}" == "/usr/bin/dosemu" ]]; then
    printf '%s\n' "${bin}"
    return 0
  fi

  if [[ -n "${dosemu_in_path}" ]]; then
    printf '%s\n' "${dosemu_in_path}"
    return 0
  fi

  printf 'dosemu\n'
}

dosemu_batch_use_pty() {
  # Default is auto: use a clean PTY when util-linux `script` can allocate one,
  # otherwise fall back to direct non-PTY execution. Explicit on/off remains
  # available for tests and debugging.
  case "${CLASSIC_BASIC_DOSEMU_PTY:-auto}" in
    1 | on | true | yes) return 0 ;;
    0 | off | false | no) return 1 ;;
    auto)
      can_run_script_pty
      return
      ;;
    *)
      return 1
      ;;
  esac
}

dosemu_fdppconf_template() {
  local candidate

  if [[ -n "${CLASSIC_BASIC_DOSEMU_FDPPCONF_TEMPLATE:-}" ]]; then
    candidate="${CLASSIC_BASIC_DOSEMU_FDPPCONF_TEMPLATE}"
    [[ -f "${candidate}" ]] && printf '%s\n' "${candidate}" && return 0
    return 1
  fi

  for candidate in \
    /usr/share/dosemu/dosemu2-cmds-0.3/fdppconf.sys \
    /usr/share/dosemu/commands/fdppconf.sys
  do
    [[ -f "${candidate}" ]] && printf '%s\n' "${candidate}" && return 0
  done

  return 1
}

prepare_dosemu_home() {
  local home_dir="$1"
  local fdppconf_path="${home_dir}/.dosemu/fdppconf.sys"
  local template_path=""

  template_path="$(dosemu_fdppconf_template 2>/dev/null || true)"
  [[ -n "${template_path}" ]] || return 0

  python3 - "${template_path}" "${fdppconf_path}" <<'PY'
import pathlib
import sys

source_path = pathlib.Path(sys.argv[1])
destination_path = pathlib.Path(sys.argv[2])

text = source_path.read_text(encoding="utf-8")
lines = []
for line in text.splitlines():
    if line.strip().lower() == r"devicehigh=dosemu\cdrom.sys":
        continue
    lines.append(line)

destination_path.parent.mkdir(parents=True, exist_ok=True)
destination_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

dosemu_interactive_pty_backend() {
  case "${CLASSIC_BASIC_DOSEMU_INTERACTIVE_PTY:-auto}" in
    1 | on | true | yes | script) printf 'script\n' ;;
    expect) printf 'expect\n' ;;
    0 | off | false | no | direct) printf 'direct\n' ;;
    *) printf 'auto\n' ;;
  esac
}

can_run_script_pty() {
  command -v script >/dev/null 2>&1 || return 1
  script -q -e /dev/null -c true >/dev/null 2>&1
}

can_run_expect_pty() {
  command -v expect >/dev/null 2>&1 || return 1
  expect -c '
    set timeout 2
    if {[catch {spawn true}]} {
      exit 1
    }
    expect eof
  ' >/dev/null 2>&1
}

timeout_spec_to_milliseconds() {
  local timeout_spec="$1"

  python3 - "${timeout_spec}" <<'PY'
import math
import re
import sys

spec = sys.argv[1].strip()
if not spec or spec == "0":
    print(0)
    raise SystemExit(0)

match = re.fullmatch(r"([0-9]+(?:\.[0-9]*)?|[0-9]*\.[0-9]+)\s*([smhdSMHD]?)", spec)
if match is None:
    print(f"error: invalid timeout spec: {spec}", file=sys.stderr)
    raise SystemExit(2)

value = float(match.group(1))
unit = (match.group(2) or "s").lower()
factor = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}[unit]
print(max(1, math.ceil(value * factor * 1000.0)))
PY
}

# run_dosemu_in_pty TIMEOUT LOG_FILE CMD [ARGS...]
# Allocates a pseudo-TTY via 'script' and runs CMD inside it.
# Requires util-linux script >= 2.35 for the -e flag.
# TIMEOUT may be empty or 0 for no timeout.
run_dosemu_in_pty() {
  local timeout_spec="$1" log_file="$2"
  shift 2
  local tmpscript status=0
  tmpscript="$(mktemp /tmp/dosemu_pty_XXXXXX.sh)"
  {
    printf '#!/usr/bin/env bash\nexec'
    printf ' %q' "$@"
    printf ' 2>>%q\n' "${log_file}"
  } > "${tmpscript}"
  chmod +x "${tmpscript}"
  if [[ -n "${timeout_spec}" && "${timeout_spec}" != "0" ]]; then
    timeout --foreground "${timeout_spec}" \
      script -q -e /dev/null -c "${tmpscript}" 2>>"${log_file}" || status=$?
  else
    script -q -e /dev/null -c "${tmpscript}" 2>>"${log_file}" || status=$?
  fi
  rm -f "${tmpscript}"
  return "${status}"
}

run_dosemu() {
  local home_dir="$1"
  shift
  local preload="" dosemu_bin=""

  preload="$(dosemu_preload_value)"
  dosemu_bin="$(dosemu_command)"
  if [[ -n "${preload}" ]]; then
    env HOME="${home_dir}" LD_PRELOAD="${preload}" "${dosemu_bin}" "$@"
    return
  fi
  env HOME="${home_dir}" "${dosemu_bin}" "$@"
}

exec_dosemu() {
  local home_dir="$1"
  shift
  local preload="" dosemu_bin=""

  preload="$(dosemu_preload_value)"
  dosemu_bin="$(dosemu_command)"
  if [[ -n "${preload}" ]]; then
    exec env HOME="${home_dir}" LD_PRELOAD="${preload}" "${dosemu_bin}" "$@"
  fi
  HOME="${home_dir}" exec "${dosemu_bin}" "$@"
}

# exec_dosemu_in_pty HOME_DIR LOG_FILE CMD [ARGS...]
# Like exec_dosemu but allocates a clean PTY via 'script'.
# Fixes keyboard glitches when the caller's terminal has stale settings.
exec_dosemu_in_pty() {
  local home_dir="$1" log_file="$2"
  shift 2
  local preload tmpscript dosemu_bin

  preload="$(dosemu_preload_value)"
  dosemu_bin="$(dosemu_command)"
  tmpscript="$(mktemp /tmp/dosemu_pty_XXXXXX.sh)"
  {
    printf '#!/usr/bin/env bash\nrm -f %q\nexec' "${tmpscript}"
    if [[ -n "${preload}" ]]; then
      printf ' env HOME=%q LD_PRELOAD=%q %q' "${home_dir}" "${preload}" "${dosemu_bin}"
    else
      printf ' env HOME=%q %q' "${home_dir}" "${dosemu_bin}"
    fi
    printf ' %q' "$@"
    printf ' 2>>%q\n' "${log_file}"
  } > "${tmpscript}"
  chmod +x "${tmpscript}"
  exec script -q -e /dev/null -c "${tmpscript}"
}

# exec_dosemu_interactive HOME_DIR LOG_FILE CMD [ARGS...]
# Prefers a clean PTY via 'script', but falls back to 'expect' in sandboxes
# where nested PTY allocation is denied. Direct execution remains available as
# an explicit override for environments where neither PTY helper is needed.
exec_dosemu_interactive() {
  local home_dir="$1" log_file="$2"
  shift 2
  local backend preload tmpscript dosemu_bin

  backend="$(dosemu_interactive_pty_backend)"
  case "${backend}" in
    script)
      exec_dosemu_in_pty "${home_dir}" "${log_file}" "$@"
      ;;
    direct)
      exec_dosemu "${home_dir}" "$@"
      ;;
  esac

  if can_run_script_pty; then
    exec_dosemu_in_pty "${home_dir}" "${log_file}" "$@"
  fi

  if [[ "${backend}" == "expect" || "${backend}" == "auto" ]]; then
    can_run_expect_pty || {
      echo "error: interactive dosemu fallback requires 'expect' when 'script' cannot allocate a PTY" >&2
      exit 2
    }

    preload="$(dosemu_preload_value)"
    dosemu_bin="$(dosemu_command)"
    tmpscript="$(mktemp /tmp/dosemu_expect_XXXXXX.sh)"
    {
      printf '#!/usr/bin/env bash\nrm -f %q\nexec' "${tmpscript}"
      if [[ -n "${preload}" ]]; then
        printf ' env HOME=%q LD_PRELOAD=%q %q' "${home_dir}" "${preload}" "${dosemu_bin}"
      else
        printf ' env HOME=%q %q' "${home_dir}" "${dosemu_bin}"
      fi
      printf ' %q' "$@"
      printf ' 2>>%q\n' "${log_file}"
    } > "${tmpscript}"
    chmod +x "${tmpscript}"
    exec expect -c '
      log_user 1
      set timeout -1
      spawn -noecho -- [lindex $argv 0]
      interact
      if {[catch wait result]} {
        exit 1
      }
      if {[llength $result] >= 4} {
        exit [lindex $result 3]
      }
      exit 0
    ' "${tmpscript}"
  fi

  exec_dosemu "${home_dir}" "$@"
}
