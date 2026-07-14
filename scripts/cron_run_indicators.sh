#!/usr/bin/env bash
# Daily market analysis indicator job for Ubuntu deployment.
# Suggested cron (crontab -e), after market_data daily update:
#   30 10 * * 2-6 /usr/bin/env bash /home/zouxc/market_analysis/scripts/cron_run_indicators.sh

set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || exit 1
LOG_DIR="${PROJECT_DIR}/logs"
RUN_TS="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="${LOG_DIR}/cron_run_indicators_${RUN_TS}.log"
LOCK_FILE="${PROJECT_DIR}/.market_analysis_run_indicators.lock"
CLI_BIN="${PROJECT_DIR}/.venv/bin/market-analysis"

DEFAULT_MAIL_TO="batmanzxc@163.com"
DEFAULT_MAIL_FROM="xuecongzou@163.com"

mkdir -p "${LOG_DIR}" || exit 1
cd "${PROJECT_DIR}" || exit 1

log_line() {
  echo "${1:-}" >> "${LOG_FILE}"
}

send_mail() {
  local subject="$1"
  local body_file="$2"

  if [ ! -x "${MSMTP_BIN}" ]; then
    log_line "=== $(date '+%Y-%m-%d %H:%M:%S') Mail not sent: ${MSMTP_BIN} not found or not executable ==="
    return 0
  fi

  {
    echo "Subject: ${subject}"
    echo "To: ${MAIL_TO}"
    echo "From: ${MAIL_FROM}"
    echo "Content-Type: text/plain; charset=UTF-8"
    echo
    cat "${body_file}"
  } | "${MSMTP_BIN}" "${MAIL_TO}" || {
    log_line "=== $(date '+%Y-%m-%d %H:%M:%S') Mail send FAILED ==="
    return 0
  }
}

handle_interrupt() {
  local signal_name="$1"
  local exit_code="$2"

  set +e
  log_line
  log_line "=== $(date '+%Y-%m-%d %H:%M:%S') Indicator job INTERRUPTED (${signal_name}) ==="
  log_line "Exit code: ${exit_code}"
  send_mail "[INTERRUPTED] market_analysis run-indicators on ${HOSTNAME_VALUE}" "${LOG_FILE}"
  exit "${exit_code}"
}

run_step() {
  local step_name="$1"
  local command_display="$2"
  shift 2

  local exit_code

  log_line
  log_line "=== $(date '+%Y-%m-%d %H:%M:%S') ${step_name} STARTED ==="
  log_line "Command: ${command_display}"
  log_line

  PYTHONUNBUFFERED=1 "$@" >> "${LOG_FILE}" 2>&1
  exit_code=$?

  log_line
  if [ "${exit_code}" -eq 0 ]; then
    log_line "=== $(date '+%Y-%m-%d %H:%M:%S') ${step_name} completed successfully ==="
  elif [ "${exit_code}" -eq 130 ]; then
    log_line "=== $(date '+%Y-%m-%d %H:%M:%S') ${step_name} INTERRUPTED (INT) ==="
  elif [ "${exit_code}" -eq 143 ]; then
    log_line "=== $(date '+%Y-%m-%d %H:%M:%S') ${step_name} INTERRUPTED (TERM) ==="
  else
    log_line "=== $(date '+%Y-%m-%d %H:%M:%S') ${step_name} FAILED ==="
  fi
  log_line "Exit code: ${exit_code}"

  return "${exit_code}"
}

# Load environment variables for database credentials and optional mail overrides.
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

MAIL_TO="${MAIL_TO:-${DEFAULT_MAIL_TO}}"
MAIL_FROM="${MAIL_FROM:-${DEFAULT_MAIL_FROM}}"
MSMTP_BIN="${MSMTP_BIN:-/usr/bin/msmtp}"
HOSTNAME_VALUE="$(hostname)"

trap 'handle_interrupt INT 130' INT
trap 'handle_interrupt TERM 143' TERM

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  log_line "=== $(date '+%Y-%m-%d %H:%M:%S') Previous indicator job is still running; skipped ==="
  log_line "Host: ${HOSTNAME_VALUE}"
  log_line "Project dir: ${PROJECT_DIR}"
  log_line "Log file: ${LOG_FILE}"
  log_line "Lock file: ${LOCK_FILE}"
  log_line "Exit code: 0"
  send_mail "[SKIPPED] market_analysis run-indicators on ${HOSTNAME_VALUE}" "${LOG_FILE}"
  exit 0
fi

log_line "=== $(date '+%Y-%m-%d %H:%M:%S') Starting market_analysis indicator job ==="
log_line "Host: ${HOSTNAME_VALUE}"
log_line "Project dir: ${PROJECT_DIR}"
log_line "Log file: ${LOG_FILE}"
log_line "Lock file: ${LOCK_FILE}"
log_line "CLI: ${CLI_BIN}"

if [ ! -x "${CLI_BIN}" ]; then
  log_line
  log_line "=== $(date '+%Y-%m-%d %H:%M:%S') Indicator job FAILED ==="
  log_line "Reason: ${CLI_BIN} not found or not executable"
  log_line "Exit code: 127"
  send_mail "[FAILED] market_analysis run-indicators on ${HOSTNAME_VALUE}" "${LOG_FILE}"
  exit 127
fi

run_step \
  "Step 1/1: market-analysis run-indicators" \
  "${CLI_BIN} run-indicators" \
  "${CLI_BIN}" run-indicators
INDICATORS_EXIT_CODE=$?

if [ "${INDICATORS_EXIT_CODE}" -eq 0 ]; then
  OVERALL_STATUS="SUCCESS"
  OVERALL_EXIT_CODE=0
elif [ "${INDICATORS_EXIT_CODE}" -eq 130 ] || [ "${INDICATORS_EXIT_CODE}" -eq 143 ]; then
  OVERALL_STATUS="INTERRUPTED"
  OVERALL_EXIT_CODE="${INDICATORS_EXIT_CODE}"
else
  OVERALL_STATUS="FAILED"
  OVERALL_EXIT_CODE="${INDICATORS_EXIT_CODE}"
fi

log_line
log_line "=== $(date '+%Y-%m-%d %H:%M:%S') Indicator job ${OVERALL_STATUS} ==="
log_line "Run indicators exit code: ${INDICATORS_EXIT_CODE}"
log_line "Overall exit code: ${OVERALL_EXIT_CODE}"

send_mail "[${OVERALL_STATUS}] market_analysis run-indicators on ${HOSTNAME_VALUE}" "${LOG_FILE}"

exit "${OVERALL_EXIT_CODE}"
