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

# ============================================================
# 0. 常量：Q2 持仓 / 篮子 / 窗口参数（唯一真源，其他脚本从此导入）
# ============================================================
# Q2(2026-06-30) 披露前十大持仓（占基金净值百分比）
TOP10 = [("688498", 9.63), ("688048", 9.36), ("300502", 9.36), ("688313", 8.42),
         ("300620", 8.35), ("300548", 8.24), ("300570", 8.18), ("688025", 7.97),
         ("300394", 7.73), ("300308", 5.92)]
TOP10_W = {c: w / 100.0 for c, w in TOP10}     # 小数权重
SUM_W = sum(TOP10_W.values())                   # ≈0.8316 —— 前十大覆盖率

BASKETS = {
    "optical": ["688498", "688048", "300502", "688313", "300620",
                "300548", "300570", "688025", "300394", "300308"],
    "pcb":     ["002916", "002463", "300476"],
    "semis":   ["688981", "002371", "603501", "603986"],
    "market":  ["000852"],
}
# 持仓动态化（季报自动更新）：cache/holdings.json（scripts/update_holdings.py 维护）
# 存在且有效时，用最新季报前十大覆盖 Q2 静态 TOP10 —— 季度切换自动生效，季度中不变。
import json as _json, os as _os

def _load_latest_top10():
    try:
        _p = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                           "cache", "holdings.json")
        if not _os.path.exists(_p):
            return None
        _d = _json.load(open(_p, encoding="utf-8"))
        _t = _d.get("top10") or []
        if len(_t) >= 5:                       # 至少 5 只才生效（防脏数据）
            return [(_h["code"], float(_h["weight"])) for _h in _t[:10]]
    except Exception:
        pass
    return None

_dyn_top10 = _load_latest_top10()
if _dyn_top10:
    TOP10 = _dyn_top10
    BASKETS["optical"] = [c for c, _ in TOP10]
# 重算派生常量（TOP10_W / SUM_W 随动态持仓变化）
TOP10_W = {c: w / 100.0 for c, w in TOP10}
SUM_W = sum(TOP10_W.values())

FACTOR_LABELS = ["光通信", "PCB", "半导体", "市场"]

# 统一窗口参数（方法文档 §四）
W_BASE = 60       # 基础滚动窗口
W_SHORT = 20      # 短期窗口
W_FAST = 10       # 快速窗口
HALF_LIFE = 20    # 时间半衰期（交易日）

# θ 上限（方法文档 §二；上限由回测选择，不代表真实持仓上限）
THETA_PCB_HI = 0.35
THETA_MKT_HI = 0.25

# 集成参数（方法文档 §十）
# --- 集成参数：由 tests/backtest_v3.py --tune / --decide 在 112 日样本上选出 ---
# 依据（2026-08-20 决策，见 cache/decide_report.json）：
#   全段(112日)MAE 0.5449%，最差子段 0.6220%，跨段波动 0.1588pp
#   同期 P4 单模型 0.5393%/0.6592%/0.2398pp、P1 静态 0.6084%/0.6220%/0.0272pp
#   → 该组合在(全段MAE, 最差子段MAE)双准则下位于帕累托前沿
MAE_FLOOR = 0.02          # w ∝ 1/(MAE + MAE_FLOOR)^MAE_POWER
MAE_POWER = 2.0           # 锐度指数：2.0≈逆方差加权
MAE_GATE = 1.00           # 劣质模型淘汰闸门：MAE 超过最优模型 1.00 倍的不参与集成（择优模式）
MODEL_WEIGHT_CAP = 0.70   # 单模型权重上限
# 无"真实样本外误差"的模型（其 MAE 只是代理/借用值）的硬上限。
# 背景：P5 个股反推曾用 max(MAE_P3, 0.4) 冒充自身误差，闸门上线后它凭假 MAE
# 独占一个分组、直接拿到 50% 权重 —— 用假误差换真权重，必须封顶。
PROVISIONAL_WEIGHT_CAP = 0.15

# 模型分组：同源模型归为一组，避免重复计权（P3/P4 同源于同一组行业 β）
MODEL_GROUPS = {
    "P1_Q2静态":   "G1_静态基准",
    "P2_调仓替代": "G2_调仓替代",
    "P3_行业因子": "G3_行业暴露",
    "P4_层级组合": "G3_行业暴露",   # 与 P3 同源
    "P5_个股辅助": "G4_个股反推",
}

