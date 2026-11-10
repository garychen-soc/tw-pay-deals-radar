"""LLM 定位：從條款全文找出回饋條件、名額與額滿、登錄規則，回傳逐字引句與數值。

分工原則 —— LLM 只做它擅長、正則做不好的事：

* 判斷哪一句是回饋條件、哪一句是舉例、哪個條件屬於哪個子活動；
* 分辨「已額滿」聲明與「額滿為止」條件句、每人上限與活動總預算；
* 讀懂「中午12:00」「遇假日順延」「無需每月登錄」這類語意。

登錄時點的換算由 ``payradar.extract.schedule``（沿用刷卡登錄雷達）完成；回饋、
上限、額滿的驗證由 ``payradar.extract.verify`` 完成：每個引句必須逐字出現在
原文、每個數字都必須能在引句裡找到。LLM 的輸出只是「候選」，過不了程式檢查
就不採用。

呼叫走本機 Claude Code CLI（``claude -p``，使用者的訂閱，不需 API key），
以 ``--json-schema`` 強制結構化輸出。結果以「提示版本＋模型＋條款文字」
的雜湊快取在 ``state/extractions/``，條款沒變就不重算。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROMPT_VERSION = "2026-11-10.2"
ROOT = Path(__file__).resolve().parents[3]
CACHE_DIR = ROOT / "state" / "extractions"
DEFAULT_MODEL = os.environ.get("PAYRADAR_LLM_MODEL", "sonnet")
TIMEOUT_SECONDS = 420
MAX_TERMS_CHARS = 30_000

# claude -p 回傳 modelUsage 的欄位 → 本專案的用量鍵
_USAGE_KEYS = {
    "inputTokens": "input",
    "outputTokens": "output",
    "cacheCreationInputTokens": "cache_creation",
    "cacheReadInputTokens": "cache_read",
}


def envelope_usage(envelope: dict[str, Any]) -> dict[str, int]:
    """一次 ``claude -p`` 的 token 用量（所有模型加總）。"""
    totals = dict.fromkeys(_USAGE_KEYS.values(), 0)
    per_model = envelope.get("modelUsage")
    if isinstance(per_model, dict) and per_model:
        for item in per_model.values():
            for source, key in _USAGE_KEYS.items():
                totals[key] += int(item.get(source) or 0)
        return totals
    usage = envelope.get("usage") or {}
    totals["input"] = int(usage.get("input_tokens") or 0)
    totals["output"] = int(usage.get("output_tokens") or 0)
    totals["cache_creation"] = int(usage.get("cache_creation_input_tokens") or 0)
    totals["cache_read"] = int(usage.get("cache_read_input_tokens") or 0)
    return totals


@dataclass
class Meter:
    """一輪的 LLM 用量累計（多執行緒共用）。只計實際呼叫，快取命中不算。"""

    calls: int = 0
    input: int = 0
    output: int = 0
    cache_creation: int = 0
    cache_read: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def add(self, usage: dict[str, int], cost_usd: float, duration_ms: int) -> None:
        with self._lock:
            self.calls += 1
            self.input += usage.get("input", 0)
            self.output += usage.get("output", 0)
            self.cache_creation += usage.get("cache_creation", 0)
            self.cache_read += usage.get("cache_read", 0)
            self.cost_usd += cost_usd
            self.seconds += duration_ms / 1000

    @property
    def tokens(self) -> int:
        return self.input + self.output + self.cache_creation + self.cache_read

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "input_tokens": self.input,
            "output_tokens": self.output,
            "cache_creation_tokens": self.cache_creation,
            "cache_read_tokens": self.cache_read,
            "total_tokens": self.tokens,
            "cost_usd": round(self.cost_usd, 4),
            "llm_seconds": round(self.seconds, 1),
        }


_POINT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["year", "month", "day", "hour", "minute"],
    "properties": {
        k: {"type": ["integer", "null"]} for k in ("year", "month", "day", "hour", "minute")
    },
}
_TIME = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["hour", "minute"],
    "properties": {"hour": {"type": "integer"}, "minute": {"type": "integer"}},
}
_CLAIM = {
    "type": "object",
    "additionalProperties": False,
    "required": ["value", "quote"],
    "properties": {"value": {"type": "string"}, "quote": {"type": "string"}},
}
_RULE = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "quote",
        "kind",
        "is_example",
        "holiday",
        "start",
        "end",
        "days_of_month",
        "nth",
        "weekday",
        "dates",
        "open_time",
        "close_time",
        "end_of_month",
    ],
    "properties": {
        "quote": {"type": "string"},
        "kind": {
            "enum": [
                "range",
                "opens_at",
                "deadline",
                "monthly",
                "monthly_nth_weekday",
                "enumerated",
                "other",
            ]
        },
        "is_example": {"type": "boolean"},
        "holiday": {"enum": ["next_business_day", "previous_business_day", "none"]},
        "start": {"anyOf": [_POINT, {"type": "null"}]},
        "end": {"anyOf": [_POINT, {"type": "null"}]},
        "days_of_month": {"type": "array", "items": {"type": "integer"}},
        "nth": {"type": ["integer", "null"]},
        "weekday": {"type": ["integer", "null"]},
        "dates": {"type": "array", "items": _POINT},
        "open_time": _TIME,
        "close_time": _TIME,
        "end_of_month": {"type": "boolean"},
    },
}
_TRI = {
    "type": "object",
    "additionalProperties": False,
    "required": ["value", "quote"],
    "properties": {"value": {"enum": ["yes", "no", "unclear"]}, "quote": {"type": "string"}},
}
_UNIT = {"enum": ["%", "元", "點", "折", "筆", "次", "份", "other"]}
_PER = {"enum": ["txn", "day", "week", "month", "campaign", "unclear"]}
_AMOUNT = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["amount", "unit", "per", "quote"],
    "properties": {
        "amount": {"type": "integer"},
        "unit": _UNIT,
        "per": _PER,
        "quote": {"type": "string"},
    },
}
_SOLD_OUT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["quote", "scope_label", "scope_start", "scope_end"],
    "properties": {
        "quote": {"type": "string"},
        "scope_label": {"type": "string"},
        "scope_start": {"anyOf": [_POINT, {"type": "null"}]},
        "scope_end": {"anyOf": [_POINT, {"type": "null"}]},
    },
}
SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["is_offer", "registration", "subs"],
    "properties": {
        "is_offer": _TRI,
        "registration": _CLAIM,
        "subs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "name",
                    "rewards",
                    "min_spends",
                    "caps",
                    "new_users_only",
                    "where",
                    "how",
                    "limited",
                    "quota",
                    "sold_out",
                    "rules",
                    "reregister",
                    "order",
                ],
                "properties": {
                    "name": {"type": "string"},
                    "rewards": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["kind", "value", "unit", "quote"],
                            "properties": {
                                "kind": {
                                    "enum": [
                                        "percent",
                                        "cash",
                                        "points",
                                        "coupon",
                                        "discount",
                                        "gift",
                                        "other",
                                    ]
                                },
                                "value": {"type": ["number", "null"]},
                                "unit": _UNIT,
                                "quote": {"type": "string"},
                            },
                        },
                    },
                    "min_spends": {"type": "array", "items": _AMOUNT},
                    "caps": {"type": "array", "items": _AMOUNT},
                    "new_users_only": _TRI,
                    "where": {"type": "string"},
                    "how": {"type": "string"},
                    "limited": _TRI,
                    "quota": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["seats", "quote"],
                        "properties": {
                            "seats": {"type": ["integer", "null"]},
                            "quote": {"type": "string"},
                        },
                    },
                    "sold_out": {"type": "array", "items": _SOLD_OUT},
                    "rules": {"type": "array", "items": _RULE},
                    "reregister": _CLAIM,
                    "order": _CLAIM,
                },
            },
        },
    },
}

INSTRUCTIONS = """你是台灣行動支付（電子支付）優惠活動條款的資料標註員。下面是某支付服務官方活動頁的條款全文。請依 JSON schema 標出回饋條件、名額與額滿、以及登錄／領券時點。

