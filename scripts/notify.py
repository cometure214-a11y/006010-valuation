#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
notify.py —— Server酱推送通知（净值更新后）

用法:
  python scripts/notify.py <SCKEY>

从 cache/result.json 读取完整估值，推送到微信（Server酱）。
未配置 SCKEY 时跳过并提示。
"""
import json, os, sys, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "cache")

SCKEY = sys.argv[1] if len(sys.argv) > 1 else ""


def load_result():
    p = os.path.join(CACHE, "result.json")
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def load_last_nav():
    """读取最近一次官方实际净值（由 nav_watch.py 写入）"""
    p = os.path.join(CACHE, "last_nav.json")
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def build_desp(d):
    if not d:
        return "完整估值已更新（结果文件暂缺，请打开页面查看）"
    lines = []
    # ── 第一段：官方实际净值（当天公布） ──
    last = load_last_nav()
    if last:
        chg = last.get("chg")
        try:
            chg_txt = f"{float(chg):+.2f}%"
        except (TypeError, ValueError):
            chg_txt = "--"
        lines.append("**📊 官方净值（实际）**")
        lines.append(f"净值：**{last.get('nav', '--')}** ｜ 当日涨跌：**{chg_txt}**")
        lines.append(f"日期：{last.get('date', '--')}")
        lines.append("")
    # ── 第二段：模型估算（同一交易日） + 偏差对比 ──
    target = d.get("target_date", d.get("cur_date", ""))
    official_chg = d.get("official_chg")
    est = d.get("P_final_corr", 0)
    lines.append(f"**🤖 模型估算（{target}）**")
    lines.append(f"估算涨跌：**{est:+.2f}%**")
    if official_chg is not None:
        diff = float(official_chg) - est
        lines.append(f"模型偏差：**{diff:+.2f}pp**（估算 vs 官方）")
    lines.append(f"预计净值：{d.get('nav_center', '--')}（基准 {d.get('nav_prev', '--')}）")
    lines.append(f"合理区间：{d.get('band_pct', [0, 0])[0]:+.2f}% ~ {d.get('band_pct', [0, 0])[1]:+.2f}%")
    lines.append(f"置信度：{d.get('confidence', '-')} | PCB信号：{d.get('pcb_signal', '-')}")
    models = d.get("models", {})
    if models:
        mline = "  ".join(f"{k.split('_')[1]} {v:+.2f}%" for k, v in models.items())
        lines.append(f"模型明细：{mline}")
    lines.append("")
    lines.append("[点击查看完整估值页面](http://106.55.94.208/)")
    return "\n\n".join(lines)


def main():
    if not SCKEY:
        print("[notify] 未配置 SCKEY（Server酱 SendKey），跳过推送")
        print("[notify] 配置方法：仓库 Settings → Secrets and variables → Actions → New secret")
        print("[notify] 名称 SCKEY，值填你的 SendKey（https://sct.ftqq.com 注册获取）")
        return 0
    d = load_result()
    desp = build_desp(d)
    data = urllib.parse.urlencode({
        "title": "📈 006010 净值已更新",
        "desp": desp,
    }).encode("utf-8")
    url = f"https://sctapi.ftqq.com/{SCKEY}.send"
    try:
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        resp = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
        print(f"[notify] Server酱响应: {resp[:200]}")
    except Exception as e:
        print(f"[notify] 推送失败: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
