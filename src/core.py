#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core.py —— 006010 估值系统纯函数层（零副作用）

设计约束（重要）：
  1. import core 绝不触发网络请求、绝不读写文件；
  2. 主脚本(fund_valuation_v2)、回测(backtest_v3)、单元测试(test_core) 三者共用本模块，
     避免"主脚本改了、回测没跟上"造成的逻辑漂移；
  3. 所有函数对输入做防御式校验，失败时降级返回而非抛异常（除显式标注者）。

单位约定：收益率一律用"百分数"（+4.39 表示 +4.39%），权重/θ/β 用小数（0.0963）。
"""
import datetime as dt
import math

import numpy as np

TOP10 = [("688498", 9.63), ("688048", 9.36), ("300502", 9.36), ("688313", 8.42),
         ("300620", 8.35), ("300548", 8.24), ("300570", 8.18), ("688025", 7.97),
         ("300394", 7.73), ("300308", 5.92)]
TOP10_W = {c: w / 100.0 for c, w in TOP10}
SUM_W = sum(TOP10_W.values())

BASKETS = {
    "optical": ["688498", "688048", "300502", "688313", "300620",
                "300548", "300570", "688025", "300394", "300308"],
    "pcb":     ["002916", "002463", "300476"],
    "semis":   ["688981", "002371", "603501", "603986"],
    "market":  ["000852"],
}
FACTOR_LABELS = ["光通信", "PCB", "半导体", "市场"]

W_BASE = 60
W_SHORT = 20
W_FAST = 10
HALF_LIFE = 20

THETA_PCB_HI = 0.35
THETA_MKT_HI = 0.25

MAE_FLOOR = 0.02
MAE_POWER = 2.0
MAE_GATE = 1.30           # 劣质模型淘汰闸门：MAE 超过最优模型 1.30 倍的不参与集成（择优模式）
MODEL_WEIGHT_CAP = 0.70
MIN_GROUP_WEIGHT = 0.05   # 每组最低保底权重，防止单模型独大
PROVISIONAL_WEIGHT_CAP = 0.15

MODEL_GROUPS = {
    "P1_Q2静态":   "G1_静态基准",
    "P2_调仓替代": "G2_调仓替代",
    "P3_行业因子": "G3_行业暴露",
    "P4_层级组合": "G3_行业暴露",
    "P5_个股辅助": "G4_个股反推",
}

CN_HOLIDAYS = {
    2025: {
        "2025-01-01", "2025-01-28", "2025-01-29", "2025-01-30", "2025-01-31",
        "2025-02-03", "2025-02-04",
        "2025-04-04", "2025-04-05", "2025-04-06",
        "2025-05-01", "2025-05-02", "2025-05-03", "2025-05-04", "2025-05-05",
        "2025-06-09", "2025-06-10", "2025-06-11",
        "2025-09-15", "2025-09-16", "2025-09-17",
        "2025-10-01", "2025-10-02", "2025-10-03", "2025-10-04", "2025-10-05",
        "2025-10-06", "2025-10-07", "2025-10-08",
    },
    2026: {
        "2026-01-01", "2026-01-02",
        "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
        "2026-04-04", "2026-04-05", "2026-04-06",
        "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05",
        "2026-06-19", "2026-06-20", "2026-06-21",
        "2026-09-25", "2026-09-26", "2026-09-27",
        "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05",
        "2026-10-06", "2026-10-07", "2026-10-08",
    },
    2027: {
        "2027-01-01",
        "2027-02-11", "2027-02-12", "2027-02-13", "2027-02-14", "2027-02-15",
        "2027-02-16", "2027-02-17",
        "2027-04-03", "2027-04-04", "2027-04-05",
        "2027-05-01", "2027-05-02", "2027-05-03", "2027-05-04",
        "2027-06-14", "2027-06-15", "2027-06-16",
        "2027-09-20", "2027-09-21", "2027-09-22",
        "2027-10-01", "2027-10-02", "2027-10-03", "2027-10-04", "2027-10-05",
        "2027-10-06", "2027-10-07",
    },
}

def _get_holidays(year):
    return CN_HOLIDAYS.get(year, set())

def is_trade_day(datestr, holidays=None):
    if holidays is None:
        year = int(datestr[:4])
        holidays = _get_holidays(year)
    d = dt.date.fromisoformat(datestr)
    return d.weekday() < 5 and datestr not in holidays

def next_trade_day(datestr, holidays=None, max_step=30):
    if holidays is None:
        year = int(datestr[:4])
        holidays = _get_holidays(year)
    d = dt.date.fromisoformat(datestr)
    for _ in range(max_step):
        d += dt.timedelta(days=1)
        s = d.isoformat()
        if d.weekday() < 5 and s not in holidays:
            return s
    return d.isoformat()

def prev_trade_day(datestr, holidays=None, max_step=30):
    if holidays is None:
        year = int(datestr[:4])
        holidays = _get_holidays(year)
    d = dt.date.fromisoformat(datestr)
    for _ in range(max_step):
        d -= dt.timedelta(days=1)
        s = d.isoformat()
        if d.weekday() < 5 and s not in holidays:
            return s
    return d.isoformat()

def half_life_weights(n, half_life):
    if n <= 0:
        return np.array([])
    w = np.exp(-np.log(2) * np.arange(n - 1, -1, -1) / half_life)
    return w / w.sum()

def constrained_regression(y_win, x_win, weights, prior=None, prev_beta=None,
                           bnd_hi=1.0, lam1=0.01, lam2=0.03):
    from scipy.optimize import minimize, nnls
    y_win = np.asarray(y_win, dtype=float)
    x_win = np.asarray(x_win, dtype=float)
    n_factors = x_win.shape[1]
    prior = np.zeros(n_factors) if prior is None else np.asarray(prior, dtype=float)
    prev_beta = prior.copy() if prev_beta is None else np.asarray(prev_beta, dtype=float)
    weights = np.asarray(weights, dtype=float)

    def obj(b):
        r = y_win - x_win @ b
        return (np.sum(weights * r ** 2)
                + lam1 * len(y_win) * np.sum((b - prev_beta) ** 2)
                + lam2 * len(y_win) * np.sum((b - prior) ** 2))

    cons = [{"type": "ineq", "fun": lambda b: 1.0 - np.sum(b)}]
    bnds = [(0, bnd_hi)] * n_factors
    res = minimize(obj, prior.copy(), method="SLSQP", bounds=bnds, constraints=cons,
                   options={"maxiter": 1000, "ftol": 1e-12})
    if res.success:
        return np.clip(res.x, 0.0, bnd_hi)
    b, _ = nnls(x_win, y_win)
    if b.sum() > 1:
        b = b / b.sum()
    return np.clip(b, 0.0, bnd_hi)

def fit_theta(y_win, q2_win, pcb_win, mkt_win, weights,
              hi_pcb=THETA_PCB_HI, hi_m=THETA_MKT_HI,
              lam_smooth=0.05, lam_shrink=0.10, prev=None):
    from scipy.optimize import minimize
    y_win = np.asarray(y_win, dtype=float)
    q2_win = np.asarray(q2_win, dtype=float)
    pcb_win = np.asarray(pcb_win, dtype=float)
    mkt_win = np.asarray(mkt_win, dtype=float)
    weights = np.asarray(weights, dtype=float)
    x2 = np.column_stack([pcb_win - q2_win, mkt_win - q2_win])
    target = y_win - q2_win
    prior = np.array([0.05, 0.05]) if prev is None else np.asarray(prev, dtype=float)

    def obj(th):
        r = target - x2 @ th
        return (np.sum(weights * r ** 2)
                + lam_smooth * len(y_win) * np.sum((th - prior) ** 2)
                + lam_shrink * len(y_win) * np.sum(th ** 2))

    cons = [{"type": "ineq", "fun": lambda t: hi_pcb - t[0]},
            {"type": "ineq", "fun": lambda t: hi_m - t[1]}]
    res = minimize(obj, prior.copy(), method="SLSQP",
                   bounds=[(0, hi_pcb), (0, hi_m)], constraints=cons,
                   options={"maxiter": 1000, "ftol": 1e-12})
    if not res.success:
        return np.array([0.0, 0.0])
    return np.array([min(max(res.x[0], 0.0), hi_pcb),
                     min(max(res.x[1], 0.0), hi_m)])

def basket_returns(kl_group, codes):
    rets = {}
    for c in codes:
        close = kl_group.get(c)
        if not close:
            continue
        ds = sorted(close.keys())
        d = {}
        for i in range(1, len(ds)):
            prev_px = close[ds[i - 1]]
            if prev_px:
                d[ds[i]] = (close[ds[i]] / prev_px - 1.0) * 100.0
        rets[c] = d
    alld = set()
    for c in rets:
        alld |= set(rets[c].keys())
    out = {}
    for d in sorted(alld):
        vals = [rets[c][d] for c in rets if d in rets[c]]
        if vals:
            out[d] = sum(vals) / len(vals)
    return out

def basket_returns_robust(kl_group, codes, trim_mad=None):
    if trim_mad is None:
        return basket_returns(kl_group, codes)
    raw = {}
    for c in codes:
        close = kl_group.get(c)
        if not close:
            continue
        ds = sorted(close.keys())
        for i in range(1, len(ds)):
            prev_px = close[ds[i - 1]]
            if prev_px:
                raw.setdefault(ds[i], []).append((close[ds[i]] / prev_px - 1.0) * 100.0)
    out = {}
    for d, vals in raw.items():
        arr = np.asarray(vals, dtype=float)
        if len(arr) >= 4:
            med = np.median(arr)
            mad = np.median(np.abs(arr - med))
            if mad > 1e-9:
                keep = arr[np.abs(arr - med) <= trim_mad * mad]
                if len(keep) >= 2:
                    arr = keep
        out[d] = float(np.mean(arr))
    return dict(sorted(out.items()))

def _is_group_map(obj):
    if not isinstance(obj, dict):
        return False
    for v in obj.values():
        if not isinstance(v, dict):
            return False
        for vv in v.values():
            return isinstance(vv, dict)
        return False
    return False

def intraday_unsettled_dates(snapshot):
    if not isinstance(snapshot, dict):
        return set()
    out = set()
    d = snapshot.get("date")
    if d and not snapshot.get("settled", False):
        out.add(d)
    for d in snapshot.get("unsettled_dates", []) or []:
        out.add(d)
    return out

def strip_unsettled(klines, unsettled_dates):
    bad = set(unsettled_dates or ())
    if not bad or not isinstance(klines, dict):
        return klines, 0
    removed = 0
    def _clean_code_map(cm):
        nonlocal removed
        out = {}
        for code, px in cm.items():
            if not isinstance(px, dict):
                out[code] = px
                continue
            kept = {d: v for d, v in px.items() if d not in bad}
            removed += len(px) - len(kept)
            out[code] = kept
        return out
    if _is_group_map(klines):
        return {g: _clean_code_map(cm) for g, cm in klines.items()}, removed
    return _clean_code_map(klines), removed

def merge_intraday_snapshot(klines, snapshot):
    if not isinstance(snapshot, dict):
        return klines, 0
    d = snapshot.get("date")
    prices = snapshot.get("prices") or {}
    if not d or not prices:
        return klines, 0
    written = 0
    grouped = _is_group_map(klines)
    def _apply(cm, pm):
        nonlocal written
        out = {code: dict(px) if isinstance(px, dict) else px for code, px in cm.items()}
        for code, v in pm.items():
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if v <= 0:
                continue
            out.setdefault(code, {})[d] = v
            written += 1
        return out
    if grouped:
        out = {}
        for g, cm in klines.items():
            pm = prices.get(g) if isinstance(prices.get(g), dict) else {}
            out[g] = _apply(cm, pm or {})
        return out, written
    flat = prices if not _is_group_map(prices) else {
        c: v for pm in prices.values() for c, v in pm.items()}
    return _apply(klines, flat), written

def load_settled_klines(cache_dir, verbose=True):
    import json as _json
    import os as _os
    kl = _json.load(open(_os.path.join(cache_dir, "klines.json"), encoding="utf-8"))
    snap = {}
    try:
        snap = _json.load(open(_os.path.join(cache_dir, "intraday.json"), encoding="utf-8"))
    except Exception:
        pass
    unsettled = intraday_unsettled_dates(snap)
    kl, n = strip_unsettled(kl, unsettled)
    meta = {"unsettled": sorted(unsettled), "stripped": n,
            "settled": bool(snap.get("settled")) if snap else None,
            "intraday_date": snap.get("date") if snap else None,
            "has_snapshot": bool(snap)}
    if verbose and unsettled:
        print(f"[数据卫生] 剔除未结算日期 {meta['unsettled']}（{n} 条盘中价）")
    return kl, meta

def q2_portfolio_returns(kl_optical, date_seq, weights=None):
    weights = TOP10_W if weights is None else weights
    out = []
    for i in range(1, len(date_seq)):
        d, prev = date_seq[i], date_seq[i - 1]
        s = 0.0
        for c, w in weights.items():
            px = kl_optical.get(c, {})
            if d in px and prev in px and px[prev]:
                s += w * (px[d] / px[prev] - 1.0)
        out.append(s * 100.0)
    return np.array(out)

def q2_weighted_basket(kl_optical, date_seq, weights=None):
    weights = TOP10_W if weights is None else weights
    wsum_all = sum(weights.values())
    if wsum_all <= 0:
        return {}
    out = {}
    for i in range(1, len(date_seq)):
        d, prev = date_seq[i], date_seq[i - 1]
        s, wsum = 0.0, 0.0
        for c, w in weights.items():
            px = kl_optical.get(c, {})
            if d in px and prev in px and px[prev]:
                s += (w / wsum_all) * (px[d] / px[prev] - 1.0) * 100.0
                wsum += w / wsum_all
        out[d] = s / wsum if wsum > 0 else 0.0
    return out

def top10_coverage(weights=None):
    weights = TOP10_W if weights is None else weights
    return float(sum(weights.values()))

def market_session(now=None):
    now = dt.datetime.now() if now is None else now
    today = now.date().isoformat()
    if not is_trade_day(today):
        return "non_trade_day"
    hm = now.hour * 60 + now.minute
    if hm < 9 * 60 + 15:
        return "pre_open"
    if hm < 11 * 60 + 30:
        return "trading"
    if hm < 13 * 60:
        return "lunch"
    if hm <= 15 * 60:
        return "trading"
    return "closed"

def resolve_estimation_mode(target_date, nav_latest_date, official_published,
                            now=None):
    session = market_session(now)
    if official_published:
        mode = "settled"
        label = f"今日官方净值（{target_date}）"
    elif session in ("trading", "lunch", "pre_open"):
        mode = "intraday"
        label = f"今日盘中估算（{target_date}）"
    else:
        mode = "next_trading_day"
        label = f"下一交易日估算（{target_date}）"
    return {
        "mode": mode,
        "target_date": target_date,
        "nav_latest_date": nav_latest_date,
        "nav_prev_date": prev_trade_day(target_date),
        "session": session,
        "label": label,
        "is_official_published": bool(official_published),
    }

def ensemble_weights(maes, groups=None, cap=MODEL_WEIGHT_CAP, floor=MAE_FLOOR,
                     power=MAE_POWER, gate=MAE_GATE, provisional=None,
                     provisional_cap=PROVISIONAL_WEIGHT_CAP):
    if not maes:
        return {}, {"group_weights": {}, "groups": {}, "group_mae": {}, "dropped": []}
    groups = MODEL_GROUPS if groups is None else groups

    def score(mae):
        return 1.0 / ((max(mae, 0.0) + floor) ** power)

    all_models = list(maes)
    orig_maes = dict(maes)
    dropped = []
    if gate and gate > 0 and len(maes) > 1:
        best_mae = min(maes.values())
        thr = (max(best_mae, 0.0) + floor) * gate
        kept = {m: v for m, v in maes.items() if (max(v, 0.0) + floor) <= thr}
        if kept:
            dropped = sorted(set(maes) - set(kept))
            maes = kept

    g_of = {m: groups.get(m, m) for m in maes}
    members = {}
    for m, g in g_of.items():
        members.setdefault(g, []).append(m)

    g_mae = {g: min(maes[m] for m in ms) for g, ms in members.items()}

    g_raw = {g: score(g_mae[g]) for g in g_mae}
    g_tot = sum(g_raw.values())
    g_w = {g: g_raw[g] / g_tot for g in g_raw}

    w = {}
    for g, ms in members.items():
        inner_raw = {m: score(maes[m]) for m in ms}
        inner_tot = sum(inner_raw.values())
        for m in ms:
            w[m] = g_w[g] * (inner_raw[m] / inner_tot)

    prov = set(provisional or ()) & set(w)
    use_prov = bool(prov) and len(prov) < len(w)
    caps = {m: (min(cap, provisional_cap) if (use_prov and m in prov) else cap)
            for m in w}
    for _ in range(12):
        w = {m: min(v, caps[m]) for m, v in w.items()}
        tot = sum(w.values())
        if tot <= 0:
            break
        w = {m: v / tot for m, v in w.items()}
        if all(v <= caps[m] + 1e-9 for m, v in w.items()):
            break

    if MIN_GROUP_WEIGHT > 0 and len(members) > 1:
        for g in g_w:
            if g_w[g] < MIN_GROUP_WEIGHT:
                g_w[g] = MIN_GROUP_WEIGHT
        tot = sum(g_w.values())
        g_w = {g: v / tot for g, v in g_w.items()}
        for g, ms in members.items():
            inner_raw = {m: score(maes[m]) for m in ms}
            inner_tot = sum(inner_raw.values())
            for m in ms:
                w[m] = g_w[g] * (inner_raw[m] / inner_tot)

    for m in all_models:
        w.setdefault(m, 0.0)
    all_members, all_gmae = {}, {}
    for m in all_models:
        g = groups.get(m, m)
        all_members.setdefault(g, []).append(m)
        all_gmae[g] = min(all_gmae.get(g, float("inf")), orig_maes[m])

    return w, {"group_weights": {g: round(g_w.get(g, 0.0), 4) for g in all_members},
               "groups": all_members,
               "group_mae": {g: round(all_gmae[g], 4) for g in all_members},
               "dropped": dropped, "n_active": len(all_models) - len(dropped),
               "provisional_capped": sorted(prov) if use_prov else []}

def ewma(values, half_life=10):
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return 0.0
    w = half_life_weights(len(arr), half_life)
    return float(np.sum(w * arr) / np.sum(w))

def bias_correction(errs, hl=10, max_abs=1.5, div_tol=0.35):
    arr = np.asarray(errs, dtype=float)
    if arr.size == 0:
        return {"med20": 0.0, "med40": 0.0, "ewma": 0.0, "applied": 0.0,
                "divergence": 0.0, "shrink": 1.0, "stable": True, "n": 0}
    m20 = float(np.median(arr[-min(20, arr.size):]))
    m40 = float(np.median(arr[-min(40, arr.size):]))
    ew = ewma(arr[-min(40, arr.size):], half_life=hl)
    cand = [m20, m40, ew]
    divergence = float(max(cand) - min(cand))
    shrink = 1.0
    if divergence > div_tol:
        shrink = max(0.3, div_tol / divergence)
    raw = 0.5 * ew + 0.5 * m20
    applied = raw * shrink
    applied = float(max(-max_abs, min(max_abs, applied)))
    return {"med20": round(m20, 4), "med40": round(m40, 4), "ewma": round(ew, 4),
            "raw": round(raw, 4), "applied": round(applied, 4),
            "divergence": round(divergence, 4), "shrink": round(shrink, 3),
            "stable": divergence <= div_tol, "n": int(arr.size)}

def confidence_score(mae, spread, theta_stability, bias_divergence,
                     data_quality=1.0, coverage=None):
    def lin(x, best, worst, full):
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return full * 0.5
        if x <= best:
            return full
        if x >= worst:
            return 0.0
        return full * (worst - x) / (worst - best)
    s_mae = lin(mae, 0.30, 1.50, 40)
    s_spread = lin(spread, 0.50, 4.00, 25)
    s_theta = lin(theta_stability, 0.03, 0.20, 15)
    s_bias = lin(bias_divergence, 0.15, 0.80, 10)
    cov = SUM_W if coverage is None else coverage
    cov_factor = min(1.0, max(0.0, (cov - 0.5) / 0.4))
    s_data = 10.0 * max(0.0, min(1.0, data_quality)) * (0.5 + 0.5 * cov_factor)
    score = s_mae + s_spread + s_theta + s_bias + s_data
    score = int(round(max(0.0, min(100.0, score))))
    grade = "高" if score >= 75 else ("中等" if score >= 50 else "低")
    return score, grade, {
        "mae": round(s_mae, 1), "spread": round(s_spread, 1),
        "theta": round(s_theta, 1), "bias": round(s_bias, 1),
        "data": round(s_data, 1),
    }

def validate_market_data(nav, klines, baskets=None, min_nav_rows=120,
                         min_kline_rows=120, max_daily_abs_pct=25.0,
                         today=None):
    baskets = BASKETS if baskets is None else baskets
    errors, warnings, detail = [], [], {}
    dates = (nav or {}).get("dates") or []
    navs = (nav or {}).get("navs") or []
    detail["nav_rows"] = len(dates)
    if len(dates) != len(navs):
        errors.append(f"nav 日期/净值长度不一致: {len(dates)} vs {len(navs)}")
    if len(dates) < min_nav_rows:
        warnings.append(f"nav 样本偏少({len(dates)} < {min_nav_rows})，滚动窗口可能不足")
    if len(dates) < 30:
        errors.append(f"nav 样本严重不足({len(dates)} < 30)，无法估计参数")
    if dates != sorted(dates):
        errors.append("nav 日期未升序")
    if len(set(dates)) != len(dates):
        errors.append("nav 存在重复日期")
    if any((v is None or v <= 0) for v in navs):
        errors.append("nav 含非正净值")
    bad_ret = []
    for i in range(1, len(navs)):
        if navs[i - 1]:
            r = (navs[i] / navs[i - 1] - 1) * 100
            if abs(r) > max_daily_abs_pct:
                bad_ret.append((dates[i], round(r, 2)))
    if bad_ret:
        warnings.append(f"净值日收益异常({len(bad_ret)}条，如 {bad_ret[:2]})")
    detail["abnormal_nav_returns"] = bad_ret[:5]
    if dates:
        detail["nav_latest"] = dates[-1]
        ref = dt.date.today() if today is None else dt.date.fromisoformat(today)
        lag_days = (ref - dt.date.fromisoformat(dates[-1])).days
        detail["nav_lag_days"] = lag_days
        if lag_days > 5:
            warnings.append(f"净值数据滞后 {lag_days} 天，可能未及时更新")
    miss_ratio = {}
    for g, codes in baskets.items():
        grp = (klines or {}).get(g) or {}
        have = [c for c in codes if len(grp.get(c) or {}) >= min_kline_rows]
        ratio = len(have) / len(codes) if codes else 0.0
        miss_ratio[g] = round(1 - ratio, 3)
        if ratio < 0.6:
            errors.append(f"篮子 {g} 可用个股不足 60%（{len(have)}/{len(codes)}）")
        elif ratio < 1.0:
            warnings.append(f"篮子 {g} 缺 {len(codes)-len(have)} 只（按剩余个股等权降级）")
    detail["basket_missing_ratio"] = miss_ratio
    detail["top10_coverage"] = round(top10_coverage(), 4)
    if errors:
        score = 0.0
    else:
        score = max(0.3, 1.0 - 0.12 * len(warnings))
    return {"ok": not errors, "score": round(score, 3),
            "errors": errors, "warnings": warnings, "detail": detail}

def check_date_consistency(target_date, nav_prev_date, nav_dates,
                           official_date=None, holidays=None):
    problems = []
    if nav_prev_date and target_date:
        expect = prev_trade_day(target_date, holidays)
        if nav_prev_date != expect:
            if nav_prev_date >= target_date:
                problems.append(
                    f"基准净值日({nav_prev_date}) 不早于目标日({target_date})，语义错位")
            else:
                problems.append(
                    f"基准净值日({nav_prev_date}) 非目标日上一交易日(应为 {expect})")
    if nav_dates and target_date and target_date in nav_dates:
        i = nav_dates.index(target_date)
        if i >= 1 and nav_prev_date != nav_dates[i - 1]:
            problems.append(
                f"目标日净值已公布，基准应取 {nav_dates[i-1]}，实际 {nav_prev_date}")
    if official_date and target_date:
        expect_same = official_date == target_date
        expect_prev = official_date == prev_trade_day(target_date, holidays)
        if not (expect_same or expect_prev):
            problems.append(f"官方净值日({official_date}) 与目标日({target_date}) 关系异常，预期相同或为上一交易日({prev_trade_day(target_date, holidays)})")
    if target_date and not is_trade_day(target_date, holidays):
        problems.append(f"目标日 {target_date} 非交易日")
    return (not problems), problems

def error_metrics(preds, actuals):
    p = np.asarray(preds, dtype=float)
    a = np.asarray(actuals, dtype=float)
    if p.size == 0 or p.size != a.size:
        return {"n": 0, "mae": None, "rmse": None, "max_abs": None,
                "hit": None, "bias": None}
    e = p - a
    hit = float(np.mean([(pi > 0) == (ai > 0) for pi, ai in zip(p, a)]))
    return {"n": int(p.size),
            "mae": round(float(np.mean(np.abs(e))), 4),
            "rmse": round(float(np.sqrt(np.mean(e ** 2)))), 4),
            "max_abs": round(float(np.max(np.abs(e))), 4),
            "hit": round(hit, 4),
            "bias": round(float(np.mean(e)), 4)}

def classify_regime(actual_pct, up=1.5, down=-1.5):
    if actual_pct >= up:
        return "急涨"
    if actual_pct <= down:
        return "急跌"
    return "震荡"

def trend_slope(values):
    arr = np.asarray(values, dtype=float)
    if arr.size < 3:
        return 0.0
    x = np.arange(arr.size, dtype=float)
    x -= x.mean()
    denom = float(np.sum(x ** 2))
    if denom < 1e-12:
        return 0.0
    return float(np.sum(x * (arr - arr.mean())) / denom)

def pcb_signal_strength(sens_by_window, theta_recent, beta_pcb, theta_now,
                        mae_with_pcb=None, mae_without_pcb=None,
                        infer_pcb_exposure=None, basket_agreement=None):
    pcbs = [sens_by_window[w][1] for w in sorted(sens_by_window)] if sens_by_window else []
    if not pcbs:
        return "无信号", {}
    c1 = all(p > 0.0 for p in pcbs)
    slope = trend_slope(theta_recent) if theta_recent is not None else 0.0
    c2 = slope > 1e-4
    if mae_with_pcb is not None and mae_without_pcb is not None:
        c3 = mae_with_pcb < mae_without_pcb - 1e-9
        mae_gain = round(float(mae_without_pcb - mae_with_pcb), 4)
    else:
        c3, mae_gain = False, None
    c4 = (infer_pcb_exposure is not None and infer_pcb_exposure > 0.03)
    c5 = bool(basket_agreement) if basket_agreement is not None else False
    hard = float(beta_pcb) > 0.05 and float(theta_now) > 0.02
    n_sub = sum([c2, c3, c4])
    if c1 and hard and c2 and c3 and c4 and (c5 or basket_agreement is None):
        level = "强信号"
    elif c1 and hard and n_sub >= 2:
        level = "中等信号"
    elif c1:
        level = "弱信号"
    else:
        level = "无信号"
    return level, {
        "C1_多窗口为正": c1,
        "C2_替代比例上升": c2,
        "C3_加PCB后MAE下降": c3,
        "C4_个股反推独立证据": c4,
        "C5_多篮子一致": c5,
        "theta_slope": round(slope, 5),
        "mae_gain_pp": mae_gain,
        "beta_pcb": round(float(beta_pcb), 4),
        "theta_now": round(float(theta_now), 4),
        "windows_pcb": [round(float(p), 4) for p in pcbs],
    }