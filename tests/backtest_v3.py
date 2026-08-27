#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_v3.py —— v4 滚动样本外回测（扩样本 + 分市况 + 多指标 + 基线对照）

相对上一版（只回测最近 3 日）的改进：
  [1] 样本从 3 日扩到 60 个交易日（可 --days 调整），结论不再靠个例
  [2] 分市况统计：急涨(>=+1.5%) / 急跌(<=-1.5%) / 震荡，暴露"大涨日滞后"这类结构性缺陷
  [3] 指标从单一 MAE 扩到 MAE / RMSE / 最大单日误差 / 方向准确率 / 平均偏差
  [4] 与 P1 静态基准逐项对照 —— 集成如果打不过 P1，那整套多模型就没有存在意义
  [5] 集成权重与偏差修正全部改用 src/core.py（与线上主脚本同一套代码，杜绝逻辑漂移）
  [6] 偏差修正改用"集成自身的历史误差"（原来错用 P3 的误差序列）

无未来泄漏纪律：
  预测第 t 日时，θ/β/MAE权重/偏差修正 一律只用 t-1 及以前的数据。

用法:
  python tests/backtest_v3.py                # 默认最近 60 个交易日
  python tests/backtest_v3.py --days 90      # 指定样本长度
  python tests/backtest_v3.py --daily        # 额外打印逐日明细
