# Shared helpers for mini-pc shell entrypoints (sourced, do not execute directly).
# Keeps optional monorepo / shared-root .env loading explicit and overrideable.

# Resolve directory that may contain a shared `.env` (API secrets, APP_BASE_URL, etc.).
# - If MEETINGBOX_MONOREPO_ROOT is set: use it when it is a real directory (absolute path recommended).
# - Else if ../server/docker-compose.yml exists next to mini-pc: parent dir (full monorepo checkout).
# - Else if ../meetingbox-server/docker-compose.yml exists: parent dir (common sibling server clone name).
# - Else: empty string (standalone mini-pc repo — only mini-pc/.env is loaded).
meetingbox_resolve_monorepo_root() {
  local mini_pc_root="$1"
  local r=""
  if [[ -n "${MEETINGBOX_MONOREPO_ROOT:-}" ]]; then
    r="$(cd "${MEETINGBOX_MONOREPO_ROOT}" 2>/dev/null && pwd)" || r=""
    if [[ -n "$r" ]]; then
      echo "$r"
      return 0
    fi
    echo "[MeetingBox] MEETINGBOX_MONOREPO_ROOT not a directory — trying auto-detect: ${MEETINGBOX_MONOREPO_ROOT}" >&2
  fi
  if [[ -f "$mini_pc_root/../server/docker-compose.yml" ]]; then
    (cd "$mini_pc_root/.." && pwd)
    return 0
  fi
  if [[ -f "$mini_pc_root/../meetingbox-server/docker-compose.yml" ]]; then
    (cd "$mini_pc_root/.." && pwd)
    return 0
  fi
  echo ""
}
