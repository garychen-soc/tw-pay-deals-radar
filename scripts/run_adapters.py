#!/usr/bin/env python3
"""確定性 adapter orchestrator（C 架構核心引擎）。

跑各大宗服務的確定性 adapter，產出帶 evidence 的活動 + source_health 成功率統計。
這些服務不再靠排程 AI 現場抓——代碼一次寫死、每天穩定跑，消除波動（404、額滿漏抓、串味）。
長尾服務（icash/歐付寶/橘子/全盈/全家/OPEN/LINE/全聯）與 PTT 仍由排程 AI 補。

用法：
  python3 scripts/run_adapters.py                 # 印出 {activities, source_health} JSON
  python3 scripts/run_adapters.py --merge         # 合併進 data/promotions.json（取代這些 provider 的舊資料）

已納入 adapter 的服務：全支付。逐步納入：台灣Pay、一卡通。
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from adapters import pxpayplus  # noqa: E402

ADAPTERS = [pxpayplus]
DATA = ROOT / "data" / "promotions.json"


def run(today=None):
    activities = []
    off_ok = off_exp = ext_ok = ext_exp = 0
    gaps = []
    for mod in ADAPTERS:
        acts, stats = mod.fetch(today)
        activities += acts
        off_ok += stats["official_ok"]; off_exp += stats["official_expected"]
        ext_ok += stats["extended_ok"]; ext_exp += stats["extended_expected"]
        if stats["errors"]:
            gaps.append(f"{stats['provider']} {stats['errors']} 筆 detail 抓取失敗")
        print(f"# {stats['provider']}: 有效 {stats['live']} 筆"
              f"（官方入口 {stats['official_ok']}/{stats['official_expected']}、"
              f"延伸 {stats['extended_ok']}/{stats['extended_expected']}、錯誤 {stats['errors']}）",
              file=sys.stderr)
    source_health = {
        "official_sources": {"succeeded": off_ok, "expected": off_exp},
        "extended_checks": {"succeeded": ext_ok, "expected": ext_exp},
        "coverage_gaps": gaps,
    }
    return {"activities": activities, "source_health": source_health}


def merge_into_promotions(result):
    """把 adapter 產出的服務活動，取代 promotions.json 中同 provider 的舊資料。"""
    d = json.loads(DATA.read_text("utf-8"))
    adapter_providers = {a["provider_name"] for a in result["activities"]}
    kept = [a for a in d.get("activities", []) if a.get("provider_name") not in adapter_providers]
    d["activities"] = kept + result["activities"]
    # 不覆蓋 source_health：adapter 目前只涵蓋部分服務，成功率若只反映這些會誤導全站；
    # 完整透明成功率待所有大宗服務都納入 adapter、或由排程 AI 統一統計時再填。
    ids = {a["id"] for a in d["activities"]}
    d["featured_ids"] = [i for i in d.get("featured_ids", []) if i in ids]
    DATA.write_text(json.dumps(d, ensure_ascii=False, indent=2), "utf-8")
    return adapter_providers, len(result["activities"]), len(d["activities"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--merge", action="store_true", help="合併進 data/promotions.json")
    args = ap.parse_args()
    result = run()
    if args.merge:
        provs, n_adapter, total = merge_into_promotions(result)
        print(f"已合併 {n_adapter} 筆（{'、'.join(sorted(provs))}）→ promotions.json 共 {total} 筆")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
