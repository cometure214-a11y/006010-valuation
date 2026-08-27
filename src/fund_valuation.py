#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基金盘中实时估值原型 (Fund Intraday NAV Estimator)
=================================================
核心算法: 归一化加权平均法 (Normalized Weighted Average)
  - 拉取基金最新季报「前十大重仓股」及占净值比例 w_i
  - 拉取每只重仓股实时行情, 计算盘中收益率 r_i = (现价 - 昨收) / 昨收
  - 朴素披露权重法:   est_chg = Σ (w_i/100 * r_i)            # 残差假设为 0
  - 归一化法(假设残差同重仓): est_chg = Σ(w_i*r_i) / Σ(w_i)
  - 估算净值 = 上一交易日单位净值 * (1 + est_chg)

数据接口(均已在 2026-08-20 沙箱实测可达):
  - 前十大持仓: 东方财富 F10 jjcc
  - 上一交易日净值: 东方财富 api/f10/lsjz
  - 个股实时行情: 腾讯 qt.gtimg.cn (GBK 编码)

用法:
  python fund_valuation.py 006010
  python fund_valuation.py 006010 --quiet
"""
import sys, re, html, json, argparse
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
TIMEOUT = 15

def _get(url, referer=None, encoding="utf-8"):
    headers = {"User-Agent": UA}
    if referer:
        headers["Referer"] = referer
    req = Request(url, headers=headers)
    raw = urlopen(req, timeout=TIMEOUT).read()
    if encoding == "gbk":
        return raw.decode("gbk", "ignore")
    return raw.decode("utf-8", "ignore")

def _sh_sz(code: str) -> str:
    """6位代码 -> 交易所前缀 (腾讯/新浪格式)"""
    if code.startswith("6") or code.startswith("5"):
        return "sh" + code
    if code.startswith(("0", "3", "2")):
        return "sz" + code
    if code.startswith(("8", "4")):
        return "bj" + code
    return "sh" + code

# ---------- 1. 持仓 ----------
def fetch_holdings(fund_code: str):
    url = (f"https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
           f"?type=jjcc&code={fund_code}&topline=10")
    txt = _get(url, referer=f"https://fundf10.eastmoney.com/{fund_code}.html")
    m = re.search(r'content:"(.*?)"', txt, re.S)
    content = m.group(1) if m else txt
    rows = re.findall(r"<tr>(.*?)</tr>", content, re.S)
    out = []
    for r in rows:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)
        tds = [re.sub(r"<[^>]+>", "", html.unescape(t)).strip() for t in tds]
        if len(tds) < 7:
            continue
        code = tds[1]
        name = tds[2]
        w = tds[6].replace("%", "").strip()
        if not re.fullmatch(r"\d{6}", code) or not w:
            continue
        try:
            out.append({"code": code, "name": name, "weight": float(w)})
        except ValueError:
            continue
        if len(out) >= 10:
            break
    return out

# ---------- 2. 上一交易日净值 ----------
def fetch_prev_nav(fund_code: str):
    url = (f"https://api.fund.eastmoney.com/f10/lsjz"
           f"?fundCode={fund_code}&pageIndex=1&pageSize=1")
    txt = _get(url, referer="https://fundf10.eastmoney.com/")
    data = json.loads(txt)["Data"]["LSJZList"][0]
    return float(data["DWJZ"]), data["FSRQ"]

# ---------- 3. 个股实时行情 ----------
def fetch_realtime(sec_code: str):
    raw = _get(f"https://qt.gtimg.cn/q={sec_code}", encoding="gbk")
    m = re.search(r'="([^"]+)"', raw)
    if not m:
        return None
    p = m.group(1).split("~")
    # p[1]=名称 p[3]=当前价 p[4]=昨收
    cur = float(p[3]); prev = float(p[4])
    ret = (cur - prev) / prev if prev else 0.0
    ts = ""
    for f in p:
        if re.fullmatch(r"\d{14}", f):
            ts = f; break
    return {"name": p[1], "price": cur, "prev_close": prev, "ret": ret, "ts": ts}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fund_code", nargs="?", default="006010")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if not args.quiet:
        print(f"[*] 拉取 {args.fund_code} 前十大持仓 ...")
    holdings = fetch_holdings(args.fund_code)
    if not holdings:
        print("[!] 未能解析到前十大持仓, 退出。"); sys.exit(1)

    prev_nav, nav_date = fetch_prev_nav(args.fund_code)
    if not args.quiet:
        print(f"[*] 上一交易日单位净值 {nav_date} = {prev_nav}")

    secs = [_sh_sz(h["code"]) for h in holdings]
    with ThreadPoolExecutor(max_workers=10) as ex:
        rts = list(ex.map(fetch_realtime, secs))

    total_w = sum(h["weight"] for h in holdings)
    naive_chg = sum(h["weight"] / 100.0 * (rt["ret"] if rt else 0.0) for h, rt in zip(holdings, rts))
    norm_chg = sum(h["weight"] * (rt["ret"] if rt else 0.0) for h, rt in zip(holdings, rts)) / total_w if total_w else 0.0

    # 覆盖率 / 置信度(70%覆盖率 + 30%时效, 此处季报时效给固定 0.6 基准)
    coverage = total_w
    confidence = round(0.7 * min(coverage / 100.0, 1.0) + 0.3 * 0.6, 3)

    print("\n=== 重仓股实时表现 ===")
    print(f"{'代码':<8}{'名称':<10}{'占净值%':>8}{'现价':>12}{'昨收':>12}{'盘中%':>9}")
    for h, rt in zip(holdings, rts):
        if rt:
            print(f"{h['code']:<8}{h['name']:<10}{h['weight']:>8.2f}{rt['price']:>12.2f}{rt['prev_close']:>12.2f}{rt['ret']*100:>8.2f}%")
        else:
            print(f"{h['code']:<8}{h['name']:<10}{h['weight']:>8.2f}{'N/A':>12}{'N/A':>12}{'N/A':>9}")

    est_naive = prev_nav * (1 + naive_chg)
    est_norm = prev_nav * (1 + norm_chg)
    print("\n=== 估值结果 ===")
    print(f"前十大合计占净值 : {coverage:.2f}%  (残差 {100-coverage:.2f}% 假设为 0)")
    print(f"朴素披露权重法   : 盘中变动 {naive_chg*100:+.2f}%  -> 估算净值 {est_naive:.4f}")
    print(f"归一化法(残差同仓): 盘中变动 {norm_chg*100:+.2f}%  -> 估算净值 {est_norm:.4f}")
    print(f"置信度评分       : {confidence}  (覆盖率权重70% + 时效权重30%)")
    if rts and rts[0] and rts[0]["ts"]:
        print(f"行情时间戳       : {rts[0]['ts']}  (腾讯行情)")

if __name__ == "__main__":
    main()
