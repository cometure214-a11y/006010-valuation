# AGENTS.md — AI 协作规范（006010 估值系统）

> 本文档面向**任何 AI 助手 / 开发者**在修改本项目时必须遵守的规则。
> 修改本项目前，**必须先读本文档**；修改后，必须按 §4 更新变更记录。

---

## §1 项目是什么

- **用途**：基金 006010（国融融银灵活配置混合C）盘中净值估算，多模型集成 + 偏差修正
- **主模型**：`src/fund_valuation_v2.py`（v4 多模型集成，读 `src/core.py` 纯函数层）
- **数据纪律（红线）**：只用 target_date 及以前的数据；**当天官方净值绝不参与当天参数估计**
- **输出**：`cache/result.json`（结构化结果）→ `scripts/gen_static.py` 渲染 `docs/index.html`

## §2 仓库 / 部署拓扑（关键！）

```
┌─────────────────────┐      ┌──────────────────────┐
│  办公电脑（开发）     │      │  家里电脑（开发）      │
│  改代码 → push       │      │  改代码 → push        │
└─────────┬───────────┘      └──────────┬───────────┘
          │                             │
          ▼                             ▼
      GitHub 仓库（代码唯一真源）
      github.com/cometure214-a11y/006010-valuation
          │
          ▼ (git pull / 手动同步)
     腾讯云轻量服务器 lhins-6tc2yllt（ap-guangzhou）
     /opt/006010-valuation  ← 部署目录
     公网页面 http://106.55.94.208/
```

**规则：**
1. **GitHub 是代码唯一真源**。所有机器都从 GitHub 拉取，不直接改服务器上的代码
2. **腾讯云只做部署和定时任务**，代码修改一律走 GitHub → 服务器 `git pull`
3. 服务器上 `/opt/006010-valuation/run_daily.sh`、`watch_evening.sh`、`run_evening.sh` 是部署脚本（不入库），它们只调用 `scripts/*.py`

## §3 多端协作工作流（每次修改必读）

**双电脑开发同一仓库，必须遵守：**

1. **改代码前**：`git pull origin main` 拉最新（防止基于旧代码改）
2. **改代码后**：`git add + commit + push`，commit message 用中文描述做了什么（如 `feat: 新增xxx` / `fix: 修复xxx`）
3. **绝不用 `git push -f`**（覆盖别人的提交）
4. **冲突处理**：如果 push 被拒（远端有新提交），先 `git pull --rebase` 再 push
5. **临时文件不入库**：`cache/` 运行时数据已 gitignore；`venv/` 不入库；部署脚本（run_*.sh）在服务器上创建，不入库

**模型修改必须遵守（数据纪律）：**
- 修改估值算法 → 更新 `006010_盘中估值方法文档_v4.md` 对应章节
- 修改 core.py 纯函数 → 确保 `tests/backtest_v3.py` 仍能跑通
- 新模型候选 → 加进 `scripts/candidate_eval.py` 的 CANDIDATES 注册表（自动评测淘汰）

## §4 变更记录规范（每次修改后必须更新）

**每次代码变更，同步更新 `CHANGELOG.md`**：

```markdown
## [2026-08-24] 一句话标题
- 改了什么（文件路径 + 一句话）
- 为什么改（背景/问题）
- 影响（对估值结果/部署的影响）
- 验证方式（怎么确认改对了）
```

**大变更（改动核心算法/数据结构）额外要求：**
- 在 `docs/复盘_YYYYMMDD.md` 写复盘：改了啥 / 回测对比 / 结论
- 更新方法论文档版本号（v4 → v4.x）

## §5 服务器部署须知（腾讯云）

- **部署目录**：`/opt/006010-valuation`（git 仓库，可 `git pull`）
- **Python 环境**：`/opt/006010-valuation/venv/`（必须用 venv，系统 Python 有 Debian 版 numpy 不可覆盖）
- **cron 定时任务**（工作日）：
  - `09:00` + `15:10` → `run_daily.sh`（持仓更新→抓数→反推→估值→页面→误差→看门狗）
  - `19:40` → `watch_evening.sh`（每5分钟查官方净值，公布即全流程+微信推送）
  - `*/10 9-23` → `health_check.py`（看门狗）
  - `周日 20:30` → `candidate_eval.py`（候选模型评测）
- **微信推送**：Server酱，SendKey 存 `/opt/006010-valuation/cache/sckey.txt`（不入库！）
- **Nginx**：站点 `valuation` → root `/opt/006010-valuation/docs`，端口 80

## §6 已知坑（务必避免）

1. **fund_valuation_v2.py L190 曾经有语法错误**（`errs_p3\,errs_p1` 行合并），已修复。任何编辑该文件后先 `python3 -m py_compile` 验证
2. **core.py 的 MAE_GATE**：1.00（择优激进，可能单模型独大）vs 1.30（保留多模型，实测 MAE 更低）。改动前确认意图
3. **fetch_cloud.py 抓盘中价**：收盘前（<15:05）抓到的当天价进 intraday.json（不参与回归），收盘后才是正式收盘价。改动注意时间语义
4. **venv 依赖**：装新包用 `venv/bin/pip install`，别用系统 pip
5. **GitHub 推送**：如果本机无 git 凭据，可用 WorkBuddy GitHub MCP（push_files），但**必须传完整文件内容**（曾因占位符内容覆盖过文件，已恢复）

## §7 数据源（公开接口，改动需谨慎）

| 数据 | 接口 | 用途 |
|------|------|------|
| 官方净值 | 东财 `nav_watch.py` / 新浪估值接口 | 晚间核验、误差日报 |
| 持仓 | 东财 jjcc（`update_holdings.py`）/ pingzhongdata | 前十大自动更新 |
| 日K行情 | 腾讯 `fetch_cloud.py` | 滚动回归 |
| 实时行情 | 腾讯 qt.gtimg.cn / 东财 push2delay | 盘中估算 |

---

*最后更新：2026-08-24（多端协作规范建立）*