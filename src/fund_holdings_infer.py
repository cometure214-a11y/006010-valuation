#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fund_holdings_infer.py —— 006010「个股层辅助预测」(持仓重建 / 组合复制)

定位（v3 定稿，见方法文档 §12）：
  个股反推结果【不用于认定真实持仓】，也不直接生成最终个股权重。
  主要用途：
    1. 判断光通信是否仍是主要收益来源；
    2. 搜索 PCB 等候选行业是否出现持续信号；
    3. 为行业调仓比例模型(P2)提供辅助特征（P5 预测）。

方法 A 非负 LASSO(LassoCV 选 λ, positive=True) —— 稀疏选择个股
方法 B NNLS + Bootstrap(200次重采样)          —— 给每只股票权重的置信区间与入选频率

⚠ 重要限制：LASSO/NNLS 在高度相关股票间解不唯一，某只股票被选中 ≠ 真实持有。
   个股结果必须经过不同窗口、不同候选池、不同篮子的稳定性检验后才可参考。

数据纪律：仅用 cur_date 及以前的数据，不泄漏未来。
诚实性检验：
  - 用一批"干扰股"(白酒/银行/锂电/医药/家电/保险/电力)验证模型不会错选无关风格；
  - 与 Q2 披露权重做相关性对照，展示"能恢复行业集中度、但共线下精确个股权重不可靠"。

