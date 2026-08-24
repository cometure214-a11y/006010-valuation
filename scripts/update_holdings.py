#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_holdings.py —— 自动更新基金前十大持仓（季报检测）
用法: python scripts/update_holdings.py [fund_code=006010]

功能:
  1. 东财 jjcc 接口拉取最新披露季报前十大持仓（代码/名称/占净值比例）
  2. 与 cache/holdings.json 比对：内容变化才重写（季度切换自动生效，季度中不变）
  3. core.py 启动时读取 holdings.json 动态覆盖 TOP10（见 core.py 持仓动态化块）

输出: cache/holdings.json  {code, report_date, top10:[{code,name,weight}], ts}
仅标准库，跨平台。
"""
import json, os, re, sys, html, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "cache")
os.makedirs(CACHE, exist_ok=True)
CODE = sys.argv[1] if len(sys.argv) > 1 else "006010"


def get(url, referer, timeout=25):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                                   "Referer": referer})
        return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
    except Exception as e:
        print(f"[holdings] 抓取失败: {e}")
        return ""


def parse_holdings(txt):
    """解析东财 jjcc 表格（复用 fund_valuation.py 的字段映射：tds[1]=代码 tds[2]=名称 tds[6]=占比）"""
    m = re.search(r'content:"(.*?)"', txt, re.S)
    content = m.group(1) if m else txt
    rows = re.findall(r"<tr>(.*?)</tr>", content, re.S)
    out = []
    for r in rows:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)
        tds = [re.sub(r"<[^>]+>", "", html.unescape(t)).strip() for t in tds]
        if len(tds) < 7:
            continue
        code, name, w = tds[1], tds[2], tds[6].replace("%", "").strip()
        if not re.fullmatch(r"\d{6}", code) or not w:
            continue
        try:
            out.append({"code": code, "name": name, "weight": float(w)})
        except ValueError:
            continue
        if len(out) >= 10:
            break
    return out


def parse_report_date(txt):
    """尝试解析报告期（jjcc 返回的 JS 里通常有日期）"""
    m = re.search(r'jzrq\s*[:=]\s*"([\d-]+)"', txt)
    if m:
        return m.group(1)
    m = re.search(r'(\d{4}-\d{2}-\d{2})', txt)
    return m.group(1) if m else ""


def main():
    txt = get(f"https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jjcc&code={CODE}&topline=10",
              f"https://fundf10.eastmoney.com/{CODE}.html")
    if not txt:
        print("[holdings] 抓取为空，保留现状")
        return 0
    top10 = parse_holdings(txt)
    if len(top10) < 5:
        print(f"[holdings] 解析异常（仅 {len(top10)} 条），保留现状")
        return 0
    report_date = parse_report_date(txt)
    new = {"code": CODE,
           "report_date": report_date or time.strftime("%Y-%m-%d"),
           "top10": top10,
           "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    old = None
    p = os.path.join(CACHE, "holdings.json")
    if os.path.exists(p):
        try:
            old = json.load(open(p, encoding="utf-8"))
        except Exception:
            old = None
    if old and old.get("top10") == top10:
        print(f"[holdings] 持仓未变化（{len(top10)} 只，报告期 {report_date or '未知'}），跳过")
        return 0
    json.dump(new, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    names = "、".join(h["name"] for h in top10[:5])
    print(f"[holdings] ✅ 前十大已更新（报告期 {report_date or '未知'}）：{names} …")
    return 0


if __name__ == "__main__":
    sys.exit(main())