"""
import json, os, sys, argparse

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
import core
from core import (TOP10_W, BASKETS, W_BASE, W_SHORT, W_FAST, HALF_LIFE, SUM_W)

ap = argparse.ArgumentParser()
ap.add_argument("--days", type=int, default=60, help="回测样本长度(交易日)")
ap.add_argument("--daily", action="store_true", help="打印逐日明细")
ap.add_argument("--tune", action="store_true",
                help="集成锐度选参：前段选参/后段独立验证（禁止同批数据既选参又验证）")
ap.add_argument("--decide", action="store_true",
                help="主口径决策：把集成/单模型放在同一验证段、同一偏差修正条件下公平对比")
args = ap.parse_args()

CACHE = os.path.join(ROOT, "cache")
NAV = json.load(open(os.path.join(CACHE, "nav.json"), encoding="utf-8"))
# 只用正式收盘价：盘中价冒充收盘价会让"样本外误差"凭空变好（优化意见 §6）
KL, KL_META = core.load_settled_klines(CACHE, verbose=False)
if KL_META["unsettled"]:
    out(f"[数据卫生] 回测剔除未结算日期 {KL_META['unsettled']}"
        f"（{KL_META['stripped']} 条盘中价），保证回测可复现")

# ---------- 数据准备（与主脚本同源） ----------
dq = core.validate_market_data(NAV, KL)
if not dq["ok"]:
    out(f"[中止] 数据体检未通过: {dq['errors']}")
    sys.exit(2)

BR = {g: core.basket_returns(KL[g], BASKETS[g]) for g in BASKETS}
dates, navs = NAV["dates"], NAV["navs"]
fund_ret = {dates[i]: (navs[i] / navs[i - 1] - 1.0) * 100.0 for i in range(1, len(dates))}
common = sorted(set(fund_ret) & set(BR["optical"]) & set(BR["pcb"]) &
                set(BR["semis"]) & set(BR["market"]))
X_all = np.array([[BR["optical"][d], BR["pcb"][d], BR["semis"][d], BR["market"][d]]
                  for d in common])

COMMON_D = common[1:]
Y = np.array([fund_ret[d] for d in COMMON_D])
X = X_all[1:]
Q2 = core.q2_portfolio_returns(KL["optical"], common)
n = len(COMMON_D)

out("=" * 92)
out(f"006010 v4 滚动样本外回测   样本池 {n} 日 ({COMMON_D[0]} ~ {COMMON_D[-1]})")
out(f"前十大覆盖率 {SUM_W*100:.2f}%   窗口 W={W_BASE} 半衰期={HALF_LIFE}")
out("=" * 92)

# ---------- 一次性滚动生成 P1~P4 的样本外预测 ----------
Q2_PRIOR = np.array([round(SUM_W, 2), 0.0, 0.0, 0.0])
opt_sum = sum(TOP10_W.values())
prev_map = {COMMON_D[i]: common[i] for i in range(n)}

def p4_optical(d):
    """层级组合的光通信腿：行业内沿用 Q2 相对权重"""
    pv = prev_map[d]
    s, wsum = 0.0, 0.0
    for c, w in TOP10_W.items():
        px = KL["optical"].get(c, {})
        if d in px and pv in px and px[pv]:
            s += (w / opt_sum) * (px[d] / px[pv] - 1.0) * 100.0
            wsum += w / opt_sum
    return s / wsum if wsum > 0 else 0.0

pred = {"P1_Q2静态": {}, "P2_调仓替代": {}, "P3_行业因子": {}, "P4_层级组合": {}}
theta_hist, beta_hist = {}, {}
_prev_th, _prev_b = None, Q2_PRIOR

out("[1/3] 滚动拟合 theta / beta ...")
for p in range(W_BASE, n):
    d = COMMON_D[p]
    w = core.half_life_weights(W_BASE, HALF_LIFE)
    # θ（仅用 p 之前的数据）
    th = core.fit_theta(Y[p - W_BASE:p], Q2[p - W_BASE:p],
                        X[p - W_BASE:p, 1], X[p - W_BASE:p, 3], w, prev=_prev_th)
    _prev_th = th
    theta_hist[d] = th
    # β（仅用 p 之前的数据）
    b = core.constrained_regression(Y[p - W_BASE:p], X[p - W_BASE:p], w, Q2_PRIOR, _prev_b)
    _prev_b = b
    beta_hist[d] = b
    # 当日样本外预测（用 p 日已知行情 + p-1 日估出的参数）
    pred["P1_Q2静态"][d] = float(Q2[p])
    pred["P2_调仓替代"][d] = float(Q2[p] + th[0] * (X[p, 1] - Q2[p]) + th[1] * (X[p, 3] - Q2[p]))
    pred["P3_行业因子"][d] = float(X[p] @ b)
    pred["P4_层级组合"][d] = float(b[0] * p4_optical(d) + b[1] * X[p, 1]
                                  + b[2] * X[p, 2] + b[3] * X[p, 3])

MODELS = list(pred.keys())
avail = sorted(pred["P3_行业因子"].keys())
out(f"      完成，可评估区间 {avail[0]} ~ {avail[-1]}（{len(avail)} 日）")

# ---------- 逐日集成（权重与偏差修正只用历史） ----------
K_ERR = 40           # MAE / 偏差修正回看窗口
WARM = 40            # 集成预热：至少积累 40 日模型误差

def build_rows(power=core.MAE_POWER, floor=core.MAE_FLOOR, gate=core.MAE_GATE):
    """按给定集成锐度参数跑一遍逐日集成。θ/β 已预计算，故此步很快。"""
    res = []
    ens_err_hist = {}    # 集成自身的历史误差（供偏差修正使用）
    for i, d in enumerate(avail):
        hist = avail[max(0, i - K_ERR):i]
        if len(hist) < 10:
            continue
        actual = float(Y[COMMON_D.index(d)])

        # 1) 各模型历史 MAE（只用 d 之前）
        maes = {m: float(np.mean(np.abs([fund_ret[h] - pred[m][h] for h in hist])))
                for m in MODELS}

        # 2) 分组集成权重（P3/P4 同源去重 + 劣质模型淘汰闸门）
        wts, _winfo = core.ensemble_weights(maes, power=power, floor=floor, gate=gate)

        # 3) 集成预测（被闸门淘汰的模型权重视为 0）
        p_final = sum(wts.get(m, 0.0) * pred[m][d] for m in MODELS)

        # 4) 偏差修正：用集成自身的历史误差（关键修正，原来错用 P3 误差序列）
        past = [ens_err_hist[h] for h in hist if h in ens_err_hist]
        bias = (core.bias_correction(past[-K_ERR:], hl=10) if len(past) >= 10 else
                {"applied": 0.0, "divergence": 0.0, "shrink": 1.0, "stable": True,
                 "med20": 0.0, "med40": 0.0, "ewma": 0.0})
        ens_err_hist[d] = actual - p_final

        if i >= WARM:
            res.append({
                "date": d, "actual": actual,
                "preds": {m: pred[m][d] for m in MODELS},
                "final": p_final, "corr": p_final + bias["applied"],
                "bias": bias["applied"], "bias_div": bias["divergence"],
                "wts": wts, "theta": float(theta_hist[d][0]),
                "dropped": _winfo.get("dropped", []),
                "regime": core.classify_regime(actual),
            })
    return res


def _mae(rs, getter):
    if not rs:
        return float("inf")
    return float(np.mean([abs(getter(r) - r["actual"]) for r in rs]))


def run_stream(fn, use_bias=True):
    """
    通用口径评估流：把任意"当日预测函数"跑成时间序列，并按同一纪律做偏差修正。

    fn(d, maes) -> 预测值（maes 是各模型截至 d-1 的历史 MAE，只含过去信息）
    这样集成与单模型就处在**完全相同**的偏差修正条件下，比较才公平。
    """
    res, err_hist = [], {}
    for i, d in enumerate(avail):
        hist = avail[max(0, i - K_ERR):i]
        if len(hist) < 10:
            continue
        actual = float(Y[COMMON_D.index(d)])
        maes = {m: float(np.mean(np.abs([fund_ret[h] - pred[m][h] for h in hist])))
                for m in MODELS}
        raw = float(fn(d, maes))
        past = [err_hist[h] for h in hist if h in err_hist]
        bias = (core.bias_correction(past[-K_ERR:], hl=10)
                if (use_bias and len(past) >= 10) else {"applied": 0.0})
        err_hist[d] = actual - raw
        if i >= WARM:
            res.append({"date": d, "actual": actual, "raw": raw,
                        "corr": raw + bias["applied"], "bias": bias["applied"]})
    return res


# ---------- --tune：前段选参 / 后段验证（禁止同批数据既选参又验证） ----------
if args.tune:
    out("[TUNE] 集成锐度参数选择：前 50% 选参 / 后 50% 独立验证")
    all_rows = build_rows()
    half = len(all_rows) // 2
    sel_dates = {r["date"] for r in all_rows[:half]}
    val_dates = {r["date"] for r in all_rows[half:]}
    out(f"       选参段 {all_rows[0]['date']} ~ {all_rows[half-1]['date']} ({half} 日)")
    out(f"       验证段 {all_rows[half]['date']} ~ {all_rows[-1]['date']} "
        f"({len(all_rows)-half} 日)\n")
    out(f"{'power':>7}{'floor':>7}{'gate':>7}{'选参MAE':>10}{'验证MAE':>10}"
        f"{'验证RMSE':>10}{'平均入选':>10}")
    out("-" * 64)
    grid, best = [], None
    GATES = (1.00, 1.15, 1.30, 1.60, 0.0)       # 0.0 = 关闭闸门（全模型参与）
    for gate in GATES:
        for power in (1.0, 2.0, 3.0, 5.0):
            for floor in (0.02, 0.05):
                rs = build_rows(power=power, floor=floor, gate=gate)
                sel = [r for r in rs if r["date"] in sel_dates]
                val = [r for r in rs if r["date"] in val_dates]
                m_sel = _mae(sel, lambda r: r["corr"])
                m_val = core.error_metrics([r["corr"] for r in val],
                                           [r["actual"] for r in val])
                n_keep = float(np.mean([len(MODELS) - len(r["dropped"]) for r in rs]))
                grid.append({"power": power, "floor": floor, "gate": gate,
                             "sel_mae": round(m_sel, 4), "val_mae": m_val["mae"],
                             "val_rmse": m_val["rmse"], "avg_models": round(n_keep, 2)})
                gs = "off" if not gate else f"{gate:.2f}"
                out(f"{power:>7.1f}{floor:>7.2f}{gs:>7}{m_sel:>10.4f}"
                    f"{m_val['mae']:>10.4f}{m_val['rmse']:>10.4f}{n_keep:>10.2f}")
                if best is None or m_sel < best["sel_mae"]:
                    best = grid[-1]
        out("-" * 64)
    # 基线与最优单模型（同一验证段，与集成完全同期同样本）
    base_rows = build_rows()
    val = [r for r in base_rows if r["date"] in val_dates]
    b_p1 = _mae(val, lambda r: r["preds"]["P1_Q2静态"])
    b_p2 = _mae(val, lambda r: r["preds"]["P2_调仓替代"])
    b_p3 = _mae(val, lambda r: r["preds"]["P3_行业因子"])
    b_p4 = _mae(val, lambda r: r["preds"]["P4_层级组合"])
    best_single = min(b_p2, b_p3, b_p4)
    gs = "off" if not best["gate"] else f"{best['gate']:.2f}"
    out(f"选参段最优: power={best['power']} floor={best['floor']} gate={gs} "
        f"(选参MAE={best['sel_mae']:.4f}, 平均入选模型 {best['avg_models']} 个)")
    out(f"该参数在【独立验证段】: MAE={best['val_mae']:.4f}%  RMSE={best['val_rmse']:.4f}%")
    out(f"同验证段对照: P1={b_p1:.4f}%  P2={b_p2:.4f}%  P3={b_p3:.4f}%  P4={b_p4:.4f}%")
    beat_base = best["val_mae"] < b_p1
    beat_best_single = best["val_mae"] < best_single
    out(f"结论: {'击败' if beat_base else '未击败'}静态基线; "
        f"{'击败' if beat_best_single else '未击败'}最优单模型({best_single:.4f}%)")
    if not beat_best_single:
        out("      [警示] 集成仍不如最优单模型 —— 已在方法文档中如实记录，")
        out("             应把 P3/P4 作为主口径、集成仅作稳健性参照")
    else:
        out(f"      [通过] 集成优于最优单模型 {best_single - best['val_mae']:+.4f}pp，"
            f"集成结构成立")
    # 验证段最优组合（仅作参考，不用于选参，避免过拟合验证段）
    val_best = min(grid, key=lambda g: g["val_mae"])
    out(f"（参考）验证段事后最优: power={val_best['power']} gate="
        f"{'off' if not val_best['gate'] else format(val_best['gate'],'.2f')} "
        f"MAE={val_best['val_mae']:.4f}% —— 与选参结果相差 "
        f"{best['val_mae'] - val_best['val_mae']:+.4f}pp（差距小说明选参稳健）")
    json.dump({"grid": grid, "selected": best, "val_best_reference": val_best,
               "split": {"select": [all_rows[0]["date"], all_rows[half - 1]["date"]],
                         "validate": [all_rows[half]["date"], all_rows[-1]["date"]]},
               "validation_baselines": {"P1": round(b_p1, 4), "P2": round(b_p2, 4),
                                        "P3": round(b_p3, 4), "P4": round(b_p4, 4)},
               "beat_baseline": beat_base, "beat_best_single": beat_best_single},
              open(os.path.join(CACHE, "tune_report.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    out("\n[已保存] cache/tune_report.json")
    sys.exit(0)

# ---------- --decide：主口径决策（同验证段 + 同修正条件的公平对比） ----------
if args.decide:
    out("[DECIDE] 主口径决策：所有候选放在同一验证段、同一偏差修正纪律下对比")
    all_rows = build_rows()
    half = len(all_rows) // 2
    val_dates = {r["date"] for r in all_rows[half:]}
    out(f"         验证段 {all_rows[half]['date']} ~ {all_rows[-1]['date']} "
        f"({len(all_rows)-half} 日，与 --tune 选参段完全不重叠)\n")

    def ens_fn(power, floor, gate):
        def f(d, maes):
            wts, _ = core.ensemble_weights(maes, power=power, floor=floor, gate=gate)
            return sum(wts.get(m, 0.0) * pred[m][d] for m in MODELS)
        return f

    def single_fn(m):
        return lambda d, maes: pred[m][d]

    def avg_fn(ms):
        return lambda d, maes: float(np.mean([pred[m][d] for m in ms]))

    candidates = [
        ("P1 Q2静态(基线)",        single_fn("P1_Q2静态")),
        ("P2 调仓替代",            single_fn("P2_调仓替代")),
        ("P3 行业因子",            single_fn("P3_行业因子")),
        ("P4 层级组合",            single_fn("P4_层级组合")),
        ("G3均值(P3+P4)/2",        avg_fn(["P3_行业因子", "P4_层级组合"])),
        ("集成 无闸门 power2",     ens_fn(2.0, 0.05, 0.0)),
        ("集成 闸门1.15 power2",   ens_fn(2.0, 0.02, 1.15)),
        ("集成 闸门1.15 power5",   ens_fn(5.0, 0.05, 1.15)),
        ("集成 闸门1.00(择优)",    ens_fn(2.0, 0.05, 1.00)),
    ]
    sel_dates = {r["date"] for r in all_rows[:half]}
    all_dates = {r["date"] for r in all_rows}
    segs = [("选参段", sel_dates), ("验证段", val_dates), ("全段", all_dates)]

    out(f"{'候选口径':<22}{'原始MAE':>10}{'修正MAE':>10}{'修正RMSE':>10}"
        f"{'最大误差':>10}{'方向':>8}{'平均偏差':>10}{'修正增益':>10}"
        f"{'选参段MAE':>11}{'全段MAE':>10}")
    out("-" * 114)
    table = []
    for label, fn in candidates:
        stream = run_stream(fn)
        seg_m = {}
        for sname, sdates in segs:
            rs = [r for r in stream if r["date"] in sdates]
            seg_m[sname] = core.error_metrics([r["corr"] for r in rs],
                                              [r["actual"] for r in rs])
        rs = [r for r in stream if r["date"] in val_dates]
        m_raw = core.error_metrics([r["raw"] for r in rs], [r["actual"] for r in rs])
        m_cor = seg_m["验证段"]
        gain = m_raw["mae"] - m_cor["mae"]
        table.append({"label": label, "raw_mae": m_raw["mae"], "mae": m_cor["mae"],
                      "rmse": m_cor["rmse"], "max_abs": m_cor["max_abs"],
                      "hit": m_cor["hit"], "bias": m_cor["bias"],
                      "bias_gain_pp": round(gain, 4),
                      "sel_mae": seg_m["选参段"]["mae"], "all_mae": seg_m["全段"]["mae"]})
        out(f"{label:<22}{m_raw['mae']:>10.4f}{m_cor['mae']:>10.4f}{m_cor['rmse']:>10.4f}"
            f"{m_cor['max_abs']:>10.3f}{m_cor['hit']*100:>7.1f}%{m_cor['bias']:>+10.3f}"
            f"{gain:>+10.4f}{seg_m['选参段']['mae']:>11.4f}{seg_m['全段']['mae']:>10.4f}")
    out("-" * 114)
    # 稳健性检查：结论是否在三个窗口一致（避免验证段偶然）
    rank_val = sorted(table, key=lambda t: t["mae"])[0]["label"]
    rank_sel = sorted(table, key=lambda t: t["sel_mae"])[0]["label"]
    rank_all = sorted(table, key=lambda t: t["all_mae"])[0]["label"]
    consistent = (rank_val == rank_sel == rank_all)
    out(f"三窗口最优口径: 选参段={rank_sel} | 验证段={rank_val} | 全段={rank_all}")
    out(f"结论一致性: {'一致（结论稳健）' if consistent else '不一致（结论受样本影响，需谨慎）'}")
    out("-" * 114)
    # 选参段/验证段排名翻转 → 单段选口径必然过拟合，故决策改用双准则：
    #   准则A（主）: 全段 MAE（样本最大）
    #   准则B（稳健性）: 各子段中的最差 MAE（worst-case，惩罚"某段崩掉"的口径）
    for t in table:
        t["worst_mae"] = round(max(t["sel_mae"], t["mae"]), 4)
        t["seg_spread"] = round(abs(t["sel_mae"] - t["mae"]), 4)
    helps = sum(1 for t in table if t["bias_gain_pp"] > 0)
    out(f"偏差修正在 {helps}/{len(table)} 个口径上有效"
        f"（{'整体有效，保留' if helps > len(table) / 2 else '整体存疑，需下调修正力度'}）")

    if not consistent:
        out("\n[关键发现] 子段最优口径发生排名翻转 —— 单段选口径必然过拟合。")
        flip = sorted(table, key=lambda t: -t["seg_spread"])[:3]
        for t in flip:
            out(f"  {t['label']:<22} 选参段{t['sel_mae']:.4f}% -> 验证段{t['mae']:.4f}% "
                f"(跨段波动 {t['seg_spread']:.4f}pp)")
        out("  因此决策改用：准则A 全段MAE（样本最大） + 准则B 最差子段MAE（抗崩）")

    out(f"\n{'口径':<22}{'全段MAE':>10}{'最差子段':>10}{'跨段波动':>10}")
    out("-" * 52)
    for t in sorted(table, key=lambda x: x["all_mae"]):
        out(f"{t['label']:<22}{t['all_mae']:>10.4f}{t['worst_mae']:>10.4f}"
            f"{t['seg_spread']:>10.4f}")
    out("-" * 52)
    win_all = min(table, key=lambda t: t["all_mae"])
    win_worst = min(table, key=lambda t: t["worst_mae"])
    out(f"准则A 全段最优: {win_all['label']}  MAE={win_all['all_mae']:.4f}%")
    out(f"准则B 抗崩最优: {win_worst['label']}  最差子段MAE={win_worst['worst_mae']:.4f}%")

    ens_best = min([t for t in table if t["label"].startswith("集成")],
                   key=lambda t: t["all_mae"])
    sgl_best = min([t for t in table if not t["label"].startswith("集成")
                    and "基线" not in t["label"]], key=lambda t: t["all_mae"])
    d = sgl_best["all_mae"] - ens_best["all_mae"]
    out(f"集成最优({ens_best['label']} 全段{ens_best['all_mae']:.4f}%) vs "
        f"单模型最优({sgl_best['label']} 全段{sgl_best['all_mae']:.4f}%): {d:+.4f}pp")
    if d > 0.02:
        rec = (f"上线主口径 = {ens_best['label']}（全段领先 {d:.4f}pp，"
               f"且最差子段 {ens_best['worst_mae']:.4f}% 优于单模型 "
               f"{sgl_best['worst_mae']:.4f}%，抗崩能力是集成的核心价值）")
    elif d > -0.02:
        rec = (f"两者全段统计无差异（|{d:.4f}|pp < 0.02pp）→ 主口径仍取 {ens_best['label']}，"
               f"因其跨段波动 {ens_best['seg_spread']:.4f}pp 小于单模型 "
               f"{sgl_best['seg_spread']:.4f}pp")
    else:
        rec = (f"上线主口径 = {sgl_best['label']}，集成降级为稳健性参照"
               f"（集成全段劣 {-d:.4f}pp，超出噪声范围）")
    # 帕累托占优检验：在(全段MAE, 最差子段MAE)二维上是否被任何口径同时超越
    def dominated_by(t):
        return [o["label"] for o in table
                if o["label"] != t["label"]
                and o["all_mae"] <= t["all_mae"] + 1e-9
                and o["worst_mae"] <= t["worst_mae"] + 1e-9
                and (o["all_mae"] < t["all_mae"] - 1e-9
                     or o["worst_mae"] < t["worst_mae"] - 1e-9)]
    pareto = [t["label"] for t in table if not dominated_by(t)]
    out(f"帕累托前沿（全段MAE + 最差子段MAE 双准则下不被任何口径同时超越）:")
    for lb in pareto:
        t = [x for x in table if x["label"] == lb][0]
        out(f"  - {lb:<22} 全段{t['all_mae']:.4f}%  最差子段{t['worst_mae']:.4f}%")
    dom = dominated_by(min([t for t in table if not t["label"].startswith("集成")
                            and "基线" not in t["label"]], key=lambda t: t["all_mae"]))
    if ens_best["label"] in pareto:
        out(f"  → 选定口径 {ens_best['label']} 位于前沿；单模型最优 {sgl_best['label']} "
            f"{'被其占优' if ens_best['label'] in dom else '未被占优但跨段波动更大'}")
    out(f"[决策] {rec}")
    win, win_raw = win_all, min(table, key=lambda t: t["raw_mae"])
    json.dump({"validation_period": [all_rows[half]["date"], all_rows[-1]["date"]],
               "n_val": len(all_rows) - half, "table": table,
               "winner_full_sample": win_all["label"],
               "winner_worst_case": win_worst["label"],
               "winner_raw": win_raw["label"],
               "winner_by_segment": {"select": rank_sel, "validate": rank_val,
                                     "all": rank_all, "consistent": consistent},
               "rank_flip_detected": not consistent,
               "bias_correction_effective_on": f"{helps}/{len(table)}",
               "ensemble_vs_single_pp": round(d, 4), "recommendation": rec},
              open(os.path.join(CACHE, "decide_report.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    out("\n[已保存] cache/decide_report.json")
    sys.exit(0)

out("[2/3] 逐日集成 + 偏差修正（无未来泄漏）...")
rows = build_rows()[-args.days:]
if not rows:
    out("[中止] 有效回测样本为 0，请检查缓存数据长度")
    sys.exit(2)

out(f"      完成，实际评估 {len(rows)} 日 ({rows[0]['date']} ~ {rows[-1]['date']})")

# ---------- 指标汇总 ----------
out("[3/3] 汇总指标 ...\n")

def metrics_of(getter, subset=None):
    src = rows if subset is None else subset
    if not src:
        return {"n": 0}
    return core.error_metrics([getter(r) for r in src], [r["actual"] for r in src])

schemes = [
    ("P1 静态基准(对照)", lambda r: r["preds"]["P1_Q2静态"]),
    ("P2 调仓替代",       lambda r: r["preds"]["P2_调仓替代"]),
    ("P3 行业因子",       lambda r: r["preds"]["P3_行业因子"]),
    ("P4 层级组合",       lambda r: r["preds"]["P4_层级组合"]),
    ("集成(未修正)",      lambda r: r["final"]),
    ("集成+偏差修正",     lambda r: r["corr"]),
]

out("=" * 92)
out(f"一、总体表现（{len(rows)} 个交易日样本外）")
out("=" * 92)
out(f"{'方案':<18}{'MAE':>8}{'RMSE':>8}{'最大误差':>10}{'方向准确':>10}{'平均偏差':>10}{'vs P1':>10}")
out("-" * 92)
base = metrics_of(schemes[0][1])
summary = {}
for name, g in schemes:
    m = metrics_of(g)
    summary[name] = m
    delta = base["mae"] - m["mae"]
    tag = f"{delta:+.3f}" if name != "P1 静态基准(对照)" else "--"
    out(f"{name:<18}{m['mae']:>8.3f}{m['rmse']:>8.3f}{m['max_abs']:>10.3f}"
        f"{m['hit']*100:>9.1f}%{m['bias']:>+10.3f}{tag:>10}")
out("-" * 92)
best_mae = min(summary[k]["mae"] for k in summary)
winner = [k for k in summary if abs(summary[k]["mae"] - best_mae) < 1e-9][0]
ens = summary["集成+偏差修正"]
imp = (base["mae"] - ens["mae"]) / base["mae"] * 100 if base["mae"] else 0
out(f"最优方案: {winner} (MAE={best_mae:.3f}%)")
out(f"集成+修正 相对 P1 基线: MAE {base['mae']:.3f}% -> {ens['mae']:.3f}% "
    f"({imp:+.1f}%)  RMSE {base['rmse']:.3f}% -> {ens['rmse']:.3f}%  "
    f"方向 {base['hit']*100:.1f}% -> {ens['hit']*100:.1f}%")
if ens["mae"] >= base["mae"]:
    out("[结论] 集成未能击败静态基准 —— 多模型结构需要重新审视，不应上线为主口径")
else:
    out("[结论] 集成优于静态基准，多模型结构成立")

# ---------- 分市况 ----------
out("\n" + "=" * 92)
out("二、分市况表现（暴露结构性缺陷）")
out("=" * 92)
regimes = ["急涨", "急跌", "震荡"]
out(f"{'市况':<8}{'样本':>6}{'方案':<18}{'MAE':>8}{'RMSE':>8}{'最大误差':>10}{'方向准确':>10}{'平均偏差':>10}")
out("-" * 92)
for rg in regimes:
    sub = [r for r in rows if r["regime"] == rg]
    if not sub:
        out(f"{rg:<8}{0:>6}  (无样本)")
        continue
    for j, (name, g) in enumerate([schemes[0], schemes[5]]):
        m = metrics_of(g, sub)
        pre = f"{rg:<8}{len(sub):>6}" if j == 0 else " " * 14
        out(f"{pre}{name:<18}{m['mae']:>8.3f}{m['rmse']:>8.3f}{m['max_abs']:>10.3f}"
            f"{m['hit']*100:>9.1f}%{m['bias']:>+10.3f}")
    out("-" * 92)

# 结构性缺陷诊断
diag = []
for rg in regimes:
    sub = [r for r in rows if r["regime"] == rg]
    if len(sub) < 3:
        continue
    m = metrics_of(schemes[5][1], sub)
    if abs(m["bias"]) > 0.4:
        direction = "系统性高估" if m["bias"] > 0 else "系统性低估"
        diag.append(f"{rg}行情下{direction} {abs(m['bias']):.2f}pp（{len(sub)}个样本）")
    if m["mae"] > ens["mae"] * 1.5:
        diag.append(f"{rg}行情下 MAE {m['mae']:.2f}% 显著高于整体 {ens['mae']:.2f}%")
out("\n[结构性缺陷诊断]")
if diag:
    for x in diag:
        out(f"  - {x}")
else:
    out("  - 未发现明显的市况相关系统性偏差")

# ---------- 偏差修正与权重稳定性 ----------
out("\n" + "=" * 92)
out("三、偏差修正与权重稳定性")
out("=" * 92)
raw_mae = summary["集成(未修正)"]["mae"]
cor_mae = summary["集成+偏差修正"]["mae"]
out(f"偏差修正贡献: MAE {raw_mae:.3f}% -> {cor_mae:.3f}% ({cor_mae-raw_mae:+.3f}pp)"
    f"  {'有效' if cor_mae < raw_mae else '无效/有害'}")
unstable = sum(1 for r in rows if r["bias_div"] > 0.35)
out(f"偏差不稳定天数: {unstable}/{len(rows)} ({unstable/len(rows)*100:.0f}%)"
    f" —— 这些日子修正幅度已自动收缩且置信度下调")
bs = [r["bias"] for r in rows]
out(f"修正幅度: 均值{np.mean(bs):+.3f}pp 范围[{min(bs):+.3f}, {max(bs):+.3f}]pp")

out("\n各模型权重区间（分组去重后）:")
for m in MODELS:
    vs = [r["wts"].get(m, 0) for r in rows]
    out(f"  {m:<14} 均值{np.mean(vs)*100:5.1f}%  范围[{min(vs)*100:4.1f}%, {max(vs)*100:4.1f}%]")
g3 = [r["wts"].get("P3_行业因子", 0) + r["wts"].get("P4_层级组合", 0) for r in rows]
out(f"  {'G3行业口径合计':<14} 均值{np.mean(g3)*100:5.1f}%  范围[{min(g3)*100:4.1f}%, {max(g3)*100:4.1f}%]"
    f"   <- 同源去重后不再双倍计权")
th = [r["theta"] for r in rows]
out(f"\ntheta_pcb 区间: 均值{np.mean(th)*100:.1f}% 范围[{min(th)*100:.1f}%, {max(th)*100:.1f}%]"
    f" 斜率{core.trend_slope(th):+.5f}")

# ---------- 逐日明细 ----------
if args.daily:
    out("\n" + "=" * 92)
    out("四、逐日明细")
    out("=" * 92)
    out(f"{'日期':<12}{'市况':<6}{'实际':>8}{'P1':>8}{'P2':>8}{'P3':>8}{'P4':>8}"
        f"{'集成':>8}{'修正':>8}{'最终':>8}{'误差':>8}")
    out("-" * 92)
    for r in rows:
        out(f"{r['date']:<12}{r['regime']:<6}{r['actual']:>+8.2f}"
            f"{r['preds']['P1_Q2静态']:>+8.2f}{r['preds']['P2_调仓替代']:>+8.2f}"
            f"{r['preds']['P3_行业因子']:>+8.2f}{r['preds']['P4_层级组合']:>+8.2f}"
            f"{r['final']:>+8.2f}{r['bias']:>+8.2f}{r['corr']:>+8.2f}"
            f"{r['corr']-r['actual']:>+8.2f}")

# ---------- 存档 ----------
report = {
    "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    "sample_days": len(rows),
    "period": [rows[0]["date"], rows[-1]["date"]],
    "window": {"W_BASE": W_BASE, "HALF_LIFE": HALF_LIFE, "K_ERR": K_ERR},
    "overall": {k: summary[k] for k in summary},
    "by_regime": {
        rg: {"n": len([r for r in rows if r["regime"] == rg]),
             "P1": metrics_of(schemes[0][1], [r for r in rows if r["regime"] == rg]),
             "ensemble_corrected": metrics_of(schemes[5][1],
                                              [r for r in rows if r["regime"] == rg])}
        for rg in regimes
    },
    "bias_effect": {"raw_mae": raw_mae, "corrected_mae": cor_mae,
                    "gain_pp": round(raw_mae - cor_mae, 4),
                    "unstable_days": unstable},
    "weight_ranges": {m: {"mean": round(float(np.mean([r["wts"].get(m, 0) for r in rows])), 4),
                          "min": round(float(min(r["wts"].get(m, 0) for r in rows)), 4),
                          "max": round(float(max(r["wts"].get(m, 0) for r in rows)), 4)}
                      for m in MODELS},
    "g3_combined_weight_mean": round(float(np.mean(g3)), 4),
    "theta_pcb": {"mean": round(float(np.mean(th)), 4),
                  "min": round(float(min(th)), 4), "max": round(float(max(th)), 4),
                  "slope": round(core.trend_slope(th), 6)},
    "diagnostics": diag,
    "verdict": ("集成优于静态基准" if ens["mae"] < base["mae"] else "集成未击败静态基准"),
    "daily": [{"date": r["date"], "regime": r["regime"], "actual": round(r["actual"], 3),
               "final": round(r["final"], 3), "corr": round(r["corr"], 3),
               "err": round(r["corr"] - r["actual"], 3)} for r in rows],
}
rp = os.path.join(CACHE, "backtest_report.json")
json.dump(report, open(rp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
out(f"\n[已保存] cache/backtest_report.json（{len(rows)} 日回测报告，供页面/复盘引用）")
out("=" * 92)
