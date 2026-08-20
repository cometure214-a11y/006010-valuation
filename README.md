# 006010 盘中估值系统

> 基金 **国融融银灵活配置混合C（006010）** 盘中实时净值估算 · 多模型集成 · 云端可访问

![Python](https://img.shields.io/badge/Python-3.11-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Pages](https://img.shields.io/badge/GitHub%20Pages-live-orange)

## 一句话

用公开的季报持仓 + 实时行情 + 统计模型，**盘中估算基金净值涨跌幅**，并在手机端实时查看。不是官方净值，是**估算参考**。

---

## 目录

- [项目结构](#项目结构)
- [快速开始（本地）](#快速开始本地)
- [手机端访问（云端部署）](#手机端访问云端部署)
- [模型方法论](#模型方法论)
- [验证与回测](#验证与回测)
- [数据源与免责声明](#数据源与免责声明)
- [更新日志](#更新日志)

---

## 项目结构

```
006010-valuation/
├── src/                        # 核心模型（方案A/B）
│   ├── fund_valuation_v2.py    # ★ v3 多模型集成估值（主模型）
│   ├── fund_holdings_infer.py  # 方案B 个股反推（LASSO/NNLS/Bootstrap）
│   └── fund_valuation.py       # v1 原型（简单加权法，保留对照）
├── scripts/                    # 数据抓取与页面生成
│   ├── fetch_all.py            # 本地抓取（净值+日K+干扰股）
│   ├── fetch_cloud.py          # 云端抓取（动态日期版，含当天收盘价补齐）
│   ├── gen_static.py           # 静态页生成器（信息分级 v4）
│   ├── 打开估值面板.bat         # 桌面 GUI 启动器
│   └── 手机端估值面板.bat       # 本地 Web 手机访问启动器
├── gui/                        # 可视化入口
│   ├── fund_gui.py             # 桌面 GUI（Tkinter）
│   └── fund_web.py             # 本地 Flask Web（手机局域网访问）
├── docs/                       # GitHub Pages 站点根（index.html 由 gen_static 生成）
│   └── methodology/            # 方法论文档（v1/v2/v3）
├── tests/                      # 测试与回测
│   └── backtest_v3.py          # v3 逐日样本外回测
├── deploy/                     # 云端部署
│   ├── .github/workflows/      # GitHub Actions（部署 Pages）
│   ├── requirements.txt        # 云端依赖
│   ├── 一键更新并推送.bat       # 本地一键全流程→推送
│   └── 云端部署优化说明.md
├── cache/                      # 运行时数据缓存（gitignore）
└── requirements.txt            # 本地依赖
```

## 快速开始（本地）

### 环境

```bash
# 需要 Python 3.10+（Windows 推荐勾选 "Add to PATH"）
python -m venv gui_venv
gui_venv\Scripts\pip install numpy scipy scikit-learn matplotlib
```

### 三步跑通

```bash
# 1. 抓数据（净值 + 日K + 干扰股，约1分钟）
python scripts/fetch_all.py

# 2. 跑方案B 个股反推（可选，供P5使用）
python src/fund_holdings_infer.py

# 3. 跑方案A v3 多模型估值（主结果）
python src/fund_valuation_v2.py
```

结果输出在 `cache/result.json`：

```json
{
  "cur_date": "2026-08-20",
  "nav_prev": 0.5558,
  "P_final_corr": 3.97,
  "nav_center": 0.5778,
  "confidence": "高",
  "pcb_signal": "强信号"
}
```

> `P_final_corr` = 完整模型估算涨跌幅（%）；`nav_center` = 预计净值。

### 可视化

| 入口 | 命令 / 操作 | 说明 |
|---|---|---|
| 桌面 GUI | 双击 `scripts\打开估值面板.bat` | 只看估值，点刷新重跑模型 |
| 本地手机版 | 双击 `scripts\手机端估值面板.bat` | 手机连同一 WiFi 访问 `http://IP:8080` |

---

## 手机端访问（云端部署）

系统已部署到 **GitHub Pages**，手机/电脑浏览器直接访问：

```
https://cometure214-a11y.github.io/006010-valuation/
```

**页面行为**：
- 打开即自动拉取前十大股票**实时行情**（零后端成本）
- 主数字 = 最近一次完整五模型估算（需手动更新）
- 盘中显示"今日盘中估算"，收盘后自动切换"下一交易日估算"

**更新完整估值**（两种方式）：

| 方式 | 操作 | 耗时 |
|---|---|---|
| 本地一键 | 双击 `deploy\一键更新并推送.bat` | ~2 分钟 |
| 云端手动 | GitHub 仓库 → Actions →「006010 盘中估值部署」→ Run workflow | ~3 分钟 |

---

## 模型方法论

完整方法论文档见 `docs/methodology/`，**当前有效版为 v3**：

| 文档 | 说明 |
|---|---|
| `006010_盘中估值方法文档_v3.md` | ★ 当前有效（11 条修改意见逐条落实） |
| `006010_盘中估值方法文档_v2.md` | v2 对照 |
| `006010_盘中估值方法文档.md` | v1 原始思路 |

### 核心架构（v3）

```
实时行情/收盘价
   ├─ P1 Q2静态      Σ(w_i × r_i) / Σ(w_i)           （Q2 前十大权重）
   ├─ P2 调仓替代    r_q2 + θ_pcb·(r_pcb−r_q2) + θ_m·(r_m−r_q2)
   ├─ P3 行业因子    滚动约束回归（β≥0, Σβ≤1, 半衰期20日）
   ├─ P4 层级组合    行业比例动态 × 行业内Q2相对权重
   └─ P5 个股辅助    LASSO/NNLS 反推（仅作辅助，不认定持仓）
        ↓
  加权集成（权重 = 1/(MAE+0.05%)，单模型≤70%）
        ↓
  + 偏差修正（历史误差中位数）→ 主数字 P_final_corr
```

**关键指标**：合理区间（±1.5σ）、置信度三维（MAE+模型分歧+θ稳定性）、PCB 调仓三级信号（弱/中/强）。

> ⚠️ 光通信与 PCB 相关性高（r≈0.71），调仓比例存在识别误差；系统定位为"行业暴露估计"，非精确个股持仓。

---

## 验证与回测

```bash
python tests/backtest_v3.py
```

| 指标 | 数值 |
|---|---|
| 回测窗口 | 2026 Q3 逐日样本外 |
| MAE（偏差修正后） | **0.80%** |
| 方向命中 | 3/3 全对 |
| 最难样本（-8.67% 大跌日） | 估 -8.06%（误差 0.61%） |

**实战校验（08-20）**：模型估 +3.98% vs 官方 +3.23%，误差 +0.75pp，方向正确。

---

## 数据源与免责声明

| 数据 | 来源 | 说明 |
|---|---|---|
| 官方净值 | 东方财富 `api.fund.eastmoney.com/f10/lsjz` | 每日收盘后更新 |
| 前十大持仓 | 东方财富 F10 jjcc（Q2 季报） | 季度披露，期间可能调仓 |
| 日K/实时行情 | 腾讯 `web.ifzq.gtimg.cn` / `qt.gtimg.cn` | 实时/收盘 |

> ⚠️ **免责声明**：本项目所有输出均为统计模型估算，**非官方净值**。基金可能发生季报后调仓、持仓变动等，估算存在误差。投资决策请以基金公司官方公布净值为准，本系统不构成任何投资建议。

---

## 更新日志

- **v4 (2026-08-20)**：页面信息分级重做（常驻层/关注层/详情层），sparkline、可视区间条、行业热力条；修复"归一化估值 ×528%"bug；修复当天收盘价缺失导致日期错配；标签动态显示"今日盘中/下一交易日"。
- **v3 (2026-08-20)**：五模型集成 + 偏差修正 + PCB 三级信号 + 置信度三维，回测 MAE 0.80%。
- **v2 (2026-08-20)**：三层优化（行业暴露回归）、方案B个股反推、GUI。
- **v1 (2026-08-20)**：Q2 权重 × 实时行情简单加权原型。
