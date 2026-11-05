"""支付欄位驗證：引句逐字、數字佐證、已額滿與條件句的分辨。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from payradar.extract.schedule import Context
from payradar.extract.verify import SOLD_OUT, build_offers, numbers, title_sold_out
from payradar.pipeline import TAIPEI

TEXT = """活動期間:2026/10/28~2026/11/30
早鳥滿額贈:10/28~10/31期間使用悠遊付繳納地價稅累計滿5,000元，贈總價值100元悠遊付現金回饋券1份，每戶限贈1份，限量1,000名。【已於10/28(三)00:03額滿】
筆筆3%回饋:使用悠遊付繳納地價稅每筆扣款享3%悠遊付回饋金，每人每月回饋上限50元。
本活動10月份限量回饋2萬份於10/29(四)18:00已額滿。
名額額滿將於官網公告，額滿為止。
活動期間使用悠遊付繳納地價稅之人，視同參加本活動。"""


def ctx(text: str = TEXT) -> Context:
    return Context(
        text=text,
        url="https://easywallet.easycard.com.tw/benefit/content?id=1",
        period_start=date(2026, 10, 28),
        period_end=date(2026, 11, 30),
        observed=datetime(2026, 11, 6, 9, 0, tzinfo=TAIPEI),
    )


def sub(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "",
        "rewards": [],
        "min_spend": None,
        "caps": [],
        "new_users_only": {"value": "unclear", "quote": ""},
        "where": "",
        "how": "",
        "limited": {"value": "unclear", "quote": ""},
        "quota": {"seats": None, "quote": ""},
        "sold_out": [],
        "rules": [],
        "reregister": {"value": "unclear", "quote": ""},
        "order": {"value": "unclear", "quote": ""},
    }
    return {**base, **fields}


def output(*subs: dict[str, Any], reg: str = "unclear", reg_quote: str = "") -> dict[str, Any]:
    return {
        "is_offer": {"value": "yes", "quote": "筆筆3%回饋"},
        "registration": {"value": reg, "quote": reg_quote},
        "subs": list(subs),
    }


def test_numbers_supports_commas_wan_and_cn_counts() -> None:
    assert {3000.0, 30_000_000.0} <= numbers("活動總限量3,000萬點")
    assert 1.0 in numbers("每人限贈一張")
    assert 1.0 in numbers("每一全支付帳號限回饋乙次")
    assert 20_000.0 in numbers("每月限量2萬份")
    assert 0.5 in numbers("享0.5%回饋")


def test_sold_out_regex_rejects_conditional_sentences() -> None:
    assert SOLD_OUT.search("【已於10/28(三)00:03額滿】")
    assert SOLD_OUT.search("11月份回饋上限已於2026/11/3 17:00 額滿")
    assert SOLD_OUT.search("10月份限量回饋2萬份於10/29(四)18:00已額滿")
    for conditional in ("額滿為止", "名額額滿將於全支付公告", "如額滿恕不另行通知", "送完為止"):
        assert not SOLD_OUT.search(conditional), conditional


def test_build_offers_verifies_quotes_and_numbers() -> None:
    raw = sub(
        name="早鳥滿額贈",
        rewards=[
            {
                "kind": "coupon",
                "value": 100,
                "unit": "元",
                "quote": "贈總價值100元悠遊付現金回饋券1份",
            },
            {"kind": "percent", "value": 5, "unit": "%", "quote": "每筆扣款享3%悠遊付回饋金"},
            {"kind": "cash", "value": 9, "unit": "元", "quote": "這句不在原文裡"},
        ],
        min_spend={"amount": 5000, "unit": "元", "per": "campaign", "quote": "累計滿5,000元"},
        caps=[
            {"amount": 1, "unit": "份", "per": "campaign", "quote": "每戶限贈1份"},
            {"amount": 1, "unit": "份", "per": "campaign", "quote": "每戶限贈1份"},
        ],
        quota={"seats": 1000, "quote": "限量1,000名"},
        sold_out=[
            {
                "quote": "【已於10/28(三)00:03額滿】",
                "scope_label": "",
                "scope_start": None,
                "scope_end": None,
            },
            {
                "quote": "名額額滿將於官網公告，額滿為止",
                "scope_label": "",
                "scope_start": None,
                "scope_end": None,
            },
        ],
    )
    built = build_offers(output(raw), ctx())
    offer = built.offers[0]
    assert [(r.unit, r.value) for r in offer.rewards] == [("元", 100.0)]
    assert offer.min_spend is not None and offer.min_spend.amount == 5000
    assert [(c.amount, c.unit) for c in offer.caps] == [(1, "份")], "重複的上限只留一個"
    assert offer.quota == 1000 and offer.limited == "yes"
    assert len(offer.sold_out) == 1 and offer.sold_out[0].scope_start is None
    assert any("5" in r and "找不到" in r for r in offer.review)
    assert any("不是已額滿" in r for r in offer.review)
    assert built.is_offer == "yes"


def test_sold_out_month_scope_resolved_within_period() -> None:
    raw = sub(
        sold_out=[
            {
                "quote": "本活動10月份限量回饋2萬份於10/29(四)18:00已額滿",
                "scope_label": "10月份",
                "scope_start": {"year": None, "month": 10, "day": 1, "hour": None, "minute": None},
                "scope_end": {"year": None, "month": 10, "day": None, "hour": None, "minute": None},
            }
        ],
        quota={"seats": 20000, "quote": "本活動10月份限量回饋2萬份"},
    )
    offer = build_offers(output(raw), ctx()).offers[0]
    sold = offer.sold_out[0]
    assert (sold.scope_label, sold.scope_start, sold.scope_end) == (
        "10月份",
        date(2026, 10, 1),
        date(2026, 10, 31),
    )
    assert offer.quota == 20000


def test_registration_not_required_claim() -> None:
    built = build_offers(output(sub(), reg="not_required", reg_quote="視同參加本活動"), ctx())
    assert built.registration == "not_required"


def test_title_sold_out_markers() -> None:
    whole = title_sold_out("東森購物刷台灣Pay回饋加倍享20%(活動額滿)", ctx())
    assert len(whole) == 1 and whole[0].scope_start is None and whole[0].source == "title"
    month = title_sold_out("[11月份回饋上限已於2026/11/3 17:00 額滿 ]綁定聯邦信用卡", ctx())
    assert month[0].scope_start == date(2026, 11, 1) and month[0].scope_end == date(2026, 11, 30)
    assert title_sold_out("每月額滿為止，送完為止", ctx()) == []


def test_sold_out_scope_label_inferred_from_quote() -> None:
    text = "首週回饋限量10,000全點，贈完為止。(首週回饋已額滿)"
    raw = sub(
        sold_out=[
            {"quote": "(首週回饋已額滿)", "scope_label": "", "scope_start": None, "scope_end": None}
        ]
    )
    sold = build_offers(output(raw), ctx(text)).offers[0].sold_out[0]
    assert sold.scope_label == "首週" and sold.scope_start is None
