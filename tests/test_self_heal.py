"""自我修復：來源層重試／改用瀏覽器、健康判定（通知、連續天數、修復候選與冷卻）。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from payradar.health import evaluate, record_repair
from payradar.models import SourceHealth
from payradar.pipeline import Fetched, heal

TODAY = date(2026, 11, 14)


def fetched(provider: str, status: str, message: str = "", transient: bool = False) -> Fetched:
    health = SourceHealth(
        provider_id=provider,
        provider_name=provider,
        status=status,  # type: ignore[arg-type]
        entry_url="https://x",
        message=message,
    )
    return Fetched(provider, health, transient=transient)


def test_blocked_http_source_falls_back_to_browser() -> None:
    calls: list[tuple[str, str | None]] = []

    def fake(provider: str, transport: str | None) -> Fetched:
        calls.append((provider, transport))
        return fetched(provider, "ok")

    items = {"opay": fetched("opay", "blocked", "HTTP 403")}
    notes = heal(items, fake, pause=0)
    assert calls == [("opay", "browser")]
    assert items["opay"].health.status == "ok"
    assert items["opay"].health.fallback == "browser"
    assert "HTTP 403" in items["opay"].health.message
    assert notes and "改用瀏覽器" in notes[0]


def test_browser_source_is_not_retried_when_blocked() -> None:
    """台灣Pay 本來就走瀏覽器：再被擋不能自己換別的方式，交給健康檢查。"""
    items = {"taiwanpay": fetched("taiwanpay", "blocked", "HTTP 403")}
    assert heal(items, lambda p, t: fetched(p, "ok"), pause=0) == []
    assert items["taiwanpay"].health.status == "blocked"


def test_transient_failure_retried_once_structural_not() -> None:
    calls: list[str] = []

    def fake(provider: str, transport: str | None) -> Fetched:
        calls.append(provider)
        assert transport is None, "暫時性失敗用原方式重試"
        return fetched(provider, "ok")

    items = {
        "pipay": fetched("pipay", "failed", "FetchFailed: 逾時", transient=True),
        "gamapay": fetched("gamapay", "failed", "ValueError: 格式改變"),
    }
    heal(items, fake, pause=0)
    assert calls == ["pipay"]
    assert items["pipay"].health.fallback == "retry"
    assert items["gamapay"].health.status == "failed"


def test_failed_retry_keeps_original_and_records_both_reasons() -> None:
    items = {"opay": fetched("opay", "blocked", "HTTP 403")}
    notes = heal(items, lambda p, t: fetched(p, "blocked", "瀏覽器也 403"), pause=0)
    assert items["opay"].health.status == "blocked"
    assert "瀏覽器也 403" in items["opay"].health.message
    assert "仍失敗" in notes[0]


def run(*sources: dict[str, str]) -> dict[str, object]:
    return {"sources": [{"provider_name": s["provider_id"], **s} for s in sources]}


def test_health_streak_suggests_permanent_browser(tmp_path: Path) -> None:
    log = tmp_path / "fallback.json"
    log.write_text(json.dumps({"opay": ["2026-11-12", "2026-11-13"]}))
    report = evaluate(
        run({"provider_id": "opay", "status": "ok", "fallback": "browser"}),
        TODAY,
        fallback_log=log,
        repair_log=tmp_path / "r.json",
    )
    assert any("連續 3 天" in n for n in report.notices)
    assert json.loads(log.read_text())["opay"][-1] == "2026-11-14"
    assert report.repairs == []


def test_health_repair_candidates_and_cooldown(tmp_path: Path) -> None:
    repairs = tmp_path / "r.json"
    sources = run(
        {"provider_id": "gamapay", "status": "failed", "message": "ValueError: 格式改變"},
        {"provider_id": "jkopay", "status": "partial", "message": "筆數由 35 掉到 3（超過 40%）"},
        {"provider_id": "pipay", "status": "partial", "message": "分類頁之一暫時無回應"},
        {"provider_id": "pxmart", "status": "ok"},
    )
    report = evaluate(sources, TODAY, fallback_log=tmp_path / "f.json", repair_log=repairs)
    assert [r["provider"] for r in report.repairs] == ["gamapay", "jkopay"]
    record_repair("gamapay", TODAY, repairs)
    again = evaluate(
        sources, date(2026, 11, 16), fallback_log=tmp_path / "f.json", repair_log=repairs
    )
    assert [r["provider"] for r in again.repairs] == ["jkopay"]
    assert any("冷卻中" in n for n in again.notices)
    later = evaluate(
        sources, date(2026, 11, 17), fallback_log=tmp_path / "f.json", repair_log=repairs
    )
    assert "gamapay" in [r["provider"] for r in later.repairs]


def test_health_quiet_when_all_ok(tmp_path: Path) -> None:
    report = evaluate(
        run({"provider_id": "opay", "status": "ok"}),
        TODAY,
        fallback_log=tmp_path / "f.json",
        repair_log=tmp_path / "r.json",
    )
    assert report.notices == [] and report.repairs == []
