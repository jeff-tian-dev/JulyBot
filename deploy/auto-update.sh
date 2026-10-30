#!/usr/bin/env bash
# Pull origin/main and redeploy the bot when it has moved. Run every minute by
# the com.julybot.autoupdate launchd agent (./deploy/install-autoupdate.sh);
# also safe to run by hand. Output goes to logs/auto-update.log.
#
# A new commit is deployed only if the checkout is on main, has no uncommitted
# changes to tracked files, hasn't diverged from origin, and the test suite
# passes against the new commit. If the restarted bot doesn't log in, the
# previous commit is restored. A skipped or failed attempt isn't repeated until
# something changes: a new push, a local commit, or the local edits.
#
# launchd never starts a second copy of a job that is still running, so slow
# runs (tests, pip) can't overlap.
set -euo pipefail

# Everything runs inside main(): bash reads a script lazily, and this script
# rewrites itself when a pull touches it. Parsing the whole body up front keeps
# the running copy intact.
main() {
  local ROOT
  ROOT="$(cd "$(dirname "$0")/.." && pwd)"
  cd "$ROOT"

  local LABEL="com.julybot"
  local BRANCH="main"
  local PY="$ROOT/.venv/bin/python"
  local BOT_LOG="$ROOT/logs/julybot.stderr.log"
  local PYTEST_LOG="$ROOT/logs/auto-update.pytest.log"
  local STATE_FILE="$ROOT/data/auto-update.last-attempt"
  local READY_PATTERN="Discord bot ready as"
  # Normal startup logs in within seconds; a migration stuck on a lock can take
  # Supabase's 2-minute statement_timeout to fail (see CLAUDE.md 2026-10-29).
  local READY_TIMEOUT_SECONDS=180
  local SERVICE="gui/$(id -u)/$LABEL"

  git fetch --quiet origin "$BRANCH"
  local prev_sha remote_sha short
  prev_sha=$(git rev-parse HEAD)
  remote_sha=$(git rev-parse "origin/$BRANCH")
  [[ "$prev_sha" == "$remote_sha" ]] && return 0

  # One attempt per (remote commit, local commit, local edits) combination.
  local attempt
  attempt="$remote_sha $prev_sha $(git status --porcelain --untracked-files=no | shasum | cut -c1-12)"
  if [[ -f "$STATE_FILE" && "$(cat "$STATE_FILE")" == "$attempt" ]]; then
    return 0
  fi
  mkdir -p "$(dirname "$STATE_FILE")"
  echo "$attempt" > "$STATE_FILE"

  short=$(git rev-parse --short "$remote_sha")
  log "origin/$BRANCH is at $short: $(git log -1 --format=%s "$remote_sha")"

  local current_branch
  current_branch=$(git rev-parse --abbrev-ref HEAD)
  if [[ "$current_branch" != "$BRANCH" ]]; then
    log "SKIP: checkout is on '$current_branch', not $BRANCH"
    return 0
  fi
  if ! git merge-base --is-ancestor HEAD "$remote_sha"; then
    log "SKIP: local $BRANCH has commits that aren't on origin (push them, or reset to origin/$BRANCH)"
    return 0
  fi
  if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    log "SKIP: uncommitted changes to tracked files (commit or stash them and it retries):"
    git status --short --untracked-files=no
    return 0
  fi

  if ! git diff --quiet "$prev_sha" "$remote_sha" -- requirements.txt; then
    log "requirements.txt changed; installing"
    if ! "$PY" -m pip install --quiet -r <(git show "$remote_sha:requirements.txt"); then
      log "SKIP: pip install failed"
      return 0
    fi
  fi

  # Test the new commit in a throwaway worktree, so a failing commit never
  # reaches the live checkout.
  local tmp
  tmp=$(mktemp -d)
  trap "git -C '$ROOT' worktree remove --force '$tmp' 2>/dev/null || rm -rf '$tmp'" EXIT
  git worktree add --quiet --detach "$tmp" "$remote_sha"
  if ! (cd "$tmp" && "$PY" -m pytest tests/ -q -p no:cacheprovider) > "$PYTEST_LOG" 2>&1; then
    log "SKIP: tests failed on $short; full output in logs/auto-update.pytest.log"
    tail -n 5 "$PYTEST_LOG"
    return 0
  fi
  log "tests passed: $(tail -n 1 "$PYTEST_LOG")"

  if ! git merge --quiet --ff-only "$remote_sha"; then
    log "SKIP: fast-forward failed (an untracked file would probably be overwritten)"
    return 0
  fi
  if ! git diff --quiet "$prev_sha" "$remote_sha" -- .env.example; then
    log "NOTE: .env.example changed; copy any new keys into .env by hand"
  fi
  if ! git diff --quiet "$prev_sha" "$remote_sha" -- deploy/com.julybot.plist.template; then
    log "NOTE: the launchd plist template changed; run ./deploy/install-service.sh by hand to apply it"
  fi

  if ! launchctl print "$SERVICE" >/dev/null 2>&1; then
    log "Updated checkout to $short; $LABEL isn't loaded, so nothing was restarted"
    return 0
  fi

  if restart_and_wait; then
    log "DEPLOYED $short"
    return 0
  fi

  log "FAIL: bot didn't log in within ${READY_TIMEOUT_SECONDS}s on $short; rolling back to $(git rev-parse --short "$prev_sha")"
  git reset --quiet --keep "$prev_sha"
  if restart_and_wait; then
    log "Rolled back to $(git rev-parse --short "$prev_sha"); $short is not deployed. Check logs/julybot.stderr.log"
  else
    log "FAIL: the rollback didn't log in either. Check logs/julybot.stderr.log"
  fi
}

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }

# Restart the bot and wait for a login line written after the restart. Uses the
# caller's SERVICE / BOT_LOG / READY_* locals.
restart_and_wait() {
  local offset waited=0
  offset=$(stat -f %z "$BOT_LOG" 2>/dev/null || echo 0)
  # kickstart -k restarts in place. install-service.sh's bootout + bootstrap
  # can race the old process's shutdown ("Bootstrap failed: 5").
  launchctl kickstart -k "$SERVICE"
  while (( waited < READY_TIMEOUT_SECONDS )); do
    sleep 3
    waited=$((waited + 3))
    if tail -c +"$((offset + 1))" "$BOT_LOG" 2>/dev/null | grep -q "$READY_PATTERN"; then
      return 0
    fi
  done
  return 1
}

main "$@"
exit
