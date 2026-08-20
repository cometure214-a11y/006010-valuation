#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_cloud.py —— 云端版数据抓取（GitHub Actions 用，动态日期 + 干扰股）

与本地 fetch_all.py 的区别：
  1. 日期动态：beg=今天往前推 260 天，end=今天（云端每次全新环境，无缓存）
  2. 并入干扰股抓取（distract.json），供 fund_holdings_infer.py 使用
输出（相对本文件目录）：
  cache/nav.json      {dates:[...], navs:[...]}  按日期升序
  cache/klines.json   {optical/pcb/semis/market: {code: {date: close}}}  **只存正式收盘价**
  cache/intraday.json {date, settled, ts, source, prices:{group:{code:px}}}  盘中实时快照
  cache/distract.json {code: {date: close}}       干扰股（白酒/银行/锂电/医药/家电/保险/电力）
仅标准库，跨平台（GitHub runner 为 Linux）。

【数据纪律 · 收盘价与实时价严格分离（优化意见 §6）】
  腾讯日 K 接口在**盘中也会返回当天一行**，其"收盘价"字段填的是当前价；
  加上原先"当天缺失→用实时接口补一条"的逻辑，会让盘中价以正式收盘价的身份
  进入 klines.json，进而污染滚动回归的 β/θ、误差统计与全部回测结果。
  现行规则：
    · 收盘结算前（北京时间 < 15:05）抓到的当天价格 → 一律进 intraday.json，
      并从 klines.json 中剔除，settled=false；
    · 收盘结算后抓取 → 当天价格视为正式收盘价，保留在 klines.json，
      intraday.json 记 settled=true（下游据此不再剔除该日）。
  时间基准统一用北京时区，避免云端 UTC runner 在北京 00:00-08:00 段算错日期。
"""
import json, os, urllib.request, time, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "cache")
os.makedirs(CACHE, exist_ok=True)

CST = datetime.timezone(datetime.timedelta(hours=8))
NOW_CST = datetime.datetime.now(CST)
TODAY = NOW_CST.date().isoformat()
BEG = (NOW_CST.date() - datetime.timedelta(days=260)).isoformat()
# 15:05 留 5 分钟缓冲，等交易所最终价落地
SETTLED = (NOW_CST.hour, NOW_CST.minute) >= (15, 5)
LIVE = {}          # {group: {code: px}} 盘中快照，单独落盘
LIVE_FLAT = {}     # 干扰股等无分组场景

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

def fetch_live(sym):
    """实时行情当前价（盘中价，不得当作收盘价）。"""
    try:
        q = f"https://qt.gtimg.cn/q={sym}"
        raw = urllib.request.urlopen(urllib.request.Request(q, headers={
            "User-Agent": "Mozilla/5.0"}), timeout=15).read().decode("gbk", "ignore")
        m = __import__("re").search(r'v_(\w+)=\"([^\"]+)\"', raw)
        if m:
            cur = float(m.group(2).split("~")[3])
            if cur > 0:
                return cur
    except Exception:
        pass
    return None

def fetch_kline(code, live_sink=None):
    """
    返回**只含正式收盘价**的 {date: close}。
    当天价格（无论来自日 K 当天行还是实时接口）在结算前一律移出，写入 live_sink。
    """
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

    today_px = out.pop(TODAY, None)      # 先无条件摘出，避免盘中价冒充收盘价
    if today_px is None:
        today_px = fetch_live(sym)       # 日 K 当天行滞后时才走实时接口
    if today_px:
        if SETTLED:
            out[TODAY] = today_px        # 已收盘结算：认定为正式收盘价
        if live_sink is not None:
            live_sink[code] = today_px   # 快照始终记录，供盘中估值与留痕
    if not out:
        print(f"  kline fail {code}")
    return out

def fetch_group(group, codes):
    out = {}
    sink = LIVE.setdefault(group, {})
    for c in codes:
        out[c] = fetch_kline(c, live_sink=sink)
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
    print(f"[klines] 共 {tot} 条正式收盘价"
          + (f"（含今日 {TODAY}，已收盘结算）" if SETTLED else f"（不含今日 {TODAY}）"))

def write_intraday():
    """盘中快照单独落盘，永不混入 klines.json。"""
    n = sum(len(v) for v in LIVE.values())
    snap = {
        "date": TODAY,
        "settled": SETTLED,
        "ts": NOW_CST.isoformat(timespec="seconds"),
        "source": "qt.gtimg.cn realtime (盘中当前价)" if not SETTLED else "收盘后抓取，等同正式收盘价",
        "n_codes": n,
        "prices": LIVE,
        "distract": LIVE_FLAT,
        "note": ("settled=false → 下游必须用 core.strip_unsettled() 把该日期从 "
                 "klines.json 中剔除，禁止参与滚动回归/误差统计/回测"),
    }
    json.dump(snap, open(os.path.join(CACHE, "intraday.json"), "w"), ensure_ascii=False)
    print(f"[intraday] {TODAY} settled={SETTLED} 快照 {n} 只 → cache/intraday.json")

# ---------- 3. 干扰股（infer 脚本需要） ----------
DISTRACT = {
    "600519": "贵州茅台(白酒)", "000858": "五粮液(白酒)", "601398": "工商银行(银行)",
    "300750": "宁德时代(锂电)", "600276": "恒瑞医药(医药)", "000333": "美的集团(家电)",
    "601318": "中国平安(保险)", "600900": "长江电力(电力)",
}

def fetch_distract():
    res = {}
    for c, n in DISTRACT.items():
        res[c] = fetch_kline(c, live_sink=LIVE_FLAT)
        time.sleep(0.5)
    json.dump(res, open(os.path.join(CACHE, "distract.json"), "w"), ensure_ascii=False)
    print(f"[distract] {len(res)} 只, {sum(len(v) for v in res.values())} 条")
    return res

if __name__ == "__main__":
    print(f"=== 云端抓取 006010 (beg={BEG}, end={TODAY}, "
          f"北京时间 {NOW_CST.strftime('%H:%M')}, 收盘结算={SETTLED}) ===")
    fetch_nav()
    fetch_all_klines()
    fetch_distract()
    write_intraday()
    print("=== 完成 ===")
