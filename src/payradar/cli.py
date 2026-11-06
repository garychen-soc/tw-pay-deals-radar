"""命令列入口。

    payradar run [--providers a,b] [--model sonnet] [--llm-budget 300] [--reuse-docs]
    payradar probe <provider> [--details N]     單一服務擷取體檢
    payradar sample <provider> [--limit N]      以 var/docs 原文抽取前 N 筆並印出（不寫 state）
    payradar usage [--days 30]                  每輪 LLM token 用量統計
    payradar emit                               只從 state 重產網站資料與行事曆
    payradar ptt                                更新 PTT 社群情報（失敗沿用上一份）
    payradar score                              以 golden 集評分目前 state
    payradar evaluate [--model sonnet]          只對 golden 頁面重跑抽取並評分

退出碼：0 成功；2 參數錯誤；4 來源大量失敗、本輪不發布；6 claude CLI 登入失效或額度用完。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta

from . import pipeline
from .emit import write
from .extract.locate import DEFAULT_MODEL, locate
from .models import Activity, SourceHealth


def _load_state() -> tuple[dict[str, list[Activity]], list[SourceHealth], datetime]:
    run_info = json.loads((pipeline.STATE / "run.json").read_text(encoding="utf-8"))
    health = [SourceHealth.model_validate(row) for row in run_info["sources"]]
    activities = {h.provider_id: pipeline.previous(h.provider_id) for h in health}
    return activities, health, datetime.fromisoformat(run_info["observed_at"])


def _usage_line(usage: dict[str, object]) -> str:
    def n(key: str) -> int:
        value = usage.get(key) or 0
        return int(value) if isinstance(value, (int, float, str)) else 0

    return (
        f"LLM 用量：合計 {n('total_tokens'):,} tokens（輸入 {n('input_tokens'):,}、"
        f"輸出 {n('output_tokens'):,}、快取寫入 {n('cache_creation_tokens'):,}、"
        f"快取讀取 {n('cache_read_tokens'):,}），約 US${float(str(usage.get('cost_usd') or 0)):.2f}"
    )


def _usage_table(rows: list[dict[str, object]], days: int) -> str:
    since = (datetime.now().astimezone() - timedelta(days=days)).isoformat()
    rows = [r for r in rows if str(r.get("observed_at", "")) >= since]
    if not rows:
        return f"近 {days} 天沒有用量紀錄（var/usage.jsonl）"
    keys = [
        "calls",
        "input_tokens",
        "output_tokens",
        "cache_creation_tokens",
        "cache_read_tokens",
        "total_tokens",
    ]
    head = (
        f"{'時間':16s} {'範圍':8s} {'呼叫':>5s} {'輸入':>9s} {'輸出':>9s} "
        f"{'快取寫入':>10s} {'快取讀取':>10s} {'合計':>11s} {'US$':>7s}"
    )
    lines = [head]
    totals = dict.fromkeys(keys, 0)
    cost = 0.0
    for row in rows:
        values = [int(str(row.get(k) or 0)) for k in keys]
        for key, value in zip(keys, values, strict=True):
            totals[key] += value
        cost += float(str(row.get("cost_usd") or 0))
        lines.append(
            f"{str(row['observed_at'])[:16].replace('T', ' '):16s} "
            f"{str(row.get('scope', 'all'))[:8]:8s} {values[0]:5d} {values[1]:9,d} "
            f"{values[2]:9,d} {values[3]:10,d} {values[4]:10,d} {values[5]:11,d} "
            f"{float(str(row.get('cost_usd') or 0)):7.2f}"
        )
    runs = len(rows)
    lines.append(
        f"共 {runs} 輪：{totals['calls']} 次呼叫、{totals['total_tokens']:,} tokens、"
        f"約 US${cost:.2f}；"
        f"平均每輪 {totals['total_tokens'] // runs:,} tokens、US${cost / runs:.2f}"
    )
    return "\n".join(lines)


def _sample(provider: str, limit: int, model: str, offset: int) -> list[dict[str, object]]:
    observed = datetime.now(pipeline.TAIPEI).replace(microsecond=0)
    docs = [d for d in pipeline.group_docs(pipeline.load_docs(provider), observed.date())]
    docs = [d for d in docs if d.extractable][offset : offset + limit]
    out = []
    for doc in docs:
        activity = pipeline.assemble(doc, locate(doc.request(), model), "", observed)
        out.append(activity.model_dump(mode="json", exclude={"observed_at", "content_hash"}))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="payradar")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--providers", default="")
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--llm-budget", type=int, default=300)
    run.add_argument("--llm-workers", type=int, default=3)
    run.add_argument(
        "--reuse-docs", action="store_true", help="用 var/docs 的上一輪原文，不重抓官網"
    )
    probe = sub.add_parser("probe")
    probe.add_argument("provider")
    probe.add_argument("--details", type=int, default=2)
    sample = sub.add_parser("sample")
    sample.add_argument("provider")
    sample.add_argument("--limit", type=int, default=3)
    sample.add_argument("--offset", type=int, default=0)
    sample.add_argument("--model", default=DEFAULT_MODEL)
    usage = sub.add_parser("usage", help="每輪 LLM token 用量（var/usage.jsonl）")
    usage.add_argument("--days", type=int, default=30)
    sub.add_parser("emit")
    sub.add_parser("ptt")
    sub.add_parser("score")
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.command == "run":
        ids = [p for p in args.providers.split(",") if p] or None
        result = pipeline.run(
            ids,
            model=args.model,
            llm_budget=args.llm_budget,
            llm_workers=args.llm_workers,
            reuse_docs=args.reuse_docs,
        )
        for item in result.health:
            print(
                f"{item.provider_id:12s} {item.status:8s} stubs={item.stubs:4d} "
                f"activities={item.activities:4d} carried={item.carried:3d} {item.message}"
            )
        print(f"LLM 呼叫 {result.llm_calls} 次，失敗 {result.llm_failures}")
        pipeline.log_usage(result, ids)
        print(_usage_line(result.usage))
        for message in result.messages:
            print(message)
        if not result.publishable:
            return 4
        if any("登入失效" in m or "額度已用完" in m for m in result.messages):
            return 6
        if ids and (pipeline.STATE / "run.json").exists():
            # 只跑部分服務時，其餘服務沿用 state，避免把它們從網站移除
            activities, health, _ = _load_state()
            activities.update(result.activities)
            merged = {h.provider_id: h for h in health}
            merged.update({h.provider_id: h for h in result.health})
            result.activities, result.health = activities, list(merged.values())
        pipeline.save(result)
        print(
            json.dumps(write(result.activities, result.health, result.observed), ensure_ascii=False)
        )
        return 0
    if args.command == "emit":
        activities, health, generated = _load_state()
        print(json.dumps(write(activities, health, generated), ensure_ascii=False))
        return 0
    if args.command == "ptt":
        from .ptt import write as write_ptt

        print(json.dumps(write_ptt(), ensure_ascii=False))
        return 0
    if args.command == "probe":
        from .sources import probe as run_probe

        print(
            json.dumps(run_probe(args.provider, details=args.details), ensure_ascii=False, indent=1)
        )
        return 0
    if args.command == "sample":
        rows = _sample(args.provider, args.limit, args.model, args.offset)
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    if args.command == "score":
        from .golden import score

        print(json.dumps(score(), ensure_ascii=False, indent=1))
        return 0
    if args.command == "evaluate":
        from .golden import evaluate as run_evaluate

        print(json.dumps(run_evaluate(args.model), ensure_ascii=False, indent=1))
        return 0
    if args.command == "usage":
        print(_usage_table(pipeline.read_usage(), args.days))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