共通規則：
1. 每個 quote（以及 where、how、name）必須逐字複製自條款原文的一個連續片段，不可改字、拼接或省略中間文字；長度以足以支撐你填的數值為準（通常 10–80 字）。找不到就填空字串。
2. 數值只填原文明寫的數字，不換算、不加總、不推測。「1,000元」填 1000；「0.5%」填 0.5；「36全點」填 36。
3. 頁面有多個子活動（主活動／加碼、活動一／活動二、不同銀行或通路各自的回饋）時，每個子活動一個 subs 項目，name 逐字取自該子活動在頁面上的標題；條件只放進它所屬的子活動。整頁只有一個活動時 subs 只有一項且 name=""。
4. is_offer：這頁是不是給消費者的優惠活動（有回饋、折扣、贈品、抽獎等）。功能介紹、服務公告、使用教學、純新聞填 no；quote 取能支持判斷的一句。

回饋條件：
5. rewards：每種「消費者得到的」回饋一項，包括頁面列出的銀行、信用卡、帳戶或付款方式的加碼回饋（「綁定華南帳戶加碼2.5%」「指定付款方式最高享5%」）與階梯式的每一階；折抵比例上限、計算方式、手續費等規則不是回饋，不要放。標題上把多項加總起來的宣稱值（「累積最高13%」）不要另外記。條款順帶提到的「另一個活動」（例如「參加台灣Pay每筆消費再享1.5%等值紅利點數回饋活動之使用金融機構限…」只是說明那個活動限哪些銀行）不是本活動的回饋，不要放。kind：percent（%回饋）、cash（現金或回饋金固定金額）、points（點數）、coupon（優惠券、回饋券、數位券）、discount（現折、折抵、特價）、gift（贈品）、other。unit：%、元、點、折（「85折」value=85、「8折」value=8）、other。「最高享10%」填 value=10。只寫「享回饋」沒寫多少填 value=null。
6. min_spends：參加門檻，每個門檻一項（「單筆滿1,200元」amount=1200、unit=元、per=txn；「累計滿5,000元」per=campaign 或依原文的期間；「付款任3筆」amount=3、unit=筆）。階梯式回饋（「滿1,000送30、滿2,000送70」「滿30,000贈1%、滿100,000贈4%」）每一階的門檻都要列，對應的回饋也都要列進 rewards。沒有門檻填空陣列。
7. caps：每人（每帳戶）回饋上限，含單筆上限（「每人每月回饋上限200點」amount=200、unit=點、per=month；「單筆最高可得25全點」per=txn；「每人限贈1份」amount=1、unit=份；「每月限回饋1次」unit=次）。同一上限只填一次。抽獎機會的次數上限不是回饋上限，不要放；「最高享150全點」「最高享10%」是回饋數值，放 rewards，不是上限。活動總預算（「回饋總金額上限5萬元」）不是每人上限，不要放這裡，放 quota。
8. new_users_only：限新戶／首次綁定／新註冊會員才有填 yes；明寫全體會員皆可填 no；沒寫 unclear。
9. where：適用通路或店家的原文（例如「全臺「全聯福利中心」實體門市」）；how：付款方式或綁定條件的原文（例如「綁定台新銀行信用卡付款」「掃描TWQR」）。沒寫填空字串。

