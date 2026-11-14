#!/bin/bash
# 程式層自我修復：對指定來源開一個無人值守的 Claude session，在獨立 worktree 的分支上
# 修擷取器、跑測試、開 PR 等 Gary 審。不改 main、不 merge。由 ops/run_local.sh 在健康檢查
# 判定為修復候選時於背景呼叫；同一來源 3 天內只開一次（payradar health 負責冷卻）。
#
#   ops/repair.sh <provider_id> <顯示名稱> <原因>
#   PAYRADAR_REPAIR_DRY=1 只印出要執行的指令，不開 session
set -euo pipefail

PROVIDER="$1"; NAME="$2"; REASON="$3"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$HOME/Library/Logs/tw-pay-radar"
STAMP="$(date +%Y%m%d-%H%M)"
BRANCH="fix/${PROVIDER}-${STAMP}"
WT="$HOME/Projects/tw-pay-deals-radar-repair/${PROVIDER}-${STAMP}"
LOG="$LOG_DIR/repair-${PROVIDER}-${STAMP}.log"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin"
mkdir -p "$LOG_DIR"
exec >>"$LOG" 2>&1
echo "==== $(date '+%F %T') repair $PROVIDER：$REASON"

notify() {
  /usr/bin/osascript -e "display notification \"$1\" with title \"支付雷達自動修復：$NAME\"" || true
}

ALLOWED=(
  "Read" "Edit" "Write" "Glob" "Grep"
  "Bash(.venv/bin/payradar probe:*)" "Bash(.venv/bin/python -m pytest:*)"
  "Bash(.venv/bin/ruff:*)" "Bash(.venv/bin/mypy:*)" "Bash(curl -s:*)"
  "Bash(git status:*)" "Bash(git diff:*)" "Bash(git log:*)" "Bash(git add:*)" "Bash(git commit:*)"
  "Bash(git push -u origin ${BRANCH}:*)" "Bash(gh pr create:*)" "Bash(osascript:*)"
)

if [ -n "${PAYRADAR_REPAIR_DRY:-}" ]; then
  echo "DRY RUN"; echo "worktree: $WT  branch: $BRANCH"; printf 'allow: %s\n' "${ALLOWED[@]}"
  exit 0
fi

git -C "$REPO" fetch -q origin
git -C "$REPO" worktree add -q -b "$BRANCH" "$WT" origin/main
cd "$WT"
# worktree 要有自己的 venv：主 repo 的 venv 是 editable 安裝，會載入主 repo 的程式而不是這個分支的
/opt/homebrew/bin/python3 -m venv .venv
.venv/bin/pip install -q -e '.[dev]'
mkdir -p var/docs
cp "$REPO/var/docs/${PROVIDER}.json" var/docs/ 2>/dev/null || true

PROMPT="$(sed -e "s|{{PROVIDER}}|$PROVIDER|g" -e "s|{{NAME}}|$NAME|g" -e "s|{{WORKTREE}}|$WT|g" \
  -e "s|{{BRANCH}}|$BRANCH|g" -e "s|{{REASON}}|${REASON//|/／}|g" "$REPO/ops/repair_prompt.md")"

notify "偵測到「${REASON:0:40}」，已開修復 session"
set +e
timeout 2700 claude -p "$PROMPT" --model sonnet --permission-mode acceptEdits \
  --allowedTools "${ALLOWED[@]}" --strict-mcp-config --no-session-persistence </dev/null
status=$?
set -e
echo "==== $(date '+%F %T') claude exit=$status"
if [ "$status" -ne 0 ]; then
  notify "修復 session 異常結束（exit $status），詳見 $LOG"
fi
# 沒開 PR（沒有提交）就把 worktree 收掉；有提交則留著，等 PR 審完再清
if [ -z "$(git log --oneline origin/main..HEAD)" ]; then
  cd "$REPO" && git worktree remove --force "$WT" && git branch -D "$BRANCH" >/dev/null
  echo "no commits; worktree removed"
fi
