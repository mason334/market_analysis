#!/usr/bin/env bash
#
# market_analysis 每日分段分析任务
#
# 执行顺序：
#   1. 自适应初分段
#   2. Pivot 精炼分段
#
# Pivot 精炼分段依赖已经写入数据库的自适应初分段结果。
# 因此，只有第一步成功后才会执行第二步。
#
# 两个 CLI 命令均会自动执行幂等的 init_schema()：
#   - 创建尚不存在的表
#   - 补充尚不存在的字段和索引
#   - 不需要在本脚本中额外运行 market-analysis init-db
#
# 建议的 crontab：
#   0 11 * * 2-6 /usr/bin/env bash /home/zouxc/market_analysis/scripts/cron_run_segmentation_pipeline.sh
#
# 上述时间表示：
#   - 每周二至周六
#   - 北京时间 11:00
#   - 在 10:00 的 market_data 每日更新任务之后执行
#
# 本脚本不再执行 market-analysis run-indicators。
# 因此 support_resistance_daily 和 trend_daily 不会由本任务更新。

set -uo pipefail

# ---------------------------------------------------------------------------
# 路径与文件配置
# ---------------------------------------------------------------------------

# 根据当前脚本所在位置推导项目根目录。脚本位于项目的 scripts 目录，
# dirname 得到 scripts 目录，再通过 /.. 返回项目根目录。
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)" || exit 1

# 每次运行产生独立日志，统一保存到项目 logs 目录。
LOG_DIR="${PROJECT_DIR}/logs"

# 运行时间戳用于生成不会互相覆盖的日志文件名。
RUN_TS="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="${LOG_DIR}/cron_segmentation_${RUN_TS}.log"

# flock 锁定的是打开的文件描述符，而不是根据文件是否存在判断。
# 锁文件长期存在是正常现象，不代表任务仍在运行。
LOCK_FILE="${PROJECT_DIR}/.market_analysis_segmentation.lock"

# 直接使用项目虚拟环境中的 CLI，避免依赖 cron 的 PATH。
CLI_BIN="${PROJECT_DIR}/.venv/bin/market-analysis"

# 确保日志目录存在，然后进入项目根目录。
mkdir -p "${LOG_DIR}" || exit 1
cd "${PROJECT_DIR}" || exit 1


# ---------------------------------------------------------------------------
# 日志辅助函数
# ---------------------------------------------------------------------------

# 向本次任务日志追加一行。使用 printf，避免不同 shell 对 echo 参数的解释差异。
log_line() {
    printf '%s\n' "${1:-}" >> "${LOG_FILE}"
}


# ---------------------------------------------------------------------------
# 环境变量
# ---------------------------------------------------------------------------

# 加载项目 .env，主要用于 market_analysis/market_data 数据库连接和可选邮件配置。
# set -a 会让 source 读取的变量自动 export 给后续 CLI 子进程。
if [ -f .env ]; then
    set -a

    # shellcheck disable=SC1091
    source .env

    set +a
fi

# 邮件配置是可选的，可在 .env 中设置 MAIL_TO、MAIL_FROM 和 MSMTP_BIN。
# 如果收发件地址未设置，任务仍会正常运行，只是不发送通知邮件。
MAIL_TO="${MAIL_TO:-}"
MAIL_FROM="${MAIL_FROM:-}"
MSMTP_BIN="${MSMTP_BIN:-/usr/bin/msmtp}"

# 当前主机名用于日志和邮件标题。
HOSTNAME_VALUE="$(hostname)"


# ---------------------------------------------------------------------------
# 邮件通知函数
# ---------------------------------------------------------------------------

