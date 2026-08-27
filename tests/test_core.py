#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_core.py —— 006010 估值系统单元测试（无网络，纯本地可跑）

v4 改动：
  [1] Windows GBK 终端兼容：全 ASCII 状态标记 [OK]/[NG]，并对无法编码的字符做安全降级
      （原来用 U+2713/U+2717 在 cmd 默认 GBK 代码页下会 UnicodeEncodeError 直接崩）
  [2] 覆盖面从 5 组 15 项扩到 15 组：θ/β 边界约束、集成权重上限与同源去重、
      模型缺失降级、节假日交易日历、日期错配、偏差修正防滞后、连续置信度、
      数据源校验、误差指标、PCB 信号分级、result.json v4 契约

用法:
  python tests/test_core.py            # 全量
  python tests/test_core.py -v         # 显示每项数值明细
"""
import sys, os, json, math

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

# ---------- Windows 终端安全输出（本轮第 1 项修复） ----------
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_ENC = (getattr(sys.stdout, "encoding", None) or "ascii")

def out(s):
    """兜底输出：即使终端是 GBK/ASCII 也不抛 UnicodeEncodeError"""
    try:
        print(s)
    except UnicodeEncodeError:
        print(s.encode(_ENC, "replace").decode(_ENC, "replace"))

VERBOSE = "-v" in sys.argv
PASS = FAIL = 0
FAILED = []

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        out(f"  [OK]  {name}" + (f"   {detail}" if VERBOSE and detail else ""))
    else:
        FAIL += 1
        FAILED.append(name)
        out(f"  [NG]  {name}   {detail}")

def section(title):
    out(f"\n[{title}]")

import numpy as np
import core


# ============================================================
# 1. 归一化估值公式（历史 +528% bug 回归）
# ============================================================
def test_normalized_valuation():
    section("1. 归一化估值公式")
    weights = [0.0963, 0.0936, 0.0936, 0.0842, 0.0835,
               0.0824, 0.0818, 0.0797, 0.0773, 0.0592]
    rets = [0.03, 0.05, 0.02, -0.01, 0.04, 0.06, 0.01, 0.03, -0.02, 0.05]
    wsum = sum(weights)
    num = sum(w * r for w, r in zip(weights, rets)) * 100.0
    norm = num / wsum
    buggy = num / (wsum / 100)
    check("简单加权 = sum(w*r)", abs(num - 2.1506) < 0.01, f"got {num:.4f}")
    check("归一化 = num/wsum", abs(norm - 2.5861) < 0.02, f"got {norm:.4f}")
    check("bug 版恰为正确版 100 倍(已修复)", abs(buggy - norm * 100) < 0.01,
          f"buggy={buggy:.2f}")
    check("归一化落在合理量级", 0 < norm < 10, f"norm={norm:.2f}")
    check("归一化 > 简单加权(wsum<1 且收益为正)", norm > num, f"{norm:.4f} > {num:.4f}")


# ============================================================
# 2. Q2 组合收益的权重单位防呆
# ============================================================
def test_q2_weight_unit():
    section("2. Q2 组合权重单位防呆(历史放大100倍 bug)")
    kl = {"688498": {"2026-01-02": 100.0, "2026-01-05": 110.0}}
    seq = ["2026-01-02", "2026-01-05"]
    dec = core.q2_portfolio_returns(kl, seq, weights={"688498": 0.0963})
    pct = core.q2_portfolio_returns(kl, seq, weights={"688498": 9.63})
    check("小数权重 -> 0.963%", abs(dec[0] - 0.963) < 1e-6, f"got {dec[0]:.4f}")
    check("百分数权重误用 -> 放大100倍", abs(pct[0] / dec[0] - 100) < 1e-6,
          f"ratio={pct[0]/dec[0]:.2f}")
    check("默认使用 TOP10_W 小数权重", max(core.TOP10_W.values()) < 1.0,
          f"max={max(core.TOP10_W.values())}")
    check("缺失价格自动跳过不报错",
          len(core.q2_portfolio_returns({}, seq)) == 1)


# ============================================================
# 3. 半衰期权重
# ============================================================
def test_half_life():
    section("3. 半衰期时间权重")
    w = core.half_life_weights(60, 20)
    check("长度正确", len(w) == 60, f"len={len(w)}")
    check("末位权重=1.0(最新最重)", abs(w[-1] - 1.0) < 1e-9, f"{w[-1]:.6f}")
    check("单调递增", all(w[i] <= w[i + 1] for i in range(len(w) - 1)))
    check("半衰期处权重=0.5", abs(w[-21] - 0.5) < 1e-6, f"{w[-21]:.6f}")
    check("n=0 返回空数组不崩", len(core.half_life_weights(0)) == 0)


# ============================================================
# 4. theta 边界约束（P2 调仓替代比例模型）
# ============================================================
def test_theta_bounds():
    section("4. theta 边界约束 0<=theta_pcb<=35%, 0<=theta_m<=25%")
    rng = np.random.default_rng(42)
    n = 60
    w = core.half_life_weights(n, 20)

    # 场景A：基金收益完全等于 PCB（理论 theta_pcb 应顶到上限）
    q2 = rng.normal(0, 2, n)
    pcb = rng.normal(0, 2, n)
    mkt = rng.normal(0, 1, n)
    th = core.fit_theta(pcb.copy(), q2, pcb, mkt, w)
    check("A 极端偏 PCB: theta_pcb <= 0.35", th[0] <= 0.35 + 1e-9, f"got {th[0]:.6f}")
    check("A theta 非负", th[0] >= -1e-12 and th[1] >= -1e-12, f"got {th}")

    # 场景B：基金收益 = 反向 PCB（理论 theta 应被压到 0，不能取负）
    th2 = core.fit_theta(-pcb, q2, pcb, mkt, w)
    check("B 反向证据: theta_pcb >= 0(不允许负暴露)", th2[0] >= -1e-12, f"got {th2[0]:.6f}")

    # 场景C：自定义更严上限
    th3 = core.fit_theta(pcb.copy(), q2, pcb, mkt, w, hi_pcb=0.10, hi_m=0.05)
    check("C 自定义上限 0.10 生效", th3[0] <= 0.10 + 1e-9, f"got {th3[0]:.6f}")
    check("C theta_m 上限 0.05 生效", th3[1] <= 0.05 + 1e-9, f"got {th3[1]:.6f}")

    # 场景D：全零输入（退化）不崩
    z = np.zeros(n)
    th4 = core.fit_theta(z, z, z, z, w)
    check("D 全零输入不崩且在界内",
          0 <= th4[0] <= 0.35 and 0 <= th4[1] <= 0.25, f"got {th4}")


# ============================================================
# 5. beta 边界约束（P3 行业因子模型）
# ============================================================
def test_beta_bounds():
    section("5. beta 边界约束 beta>=0 且 sum(beta)<=1")
    rng = np.random.default_rng(7)
    n = 60
    x = rng.normal(0, 2, (n, 4))
    w = core.half_life_weights(n, 20)
    prior = np.array([0.83, 0, 0, 0])

    # 场景A：真实 beta 之和远超 1（应被约束压住）
    y = x @ np.array([1.5, 1.2, 0.8, 0.6]) + rng.normal(0, 0.1, n)
    b = core.constrained_regression(y, x, w, prior, prior)
    check("A beta 全部 >= 0", all(v >= -1e-9 for v in b), f"got {np.round(b,4)}")
    check("A sum(beta) <= 1", b.sum() <= 1.0 + 1e-6, f"sum={b.sum():.6f}")

    # 场景B：真实 beta 含负值（应被截到 0）
    y2 = x @ np.array([0.8, -0.5, 0.2, -0.3]) + rng.normal(0, 0.1, n)
    b2 = core.constrained_regression(y2, x, w, prior, prior)
    check("B 负暴露被截为 0", all(v >= -1e-9 for v in b2), f"got {np.round(b2,4)}")

    # 场景C：自定义单因子上限
    b3 = core.constrained_regression(y, x, w, prior, prior, bnd_hi=0.3)
    check("C 单因子上限 0.3 生效", all(v <= 0.3 + 1e-9 for v in b3), f"got {np.round(b3,4)}")

    # 场景D：退化输入（全零 y）
    b4 = core.constrained_regression(np.zeros(n), x, w, prior, prior)
    check("D 全零 y 不崩且在界内",
          all(v >= -1e-9 for v in b4) and b4.sum() <= 1 + 1e-6, f"got {np.round(b4,4)}")


# ============================================================
# 6. 集成权重：上限 + 同源分组去重
# ============================================================
def test_ensemble_weights():
    section("6. 集成权重(上限70% + P3/P4同源去重)")
    maes = {"P1_Q2静态": 0.64, "P2_调仓替代": 0.62,
            "P3_行业因子": 0.44, "P4_层级组合": 0.44, "P5_个股辅助": 0.44}
    # 本组专测"同源去重"机制，故显式关闭淘汰闸门（闸门单独在第 6b 组测）
    w, info = core.ensemble_weights(maes, gate=0.0)
    check("权重和 = 1", abs(sum(w.values()) - 1.0) < 1e-9, f"sum={sum(w.values()):.9f}")
    check("单模型权重 <= 70%", all(v <= 0.70 + 1e-9 for v in w.values()),
          f"max={max(w.values()):.4f}")
    check("P3 与 P4 同组同权(同源)", abs(w["P3_行业因子"] - w["P4_层级组合"]) < 1e-9,
          f"{w['P3_行业因子']:.4f} vs {w['P4_层级组合']:.4f}")

    # 关键回归：同源去重后 G3 总权重不应等于"两个独立模型各拿一份"
    g3 = w["P3_行业因子"] + w["P4_层级组合"]
    g4 = w["P5_个股辅助"]
    check("G3(P3+P4) 合计 ~= G4(P5) 单模型(同MAE同组间权重)",
          abs(g3 - g4) < 0.02, f"G3={g3:.4f} G4={g4:.4f}")
    check("P3 单模型权重 < P5(同MAE但被同源摊分)", w["P3_行业因子"] < g4 - 1e-6,
          f"P3={w['P3_行业因子']:.4f} P5={g4:.4f}")

    # 未分组的朴素做法会让行业口径拿到约两倍权重 -> 证明修复有效
    naive = {k: 1.0 / (maes[k] + core.MAE_FLOOR) for k in maes}
    tot = sum(naive.values())
    naive = {k: v / tot for k, v in naive.items()}
    naive_g3 = naive["P3_行业因子"] + naive["P4_层级组合"]
    check("修复前行业口径被双倍计权(对照)", naive_g3 > g3 + 0.05,
          f"naive_G3={naive_g3:.4f} -> fixed_G3={g3:.4f}")

    check("MAE 越小权重越大", w["P3_行业因子"] > 0 and w["P1_Q2静态"] < g4,
          f"P1={w['P1_Q2静态']:.4f}")
    check("分组信息完整", set(info["groups"].keys()) == {
        "G1_静态基准", "G2_调仓替代", "G3_行业暴露", "G4_个股反推"},
        f"{list(info['groups'].keys())}")

    # 极端：只有一个模型 -> 权重必为 1（cap 不应把它压到 0.7 后失衡）
    w1, _ = core.ensemble_weights({"P1_Q2静态": 0.5})
    check("单模型场景权重=1", abs(w1["P1_Q2静态"] - 1.0) < 1e-9, f"got {w1}")
    check("空输入返回空不崩", core.ensemble_weights({})[0] == {})

    # ---- 6b. 劣质模型淘汰闸门（回测选定 gate=1.15） ----
    section("6b. 劣质模型淘汰闸门 gate")
    wg, ig = core.ensemble_weights(maes, gate=1.15)
    # 最优 MAE=0.44，(0.44+0.02)*1.15=0.529 -> P1(0.64)/P2(0.62) 应被淘汰
    check("劣质模型被淘汰", set(ig["dropped"]) == {"P1_Q2静态", "P2_调仓替代"},
          f"dropped={ig['dropped']}")
    check("被淘汰模型保留键且权重=0",
          all(k in wg for k in maes) and wg["P1_Q2静态"] == 0.0
          and wg["P2_调仓替代"] == 0.0,
          f"P1={wg['P1_Q2静态']}, P2={wg['P2_调仓替代']}")
    check("淘汰后权重和仍 = 1", abs(sum(wg.values()) - 1.0) < 1e-9,
          f"sum={sum(wg.values()):.9f}")
    check("n_active 与 dropped 自洽", ig["n_active"] == len(maes) - len(ig["dropped"]),
          f"n_active={ig['n_active']}")
    check("分组信息保留被淘汰组（可审计）",
          set(ig["groups"].keys()) == {"G1_静态基准", "G2_调仓替代",
                                       "G3_行业暴露", "G4_个股反推"},
          f"{sorted(ig['groups'])}")
    check("被淘汰组 group_weight = 0",
          ig["group_weights"]["G1_静态基准"] == 0.0
          and ig["group_weights"]["G2_调仓替代"] == 0.0,
          f"{ig['group_weights']}")
    check("闸门后幸存模型权重高于闸门前",
          wg["P3_行业因子"] > w["P3_行业因子"],
          f"{w['P3_行业因子']:.4f} -> {wg['P3_行业因子']:.4f}")
    # 闸门绝不能清空：全部模型 MAE 相同时应全员幸存
    wsame, isame = core.ensemble_weights({"A": 0.5, "B": 0.5, "C": 0.5}, gate=1.15,
                                         groups={"A": "a", "B": "b", "C": "c"})
    check("MAE 相同时无人被淘汰", isame["dropped"] == [], f"{isame['dropped']}")
    # 极端：只有一个模型远好于其他 -> 只剩它，且权重=1
    wex, iex = core.ensemble_weights({"A": 0.10, "B": 5.0, "C": 6.0}, gate=1.15,
                                     groups={"A": "a", "B": "b", "C": "c"})
    check("极端差距下仅最优幸存且权重=1",
          abs(wex["A"] - 1.0) < 1e-9 and wex["B"] == 0.0 and wex["C"] == 0.0,
          f"A={wex['A']:.4f} dropped={iex['dropped']}")
    check("闸门关闭(gate=0)时全员参与",
          core.ensemble_weights(maes, gate=0.0)[1]["dropped"] == [])
    # MAE≈0 的退化场景：若不做 floor 平移，阈值 = 0*1.15 = 0，会把所有非零模型清光。
    # 平移后阈值 =(0+0.02)*1.15=0.023，即"差距在 0.003pp 内"才算并列。
    g0 = {"A": "a", "B": "b"}
    check("MAE≈0 时极接近者仍幸存(floor 平移生效)",
          core.ensemble_weights({"A": 0.0, "B": 0.002}, gate=1.15,
                                groups=g0)[1]["dropped"] == [],
          "0.002+0.02=0.022 <= 0.023 -> 幸存")
    check("MAE≈0 时明显更差者被淘汰",
          core.ensemble_weights({"A": 0.0, "B": 0.03}, gate=1.15,
                                groups=g0)[1]["dropped"] == ["B"],
          "0.03+0.02=0.05 > 0.023 -> 淘汰")


# ============================================================
# 7. 模型缺失降级（P5 不可用）
# ============================================================
def test_degradation():
    section("7. 模型/数据缺失降级")
    full = {"P1_Q2静态": 0.64, "P2_调仓替代": 0.62,
            "P3_行业因子": 0.44, "P4_层级组合": 0.44, "P5_个股辅助": 0.44}
    part = {k: v for k, v in full.items() if k != "P5_个股辅助"}
    # 用 gate=0 检验"缺模型后权重重分配"，避免与淘汰闸门效果混淆
    w_full, _ = core.ensemble_weights(full, gate=0.0)
    w_part, _ = core.ensemble_weights(part, gate=0.0)
    check("P5 缺失后权重和仍 = 1", abs(sum(w_part.values()) - 1.0) < 1e-9,
          f"sum={sum(w_part.values()):.9f}")
    check("P5 缺失后不含 P5 键", "P5_个股辅助" not in w_part)
    check("P5 缺失后其余模型权重上升",
          w_part["P1_Q2静态"] > w_full["P1_Q2静态"],
          f"{w_full['P1_Q2静态']:.4f} -> {w_part['P1_Q2静态']:.4f}")
    # 线上默认参数（含闸门）下的缺失降级同样必须成立
    w_pg, i_pg = core.ensemble_weights(part)
    check("默认参数下 P5 缺失仍权重和=1", abs(sum(w_pg.values()) - 1.0) < 1e-9,
          f"sum={sum(w_pg.values()):.9f}  active={i_pg['n_active']}")
    check("默认参数下 P5 缺失后仍有活跃模型", i_pg["n_active"] >= 1,
          f"n_active={i_pg['n_active']}, dropped={i_pg['dropped']}")
    check("仅剩 P1 时退化为纯静态基准",
          abs(core.ensemble_weights({"P1_Q2静态": 0.64})[0]["P1_Q2静态"] - 1.0) < 1e-9)
    # 全模型齐备但只有 P1 可用（其余 MAE 为 inf 的极端）
    w_inf, i_inf = core.ensemble_weights(
        {"P1_Q2静态": 0.64, "P3_行业因子": 99.0, "P5_个股辅助": 99.0})
    check("其余模型误差爆炸时退化为 P1 主导",
          w_inf["P1_Q2静态"] > 0.99 and i_inf["n_active"] == 1,
          f"P1={w_inf['P1_Q2静态']:.4f} active={i_inf['n_active']}")
    # theta 拟合失败降级为 [0,0]（即退化成 P1）
    w = core.half_life_weights(5, 20)
    th = core.fit_theta(np.zeros(5), np.zeros(5), np.zeros(5), np.zeros(5), w)
    check("theta 退化场景返回界内值", 0 <= th[0] <= 0.35, f"got {th}")


# ============================================================
# 8. 交易日历（周末 + 法定节假日）
# ============================================================
def test_trade_calendar():
    section("8. 交易日历(周末 + 节假日)")
    check("周四->周五", core.next_trade_day("2026-08-20") == "2026-08-21",
          core.next_trade_day("2026-08-20"))
    check("周五->下周一", core.next_trade_day("2026-08-21") == "2026-08-24",
          core.next_trade_day("2026-08-21"))
    check("周六->下周一", core.next_trade_day("2026-08-22") == "2026-08-24")
    # 中秋 2026-09-25(五) 休市 -> 09-24(四) 的下一交易日是 09-28(一)
    check("跳过中秋休市 09-24->09-28", core.next_trade_day("2026-09-24") == "2026-09-28",
          core.next_trade_day("2026-09-24"))
    # 国庆 10-01~10-07(含) -> 09-30 的下一交易日是 10-08
    check("跳过国庆长假 09-30->10-08", core.next_trade_day("2026-09-30") == "2026-10-08",
          core.next_trade_day("2026-09-30"))
    check("上一交易日 08-20->08-19", core.prev_trade_day("2026-08-20") == "2026-08-19")
    check("上一交易日跨周末 08-24->08-21", core.prev_trade_day("2026-08-24") == "2026-08-21")
    check("节假日判定: 10-01 非交易日", not core.is_trade_day("2026-10-01"))
    check("周末判定: 08-22 非交易日", not core.is_trade_day("2026-08-22"))
    check("正常日判定: 08-20 是交易日", core.is_trade_day("2026-08-20"))
    # 未覆盖年份安全降级（仅跳周末，不死循环）
    nxt = core.next_trade_day("2030-01-04", holidays=set())
    check("未知年份降级为仅跳周末且不死循环", nxt == "2030-01-07", nxt)


# ============================================================
# 9. 估算语义 + 日期错配自检
# ============================================================
def test_date_consistency():
    section("9. 估算语义与日期错配自检")
    import datetime as dt
    navd = ["2026-08-18", "2026-08-19", "2026-08-20"]

    ok, prob = core.check_date_consistency("2026-08-20", "2026-08-19", navd)
    check("正常: 基准=目标日上一交易日", ok, f"{prob}")

    # 历史真实 bug：盘后 nav_prev 取到目标日自身净值
    ok2, prob2 = core.check_date_consistency("2026-08-20", "2026-08-20", navd)
    check("捕获语义错位(基准日=目标日)", not ok2 and any("语义错位" in p for p in prob2),
          f"{prob2}")

    # 基准日晚于目标日
    ok3, _ = core.check_date_consistency("2026-08-19", "2026-08-20", navd)
    check("捕获基准日晚于目标日", not ok3)

    # 官方净值日与目标日不一致时不应填充
    ok4, prob4 = core.check_date_consistency("2026-08-20", "2026-08-19", navd,
                                            official_date="2026-08-19")
    check("捕获官方净值日错配", not ok4 and any("官方" in p for p in prob4), f"{prob4}")

    # 目标日为周末
    ok5, prob5 = core.check_date_consistency("2026-08-22", "2026-08-21", [])
    check("捕获目标日非交易日", not ok5 and any("非交易日" in p for p in prob5), f"{prob5}")

    # 估算模式：官方已公布 -> settled
    e1 = core.resolve_estimation_mode("2026-08-20", "2026-08-20", True,
                                      now=dt.datetime(2026, 8, 20, 20, 0))
    check("官方已公布 -> settled", e1["mode"] == "settled", e1["mode"])
    # 盘中 -> intraday
    e2 = core.resolve_estimation_mode("2026-08-20", "2026-08-19", False,
                                      now=dt.datetime(2026, 8, 20, 10, 30))
    check("盘中时段 -> intraday", e2["mode"] == "intraday", e2["mode"])
    check("盘中 session=trading", e2["session"] == "trading", e2["session"])
    # 收盘后未公布 -> next_trading_day
    e3 = core.resolve_estimation_mode("2026-08-21", "2026-08-20", False,
                                      now=dt.datetime(2026, 8, 20, 18, 0))
    check("收盘后未公布 -> next_trading_day", e3["mode"] == "next_trading_day", e3["mode"])
    check("基准净值日自动取上一交易日", e3["nav_prev_date"] == "2026-08-20",
          e3["nav_prev_date"])
    check("午休 session=lunch",
          core.market_session(dt.datetime(2026, 8, 20, 12, 0)) == "lunch")
    check("周末 session=non_trade_day",
          core.market_session(dt.datetime(2026, 8, 22, 10, 0)) == "non_trade_day")


# ============================================================
# 10. 偏差修正防滞后
# ============================================================
def test_bias_correction():
    section("10. 偏差修正(med20/med40/EWMA + 防滞后收缩)")
    # 稳定负偏差 -> 三估计量应一致，不收缩
    stable = [-0.20] * 40
    b = core.bias_correction(stable)
    check("稳定偏差: med20=med40=EWMA", abs(b["med20"] - b["ewma"]) < 1e-6, f"{b}")
    check("稳定偏差: 不收缩", abs(b["shrink"] - 1.0) < 1e-9, f"shrink={b['shrink']}")
    check("稳定偏差: 修正值=偏差本身", abs(b["applied"] + 0.20) < 1e-6, f"{b['applied']}")
    check("稳定偏差: 标记 stable", b["stable"])

    # 偏差结构切换：前20日 +0.5，后20日 -0.5（EWMA 应显著快于 med40）
    shift = [0.5] * 20 + [-0.5] * 20
    b2 = core.bias_correction(shift)
    check("切换期: EWMA 比 med40 更贴近最新", abs(b2["ewma"] + 0.5) < abs(b2["med40"] + 0.5),
          f"ewma={b2['ewma']} med40={b2['med40']}")
    check("切换期: 分歧被识别", b2["divergence"] > 0.35, f"divergence={b2['divergence']}")
    check("切换期: 触发收缩", b2["shrink"] < 1.0, f"shrink={b2['shrink']}")
    check("切换期: 标记不稳定", not b2["stable"])
    check("收缩后幅度小于原始融合值", abs(b2["applied"]) < abs(b2["raw"]) + 1e-9,
          f"applied={b2['applied']} raw={b2['raw']}")

    # 极端异常值 -> 中位数应抗噪，且有上限截断
    wild = [-0.2] * 39 + [50.0]
    b3 = core.bias_correction(wild)
    check("极端异常值不污染修正(中位数抗噪)", abs(b3["med20"] + 0.2) < 1e-6, f"{b3['med20']}")
    check("修正幅度被上限截断 <= 1.5pp", abs(b3["applied"]) <= 1.5 + 1e-9,
          f"applied={b3['applied']}")

    b4 = core.bias_correction([])
    check("空误差序列返回 0 不崩", b4["applied"] == 0.0 and b4["n"] == 0)
    check("EWMA 末位权重最高(对最新更敏感)",
          abs(core.ewma([0, 0, 0, 1], half_life=1) - 0.5333) < 0.01,
          f"{core.ewma([0,0,0,1], half_life=1):.4f}")


# ============================================================
# 11. 连续置信度 0-100
# ============================================================
def test_confidence_score():
    section("11. 连续置信度评分 0-100")
    best = core.confidence_score(0.25, 0.4, 0.02, 0.10, 1.0, 0.85)
    worst = core.confidence_score(2.0, 5.0, 0.5, 1.5, 0.0, 0.5)
    check("最优场景接近满分", best[0] >= 95, f"score={best[0]}")
    check("最差场景接近 0 分", worst[0] <= 5, f"score={worst[0]}")
    check("最优 -> 高", best[1] == "高", best[1])
    check("最差 -> 低", worst[1] == "低", worst[1])

    # 单调性：MAE 越大分数越低
    scores = [core.confidence_score(m, 1.0, 0.05, 0.2, 1.0)[0]
              for m in (0.3, 0.6, 0.9, 1.2, 1.5)]
    check("MAE 单调性(越大分越低)", all(scores[i] >= scores[i + 1] for i in range(4)),
          f"{scores}")
    # 单调性：分歧越大分数越低
    sp = [core.confidence_score(0.5, s, 0.05, 0.2, 1.0)[0] for s in (0.5, 1.5, 2.5, 4.0)]
    check("分歧单调性", all(sp[i] >= sp[i + 1] for i in range(3)), f"{sp}")
    # 偏差不稳定应扣分（与 test_bias_correction 联动）
    s_stable = core.confidence_score(0.5, 1.0, 0.05, 0.10, 1.0)[0]
    s_unstable = core.confidence_score(0.5, 1.0, 0.05, 0.90, 1.0)[0]
    check("偏差分歧大 -> 置信度自动下调", s_unstable < s_stable,
          f"{s_stable} -> {s_unstable}")
    # 数据质量下降应扣分
    s_dq = core.confidence_score(0.5, 1.0, 0.05, 0.2, 0.3)[0]
    check("数据质量差 -> 置信度下调", s_dq < s_stable, f"{s_stable} -> {s_dq}")
    check("分数恒在 0~100", all(0 <= core.confidence_score(m, s, 0.1, 0.3, q)[0] <= 100
                              for m in (0, 0.5, 3) for s in (0, 2, 9) for q in (0, 1)))
    check("分档阈值: 75 分及以上为高",
          core.confidence_score(0.30, 0.50, 0.03, 0.15, 1.0, 0.85)[1] == "高")
    check("明细各项之和 = 总分(±1 取整误差)",
          abs(sum(best[2].values()) - best[0]) <= 1, f"{best[2]} vs {best[0]}")


# ============================================================
# 12. 数据源校验
# ============================================================
def test_data_validation():
    section("12. 数据源校验/降级")
    def mk_kl(rows=200):
        kl = {}
        for g, codes in core.BASKETS.items():
            kl[g] = {c: {f"2026-01-{(i%28)+1:02d}-{i}": 10.0 + i for i in range(rows)}
                     for c in codes}
        return kl

    good_dates = [f"2026-{(i//20)+1:02d}-{(i%20)+1:02d}" for i in range(200)]
    good = {"dates": good_dates, "navs": [0.5 + 0.001 * i for i in range(200)]}
    r = core.validate_market_data(good, mk_kl(), today="2026-10-01")
    check("正常数据通过", r["ok"], f"{r['errors']}")
    check("覆盖率写入 detail", abs(r["detail"]["top10_coverage"] - 0.8316) < 1e-4,
          f"{r['detail']['top10_coverage']}")

    # 长度不一致 -> 致命
    bad1 = {"dates": good_dates, "navs": [0.5] * 199}
    check("长度不一致 -> 致命",
          not core.validate_market_data(bad1, mk_kl())["ok"])
    # 日期乱序 -> 致命
    bad2 = {"dates": list(reversed(good_dates)), "navs": good["navs"]}
    check("日期乱序 -> 致命", not core.validate_market_data(bad2, mk_kl())["ok"])
    # 重复日期 -> 致命
    bad3 = {"dates": good_dates[:-1] + [good_dates[-2]], "navs": good["navs"]}
    check("重复日期 -> 致命", not core.validate_market_data(bad3, mk_kl())["ok"])
    # 负净值 -> 致命
    bad4 = {"dates": good_dates, "navs": [0.5] * 199 + [-1.0]}
    check("非正净值 -> 致命", not core.validate_market_data(bad4, mk_kl())["ok"])
    # 样本严重不足 -> 致命
    bad5 = {"dates": good_dates[:20], "navs": good["navs"][:20]}
    check("样本 <30 -> 致命", not core.validate_market_data(bad5, mk_kl())["ok"])
    # 样本偏少 -> 告警但可用
    warn = {"dates": good_dates[:100], "navs": good["navs"][:100]}
    rw = core.validate_market_data(warn, mk_kl(), today="2026-10-01")
    check("样本偏少 -> 告警但可用", rw["ok"] and rw["warnings"], f"{rw['warnings']}")
    check("告警会扣质量分", rw["score"] < 1.0, f"score={rw['score']}")
    # 篮子缺股过半 -> 致命
    kl_miss = mk_kl()
    kl_miss["pcb"] = {}
    check("篮子全缺 -> 致命",
          not core.validate_market_data(good, kl_miss)["ok"])
    # 数据滞后 -> 告警
    rl = core.validate_market_data(good, mk_kl(), today="2026-12-31")
    check("数据滞后 -> 告警", any("滞后" in w for w in rl["warnings"]), f"{rl['warnings']}")
    # 异常日收益 -> 告警
    jump = {"dates": good_dates, "navs": [0.5] * 199 + [5.0]}
    rj = core.validate_market_data(jump, mk_kl(), today="2026-10-01")
    check("净值日收益异常 -> 告警", any("异常" in w for w in rj["warnings"]), f"{rj['warnings']}")
    check("空输入不抛异常", core.validate_market_data({}, {})["ok"] is False)


# ============================================================
# 13. 误差指标
# ============================================================
def test_error_metrics():
    section("13. 误差指标 MAE/RMSE/最大误差/方向准确率")
    preds = [1.0, 2.0, -1.0, 3.0]
    acts = [1.5, 1.0, -2.0, 1.0]
    m = core.error_metrics(preds, acts)
    check("n 正确", m["n"] == 4)
    # 误差 = [-0.5, +1.0, +1.0, +2.0] -> MAE=4.5/4=1.125, RMSE=sqrt(6.25/4)=1.25
    check("MAE = 1.125", abs(m["mae"] - 1.125) < 1e-9, f"{m['mae']}")
    check("RMSE = 1.25", abs(m["rmse"] - 1.25) < 1e-9, f"{m['rmse']}")
    check("平均偏差 = +0.875(系统性高估)", abs(m["bias"] - 0.875) < 1e-9, f"{m['bias']}")
    check("最大绝对误差 = 2.0", abs(m["max_abs"] - 2.0) < 1e-9, f"{m['max_abs']}")
    check("方向准确率 = 100%(符号全对)", abs(m["hit"] - 1.0) < 1e-9, f"{m['hit']}")
    check("RMSE >= MAE(数学必然)", m["rmse"] >= m["mae"] - 1e-12)
    m2 = core.error_metrics([1.0, -1.0], [-1.0, 1.0])
    check("方向全错 -> 0%", abs(m2["hit"]) < 1e-9, f"{m2['hit']}")
    check("空输入返回 n=0", core.error_metrics([], [])["n"] == 0)
    check("长度不一致返回 n=0", core.error_metrics([1, 2], [1])["n"] == 0)
    check("市况分类: 急涨", core.classify_regime(2.0) == "急涨")
    check("市况分类: 急跌", core.classify_regime(-3.0) == "急跌")
    check("市况分类: 震荡", core.classify_regime(0.5) == "震荡")


# ============================================================
# 14. PCB 三级信号（严格版）
# ============================================================
def test_pcb_signal():
    section("14. PCB 三级信号(证据逐条校验)")
    sens = {10: np.array([0.78, 0.17, 0, 0.08]),
            20: np.array([0.78, 0.169, 0, 0.08]),
            60: np.array([0.78, 0.116, 0, 0.08])}
    rising = [0.04 + 0.005 * i for i in range(10)]

    lvl, ev = core.pcb_signal_strength(sens, rising, 0.105, 0.085,
                                       mae_with_pcb=0.442, mae_without_pcb=0.455,
                                       infer_pcb_exposure=0.06, basket_agreement=True)
    check("五项证据齐全 -> 强信号", lvl == "强信号", f"{lvl} {ev}")
    check("C3 增量验证通过", ev["C3_加PCB后MAE下降"])
    check("theta 斜率为正", ev["theta_slope"] > 0, f"{ev['theta_slope']}")

    # 加 PCB 后 MAE 反而上升 -> 不得判强
    lvl2, ev2 = core.pcb_signal_strength(sens, rising, 0.105, 0.085,
                                         mae_with_pcb=0.500, mae_without_pcb=0.455,
                                         infer_pcb_exposure=0.06, basket_agreement=True)
    check("加PCB后MAE上升 -> 降级为中等", lvl2 == "中等信号", f"{lvl2}")
    check("C3 标记未通过", not ev2["C3_加PCB后MAE下降"])

    # 多篮子不一致 -> 不得判强
    lvl3, _ = core.pcb_signal_strength(sens, rising, 0.105, 0.085,
                                       mae_with_pcb=0.442, mae_without_pcb=0.455,
                                       infer_pcb_exposure=0.06, basket_agreement=False)
    check("多篮子不一致 -> 不判强信号", lvl3 != "强信号", f"{lvl3}")

    # theta 下降 + 无独立证据 -> 弱
    falling = [0.10 - 0.008 * i for i in range(10)]
    lvl4, _ = core.pcb_signal_strength(sens, falling, 0.105, 0.085,
                                       mae_with_pcb=0.500, mae_without_pcb=0.455,
                                       infer_pcb_exposure=0.0, basket_agreement=False)
    check("证据全否 -> 弱信号", lvl4 == "弱信号", f"{lvl4}")

    # 某窗口 beta_pcb 为负 -> 无信号
    sens_neg = dict(sens)
    sens_neg[60] = np.array([0.78, -0.01, 0, 0.08])
    lvl5, _ = core.pcb_signal_strength(sens_neg, rising, 0.105, 0.085,
                                       mae_with_pcb=0.442, mae_without_pcb=0.455,
                                       infer_pcb_exposure=0.06, basket_agreement=True)
    check("存在窗口 beta_pcb<=0 -> 无信号", lvl5 == "无信号", f"{lvl5}")
    check("空输入 -> 无信号", core.pcb_signal_strength({}, [], 0, 0)[0] == "无信号")
    check("趋势斜率: 上升序列为正", core.trend_slope([1, 2, 3, 4]) > 0)
    check("趋势斜率: 下降序列为负", core.trend_slope([4, 3, 2, 1]) < 0)
    check("趋势斜率: 样本不足返回 0", core.trend_slope([1, 2]) == 0.0)


# ============================================================
# 14b. 数据卫生：收盘价 vs 盘中价隔离（优化意见 §6 事故回归）
# ============================================================
def test_data_hygiene():
    section("14b. 数据卫生（盘中价不得冒充收盘价）")
    KL = {
        "optical": {"688498": {"2026-08-18": 10.0, "2026-08-19": 11.0, "2026-08-20": 12.0},
                    "688048": {"2026-08-19": 20.0, "2026-08-20": 21.0}},
        "pcb": {"002916": {"2026-08-19": 30.0, "2026-08-20": 33.0}},
    }
    snap_live = {"date": "2026-08-20", "settled": False,
                 "prices": {"optical": {"688498": 12.0, "688048": 21.0},
                            "pcb": {"002916": 33.0}}}
    snap_settled = {"date": "2026-08-20", "settled": True, "prices": {}}

    # --- 未结算日期解析 ---
    check("未结算解析: settled=false → 该日期入集合",
          core.intraday_unsettled_dates(snap_live) == {"2026-08-20"})
    check("未结算解析: settled=true → 空集合（已是正式收盘价）",
          core.intraday_unsettled_dates(snap_settled) == set())
    check("未结算解析: 无快照 → 空集合", core.intraday_unsettled_dates({}) == set())
    check("未结算解析: 非法输入不崩", core.intraday_unsettled_dates(None) == set())
    check("未结算解析: 显式 unsettled_dates 列表生效",
          core.intraday_unsettled_dates(
              {"date": "2026-08-20", "settled": True,
               "unsettled_dates": ["2026-08-14"]}) == {"2026-08-14"})

    # --- 剔除 ---
    cleaned, n = core.strip_unsettled(KL, {"2026-08-20"})
    # 3 只个股各含 1 条 2026-08-20
    check("剔除: 两层结构按日期清除", n == 3, f"removed={n}")
    check("剔除: 目标日期确已消失",
          all("2026-08-20" not in px for g in cleaned.values() for px in g.values()))
    check("剔除: 历史日期完好",
          cleaned["optical"]["688498"] == {"2026-08-18": 10.0, "2026-08-19": 11.0})
    check("剔除: 不修改入参（无副作用）",
          "2026-08-20" in KL["optical"]["688498"])
    flat = {"600519": {"2026-08-19": 1.0, "2026-08-20": 2.0}}
    fc, fn_ = core.strip_unsettled(flat, {"2026-08-20"})
    check("剔除: 一层结构（distract.json）同样生效",
          fn_ == 1 and fc["600519"] == {"2026-08-19": 1.0})
    same, n0 = core.strip_unsettled(KL, set())
    check("剔除: 空集合原样返回", n0 == 0 and same is KL)
    _, n_miss = core.strip_unsettled(KL, {"2099-01-01"})
    check("剔除: 不存在的日期 → 0 条", n_miss == 0)

    # --- 剔除后收益序列不含当日（关键：回归样本不被污染）---
    br = core.basket_returns(cleaned["optical"], ["688498", "688048"])
    check("剔除后篮子收益不含未结算日", "2026-08-20" not in br)
    br_dirty = core.basket_returns(KL["optical"], ["688498", "688048"])
    check("对照：未剔除时篮子收益确实含污染日（证明本修复有效）",
          "2026-08-20" in br_dirty)

    # --- 合并快照（仅内存）---
    merged, w = core.merge_intraday_snapshot(cleaned, snap_live)
    check("合并快照: 写回条数正确", w == 3, f"written={w}")
    check("合并快照: 当日价格可用于盘中计算",
          merged["optical"]["688498"]["2026-08-20"] == 12.0)
    check("合并快照: 不污染磁盘副本（cleaned 未被改）",
          "2026-08-20" not in cleaned["optical"]["688498"])
    _, w0 = core.merge_intraday_snapshot(cleaned, {"date": "2026-08-20"})
    check("合并快照: 无 prices → 不写入", w0 == 0)
    _, wneg = core.merge_intraday_snapshot(
        cleaned, {"date": "2026-08-20", "prices": {"optical": {"688498": -1}}})
    check("合并快照: 非正价格被忽略", wneg == 0)
    _, wbad = core.merge_intraday_snapshot(
        cleaned, {"date": "2026-08-20", "prices": {"optical": {"688498": "abc"}}})
    check("合并快照: 非数值被忽略", wbad == 0)

    # --- 幂等性 ---
    once, n1 = core.strip_unsettled(KL, {"2026-08-20"})
    twice, n2 = core.strip_unsettled(once, {"2026-08-20"})
    check("幂等: 二次清洗无额外剔除", n2 == 0 and twice == once)


# ============================================================
# 15. result.json v4 契约
# ============================================================
def test_result_contract():
    section("15. result.json v4 输出契约")
    p = os.path.join(ROOT, "cache", "result.json")
    if not os.path.exists(p):
        out("  --  跳过(cache/result.json 不存在，先跑 src/fund_valuation_v2.py)")
        return
    d = json.load(open(p, encoding="utf-8"))

    required = ["version", "target_date", "estimation_mode", "estimation_label",
                "market_session", "is_official_published", "nav_prev", "nav_prev_date",
                "models", "model_weights", "group_weights", "P_final",
                "bias_correction", "bias_detail", "P_final_corr", "nav_center",
                "band_pct", "confidence", "confidence_score", "confidence_detail",
                "top10_coverage", "undisclosed_ratio", "data_quality",
                "pcb_signal", "pcb_evidence", "date_check_ok",
                "ensemble_audit", "data_hygiene"]
    missing = [k for k in required if k not in d]
    check("v4 必需字段齐全", not missing, f"missing={missing}")

    # --- 集成审计块：权重可被外部复核 ---
    ea = d.get("ensemble_audit") or {}
    check("审计: 闸门/锐度/上限参数齐全",
          all(k in ea for k in ("gate", "mae_power", "weight_cap",
                                "provisional_cap", "dropped", "n_active")),
          f"keys={sorted(ea)}")
    _mu = ea.get("model_mae_used") or {}
    check("审计: 每个模型都有所用 MAE",
          set(_mu.keys()) == set(d.get("models", {}).keys()),
          f"{sorted(_mu)} vs {sorted(d.get('models', {}))}")
    _w = d.get("model_weights", {})
    check("审计: 被闸门剔除的模型权重必须为 0",
          all(abs(_w.get(m, 0.0)) < 1e-9 for m in ea.get("dropped", [])),
          f"dropped={ea.get('dropped')}")
    check("审计: 活跃模型数 = 权重>0 的模型数",
          ea.get("n_active") == sum(1 for v in _w.values() if v > 1e-9),
          f"n_active={ea.get('n_active')} vs {sum(1 for v in _w.values() if v > 1e-9)}")
    if _mu and ea.get("gate"):
        _best = min(_mu.values())
        _thr = (_best + ea.get("mae_floor", 0.02)) * ea["gate"]
        _should_drop = {m for m, v in _mu.items()
                        if v + ea.get("mae_floor", 0.02) > _thr}
        check("审计: 剔除名单与闸门阈值自洽",
              set(ea.get("dropped", [])) == _should_drop,
              f"实际={sorted(ea.get('dropped', []))} 应={sorted(_should_drop)}")
    check("审计: P5 若非真实样本外则必须被临时封顶或剔除",
          ea.get("p5_is_real_oos") is True
          or "P5_个股辅助" in ea.get("dropped", [])
          or "P5_个股辅助" in ea.get("provisional_capped", [])
          or _w.get("P5_个股辅助", 0.0) <= ea.get("provisional_cap", 0.15) + 1e-6,
          f"p5_real={ea.get('p5_is_real_oos')} w={_w.get('P5_个股辅助')}")

    # --- 数据卫生块：盘中价不得进入收盘价序列 ---
    dh = d.get("data_hygiene") or {}
    check("卫生: 字段齐全",
          all(k in dh for k in ("has_intraday_snapshot", "unsettled_dates",
                                "stripped_rows")),
          f"keys={sorted(dh)}")
    check("卫生: 已结算快照不应产生未结算日期",
          not (dh.get("intraday_settled") is True and dh.get("unsettled_dates")),
          f"settled={dh.get('intraday_settled')} unsettled={dh.get('unsettled_dates')}")
    check("卫生: 有未结算日期时必须实际剔除过数据",
          (not dh.get("unsettled_dates")) or dh.get("stripped_rows", 0) > 0,
          f"unsettled={dh.get('unsettled_dates')} stripped={dh.get('stripped_rows')}")

    models = d.get("models", {})
    weights = d.get("model_weights", {})
    check("模型数 4~5 个", 4 <= len(models) <= 5, f"{list(models.keys())}")
    check("权重和 = 1", abs(sum(weights.values()) - 1.0) < 0.005,
          f"sum={sum(weights.values())}")
    check("单模型权重 <= 70%", all(v <= 0.70 + 1e-6 for v in weights.values()),
          f"max={max(weights.values()) if weights else 0}")
    check("模型与权重键一致", set(models.keys()) == set(weights.keys()))
    check("P_final 落在各模型区间内",
          min(models.values()) - 0.01 <= d["P_final"] <= max(models.values()) + 0.01,
          f"P_final={d['P_final']} range=[{min(models.values())},{max(models.values())}]")
    check("band 中心 = 修正后中心",
          abs((d["band_pct"][0] + d["band_pct"][1]) / 2 - d["P_final_corr"]) < 0.02,
          f"band={d['band_pct']} center={d['P_final_corr']}")
    check("净值链一致 nav_prev*(1+pct) = nav_center",
          abs(d["nav_prev"] * (1 + d["P_final_corr"] / 100) - d["nav_center"]) < 0.0002,
          f"{d['nav_prev']}*(1+{d['P_final_corr']}%)={d['nav_center']}")
    check("偏差修正 = P_final_corr - P_final",
          abs((d["P_final_corr"] - d["P_final"]) - d["bias_correction"]) < 0.02,
          f"{d['P_final_corr']}-{d['P_final']} vs {d['bias_correction']}")

    check("estimation_mode 取值合法",
          d["estimation_mode"] in ("settled", "intraday", "next_trading_day"),
          d["estimation_mode"])
    check("日期自检通过", d["date_check_ok"] is True, f"{d.get('date_problems')}")
    check("基准净值日 早于 目标日", d["nav_prev_date"] < d["target_date"],
          f"{d['nav_prev_date']} < {d['target_date']}")
    check("基准日 = 目标日上一交易日",
          d["nav_prev_date"] == core.prev_trade_day(d["target_date"]),
          f"{d['nav_prev_date']} vs {core.prev_trade_day(d['target_date'])}")
    check("覆盖率 = 83.16%", abs(d["top10_coverage"] - 0.8316) < 1e-4,
          f"{d['top10_coverage']}")
    check("覆盖率 + 未披露 = 1",
          abs(d["top10_coverage"] + d["undisclosed_ratio"] - 1.0) < 1e-6)
    check("置信度分 0~100", 0 <= d["confidence_score"] <= 100, f"{d['confidence_score']}")
    check("置信度分档与分数一致",
          (d["confidence"] == "高" and d["confidence_score"] >= 75) or
          (d["confidence"] == "中等" and 50 <= d["confidence_score"] < 75) or
          (d["confidence"] == "低" and d["confidence_score"] < 50),
          f"{d['confidence']}/{d['confidence_score']}")
    check("数据体检通过", d["data_quality"]["ok"] is True, f"{d['data_quality']}")
    check("偏差明细含 med20/med40/ewma",
          all(k in d["bias_detail"] for k in ("med20", "med40", "ewma", "shrink")),
          f"{list(d['bias_detail'].keys())}")
    check("PCB 强信号必须五证齐全",
          d["pcb_signal"] != "强信号" or
          all(d["pcb_evidence"].get(k) for k in
              ("C1_多窗口为正", "C2_替代比例上升", "C3_加PCB后MAE下降", "C4_个股反推独立证据")),
          f"{d['pcb_signal']} {d['pcb_evidence']}")

    # 与 nav.json 交叉核对
    np_path = os.path.join(ROOT, "cache", "nav.json")
    if os.path.exists(np_path):
        nav = json.load(open(np_path, encoding="utf-8"))
        idx = nav["dates"].index(d["nav_prev_date"]) if d["nav_prev_date"] in nav["dates"] else -1
        check("nav_prev 与 nav.json 对应日期一致",
              idx >= 0 and abs(nav["navs"][idx] - d["nav_prev"]) < 1e-9,
              f"idx={idx}")
        if d["is_official_published"] and d.get("official_nav"):
            check("官方净值与 nav.json 末值一致",
                  abs(nav["navs"][-1] - d["official_nav"]) < 1e-9,
                  f"{nav['navs'][-1]} vs {d['official_nav']}")


# ============================================================
if __name__ == "__main__":
    out("=" * 62)
    out("006010 估值系统单元测试 v4 (无网络)")
    out("=" * 62)
    for fn in (test_normalized_valuation, test_q2_weight_unit, test_half_life,
               test_theta_bounds, test_beta_bounds, test_ensemble_weights,
               test_degradation, test_trade_calendar, test_date_consistency,
               test_bias_correction, test_confidence_score, test_data_validation,
               test_error_metrics, test_pcb_signal, test_data_hygiene,
               test_result_contract):
        try:
            fn()
        except Exception as e:
            FAIL += 1
            FAILED.append(f"{fn.__name__}(异常)")
            out(f"  [NG]  {fn.__name__} 抛出异常: {repr(e)[:200]}")
    out("\n" + "=" * 62)
    out(f"结果: {PASS} 通过, {FAIL} 失败  (共 {PASS+FAIL} 项)")
    if FAILED:
        out("失败项:")
        for f in FAILED:
            out(f"  - {f}")
    out("=" * 62)
    sys.exit(1 if FAIL else 0)