# 中国 A 股法定休市日（不含周末），按年份分组，支持多年份查询。
# 来源：交易所公告 + 国务院放假通知。仅含全天休市，不含半日交易。
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

def _get_holidays(year: int):
    """获取指定年份的休市日集合，缺失年份返回空集（仅跳周末）"""
    return CN_HOLIDAYS.get(year, set())

def is_trade_day(datestr, holidays=None):
    """是否为交易日（排除周末与法定休市日）"""
    if holidays is None:
        year = int(datestr[:4])
        holidays = _get_holidays(year)
    d = dt.date.fromisoformat(datestr)
    return d.weekday() < 5 and datestr not in holidays


def next_trade_day(datestr, holidays=None, max_step=30):
    """
    下一交易日：跳过周末 + 法定休市日。
    若 holidays 未覆盖该年份，退化为"仅跳周末"（安全降级，不会死循环）。
    """
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
    """上一交易日（同样跳周末+休市日）"""
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
    """
    半衰期指数权重（越近权重越大），用于滚动回归。
    返回长度 n 的权重数组（归一化，和为 1）。
    """
    if n <= 0:
        return np.array([])
    w = np.exp(-np.log(2) * np.arange(n - 1, -1, -1) / half_life)
    return w / w.sum()


def constrained_regression(y_win, x_win, weights, prior=None, prev_beta=None,
                           bnd_hi=1.0, lam1=0.01, lam2=0.03):
    """
    带约束加权最小二乘：β≥0，Σβ≤1，含平滑项(lam1，防跳变)与先验项(lam2，向 Q2 收缩)。
    失败时降级为 NNLS 并归一化，保证始终返回可用 β。
    """
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
    """
    调仓替代比例模型（方法文档 §二）：
        y = r_q2 + θ_pcb×(r_pcb − r_q2) + θ_m×(r_m − r_q2) + ε
    约束 0≤θ_pcb≤hi_pcb，0≤θ_m≤hi_m；lam_shrink 防止无证据时过度加 PCB。
    失败时返回 [0,0]（退化为纯 Q2 静态，即 P1）。
    """
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
    # 双保险：优化器边界偶有 1e-12 级越界，显式截断
    return np.array([min(max(res.x[0], 0.0), hi_pcb),
                     min(max(res.x[1], 0.0), hi_m)])


# ============================================================
# 2. 篮子/组合收益（纯函数，输入为已加载的 K 线字典）
# ============================================================
def basket_returns(kl_group, codes):
    """
    等权篮子日收益（百分数）。kl_group: {code: {date: close}}。
    自动跳过缺失日期的个股，返回 {date: pct}。
    """
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
    """
    稳健篮子收益：可选 MAD 截尾（复盘_20260820 的 P0 优化）。
    trim_mad=None 时等同 basket_returns；给定倍数 k 时剔除偏离中位数超过 k×MAD 的个股。
    """
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


# ------------------------------------------------------------
# 2.0 数据卫生：正式收盘价 vs 盘中实时价（优化意见 §6）
# ------------------------------------------------------------
def _is_group_map(obj):
    """区分 {group:{code:{date:px}}}（两层）与 {code:{date:px}}（一层）。"""
    if not isinstance(obj, dict):
        return False
    for v in obj.values():
        if not isinstance(v, dict):
            return False
        for vv in v.values():
            return isinstance(vv, dict)
        return False          # v 为空 dict：无法判定，按一层处理
    return False


