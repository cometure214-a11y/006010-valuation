#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
daily_errors.py —— 每日估值误差日报（进化闭环的"燃料"）
用法: python scripts/daily_errors.py [SENDKEY]

功能:
  1. 读取 last_nav.json(官方净值) 与 result.json(模型估算)
  2. 计算单日误差 = 官方涨跌 - 模型估算涨跌
  3. 追加到 cache/error_history.json（按日期去重，保留最近 90 日）
  4. 计算滚动 MAE（近7日/近20日/全部）
  5. Server酱推送误差日报（微信）
仅标准库，跨平台。
"""
import json, os, sys, time, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "cache")

SCKEY = sys.argv[1] if len(sys.argv) > 1 else ""


def load(name):
    p = os.path.join(CACHE, name)
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def push(title, desp):
    data = urllib.parse.urlencode({"title": title, "desp": desp}).encode("utf-8")
    url = f"https://sctapi.ftqq.com/{SCKEY}.send"
    try:
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        resp = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
        print(f"[errors] 推送响应: {resp[:80]}")
        return resp.startswith('{"code":0')
    except Exception as e:
        print(f"[errors] 推送失败: {e}")
        return False


def main():
    last = load("last_nav.json")
    res = load("result.json")
    if not last or not res:
        print("[errors] 缺少 last_nav.json 或 result.json，跳过（首次运行等净值公布后）")
        return 0
    nav_date = last.get("date", "")
    nav_chg = last.get("chg")
    try:
        nav_chg = float(nav_chg)
    except (TypeError, ValueError):
        nav_chg = None
    est = res.get("P_final_corr")
    target = res.get("target_date", res.get("cur_date", ""))
    if nav_date and target and nav_date != target:
        print(f"[errors] 日期不匹配：last_nav={nav_date} vs 模型目标={target}，跳过（等净值公布后重跑）")
        return 0
    if nav_chg is None or est is None:
        print("[errors] 官方净值或模型估算缺失，跳过")
        return 0
    err = nav_chg - est

    # 入库（按日期去重，保留最近 90 日）
    hist = load("error_history.json") or {"records": []}
    rec = {
        "date": nav_date,
        "official": round(nav_chg, 4),
        "est": round(est, 4),
        "err": round(err, 4),
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    hist["records"] = [r for r in hist["records"] if r.get("date") != nav_date] + [rec]
    hist["records"] = hist["records"][-90:]
    json.dump(hist, open(os.path.join(CACHE, "error_history.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    recs = hist["records"]
    n_all = len(recs)
    n20 = min(20, n_all)
    n7 = min(7, n_all)
    mae20 = sum(abs(r["err"]) for r in recs[-n20:]) / n20 if n20 else 0
    mae7 = sum(abs(r["err"]) for r in recs[-n7:]) / n7 if n7 else 0
    mae_all = sum(abs(r["err"]) for r in recs) / n_all if n_all else 0
    bias20 = sum(r["err"] for r in recs[-n20:]) / n20 if n20 else 0

    print(f"[errors] {nav_date} 误差 {err:+.2f}pp | 滚动MAE20 {mae20:.3f}% | 样本 {n_all} 日")

    if not SCKEY:
        print("[errors] 未配置 SENDKEY，跳过推送")
        return 0

    trend = ""
    if n_all >= 7:
        mae_prev = sum(abs(r["err"]) for r in recs[-14:-7]) / 7 if n_all >= 14 else None
        if mae_prev:
            delta = (mae20 - mae_prev) * 100
            mark = "🔻 改善" if delta < -0.005 else ("🔺 变差" if delta > 0.005 else "➖ 持平")
            trend = f"近20日 vs 前14日：**{mark}**（{mae_prev:.2f}% → {mae20:.2f}%）"

    lines = [
        f"**📊 006010 估值误差日报（{nav_date}）**",
        "",
        f"官方涨跌：**{nav_chg:+.2f}%**",
        f"模型估算：**{est:+.2f}%**",
        f"**单日误差：{err:+.2f}pp**",
        "",
        f"滚动 MAE（近20日）：**{mae20:.2f}%**",
        f"近7日 MAE：**{mae7:.2f}%**",
        f"累计 MAE（{n_all}日）：**{mae_all:.2f}%**",
        f"近20日偏差：{bias20:+.2f}%（>0 模型偏保守）",
    ]
    if trend:
        lines.append("")
        lines.append(trend)
    lines.append("")
    lines.append("误差库：cache/error_history.json（自动积累，用于模型自进化）")
    desp = "\n\n".join(lines)
    ok = push("📈 006010 估值误差日报", desp)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
