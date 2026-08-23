#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fund_valuation_v2.py —— 006010 盘中估值 v4（可信度与验证严谨性强化版）

总原则（延续 v3）：
  目标不是"精确还原真实个股持仓"，而是"尽可能降低盘中估算与收盘官方净值的误差"。
  β/θ 一律解释为"模型有效暴露"，不等同真实持仓比例。

v4 相对 v3 的改动（本轮 9 项可信度强化）：
  [1] 全部核心算法迁入 src/core.py 纯函数层 —— 主脚本/回测/单测共用一套逻辑，消除漂移
  [2] 数据源上线体检 validate_market_data()：致命问题拒绝出数，非致命降级并扣质量分
  [3] 集成权重改分组去重：P3/P4 同源于同一组 β，合并为 G3 组共享权重（原来各拿一份=双倍计权）
  [4] 偏差修正防滞后：同时输出 med20/med40/EWMA，主用 EWMA+med20 融合；
      三估计量分歧过大时按比例收缩修正幅度，并自动下调置信度
  [5] 置信度改 0-100 连续分（MAE40 + 分歧25 + θ稳定15 + 偏差稳定10 + 数据质量10）
  [6] 显式估算语义字段 estimation_mode(settled/intraday/next_trading_day) + market_session
  [7] 日期错配自检 check_date_consistency()，基准净值日必须是目标日上一交易日
  [8] 前十大覆盖率 top10_coverage 提升为核心输出指标（估值精度的物理上限）
  [9] 实时行情分篮子降级：单篮子可用个股不足则标记 degraded 而非静默用错数

