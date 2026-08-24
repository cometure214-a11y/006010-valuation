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
- [多端协作规范](#多端协作规范-办公家-github-腾讯云)

---

## 多端协作规范（办公/家/GitHub/腾讯云）

本项目由**办公电脑 + 家里电脑 + AI 助手**共同维护，GitHub 为代码唯一真源，腾讯云负责部署：

| 文档 | 用途 | 谁看 |
|------|------|------|
| [AGENTS.md](./AGENTS.md) | AI 协作规则（数据纪律/仓库拓扑/已知坑） | AI 助手、开发者 |
| [CONTRIBUTING.md](./CONTRIBUTING.md) | 多端开发流程（pull→改→验证→CHANGELOG→push） | 人 |
| [CHANGELOG.md](./CHANGELOG.md) | 变更日志（每次修改必须追加） | 所有人 |
| [docs/部署手册.md](./docs/部署手册.md) | 腾讯云运维手册（cron/日志/故障排查） | 服务器运维 |
| [docs/使用指南.md](./docs/使用指南.md) | 日常使用（看估值/更新/部署） | 使用者 |

**核心规则**：改代码前 `git pull`，改完更新 CHANGELOG 再 push；服务器只 `git pull` 不直接改代码。

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

结果输出在 `cache/result.json`。

## 手机端访问（云端部署）

已部署到腾讯云：**http://106.55.94.208/**（页面自动拉取实时行情，60秒自动刷新）。

更多部署/运维细节见 [docs/部署手册.md](./docs/部署手册.md)。

## 模型方法论

完整方法论见 [006010_盘中估值方法文档_v4.md](./006010_盘中估值方法文档_v4.md)。

核心思路：
1. **多模型集成**：P1 静态加权 / P2 调仓替代 / P3 行业因子 / P4 层级组合 / P5 个股反推
2. **分组去重**：P3/P4 同源共享权重，避免双倍计权
3. **偏差修正**：EWMA+med20 融合，分歧过大自动收缩
4. **置信度**：0-100 连续分（MAE/分歧/θ稳定/偏差/数据质量）

## 验证与回测

- 回测脚本：`tests/backtest_v3.py`
- 集成+偏差修正 MAE：**0.476%**（v4 优化后）
- 急跌日 MAE：0.345%

## 数据源与免责声明

- 官方净值：东方财富 / 新浪公开接口
- 持仓：天天基金季报（jjcc）
- 行情：腾讯 / 东方财富公开接口
- **免责声明**：所有数据来自公开接口，估算结果仅供参考，不构成投资建议

## 更新日志

历史变更见 [CHANGELOG.md](./CHANGELOG.md)。