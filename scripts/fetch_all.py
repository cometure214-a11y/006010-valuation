#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_all.py —— 拉取 006010 估值模型所需的全部历史数据到本地缓存。
输出：
  cache/nav.json      {dates:[...], navs:[...]}  按日期升序
  cache/klines.json   {code: {date: close}}       各成分股 + 市场指数日线收盘价(前复权)
不依赖任何第三方库，仅用标准库。
"""
import json, os, urllib.request, urllib.parse, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "cache")
os.makedirs(CACHE, exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://fundf10.eastmoney.com/"}

def get(url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers=headers or UA)
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            print("  retry", e); time.sleep(1)
    return ""

# ---------- 1. 分页拉取官方净值 (lsjz) ----------
def fetch_nav(code="006010", min_rows=130):
    rows = []
    page = 1
    while len(rows) < min_rows:
        url = (f"https://api.fund.eastmoney.com/f10/lsjz?fundCode={code}"
               f"&pageIndex={page}&pageSize=20")
        txt = get(url, {"User-Agent": "Mozilla/5.0",
                        "Referer": "https://fundf10.eastmoney.com/"})
        try:
            d = json.loads(txt)
            lst = d["Data"]["LSJZList"]
        except Exception:
            break
        if not lst:
            break
        rows.extend(lst)
        page += 1
        if page > 40:
            break
        time.sleep(0.2)
    # 按日期升序
    recs = [(x["FSRQ"], float(x["DWJZ"])) for x in rows if x.get("FSRQ") and x.get("DWJZ")]
    recs.sort(key=lambda t: t[0])
    out = {"dates": [r[0] for r in recs], "navs": [r[1] for r in recs]}
    json.dump(out, open(os.path.join(CACHE, "nav.json"), "w"), ensure_ascii=False)
    print(f"[nav] {len(recs)} 条, {out['dates'][0]} → {out['dates'][-1]}")
    return out

# ---------- 2. 拉取日线收盘价 (kline) ----------
def ts_symbol(code):
    """腾讯历史kline用的符号：sh/sz + 代码；指数同样用 sh/sz 前缀。"""
    if code.startswith(("60", "68", "9", "000", "399")):
        return "sh" + code
    return "sz" + code

def fetch_kline(code, beg="2026-01-01", end="2026-08-20"):
    sym = ts_symbol(code)
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={sym},day,{beg},{end},400,qfq")
    out = {}
    for attempt in range(4):
        txt = get(url, {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
        try:
            d = json.loads(txt)
            node = d["data"][sym]
            kl = node.get("qfqday") or node.get("day")
            for row in kl:
                out[row[0]] = float(row[2])  # row[2]=收盘价(前复权)
            break
        except Exception:
            time.sleep(1.0 * (attempt + 1))
    # 腾讯日K长窗口当天数据更新滞后 → 用实时行情接口补当天收盘价
    if end not in out:
        try:
            q = f"https://qt.gtimg.cn/q={sym}"
            raw = urllib.request.urlopen(urllib.request.Request(q, headers={
                "User-Agent": "Mozilla/5.0"}), timeout=15).read().decode("gbk", "ignore")
            m = __import__("re").search(r'v_(\w+)=\"([^\"]+)\"', raw)
            if m:
                p = m.group(2).split("~")
                cur = float(p[3])
                if cur > 0:
                    out[end] = cur
        except Exception:
            pass
    if not out:
        print(f"  kline fail {code}")
    return out

def fetch_all_klines():
    baskets = {
        # 光通信/光模块（=基金Q2前十大，作为光通信风格因子）
        "optical": ["688498","688048","300502","688313","300620",
                    "300548","300570","688025","300394","300308"],
        # PCB
        "pcb": ["002916","002463","300476"],
        # 半导体
        "semis": ["688981","002371","603501","603986"],
        # 市场（中证1000）
        "market": ["000852"],
    }
    result = {}
    for grp, codes in baskets.items():
        result[grp] = {}
        for c in codes:
            print(f"  kline {grp} {c} ...")
            result[grp][c] = fetch_kline(c)
            time.sleep(0.4)
    json.dump(result, open(os.path.join(CACHE, "klines.json"), "w"), ensure_ascii=False)
    tot = sum(len(v) for g in result.values() for v in g.values())
    print(f"[klines] 共 {tot} 条收盘价记录")

if __name__ == "__main__":
    print("=== 抓取 006010 估值数据 ===")
    fetch_nav()
    print("=== 抓取成分股日线 ===")
    fetch_all_klines()
    print("=== 完成，已写入 cache/ ===")
