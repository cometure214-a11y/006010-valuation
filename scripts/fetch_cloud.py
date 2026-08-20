#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_cloud.py —— 云端版数据抓取（GitHub Actions 用，动态日期 + 干扰股）

与本地 fetch_all.py 的区别：
  1. 日期动态：beg=今天往前推 260 天，end=今天（云端每次全新环境，无缓存）
  2. 并入干扰股抓取（distract.json），供 fund_holdings_infer.py 使用
输出（相对本文件目录）：
  cache/nav.json      {dates:[...], navs:[...]}  按日期升序
  cache/klines.json   {optical/pcb/semis/market: {code: {date: close}}}
  cache/distract.json {code: {date: close}}       干扰股（白酒/银行/锂电/医药/家电/保险/电力）
仅标准库，跨平台（GitHub runner 为 Linux）。
"""
import json, os, urllib.request, time, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "cache")
os.makedirs(CACHE, exist_ok=True)

TODAY = datetime.date.today().isoformat()
BEG = (datetime.date.today() - datetime.timedelta(days=260)).isoformat()

def get(url, headers, timeout=25, retries=4):
    last = None
    for a in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            last = e
            time.sleep(1.2 * (a + 1))
    print(f"  [WARN] fetch fail: {last}")
    return ""

# ---------- 1. 官方净值（分页） ----------
def fetch_nav(code="006010", min_rows=200):
    """抓取净值历史。min_rows=200 → 约 10 页 ≈ 8 个月，足够 W=60 滚动回归 + 短期窗口。"""
    rows = []
    page = 1
    while len(rows) < min_rows:
        url = (f"https://api.fund.eastmoney.com/f10/lsjz?fundCode={code}"
               f"&pageIndex={page}&pageSize=20")
        txt = get(url, {"User-Agent": "Mozilla/5.0",
                        "Referer": "https://fundf10.eastmoney.com/"})
        try:
            lst = json.loads(txt)["Data"]["LSJZList"]
        except Exception:
            break
        if not lst:
            break
        rows.extend(lst)
        page += 1
        if page > 30:
            break
        time.sleep(0.2)
    recs = [(x["FSRQ"], float(x["DWJZ"])) for x in rows if x.get("FSRQ") and x.get("DWJZ")]
    recs.sort(key=lambda t: t[0])
    out = {"dates": [r[0] for r in recs], "navs": [r[1] for r in recs]}
    json.dump(out, open(os.path.join(CACHE, "nav.json"), "w"), ensure_ascii=False)
    print(f"[nav] {len(recs)} 条, {out['dates'][0]} → {out['dates'][-1]}")
    return out

# ---------- 2. 日线收盘价（腾讯，动态日期） ----------
def ts_symbol(code):
    if code.startswith(("60", "68", "9", "000", "399")):
        return "sh" + code
    return "sz" + code

def fetch_kline(code):
    sym = ts_symbol(code)
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={sym},day,{BEG},{TODAY},400,qfq")
    out = {}
    for attempt in range(5):
        txt = get(url, {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
        try:
            d = json.loads(txt)
            node = d["data"][sym]
            kl = node.get("qfqday") or node.get("day")
            out = {row[0]: float(row[2]) for row in kl}
            break
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    # 腾讯日K长窗口当天数据更新滞后 → 用实时行情接口补当天收盘价
    if TODAY not in out:
        try:
            q = f"https://qt.gtimg.cn/q={sym}"
            raw = urllib.request.urlopen(urllib.request.Request(q, headers={
                "User-Agent": "Mozilla/5.0"}), timeout=15).read().decode("gbk", "ignore")
            m = __import__("re").search(r'v_(\w+)=\"([^\"]+)\"', raw)
            if m:
                p = m.group(2).split("~")
                cur = float(p[3])
                if cur > 0:
                    out[TODAY] = cur
                    print(f"    [补] {sym} 当天收盘价 {cur} ({TODAY})")
        except Exception:
            pass
    if not out:
        print(f"  kline fail {code}")
    return out

def fetch_group(group, codes):
    out = {}
    for c in codes:
        out[c] = fetch_kline(c)
        time.sleep(0.4)
        print(f"  {group} {c}: {len(out[c])} 日")
    return out

def fetch_all_klines():
    baskets = {
        "optical": ["688498","688048","300502","688313","300620",
                    "300548","300570","688025","300394","300308"],
        "pcb":     ["002916","002463","300476"],
        "semis":   ["688981","002371","603501","603986"],
        "market":  ["000852"],
    }
    result = {}
    for grp, codes in baskets.items():
        result[grp] = fetch_group(grp, codes)
    json.dump(result, open(os.path.join(CACHE, "klines.json"), "w"), ensure_ascii=False)
    tot = sum(len(v) for g in result.values() for v in g.values())
    print(f"[klines] 共 {tot} 条")

# ---------- 3. 干扰股（infer 脚本需要） ----------
DISTRACT = {
    "600519": "贵州茅台(白酒)", "000858": "五粮液(白酒)", "601398": "工商银行(银行)",
    "300750": "宁德时代(锂电)", "600276": "恒瑞医药(医药)", "000333": "美的集团(家电)",
    "601318": "中国平安(保险)", "600900": "长江电力(电力)",
}

def fetch_distract():
    res = {}
    for c, n in DISTRACT.items():
        res[c] = fetch_kline(c)
        time.sleep(0.5)
    json.dump(res, open(os.path.join(CACHE, "distract.json"), "w"), ensure_ascii=False)
    print(f"[distract] {len(res)} 只, {sum(len(v) for v in res.values())} 条")
    return res

if __name__ == "__main__":
    print(f"=== 云端抓取 006010 (beg={BEG}, end={TODAY}) ===")
    fetch_nav()
    fetch_all_klines()
    fetch_distract()
    print("=== 完成 ===")