def intraday_unsettled_dates(snapshot):
    """
    从 cache/intraday.json 快照解析"未结算日期"集合。

    快照约定：{"date": "2026-08-20", "settled": false, "prices": {...}, ...}
    settled=true 表示已用正式收盘价覆盖过，该日期不再算污染源。
    """
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
    """
    从 K 线字典中剔除"未结算日期"（盘中实时价被误写成收盘价的那些日子）。

    背景（真实事故，优化意见 §6）：取数脚本发现腾讯长窗口日 K 当天缺失时，会用
    实时行情接口补一条 out[TODAY]=当前价。若 14:00 抓取、20:00 官方净值才发布，
    则当天这条"收盘价"实为 14:00 的盘中价，却会作为正式收盘价进入滚动回归、
    误差统计与回测的样本，同时污染 β/θ 估计和 MAE —— 属于典型的隐性未来信息/
    错值混入。

    本函数是**幂等的防御性清洗**：无论上游取数脚本是否已修好，下游一律先剔除。
    支持两层 {group:{code:{date:px}}} 与一层 {code:{date:px}}。
    返回 (新字典, 剔除条数)；不修改入参。
    """
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
    """
    把盘中实时快照合并进 K 线的**内存副本**，供确实需要当天价格的计算使用。

    纪律：结果只在内存中流转，绝不回写 cache/klines.json —— 磁盘上的
    klines.json 永远只保存正式收盘价，这是"可复现回测"的前提。
    snapshot 结构：{"date": d, "prices": {group: {code: px}}}（或一层 {code: px}）。
    返回 (新字典, 写入条数)。
    """
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
    """
    【本模块唯一的 I/O 函数】载入 klines.json 并按 intraday.json 剔除未结算日期。

    core.py 其余部分保持纯函数（可无文件、无网络单测）；此处集中做一次装载，
    是为了避免主脚本/回测/反推/验证脚本各写一份清洗逻辑而产生漂移——
    "同一份数据在不同脚本里含义不同"正是本项目要消灭的头号可信度问题。

    返回 (klines, meta)，meta = {unsettled, stripped, settled, intraday_date, has_snapshot}
    """
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
    """
    Q2 披露组合日收益（百分数）。date_seq 需为升序交易日序列；
    返回长度 len(date_seq)-1 的数组，对应 date_seq[1:]。

    注意（历史 bug 回归点）：weights 必须是小数权重（0.0963），
    误用百分数权重(9.63)会把组合收益放大 100 倍。
    """
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
    """
    P4 层级组合的光通信腿：行业内部沿用 Q2 相对权重（而非等权）。

    与 q2_portfolio_returns 的区别：这里把权重**在行业内归一化**（除以权重之和），
    得到的是"这个行业本身涨了多少"，再由 β 决定该行业占多少仓位；
    q2_portfolio_returns 返回的是"占净值 83% 的组合贡献了多少收益"。
    两者混用会导致 P4 收益被系统性放大/缩小，故统一在此实现，禁止各处重写。

    返回 dict {date: pct}，覆盖 date_seq[1:]（缺数据的日子按可用权重重新归一）。
    """
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
    """前十大持仓覆盖率（占净值比例）。这是估值精度的物理上限，属核心指标。"""
    weights = TOP10_W if weights is None else weights
    return float(sum(weights.values()))


# ============================================================
# 3. 交易日历
# ============================================================
# 说明：is_trade_day / next_trade_day / prev_trade_day 已在前半段定义（使用多年份 CN_HOLIDAYS + _get_holidays），
# 此处不再重复定义。若需引用请直接使用 core.is_trade_day 等。


def market_session(now=None):
    """
    当前行情时段：pre_open / trading / lunch / closed / non_trade_day
    用于让页面/推送显式声明"估的是盘中还是下一交易日"，而不是靠猜。
    """
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
    """
    显式判定估算语义（用户反馈的"到底估的哪天"问题的根治点）。

    返回 dict：
      mode          settled / intraday / next_trading_day
      target_date   估算目标交易日
      nav_prev_date 基准净值日（必须是 target_date 的上一交易日）
      session       行情时段
      label         界面文案
    """
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


