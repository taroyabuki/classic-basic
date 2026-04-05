#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

run_as_root() {
  if [[ "${EUID}" == "0" ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    echo "error: need root or sudo to install packages" >&2
    return 1
  fi
}

dosemu_available() {
  command -v dosemu >/dev/null 2>&1 || command -v dosemu2 >/dev/null 2>&1
}

apt_package_has_candidate() {
  local package="$1"
  local apt_output
  apt_output="$(apt-cache policy "${package}" 2>/dev/null || true)"
  [[ -n "${apt_output}" ]] || return 1
  grep -Eq 'Candidate:[[:space:]]*\(none\)$' <<<"${apt_output}" && return 1
  grep -Eq 'Candidate:[[:space:]]*[^[:space:]]+' <<<"${apt_output}"
}

os_release_value() {
  local key="$1"
  local value
  value="$(
    KEY="${key}" python3 - <<'PY'
import os

key = os.environ["KEY"]
data = {}
with open("/etc/os-release", "r", encoding="utf-8") as fh:
    for raw_line in fh:
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        name, raw_value = line.split("=", 1)
        data[name] = raw_value.strip().strip('"')
print(data.get(key, ""))
PY
  )"
  printf '%s\n' "${value}"
}

try_install_apt_package() {
  local package
  for package in "$@"; do
    if ! apt_package_has_candidate "${package}"; then
      continue
    fi
    if run_as_root apt-get install -y "${package}"; then
      return 0
    fi
  done
  return 1
}

install_dosemu_via_ubuntu_ppa() {
  local distro_id
  distro_id="$(os_release_value ID)"
  [[ "${distro_id}" == "ubuntu" ]] || return 1

  run_as_root apt-get install -y software-properties-common
  if ! grep -Rqs '^deb .*ppa.launchpadcontent.net/dosemu2/ppa/ubuntu' /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null; then
    run_as_root add-apt-repository -y ppa:dosemu2/ppa
    run_as_root apt-get update
  fi

  run_as_root apt-get install -y dosemu2
}

install_debian_packages() {
  export DEBIAN_FRONTEND=noninteractive
  run_as_root apt-get update
  run_as_root apt-get install -y \
    ca-certificates \
    curl \
    expect \
    g++ \
    gcc \
    git \
    make \
    mtools \
    python3 \
    python3-pil \
    python3-pytest \
    python3-setuptools \
    util-linux

  if ! command -v 7z >/dev/null 2>&1; then
    try_install_apt_package p7zip-full 7zip || {
      echo "error: failed to install a package that provides 7z" >&2
      return 1
    }
  fi

  if ! command -v openmsx >/dev/null 2>&1; then
    try_install_apt_package openmsx || {
      echo "error: failed to install openmsx" >&2
      return 1
    }
  fi

  if ! dosemu_available; then
    try_install_apt_package dosemu2 dosemu || {
      install_dosemu_via_ubuntu_ppa || {
        echo "error: failed to install dosemu or dosemu2" >&2
        return 1
      }
    }
  fi

  command -v openmsx >/dev/null 2>&1 || {
    echo "error: openmsx is not installed" >&2
    return 1
  }

  dosemu_available || {
    echo "error: dosemu or dosemu2 is not installed" >&2
    return 1
  }
}

if [[ "${CLASSIC_BASIC_SKIP_APT:-0}" != "1" ]] && command -v apt-get >/dev/null 2>&1; then
  install_debian_packages
fi

export PYTHONPATH="${ROOT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"

echo "==> setup/6502.sh"
bash ./setup/6502.sh

echo "==> setup/basic80.sh"
bash ./setup/basic80.sh

echo "==> setup/grantsbasic.sh"
bash ./setup/grantsbasic.sh

echo "==> setup/nbasic.sh"
bash ./setup/nbasic.sh

echo "==> setup/n88basic.sh"
bash ./setup/n88basic.sh

echo "==> setup/msxbasic.sh"
bash ./setup/msxbasic.sh

echo "==> setup/gwbasic.sh"
bash ./setup/gwbasic.sh

echo "==> setup/qbasic.sh"
bash ./setup/qbasic.sh

echo "==> pytest (self-contained tests)"
python3 -m pytest tests -q -k 'not fidelity_audit'

echo "==> pytest (runtime smoke tests)"
python3 -m pytest tests/test_fidelity_audit.py -q -rs
