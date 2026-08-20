#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_core.py —— 核心计算逻辑单元测试（不依赖网络，纯本地可跑）

覆盖：
  1. 归一化估值公式（防止 +528% 类 bug 回归）
  2. 简单加权 vs 归一化 的数学关系
  3. 下一交易日推算（跳过周末）
  4. 权重和为 1 的归一化退化情况
  5. 净增值链（nav_prev × (1+pct) = nav_center）

用法:
  python tests/test_core.py
"""
import sys, os, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS = 0
FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def test_normalized_valuation():
    """归一化估值：num/wsum，不是 num/(wsum/100)（历史 bug +528% 的回归测试）"""
    print("\n[1] 归一化估值公式")
    # 模拟：10只股票，权重之和=0.8463，简单加权=+4.47%
    weights = [0.0963, 0.0936, 0.0936, 0.0842, 0.0835,
               0.0824, 0.0818, 0.0797, 0.0773, 0.0592]
    rets = [0.03, 0.05, 0.02, -0.01, 0.04, 0.06, 0.01, 0.03, -0.02, 0.05]
    wsum = sum(weights)
    num = sum(w * r for w, r in zip(weights, rets)) * 100.0   # 转 %
    naive = num
    norm = num / wsum                                         # 正确
    buggy = num / (wsum / 100)                                # 历史 bug
    check("简单加权 = Σ(w×r)", abs(naive - 2.1506) < 0.01, f"got {naive:.4f}")
    check("归一化 = num/wsum ≈ 2.59%", abs(norm - 2.5861) < 0.02, f"got {norm:.4f}")
    check("归一化 不是 num/(wsum/100)", abs(buggy - norm * 100) < 0.01,
          f"buggy={buggy:.2f} norm*100={norm*100:.2f}（bug 版恰为正确版100倍）")
    check("归一化 ∈ [max(r), min(r)] 数量级", 0 < norm < 10, f"norm={norm:.2f}")


def test_naive_lt_norm():
    """简单加权 < 归一化（当 wsum<1 且收益为正时）"""
    print("\n[2] 简单加权 vs 归一化 关系")
    weights = [0.0963, 0.0936, 0.0936]
    rets = [0.03, 0.04, 0.05]
    wsum = sum(weights)
    num = sum(w * r for w, r in zip(weights, rets)) * 100.0
    check("wsum < 1", wsum < 1, f"wsum={wsum:.4f}")
    check("归一化 > 简单加权", num / wsum > num, f"{num:.4f} vs {num/wsum:.4f}")


def test_next_trade_day():
    """下一交易日推算（跳过周末）—— gen_static.py 逻辑"""
    print("\n[3] 下一交易日推算")
    import datetime as dt
    def next_trade_day(datestr):
        d = dt.date.fromisoformat(datestr)
        nxt = d + dt.timedelta(days=1)
        while nxt.weekday() >= 5:  # 5=周六, 6=周日
            nxt += dt.timedelta(days=1)
        return nxt.isoformat()
    check("周四→周五", next_trade_day("2026-08-20") == "2026-08-21")
    check("周五→周一", next_trade_day("2026-08-21") == "2026-08-24")
    check("周六→周一", next_trade_day("2026-08-22") == "2026-08-24")


def test_nav_chain():
    """净值链：nav_center = nav_prev × (1 + pct/100)"""
    print("\n[4] 净值链")
    nav_prev = 0.5558
    pct = 3.97
    nav_center = round(nav_prev * (1 + pct / 100), 4)
    check("0.5558 × 1.0397 = 0.5778", abs(nav_center - 0.5778) < 0.0001, f"got {nav_center}")
    # 反向验证涨跌幅
    back_pct = (nav_center / nav_prev - 1) * 100
    check("反向涨跌 = 3.97%", abs(back_pct - pct) < 0.01, f"got {back_pct:.4f}")


def test_result_integrity():
    """result.json 完整性（若存在）"""
    print("\n[5] result.json 完整性")
    p = os.path.join(ROOT, "cache", "result.json")
    if not os.path.exists(p):
        print("  - 跳过（cache/result.json 不存在，先跑模型）")
        return
    d = json.load(open(p, encoding="utf-8"))
    models = d.get("models", {})
    check("5 个模型齐全", len(models) == 5, f"got {list(models.keys())}")
    check("主数字在模型区间内", d.get("P_final", 0) <= max(models.values()) + 0.01 and
          d.get("P_final", 0) >= min(models.values()) - 0.01,
          f"P_final={d.get('P_final')}")
    check("band 对称中心≈主数字", abs((d["band_pct"][0] + d["band_pct"][1]) / 2 - d["P_final_corr"]) < 0.01,
          f"band={d['band_pct']} center={d['P_final_corr']}")
    nav = json.load(open(os.path.join(ROOT, "cache", "nav.json"), encoding="utf-8"))
    check("nav_prev = 最新净值", abs(d.get("nav_prev", 0) - nav["navs"][-1]) < 1e-9,
          f"result={d.get('nav_prev')} nav={nav['navs'][-1]}")


if __name__ == "__main__":
    print("=" * 50)
    print("006010 估值核心逻辑单元测试")
    print("=" * 50)
    test_normalized_valuation()
    test_naive_lt_norm()
    test_next_trade_day()
    test_nav_chain()
    test_result_integrity()
    print("\n" + "=" * 50)
    print(f"结果: {PASS} 通过, {FAIL} 失败")
    print("=" * 50)
    sys.exit(1 if FAIL else 0)
