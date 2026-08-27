#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_p5.py —— P5「个股反推」的真实样本外误差（落实优化意见 §5 方案B）

问题背景
--------
主脚本里 P5 的 MAE 一直是借来的代理值：

    maes["P5_个股辅助"] = max(err_mae_p3, 0.40)      # 用 P3 的误差冒充 P5 的

这在旧的平缓加权下危害有限；但淘汰闸门(gate=1.15)上线后，P5 凭这个"假 MAE"
恰好挤进幸存名单，又因为自成一组(G4_个股反推)，一口气拿到 50% 权重 ——
用假误差换真权重，是整套系统里最不可辩护的一环。

本脚本逐日重做 LASSO/NNLS 反推，生成 P5 真正的样本外预测序列，算出真实 MAE，
写入 cache/p5_mae.json 供主脚本读取。

纪律
----
  * 第 t 日预测只用 [t-W, t) 的个股收益与净值拟合，当日只用行情、不用当日净值；
  * LassoCV 的 λ 也在窗口内选，不跨期偷看；
  * 与主脚本 fund_holdings_infer.py 完全同构：candidate 池、W=60、
    预测 = (LASSO 归一化权重 + NNLS 归一化权重)/2 点乘当日个股收益。

用法:
  python tests/backtest_p5.py             # 默认评估最近 80 个交易日
  python tests/backtest_p5.py --days 40   # 缩短（LassoCV 较慢时用）
  python tests/backtest_p5.py --no-lasso  # 只用 NNLS（快速粗测）