# ============================================================
# 4. 集成权重（含同源分组去重）
# ============================================================
def ensemble_weights(maes, groups=None, cap=MODEL_WEIGHT_CAP, floor=MAE_FLOOR,
                     power=MAE_POWER, gate=MAE_GATE, provisional=None,
                     provisional_cap=PROVISIONAL_WEIGHT_CAP):
    """
    分组集成权重（修复 P3/P4 同源重复计权 + 劣质模型淘汰）。

    做法：
      0. 淘汰闸门：MAE 明显劣于最优模型的直接剔除，不给残余权重；
      1. 按 MODEL_GROUPS 把幸存模型归组，同源模型进同一组；
      2. 组的代表误差 = 组内最小 MAE（该口径的最优代表）；
      3. 组间按 w_g ∝ 1/(MAE_g + floor)^power 分配；
      4. 组内按同一公式二次分配（同源模型共享该组总权重，不再各拿一份）；
      5. 单模型 cap 截断后重新归一化。

    为什么需要闸门（gate）：
      60 日样本外回测显示，纯 1/(MAE+floor) 加权会让 P1/P2 拿到 ~65% 权重，
      集成 MAE 0.516% 反而输给最优单模型 0.486%。提高 power 只能缓解、
      不能根治（MAE 随 power 单调下降说明最优解在"向最优模型集中"方向）。
      闸门直接剔除劣质模型，是组合预测里的标准做法（model screening）。

    power 控制权重锐度：power=2 相当于逆方差加权（误差近似独立时的统计最优解）。
    gate=1.30 表示"MAE 超过最优模型 1.30 倍的模型不参与"；gate=None/0 关闭闸门。
    两者取值均由 tests/backtest_v3.py --tune 用"前段选参、后段独立验证"选出。

    参数 maes: {model_name: mae_pct}
    返回 (weights: {model: w}, info: {group_weights, groups, group_mae, dropped})
    """
    if not maes:
        return {}, {"group_weights": {}, "groups": {}, "group_mae": {}, "dropped": []}
    groups = MODEL_GROUPS if groups is None else groups

    def score(mae):
        return 1.0 / ((max(mae, 0.0) + floor) ** power)

    # 0) 淘汰闸门：用 floor 平移，避免最优 MAE≈0 时闸门失效
    #    契约：被淘汰模型**保留键、权重显式为 0**，不从字典里消失，
    #    否则下游 sum(w[m]*pred[m]) 会 KeyError，且 JSON 里看不出它被淘汰。
    all_models = list(maes)
    orig_maes = dict(maes)
    dropped = []
    if gate and gate > 0 and len(maes) > 1:
        best_mae = min(maes.values())
        thr = (max(best_mae, 0.0) + floor) * gate
        kept = {m: v for m, v in maes.items() if (max(v, 0.0) + floor) <= thr}
        if kept:                       # 至少保留最优模型，绝不会清空
            dropped = sorted(set(maes) - set(kept))
            maes = kept

    # 1) 归组
    g_of = {m: groups.get(m, m) for m in maes}
    members = {}
    for m, g in g_of.items():
        members.setdefault(g, []).append(m)

    # 2) 组代表误差：组内最小 MAE（该口径的最优代表）
    g_mae = {g: min(maes[m] for m in ms) for g, ms in members.items()}

    # 3) 组间权重
    g_raw = {g: score(g_mae[g]) for g in g_mae}
    g_tot = sum(g_raw.values())
    g_w = {g: g_raw[g] / g_tot for g in g_raw}

    # 4) 组内二次分配
    w = {}
    for g, ms in members.items():
        inner_raw = {m: score(maes[m]) for m in ms}
        inner_tot = sum(inner_raw.values())
        for m in ms:
            w[m] = g_w[g] * (inner_raw[m] / inner_tot)

    # 5) cap 截断 + 归一化（provisional 模型用更严的上限）
    #    注意：若幸存模型全是 provisional，该上限在数学上不可能满足
    #    （权重和必须为 1），此时退回普通 cap，否则迭代不收敛。
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

    # 6) 补回被闸门淘汰的模型/分组，权重显式 0（保持键完整，便于下游与审计）
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


# ============================================================
# 5. 偏差修正（防滞后）
# ============================================================
def ewma(values, half_life=10):
    """指数加权均值（末位权重最高）"""
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return 0.0
    w = half_life_weights(len(arr), half_life)
    return float(np.sum(w * arr) / np.sum(w))


def bias_correction(errs, hl=10, max_abs=1.5, div_tol=0.35):
    """
    历史偏差修正（方法文档 §六）+ 防滞后（本轮新增）。

    errs: 样本外误差序列（实际 − 模型，百分数，按时间升序）
    输出 dict：
      med20 / med40   近 20、40 日误差中位数
      ewma            半衰期 hl 的指数加权均值（对最近变化更敏感，抗滞后）
      applied         实际采用的修正值
      divergence      三个估计量的极差（衡量偏差本身是否稳定）
      shrink          分歧过大时的收缩系数（1.0=不收缩）
      stable          偏差是否稳定（分歧 < div_tol）

    逻辑：以 EWMA 为主（抗滞后），med20 为辅，两者取均值；
         当三估计量分歧 > div_tol 时按比例收缩修正幅度，
         避免在偏差结构切换期把错误修正放大（并触发置信度下调）。
    """
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
        # 分歧越大收缩越狠，下限 0.3
        shrink = max(0.3, div_tol / divergence)
    raw = 0.5 * ew + 0.5 * m20
    applied = raw * shrink
    applied = float(max(-max_abs, min(max_abs, applied)))
    return {"med20": round(m20, 4), "med40": round(m40, 4), "ewma": round(ew, 4),
            "raw": round(raw, 4), "applied": round(applied, 4),
            "divergence": round(divergence, 4), "shrink": round(shrink, 3),
            "stable": divergence <= div_tol, "n": int(arr.size)}