send_mail() {
    local subject="$1"

    # 邮件地址未配置时只记录日志，不把它视为分析任务失败。
    if [ -z "${MAIL_TO}" ] || [ -z "${MAIL_FROM}" ]; then
        log_line "Mail not sent: MAIL_TO or MAIL_FROM is not configured"
        return 0
    fi

    # msmtp 不存在或不可执行时，同样不影响主任务退出状态。
    if [ ! -x "${MSMTP_BIN}" ]; then
        log_line "Mail not sent: ${MSMTP_BIN} is unavailable"
        return 0
    fi

    {
        printf 'Subject: %s\n' "${subject}"
        printf 'To: %s\n' "${MAIL_TO}"
        printf 'From: %s\n' "${MAIL_FROM}"
        printf 'Content-Type: text/plain; charset=UTF-8\n'
        printf '\n'

        # 分段过程可能产生大量进度日志，邮件只附带最后 200 行，避免正文过大。
        tail -n 200 "${LOG_FILE}"
    } | "${MSMTP_BIN}" "${MAIL_TO}" || {
        # 邮件发送失败不覆盖分析任务本身的退出状态。
        log_line "Mail delivery failed"
        return 0
    }
}


# ---------------------------------------------------------------------------
# 中断信号处理
# ---------------------------------------------------------------------------

# 收到 INT/TERM 时记录中断原因、尝试发送通知并返回约定的退出码。
handle_interrupt() {
    local signal_name="$1"
    local exit_code="$2"

    # 避免中断处理过程中某个辅助命令失败导致日志不完整。
    set +e

    log_line
    log_line "=== $(date '+%Y-%m-%d %H:%M:%S') Pipeline interrupted: ${signal_name} ==="
    log_line "Exit code: ${exit_code}"

    send_mail "[INTERRUPTED] market_analysis segmentation on ${HOSTNAME_VALUE}"

    exit "${exit_code}"
}

# 注册常见中断信号：INT 对应 130，TERM 对应 143。
trap 'handle_interrupt INT 130' INT
trap 'handle_interrupt TERM 143' TERM


# ---------------------------------------------------------------------------
# 通用步骤执行函数
# ---------------------------------------------------------------------------

# 执行一个分析步骤，并统一记录开始时间、命令、输出、完成状态和退出码。
# 参数：$1 是步骤名称，$2... 是实际执行的命令及参数。
run_step() {
    local step_name="$1"
    shift

    local exit_code

    log_line
    log_line "=== $(date '+%Y-%m-%d %H:%M:%S') ${step_name} STARTED ==="
    log_line "Command: $*"
    log_line

    # 禁用 Python 输出缓冲，确保进度和错误及时写入日志。
    PYTHONUNBUFFERED=1 "$@" >> "${LOG_FILE}" 2>&1
    exit_code=$?

    log_line

    if [ "${exit_code}" -eq 0 ]; then
        log_line "=== $(date '+%Y-%m-%d %H:%M:%S') ${step_name} SUCCESS ==="
    elif [ "${exit_code}" -eq 130 ]; then
        log_line "=== $(date '+%Y-%m-%d %H:%M:%S') ${step_name} INTERRUPTED: INT ==="
    elif [ "${exit_code}" -eq 143 ]; then
        log_line "=== $(date '+%Y-%m-%d %H:%M:%S') ${step_name} INTERRUPTED: TERM ==="
    else
        log_line "=== $(date '+%Y-%m-%d %H:%M:%S') ${step_name} FAILED ==="
    fi

    log_line "Exit code: ${exit_code}"

    return "${exit_code}"
}


# ---------------------------------------------------------------------------
# 防止任务并发运行
# ---------------------------------------------------------------------------

# 使用文件描述符 9 持有独占锁。如果前一次任务尚未结束，本次任务不并发运行，
# 而是记录为 SKIPPED 并返回 0，避免 cron 把正常跳过误判为故障。
exec 9>"${LOCK_FILE}"

if ! flock -n 9; then
    log_line "=== $(date '+%Y-%m-%d %H:%M:%S') Pipeline skipped ==="
    log_line "Reason: another segmentation pipeline holds ${LOCK_FILE}"

    send_mail "[SKIPPED] market_analysis segmentation on ${HOSTNAME_VALUE}"

    exit 0
fi


# ---------------------------------------------------------------------------
# 启动前检查
# ---------------------------------------------------------------------------

