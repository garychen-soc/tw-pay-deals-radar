"""驗證 scripts/build_ics.py 產出的行事曆訂閱檔符合 RFC 5545（不需網路）。

守門重點：訂閱檔一旦格式壞掉，使用者的日曆 App 會整份拒收或顯示亂碼，而且訂閱是
「加一次、之後自動同步」，壞掉不會有人回報。這裡守住折行、跳脫、全天日期與 UID 穩定性。
"""
import datetime
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_ics  # noqa: E402

STAMP = datetime.datetime(2026, 8, 14, 10, 20, tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
TODAY = STAMP.date()


def activity(**kw):
    base = {
        "id": "x1",
        "provider_name": "測試Pay",
        "title": "測試活動",
        "reward": "10%",
        "lifecycle": "active",
        "quota_status": "not_marked_full",
        "start_date": None,
        "end_date": "2026-09-30",
    }
    base.update(kw)
    return base


class TestFold(unittest.TestCase):
    def test_short_line_untouched(self):
        self.assertEqual(build_ics.fold("SUMMARY:abc"), "SUMMARY:abc")

    def test_folded_lines_within_75_octets(self):
        line = "DESCRIPTION:" + "台灣行動支付優惠" * 20
        for part in build_ics.fold(line).split("\r\n"):
            self.assertLessEqual(len(part.encode("utf-8")), 75)

    def test_unfolding_restores_original(self):
        line = "DESCRIPTION:" + "台灣行動支付優惠雷達abc" * 15
        self.assertEqual(build_ics.fold(line).replace("\r\n ", ""), line)

    def test_never_splits_multibyte_char(self):
        # 中文字被切在位元組中間就會變亂碼，這裡確保每段都能單獨解碼
        for part in build_ics.fold("X:" + "測" * 60).split("\r\n"):
            part.encode("utf-8").decode("utf-8")  # 不得拋例外


class TestEscape(unittest.TestCase):
    def test_escapes_special_chars(self):
        self.assertEqual(build_ics.escape("a;b,c\\d\ne"), "a\\;b\\,c\\\\d\\ne")

    def test_backslash_escaped_first(self):
        # 先跳脫反斜線才不會把後面補上的跳脫字元再跳脫一次
        self.assertEqual(build_ics.escape("\\;"), "\\\\\\;")


class TestBuildEvents(unittest.TestCase):
    def test_aggregates_same_end_date_into_one_event(self):
        data = {"activities": [activity(id=f"a{i}") for i in range(88)]}
        events = build_ics.build_events(data, TODAY)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["summary"], "88 檔優惠今日截止")

    def test_excludes_sold_out_and_ended(self):
        data = {"activities": [
            activity(id="ok"),
            activity(id="full", quota_status="sold_out"),
            activity(id="over", lifecycle="ended"),
        ]}
        events = build_ics.build_events(data, TODAY)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["summary"], "1 檔優惠今日截止")

    def test_excludes_past_end_dates(self):
        data = {"activities": [activity(end_date="2026-08-13")]}
        self.assertEqual(build_ics.build_events(data, TODAY), [])

    def test_includes_today_end_date(self):
        data = {"activities": [activity(end_date="2026-08-14")]}
        self.assertEqual(len(build_ics.build_events(data, TODAY)), 1)

    def test_future_start_date_creates_start_event(self):
        data = {"activities": [activity(start_date="2026-09-01", end_date="2026-09-30")]}
        kinds = sorted(e["uid"].split("-")[0] for e in build_ics.build_events(data, TODAY))
        self.assertEqual(kinds, ["end", "start"])

    def test_already_started_creates_no_start_event(self):
        data = {"activities": [activity(start_date="2026-07-01")]}
        events = build_ics.build_events(data, TODAY)
        self.assertTrue(all(e["uid"].startswith("end-") for e in events))

    def test_missing_dates_are_skipped(self):
        data = {"activities": [activity(start_date=None, end_date=None)]}
        self.assertEqual(build_ics.build_events(data, TODAY), [])

    def test_description_truncates_long_lists(self):
        n = build_ics.MAX_ITEMS_IN_DESCRIPTION + 12
        data = {"activities": [activity(id=f"a{i}") for i in range(n)]}
        desc = build_ics.build_events(data, TODAY)[0]["description"]
        self.assertIn("…另有 12 檔", desc)
        self.assertEqual(desc.count("・"), build_ics.MAX_ITEMS_IN_DESCRIPTION)

    def test_uid_is_stable_across_runs(self):
        data = {"activities": [activity()]}
        first = build_ics.build_events(data, TODAY)[0]["uid"]
        second = build_ics.build_events(data, TODAY)[0]["uid"]
        self.assertEqual(first, second)
        self.assertEqual(first, "end-2026-09-30@tw-pay-deals-radar")


class TestRender(unittest.TestCase):
    def setUp(self):
        data = {"activities": [activity(), activity(id="b", end_date="2026-12-31")]}
        self.ics = build_ics.render(build_ics.build_events(data, TODAY), STAMP)

    def test_crlf_line_endings_only(self):
        self.assertNotIn("\n", self.ics.replace("\r\n", ""))

    def test_calendar_wrapper(self):
        self.assertTrue(self.ics.startswith("BEGIN:VCALENDAR\r\n"))
        self.assertTrue(self.ics.endswith("END:VCALENDAR\r\n"))

    def test_event_count_balanced(self):
        self.assertEqual(self.ics.count("BEGIN:VEVENT"), self.ics.count("END:VEVENT"))

    def test_all_day_dtend_is_day_after(self):
        self.assertIn("DTSTART;VALUE=DATE:20260930\r\n", self.ics)
        self.assertIn("DTEND;VALUE=DATE:20261001\r\n", self.ics)

    def test_dtstamp_converted_to_utc(self):
        # 2026-08-14 10:20 +08:00 → 02:20Z
        self.assertIn("DTSTAMP:20260814T022000Z\r\n", self.ics)

    def test_every_line_within_75_octets(self):
        for line in self.ics.split("\r\n")[:-1]:
            self.assertLessEqual(len(line.encode("utf-8")), 75)

    def test_uids_unique(self):
        uids = [l for l in self.ics.split("\r\n") if l.startswith("UID:")]
        self.assertEqual(len(uids), len(set(uids)))


class TestShippedFeed(unittest.TestCase):
    """實際 commit 進 repo 的 calendar.ics 必須與目前資料一致，否則訂閱者會拿到舊資料。"""

    def test_calendar_ics_matches_current_data(self):
        import json

        feed = ROOT / "calendar.ics"
        self.assertTrue(feed.exists(), "calendar.ics 不存在，請跑 python3 scripts/build_ics.py")
        data = json.loads((ROOT / "data" / "promotions.json").read_text("utf-8"))
        stamp = datetime.datetime.fromisoformat(data["generated_at"])
        expected = build_ics.render(build_ics.build_events(data, stamp.date()), stamp)
        # 用 read_bytes 而非 read_text：避免 universal newlines 把 CRLF 轉成 LF，那會讓比對失去意義
        self.assertEqual(
            feed.read_bytes().decode("utf-8"),
            expected,
            "calendar.ics 與 promotions.json 不同步，請重跑 python3 scripts/build_ics.py",
        )


if __name__ == "__main__":
    unittest.main()
