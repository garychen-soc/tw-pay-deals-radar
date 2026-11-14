"""每輪結束後的健康判定：要通知什麼、哪些來源該開修復 session。

輸入是 ``state/run.json``（本輪各來源狀態），以及兩個只留本機的紀錄檔：

``var/fallback_history.json``
    各來源「靠瀏覽器才抓到」的日期。連續 3 天都靠瀏覽器 → 建議永久改用（需 Gary 核准）。
``var/repairs.json``
    各來源最近一次自動開修復 session 的日期。同一來源 3 天內不重開，避免反覆花 token。

修復候選＝自我修復後仍有結構性問題的來源：
  * 狀態 failed／blocked（改用瀏覽器、重試都沒救回來）
  * 筆數驟降超過門檻、被防護擋下而沿用上一輪（partial 且訊息含「筆數由」）
  * 明細讀取失敗超過兩成（partial 且訊息含「明細讀取失敗」）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "state" / "run.json"
FALLBACK_LOG = ROOT / "var" / "fallback_history.json"
REPAIR_LOG = ROOT / "var" / "repairs.json"
STREAK_DAYS = 3
REPAIR_COOLDOWN = timedelta(days=3)
KEEP_DAYS = 30


@dataclass
class Report:
    notices: list[str] = field(default_factory=list)
    repairs: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"notices": self.notices, "repairs": self.repairs}


def _load(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {str(k): [str(d) for d in v] for k, v in data.items() if isinstance(v, list)}


def _save(path: Path, data: dict[str, list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _streak(days: list[str], today: date) -> int:
    seen = set(days)
    count = 0
    while (today - timedelta(days=count)).isoformat() in seen:
        count += 1
    return count


def _repair_reason(source: dict[str, Any]) -> str:
    status, message = source.get("status"), str(source.get("message") or "")
    if status in ("failed", "blocked"):
        return f"{status}：{message[:160]}"
    if status == "partial" and ("筆數由" in message or "明細讀取失敗" in message):
        return f"partial：{message[:160]}"
    return ""


def evaluate(
    run: dict[str, Any],
    today: date,
    *,
    fallback_log: Path = FALLBACK_LOG,
    repair_log: Path = REPAIR_LOG,
    record: bool = True,
) -> Report:
    report = Report()
    history = _load(fallback_log)
    repairs = _load(repair_log)
    for source in run.get("sources") or []:
        provider = str(source["provider_id"])
        name = str(source.get("provider_name") or provider)
        status = str(source.get("status"))
        fallback = str(source.get("fallback") or "")
        if fallback == "browser":
            days = sorted({*history.get(provider, []), today.isoformat()})
            history[provider] = days
            streak = _streak(days, today)
            if streak >= STREAK_DAYS:
                report.notices.append(
                    f"{name} 已連續 {streak} 天要靠瀏覽器才抓得到，"
                    "建議改成永久使用瀏覽器（需你核准）"
                )
            else:
                report.notices.append(f"{name} 被擋，已自動改用瀏覽器抓取")
        elif fallback == "retry":
            report.notices.append(f"{name} 暫時性失敗，重試後已恢復")
        if status != "ok":
            report.notices.append(f"{name}（{status}）：{str(source.get('message') or '')[:60]}")
        reason = _repair_reason(source)
        if not reason:
            continue
        last = max(repairs.get(provider, []), default="")
        if last and today - date.fromisoformat(last) < REPAIR_COOLDOWN:
            report.notices.append(f"{name} 仍有問題，{last} 已開過修復 session，冷卻中不重開")
            continue
        report.repairs.append({"provider": provider, "name": name, "reason": reason})
    if record:
        cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
        _save(fallback_log, {k: [d for d in v if d >= cutoff] for k, v in history.items()})
    return report


def record_repair(provider: str, today: date, repair_log: Path = REPAIR_LOG) -> None:
    repairs = _load(repair_log)
    repairs[provider] = sorted({*repairs.get(provider, []), today.isoformat()})[-10:]
    _save(repair_log, repairs)


def run_report(today: date) -> Report:
    run = json.loads(RUN.read_text(encoding="utf-8"))
    return evaluate(run, today)