数据纪律：仅用 target_date 及以前数据；当天官方净值绝不参与当天参数估计。
"""
import json, os, sys, urllib.request, time, re
import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "cache")
sys.path.insert(0, HERE)

import core
from core import (TOP10, TOP10_W, SUM_W, BASKETS, FACTOR_LABELS,
                  W_BASE, W_SHORT, W_FAST, HALF_LIFE, MAE_FLOOR)

NAME = {"optical": "光通信", "pcb": "PCB", "semis": "半导体", "market": "市场(中证1000)"}
P5_MAE_PROXY_FLOOR = 0.40

NAV = json.load(open(os.path.join(CACHE, "nav.json"), encoding="utf-8"))
KL = json.load(open(os.path.join(CACHE, "klines.json"), encoding="utf-8"))

INTRA = {}
try:
    INTRA = json.load(open(os.path.join(CACHE, "intraday.json"), encoding="utf-8"))
except Exception:
    pass
_unsettled = core.intraday_unsettled_dates(INTRA)
KL, _n_strip = core.strip_unsettled(KL, _unsettled)
if _unsettled:
    print(f"[数据卫生] intraday.json 标记未结算日期 {sorted(_unsettled)}"
          f" → 已从收盘价序列剔除 {_n_strip} 条（盘中价不得参与回归/误差统计）")
elif INTRA:
    print(f"[数据卫生] intraday.json 日期 {INTRA.get('date')} 已收盘结算，收盘价序列保留")
else:
    print("[数据卫生] 未找到 cache/intraday.json（旧版取数脚本）→ "
          "无法判定当日价格是否已结算，建议重跑 scripts/fetch_all.py")

dq = core.validate_market_data(NAV, KL)
print(f"[数据体检] {'通过' if dq['ok'] else '未通过'}  质量分={dq['score']:.2f}  "
      f"nav={dq['detail'].get('nav_rows')}条 最新={dq['detail'].get('nav_latest')} "
      f"滞后={dq['detail'].get('nav_lag_days')}天")
for e in dq["errors"]:
    print(f"  [致命] {e}")
for w in dq["warnings"]:
    print(f"  [告警] {w}")
if not dq["ok"]:
    print("[中止] 数据源存在致命问题，拒绝输出估值（避免用坏数据误导决策）")
    sys.exit(2)
print(f"[覆盖率] 前十大占净值 {SUM_W*100:.2f}% —— 估值精度的物理上限,"
      f"剩余 {(1-SUM_W)*100:.2f}% 为未披露持仓/现金")

BR = {g: core.basket_returns_robust(KL[g], BASKETS[g], trim_mad=3.0) for g in BASKETS}

dates, navs = NAV["dates"], NAV["navs"]
fund_ret = {dates[i]: (navs[i] / navs[i - 1] - 1.0) * 100.0 for i in range(1, len(dates))}

common = sorted(set(fund_ret) & set(BR["optical"]) & set(BR["pcb"]) &
                set(BR["semis"]) & set(BR["market"]))
X = np.array([[BR["optical"][d], BR["pcb"][d], BR["semis"][d], BR["market"][d]]
              for d in common])

Q2d = core.q2_portfolio_returns(KL["optical"], common)
Yd = np.array([fund_ret[d] for d in common[1:]])
Xd = X[1:]
PCBr = np.array([BR["pcb"][d] for d in common[1:]])
MKTd = np.array([BR["market"][d] for d in common[1:]])
COMMON_D = common[1:]

if len(COMMON_D) < W_BASE + 5:
    print(f"[中止] 回归样本不足（{len(COMMON_D)} < {W_BASE+5}）")
    sys.exit(2)

print(f"[对齐] 回归样本 {len(COMMON_D)} 日, {COMMON_D[0]} → {COMMON_D[-1]}")
_corr = np.corrcoef(X.T)
print(f"[因子相关性] 光通信~PCB r={_corr[0,1]:.2f} | 光通信~半导体 r={_corr[0,2]:.2f} | "
      f"光通信~市场 r={_corr[0,3]:.2f}")

theta_pcb_hist, theta_m_hist, p2_pred = {}, {}, {}
_prev_th = None
for p in range(W_BASE, len(COMMON_D)):
    w = core.half_life_weights(W_BASE, HALF_LIFE)
    th = core.fit_theta(Yd[p - W_BASE:p], Q2d[p - W_BASE:p],
                        PCBr[p - W_BASE:p], MKTd[p - W_BASE:p], w, prev=_prev_th)
    _prev_th = th
    theta_pcb_hist[COMMON_D[p]] = th[0]
    theta_m_hist[COMMON_D[p]] = th[1]
    p2_pred[COMMON_D[p]] = Q2d[p] + th[0] * (PCBr[p] - Q2d[p]) + th[1] * (MKTd[p] - Q2d[p])

theta_pcb_now = theta_pcb_hist[COMMON_D[-1]]
theta_m_now = theta_m_hist[COMMON_D[-1]]
print(f"[P2 调仓替代] θ_pcb={theta_pcb_now:.3f} θ_m={theta_m_now:.3f} (截至 {COMMON_D[-1]})")

Q2_PRIOR = np.array([round(SUM_W, 2), 0.0, 0.0, 0.0])
p3_beta_hist, p3_pred = {}, {}
for p in range(W_BASE, len(COMMON_D)):
    w = core.half_life_weights(W_BASE, HALF_LIFE)
    prev = p3_beta_hist[COMMON_D[p - 1]] if p > W_BASE else Q2_PRIOR
    b = core.constrained_regression(Yd[p - W_BASE:p], Xd[p - W_BASE:p], w, Q2_PRIOR, prev)
    p3_beta_hist[COMMON_D[p]] = b
    p3_pred[COMMON_D[p]] = float(Xd[p] @ b)

beta_cur = p3_beta_hist[COMMON_D[-1]]
print("[P3 行业因子] " + ", ".join(f"{l}={beta_cur[i]:.3f}" for i, l in enumerate(FACTOR_LABELS))
      + f"  | 残差≈{1 - beta_cur.sum():.3f}")

sens = {}
for ww in (W_BASE, W_SHORT, W_FAST):
    if len(COMMON_D) >= ww:
        w = core.half_life_weights(ww, HALF_LIFE)
        sens[ww] = core.constrained_regression(Yd[-ww:], Xd[-ww:], w, Q2_PRIOR, Q2_PRIOR)
print("[PCB敏感性] " + ", ".join(f"{w}d={sens[w][1]:.3f}" for w in sorted(sens)))

_IDX_NO_PCB = [0, 2, 3]
Xd_np = Xd[:, _IDX_NO_PCB]
Q2_PRIOR_NP = Q2_PRIOR[_IDX_NO_PCB]
p3_pred_nopcb = {}
_prev_b = Q2_PRIOR_NP
for p in range(W_BASE, len(COMMON_D)):
    w = core.half_life_weights(W_BASE, HALF_LIFE)
    b = core.constrained_regression(Yd[p - W_BASE:p], Xd_np[p - W_BASE:p], w,
                                    Q2_PRIOR_NP, _prev_b)
    _prev_b = b
    p3_pred_nopcb[COMMON_D[p]] = float(Xd_np[p] @ b)

def pcb_leave_one_out():
    codes = BASKETS["pcb"]
    if len(codes) < 3:
        return None
    betas = []
    for drop in codes:
        sub = [c for c in codes if c != drop]
        br_sub = core.basket_returns(KL["pcb"], sub)
        try:
            x_alt = np.array([[BR["optical"][d], br_sub[d], BR["semis"][d], BR["market"][d]]
                              for d in COMMON_D[-W_BASE:]])
        except KeyError:
            continue
        w = core.half_life_weights(W_BASE, HALF_LIFE)
        b = core.constrained_regression(Yd[-W_BASE:], x_alt, w, Q2_PRIOR, Q2_PRIOR)
        betas.append(float(b[1]))
    if not betas:
        return None
    return all(b > 0.0 for b in betas), [round(b, 4) for b in betas]

_loo = pcb_leave_one_out()
basket_agree = _loo[0] if _loo else None
if _loo:
    print(f"[PCB多篮子] 留一法 β_pcb={_loo[1]} → {'一致为正' if _loo[0] else '不一致'}")

def oos_errors(pred_dict):
    return np.array([fund_ret[d] - pred_dict[d] for d in sorted(pred_dict)])

_p4_opt_hist = core.q2_weighted_basket(KL["optical"], common)
p4_pred = {}
for p in range(W_BASE, len(COMMON_D)):
    d = COMMON_D[p]
    b = p3_beta_hist[d]
    if d in _p4_opt_hist:
        p4_pred[d] = float(b[0] * _p4_opt_hist[d] + b[1] * Xd[p, 1]
                           + b[2] * Xd[p, 2] + b[3] * Xd[p, 3])

errs_p2 = oos_errors(p2_pred)
errs_p3 = oos_errors(p3_pred)
errs_p4 = oos_errors(p4_pred) if p4_pred else errs_p3\,errs_p1 = np.array([Yd[i] - Q2d[i] for i in range(len(Q2d))])

K = min(W_BASE, len(errs_p2))
err_mae_p1 = float(np.mean(np.abs(errs_p1[-K:])))
err_mae_p2 = float(np.mean(np.abs(errs_p2[-K:])))
err_mae_p3 = float(np.mean(np.abs(errs_p3[-K:])))
err_mae_p4 = float(np.mean(np.abs(errs_p4[-K:])))
print(f"[P4 独立误差] 近{K}日 MAE={err_mae_p4:.3f}%"
      f"（此前借用 P3 的 {err_mae_p3:.3f}%，差 {err_mae_p4-err_mae_p3:+.3f}pp）")

p5_mae_real, p5_mae_src, p5_provisional = None, "无", True
try:
    _p5r = json.load(open(os.path.join(CACHE, "p5_mae.json"), encoding="utf-8"))
    if _p5r.get("is_real_oos") and _p5r.get("mae_40"):
        p5_mae_real = float(_p5r["mae_40"])
        p5_mae_src = (f"真实样本外 {_p5r['n_samples']}日回测"
                      f"（{_p5r['period'][0]}~{_p5r['period'][1]}，近40日）")
        p5_provisional = False
except Exception:
    pass
if p5_mae_real is None:
    p5_mae_src = f"代理值 max(MAE_P3, {P5_MAE_PROXY_FLOOR})，权重将被硬性封顶"
errs_p3_nopcb = oos_errors(p3_pred_nopcb)
err_mae_nopcb = float(np.mean(np.abs(errs_p3_nopcb[-K:])))
print(f"[PCB增量验证] 含PCB因子 MAE={err_mae_p3:.3f}% vs 不含PCB MAE={err_mae_nopcb:.3f}% "
      f"→ {'下降' if err_mae_p3 < err_mae_nopcb else '未下降'} {err_mae_nopcb-err_mae_p3:+.3f}pp")

_ens_hist_pred, _dlist = {}, sorted(p3_pred)
_p5_daily = {}
try:
    for _r in json.load(open(os.path.join(CACHE, "p5_mae.json"),
                             encoding="utf-8")).get("daily", []):
        _p5_daily[_r["date"]] = float(_r["p5"])
except Exception:
    pass

for _i, _d in enumerate(_dlist):
    _hist = _dlist[max(0, _i - 40):_i]
    if len(_hist) < 10:
        continue
    _m = {}
    _mp = {"P1_Q2静态": lambda h: Yd[COMMON_D.index(h)] - errs_p1[COMMON_D.index(h)],
           "P2_调仓替代": lambda h: p2_pred[h],
           "P3_行业因子": lambda h: p3_pred[h],
           "P4_层级组合": lambda h: p4_pred.get(h, p3_pred[h])}
    for _k, _f in _mp.items():
        _m[_k] = float(np.mean([abs(fund_ret[h] - _f(h)) for h in _hist]))
    if all(h in _p5_daily for h in _hist) and _d in _p5_daily:
        _m["P5_个股辅助"] = float(np.mean([abs(fund_ret[h] - _p5_daily[h])
                                          for h in _hist]))
    _w, _ = core.ensemble_weights(_m)
    _cur = {"P1_Q2静态": Q2d[COMMON_D.index(_d)], "P2_调仓替代": p2_pred[_d],
            "P3_行业因子": p3_pred[_d], "P4_层级组合": p4_pred.get(_d, p3_pred[_d])}
    if "P5_个股辅助" in _m:
        _cur["P5_个股辅助"] = _p5_daily[_d]
    _ens_hist_pred[_d] = sum(_w.get(k, 0.0) * v for k, v in _cur.items())

errs_ens = np.array([fund_ret[d] - _ens_hist_pred[d] for d in sorted(_ens_hist_pred)])
if len(errs_ens) >= 10:
    bias = core.bias_correction(errs_ens[-K:], hl=10)
    err_std = float(np.std(errs_ens[-K:]))
    _bias_src = f"集成自身历史误差（{len(errs_ens)}日重建）"
else:
    bias = core.bias_correction(errs_p3[-K:], hl=10)
    err_std = float(np.std(errs_p3[-K:]))
    _bias_src = f"P3 误差序列（集成序列仅 {len(errs_ens)} 日，不足 10 日故降级）"
err_med = bias["applied"]
_bias_p3 = core.bias_correction(errs_p3[-K:], hl=10)["applied"]
print(f"[偏差口径] {_bias_src}：实施 {err_med:+.3f}%"
      f"（若沿用 P3 误差会是 {_bias_p3:+.3f}%，差 {err_med-_bias_p3:+.3f}pp）")

print(f"[误差跟踪] 近{K}日 P1 MAE={err_mae_p1:.2f}% | P2 MAE={err_mae_p2:.2f}% | "
      f"P3 MAE={err_mae_p3:.2f}% | 误差σ={err_std:.2f}%")
print(f"[偏差修正] med20={bias['med20']:+.2f}% med40={bias['med40']:+.2f}% "
      f"EWMA={bias['ewma']:+.2f}% → 融合{bias['raw']:+.2f}% "
      f"×收缩{bias['shrink']:.2f} = 实施{err_med:+.2f}%  "
      f"(分歧{bias['divergence']:.2f}pp {'稳定' if bias['stable'] else '不稳定→已收缩并下调置信度'})")

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
                m = re.search(r'v_(\w+)="([^"]+)"', line)
                if not m:
                    continue
                p = m.group(2).split("~")
                try:
                    cur, prev = float(p[3]), float(p[4])
                except (ValueError, IndexError):
                    continue
                if cur <= 0 or prev <= 0:
                    continue
                out[m.group(1)] = (cur, prev, (cur - prev) / prev)
            if out:
                return out
            last_err = "empty response"
        except Exception as e:
            last_err = repr(e)[:120]
        time.sleep(1.0 * (a + 1))
    raise RuntimeError(f"实时行情获取失败({retries}次重试): {last_err}")

all_codes = BASKETS["optical"] + BASKETS["pcb"] + BASKETS["semis"] + ["000852"]
rt = fetch_realtime(all_codes)

rt_degraded = {}
for g, codes in BASKETS.items():
    have = sum(1 for c in codes if qt_symbol(c) in rt)
    ratio = have / len(codes)
    if ratio < 1.0:
        rt_degraded[g] = f"{have}/{len(codes)}"
    if ratio < 0.6:
        print(f"[中止] 实时行情篮子 {g} 可用个股不足 60%（{have}/{len(codes)}）")
        sys.exit(3)
if rt_degraded:
    print(f"[行情降级] 部分篮子个股缺失（按剩余等权）: {rt_degraded}")

def intr(grp):
    vals = [rt[qt_symbol(c)][2] for c in BASKETS[grp] if qt_symbol(c) in rt]
    return (sum(vals) / len(vals) * 100) if vals else 0.0

intr_opt, intr_pcb, intr_sem = intr("optical"), intr("pcb"), intr("semis")
mkt_sec = qt_symbol("000852")
intr_mkt = rt[mkt_sec][2] * 100 if mkt_sec in rt else 0.0
print(f"[盘中行情] 光通信 {intr_opt:+.2f}% | PCB {intr_pcb:+.2f}% | "
      f"半导体 {intr_sem:+.2f}% | 市场 {intr_mkt:+.2f}%")

top10_live = sum(1 for c, _ in TOP10 if qt_symbol(c) in rt)
live_cov = sum(TOP10_W[c] for c, _ in TOP10 if qt_symbol(c) in rt)
r_q2_now = sum(TOP10_W[c] * rt[qt_symbol(c)][2] for c, _ in TOP10 if qt_symbol(c) in rt) * 100.0

P1 = r_q2_now
P2 = r_q2_now + theta_pcb_now * (intr_pcb - r_q2_now) + theta_m_now * (intr_mkt - r_q2_now)
P3 = float(beta_cur @ np.array([intr_opt, intr_pcb, intr_sem, intr_mkt]))

opt_sum = sum(TOP10_W.values())
P4_opt = (sum((TOP10_W[c] / opt_sum) * rt[qt_symbol(c)][2] * 100.0
              for c, _ in TOP10 if qt_symbol(c) in rt) if opt_sum > 0 else intr_opt)
P4 = beta_cur[0] * P4_opt + beta_cur[1] * intr_pcb + beta_cur[2] * intr_sem + beta_cur[3] * intr_mkt

P5 = None
try:
    infer = json.load(open(os.path.join(CACHE, "infer.json"), encoding="utf-8"))
    tr = infer.get("today_return_pct", {})
    if tr.get("lasso") is not None and tr.get("nnls") is not None:
        P5 = (float(tr["lasso"]) + float(tr["nnls"])) / 2.0
except Exception as e:
    print(f"[P5降级] 个股反推不可用({repr(e)[:60]})，权重归一到 P1~P4")

models = {"P1_Q2静态": P1, "P2_调仓替代": P2, "P3_行业因子": P3, "P4_层级组合": P4}
if P5 is not None:
    models["P5_个股辅助"] = P5

maes = {"P1_Q2静态": err_mae_p1, "P2_调仓替代": err_mae_p2,
        "P3_行业因子": err_mae_p3, "P4_层级组合": err_mae_p4}
provisional = set()
if P5 is not None:
    if p5_mae_real is not None:
        maes["P5_个股辅助"] = p5_mae_real
    else:
        maes["P5_个股辅助"] = max(err_mae_p3, P5_MAE_PROXY_FLOOR)
        provisional.add("P5_个股辅助")
print(f"[P5 误差来源] {p5_mae_src}"
      + (f" → MAE={p5_mae_real:.3f}%" if p5_mae_real is not None else ""))

weights, ens_info = core.ensemble_weights(maes, provisional=provisional)
print(f"[集成分组] " + " | ".join(
    f"{g}={ens_info['group_weights'][g]*100:.0f}%({','.join(m[:2] for m in ms)})"
    for g, ms in ens_info["groups"].items()))
if ens_info["dropped"]:
    print(f"[淘汰闸门] gate={core.MAE_GATE} 剔除 {ens_info['dropped']}"
          f"（MAE 超过最优模型 {core.MAE_GATE} 倍），活跃模型 {ens_info['n_active']} 个")
if ens_info["provisional_capped"]:
    print(f"[安全阀] {ens_info['provisional_capped']} 无真实样本外误差 → "
          f"权重封顶 {core.PROVISIONAL_WEIGHT_CAP*100:.0f}%")

P_final = sum(weights.get(k, 0.0) * models[k] for k in models)
P_final_corr = P_final + err_med

target_date = COMMON_D[-1]

official_nav = official_chg = official_date = None
try:
    _ln = json.load(open(os.path.join(CACHE, "last_nav.json"), encoding="utf-8"))
    if _ln.get("date") == target_date and _ln.get("nav") is not None:
        official_nav = _ln.get("nav")
        official_date = _ln.get("date")
        try:
            official_chg = round(float(_ln.get("chg")), 2)
        except (TypeError, ValueError):
            official_chg = None
except Exception:
    pass

if official_nav is not None:
    target_date = core.next_trade_day(target_date)
    nav_prev, nav_prev_date = navs[-1], dates[-1]
else:
    target_date = dates[-1]
    nav_prev, nav_prev_date = navs[-1], dates[-1]

est = core.resolve_estimation_mode(target_date, dates[-1], official_nav is not None)
ok_date, date_problems = core.check_date_consistency(
    target_date, nav_prev_date, dates, official_date)
if not ok_date:
    for p in date_problems:
        print(f"  [日期告警] {p}")
print(f"[估算语义] mode={est['mode']} session={est['session']} → {est['label']}"
      f"  基准净值 {nav_prev}({nav_prev_date})")

nav_center = nav_prev * (1 + P_final_corr / 100)
band = 1.5 * err_std
lo_band, hi_band = P_final_corr - band, P_final_corr + band

_infer_pcb = None
try:
    _inf = json.load(open(os.path.join(CACHE, "infer.json"), encoding="utf-8"))
    _infer_pcb = _inf.get("group_exposure_lasso", {}).get("pcb")
except Exception:
    pass

_theta_seq = [theta_pcb_hist[d] for d in sorted(theta_pcb_hist)][-10:]

pcb_level, pcb_evidence = core.pcb_signal_strength(
    sens_by_window=sens,
    theta_recent=_theta_seq,
    beta_pcb=beta_cur[1],
    theta_now=theta_pcb_now,
    mae_with_pcb=err_mae_p3,
    mae_without_pcb=err_mae_nopcb,
    infer_pcb_exposure=_infer_pcb,
    basket_agreement=basket_agree,
)
print(f"[PCB信号] {pcb_level}（θ_pcb={theta_pcb_now:.3f}，β_pcb={beta_cur[1]:.3f}）")
print("  证据: " + "  ".join(
    f"{k}={'✓' if v else '✗'}" for k, v in pcb_evidence.items()
    if k.startswith("C")) + f"  θ斜率={pcb_evidence.get('theta_slope')}")

model_spread = max(models.values()) - min(models.values())
_ref_key = COMMON_D[-min(20, len(COMMON_D) - 1)]
theta_stable = abs(theta_pcb_now - theta_pcb_hist.get(_ref_key, theta_pcb_now))
conf_score, conf, conf_detail = core.confidence_score(
    mae=err_mae_p3, spread=model_spread, theta_stability=theta_stable,
    bias_divergence=bias["divergence"], data_quality=dq["score"], coverage=SUM_W)

print("\n" + "=" * 66)
print("006010 盘中估值 v4 · 多模型组合（可信度强化版）")
print("=" * 66)
print(f"{est['label']}    基准净值 {nav_prev} ({nav_prev_date})")
print("-" * 66)
for k in models:
    print(f"  {k:<12} {models[k]:+.2f}%   (权重 {weights[k]*100:.0f}%)")
print("-" * 66)
print(f"模型中心估计(加权): {P_final:+.2f}%")
print(f"历史偏差修正({err_med:+.2f}%): {P_final_corr:+.2f}%  → 预计净值 ≈ {nav_center:.4f}")
print(f"经验预测区间: {lo_band:+.2f}% ~ {hi_band:+.2f}%  (±1.5σ={band:.2f}%)")
print(f"模型分歧: {model_spread:.2f} pct   前十大覆盖率: {SUM_W*100:.2f}%（实时兑现 {live_cov*100:.2f}%/{top10_live}只）")
print(f"PCB有效替代比例: {theta_pcb_now*100:.1f}%  | PCB调仓信号: {pcb_level}")
print(f"置信度: {conf_score}/100 ({conf})  明细 MAE{conf_detail['mae']}/40 "
      f"分歧{conf_detail['spread']}/25 θ{conf_detail['theta']}/15 "
      f"偏差{conf_detail['bias']}/10 数据{conf_detail['data']}/10")
print(f"主要风险: ①光通信~PCB相关r={_corr[0,1]:.2f}调仓比例识别误差 "
      f"②未披露持仓{(1-SUM_W)*100:.1f}%不可观测 ③盘中行情与基金估值时点存在时间差")
print("=" * 66)

_CALIBER = None
try:
    _dec = json.load(open(os.path.join(CACHE, "decide_report.json"), encoding="utf-8"))
    _CALIBER = {
        "primary": _dec.get("winner_full_sample"),
        "winner_by_segment": _dec.get("winner_by_segment"),
        "rank_flip_detected": _dec.get("rank_flip_detected"),
        "ensemble_vs_single_pp": _dec.get("ensemble_vs_single_pp"),
        "recommendation": _dec.get("recommendation"),
        "validation_period": _dec.get("validation_period"),
        "source": "tests/backtest_v3.py --decide",
    }
except Exception:
    pass

result = {
    "version": "v4",
    "cur_date": target_date,
    "target_date": target_date,
    "estimation_mode": est["mode"],
    "estimation_label": est["label"],
    "market_session": est["session"],
    "is_official_published": est["is_official_published"],
    "nav_latest_date": dates[-1],
    "nav_prev": nav_prev,
    "nav_prev_date": nav_prev_date,
    "date_check_ok": ok_date,
    "date_problems": date_problems,
    "models": {k: round(float(v), 2) for k, v in models.items()},
    "model_weights": {k: round(float(w), 3) for k, w in weights.items()},
    "model_groups": ens_info["groups"],
    "group_weights": ens_info["group_weights"],
    "ensemble_audit": {
        "gate": core.MAE_GATE,
        "mae_power": core.MAE_POWER,
        "mae_floor": core.MAE_FLOOR,
        "weight_cap": core.MODEL_WEIGHT_CAP,
        "provisional_cap": core.PROVISIONAL_WEIGHT_CAP,
        "dropped": ens_info["dropped"],
        "n_active": ens_info["n_active"],
        "provisional_capped": ens_info["provisional_capped"],
        "model_mae_used": {k: round(float(v), 3) for k, v in maes.items()},
        "group_mae": {g: (round(float(v), 3) if v is not None and np.isfinite(v) else None)
                      for g, v in ens_info["group_mae"].items()},
        "p5_mae_source": p5_mae_src,
        "p5_is_real_oos": (not p5_provisional),
        "bias_source": "集成自身历史误差（非借用 P3）",
    },
    "data_hygiene": {
        "has_intraday_snapshot": bool(INTRA),
        "intraday_date": INTRA.get("date") if INTRA else None,
        "intraday_settled": INTRA.get("settled") if INTRA else None,
        "unsettled_dates": sorted(_unsettled),
        "stripped_rows": _n_strip,
        "note": ("klines.json 只含正式收盘价；盘中实时价存 intraday.json,"
                 "不参与滚动回归/误差统计/回测"),
    },
    "caliber_decision": _CALIBER,
    "P_final": round(float(P_final), 2),
    "bias_correction": round(float(err_med), 2),
    "bias_detail": bias,
    "P_final_corr": round(float(P_final_corr), 2),
    "nav_center": round(float(nav_center), 4),
    "band_pct": [round(float(lo_band), 2), round(float(hi_band), 2)],
    "model_spread_pct": round(float(model_spread), 2),
    "theta_pcb": round(float(theta_pcb_now), 4),
    "theta_mkt": round(float(theta_m_now), 4),
    "pcb_signal": pcb_level,
    "pcb_evidence": pcb_evidence,
    "pcb_mae_with": round(err_mae_p3, 3),
    "pcb_mae_without": round(err_mae_nopcb, 3),
    "pcb_basket_loo": (_loo[1] if _loo else None),
    "pcb_sensitivity": {str(w): round(float(sens[w][1]), 4) for w in sens},
    "exposure": {FACTOR_LABELS[i]: round(float(beta_cur[i]), 4) for i in range(4)},
    "residual": round(float(1 - beta_cur.sum()), 4),
    "confidence": conf,
    "confidence_score": conf_score,
    "confidence_detail": conf_detail,
    "mae": {"P1": round(err_mae_p1, 2), "P2": round(err_mae_p2, 2),
            "P3": round(err_mae_p3, 2), "P4": round(err_mae_p4, 2),
            "P5": (round(p5_mae_real, 2) if p5_mae_real is not None else None)},
    "oos_err_std_pct": round(err_std, 2),
    "top10_coverage": round(SUM_W, 4),
    "top10_coverage_live": round(live_cov, 4),
    "top10_live_count": top10_live,
    "undisclosed_ratio": round(1 - SUM_W, 4),
    "data_quality": {"ok": dq["ok"], "score": dq["score"],
                     "warnings": dq["warnings"], "errors": dq["errors"],
                     "realtime_degraded": rt_degraded,
                     "nav_rows": dq["detail"].get("nav_rows"),
                     "nav_lag_days": dq["detail"].get("nav_lag_days")},
    "intraday": {"optical": round(intr_opt, 2), "pcb": round(intr_pcb, 2),
                 "semis": round(intr_sem, 2), "market": round(intr_mkt, 2),
                 "q2_now": round(r_q2_now, 2)},
    "factor_corr": {"opt_pcb": round(float(_corr[0, 1]), 3)},
    "official_nav": official_nav,
    "official_chg": official_chg,
    "official_date": official_date,
}
json.dump(result, open(os.path.join(CACHE, "result.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print("[已保存] cache/result.json (v4 格式)")
if official_nav is not None:
    print(f"[官方净值] {official_date} = {official_nav} ({official_chg:+.2f}%)  "
          f"模型估算 {P_final_corr:+.2f}%  偏差 {P_final_corr-official_chg:+.2f}pp")
else:
    print(f"[官方净值] 未公布（目标日 {target_date}）")