名額與額滿：
10. limited：原文寫「限量」「送完為止」「額滿為止」「名額有限」、或寫了總名額／總預算填 yes；沒寫 unclear。quota.seats：總名額或總份數（「限量1,000名」填 1000；「每月限量2萬份」填 20000 並以引句佐證「2萬」）；只有金額預算時填 null 但 quote 仍放那句。
11. sold_out：只放官方「已經」額滿／已達上限／已贈完的聲明（例如「【已於10/28(三)00:03額滿】」「11月份回饋上限已於2026/11/3 17:00 額滿」）。「額滿為止」「名額額滿將於…公告」「如額滿」「額滿後」是條件句，不是已額滿，絕不放這裡。scope_label 取原文的範圍字樣（「11月份」「早鳥滿額贈」「首週」，只有確定是整檔活動才填空字串）；scope_start/scope_end 是這句額滿涵蓋的期間（「11月份」→ 11/1~11/30；只寫月份時 day 填該月首日／末日；整檔填 null）。

登錄／領券時點（與信用卡登錄同一套規則）：
12. registration.value：參加前須主動完成的動作（登錄、報名、領券、加入點數計畫、切換權益、輸入代碼、開通券包）是否必要：required、not_required、unclear，附 quote。單純「綁定付款工具」「下載APP」「註冊會員」不算登錄。頁面有多個子活動時，任一子活動需要就填 required。條款描述了參加方式、卻沒有任何這類主動動作時，填 not_required，quote 取描述參加方式或消費即回饋的那一句（例如「於活動期間使用台灣Pay消費即享回饋」）；只有條款語焉不詳、無法判斷時才填 unclear。
13. rules：只標登錄／領券的時點。活動期間、消費期間、回饋入帳時間都不是登錄時點。is_example=true 只用在舉例情境句。kind：range（明寫起訖）、opens_at（明寫開始未寫截止）、deadline（只寫截止）、monthly（每月第幾日固定時刻開放，時刻放 open_time）、monthly_nth_weekday（weekday 1=週一…7=週日）、enumerated（列舉多個日期，放 dates）、other（如「消費後 7 日內」）。沒寫年份 year=null；「中午12:00」hour=12；「下午3點」hour=15；「24:00」填 hour=23、minute=59；沒寫時刻 hour/minute=null。holiday：寫「遇假日順延」填 next_business_day；「提前至前一營業日」填 previous_business_day；沒寫填 none。close_time：同一天內的截止時刻。end_of_month：「至月底」「當月內完成」填 true。
14. reregister.value：each_period（每月／每期須重新登錄或領券）、once、unclear。order.value：register_before_spend（須先登錄或領券再消費）、retroactive_ok（活動期間內完成即可）、unclear。unclear 時 quote=""。
15. 不需登錄時 rules 為空陣列。不知道就留空或 unclear，絕不推測。"""


class LocateError(RuntimeError):
    pass


class AuthExpired(LocateError):
    """claude CLI 的登入過期。整輪都會失敗，呼叫端應停止並通知使用者重新登入。"""


class LimitReached(AuthExpired):
    """訂閱用量額度用完（「You've hit your … limit」）。同樣整輪停止，等額度重置。

    2026-11-06 實測：沒有這個判斷時，額度用完後 pipeline 仍把剩下 146 頁逐一送出，
    全部失敗；應在第一次就停。
    """


@dataclass(frozen=True)
class Request:
    title: str
    period: str
    text: str

    def prompt(self) -> str:
        return (
            f"{INSTRUCTIONS}\n\n活動標題：{self.title}\n活動期間（列表公告）：{self.period or '未知'}\n"
            f"條款全文：\n<<<\n{self.text[:MAX_TERMS_CHARS]}\n>>>"
        )


def cache_key(request: Request, model: str) -> str:
    material = "\x1f".join([PROMPT_VERSION, model, request.title, request.period, request.text])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def cached(request: Request, model: str = DEFAULT_MODEL) -> dict[str, Any] | None:
    path = CACHE_DIR / f"{cache_key(request, model)}.json"
    if not path.exists():
        return None
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    output: dict[str, Any] = payload["output"]
    return output


def _claude_bin() -> str:
    found = os.environ.get("CLAUDE_BIN") or shutil.which("claude")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "claude"
    if fallback.exists():
        return str(fallback)
    raise LocateError("找不到 claude CLI（設定 CLAUDE_BIN 或把它加進 PATH）")


def locate(
    request: Request, model: str = DEFAULT_MODEL, meter: Meter | None = None
) -> dict[str, Any]:
    """回傳符合 ``SCHEMA`` 的 dict。有快取直接回快取；實際呼叫的用量記入 ``meter``。"""
    hit = cached(request, model)
    if hit is not None:
        return hit
    command = [
        _claude_bin(),
        "-p",
        "--model",
        model,
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(SCHEMA, ensure_ascii=False),
        "--tools",
        "",
        "--strict-mcp-config",
        "--setting-sources",
        "",
        "--no-session-persistence",
    ]
    with tempfile.TemporaryDirectory(prefix="payradar-llm-") as workdir:
        try:
            completed = subprocess.run(
                command,
                input=request.prompt(),
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                cwd=workdir,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise LocateError(f"claude -p 逾時（{TIMEOUT_SECONDS}s）") from exc
    try:
        envelope = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise LocateError(
            f"claude -p 失敗（{completed.returncode}）：{(completed.stderr or completed.stdout)[-300:]}"
        ) from exc
    usage = envelope_usage(envelope)
    if meter is not None:
        meter.add(
            usage,
            float(envelope.get("total_cost_usd") or 0),
            int(envelope.get("duration_ms") or 0),
        )
    reason = str(envelope.get("result") or "")
    if "hit your" in reason and "limit" in reason:
        raise LimitReached(f"claude 用量額度已用完：{reason[:200]}")
    if "authenticate" in reason or "OAuth" in reason or "/login" in reason:
        raise AuthExpired(f"claude CLI 登入失效：{reason[:200]}（請執行 claude auth login）")
    output = envelope.get("structured_output")
    if envelope.get("is_error") or not isinstance(output, dict):
        raise LocateError(f"claude -p 沒有結構化輸出：{str(envelope.get('result'))[:200]}")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "key": cache_key(request, model),
        "prompt_version": PROMPT_VERSION,
        "model": model,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "title": request.title,
        "duration_ms": envelope.get("duration_ms"),
        "cost_usd": envelope.get("total_cost_usd"),
        "usage": usage,
        "output": output,
    }
    path = CACHE_DIR / f"{record['key']}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return output
