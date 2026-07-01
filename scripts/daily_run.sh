#!/bin/bash
# 知识星球每日自动流程 — 每个交易日 8:50 运行
# 由 cron 调用

set -euo pipefail

PROJECT_DIR="/Users/chenlin/Desktop/zsxq-stock-report"
LOG_DIR="$PROJECT_DIR/data/logs"
LOG_FILE="$LOG_DIR/daily_$(date +%Y%m%d_%H%M%S).log"

mkdir -p "$LOG_DIR"

export PYTHONPATH="$PROJECT_DIR"
export MIMO_API_KEY="sk-c0xsk7sofzycx5s6z6vz9nxrj8qwx4s6q6e3zhj7iuyyljg7"
export TO_EMAIL="470337944@qq.com"
export SMTP_USER="470337944@qq.com"

# 从 .env.local 读取 SMTP_PASS（不在 git 中）
if [ -f "$PROJECT_DIR/.env.local" ]; then
    export SMTP_PASS="$(cat "$PROJECT_DIR/.env.local")"
fi

cd "$PROJECT_DIR"

echo "=== 开始运行 $(date) ===" >> "$LOG_FILE"

python3 main.py all https://wx.zsxq.com/group/88888142214212 -n 30 >> "$LOG_FILE" 2>&1

EXIT_CODE=$?

echo "=== 运行结束 $(date), exit=$EXIT_CODE ===" >> "$LOG_FILE"

# 清理 30 天前的日志
find "$LOG_DIR" -name "daily_*.log" -mtime +30 -delete 2>/dev/null || true

exit $EXIT_CODE