"""
import json, os, sys, time, argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
_ENC = getattr(sys.stdout, "encoding", None) or "ascii"


def out(s):
    try:
        print(s)
    except UnicodeEncodeError:
        print(s.encode(_ENC, "replace").decode(_ENC, "replace"))


import numpy as np
from scipy.optimize import nnls
import core

ap = argparse.ArgumentParser()
ap.add_argument("--days", type=int, default=80, help="样本外评估日数")
ap.add_argument("--no-lasso", action="store_true", help="跳过 LassoCV（快速模式）")
ap.add_argument("--window", type=int, default=60, help="拟合窗口，须与主脚本一致")
args = ap.parse_args()

CACHE = os.path.join(ROOT, "cache")
NAV = json.load(open(os.path.join(CACHE, "nav.json"), encoding="utf-8"))
KL, KL_META = core.load_settled_klines(CACHE, verbose=False)   # 只用正式收盘价
if KL_META["unsettled"]:
    out(f"[数据卫生] 剔除未结算日期 {KL_META['unsettled']}（{KL_META['stripped']} 条）")
try:
    DIST = json.load(open(os.path.join(CACHE, "distract.json"), encoding="utf-8"))
except Exception:
    DIST = {}

# ---------- 候选池：与 src/fund_holdings_infer.py 保持一致 ----------
OPTICAL = ["688498", "688048", "300502", "688313", "300620",
           "300548", "300570", "688025", "300394", "300308"]
PCB = ["002916", "002463", "300476"]
SEMIS = ["688981", "002371", "603501", "603986"]
MARKET = ["000852"]

closedict = {}
for g in ("optical", "pcb", "semis", "market"):
    for c, d in KL.get(g, {}).items():
        closedict[c] = d
for c, d in DIST.items():
    closedict[c] = d

UNIVERSE = [c for c in OPTICAL + PCB + SEMIS + MARKET + list(DIST.keys())
            if c in closedict]

dates, navs = NAV["dates"], NAV["navs"]
fund_ret = {dates[i]: navs[i] / navs[i - 1] - 1.0 for i in range(1, len(dates))}

stock_ret = {}
for c in UNIVERSE:
    cd = sorted(closedict[c].keys())
    r = {}
    for i in range(1, len(cd)):
        prev = closedict[c][cd[i - 1]]
        if prev:
            r[cd[i]] = closedict[c][cd[i]] / prev - 1.0
    stock_ret[c] = r

UNIVERSE = [c for c in UNIVERSE if stock_ret[c]]
common = sorted(set(fund_ret) & set().union(*[set(stock_ret[c]) for c in UNIVERSE]))
common = [d for d in common if all(d in stock_ret[c] for c in UNIVERSE)]

W = args.window
y_all = np.array([fund_ret[d] for d in common])
X_all = np.array([[stock_ret[c][d] for c in UNIVERSE] for d in common])

out("=" * 84)
out(f"P5 个股反推 · 真实样本外回测   候选股 {len(UNIVERSE)} 只   窗口 W={W}")
out(f"数据区间 {common[0]} ~ {common[-1]}（{len(common)} 日）")
out("=" * 84)
if len(common) < W + 5:
    out(f"[中止] 对齐后样本仅 {len(common)} 日，不足 W+5={W+5}")
    sys.exit(2)

use_lasso = not args.no_lasso
if use_lasso:
    from sklearn.linear_model import LassoCV, Lasso

start = max(W, len(common) - args.days)
evald = common[start:]
out(f"[1/2] 逐日重拟合 {len(evald)} 日"
    f"（{'LASSO+NNLS' if use_lasso else '仅 NNLS'}）...")

rows, t0 = [], time.time()
for k, d in enumerate(evald):
    p = common.index(d)
    Xw, yw = X_all[p - W:p], y_all[p - W:p]          # 严格只用 d 之前
    xd = X_all[p]                                    # 当日个股收益（行情，非净值）

    preds = []
    if use_lasso:
        try:
            lcv = LassoCV(cv=5, positive=True, max_iter=50000,
                          random_state=0, n_jobs=1).fit(Xw, yw)
            co = np.maximum(Lasso(alpha=lcv.alpha_, positive=True,
                                  max_iter=50000).fit(Xw, yw).coef_, 0)
            if co.sum() > 0:
                preds.append(float((co / co.sum()) @ xd))
        except Exception:
            pass
    try:
        nw, _ = nnls(Xw, yw)
        if nw.sum() > 0:
            preds.append(float((nw / nw.sum()) @ xd))
    except Exception:
        pass

    if not preds:
        continue
    rows.append({"date": d, "actual": float(y_all[p]) * 100.0,
                 "p5": float(np.mean(preds)) * 100.0})
    if (k + 1) % 20 == 0:
        out(f"      {k+1}/{len(evald)} 日  ({time.time()-t0:.0f}s)")

if not rows:
    out("[中止] 无有效 P5 预测")
    sys.exit(2)

out(f"      完成 {len(rows)} 日，耗时 {time.time()-t0:.0f}s")

# ---------- 指标 ----------
out("[2/2] 汇总 ...\n")
m_all = core.error_metrics([r["p5"] for r in rows], [r["actual"] for r in rows])


def win_mae(n):
    sub = rows[-n:]
    return float(np.mean([abs(r["p5"] - r["actual"]) for r in sub])) if sub else None


out("=" * 84)
out(f"{'区间':<12}{'样本':>6}{'MAE':>9}{'RMSE':>9}{'最大误差':>10}"
    f"{'方向准确':>10}{'平均偏差':>10}")
out("-" * 84)
out(f"{'全部':<12}{len(rows):>6}{m_all['mae']:>9.3f}{m_all['rmse']:>9.3f}"
    f"{m_all['max_abs']:>10.3f}{m_all['hit']*100:>9.1f}%{m_all['bias']:>+10.3f}")
for n in (40, 20, 10):
    if len(rows) >= n:
        sub = rows[-n:]
        m = core.error_metrics([r["p5"] for r in sub], [r["actual"] for r in sub])
        out(f"{'近'+str(n)+'日':<12}{n:>6}{m['mae']:>9.3f}{m['rmse']:>9.3f}"
            f"{m['max_abs']:>10.3f}{m['hit']*100:>9.1f}%{m['bias']:>+10.3f}")
out("-" * 84)

# 与主脚本此前使用的代理值对比 —— 这是本次回测要回答的核心问题
proxy_floor = 0.40
mae40 = win_mae(40) or m_all["mae"]
out(f"主脚本原代理值: max(MAE_P3, {proxy_floor}) —— 近期常取 ≈0.44%")
out(f"P5 真实近40日 MAE: {mae40:.3f}%")
verdict = ("真实误差明显大于代理值 —— 此前 P5 被严重高估，"
           "闸门下拿到 50% 权重是错误的" if mae40 > 0.50 else
           "真实误差与代理值接近 —— P5 参与集成可接受")
out(f"[判定] {verdict}")

rep = {
    "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    "window": W, "universe_size": len(UNIVERSE),
    "method": "LASSO(CV)+NNLS 均值" if use_lasso else "NNLS only",
    "n_samples": len(rows),
    "period": [rows[0]["date"], rows[-1]["date"]],
    "mae": m_all["mae"], "rmse": m_all["rmse"], "max_abs": m_all["max_abs"],
    "hit": m_all["hit"], "bias": m_all["bias"],
    "mae_40": round(mae40, 4),
    "mae_20": round(win_mae(20), 4) if len(rows) >= 20 else None,
    "mae_10": round(win_mae(10), 4) if len(rows) >= 10 else None,
    "is_real_oos": True,
    "verdict": verdict,
    "daily": [{"date": r["date"], "actual": round(r["actual"], 3),
               "p5": round(r["p5"], 3), "err": round(r["p5"] - r["actual"], 3)}
              for r in rows],
}
json.dump(rep, open(os.path.join(CACHE, "p5_mae.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
out(f"\n[已保存] cache/p5_mae.json —— 主脚本将改用其中的真实 MAE，不再用代理值")
out("=" * 84)
