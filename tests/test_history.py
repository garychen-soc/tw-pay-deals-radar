"""測試 history.py 的 ingest 邏輯（用記憶體 SQLite，不碰真 DB、不需網路）。"""
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import history  # noqa: E402


def _data(quota="not_marked_full"):
    return {
        "generated_at": "2026-08-05T09:00:00+08:00",
        "source_health": {"status": "ok",
                          "official_sources": {"succeeded": 14, "expected": 15},
                          "coverage_gaps": ["icash App-only"]},
        "activities": [{"id": "a1", "provider_name": "台灣Pay", "title": "T",
                        "url": "https://x", "lifecycle": "active", "quota_status": quota}],
    }


class TestHistoryIngest(unittest.TestCase):
    def test_insert_then_upsert(self):
        con = sqlite3.connect(":memory:")
        history.ingest(con, _data(), "2026-08-05T09:00:00+08:00")
        r1 = con.execute("SELECT times_seen FROM activities WHERE activity_id='a1'").fetchone()
        self.assertEqual(r1[0], 1)
        history.ingest(con, _data(), "2026-08-06T09:00:00+08:00")  # 第二輪
        r2 = con.execute("SELECT times_seen,first_seen,last_seen FROM activities WHERE activity_id='a1'").fetchone()
        self.assertEqual(r2[0], 2)                              # times_seen 遞增
        self.assertEqual(r2[1], "2026-08-05T09:00:00+08:00")   # first_seen 保留
        self.assertEqual(r2[2], "2026-08-06T09:00:00+08:00")   # last_seen 更新

    def test_quota_change_logged(self):
        con = sqlite3.connect(":memory:")
        history.ingest(con, _data("not_marked_full"), "2026-08-05T09:00:00+08:00")
        history.ingest(con, _data("sold_out"), "2026-08-06T09:00:00+08:00")   # 額滿了
        history.ingest(con, _data("sold_out"), "2026-08-07T09:00:00+08:00")   # 維持額滿（不重複記）
        logs = con.execute("SELECT quota_status FROM quota_log WHERE activity_id='a1' ORDER BY observed_at").fetchall()
        self.assertEqual([x[0] for x in logs], ["not_marked_full", "sold_out"])

    def test_runs_recorded(self):
        con = sqlite3.connect(":memory:")
        stats = history.ingest(con, _data(), "2026-08-05T09:00:00+08:00")
        self.assertEqual(stats["ingested"], 1)
        run = con.execute("SELECT total,official_succeeded,coverage_gaps FROM runs").fetchone()
        self.assertEqual(run, (1, 14, 1))


if __name__ == "__main__":
    unittest.main()
