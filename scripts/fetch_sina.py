#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# fetch_sina.py: 抓新浪基金盘中估值(sina_ds2口径), 存 cache/sina_estimate.json。
# 接口 getEstimateNetworthPic?symbol=006010&source=2
# 关键字段: worth(估算净值)/worth_date(YYYYMMDD)/worth_rate(小数涨跌幅,*100=百分比)
# 新浪口径=前十大重仓加权, 存在滞后性。仅作交叉验证源, 不参与主力模型。
import json, os, sys, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "cache")
CODE = "006010"
OUT = os.path.join(CACHE, "sina_estimate.json")
URL = ("https://stock.finance.sina.com.cn/fundInfo/api/openapi.php/"
       "FdFundService.getEstimateNetworthPic?symbol=%s&source=2" % CODE)
HDR = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}

def main():
    try:
        req = urllib.request.Request(URL, headers=HDR)
        with urllib.request.urlopen(req, timeout=25) as r:
            txt = r.read().decode("utf-8", "ignore")
        d = json.loads(txt)
        data = d.get("result", {}).get("data", {})
        worth = data.get("worth"); wdate = data.get("worth_date"); wrate = data.get("worth_rate")
    except Exception as e:
        print("[sina] 抓取失败: %s" % e)
        rec = {"ok": False, "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "err": str(e)}
        json.dump(rec, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return 1
    est = None
    if wrate is not None:
        try: est = round(float(wrate) * 100, 4)
        except Exception: est = None
    nav = None
    if worth is not None:
        try: nav = round(float(worth), 4)
        except Exception: nav = None
    rec = {"ok": est is not None, "symbol": CODE, "est_pct": est, "est_nav": nav,
           "worth_date": wdate, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    json.dump(rec, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("[sina] ok est=%s%% nav=%s date=%s" % (rec["est_pct"], rec["est_nav"], rec["worth_date"]))
    return 0

if __name__ == "__main__":
    sys.exit(main())