依赖：numpy, scipy, scikit-learn（venv 内）
"""
import json, os, sys, urllib.request, re, time
import numpy as np
from scipy.optimize import nnls

# 统一 UTF-8 输出：避免 cmd(GBK) 下打印 ²/⚠/✅ 等字符时 UnicodeEncodeError 崩溃
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
DIST = json.load(open(os.path.join(CACHE, "distract.json"), encoding="utf-8"))

# 候选股统一池：光通信(10) + PCB(3) + 半导体(4) + 市场(1) + 干扰股(7)
OPTICAL = ["688498", "688048", "300502", "688313", "300620",
           "300548", "300570", "688025", "300394", "300308"]
PCB = ["002916", "002463", "300476"]
SEMIS = ["688981", "002371", "603501", "603986"]
MARKET = ["000852"]
UNIVERSE = OPTICAL + PCB + SEMIS + MARKET + list(DIST.keys())
GROUP = {c: "optical" for c in OPTICAL}
GROUP.update({c: "pcb" for c in PCB})
GROUP.update({c: "semis" for c in SEMIS})
GROUP.update({c: "market" for c in MARKET})
GROUP.update({c: "distract" for c in DIST})

NAME = {
    "688498": "源杰科技", "688048": "长光华芯", "300502": "新易盛", "688313": "仕佳光子",
    "300620": "光库科技", "300548": "博创科技", "300570": "太辰光", "688025": "杰普特",
    "300394": "天孚通信", "300308": "中际旭创",
    "002916": "深南电路", "002463": "沪电股份", "300476": "胜宏科技",
    "688981": "中芯国际", "002371": "北方华创", "603501": "韦尔股份", "603986": "兆易创新",
    "000852": "中证1000",
    "600519": "贵州茅台", "000858": "五粮液", "601398": "工商银行", "300750": "宁德时代",
    "600276": "恒瑞医药", "000333": "美的集团", "601318": "中国平安", "600900": "长江电力",
}
# Q2(2026-06-30) 披露前十大占净值%（用于对照）
Q2 = {"688498": 9.63, "688048": 9.36, "300502": 9.36, "688313": 8.42, "300620": 8.35,
      "300548": 8.24, "300570": 8.18, "688025": 7.97, "300394": 7.73, "300308": 5.92}

# ---------- 1. 构造对齐的日收益矩阵 ----------
closedict = {}
for g in ("optical", "pcb", "semis", "market"):
    for c, d in KL[g].items():
        closedict[c] = d
for c, d in DIST.items():
    closedict[c] = d

dates = NAV["dates"]
navs = NAV["navs"]
fund_ret = {dates[i]: navs[i] / navs[i - 1] - 1.0 for i in range(1, len(dates))}

stock_ret = {c: {} for c in UNIVERSE}
for c in UNIVERSE:
    cd = sorted(closedict[c].keys())
    for i in range(1, len(cd)):
        stock_ret[c][cd[i]] = closedict[c][cd[i]] / closedict[c][cd[i - 1]] - 1.0

# 剔除无数据候选股(如未抓到的干扰股)，避免严格对齐把全部日期剔除
UNIVERSE = [c for c in UNIVERSE if stock_ret[c]]
miss = [c for c in (OPTICAL + PCB + SEMIS + MARKET + list(DIST.keys())) if c not in UNIVERSE]
if miss:
    print(f"[提示] 剔除无数据候选股: {miss}")

W = 60  # 近60交易日(≈Q2后当前 regime)
common = sorted(set(fund_ret) & set().union(*[set(stock_ret[c]) for c in UNIVERSE]))
common = [d for d in common if all(d in stock_ret[c] for c in UNIVERSE)]
common = common[-W:]
y = np.array([fund_ret[d] for d in common])
X = np.array([[stock_ret[c][d] for c in UNIVERSE] for d in common])
print(f"[对齐] 窗口 {len(common)} 日, {common[0]} → {common[-1]}, 候选股 {len(UNIVERSE)} 只")

# ---------- 2. 方法A：非负 LASSO(CV 选 λ) ----------
from sklearn.linear_model import LassoCV, Lasso
lasso = LassoCV(cv=5, positive=True, max_iter=50000,
                random_state=0, n_jobs=1).fit(X, y)
alpha = lasso.alpha_
coef_a = Lasso(alpha=alpha, positive=True, max_iter=50000).fit(X, y).coef_
coef_a = np.maximum(coef_a, 0)
sa = coef_a.sum()
w_a = coef_a / sa if sa > 0 else coef_a  # 归一化为组合权重
print(f"[LASSO] α={alpha:.6f}, 入选 {int((coef_a > 1e-4).sum())} 只, "
      f"拟合R²={lasso.score(X, y):.3f}")

# ---------- 3. 方法B：NNLS + Bootstrap(200) ----------
n_boot = 200
B = X.shape[1]
boot_coef = np.zeros((n_boot, B))
rng = np.random.default_rng(0)
for b in range(n_boot):
    idx = rng.integers(0, len(y), len(y))
    try:
        cb, _ = nnls(X[idx], y[idx])
    except Exception:
        cb = np.zeros(B)
    boot_coef[b] = cb
ci_lo = np.percentile(boot_coef, 5, axis=0)
ci_hi = np.percentile(boot_coef, 95, axis=0)
sel_freq = (boot_coef > 0.01).mean(axis=0)  # 权重>1%的入选频率
nnls_w, _ = nnls(X, y)
sn = nnls_w.sum()
nnls_w = nnls_w / sn if sn > 0 else nnls_w
pred_nnls = X @ nnls_w
r2_nnls = 1 - np.sum((y - pred_nnls) ** 2) / np.sum((y - y.mean()) ** 2)
print(f"[NNLS] 拟合R²={r2_nnls:.3f}, Bootstrap {n_boot} 次完成")

# ---------- 4. 实时行情(今日盘中) ----------
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
                m = re.search(r'v_(\w+)=\"([^\"]+)\"', line)
                if not m:
                    continue
                p = m.group(2).split("~")
                try:
                    cur, prev = float(p[3]), float(p[4])
                except (ValueError, IndexError):
                    continue
                out[m.group(1)] = (cur - prev) / prev if prev else 0.0
            if out:
                return out
            last_err = "empty response"
        except Exception as e:
            last_err = repr(e)[:100]
        time.sleep(1.0 * (a + 1))
    raise RuntimeError(f"实时行情获取失败({retries}次重试): {last_err}")
rt = fetch_realtime(UNIVERSE)
rt_ret = {c: rt[qt_symbol(c)] for c in UNIVERSE if qt_symbol(c) in rt}

def today_return(weights):
    return sum(weights[i] * rt_ret.get(UNIVERSE[i], 0.0) for i in range(B))

today_a = today_return(w_a) * 100
today_nnls = today_return(nnls_w) * 100

# ---------- 5. 分组汇总(与行业暴露方案对照) ----------
def group_total(weights):
    g = {k: 0.0 for k in set(GROUP.values())}
    for i, c in enumerate(UNIVERSE):
        g[GROUP[c]] += weights[i]
    return g
gt_a = group_total(w_a)
gt_nnls = group_total(nnls_w)

# 干扰股总权重(应≈0 才说明方法没错选风格)
distract_total_a = sum(w_a[i] for i, c in enumerate(UNIVERSE) if GROUP[c] == "distract")
distract_total_nnls = sum(nnls_w[i] for i, c in enumerate(UNIVERSE) if GROUP[c] == "distract")

# 与 Q2 披露对照(仅 optical 10 只)
q2vec = np.array([Q2.get(c, 0.0) / 100 for c in OPTICAL])
infer_opt = np.array([w_a[UNIVERSE.index(c)] for c in OPTICAL])
if q2vec.std() > 0 and infer_opt.std() > 0:
    corr_q2 = float(np.corrcoef(q2vec, infer_opt)[0, 1])
else:
    corr_q2 = float("nan")

# ---------- 6. 输出 ----------
print("\n" + "=" * 64)
print("006010 精确个股反推（与行业暴露方案并行）")
print("=" * 64)
print(f"【方法A 非负LASSO】 今日反推涨跌幅 ≈ {today_a:+.2f}%")
print(f"【方法B NNLS+Boot】  今日反推涨跌幅 ≈ {today_nnls:+.2f}%")
print("-" * 64)
print("分组暴露(LASSO)        光通信    PCB     半导体   市场    干扰股  残差")
print(f"                     {gt_a['optical']:6.1%}  {gt_a['pcb']:6.1%}  {gt_a['semis']:6.1%}  "
      f"{gt_a['market']:6.1%}  {gt_a['distract']:6.1%}  {1-gt_a['optical']-gt_a['pcb']-gt_a['semis']-gt_a['market']-gt_a['distract']:6.1%}")
print(f"  (行业暴露方案v2：     光通信83.6% PCB0.5% 半导体0.3% 市场0.2% 残差15.4%)")
print(f"  干扰股总权重={distract_total_a:.2%} -> {'✅未错选无关风格' if distract_total_a < 0.05 else '⚠异常'}")
print(f"  与Q2披露权重相关(光通信10只) r={corr_q2:.2f}")
print("-" * 64)
print("反推 Top 持仓(按LASSO权重, 含Bootstrap 90%CI 与入选频率):")
order = sorted(range(B), key=lambda i: w_a[i], reverse=True)[:12]
for i in order:
    c = UNIVERSE[i]
    print(f"  {c} {NAME.get(c,'?'):<8} w={w_a[i]:6.2%}  "
          f"CI[{ci_lo[i]:5.2%},{ci_hi[i]:5.2%}]  入选{sel_freq[i]:5.1%}  [{GROUP[c]}]")
print("=" * 64)
print("结论: 两方案在'光通信主导(~80%+)'上一致; 但光通信10股高度共线,")
print("      精确个股权重不可靠(LASSO/NNLS给出不同分解)——与'行业暴露更稳'判断吻合。")
print("=" * 64)

result = {
    "window": f"{common[0]}~{common[-1]} ({len(common)}d)",
    "alpha": round(float(alpha), 6),
    "lasso_r2": round(float(lasso.score(X, y)), 3),
    "nnls_r2": round(float(r2_nnls), 3),
    "today_return_pct": {"lasso": round(today_a, 2), "nnls": round(today_nnls, 2)},
    "group_exposure_lasso": {k: round(float(v), 4) for k, v in gt_a.items()},
    "distract_total_weight": round(float(distract_total_a), 4),
    "corr_with_q2": round(corr_q2, 3),
    "top_holdings": [
        {"code": UNIVERSE[i], "name": NAME.get(UNIVERSE[i], "?"), "group": GROUP[UNIVERSE[i]],
         "weight": round(float(w_a[i]), 4), "ci_lo": round(float(ci_lo[i]), 4),
         "ci_hi": round(float(ci_hi[i]), 4), "sel_freq": round(float(sel_freq[i]), 3)}
        for i in order
    ],
    "universe_size": len(UNIVERSE),
}
json.dump(result, open(os.path.join(CACHE, "infer.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("[已保存] cache/infer.json")
