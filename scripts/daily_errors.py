#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# daily_errors.py v2: 每日误差记录(幂等/可补录)。estimates_history.json + nav.json 反查。
import json, os, sys, time, urllib.request, urllib.parse
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "cache")
SCKEY = sys.argv[1] if len(sys.argv) > 1 else ""

def load(name):
    try:
        return json.load(open(os.path.join(CACHE, name), encoding="utf-8"))
    except Exception:
        return None

def nav_chg_map():
    nav = load("nav.json")
    if not nav: return {}
    dates = nav.get("dates") or []
    vals = nav.get("navs") or nav.get("nav") or nav.get("values") or nav.get("nav_list") or []
    if not dates or not vals or len(dates) != len(vals): return {}
    m = {}
    for i, d in enumerate(dates):
        if i == 0: continue
        try:
            p = float(vals[i-1]); c = float(vals[i])
            if p > 0: m[d] = (c/p - 1)*100
        except Exception: pass
    return m

def load_baseline_est(nd):
    try:
        h = load("holdings.json")
        if not h or not h.get("top10"): return None, None
        t10 = [(t["code"], float(t["weight"])) for t in h["top10"][:10]]
        kl = load("klines.json")
        if not kl: return None, None
        ser = {}
        for b in ("optical","pcb","semis"):
            for cd, s in kl.get(b, {}).items(): ser[cd] = s
        num, ws = 0.0, 0.0
        for cd, w in t10:
            s = ser.get(cd)
            if not s or nd not in s: continue
            ds = sorted(s.keys()); pv = None
            for d in ds:
                if d >= nd: break
                pv = d
            if pv is None: continue
            num += w*(s[nd]/s[pv]-1)*100; ws += w
        if ws < 50: return None, None
        return num/ws, ws
    except Exception:
        return None, None

def push(title, desp):
    if not SCKEY: return False
    data = urllib.parse.urlencode({"title": title, "desp": desp}).encode("utf-8")
    url = "https://sctapi.ftqq.com/" + SCKEY + ".send"
    try:
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        r = urllib.request.urlopen(req, timeout=15).read().decode("utf-8","ignore")
        print("[errors] 推送响应: " + r[:80])
        return r.startswith('{"code":0')
    except Exception as e:
        print("[errors] 推送失败: " + str(e)); return False

def main():
    est_hist = load("estimates_history.json")
    if not est_hist or not est_hist.get("records"):
        print("[errors] 无 estimates_history.json（先跑 snapshot_estimate.py），跳过"); return 0
    nav_map = nav_chg_map()
    if not nav_map:
        print("[errors] 无法从 nav.json 构建官方涨跌映射，跳过"); return 0
    emap = {r["date"]: r for r in est_hist["records"] if r.get("date")}
    hist = load("error_history.json") or {"records": []}
    seen = {r.get("date") for r in hist.get("records", [])}
    new_recs = []
    for date, rec in emap.items():
        if date in seen: continue
        if date not in nav_map: continue
        off = nav_map[date]; est = rec.get("est")
        if est is None: continue
        err = off - est
        base_est, base_wsum = load_baseline_est(date)
        new_recs.append({
            "date": date, "official": round(off, 4), "est": round(est, 4),
            "err": round(err, 4), "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "base": round(base_est, 4) if base_est is not None else None,
            "base_err": round(off - base_est, 4) if base_est is not None else None,
        })
    merged = {r["date"]: r for r in hist.get("records", [])}
    for r in new_recs: merged[r["date"]] = r
    recs = sorted(merged.values(), key=lambda x: x["date"])[-180:]
    json.dump({"records": recs}, open(os.path.join(CACHE, "error_history.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    n_all = len(recs); n20 = min(20, n_all); n7 = min(7, n_all)
    mae20 = sum(abs(r["err"]) for r in recs[-n20:])/n20 if n20 else 0
    mae7 = sum(abs(r["err"]) for r in recs[-n7:])/n7 if n7 else 0
    mae_all = sum(abs(r["err"]) for r in recs)/n_all if n_all else 0
    bias20 = sum(r["err"] for r in recs[-n20:])/n20 if n20 else 0
    print(f"[errors] 新增 {len(new_recs)} 条；累计 {n_all} 日 | MAE20 {mae20:.3f}% | MAE7 {mae7:.3f}%")
    for r in new_recs:
        print(f"  {r['date']} 官方 {r['official']:+.2f}% 估算 {r['est']:+.2f}% 误差 {r['err']:+.2f}pp")
    if not SCKEY or not new_recs:
        return 0
    lines = ["**📊 006010 估值误差日报**", "",
             f"本次新增 **{len(new_recs)}** 条（累计 {n_all} 日样本）"]
    for r in new_recs[-10:]:
        mark = "高估" if r["err"] > 0 else ("低估" if r["err"] < 0 else "吻合")
        extra = ""
        if r.get("base_err") is not None:
            win = "集成更准" if abs(r["err"]) < abs(r["base_err"]) else "基线更准"
            extra = f" | 基线 {r['base_err']:+.2f}pp({win})"
        lines.append(f"  {r['date']} 官方 {r['official']:+.2f}% / 估算 {r['est']:+.2f}% -> {r['err']:+.2f}pp（{mark}）{extra}")
    lines += ["",
              f"滚动 MAE（近20日）：**{mae20:.2f}%**",
              f"近7日 MAE：**{mae7:.2f}%**",
              f"累计 MAE（{n_all}日）：**{mae_all:.2f}%**",
              f"近20日偏差：{bias20:+.2f}%（>0 模型偏保守）", "",
              "误差库：cache/error_history.json（每日自动累计，用于模型自进化）"]
    push("📈 006010 估值误差日报", "\n\n".join(lines))
    return 0

if __name__ == "__main__":
    sys.exit(main())
