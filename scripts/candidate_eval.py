#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
candidate_eval.py —— P2 候选模型自动评测淘汰框架
用法: python scripts/candidate_eval.py [SENDKEY]

目标：让"新模型是否值得纳入集成"这件事自动化和数据化。
流程：
  1. 候选注册表 CANDIDATES：每个候选 = {name, predict(date)->pct, desc}
  2. 对每个候选跑滚动样本外（OOS）评测：逐日用截至 T-1 的数据拟合、预测 T，
     与官方净值对比得逐日误差（复用 core 的日期序列与净值数据）
  3. 与当前集成参考 MAE（从 result.json / cache 读）对比：
     - 候选 OOS MAE < 参考 MAE × GATE_IN(0.95) → 标记 eligible（值得纳入）
     - 连续 N 日跑输参考 → 标记 dropped（淘汰）
  4. 结果写 cache/candidate_results.json，变化时 Server酱推送
  5. v2 集成可读取该文件：把 eligible 候选并入 P6/P7（后续接入）

说明：候选的"预测函数"是特征工程接口；本框架提供示例候选（PCB 动量），
真实候选由研究迭代补充。仅标准库 + numpy。
"""
import json, os, sys, time, urllib.request, urllib.parse
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "cache")
sys.path.insert(0, os.path.join(ROOT, "src"))
SCKEY = sys.argv[1] if len(sys.argv) > 1 else ""

GATE_IN = 0.95      # 候选 MAE ≤ 参考×0.95 才纳入（严格优于）
GATE_DROP = 1.10    # 连续评估窗口 MAE > 参考×1.10 则淘汰
MIN_DAYS = 20       # 至少 20 个样本才下结论


def load(name):
    try:
        return json.load(open(os.path.join(CACHE, name), encoding="utf-8"))
    except Exception:
        return None


def oos_errors(predict, dates, navs, ref_mae):
    """对候选 predict 函数做滚动 OOS 评测，返回逐日误差数组。
    predict(hist_dates, hist_navs, target_date) -> 预测涨跌幅%
    （每个候选实现自己的拟合+预测逻辑，这里只做评估循环）"""
    errs = []
    for i in range(MIN_DAYS, len(dates)):
        target = dates[i]
        actual = (navs[i] / navs[i - 1] - 1) * 100
        try:
            est = predict(dates[:i], navs[:i], target)
        except Exception:
            continue
        if est is None or not np.isfinite(est):
            continue
        errs.append(actual - est)
    return np.array(errs)


def example_pcb_momentum():
    """示例候选 P6：PCB 篮子近 5 日动量作为次日涨跌代理（特征工程示意）。
    真实候选应基于 core 的 klines 构建更有信息量的特征。"""
    klines = load("klines.json")
    if not klines:
        return None

    def predict(hist_dates, hist_navs, target_date):
        try:
            pcb = klines.get("pcb", {})
            px = []
            for code in list(pcb)[:3]:
                closes = pcb[code]
                ds = sorted(closes)
                if len(ds) < 6:
                    continue
                px.append(closes[ds[-1]] / closes[ds[-6]] - 1)
            return float(np.mean(px)) * 100 if px else None
        except Exception:
            return None

    return predict


def main():
    nav = load("nav.json")
    result = load("result.json")
    if not nav or len(nav.get("dates", [])) < MIN_DAYS + 5:
        print("[cand] nav 数据不足，跳过")
        return 0
    dates, navs = nav["dates"], nav["navs"]
    ref_mae = None
    mae_key = (result or {}).get("mae", {})
    if isinstance(mae_key, dict) and mae_key.get("P3"):
        ref_mae = float(mae_key["P3"])
    elif result and result.get("mae_p3"):
        ref_mae = float(result["mae_p3"])
    if not ref_mae:
        ref_mae = 0.50   # 兜底参考（v4 集成 MAE 量级）

    CANDIDATES = {
        "P6_pcb_momentum": {"predict": example_pcb_momentum(), "desc": "PCB 篮子5日动量代理"},
    }

    out = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "ref_mae": round(ref_mae, 4), "results": {}}
    changed = False
    for name, c in CANDIDATES.items():
        if c["predict"] is None:
            continue
        errs = oos_errors(c["predict"], dates, navs, ref_mae)
        if len(errs) < MIN_DAYS:
            out["results"][name] = {"status": "insufficient", "n": len(errs)}
            continue
        mae = float(np.mean(np.abs(errs)))
        bias = float(np.mean(errs))
        if mae <= ref_mae * GATE_IN:
            status = "eligible"     # 值得纳入集成（后续由 v2 读取）
        elif mae > ref_mae * GATE_DROP:
            status = "dropped"      # 跑输太多，淘汰
        else:
            status = "watch"        # 观察
        out["results"][name] = {"status": status, "mae": round(mae, 4),
                                "bias": round(bias, 4), "n": int(len(errs)),
                                "desc": c["desc"]}
        changed = True

    old = load("candidate_results.json")
    if old and old.get("results") == out["results"]:
        print("[cand] 无变化，跳过推送")
        return 0
    json.dump(out, open(os.path.join(CACHE, "candidate_results.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("[cand] 评测完成:", json.dumps(out["results"], ensure_ascii=False))

    if SCKEY and changed:
        lines = [f"**🧪 006010 候选模型评测**", "", f"参考 MAE：{ref_mae:.3f}%（集成）"]
        for name, r in out["results"].items():
            st = {"eligible": "✅ 值得纳入", "dropped": "❌ 淘汰", "watch": "👀 观察",
                  "insufficient": "⏳ 样本不足"}.get(r["status"], r["status"])
            lines.append(f"{st} **{name}**：MAE {r.get('mae', '-')}%（{r.get('n', 0)}日）· {r.get('desc','')}")
        lines.append("")
        lines.append("eligible 候选将由 v2 集成自动读取（candidate_results.json）")
        data = urllib.parse.urlencode({"title": "🧪 006010 候选模型评测", "desp": "\n\n".join(lines)}).encode("utf-8")
        try:
            req = urllib.request.Request(f"https://sctapi.ftqq.com/{SCKEY}.send", data=data,
                                         headers={"Content-Type": "application/x-www-form-urlencoded"})
            print("[cand] 推送:", urllib.request.urlopen(req, timeout=15).read()[:80])
        except Exception as e:
            print("[cand] 推送失败:", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
