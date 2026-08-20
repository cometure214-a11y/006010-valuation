#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fund_valuation_v3.py —— 006010 盘中估值 v3（按专家修改意见重构）

总原则（v3 定稿）：
  目标不是"精确还原第三季度真实个股持仓"，而是"尽可能降低盘中估算与收盘官方净值的误差"。
  β/θ 一律解释为"模型有效暴露"，不等同于真实持仓比例。

模型组合（每天生成，按样本外误差加权）：
  P1  Q2 静态基准         : r_q2（Q2披露权重 × 今日个股收益）
  P2  调仓替代比例模型     : y = r_q2 + θ_pcb×(r_pcb−r_q2) + θ_m×(r_m−r_q2)，θ 滚动约束回归
  P3  行业因子模型         : β1×光通信 + β2×PCB + β3×半导体 + β4×市场（滚动约束回归）
  P4  层级组合             : 行业总比例动态(P3的β) × 行业内Q2披露相对权重
  P5  个股辅助反推         : 读 cache/infer.json（LASSO/NNLS 加权），仅作辅助特征

输出：
  P_final = Σ w_i × P_i（w_i ∝ 1/(MAE_i+0.05%)，单模型 ≤70%）
  历史偏差修正：r_final += median(e_recent)，e = 实际 − 模型
  经验预测区间：中心 ± 1.5σ(样本外误差)
  PCB 三级信号（弱/中/强）+ 有效替代比例 θ_pcb
  置信度三维：样本外MAE / 模型分歧 / 调仓比例稳定性

