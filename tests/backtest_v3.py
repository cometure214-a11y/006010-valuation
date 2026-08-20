#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_v3.py —— 用 v3 多模型组合算法对最近 N 个交易日做逐日样本外回测

纪律（与 v3 一致，无未来泄漏）：
  - 预测 T 日：只用 T-1 及以前数据估计 θ/β/MAE 权重/偏差修正
  - 盘中行情用 T 日收盘因子收益（回测中即"当日已知行情"）
  - P5 个股反推无历史序列，回测不含 P5；权重在 P1~P4 间重新归一化

输出：逐日 各模型预测 vs 实际、偏差修正、最终预测、误差；统计 MAE/方向命中率
"""
import json, os, sys
import numpy as np
from scipy.optimize import minimize

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
NAV = json.load(open(os.path.join(CACHE, "nav.json"), encoding="utf-8"))
KL = json.load(open(os.path.join(CACHE, "klines.json"), encoding="utf-8"))

TOP10 = [("688498", 9.63), ("688048", 9.36), ("300502", 9.36), ("688313", 8.42),
         ("300620", 8.35), ("300548", 8.24), ("300570", 8.18), ("688025", 7.97),
         ("300394", 7.73), ("300308", 5.92)]
TOP10_W = {c: w / 100.0 for c, w in TOP10}

BASKETS = {
    "optical": ["688498", "688048", "300502", "688313", "300620",
                "300548", "300570", "688025", "300394", "300308"],
    "pcb":     ["002916", "002463", "300476"],
    "semis":   ["688981", "002371", "603501", "603986"],
    "market":  ["000852"],
}
W_BASE, HALF_LIFE, N_LAST = 60, 20, 3   # 回测最近3日

def basket_returns(grp):
    rets = {}
    for c in BASKETS[grp]:
        close = KL[grp][c]
        ds = sorted(close.keys())
        d = {}
        for i in range(1, len(ds)):
            d[ds[i]] = (close[ds[i]] / close[ds[i - 1]] - 1.0) * 100.0
        rets[c] = d
    alld = set()
    for c in BASKETS[grp]:
        alld |= set(rets[c].keys())
    out = {}
    for dt in sorted(alld):
        vals = [rets[c][dt] for c in BASKETS[grp] if dt in rets[c]]
        if vals:
            out[dt] = sum(vals) / len(vals)
    return out

BR = {g: basket_returns(g) for g in BASKETS}
dates, navs = NAV["dates"], NAV["navs"]
fund_ret = {dates[i]: (navs[i] / navs[i - 1] - 1.0) * 100.0 for i in range(1, len(dates))}
common = sorted(set(fund_ret) & set(BR["optical"]) & set(BR["pcb"]) &
                set(BR["semis"]) & set(BR["market"]))
COMMON_D = common[1:]  # 有每日收益的日期序列

Y = np.array([fund_ret[d] for d in COMMON_D])
X = np.array([[BR["optical"][d], BR["pcb"][d], BR["semis"][d], BR["market"][d]]
              for d in COMMON_D])

# Q2 组合日收益（小数权重）
def q2_returns(cd):
    out = []
    for i in range(1, len(cd)):
        d, prev = cd[i], cd[i - 1]
        s = 0.0
        for c, w in TOP10_W.items():
            if d in KL["optical"][c] and prev in KL["optical"][c]:
                s += w * (KL["optical"][c][d] / KL["optical"][c][prev] - 1.0)
        out.append(s * 100.0)
    return np.array(out)

Q2 = q2_returns(common)  # 对应 COMMON_D（与 Y 同序）

def half_life_weights(n, hl):
    return np.array([0.5 ** ((n - 1 - i) / hl) for i in range(n)])

def constrained_regression(y_win, x_win, w, prior, prev, lam1=0.01, lam2=0.03):
    def obj(b):
        r = y_win - x_win @ b
        return (np.sum(w * r**2)
                + lam1 * len(y_win) * np.sum((b - prev)**2)
                + lam2 * len(y_win) * np.sum((b - prior)**2))
    cons = [{"type": "ineq", "fun": lambda b: 1.0 - np.sum(b)}]
    bnds = [(0, 1)] * x_win.shape[1]
    res = minimize(obj, prior.copy(), method="SLSQP", bounds=bnds,
                   constraints=cons, options={"maxiter": 1000, "ftol": 1e-12})
    if not res.success:
        from scipy.optimize import nnls
        b, _ = nnls(x_win, y_win)
        return b / b.sum() if b.sum() > 1 else b
    return res.x

def fit_theta(y_win, q2_win, pcb_win, mkt_win, w, hi_pcb=0.35, hi_m=0.25):
    x2 = np.column_stack([pcb_win - q2_win, mkt_win - q2_win])
    target = y_win - q2_win
    prior = np.array([0.05, 0.05])

    def obj(th):
        r = target - x2 @ th
        return (np.sum(w * r**2) + 0.05 * len(y_win) * np.sum((th - prior)**2)
                + 0.10 * len(y_win) * np.sum(th**2))
    cons = [{"type": "ineq", "fun": lambda t: hi_pcb - t[0]},
            {"type": "ineq", "fun": lambda t: hi_m - t[1]}]
    res = minimize(obj, prior, method="SLSQP",
                   bounds=[(0, hi_pcb), (0, hi_m)], constraints=cons,
                   options={"maxiter": 1000, "ftol": 1e-12})
    return res.x if res.success else np.array([0.0, 0.0])

# ---------- 滚动生成各日期的模型预测与误差 ----------
n = len(COMMON_D)
Q2_PRIOR = np.array([0.83, 0.0, 0.0, 0.0])
p2_pred, p3_pred, p2_theta = {}, {}, {}
p3_beta = {}
for p in range(W_BASE, n):
    # P2 θ（截至 p-1）
    th = fit_theta(Y[p-W_BASE:p], Q2[p-W_BASE:p], X[p-W_BASE:p, 1],
                   X[p-W_BASE:p, 3], half_life_weights(W_BASE, HALF_LIFE))
    p2_theta[COMMON_D[p]] = th
    p2_pred[COMMON_D[p]] = (Q2[p] + th[0] * (X[p, 1] - Q2[p]) + th[1] * (X[p, 3] - Q2[p]))
    # P3 β（截至 p-1）
    prev = p3_beta[COMMON_D[p-1]] if p > W_BASE else Q2_PRIOR
    b = constrained_regression(Y[p-W_BASE:p], X[p-W_BASE:p],
                               half_life_weights(W_BASE, HALF_LIFE), Q2_PRIOR, prev)
    p3_beta[COMMON_D[p]] = b
    p3_pred[COMMON_D[p]] = float(X[p] @ b)

# P1 = Q2 组合当日收益；P4 = 行业β × 行业内Q2权重（光通信内部Q2相对权重）
opt_rel = {c: TOP10_W[c] for c, _ in TOP10}
opt_sum = sum(opt_rel.values())

def p4_for(b, d):
    # 光通信内部：Q2 相对权重的个股当日收益
    p4_opt = sum((opt_rel[c]/opt_sum) * (KL["optical"][c][d] / KL["optical"][c][d_prev(d)] - 1) * 100
                 for c in opt_rel) if opt_sum > 0 else X_d(d)[0]
    return b[0]*p4_opt + b[1]*X_d(d)[1] + b[2]*X_d(d)[2] + b[3]*X_d(d)[3]

d_prev_map = {COMMON_D[i]: common[i] for i in range(len(COMMON_D))}
def d_prev(d): return d_prev_map[d]
def X_d(d): return X[COMMON_D.index(d)]

# ---------- 逐日回测（最近 N_LAST 日） ----------
targets = COMMON_D[-N_LAST:]
rows = []
for t_i, d in enumerate(targets):
    p = COMMON_D.index(d)
    # 截至 d-1 的样本外误差（用于 MAE 权重与偏差修正）
    hist_dates = [x for x in COMMON_D[W_BASE:p]]
    e_p1 = [Y[COMMON_D.index(x)] - Q2[COMMON_D.index(x)] for x in hist_dates]
    e_p2 = [Y[COMMON_D.index(x)] - p2_pred[x] for x in hist_dates]
    e_p3 = [Y[COMMON_D.index(x)] - p3_pred[x] for x in hist_dates]
    K = min(40, len(e_p3))
    mae = {
        "P1": float(np.mean(np.abs(e_p1[-K:]))),
        "P2": float(np.mean(np.abs(e_p2[-K:]))),
        "P3": float(np.mean(np.abs(e_p3[-K:]))),
    }
    mae["P4"] = mae["P3"]
    med = float(np.median(e_p3[-K:]))
    # 各模型当日预测
    b = p3_beta[d]
    p1 = Q2[p]
    p2 = p2_pred[d]
    p3 = p3_pred[d]
    p4_opt = sum((opt_rel[c]/opt_sum) * (KL["optical"][c][d] / KL["optical"][c][d_prev(d)] - 1) * 100
                 for c in opt_rel) if opt_sum > 0 else X[p, 0]
    p4 = b[0]*p4_opt + b[1]*X[p,1] + b[2]*X[p,2] + b[3]*X[p,3]
    # 集成权重（1/(MAE+0.05%)，归一化）
    wts = {k: 1.0/(mae[k]+0.05) for k in mae}
    tot = sum(wts.values())
    wts = {k: min(v/tot, 0.70) for k, v in wts.items()}
    t2 = sum(wts.values())
    wts = {k: v/t2 for k, v in wts.items()}
    p_final = wts["P1"]*p1 + wts["P2"]*p2 + wts["P3"]*p3 + wts["P4"]*p4
    p_corr = p_final + med
    actual = Y[p]
    rows.append({
        "date": d, "actual": actual,
        "P1": p1, "P2": p2, "P3": p3, "P4": p4,
        "final": p_final, "corr": p_corr, "med": med,
        "mae": mae, "theta": p2_theta[d],
    })

# ---------- 输出 ----------
print("=" * 78)
print("v3 多模型算法 · 最近 3 个交易日样本外回测（预测只用 T-1 及以前数据）")
print("=" * 78)
hdr = f"{'日期':<12}{'实际':>8}{'P1_Q2':>8}{'P2替代':>8}{'P3行业':>8}{'P4层级':>8}{'集成':>8}{'偏差修正':>10}{'最终':>8}"
print(hdr)
print("-" * 78)
errs_final, errs_raw = [], []
for r in rows:
    print(f"{r['date']:<12}{r['actual']:>+8.2f}{r['P1']:>+8.2f}{r['P2']:>+8.2f}"
          f"{r['P3']:>+8.2f}{r['P4']:>+8.2f}{r['final']:>+8.2f}"
          f"{r['med']:>+10.2f}{r['corr']:>+8.2f}")
    errs_raw.append(r["corr"] - r["actual"] if False else r["final"] - r["actual"])
    errs_final.append(r["corr"] - r["actual"])
print("-" * 78)
# 误差统计
import statistics
mae_f = statistics.mean(abs(e) for e in errs_final)
mae_raw = statistics.mean(abs(e) for e in errs_raw)
hit = sum(1 for r in rows if (r["corr"] > 0) == (r["actual"] > 0))
print(f"最近{len(rows)}日  集成后MAE={mae_raw:.2f}% | 偏差修正后MAE={mae_f:.2f}% | "
      f"方向命中 {hit}/{len(rows)}")
print(f"各日 θ_pcb: " + "  ".join(f"{r['date'][5:]}: {r['theta'][0]*100:.1f}%" for r in rows))
print("=" * 78)
