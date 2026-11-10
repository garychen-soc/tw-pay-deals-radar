#!/usr/bin/env python3
"""golden 換新工具（流程見 golden/RENEWAL.md）。

    .venv/bin/python golden/renew.py select   找出失效條目、挑替補頁、存原文副本 → var/golden_src/renewal.json
    .venv/bin/python golden/renew.py apply    把 var/golden_src/renewal.yaml 合併進 golden/golden.yaml
    .venv/bin/python golden/renew.py check    結構、原文逐字、內容雜湊與數字佐證檢查（RESULT: PASS / FAIL）

失效的定義（以 var/docs 的官方原文判斷，不讀 state 或任何抽取輸出）：
  changed  官方頁還在，但條款內容雜湊變了 → 同一頁重新標註（沿用 id）
  gone     官方頁已不在列表（下架或過期）   → 移除，改挑同一服務的另一頁補上（新 id）
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from payradar import pipeline  # noqa: E402
from payradar.extract.verify import SOLD_OUT, numbers  # noqa: E402
from payradar.textnorm import normalize  # noqa: E402

GOLDEN = ROOT / "golden" / "golden.yaml"
SRC = ROOT / "var" / "golden_src"
PLAN = SRC / "renewal.json"
PATCH = SRC / "renewal.yaml"
UNITS = {"%", "元", "點", "折", "筆", "次", "份", "other"}
HEADER_END = "- id:"
# 與 extract.verify.title_sold_out 相同：官方標題的括號額滿標記，例如台灣Pay「(活動額滿)」
_TITLE_FULL = re.compile(r"[（(【\[]\s*(?:活動)?額滿\s*[）)】\]]")


def _today() -> Any:
    return datetime.now(pipeline.TAIPEI).date()


def load_golden() -> tuple[str, list[dict[str, Any]]]:
    raw = GOLDEN.read_text(encoding="utf-8")
    head = raw[: raw.index(HEADER_END)]
    return head, yaml.safe_load(raw) or []


def save_golden(head: str, rows: list[dict[str, Any]]) -> None:
    GOLDEN.write_text(
        head + yaml.safe_dump(rows, allow_unicode=True, sort_keys=False, width=200), "utf-8"
    )


def current_docs(provider: str) -> list[pipeline.Doc]:
    return [d for d in pipeline.group_docs(pipeline.load_docs(provider), _today()) if d.extractable]


def find_doc(entry: dict[str, Any], docs: list[pipeline.Doc]) -> pipeline.Doc | None:
    hits = [d for d in docs if any(s.url == entry["url"] for s in d.stubs)]
    if len(hits) > 1:
        title = str(entry.get("title") or "")
        hits = [d for d in hits if any(s.title == title for s in d.stubs)]
    return hits[0] if len(hits) == 1 else None


def write_src(gid: str, doc: pipeline.Doc) -> None:
    stub = doc.primary
    SRC.mkdir(parents=True, exist_ok=True)
    (SRC / f"{gid}.txt").write_text(
        f"標題：{stub.title}\n網址：{stub.url}\n列表期間：{stub.period_start}~{stub.period_end}\n"
        f"內容雜湊：{doc.digest}\n=====\n{doc.text}\n",
        encoding="utf-8",
    )


def select() -> int:
    _, rows = load_golden()
    plan: list[dict[str, Any]] = []
    by_provider: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_provider.setdefault(str(row["provider"]), []).append(row)
    for provider, entries in sorted(by_provider.items()):
        docs = current_docs(provider)
        used = {d.digest for e in entries if (d := find_doc(e, docs))}
        numbers_used = [int(str(e["id"]).rsplit("-", 1)[1]) for e in entries]
        next_no = max(numbers_used, default=0) + 1
        has_sold = any(e.get("sold_out") for e in entries)
        for entry in entries:
            doc = find_doc(entry, docs)
            if doc is not None and doc.digest == entry.get("content_hash"):
                continue
            if doc is not None:
                write_src(str(entry["id"]), doc)
                plan.append(_item(entry["id"], provider, doc, "changed", ""))
                continue
            spare = [d for d in docs if d.digest not in used]
            if not has_sold:  # 該服務 golden 沒有已額滿頁時優先補一頁有額滿聲明的
                spare.sort(key=lambda d: not SOLD_OUT.search(normalize(d.text)))
            if not spare:
                plan.append({"id": entry["id"], "provider": provider, "action": "gone", "replacement": ""})
                continue
            pick = spare[0]
            used.add(pick.digest)
            new_id = f"{provider}-{next_no:02d}"
            next_no += 1
            write_src(new_id, pick)
            plan.append({"id": entry["id"], "provider": provider, "action": "gone", "replacement": new_id})
            plan.append(_item(new_id, provider, pick, "new", str(entry["id"])))
    PLAN.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    todo = [p for p in plan if p["action"] in ("changed", "new")]
    print(f"golden {len(rows)} 筆；需重新標註 {len(todo)} 筆（changed {sum(p['action'] == 'changed' for p in plan)}、"
          f"new {sum(p['action'] == 'new' for p in plan)}）；移除 {sum(p['action'] == 'gone' for p in plan)} 筆")
    for p in todo:
        print(f"  {p['action']:7s} {p['id']:16s} {p['title'][:40]}")
    print(f"計畫：{PLAN}")
    return 0


def _item(gid: Any, provider: str, doc: pipeline.Doc, action: str, replaces: str) -> dict[str, Any]:
    stub = doc.primary
    return {
        "id": str(gid),
        "provider": provider,
        "url": stub.url,
        "title": stub.title,
        "content_hash": doc.digest,
        "action": action,
        "replaces": replaces,
        "src": str(SRC / f"{gid}.txt"),
    }


def apply() -> int:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    patch: list[dict[str, Any]] = yaml.safe_load(PATCH.read_text(encoding="utf-8")) or []
    expected = {p["id"]: p for p in plan if p["action"] in ("changed", "new")}
    got = {str(e["id"]): e for e in patch}
    missing = sorted(set(expected) - set(got))
    extra = sorted(set(got) - set(expected))
    if missing or extra:
        print(f"renewal.yaml 與計畫不符：缺 {missing}、多 {extra}")
        return 1
    for gid, entry in got.items():
        meta = expected[gid]
        entry.update({k: meta[k] for k in ("provider", "url", "title", "content_hash")})
    head, rows = load_golden()
    gone = {p["id"] for p in plan if p["action"] == "gone"}
    merged = [got.get(str(r["id"]), r) for r in rows if str(r["id"]) not in gone]
    merged += [got[gid] for gid, meta in expected.items() if meta["action"] == "new"]
    merged.sort(key=lambda r: (str(r["provider"]), str(r["id"])))
    save_golden(head, merged)
    print(f"已合併：更新 {sum(m['action'] == 'changed' for m in expected.values())}、新增 "
          f"{sum(m['action'] == 'new' for m in expected.values())}、移除 {len(gone)}；共 {len(merged)} 筆")
    return 0


def _quote_ok(quote: str, text: str) -> bool:
    squash = lambda s: re.sub(r"\s+", "", normalize(s))  # noqa: E731
    return bool(quote) and squash(quote) in squash(text)


def check() -> int:
    _, rows = load_golden()
    errors: list[str] = []
    warnings: list[str] = []
    ids = [str(r.get("id")) for r in rows]
    if len(ids) != len(set(ids)):
        errors.append("id 重複")
    docs_cache: dict[str, list[pipeline.Doc]] = {}
    for row in rows:
        gid = str(row.get("id"))
        for key in ("provider", "url", "title", "content_hash"):
            if not row.get(key):
                errors.append(f"{gid}：缺 {key}")
        if not gid.startswith(f"{row.get('provider')}-"):
            errors.append(f"{gid}：id 前綴與 provider 不符")
        if row.get("is_offer") not in ("yes", "no", True, False):
            errors.append(f"{gid}：is_offer 必須是 yes／no")
        if row.get("registration") not in ("required", "not_required", "unknown"):
            errors.append(f"{gid}：registration 值不合法")
        path = SRC / f"{gid}.txt"
        if not path.exists():
            errors.append(f"{gid}：缺原文副本 {path.name}")
            continue
        text = path.read_text(encoding="utf-8")
        if f"內容雜湊：{row.get('content_hash')}" not in text:
            errors.append(f"{gid}：原文副本的內容雜湊與條目不符")
        quotes = [str(q) for q in row.get("quotes") or []]
        for q in quotes:
            if not _quote_ok(q, text):
                errors.append(f"{gid}：quote 不在原文：{q[:30]}")
        for item in row.get("rewards") or []:
            if str(item.get("unit")) not in UNITS:
                errors.append(f"{gid}：rewards 單位不合法 {item.get('unit')}")
        for key in ("min_spend", "caps"):
            for item in row.get(key) or []:
                if str(item.get("unit")) not in UNITS:
                    errors.append(f"{gid}：{key} 單位不合法 {item.get('unit')}")
                elif quotes and not any(float(item["amount"]) in numbers(q) for q in quotes):
                    warnings.append(f"{gid}：{key} {item['amount']} 在 quotes 裡找不到數字佐證")
        for sold in row.get("sold_out") or []:
            quote = str(sold.get("quote") or "")
            if not _quote_ok(quote, text):
                errors.append(f"{gid}：sold_out quote 不在原文：{quote[:30]}")
            elif not (SOLD_OUT.search(normalize(quote)) or _TITLE_FULL.search(normalize(quote))):
                errors.append(f"{gid}：sold_out quote 不是已額滿語氣：{quote[:30]}")
        provider = str(row.get("provider"))
        if provider not in docs_cache:
            try:
                docs_cache[provider] = current_docs(provider)
            except FileNotFoundError:
                docs_cache[provider] = []
        doc = find_doc(row, docs_cache[provider])
        if doc is None:
            warnings.append(f"{gid}：官方頁已不在本輪列表（應換新）")
        elif doc.digest != row.get("content_hash"):
            warnings.append(f"{gid}：官方頁內容已變動（應重新標註）")
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    print(f"golden {len(rows)} 筆；錯誤 {len(errors)}、提醒 {len(warnings)}")
    print("RESULT: PASS" if not errors else "RESULT: FAIL")
    return 0 if not errors else 1


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "check"
    sys.exit({"select": select, "apply": apply, "check": check}[command]())
