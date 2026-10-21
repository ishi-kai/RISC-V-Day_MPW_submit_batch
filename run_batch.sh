#!/usr/bin/env bash
# Batch entry point for cron (e.g. every 30 minutes):
#   */30 * * * * /home/noritsuna/submit_inverter/run_batch.sh
# Settings come from batch.env next to this script (see batch.env.example).
# Each run's output is appended to logs/batch_YYYYMMDD.log.
# Arguments are passed on to import_submissions.py, e.g. `./run_batch.sh --reset` to start over.
set -uo pipefail

cd "$(dirname "$0")"
[ -f batch.env ] && . ./batch.env
export PATH="${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin:${PATH}"
[ -n "${GITHUB_TOKEN:-}" ] && export GITHUB_TOKEN
export SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}" SSH_KEY_PASSPHRASE="${SSH_KEY_PASSPHRASE:-}"
export PUBLISH_GIT_NAME="${PUBLISH_GIT_NAME:-submit_inverter}" PUBLISH_GIT_EMAIL="${PUBLISH_GIT_EMAIL:-submit_inverter@localhost}"
PUB=()
[ -n "${PUBLISH_REPO:-}" ] && PUB=(--publish-repo "${PUBLISH_REPO}" --publish-branch "${PUBLISH_BRANCH:-main}")

if [ -n "${SHEET_ID:-}" ]; then
  SRC=(--sheet-id "${SHEET_ID}" --credentials "${CREDENTIALS}")
  [ -n "${SHEET_RANGE:-}" ] && SRC+=(--range "${SHEET_RANGE}")
elif [ -n "${RESPONSES_CSV:-}" ]; then
  SRC=(--responses "${RESPONSES_CSV}")
else
  echo "set SHEET_ID (+ CREDENTIALS) or RESPONSES_CSV in batch.env" >&2
  exit 1
fi

mkdir -p logs
LOG="logs/batch_$(date +%Y%m%d).log"
{
  echo "==== $(date '+%F %T') start"
  python3 import_submissions.py "${SRC[@]}" "${PUB[@]}" ${THREADS:+--threads "${THREADS}"} "$@"
  rc=$?
  echo "==== $(date '+%F %T') end (rc=${rc})"
} >> "${LOG}" 2>&1
