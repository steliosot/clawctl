#!/usr/bin/env bash
set -euo pipefail

REPO_URL="git+https://github.com/steliosot/clawctl.git"
RAW_INSTALL_URL="https://raw.githubusercontent.com/steliosot/clawctl/main/scripts/install.sh"
USER_BIN="${HOME}/.local/bin"
SHELL_NAME="$(basename "${SHELL:-bash}")"

log() {
  printf '%s\n' "$*"
}

have() {
  command -v "$1" >/dev/null 2>&1
}

append_path_line() {
  local profile="$1"
  local line='export PATH="$HOME/.local/bin:$PATH"'
  [ -f "$profile" ] || touch "$profile"
  if ! grep -Fq "$line" "$profile"; then
    printf '\n%s\n' "$line" >>"$profile"
  fi
}

require_python() {
  if ! have python3; then
    log "python3 is required. Install Python 3.10+ and retry."
    exit 1
  fi
  python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10+ is required.")
PY
}

ensure_prereqs() {
  require_python
  if ! have git; then
    log "git is required. Install git and retry."
    exit 1
  fi
  if ! python3 -m pip --version >/dev/null 2>&1; then
    log "pip for python3 is required. Install pip and retry."
    exit 1
  fi
}

install_with_pipx_or_pip() {
  if have pipx; then
    log "Installing with pipx..."
    pipx install --force "${REPO_URL}" >/dev/null || pipx install "${REPO_URL}" >/dev/null
    return
  fi

  log "pipx not found; installing with python3 -m pip --user..."
  python3 -m pip install --user --upgrade "${REPO_URL}"
}

resolve_clawctl_path() {
  if have clawctl; then
    command -v clawctl
    return
  fi

  local user_base
  user_base="$(python3 -m site --user-base)"
  if [ -x "${user_base}/bin/clawctl" ]; then
    printf '%s\n' "${user_base}/bin/clawctl"
    return
  fi

  if [ -x "${HOME}/.local/pipx/venvs/clawctl/bin/clawctl" ]; then
    printf '%s\n' "${HOME}/.local/pipx/venvs/clawctl/bin/clawctl"
    return
  fi

  return 1
}

install_global_wrapper() {
  local target="$1"
  if [ "$target" = "/usr/local/bin/clawctl" ]; then
    return 0
  fi
  if [ -w /usr/local/bin ]; then
    ln -sf "$target" /usr/local/bin/clawctl
    return
  fi
  if have sudo && sudo -n true 2>/dev/null; then
    sudo ln -sf "$target" /usr/local/bin/clawctl
    return
  fi
  return 1
}

ensure_user_wrapper_and_path() {
  local target="$1"
  mkdir -p "$USER_BIN"
  if [ "$target" != "${USER_BIN}/clawctl" ]; then
    ln -sf "$target" "${USER_BIN}/clawctl"
  fi

  append_path_line "${HOME}/.profile"
  append_path_line "${HOME}/.bashrc"
  append_path_line "${HOME}/.zshrc"
  export PATH="${USER_BIN}:$PATH"
}

main() {
  log "Installing clawctl..."
  ensure_prereqs
  install_with_pipx_or_pip

  local clawctl_path
  if ! clawctl_path="$(resolve_clawctl_path)"; then
    log "Install completed but clawctl entrypoint was not found."
    log "Try: python3 -m pip install --user --upgrade \"${REPO_URL}\""
    exit 1
  fi

  if install_global_wrapper "$clawctl_path"; then
    export PATH="/usr/local/bin:$PATH"
    log "Linked clawctl into /usr/local/bin."
  else
    ensure_user_wrapper_and_path "$clawctl_path"
    log "Linked clawctl into ${USER_BIN} and updated shell profiles."
  fi

  hash -r || true

  if ! clawctl --help >/dev/null 2>&1; then
    log "Install finished, but clawctl is not yet on this shell PATH."
    log "Open a new shell, or run: export PATH=\"${USER_BIN}:\$PATH\""
    log "Fallback: python3 -m clawctl --help"
    exit 1
  fi

  log "clawctl is ready."
  log "Try:"
  log "  clawctl --help"
  log "  clawctl up"
  log "Installer URL: ${RAW_INSTALL_URL}"
}

main "$@"
