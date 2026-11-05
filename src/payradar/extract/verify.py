"""把 LLM 定位結果驗證後組成 ``Offer``。

登錄時點交給 ``schedule.build``（沿用刷卡登錄雷達、逐子活動呼叫）；本模組
驗證支付專屬欄位，規則一律是：

1. 引句（以及 where／how／子活動名稱）逐字出現在條款原文（忽略空白差異）。
2. LLM 填的每個數字都能在引句裡找到（支援千分位、小數、「2萬」）。
3. 「已額滿」聲明必須真的是已發生的語氣（含「已」或以「額滿】」作結），
   「額滿為止」「名額額滿將於…公告」這類條件句一律不採用。

驗不過的欄位留空並在 review 記一句原因，不採用 LLM 的說法。
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from ..models import (
    Amount,
    Evidence,
    Offer,
    Per,
    Registration,
    Reward,
    RewardKind,
    RewardUnit,
    SoldOut,
    SubActivity,
    Tri,
)
from ..textnorm import normalize
from .schedule import Context, _resolve_year, build

# 已發生的額滿：「已…額滿」「已達上限」「已贈完」「…額滿】」
SOLD_OUT = re.compile(
    r"已[^。；;\n]{0,30}?(?:額滿|達[^。；;\n]{0,6}上限|贈完|送完|兌換完|發放完|領完|售完)"
    r"|(?:額滿|贈完|送完|領完)\s*[】\]］]"
)
# 參加前須主動完成的動作；條款完全沒有這些字樣時，判為不需登錄（不靠 LLM）
_ACTION_WORDS = re.compile(r"登錄|登記|報名|領券|領取|代碼|序號|同意加入|切換|預約|兌換|開通")
# 已額滿聲明裡的範圍字樣：LLM 沒給 scope_label 時由程式補，避免把「首週已額滿」當整檔額滿
_SCOPE_WORDS = re.compile(
    r"首[週周]|第[一二三四五六七八九十\d]+[波週周期梯]|早鳥|本[週周月]|\d{1,2}\s*/\s*\d{1,2}\s*[~～-]"
    r"\s*\d{1,2}\s*/\s*\d{1,2}|\d{1,2}\s*月份?"
)
_MONTH_SCOPE = re.compile(r"(\d{1,2})\s*月份?")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_WAN = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*萬")
_CN_COUNT = re.compile(r"([一乙兩二三四五六七八九十])\s*[張份次筆名個組人]")
_CN = {
    "一": 1,
    "乙": 1,
    "兩": 2,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
_REWARD_KINDS: tuple[RewardKind, ...] = (
    "percent",
    "cash",
    "points",
    "coupon",
    "discount",
    "gift",
    "other",
)
_UNITS: tuple[RewardUnit, ...] = ("%", "元", "點", "折", "筆", "次", "份", "other")
_PERS: dict[str, Per] = {
    "txn": "txn",
    "day": "day",
    "week": "week",
    "month": "month",
    "campaign": "campaign",
}


def _unit(value: Any) -> RewardUnit:
    return next((u for u in _UNITS if u == value), "other")


def _kind(value: Any) -> RewardKind:
    return next((k for k in _REWARD_KINDS if k == value), "other")


def numbers(quote: str) -> set[float]:
    """引句裡出現的所有數字（千分位去逗號、「2萬」＝20000、「滿千」＝1000、「一張」＝1）。"""
    value = normalize(quote)
    found = {float(n.replace(",", "")) for n in _NUMBER.findall(value)}
    found |= {float(n.replace(",", "")) * 10_000 for n in _WAN.findall(value)}
    found |= {float(_CN[c]) for c in _CN_COUNT.findall(value)}
    if re.search(r"(?<![\d一二三四五六七八九十百])千", value):
        found.add(1000.0)
    if re.search(r"(?<![\d一二三四五六七八九十])百(?![萬])", value):
        found.add(100.0)
    return found


def _supported(number: float, quote: str) -> bool:
    return any(abs(number - n) < 1e-9 for n in numbers(quote))


@dataclass
class Built:
    is_offer: Tri
    registration: Registration
    registration_quote: str
    offers: list[Offer]
    review: list[str]


def _tri(raw: dict[str, Any] | None, ctx: Context) -> tuple[Tri, str]:
    if not raw:
        return "unknown", ""
    value, quote = raw.get("value"), raw.get("quote", "")
    if value in ("yes", "no") and quote and ctx.quote_ok(quote):
        return value, quote
    return "unknown", ""


def _text(value: str, ctx: Context) -> str:
    value = (value or "").strip()
    return value if value and ctx.quote_ok(value) else ""


def _amount(
    raw: dict[str, Any] | None, ctx: Context, label: str, review: list[str]
) -> Amount | None:
    if not raw:
        return None
    quote = raw.get("quote", "")
    amount = raw.get("amount")
    if not quote or not ctx.quote_ok(quote):
        review.append(f"{label}引句不在原文中：{quote[:30]}")
        return None
    if amount is None or not _supported(float(amount), quote):
        review.append(f"{label} {amount} 在引句中找不到：{quote[:30]}")
        return None
    unit = _unit(raw.get("unit"))
    return Amount(
        amount=int(amount), unit=unit, per=_PERS.get(raw.get("per", ""), "unknown"), quote=quote
    )


def _rewards(raws: list[dict[str, Any]], ctx: Context, review: list[str]) -> list[Reward]:
    out: list[Reward] = []
    seen: set[tuple[str, float | None, str]] = set()
    for raw in raws:
        quote = raw.get("quote", "")
        if not quote or not ctx.quote_ok(quote):
            review.append(f"回饋引句不在原文中：{quote[:30]}")
            continue
        value = raw.get("value")
        if value is not None and not _supported(float(value), quote):
            review.append(f"回饋數值 {value} 在引句中找不到：{quote[:30]}")
            continue
        unit = _unit(raw.get("unit"))
        if unit == "%" and "%" not in normalize(quote):
            review.append(f"回饋標為百分比但引句沒有 %：{quote[:30]}")
            continue
        kind = _kind(raw.get("kind"))
        if unit == "%" and value is not None and float(value) >= 100:
            review.append(f"回饋 {value}% 不合理，多半是比例規則而非回饋：{quote[:30]}")
            continue
        key = (unit, float(value) if value is not None else None, kind)
        if key in seen:
            continue
        seen.add(key)
        out.append(Reward(kind=kind, value=key[1], unit=unit, quote=quote))
    return out


def _scope_day(point: dict[str, Any] | None, ctx: Context, *, end: bool) -> date | None:
    if not point or point.get("month") is None:
        return None
    month = int(point["month"])
    year = int(point["year"]) if point.get("year") else _resolve_year(month, 1, ctx)
    last = calendar.monthrange(year, month)[1]
    day = point.get("day") or (last if end else 1)
    try:
        return date(year, month, min(int(day), last))
    except ValueError:
        return None


def sold_out_from(
    raw: dict[str, Any], ctx: Context, review: list[str], *, source: str = "terms"
) -> SoldOut | None:
    quote = raw.get("quote", "")
    if not quote or not ctx.quote_ok(quote):
        review.append(f"額滿引句不在原文中：{quote[:30]}")
        return None
    if not SOLD_OUT.search(normalize(quote)):
        review.append(f"額滿引句不是已額滿的聲明：{quote[:30]}")
        return None
    label = (raw.get("scope_label") or "").strip()
    if label and not ctx.quote_ok(label):
        label = ""
    if not label and (found := _SCOPE_WORDS.search(normalize(quote))):
        label = found[0]
    start = _scope_day(raw.get("scope_start"), ctx, end=False)
    end = _scope_day(raw.get("scope_end"), ctx, end=True)
    nums = numbers(quote) | numbers(label)
    for day in (start, end):
        if day is not None and float(day.month) not in nums:
            review.append(f"額滿範圍 {day.month} 月在引句中找不到：{quote[:30]}")
            start = end = None
            break
    if start and end and end < start:
        start = end = None
    return SoldOut(
        scope_label=label,
        scope_start=start,
        scope_end=end,
        source=source,  # type: ignore[arg-type]
        evidence=Evidence(quote=quote, url=ctx.url),
    )


def title_sold_out(title: str, ctx: Context) -> list[SoldOut]:
    """官方列表標題上的額滿標記（確定性，不經 LLM）。

    台灣Pay「(活動額滿)」、一卡通「[11月份回饋上限已於2026/11/3 17:00 額滿]」。
    「每月額滿」這類沒有「已」也沒有括號作結的字樣不算。
    """
    value = normalize(title)
    whole = re.search(r"[（(【\[]\s*(?:活動)?額滿\s*[）)】\]]", value)
    match = whole or SOLD_OUT.search(value)
    if not match:
        return []
    label, start, end = "", None, None
    if not whole and (month := _MONTH_SCOPE.search(value[: match.end()])):
        number = int(month[1])
        if 1 <= number <= 12:
            year = _resolve_year(number, 1, ctx)
            label = month[0]
            start = date(year, number, 1)
            end = date(year, number, calendar.monthrange(year, number)[1])
    return [
        SoldOut(
            scope_label=label,
            scope_start=start,
            scope_end=end,
            source="title",
            evidence=Evidence(quote=title, url=ctx.url),
        )
    ]


def build_offers(output: dict[str, Any], ctx: Context) -> Built:
    review: list[str] = []
    is_offer, _ = _tri(output.get("is_offer"), ctx)
    reg_raw = output.get("registration") or {}
    offers: list[Offer] = []
    for raw in output.get("subs") or []:
        # 登錄時點：逐子活動交給 schedule.build（名額另外驗，因為它不認得「2萬」）
        part = build(
            {
                "registration": reg_raw,
                "subs": [{**raw, "quota": {"seats": None, "quote": ""}}],
            },
            ctx,
        )
        base: SubActivity = part.subs[0] if part.subs else SubActivity()
        sub_review = list(base.review)
        seats_raw = raw.get("quota") or {}
        seats, seats_quote = seats_raw.get("seats"), seats_raw.get("quote", "")
        if seats_quote and not ctx.quote_ok(seats_quote):
            seats, seats_quote = None, ""
        elif seats is not None and not _supported(float(seats), seats_quote):
            sub_review.append(f"名額 {seats} 在引句中找不到：{seats_quote[:30]}")
            seats = None
        new_users, new_users_quote = _tri(raw.get("new_users_only"), ctx)
        limited, limited_quote = _tri(raw.get("limited"), ctx)
        if limited == "unknown" and seats is not None:
            limited, limited_quote = "yes", seats_quote
        sold = [
            s
            for s in (sold_out_from(r, ctx, sub_review) for r in raw.get("sold_out") or [])
            if s is not None
        ]
        caps: list[Amount] = []
        for cap_raw in raw.get("caps") or []:
            cap = _amount(cap_raw, ctx, "上限", sub_review)
            if cap and all(
                (c.amount, c.unit, c.per) != (cap.amount, cap.unit, cap.per) for c in caps
            ):
                caps.append(cap)
        offers.append(
            Offer(
                **{
                    **base.model_dump(),
                    "review": sub_review,
                    "quota": seats,
                    "quota_quote": seats_quote,
                },
                rewards=_rewards(raw.get("rewards") or [], ctx, sub_review),
                min_spend=_amount(raw.get("min_spend"), ctx, "門檻", sub_review),
                caps=caps,
                new_users_only=new_users,
                new_users_quote=new_users_quote,
                where=_text(raw.get("where", ""), ctx),
                how=_text(raw.get("how", ""), ctx),
                limited=limited,
                limited_quote=limited_quote,
                sold_out=sold,
            )
        )
    value, quote = reg_raw.get("value"), reg_raw.get("quote", "")
    claimed = (
        value if value in ("required", "not_required") and quote and ctx.quote_ok(quote) else ""
    )
    has_slots = any(o.slots for o in offers)
    registration: Registration
    if has_slots or claimed == "required":
        registration = "required"
    elif claimed == "not_required" or not _ACTION_WORDS.search(normalize(ctx.text)):
        registration = "not_required"
    else:
        registration = "unknown"
    if registration == "required" and not has_slots and not any(o.rules for o in offers):
        review.append("需登錄或領券，但官方未寫時點")
    return Built(is_offer, registration, quote if claimed else "", offers, review)
