#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fund_gui.py —— 006010 盘中估值 · 极简面板

设计原则：GUI 只显示"估值结果"，不展示任何计算/算法/过程细节。
计算过程与算法交流请走对话；本面板职责 = 估值数字 + 刷新。

数据来源：
  方案A 行业暴露模型  cache/result.json   （主估值：中心/区间/置信度）
  方案B 精确个股反推  cache/infer.json    （并行参考：LASSO/NNLS 今日涨跌）

刷新：重跑 fund_valuation_v2.py + fund_holdings_infer.py（需联网取实时行情）
"""
import os, sys, json, subprocess, tkinter as tk
from tkinter import ttk, messagebox

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "cache")
VENV_PY = sys.executable

# 暗色主题
BG = "#1e1e1e"; PANEL = "#252526"; FG = "#d4d4d4"; ACC = "#4ec9b0"
UP = "#f0533d"      # 涨（红，A股习惯）
DOWN = "#2e9e5b"    # 跌（绿，A股习惯）

def load_json(name):
    p = os.path.join(CACHE, name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None

class App:
    def __init__(self, root):
        self.root = root
        root.title("006010 盘中估值")
        root.geometry("460x420")
        root.configure(bg=BG)
        root.resizable(False, False)

        # 顶栏
        top = tk.Frame(root, bg=BG)
        top.pack(fill="x", padx=16, pady=(14, 0))
        tk.Label(top, text="006010 国融融银灵活配置混合 C", bg=BG, fg=FG,
                 font=("Microsoft YaHei", 12, "bold")).pack(side="left")
        self.btn = tk.Button(top, text="刷新", command=self.refresh, bg=ACC,
                             fg="#111", relief="flat", padx=12, cursor="hand2",
                             font=("Microsoft YaHei", 10, "bold"))
        self.btn.pack(side="right")

        # 副标题
        self.sub = tk.Label(root, text="盘中估值", bg=BG, fg="#8a8a8a",
                            font=("Microsoft YaHei", 10))
        self.sub.pack(anchor="w", padx=18, pady=(2, 4))

        # 主数字：今日估算涨跌幅
        self.chg_label = tk.Label(root, text="--", bg=BG, fg=FG,
                                  font=("Microsoft YaHei", 52, "bold"))
        self.chg_label.pack(pady=(14, 0))

        # 预计净值
        self.nav_label = tk.Label(root, text="预计净值 --", bg=BG, fg=FG,
                                  font=("Microsoft YaHei", 18))
        self.nav_label.pack(pady=(2, 0))

        # 信息区
        self.info = tk.Label(root, text="", bg=BG, fg=FG, justify="left",
                             font=("Microsoft YaHei", 11))
        self.info.pack(pady=(18, 0))

        # 参考区（方案B，一行小字）
        self.ref = tk.Label(root, text="", bg=BG, fg="#8a8a8a", justify="left",
                            font=("Microsoft YaHei", 9))
        self.ref.pack(pady=(10, 0))

        # 底部说明
        tk.Label(root, text="算法与计算细节请与助手在对话中交流；此处仅展示估值结果。",
                 bg=BG, fg="#5a5a5a", font=("Microsoft YaHei", 8)).pack(side="bottom", pady=8)

        self.draw()

    # ---------- 显示 ----------
    def draw(self):
        d = load_json("result.json")
        if not d:
            self.chg_label.config(text="--", fg=FG)
            self.nav_label.config(text="暂无估值，点「刷新」运行模型")
            return
        # v3 格式：P_final_corr（偏差修正后中心）
        center = d.get("P_final_corr", d.get("L3_center_pct", 0))
        band = d.get("band_pct", [0, 0])
        color = UP if center > 0 else (DOWN if center < 0 else FG)
        sign = "+" if center > 0 else ""
        self.chg_label.config(text=f"{sign}{center}%", fg=color)
        self.nav_label.config(text=f"预计净值  {d.get('nav_center', '--')}")
        models = d.get("models", {})
        m_lines = "  ".join(f"{k.split('_')[1]}:{v:+.2f}%" for k, v in models.items())
        self.info.config(text=(
            f"合理区间    {band[0]}% ~ {band[1]}%\n"
            f"置信度      {d.get('confidence', '-')}（样本外MAE {d.get('mae', {}).get('P3', '-')}%，"
            f"分歧 {d.get('model_spread_pct', '-')} pct）\n"
            f"基准        {d.get('nav_prev', '-')}（{d.get('cur_date', '-')} 收盘）\n"
            f"偏差修正    {d.get('bias_correction', 0):+.2f}%（历史样本外误差中位数）"))
        self.ref.config(text=f"多模型: {m_lines}  |  PCB信号: {d.get('pcb_signal', '-')}（θ={d.get('theta_pcb', 0)*100:.1f}%）")

    # ---------- 刷新 ----------
    def refresh(self):
        self.btn.config(state="disabled", text="计算中…")
        self.root.update_idletasks()
        ok = True
        err_msgs = []
        # 先跑 infer（生成最新 infer.json），再跑 v2（读取最新 P5），避免 P5 用旧数据
        for script in ("src/fund_holdings_infer.py", "src/fund_valuation_v2.py"):
            try:
                r = subprocess.run([VENV_PY, os.path.join(HERE, script)],
                                   capture_output=True, text=True,
                                   encoding="utf-8", errors="replace",
                                   timeout=240, cwd=HERE)
                if r.returncode != 0:
                    ok = False
                    tail = (r.stderr or r.stdout or "").strip().splitlines()[-4:]
                    err_msgs.append(f"[{script}] 退出码 {r.returncode}\n" + "\n".join(tail))
            except Exception as e:
                ok = False
                err_msgs.append(f"[{script}] 异常: {e}")
        # 完整日志落盘便于排查
        try:
            with open(os.path.join(CACHE, "gui_refresh.log"), "a", encoding="utf-8") as f:
                f.write("\n".join(err_msgs) + "\n")
        except Exception:
            pass
        self.btn.config(state="normal", text="刷新")
        if ok:
            self.draw()
        else:
            msg = "\n\n".join(err_msgs) or "未知错误"
            messagebox.showerror("刷新失败", f"模型运行出错，详细信息：\n\n{msg}\n\n"
                                            f"完整日志：cache/gui_refresh.log")

if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
