"""以 golden 集（官方原文人工核對）評分。

比對單位是「事實」，以整頁為單位（不分子活動）：

* ``reward``：(單位, 數值)
* ``min``／``cap``：(金額, 單位)
* ``sold``：已額滿聲明涵蓋的 (起日, 迄日)；整檔為 (None, None)
* ``reg``：是否需登錄／領券（golden 為 unknown 的不計）
* ``offer``：這頁是不是優惠活動

精確率＝產出的事實中正確的比例 —— 這是發布門檻，因為網站上一個錯的回饋或錯的
「已額滿」會讓使用者白跑或錯過；召回率衡量漏了多少。

golden 每筆記錄標註當時的條款內容雜湊；官方頁改版（雜湊不同）的筆數不計分、
列入 ``stale``，提醒該換新 golden，而不是把官方改版算成抽取錯誤。

``score()`` 評目前 state；``evaluate(model)`` 只對 golden 頁面重跑抽取（用
var/docs 的原文，不寫 state），用來比較提示詞或模型。
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .extract.locate import LocateError, locate
from .models import Activity
from .pipeline import DOCS, STATE, TAIPEI, Doc, assemble, group_docs, load_docs
from .pipeline import previous as previous_state

GOLDEN = Path(__file__).resolve().parents[2] / "golden" / "golden.yaml"
Fact = tuple[Any, ...]
KINDS = ("reward", "min", "cap", "sold", "reg", "offer")


_COUNT_UNITS = {"筆", "次", "份", "other"}


def _reward(unit: str, value: Any) -> Fact:
    """折扣、贈品、贈品數量等「其他」回饋只比有沒有，不比數值（記法因人而異）。"""
    if unit in ("other", "折", "筆", "次", "份"):
        return ("reward", "other", None)
    return ("reward", unit, _num(value))


def _amount(kind: str, amount: Any, unit: str) -> Fact:
    """次數類單位（次／份／筆／other）視為同一類。"""
    return (kind, _num(amount), "count" if unit in _COUNT_UNITS else unit)


def _sold(start: Any, end: Any, label: Any) -> Fact:
    """整檔額滿與「有範圍字樣但沒日期」的部分額滿必須分開 —— 後者標成整檔是嚴重錯誤。"""
    s, e = _day(start), _day(end)
    scope = "dated" if s or e else ("part" if label else "whole")
    return ("sold", s, e, scope)


def _num(value: Any) -> float | None:
    return None if value is None else round(float(value), 4)


def _day(value: Any) -> str | None:
    return None if value in (None, "") else str(value)[:10]


def facts_of(activity: Activity) -> set[Fact]:
    out: set[Fact] = set()
    if activity.is_offer != "unknown":
        out.add(("offer", activity.is_offer))
    if activity.registration != "unknown":
        out.add(("reg", activity.registration))
    for offer in activity.offers:
        out |= {_reward(r.unit, r.value) for r in offer.rewards}
        if offer.min_spend:
            out.add(_amount("min", offer.min_spend.amount, offer.min_spend.unit))
        out |= {_amount("cap", c.amount, c.unit) for c in offer.caps}
        out |= {_sold(s.scope_start, s.scope_end, s.scope_label) for s in offer.sold_out}
    out |= {_sold(s.scope_start, s.scope_end, s.scope_label) for s in activity.title_sold_out}
    return out


def facts_expected(entry: dict[str, Any]) -> set[Fact]:
    out: set[Fact] = set()
    offer = entry.get("is_offer")
    if isinstance(offer, bool):
        offer = "yes" if offer else "no"
    if offer in ("yes", "no"):
        out.add(("offer", offer))
    if entry.get("registration") in ("required", "not_required"):
        out.add(("reg", entry["registration"]))
    out |= {_reward(str(r["unit"]), r.get("value")) for r in entry.get("rewards") or []}
    out |= {_amount("min", m["amount"], str(m["unit"])) for m in entry.get("min_spend") or []}
    out |= {_amount("cap", c["amount"], str(c["unit"])) for c in entry.get("caps") or []}
    out |= {
        _sold(s.get("scope_start"), s.get("scope_end"), s.get("scope_label"))
        for s in entry.get("sold_out") or []
    }
    return out


def entries() -> list[dict[str, Any]]:
    loaded: list[dict[str, Any]] = yaml.safe_load(GOLDEN.read_text(encoding="utf-8")) or []
    return loaded


def _compare(
    golden: list[dict[str, Any]], find: Callable[[dict[str, Any]], Activity | None]
) -> dict[str, Any]:
    tally = {k: [0, 0, 0] for k in KINDS}  # tp, fp, fn
    missing: list[str] = []
    stale: list[str] = []
    diffs: list[dict[str, Any]] = []
    for entry in golden:
        activity = find(entry)
        if activity is None:
            missing.append(str(entry["id"]))
            continue
        if entry.get("content_hash") and activity.content_hash != entry["content_hash"]:
            stale.append(str(entry["id"]))
            continue
        expected, produced = facts_expected(entry), facts_of(activity)
        # golden 標 unknown（原文有爭議）的欄位不計分，產出的也不算錯
        scored_kinds = {f[0] for f in expected} | {"reward", "min", "cap", "sold"}
        produced = {f for f in produced if f[0] in scored_kinds}
        for fact in produced | expected:
            slot = tally[str(fact[0])]
            if fact in produced and fact in expected:
                slot[0] += 1
            elif fact in produced:
                slot[1] += 1
            else:
                slot[2] += 1
        if produced != expected:
            diffs.append(
                {
                    "id": entry["id"],
                    "wrong": sorted(str(f) for f in produced - expected),
                    "missed": sorted(str(f) for f in expected - produced),
                }
            )
    tp = sum(v[0] for v in tally.values())
    fp = sum(v[1] for v in tally.values())
    fn = sum(v[2] for v in tally.values())

    def ratio(a: int, b: int) -> float:
        return round(a / b, 4) if b else 1.0

    return {
        "entries": len(golden),
        "scored": len(golden) - len(missing) - len(stale),
        "matched": tp,
        "wrong": fp,
        "missed": fn,
        "precision": ratio(tp, tp + fp),
        "recall": ratio(tp, tp + fn),
        "by_kind": {
            k: {"precision": ratio(v[0], v[0] + v[1]), "recall": ratio(v[0], v[0] + v[2])}
            for k, v in tally.items()
        },
        "not_found": missing,
        "stale": stale,
        "diffs": diffs,
    }


def _finder(pools: dict[str, list[Activity]]) -> Callable[[dict[str, Any]], Activity | None]:
    def find(entry: dict[str, Any]) -> Activity | None:
        for activity in pools.get(str(entry["provider"]), []):
            if activity.url == entry["url"]:
                return activity
        return None

    return find


def score() -> dict[str, Any]:
    golden = entries()
    pools = {
        str(e["provider"]): previous_state(str(e["provider"]))
        for e in golden
        if (STATE / "activities" / f"{e['provider']}.json").exists()
    }
    return _compare(golden, _finder(pools))


def evaluate(model: str, workers: int = 3) -> dict[str, Any]:
    """只對 golden 頁面以指定模型抽取並評分（不寫 state）。"""
    observed = datetime.now(TAIPEI).replace(microsecond=0)
    golden = entries()
    urls = {str(e["url"]) for e in golden}
    targets: list[Doc] = []
    for provider in sorted({str(e["provider"]) for e in golden}):
        if not (DOCS / f"{provider}.json").exists():
            continue
        targets += [
            d
            for d in group_docs(load_docs(provider), observed.date())
            if any(s.url in urls for s in d.stubs)
        ]

    def extract(doc: Doc) -> Activity:
        if not doc.extractable:
            return assemble(doc, None, "", observed)
        try:
            return assemble(doc, locate(doc.request(), model), "", observed)
        except LocateError as exc:
            return assemble(doc, None, f"抽取失敗：{exc}", observed)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        built = list(pool.map(extract, targets))
    pools: dict[str, list[Activity]] = {}
    for activity in built:
        pools.setdefault(activity.provider_id, []).append(activity)
    report = _compare(golden, _finder(pools))
    report["model"] = model
    return report