数据纪律：仅用 cur_date 及以前数据；当天官方净值绝不参与当天参数估计。
统一 UTF-8 输出：避免 cmd(GBK) 下 ²/⚠/✅ 等字符崩溃。
"""
import json, os, sys, urllib.request, time
import numpy as np
from scipy.optimize import minimize

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # 项目根
CACHE = os.path.join(ROOT, "cache")

NAV = json.load(open(os.path.join(CACHE, "nav.json"), encoding="utf-8"))
KL = json.load(open(os.path.join(CACHE, "klines.json"), encoding="utf-8"))

# Q2(2026-06-30) 前十大（占净值%），均为光通信/光模块
TOP10 = [("688498", 9.63), ("688048", 9.36), ("300502", 9.36), ("688313", 8.42),
         ("300620", 8.35), ("300548", 8.24), ("300570", 8.18), ("688025", 7.97),
         ("300394", 7.73), ("300308", 5.92)]
TOP10_W = {c: w / 100.0 for c, w in TOP10}
SUM_W = sum(TOP10_W.values())  # 0.8316

BASKETS = {
    "optical": ["688498", "688048", "300502", "688313", "300620",
                "300548", "300570", "688025", "300394", "300308"],
    "pcb":     ["002916", "002463", "300476"],
    "semis":   ["688981", "002371", "603501", "603986"],
    "market":  ["000852"],
}
NAME = {"optical": "光通信", "pcb": "PCB", "semis": "半导体", "market": "市场(中证1000)"}

# ---------- 统一参数（意见§四）：W 60/20/10、半衰期 20 ----------
W_BASE = 60      # 基础滚动窗口
W_SHORT = 20     # 短期窗口
W_FAST = 10      # 快速窗口
HALF_LIFE = 20   # 时间半衰期（交易日）

# ---------- 1. 篮子日收益（等权，百分比单位） ----------
def basket_returns(grp):
    codes = BASKETS[grp]
    rets = {}
    for c in codes:
        close = KL[grp][c]
        ds = sorted(close.keys())
        d = {}
        for i in range(1, len(ds)):
            d[ds[i]] = (close[ds[i]] / close[ds[i - 1]] - 1.0) * 100.0
        rets[c] = d
    alld = set()
    for c in codes:
        alld |= set(rets[c].keys())
    out = {}
    for dt in sorted(alld):
        vals = [rets[c][dt] for c in codes if dt in rets[c]]
        if vals:
            out[dt] = sum(vals) / len(vals)
    return out

BR = {g: basket_returns(g) for g in BASKETS}

dates = NAV["dates"]
navs = NAV["navs"]
fund_ret = {dates[i]: (navs[i] / navs[i - 1] - 1.0) * 100.0 for i in range(1, len(dates))}

common = sorted(set(fund_ret) & set(BR["optical"]) & set(BR["pcb"]) &
                set(BR["semis"]) & set(BR["market"]))
Y = np.array([fund_ret[d] for d in common])
X = np.array([[BR["optical"][d], BR["pcb"][d], BR["semis"][d], BR["market"][d]]
              for d in common])

# Q2 组合日收益：用 Q2 披露权重(小数) × 对应股票日收益（光通信 10 只）
def q2_returns(common_dates):
    out = []
    for i in range(1, len(common_dates)):
        d, prev = common_dates[i], common_dates[i - 1]
        s = 0.0
        for c, w in TOP10_W.items():   # TOP10_W 为小数权重（0.0963），避免放大100倍
            if d in KL["optical"][c] and prev in KL["optical"][c]:
                s += w * (KL["optical"][c][d] / KL["optical"][c][prev] - 1.0)
        out.append(s * 100.0)
    return np.array(out)

Q2_Y = q2_returns(common)  # len = len(common)-1，与 fund_ret 的每日收益对齐（从第2天起）
# 对齐：fund_ret 从 common[1] 起有值，X 对应同样
Yd = np.array([fund_ret[d] for d in common[1:]])
Xd = X[1:]
Q2d = Q2_Y
PCBr = np.array([BR["pcb"][d] for d in common[1:]])
MKTd = np.array([BR["market"][d] for d in common[1:]])
COMMON_D = common[1:]  # 用于回归的日期序列（每日收益的"当天"）

print(f"[对齐] 回归样本 {len(COMMON_D)} 日, {COMMON_D[0]} → {COMMON_D[-1]}")
_corr = np.corrcoef(X.T)
print(f"[因子相关性] 光通信~PCB r={_corr[0,1]:.2f} | 光通信~半导体 r={_corr[0,2]:.2f} | 光通信~市场 r={_corr[0,3]:.2f}")

# ---------- 2. 通用工具：半衰期加权 + 约束回归 ----------
def half_life_weights(n, half_life):
    return np.array([0.5 ** ((n - 1 - i) / half_life) for i in range(n)])

def constrained_regression(y_win, x_win, weights, prior=None, prev_beta=None,
                           bnd_hi=1.0, lam1=0.01, lam2=0.03):
    """带约束 WLS: β≥0, Σβ≤1，含平滑(lam1)与先验(lam2)正则"""
    n_factors = x_win.shape[1]
    prior = np.zeros(n_factors) if prior is None else prior
    prev_beta = prior.copy() if prev_beta is None else prev_beta

    def obj(b):
        r = y_win - x_win @ b
        return (np.sum(weights * r**2)
                + lam1 * len(y_win) * np.sum((b - prev_beta)**2)
                + lam2 * len(y_win) * np.sum((b - prior)**2))

    cons = [{"type": "ineq", "fun": lambda b: 1.0 - np.sum(b)}]
    bnds = [(0, bnd_hi)] * n_factors
    x0 = prior.copy()
    res = minimize(obj, x0, method="SLSQP", bounds=bnds, constraints=cons,
                   options={"maxiter": 1000, "ftol": 1e-12})
    if not res.success:
        from scipy.optimize import nnls
        b, _ = nnls(x_win, y_win)
        if b.sum() > 1:
            b = b / b.sum()
        return b
    return res.x

# ---------- 3. P1: Q2 静态基准 ----------
# 用当日实时行情算（见后）。此处先准备 Q2 组合在回归样本上的拟合（用于误差）

# ---------- 4. P2: 调仓替代比例模型（意见§二） ----------
# y_t = r_q2,t + θ_pcb×(r_pcb,t − r_q2,t) + θ_m×(r_m,t − r_q2,t) + ε
# 构造 X2 = [r_pcb−r_q2, r_m−r_q2]，θ = [θ_pcb, θ_m]，约束 0≤θ≤上限(回测选)
def fit_theta(y_win, q2_win, pcb_win, mkt_win, w, hi_pcb=0.35, hi_m=0.25):
    x2 = np.column_stack([pcb_win - q2_win, mkt_win - q2_win])
    target = y_win - q2_win
    prior = np.array([0.05, 0.05])
    prev = prior.copy()

    def obj(th):
        r = target - x2 @ th
        return np.sum(w * r**2) + 0.05 * len(y_win) * np.sum((th - prev)**2) \
               + 0.10 * len(y_win) * np.sum(th**2)  # λ3 收缩：无证据时不过度加PCB

    cons = [{"type": "ineq", "fun": lambda t: hi_pcb - t[0]},
            {"type": "ineq", "fun": lambda t: hi_m - t[1]}]
    bnds = [(0, hi_pcb), (0, hi_m)]
    res = minimize(obj, prior, method="SLSQP", bounds=bnds, constraints=cons,
                   options={"maxiter": 1000, "ftol": 1e-12})
    if not res.success:
        return np.array([0.0, 0.0])
    return res.x

# 滚动估计 θ（样本外预测）
theta_pcb_hist, theta_m_hist = {}, {}
p2_pred = {}
for p in range(W_BASE, len(COMMON_D)):
    y_win = Yd[p - W_BASE:p]
    q2_win = Q2d[p - W_BASE:p]
    pcb_win = PCBr[p - W_BASE:p]
    mkt_win = MKTd[p - W_BASE:p]
    w = half_life_weights(W_BASE, HALF_LIFE)
    th = fit_theta(y_win, q2_win, pcb_win, mkt_win, w)
    theta_pcb_hist[COMMON_D[p]] = th[0]
    theta_m_hist[COMMON_D[p]] = th[1]
    # 样本外预测：P2 = r_q2 + θ_pcb×(r_pcb−r_q2) + θ_m×(r_m−r_q2)
    p2_pred[COMMON_D[p]] = Q2d[p] + th[0] * (PCBr[p] - Q2d[p]) + th[1] * (MKTd[p] - Q2d[p])

theta_pcb_now = theta_pcb_hist[COMMON_D[-1]]
theta_m_now = theta_m_hist[COMMON_D[-1]]
print(f"[P2 调仓替代] θ_pcb={theta_pcb_now:.3f} θ_m={theta_m_now:.3f} "
      f"(截至 {COMMON_D[-1]})")

# ---------- 5. P3: 行业因子模型（滚动约束回归，W=60） ----------
p3_beta_hist, p3_pred = {}, {}
Q2_PRIOR = np.array([0.83, 0.0, 0.0, 0.0])
for p in range(W_BASE, len(COMMON_D)):
    y_win = Yd[p - W_BASE:p]
    x_win = Xd[p - W_BASE:p]
    w = half_life_weights(W_BASE, HALF_LIFE)
    prev = p3_beta_hist[COMMON_D[p - 1]] if p > W_BASE else Q2_PRIOR
    b = constrained_regression(y_win, x_win, w, Q2_PRIOR, prev)
    p3_beta_hist[COMMON_D[p]] = b
    p3_pred[COMMON_D[p]] = float(Xd[p] @ b)

beta_cur = p3_beta_hist[COMMON_D[-1]]
labels = ["光通信", "PCB", "半导体", "市场"]
print("[P3 行业因子] " + ", ".join(f"{l}={beta_cur[i]:.3f}" for i, l in enumerate(labels))
      + f"  | 残差≈{1 - beta_cur.sum():.3f}")

# PCB 敏感性（意见§七：弱/中/强 信号依据之一）
def beta_snapshot(w_len):
    y_w, x_w = Yd[-w_len:], Xd[-w_len:]
    w = half_life_weights(w_len, HALF_LIFE)
    return constrained_regression(y_w, x_w, w, Q2_PRIOR, Q2_PRIOR)

sens = {}
for ww in (W_BASE, W_SHORT, W_FAST):
    if len(COMMON_D) >= ww:
        sens[ww] = beta_snapshot(ww)
print(f"[PCB敏感性] " + ", ".join(f"{w}d={sens[w][1]:.3f}" for w in sorted(sens)))

# ---------- 6. 误差跟踪（意见§六：历史偏差修正） ----------
def oos_errors(pred_dict):
    return np.array([fund_ret[d] - pred_dict[d] for d in sorted(pred_dict)])

errs_p2 = oos_errors(p2_pred)
errs_p3 = oos_errors(p3_pred)
# P1 的样本外误差：Q2 静态组合收益 vs 实际（当日收益即 Q2 组合收益）
errs_p1 = np.array([Yd[i] - Q2d[i] for i in range(len(Q2d))])

K = min(40, len(errs_p2))
err_p2_recent = errs_p2[-K:]
err_p3_recent = errs_p3[-K:]
err_p1_recent = errs_p1[-K:]
err_med = float(np.median(err_p3_recent))       # 偏差修正（优先中位数）
err_std = float(np.std(err_p3_recent))
err_mae_p1 = float(np.mean(np.abs(err_p1_recent)))
err_mae_p2 = float(np.mean(np.abs(err_p2_recent)))
err_mae_p3 = float(np.mean(np.abs(err_p3_recent)))
print(f"[误差跟踪] 近{K}日 P1 MAE={err_mae_p1:.2f}% | P2 MAE={err_mae_p2:.2f}% | "
      f"P3 MAE={err_mae_p3:.2f}% | 偏差中位数={err_med:+.2f}% 波动={err_std:.2f}%")

# ---------- 7. 今日盘中实时行情 ----------
def qt_symbol(code):
    return ("sh" if code.startswith(("60", "68", "9", "000", "399")) else "sz") + code

def fetch_realtime(codes, retries=3):
    syms = [qt_symbol(c) for c in codes]
    url = "https://qt.gtimg.cn/q=" + ",".join(syms)
    last_err = None
    for a in range(retries):
        try:
            raw = urllib.request.urlopen(url, timeout=15).read().decode("gbk", "ignore")
            out = {}
            for line in raw.split(";"):
                m = __import__("re").search(r'v_(\w+)=\"([^\"]+)\"', line)
                if not m:
                    continue
                sec = m.group(1)
                p = m.group(2).split("~")
                try:
                    cur, prev = float(p[3]), float(p[4])
                except (ValueError, IndexError):
                    continue
                out[sec] = (cur, prev, (cur - prev) / prev if prev else 0.0)
            if out:
                return out
            last_err = "empty response"
        except Exception as e:
            last_err = repr(e)[:100]
        time.sleep(1.0 * (a + 1))
    raise RuntimeError(f"实时行情获取失败({retries}次重试): {last_err}")

all_codes = BASKETS["optical"] + BASKETS["pcb"] + BASKETS["semis"] + ["000852"]
rt = fetch_realtime(all_codes)

def intr(grp):
    codes = BASKETS[grp]
    vals = [rt[qt_symbol(c)][2] for c in codes if qt_symbol(c) in rt]
    return sum(vals) / len(vals) if vals else 0.0

intr_opt = intr("optical") * 100   # 转百分比
intr_pcb = intr("pcb") * 100
intr_sem = intr("semis") * 100
mkt_sec = qt_symbol("000852")
intr_mkt = rt[mkt_sec][2] * 100 if mkt_sec in rt else 0.0
print(f"[盘中行情] 光通信 {intr_opt:+.2f}% | PCB {intr_pcb:+.2f}% | 半导体 {intr_sem:+.2f}% | 市场 {intr_mkt:+.2f}%")

# Q2 组合今日盘中收益
r_q2_now = sum(TOP10_W[c] * rt[qt_symbol(c)][2] for c, _ in TOP10 if qt_symbol(c) in rt) * 100.0

# ---------- 8. 五个模型今日预测 ----------
P1 = r_q2_now
P2 = r_q2_now + theta_pcb_now * (intr_pcb - r_q2_now) + theta_m_now * (intr_mkt - r_q2_now)
P3 = float(beta_cur @ np.array([intr_opt, intr_pcb, intr_sem, intr_mkt]))
# P4 层级组合：行业总比例(P3 β) × 行业内Q2披露相对权重（光通信/PCB 内部）
# 光通信内部 = Q2 披露权重归一化；PCB 内部等权
opt_rel = {c: TOP10_W[c] for c, _ in TOP10}  # 光通信内部 Q2 相对权重
opt_sum = sum(opt_rel.values())
P4_opt = sum((opt_rel[c] / opt_sum) * rt[qt_symbol(c)][2] * 100.0
             for c in opt_rel if qt_symbol(c) in rt) if opt_sum > 0 else intr_opt
pcb_codes = BASKETS["pcb"]
P4_pcb = intr_pcb
P4 = beta_cur[0] * P4_opt + beta_cur[1] * P4_pcb + beta_cur[2] * intr_sem + beta_cur[3] * intr_mkt
# P5 个股辅助反推：读 infer.json（若存在）
P5 = None
try:
    infer = json.load(open(os.path.join(CACHE, "infer.json"), encoding="utf-8"))
    tr = infer.get("today_return_pct", {})
    if tr.get("lasso") is not None and tr.get("nnls") is not None:
        P5 = (float(tr["lasso"]) + float(tr["nnls"])) / 2.0
except Exception:
    pass

models = {"P1_Q2静态": P1, "P2_调仓替代": P2, "P3_行业因子": P3, "P4_层级组合": P4}
if P5 is not None:
    models["P5_个股辅助"] = P5

# ---------- 9. 模型集成（意见§十）：w ∝ 1/(MAE+0.05%)，单模型≤70% ----------
maes = {"P1_Q2静态": err_mae_p1, "P2_调仓替代": err_mae_p2, "P3_行业因子": err_mae_p3}
if P5 is not None:
    maes["P5_个股辅助"] = max(err_mae_p3, 0.4)  # 反推误差较大，保守权重
# P4 用 P3 的 MAE 近似（同源于行业暴露）
maes["P4_层级组合"] = err_mae_p3

weights = {}
for k in models:
    weights[k] = 1.0 / (maes[k] + 0.05)
cap = 0.70
# 归一化后截断到70%，再归一化
tot = sum(weights.values())
for k in weights:
    weights[k] = min(weights[k] / tot, cap)
tot2 = sum(weights.values())
for k in weights:
    weights[k] = weights[k] / tot2

P_final = sum(weights[k] * models[k] for k in models)
P_final_corr = P_final + err_med   # 历史偏差修正
# 估算目标日 = 最新行情日（COMMON_D[-1]）；nav_prev 应取"目标日的前一交易日净值"
# 若目标日净值已公布（navs 最后日期 == 目标日），nav_prev 取 navs[-2]，否则取 navs[-1]
target_date = COMMON_D[-1]
if dates[-1] == target_date:
    nav_prev = navs[-2]
    nav_prev_date = dates[-2]
else:
    nav_prev = navs[-1]
    nav_prev_date = dates[-1]
nav_center = nav_prev * (1 + P_final_corr / 100)
band = 1.5 * err_std
lo_band, hi_band = P_final_corr - band, P_final_corr + band

# ---------- 10. PCB 三级信号（意见§七） ----------
def pcb_signal_level():
    pcbs = [sens[w][1] for w in sorted(sens) if w in sens]
    if not pcbs:
        return "无信号"
    all_positive = all(p > 0.0 for p in pcbs)
    # 趋势：短窗系数是否持续高于长窗
    trend_up = (sens.get(W_FAST, [0])[1] if W_FAST in sens else 0) > \
               (sens.get(W_BASE, [0])[1] if W_BASE in sens else 0)
    pcb_beta_now = float(beta_cur[1])
    theta_now = float(theta_pcb_now)
    # Bootstrap 证据（若 infer.json 有 PCB 权重与 CI）
    boot_ok = False
    try:
        infer = json.load(open(os.path.join(CACHE, "infer.json"), encoding="utf-8"))
        ge = infer.get("group_exposure_lasso", {})
        boot_ok = ge.get("pcb", 0) > 0.03
    except Exception:
        pass
    if all_positive and trend_up and pcb_beta_now > 0.10 and theta_now > 0.05 and boot_ok:
        return "强信号"
    if all_positive and (trend_up or pcb_beta_now > 0.05):
        return "中等信号"
    return "弱信号"

pcb_level = pcb_signal_level()
print(f"[PCB信号] {pcb_level}（θ_pcb={theta_pcb_now:.3f}，β_pcb={beta_cur[1]:.3f}）")

# ---------- 11. 置信度三维（意见§五） ----------
model_spread = max(models.values()) - min(models.values())
theta_stable = abs(theta_pcb_now - (theta_pcb_hist.get(COMMON_D[-min(20, len(COMMON_D)-1)], theta_pcb_now)))
if err_mae_p3 < 0.6 and model_spread < 1.5 and theta_stable < 0.15:
    conf = "高"
elif err_mae_p3 < 1.2 and model_spread < 3.0:
    conf = "中等"
else:
    conf = "低"

# ---------- 12. 输出 ----------
print("\n" + "=" * 64)
print("006010 盘中估值 v3 · 多模型组合（按修改意见重构）")
print("=" * 64)
print(f"估算基准净值: {nav_prev} ({nav_prev_date}) → 目标日 {target_date}")
print("-" * 64)
for k in models:
    print(f"  {k:<12} {models[k]:+.2f}%   (权重 {weights[k]*100:.0f}%)")
print("-" * 64)
print(f"模型中心估计(加权): {P_final:+.2f}%")
print(f"历史偏差修正({err_med:+.2f}%): {P_final_corr:+.2f}%  → 预计净值 ≈ {nav_center:.4f}")
print(f"经验预测区间: {lo_band:+.2f}% ~ {hi_band:+.2f}%  (±1.5σ={band:.2f}%)")
print(f"模型分歧: {model_spread:.2f} pct")
print(f"PCB有效替代比例: {theta_pcb_now*100:.1f}%  | PCB调仓信号: {pcb_level}")
print(f"置信度: {conf}（近{K}日P3 MAE={err_mae_p3:.2f}%，分歧{model_spread:.2f}，θ稳定{theta_stable:.3f}）")
print(f"主要风险: ①光通信~PCB相关r={_corr[0,1]:.2f}调仓比例识别误差 "
      f"②Q2后或存未纳入候选池的其他科技股 ③盘中行情与基金估值时点存在时间差")
print("=" * 64)

result = {
    "version": "v3",
    "cur_date": COMMON_D[-1],
    "target_date": COMMON_D[-1],
    "nav_prev": nav_prev,
    "nav_prev_date": nav_prev_date,
    "models": {k: round(float(v), 2) for k, v in models.items()},
    "model_weights": {k: round(float(w), 3) for k, w in weights.items()},
    "P_final": round(float(P_final), 2),
    "bias_correction": round(err_med, 2),
    "P_final_corr": round(float(P_final_corr), 2),
    "nav_center": round(float(nav_center), 4),
    "band_pct": [round(float(lo_band), 2), round(float(hi_band), 2)],
    "model_spread_pct": round(float(model_spread), 2),
    "theta_pcb": round(float(theta_pcb_now), 4),
    "theta_mkt": round(float(theta_m_now), 4),
    "pcb_signal": pcb_level,
    "pcb_sensitivity": {str(w): round(float(sens[w][1]), 4) for w in sens},
    "exposure": {labels[i]: round(float(beta_cur[i]), 4) for i in range(4)},
    "residual": round(float(1 - beta_cur.sum()), 4),
    "confidence": conf,
    "mae": {"P1": round(err_mae_p1, 2), "P2": round(err_mae_p2, 2),
            "P3": round(err_mae_p3, 2)},
    "oos_err_std_pct": round(err_std, 2),
    "intraday": {"optical": round(intr_opt, 2), "pcb": round(intr_pcb, 2),
                 "semis": round(intr_sem, 2), "market": round(intr_mkt, 2),
                 "q2_now": round(r_q2_now, 2)},
    "factor_corr": {"opt_pcb": round(float(_corr[0, 1]), 3)},
    # 官方实际净值（若估算目标日净值已公布）→ 供页面/推送做"官方 vs 估算"对比
    "official_nav": None,
    "official_chg": None,
    "official_date": None,
}
# 读取 last_nav.json（由 nav_watch.py 写入），若官方净值已公布则填入
try:
    _ln = json.load(open(os.path.join(CACHE, "last_nav.json"), encoding="utf-8"))
    if _ln.get("date") == result["target_date"]:
        result["official_nav"] = _ln.get("nav")
        try:
            result["official_chg"] = round(float(_ln.get("chg")), 2)
        except (TypeError, ValueError):
            result["official_chg"] = None
        result["official_date"] = _ln.get("date")
except Exception:
    pass
json.dump(result, open(os.path.join(CACHE, "result.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print("[已保存] cache/result.json (v3 格式)")
if result["official_nav"] is not None:
    print(f"[官方净值] {result['official_date']} = {result['official_nav']} "
          f"({result['official_chg']:+.2f}%)  模型估算 {result['P_final_corr']:+.2f}%")
else:
    print(f"[官方净值] 未公布（模型估算目标日 {result['target_date']} 净值未出）")
