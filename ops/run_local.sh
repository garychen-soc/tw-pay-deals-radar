#!/bin/bash
# 本機排程入口：更新資料 → 驗收 → commit → push（GitHub Pages 由 deploy.yml 在 push 後建置）。
#
# 任何一步失敗都不 push：寧可網站停在上一版，也不發布沒驗過的資料。
# 退出碼：0 已發布或無變化；3 等不到網路；4 來源大量失敗；5 golden 精確率未達門檻；
#        6 claude 登入失效或用量額度用完；其他＝程式錯誤（一律發通知，不會靜默失敗）。
set -Eeuo pipefail

# launchd 常在 Mac 短暫喚醒（DarkWake）時觸發，跑到一半又睡回去（2026-11-14 實測拖了 1.5 小時）：
# 整輪包在 caffeinate -i 底下，執行期間不進入閒置睡眠。
if [ -z "${PAYRADAR_CAFFEINATED:-}" ]; then
  export PAYRADAR_CAFFEINATED=1
  exec /usr/bin/caffeinate -i /bin/bash "$0" "$@"
fi

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$HOME/Library/Logs/tw-pay-radar"
BRANCH="${PAYRADAR_BRANCH:-main}"
LLM_BUDGET="${PAYRADAR_LLM_BUDGET:-120}"
# golden 精確率：低於 WARN 通知（多半是官方頁換內容、golden 該更新）；低於 BLOCK 才不發布
WARN_PRECISION="${PAYRADAR_WARN_PRECISION:-0.98}"
MIN_PRECISION="${PAYRADAR_MIN_PRECISION:-0.90}"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin"

mkdir -p "$LOG_DIR"
exec >>"$LOG_DIR/run-$(date +%Y%m%d).log" 2>&1
echo "==== $(date '+%F %T') start"

notify() {
  /usr/bin/osascript -e "display notification \"$1\" with title \"台灣支付優惠雷達\"" || true
}
step() { echo "---- $(date '+%T') $*"; }
# 任何未預期的失敗都要通知（2026-11-12、11-13 兩天 git pull 失敗卻沒通知，靜默停更）
trap 'notify "本輪未發布：第 ${LINENO} 行失敗，詳見 $LOG_DIR"' ERR

cd "$REPO"
# 同一時間只允許一個更新程序（shlock 會清掉已不存在 PID 的殘留鎖）
LOCK="$REPO/state/.run.lock"
if ! /usr/bin/shlock -f "$LOCK" -p $$; then
  echo "another run holds the lock; skip"
  exit 0
fi
trap 'rm -f "$LOCK"' EXIT

# 剛喚醒時網路常還沒好（2026-11-12、11-13：Could not resolve host: github.com）：最多等 10 分鐘
step "wait for network"
for attempt in $(seq 1 20); do
  if git ls-remote -q origin HEAD >/dev/null 2>&1 && curl -s -m 10 -o /dev/null https://www.google.com; then
    break
  fi
  if [ "$attempt" -eq 20 ]; then
    notify "等了 10 分鐘仍連不上網路，本輪未執行"
    trap - ERR
    exit 3
  fi
  sleep 30
done

step "git pull"
git switch -q "$BRANCH"
git pull -q --rebase origin "$BRANCH"

step "payradar run"
set +e
.venv/bin/payradar run --llm-budget "$LLM_BUDGET"
status=$?
set -e
if [ "$status" -ne 0 ]; then
  git checkout -- state web/public
  case "$status" in
    4) notify "多個來源讀取失敗，本輪未發布" ;;
    6) notify "claude 登入失效或用量額度用完，本輪未發布（已抽取的會留在快取）" ;;
    *) notify "本輪未發布（payradar run 退出碼 $status），詳見 $LOG_DIR" ;;
  esac
  trap - ERR
  exit "$status"
fi

# 個別來源被擋或讀取失敗時照常發布（沿用上一輪資料並標示），但要通知，不能悄悄過期
abnormal="$(.venv/bin/python -c '
import json
run = json.load(open("state/run.json"))
print("、".join(s["provider_name"] + "（" + s["status"] + "）" for s in run["sources"] if s["status"] != "ok"))
')"
if [ -n "$abnormal" ]; then
  echo "abnormal sources: $abnormal"
  notify "來源異常：$abnormal，已沿用上一輪資料"
fi

# PTT 社群情報：失敗沿用上一份，不影響發布
step "ptt"
.venv/bin/payradar ptt || echo "ptt failed (ignored)"

step "pytest + golden"
.venv/bin/python -m pytest -q
score_json="$(.venv/bin/payradar score)"
precision="$(echo "$score_json" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["precision"])')"
scored="$(echo "$score_json" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["scored"])')"
echo "golden precision=$precision scored=$scored"
# 官方頁改版會讓 golden 條目失效（不計分）；有效筆數太少時精確率不可靠，提醒換新
if [ "$scored" -lt "${PAYRADAR_MIN_SCORED:-45}" ]; then
  notify "golden 只剩 $scored 筆有效，請執行換新（golden/RENEWAL.md）"
fi
if ! .venv/bin/python -c "import sys; sys.exit(0 if float('$precision') >= float('$WARN_PRECISION') else 1)"; then
  notify "golden 精確率 $precision，請檢查是否有官方頁換了內容（照常發布）"
fi
if ! .venv/bin/python -c "import sys; sys.exit(0 if float('$precision') >= float('$MIN_PRECISION') else 1)"; then
  git checkout -- state web/public
  notify "golden 精確率 $precision 低於 $MIN_PRECISION，本輪不發布"
  trap - ERR
  exit 5
fi

step "build"
npm --prefix web run build >/dev/null

git add state web/public
if git diff --cached --quiet; then
  echo "no data change"
  exit 0
fi
step "commit + push"
git commit -q -m "data: refresh $(date '+%F %H:%M')"
git push -q origin "$BRANCH"
echo "==== $(date '+%F %T') published"