# ============================================================
# 6. 连续置信度评分 0-100
# ============================================================
def confidence_score(mae, spread, theta_stability, bias_divergence,
                     data_quality=1.0, coverage=None):
    """
    连续置信度评分（替代原三档硬阈值），满分 100：
      样本外 MAE      40 分   (MAE 0.3%→满分, 1.5%→0 分)
      模型分歧        25 分   (spread 0.5pp→满分, 4pp→0 分)
      θ 稳定性        15 分   (变动 0.03→满分, 0.20→0 分)
      偏差稳定性      10 分   (divergence 0.15→满分, 0.8→0 分)
      数据质量+覆盖   10 分   (data_quality × 覆盖率修正)

    返回 (score:int, grade:str, detail:dict)
    """
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
    cov_factor = min(1.0, max(0.0, (cov - 0.5) / 0.4))   # 0.5→0, 0.9→1
    s_data = 10.0 * max(0.0, min(1.0, data_quality)) * (0.5 + 0.5 * cov_factor)

    score = s_mae + s_spread + s_theta + s_bias + s_data
    score = int(round(max(0.0, min(100.0, score))))
    grade = "高" if score >= 75 else ("中等" if score >= 50 else "低")
    return score, grade, {
        "mae": round(s_mae, 1), "spread": round(s_spread, 1),
        "theta": round(s_theta, 1), "bias": round(s_bias, 1),
        "data": round(s_data, 1),
    }


# ============================================================
# 7. 数据源校验 / 降级
# ============================================================
def validate_market_data(nav, klines, baskets=None, min_nav_rows=120,
                         min_kline_rows=120, max_daily_abs_pct=25.0,
                         today=None):
    """
    数据源体检。返回 dict：
      ok           是否可用于生产估值
      score        数据质量分 0~1（喂给 confidence_score）
      errors       致命问题（ok=False）
      warnings     非致命问题（降级但可用）
      detail       各项统计
    """
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

    # 净值日收益异常
    bad_ret = []
    for i in range(1, len(navs)):
        if navs[i - 1]:
            r = (navs[i] / navs[i - 1] - 1) * 100
            if abs(r) > max_daily_abs_pct:
                bad_ret.append((dates[i], round(r, 2)))
    if bad_ret:
        warnings.append(f"净值日收益异常({len(bad_ret)}条，如 {bad_ret[:2]})")
    detail["abnormal_nav_returns"] = bad_ret[:5]

    # 数据新鲜度
    if dates:
        detail["nav_latest"] = dates[-1]
        ref = dt.date.today() if today is None else dt.date.fromisoformat(today)
        lag_days = (ref - dt.date.fromisoformat(dates[-1])).days
        detail["nav_lag_days"] = lag_days
        if lag_days > 5:
            warnings.append(f"净值数据滞后 {lag_days} 天，可能未及时更新")

    # 篮子完整度
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

    # 质量分：致命=0；每个 warning 扣 0.12
    if errors:
        score = 0.0
    else:
        score = max(0.3, 1.0 - 0.12 * len(warnings))
    return {"ok": not errors, "score": round(score, 3),
            "errors": errors, "warnings": warnings, "detail": detail}


def check_date_consistency(target_date, nav_prev_date, nav_dates,
                           official_date=None, holidays=None):
    """
    日期错配自检（曾出过"nav_prev 取到目标日自身净值"的语义错位 bug）。
    返回 (ok:bool, problems:list)
    """
    problems = []
    if nav_prev_date and target_date:
        expect = prev_trade_day(target_date, holidays)
        if nav_prev_date != expect:
            # 允许基准日更早（数据缺该日），但绝不允许 >= 目标日
            if nav_prev_date >= target_date:
                problems.append(
                    f"基准净值日({nav_prev_date}) 不早于目标日({target_date})，语义错位")
            else:
                problems.append(
                    f"基准净值日({nav_prev_date}) 非目标日上一交易日(应为 {expect})")
    if nav_dates and target_date and target_date in nav_dates:
        # 目标日净值已在 nav 序列里 → 基准必须是它之前那条
        i = nav_dates.index(target_date)
        if i >= 1 and nav_prev_date != nav_dates[i - 1]:
            problems.append(
                f"目标日净值已公布，基准应取 {nav_dates[i-1]}，实际 {nav_prev_date}")
    if official_date and target_date:
        # 允许：官方净值日是目标日的上一交易日（估算下一交易日），或两者相同（估算当日）
        expect_same = official_date == target_date
        expect_prev = official_date == prev_trade_day(target_date, holidays)
        if not (expect_same or expect_prev):
            problems.append(f"官方净值日({official_date}) 与目标日({target_date}) 关系异常，预期相同或为上一交易日({prev_trade_day(target_date, holidays)})")
    if target_date and not is_trade_day(target_date, holidays):
        problems.append(f"目标日 {target_date} 非交易日")
    return (not problems), problems