log_line "=== $(date '+%Y-%m-%d %H:%M:%S') Segmentation pipeline started ==="
log_line "Host: ${HOSTNAME_VALUE}"
log_line "Project: ${PROJECT_DIR}"
log_line "CLI: ${CLI_BIN}"
log_line "Log: ${LOG_FILE}"

# 虚拟环境不存在、项目尚未安装或 CLI 不可执行时返回“命令不可用”退出码 127。
if [ ! -x "${CLI_BIN}" ]; then
    log_line "Pipeline failed: ${CLI_BIN} is unavailable"

    send_mail "[FAILED] market_analysis segmentation on ${HOSTNAME_VALUE}"

    exit 127
fi


# ---------------------------------------------------------------------------
# 第一步：自适应初分段
# ---------------------------------------------------------------------------

# 不传 --date：CLI 根据 market_data 中可用的最新行情生成快照，避免北京时间日期与
# 美国交易日日期不一致。
#
# 不传 --force：如果数据库中已存在参数和结果均匹配的快照，pipeline 可以按自身的
# 幂等/跳过逻辑避免无意义的重复计算。
run_step \
    "Step 1/2: adaptive segmentation" \
    "${CLI_BIN}" run-adaptive-segmentation

ADAPTIVE_EXIT_CODE=$?


# ---------------------------------------------------------------------------
# 自适应初分段失败时停止
# ---------------------------------------------------------------------------

# Pivot 依赖自适应初分段。如果第一步失败，禁止继续运行 Pivot，避免使用旧日期结果、
# 产生日期错配，或者把上游失败掩盖成 Pivot 成功。
if [ "${ADAPTIVE_EXIT_CODE}" -ne 0 ]; then
    log_line
    log_line "=== Step 2/2: Pivot segmentation SKIPPED ==="
    log_line "Reason: adaptive segmentation exit code was ${ADAPTIVE_EXIT_CODE}"

    if [ "${ADAPTIVE_EXIT_CODE}" -eq 130 ] ||
       [ "${ADAPTIVE_EXIT_CODE}" -eq 143 ]; then
        OVERALL_STATUS="INTERRUPTED"
    else
        OVERALL_STATUS="FAILED"
    fi

    log_line "=== Pipeline ${OVERALL_STATUS} ==="

    send_mail "[${OVERALL_STATUS}] market_analysis segmentation on ${HOSTNAME_VALUE}"

    exit "${ADAPTIVE_EXIT_CODE}"
fi


# ---------------------------------------------------------------------------
# 第二步：Pivot 精炼分段
# ---------------------------------------------------------------------------

# 不传 --date：Pivot pipeline 选择数据库中最新的自适应分段快照。
# 本步骤仅在第一步成功后运行，正常情况下选择的就是本轮刚完成的最新结果。
run_step \
    "Step 2/2: Pivot segmentation" \
    "${CLI_BIN}" run-pivot-segmentation

PIVOT_EXIT_CODE=$?


# ---------------------------------------------------------------------------
# 汇总最终状态
# ---------------------------------------------------------------------------

if [ "${PIVOT_EXIT_CODE}" -eq 0 ]; then
    OVERALL_STATUS="SUCCESS"
elif [ "${PIVOT_EXIT_CODE}" -eq 130 ] ||
     [ "${PIVOT_EXIT_CODE}" -eq 143 ]; then
    OVERALL_STATUS="INTERRUPTED"
else
    OVERALL_STATUS="FAILED"
fi

log_line
log_line "=== $(date '+%Y-%m-%d %H:%M:%S') Pipeline ${OVERALL_STATUS} ==="
log_line "Adaptive exit code: ${ADAPTIVE_EXIT_CODE}"
log_line "Pivot exit code: ${PIVOT_EXIT_CODE}"

send_mail "[${OVERALL_STATUS}] market_analysis segmentation on ${HOSTNAME_VALUE}"

# 返回 Pivot 步骤退出码，使 cron、监控工具和人工检查能够识别失败。
exit "${PIVOT_EXIT_CODE}"
