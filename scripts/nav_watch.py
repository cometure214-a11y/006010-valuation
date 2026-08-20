#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nav_watch.py —— 净值更新检测器（云端定时任务用）

职责：
  1. 调东方财富接口抓 006010 最新官方净值
  2. 与 cache/last_nav.json 记录的上次净值对比
  3. 若发现新净值（日期变化）→ 更新记录并输出 updated=1 + 明细
  4. 若未更新 → 输出 updated=0（workflow 据此跳过重活）

用法：
  python scripts/nav_watch.py --check        # 检测（供 workflow）
  python scripts/nav_watch.py --show         # 只看当前最新净值

输出（--check）：
  updated=1 date=2026-08-20 nav=0.5558 chg=+3.23%
  或
  updated=0 last_date=2026-08-20
"""
import json, os, sys, urllib.request, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "cache")
LAST_FILE = os.path.join(CACHE, "last_nav.json")
CODE = "006010"

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://fundf10.eastmoney.com/"}


def latest_nav():
    """抓最新一条官方净值，返回 (date, nav, chg%) 或 None"""
    url = (f"https://api.fund.eastmoney.com/f10/lsjz?fundCode={CODE}"
           f"&pageIndex=1&pageSize=2")
    req = urllib.request.Request(url, headers=UA)
    d = json.load(urllib.request.urlopen(req, timeout=25))
    rows = d["Data"]["LSJZList"]
    if not rows:
        return None
    r = rows[0]  # 最新一条
    return (r["FSRQ"], float(r["DWJZ"]), r.get("JZZZL"))


def load_last():
    if os.path.exists(LAST_FILE):
        try:
            return json.load(open(LAST_FILE, encoding="utf-8"))
        except Exception:
            return None
    return None


def save_last(date, nav):
    os.makedirs(CACHE, exist_ok=True)
    json.dump({"date": date, "nav": nav}, open(LAST_FILE, "w", encoding="utf-8"))


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--check"
    cur = latest_nav()
    if not cur:
        print("error=无法获取净值接口")
        sys.exit(0)
    date, nav, chg = cur
    if mode == "--show":
        print(f"最新净值: {date} {nav} ({chg}%)")
        return
    last = load_last()
    last_date = last.get("date") if last else None
    if last_date == date:
        print(f"updated=0 last_date={date}")
        return
    save_last(date, nav)
    print(f"updated=1 date={date} nav={nav} chg={chg}")
    print(f"last_date={last_date or 'none'}")


if __name__ == "__main__":
    main()