# ============================================================
# 8. 误差指标
# ============================================================
def error_metrics(preds, actuals):
    """MAE / RMSE / 最大绝对误差 / 方向准确率 / 平均偏差"""
    p = np.asarray(preds, dtype=float)
    a = np.asarray(actuals, dtype=float)
    if p.size == 0 or p.size != a.size:
        return {"n": 0, "mae": None, "rmse": None, "max_abs": None,
                "hit": None, "bias": None}
    e = p - a
    hit = float(np.mean([(pi > 0) == (ai > 0) for pi, ai in zip(p, a)]))
    return {"n": int(p.size),
            "mae": round(float(np.mean(np.abs(e))), 4),
            "rmse": round(float(np.sqrt(np.mean(e ** 2))), 4),
            "max_abs": round(float(np.max(np.abs(e))), 4),
            "hit": round(hit, 4),
            "bias": round(float(np.mean(e)), 4)}


def classify_regime(actual_pct, up=1.5, down=-1.5):
    """市况分类：急涨 / 急跌 / 震荡（用于分市况回测）"""
    if actual_pct >= up:
        return "急涨"
    if actual_pct <= down:
        return "急跌"
    return "震荡"


# ============================================================
# 9. PCB 三级信号（严格版）
# ============================================================
def trend_slope(values):
    """简单线性趋势斜率（每期变化量）。样本<3 返回 0。"""
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
    """
    PCB 调仓三级信号（方法文档 §七 的严格实现）。

    v3 旧实现只用"多窗口为正 + 10d>60d"两个宽松代理就可判强信号，
    会让界面打出"PCB调仓概率较高"的实质断言。v4 要求逐条证据：

      C1 多窗口一致为正          所有窗口 β_pcb > 0
      C2 替代比例连续上升        θ_pcb 近期线性斜率 > 0
      C3 增量验证               加入 PCB 因子后样本外 MAE 下降
      C4 独立证据               个股反推的 PCB 暴露 > 3%
      C5 多篮子一致             不同 PCB 篮子结论同向（缺失时视为未通过）

    强信号 = C1 且 C2 且 C3 且 C4（且 C5 若可得）
    中信号 = C1 且 (C2/C3/C4 中至少 2 项)
    弱信号 = C1 或部分条件
    无信号 = 无数据

    返回 (level:str, evidence:dict)
    """
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


def market_regime(kl_market, lookback=20, vol_lookback=10, bull_thr=5.0, bear_thr=-5.0):
    """市场状态感知（牛熊/震荡）：中证1000 收盘趋势 + 波动率。
    兼容 BASKETS["market"] 的 {code: {date: close}} 嵌套结构。
    返回 {regime: bull/bear/range, trend_pct, vol_pct, n}。
    regime 供模型/页面做状态感知：bull 追涨环境、bear 防御环境、range 中性。"""
    try:
        if isinstance(kl_market, dict) and kl_market:
            _inner = kl_market
            _first = next(iter(_inner.values()))
            if isinstance(_first, dict):              # {code: {date: close}}
                _inner = next(iter(_inner.values()))
            ds = sorted(_inner)
            if len(ds) < lookback + 2:
                return {"regime": "range", "trend_pct": 0.0, "vol_pct": 0.0, "n": len(ds)}
            closes = [float(_inner[d]) for d in ds[-(lookback + 1):]]
            trend = (closes[-1] / closes[0] - 1) * 100
            rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
            vol = float(np.std(rets[-vol_lookback:])) * 100
            regime = "bull" if trend >= bull_thr else ("bear" if trend <= bear_thr else "range")
            return {"regime": regime, "trend_pct": round(trend, 2),
                    "vol_pct": round(vol, 2), "n": len(closes)}
    except Exception:
        pass
    return {"regime": "range", "trend_pct": 0.0, "vol_pct": 0.0, "n": 0}
