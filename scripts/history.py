#!/usr/bin/env python3
"""把每輪 promotions.json 存進 SQLite，追蹤活動生命週期與額滿歷史（吸收 codex 可稽核設計）。

先前缺點：只有當天 JSON、無歷史，無法回答「這活動何時首次出現、何時額滿、規模趨勢」。
本腳本每輪 upsert 進 data/monitor.sqlite3（本地歷史，.gitignore 排除、不 push）：
- activities：每個活動的首見/末見/出現次數/最新狀態
- quota_log：額滿狀態每次變化的時間點（額滿何時發生、是否回補）
- runs：每輪規模與來源成功率快照

用法：python3 scripts/history.py    # 讀 data/promotions.json → 更新 data/monitor.sqlite3
排程 commit 前跑（見 SKILL 輸出步驟）。純標準庫，無第三方相依。
"""
import datetime
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "promotions.json"
DB = ROOT / "data" / "monitor.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS activities(
  activity_id TEXT PRIMARY KEY, provider_name TEXT, title TEXT, url TEXT,
  first_seen TEXT, last_seen TEXT, times_seen INTEGER DEFAULT 0,
  last_lifecycle TEXT, last_quota TEXT);
CREATE TABLE IF NOT EXISTS quota_log(
  activity_id TEXT, quota_status TEXT, observed_at TEXT,
  PRIMARY KEY(activity_id, quota_status, observed_at));
CREATE TABLE IF NOT EXISTS runs(
  run_at TEXT PRIMARY KEY, generated_at TEXT, total INTEGER, status TEXT,
  official_succeeded INTEGER, official_expected INTEGER, coverage_gaps INTEGER);
"""


def ingest(con, data, now):
    """把一輪資料寫進 DB（可測：傳入 sqlite 連線與資料 dict）。回傳統計 dict。"""
    con.executescript(SCHEMA)
    cur = con.cursor()
    acts = data.get("activities", [])
    quota_changes = 0
    for a in acts:
        aid = a.get("id")
        if not aid:
            continue
        prov, title, url = a.get("provider_name"), a.get("title"), a.get("url")
        life, quota = a.get("lifecycle"), a.get("quota_status")
        row = cur.execute(
            "SELECT last_quota FROM activities WHERE activity_id=?", (aid,)
        ).fetchone()
        if row:
            cur.execute(
                "UPDATE activities SET last_seen=?,times_seen=times_seen+1,title=?,"
                "url=?,last_lifecycle=?,last_quota=?,provider_name=? WHERE activity_id=?",
                (now, title, url, life, quota, prov, aid))
            prev_quota = row[0]
        else:
            cur.execute(
                "INSERT INTO activities(activity_id,provider_name,title,url,first_seen,"
                "last_seen,times_seen,last_lifecycle,last_quota) VALUES(?,?,?,?,?,?,1,?,?)",
                (aid, prov, title, url, now, now, life, quota))
            prev_quota = None
        if quota and quota != prev_quota:  # 額滿狀態變化才記一筆
            cur.execute(
                "INSERT OR IGNORE INTO quota_log(activity_id,quota_status,observed_at) "
                "VALUES(?,?,?)", (aid, quota, now))
            quota_changes += 1
    sh = data.get("source_health") or {}
    osrc = sh.get("official_sources") or {}
    cur.execute(
        "INSERT OR REPLACE INTO runs(run_at,generated_at,total,status,official_succeeded,"
        "official_expected,coverage_gaps) VALUES(?,?,?,?,?,?,?)",
        (now, data.get("generated_at"), len(acts), sh.get("status"),
         osrc.get("succeeded"), osrc.get("expected"), len(sh.get("coverage_gaps") or [])))
    con.commit()
    return {"ingested": len(acts), "quota_changes": quota_changes}


def main():
    data = json.loads(DATA.read_text("utf-8"))
    now = data.get("generated_at") or datetime.datetime.now().astimezone().isoformat()
    con = sqlite3.connect(DB)
    stats = ingest(con, data, now)
    cur = con.cursor()
    total_hist = cur.execute("SELECT COUNT(*) FROM activities").fetchone()[0]
    sold = cur.execute(
        "SELECT COUNT(*) FROM activities WHERE last_quota IN "
        "('sold_out','partial_sold_out')").fetchone()[0]
    runs = cur.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    con.close()
    print(f"歷史 DB 更新：本輪 {stats['ingested']} 筆（額滿狀態變化 {stats['quota_changes']}）"
          f"｜歷史累計活動 {total_hist} 筆｜目前額滿 {sold}｜已記錄 {runs} 輪")


if __name__ == "__main__":
    main()
