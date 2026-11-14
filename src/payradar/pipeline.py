"""一輪完整更新：擷取 → 合併 → LLM 定位 → 驗證 → 沿用與防護 → 寫入 state。

state/activities/<provider>.json 是唯一正本（進版控），網站資料由 emit 從它產生。
來源失敗時沿用上一輪仍有效的活動並標 stale，而不是讓整個服務從網站消失；
但也不把舊資料冒充成新資料。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import providers
from .dates import period_from_terms
from .extract.locate import (
    DEFAULT_MODEL,
    AuthExpired,
    LocateError,
    Meter,
    Request,
    cached,
    locate,
)
from .extract.schedule import Context
from .extract.verify import build_offers, title_sold_out
from .models import Activity, SourceHealth, Stub
from .providers.base import ExternalTerms, NotActivity
from .sources import open_adapter
from .textnorm import normalize
from .transport import AccessDenied, FetchFailed, TransportError

log = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")
ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "state"
DOCS = ROOT / "var" / "docs"
USAGE_LOG = ROOT / "var" / "usage.jsonl"
DROP_LIMIT = 0.4
MIN_TERMS_CHARS = 80


@dataclass
class Page:
    stub: Stub
    text: str
    external_url: str = ""


@dataclass
class Fetched:
    provider_id: str
    health: SourceHealth
    pages: list[Page] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    # 失敗是否為暫時性（逾時、連線、5xx）：是的話本輪結尾再重試一次
    transient: bool = False


@dataclass
class RunResult:
    observed: datetime
    activities: dict[str, list[Activity]]
    health: list[SourceHealth]
    llm_calls: int
    llm_failures: int
    publishable: bool
    messages: list[str]
    usage: dict[str, Any] = field(default_factory=dict)


# ---- 擷取 ----------------------------------------------------------------


def fetch_provider(provider_id: str, transport: str | None = None) -> Fetched:
    cls = providers.load(provider_id)
    health = SourceHealth(
        provider_id=provider_id, provider_name=cls.name, status="ok", entry_url=cls.entry_url
    )
    out = Fetched(provider_id, health)
    try:
        with open_adapter(provider_id, transport) as adapter:
            stubs = _dedupe(adapter.discover())
            health.stubs = len(stubs)
            failures = len(adapter.failures)
            for stub in stubs:
                if stub.detail_text is not None:
                    out.pages.append(Page(_with_period(stub, stub.detail_text), stub.detail_text))
                    continue
                try:
                    terms = adapter.terms(stub)
                except ExternalTerms as exc:
                    out.pages.append(Page(stub, "", external_url=exc.target))
                    continue
                except NotActivity as exc:
                    out.skipped.append(f"{stub.title}：{exc}")
                    continue
                except (TransportError, ValueError) as exc:
                    failures += 1
                    log.warning("%s 明細失敗 %s：%s", provider_id, stub.url, exc)
                    continue
                if stub.period_end is None and terms.period_end is not None:
                    stub = stub.model_copy(
                        update={"period_start": terms.period_start, "period_end": terms.period_end}
                    )
                out.pages.append(Page(_with_period(stub, terms.text), terms.text))
            out.skipped.extend(adapter.skipped)
            health.detail_failures = failures
            if stubs and failures > len(stubs) * 0.2:
                health.status = "partial"
                health.message = f"{failures} 筆明細讀取失敗"
            if not stubs:
                health.status = "failed"
                health.message = "列表為空"
    except AccessDenied as exc:
        health.status, health.message = "blocked", str(exc)[:200]
    except FetchFailed as exc:
        health.status, health.message = "failed", f"{type(exc).__name__}: {exc}"[:200]
        out.transient = True
    except Exception as exc:
        health.status, health.message = "failed", f"{type(exc).__name__}: {exc}"[:200]
    return out


RETRY_PAUSE_SECONDS = 20.0


def heal(
    fetched: dict[str, Fetched],
    fetch: Callable[[str, str | None], Fetched] = fetch_provider,
    pause: float = RETRY_PAUSE_SECONDS,
) -> list[str]:
    """來源層自我修復，回傳要寫進本輪訊息的說明。

    * 被擋（403／429）且原本走 HTTP → 改用瀏覽器重抓一次（Gary 2026-11-14 核准；不改 UA、
      不登入、不解驗證碼）。
    * 暫時性失敗（逾時、連線、5xx）→ 等其他來源跑完、稍候再以原方式重試一次。
    結構性失敗（解析錯誤、列表為空）不在這裡重試，交給健康檢查判斷是否要開修復 session。
    """
    notes: list[str] = []
    for provider_id, item in list(fetched.items()):
        cls = providers.load(provider_id)
        status = item.health.status
        if status == "blocked" and cls.transport == "http":
            retry, mode, label = (
                fetch(provider_id, "browser"),
                "browser",
                "HTTP 被擋，改用瀏覽器重抓",
            )
        elif status == "failed" and item.transient:
            time.sleep(pause)
            retry, mode, label = fetch(provider_id, None), "retry", "暫時性失敗，重試"
        else:
            continue
        before = item.health.message
        if retry.health.status in ("ok", "partial"):
            retry.health.fallback = mode  # type: ignore[assignment]
            retry.health.message = "；".join(
                filter(None, [f"{label}成功（原因：{before[:80]}）", retry.health.message])
            )
            fetched[provider_id] = retry
            notes.append(f"{cls.name}：{label}成功")
        else:
            item.health.message = f"{before[:120]}；{label}仍失敗：{retry.health.message[:80]}"
            notes.append(f"{cls.name}：{label}仍失敗")
    return notes


def _with_period(stub: Stub, text: str) -> Stub:
    """列表與明細都沒給期間時，從條款「活動期間」那一行補（有寫年份才採用）。"""
    if stub.period_end is not None:
        return stub
    start, end = period_from_terms(text)
    if end is None:
        return stub
    return stub.model_copy(update={"period_start": stub.period_start or start, "period_end": end})


def save_docs(item: Fetched) -> None:
    """本輪抓到的條款原文存在本機（不進版控），供換模型重算或除錯時免重抓官網。"""
    DOCS.mkdir(parents=True, exist_ok=True)
    payload = {
        "health": item.health.model_dump(mode="json"),
        "skipped": item.skipped,
        "pages": [
            {"stub": p.stub.model_dump(mode="json"), "text": p.text, "external_url": p.external_url}
            for p in item.pages
        ],
    }
    (DOCS / f"{item.provider_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def load_docs(provider_id: str) -> Fetched:
    payload = json.loads((DOCS / f"{provider_id}.json").read_text(encoding="utf-8"))
    item = Fetched(provider_id, SourceHealth.model_validate(payload["health"]))
    item.skipped = list(payload.get("skipped") or [])
    item.pages = [
        Page(Stub.model_validate(p["stub"]), p["text"], p.get("external_url", ""))
        for p in payload["pages"]
    ]
    return item


def _dedupe(stubs: Iterable[Stub]) -> list[Stub]:
    seen: set[str] = set()
    out: list[Stub] = []
    for stub in stubs:
        if stub.key not in seen:
            seen.add(stub.key)
            out.append(stub)
    return out


def content_hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", "", normalize(text)).encode("utf-8")).hexdigest()[:16]


# ---- 組活動 ----------------------------------------------------------------


@dataclass
class Doc:
    provider_id: str
    stubs: list[Stub]
    text: str
    digest: str
    external_url: str = ""

    @property
    def primary(self) -> Stub:
        return self.stubs[0]

    @property
    def extractable(self) -> bool:
        return not self.external_url and len(self.text.strip()) >= MIN_TERMS_CHARS

    def request(self) -> Request:
        stub = self.primary
        period = f"{stub.period_start or '?'}~{stub.period_end or '?'}"
        return Request(title=stub.title, period=period, text=self.text)


def group_docs(fetched: Fetched, today: date | None = None) -> list[Doc]:
    """同一服務、條款文字相同的多張卡片合併成一筆活動，其餘標題留作別名。

    列表公告的結束日已過的活動直接略過 —— 不發布，也不浪費 LLM 額度。
    """
    groups: dict[str, Doc] = {}
    for page in fetched.pages:
        stub = page.stub
        if today and stub.period_end and stub.period_end < today:
            continue
        long_enough = len(page.text) >= 200 and not page.external_url
        digest = content_hash(page.text) if long_enough else content_hash(stub.key)
        doc = groups.get(digest)
        if doc is None:
            groups[digest] = Doc(fetched.provider_id, [stub], page.text, digest, page.external_url)
        else:
            doc.stubs.append(stub)
    for doc in groups.values():
        doc.stubs.sort(key=lambda s: (-len(s.title), s.key))
    return list(groups.values())


def activity_id(provider_id: str, key: str) -> str:
    return f"{provider_id}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:10]}"


def assemble(doc: Doc, output: dict[str, Any] | None, failure: str, observed: datetime) -> Activity:
    cls = providers.load(doc.provider_id)
    stub = doc.primary
    review: list[str] = []
    ctx = Context(
        text=doc.text,
        url=stub.url,
        period_start=stub.period_start,
        period_end=stub.period_end,
        observed=observed,
    )
    activity = Activity(
        id=activity_id(doc.provider_id, stub.key),
        provider_id=doc.provider_id,
        provider_name=cls.name,
        title=stub.title,
        aliases=sorted({s.title for s in doc.stubs} - {stub.title}),
        url=stub.url,
        period_start=stub.period_start,
        period_end=stub.period_end,
        category=stub.category,
        portal_url=cls.portal_url or cls.entry_url,
        content_hash=doc.digest,
        observed_at=observed,
        title_sold_out=[s for st in doc.stubs for s in title_sold_out(st.title, ctx)],
    )
    if doc.external_url:
        activity.terms_source = "external"
        activity.external_url = doc.external_url
        review.append("條款在合作方網站，未抽取，請點官方頁確認")
    elif not doc.extractable:
        activity.terms_source = "image"
        review.append("官方頁面沒有可讀的條款文字（多半是圖片），請直接查看活動頁")
    elif output is not None:
        built = build_offers(output, ctx)
        activity.is_offer = built.is_offer
        activity.registration = built.registration
        activity.registration_quote = built.registration_quote
        activity.offers = built.offers
        review.extend(built.review)
    elif failure:
        review.append(failure)
    activity.review = review
    return activity


# ---- 一輪 ----------------------------------------------------------------


def previous(provider_id: str) -> list[Activity]:
    path = STATE / "activities" / f"{provider_id}.json"
    if not path.exists():
        return []
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [Activity.model_validate(row) for row in rows]


def run(
    provider_ids: list[str] | None = None,
    *,
    model: str = DEFAULT_MODEL,
    llm_workers: int = 3,
    llm_budget: int = 300,
    fetch_workers: int = 4,
    observed: datetime | None = None,
    reuse_docs: bool = False,
) -> RunResult:
    observed = observed or datetime.now(TAIPEI).replace(microsecond=0)
    provider_ids = provider_ids or providers.available()
    messages: list[str] = []

    fetched: dict[str, Fetched] = {}
    if reuse_docs:
        fetched = {p: load_docs(p) for p in provider_ids}
    else:
        http_ids = [p for p in provider_ids if providers.load(p).transport == "http"]
        browser_ids = [p for p in provider_ids if p not in http_ids]
        with ThreadPoolExecutor(max_workers=fetch_workers) as pool:
            for result in pool.map(fetch_provider, http_ids):
                fetched[result.provider_id] = result
        for provider_id in browser_ids:
            fetched[provider_id] = fetch_provider(provider_id)
        messages.extend(heal(fetched))
        for item in fetched.values():
            save_docs(item)

    docs = [d for p in provider_ids for d in group_docs(fetched[p], observed.date())]
    outputs: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    pending: list[Doc] = []
    for doc in docs:
        if not doc.extractable:
            continue
        hit = cached(doc.request(), model)
        if hit is not None:
            outputs[doc.digest] = hit
        else:
            pending.append(doc)
    if len(pending) > llm_budget:
        messages.append(f"LLM 待抽取 {len(pending)} 筆，超出本輪額度 {llm_budget}，其餘下輪處理")
        for doc in pending[llm_budget:]:
            failures[doc.digest] = "條款本輪尚未抽取（超出額度，下輪處理）"
        pending = pending[:llm_budget]
    calls = 0
    auth_failed = False
    meter = Meter()
    with ThreadPoolExecutor(max_workers=llm_workers) as pool:
        futures = {pool.submit(locate, doc.request(), model, meter): doc for doc in pending}
        for future in as_completed(futures):
            doc = futures[future]
            if future.cancelled():
                failures[doc.digest] = "條款本輪尚未抽取（下輪處理）"
                continue
            calls += 1
            try:
                outputs[doc.digest] = future.result()
            except AuthExpired as exc:
                failures[doc.digest] = "條款本輪尚未抽取（下輪處理）"
                if not auth_failed:
                    auth_failed = True
                    messages.append(str(exc))
                    for other in futures:
                        other.cancel()
            except LocateError as exc:
                failures[doc.digest] = "條款抽取失敗，請查看官方頁"
                log.warning("LLM 失敗 %s：%s", doc.primary.url, exc)

    activities: dict[str, list[Activity]] = {}
    health: list[SourceHealth] = []
    for provider_id in provider_ids:
        item = fetched[provider_id]
        built = [
            assemble(doc, outputs.get(doc.digest), failures.get(doc.digest, ""), observed)
            for doc in docs
            if doc.provider_id == provider_id
        ]
        built = _carry_unextracted(built, provider_id)
        old = [a for a in previous(provider_id) if _still_valid(a, observed)]
        fresh_old = [a for a in old if not a.stale]
        if item.health.status in ("failed", "blocked"):
            built = [a.model_copy(update={"stale": True}) for a in old]
            item.health.carried = len(built)
        elif fresh_old and len(built) < len(fresh_old) * (1 - DROP_LIMIT):
            item.health.status = "partial"
            item.health.message = (
                f"筆數由 {len(fresh_old)} 掉到 {len(built)}（超過 {int(DROP_LIMIT * 100)}%），"
                "沿用上一輪資料"
            )
            built = [a.model_copy(update={"stale": True}) for a in old]
            item.health.carried = len(built)
        item.health.activities = len(built)
        activities[provider_id] = built
        health.append(item.health)

    failed = sum(1 for h in health if h.status in ("failed", "blocked"))
    publishable = failed < max(2, len(health) // 2)
    if not publishable:
        messages.append(f"{failed}/{len(health)} 個來源失敗，本輪不發布")
    return RunResult(
        observed,
        activities,
        health,
        calls,
        sum(1 for v in failures.values() if "失敗" in v),
        publishable,
        messages,
        {"model": model, **meter.as_dict()},
    )


def _still_valid(activity: Activity, observed: datetime) -> bool:
    return (
        activity.period_end is None or activity.period_end >= (observed - timedelta(days=1)).date()
    )


def _carry_unextracted(built: list[Activity], provider_id: str) -> list[Activity]:
    """本輪 LLM 沒跑到的活動，若上一輪同內容已有結果就沿用。"""
    old = {a.content_hash: a for a in previous(provider_id)}
    out: list[Activity] = []
    for activity in built:
        prior = old.get(activity.content_hash)
        unextracted = any("尚未抽取" in r or "抽取失敗" in r for r in activity.review)
        if unextracted and prior is not None and prior.offers:
            activity = activity.model_copy(
                update={
                    "is_offer": prior.is_offer,
                    "registration": prior.registration,
                    "registration_quote": prior.registration_quote,
                    "offers": prior.offers,
                    "review": [r for r in activity.review if "抽取" not in r] + prior.review,
                }
            )
        out.append(activity)
    return out


def save(result: RunResult) -> None:
    folder = STATE / "activities"
    folder.mkdir(parents=True, exist_ok=True)
    for provider_id, items in result.activities.items():
        rows = [a.model_dump(mode="json") for a in sorted(items, key=lambda a: a.id)]
        path = folder / f"{provider_id}.json"
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    run_info = {
        "observed_at": result.observed.isoformat(),
        "llm_calls": result.llm_calls,
        "llm_failures": result.llm_failures,
        "llm_usage": result.usage,
        "publishable": result.publishable,
        "messages": result.messages,
        "sources": [h.model_dump(mode="json") for h in result.health],
    }
    (STATE / "run.json").write_text(
        json.dumps(run_info, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )


def log_usage(result: RunResult, scope: list[str] | None, *, path: Path | None = None) -> None:
    """把本輪 LLM 用量附加到 ``var/usage.jsonl``；不論本輪是否發布都記（token 已花掉）。"""
    target = path or USAGE_LOG
    target.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "observed_at": result.observed.isoformat(),
        "scope": ",".join(scope) if scope else "all",
        "publishable": result.publishable,
        **result.usage,
    }
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_usage(path: Path | None = None) -> list[dict[str, Any]]:
    target = path or USAGE_LOG
    if not target.exists():
        return []
    return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line]
