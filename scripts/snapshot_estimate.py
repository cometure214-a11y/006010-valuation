#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""snapshot_estimate.py: 估值后把目标日+估算归档到 estimates_history.json，防 result.json 覆盖丢失。"""
import json, os, sys, time, datetime
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "cache")
EST_HIST = os.path.join(CACHE, "estimates_history.json")

def load(name):
    try:
        return json.load(open(os.path.join(CACHE, name), encoding="utf-8"))
    except Exception:
        return None

def main():
    res = load("result.json")
    if not res:
        print("[snapshot] 无 result.json，跳过"); return 0
    date = res.get("target_date") or res.get("cur_date")
    est = res.get("P_final_corr")
    if not date or est is None:
        print("[snapshot] result.json 缺 target_date/P_final_corr，跳过"); return 0
    try:
        est = float(est)
    except (TypeError, ValueError):
        print("[snapshot] P_final_corr 非数字，跳过"); return 0
    try:
        today = datetime.date.today()
        if (today - datetime.date.fromisoformat(date)).days > 3:
            print(f"[snapshot] target_date={date} 过于陈旧，跳过"); return 0
    except Exception:
        pass
    hist = load("estimates_history.json") or {"records": []}
    recs = [r for r in hist.get("records", []) if r.get("date") != date]
    rec = {
        "date": date,
        "est": round(est, 4),
        "p_final": round(float(res["P_final"]), 4) if res.get("P_final") is not None else None,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    recs.append(rec)
    recs = recs[-250:]
    hist["records"] = recs
    json.dump(hist, open(EST_HIST, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[snapshot] 已归档 {date} 估算 {est:+.2f}% （共 {len(recs)} 条）")
    return 0

if __name__ == "__main__":
    sys.exit(main())
