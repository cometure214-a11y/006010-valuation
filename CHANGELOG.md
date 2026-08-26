# CHANGELOG — 006010 估值系统变更日志

> 格式：`## [日期] 一句话标题` → 改动 / 原因 / 影响 / 验证
> **规则：每次代码变更必须在此追加一条记录（见 CONTRIBUTING.md §三）**

---

## [2026-08-26] 微信推送去重 + 详情页链接指向腾讯云最新版
- 改动：`scripts/notify.py` 详情页链接由 GitHub Pages 改为 `http://106.55.94.208/`；`.github/workflows/nav-watch.yml` 的 Server酱推送步骤停用（`if: ${{ false }}`）
- 原因：GitHub Actions 与腾讯云 cron 双通道各自检测净值并推送，晚间微信收到多条重复推送；且推送详情页原指向 GitHub Pages，非腾讯云每日更新版本
- 影响：微信仅收腾讯云 cron 一条推送，点击详情直达最新估值页面；GitHub Actions 仍正常跑模型、部署 Pages，仅不再推送
- 验证：notify.py 链接替换；workflow 语法校验通过；腾讯云页面 `http://106.55.94.208/` HTTP 200

## [2026-08-25] 误差日报加入「基线对照」——用数据验证集成增益
- 改动：`scripts/daily_errors.py` 新增 `load_baseline_est()`（简单加权法公式① Σwᵢ×rᵢ/Σwᵢ）
  - 误差库记录新增 `base` / `base_err` 字段（与模型误差同日积累，保留 90 日）
  - 日报推送增加：基线估算、当日胜负（集成 vs 基线）、基线对照 MAE + 增益（样本≥3日）
- 原因：与用户讨论"简单加权法 vs 集成系统"时发现——集成是否真的优于基线，需要同口径数据证明，不能只靠方法论说服
- 影响：每晚净值公布后，日报自动输出「集成 MAE vs 基线 MAE」，攒够样本即定量验证进化增益
- 验证：本地实测 8/20 场景——基线 +5.28%（高估 2.05pp）vs 集成估算更接近官方 +3.23%；基线函数与手工计算一致

## [2026-08-24] 页面 v2 改版（基于多模态 AI 评审意见）
- 改动：`scripts/gen_static.py` 全面重构
  - 首屏重构：只留 大数字+置信度大白话+大幅走势图（44→110px）
  - 配色统一：红涨绿跌；走势图折线随涨跌变色；进度条同色系渐变
  - 桌面响应式：>=1024px 左右分栏（左65% 核心 / 右35% 辅助）
  - 普通/高级模式：默认普通，右上角开关切高级（localStorage 记忆）
  - 文案精简：删重复日期/净值；「较上一交易日」→「基准净值」；开发说明默认隐藏
  - 新增「单模型独大」警示条（权重>70% 时显示）
- 原因：AI 评审指出首屏信息密度高/走势图权重不足/桌面无响应式/专业术语噪音
- 影响：移动端一眼看懂，桌面端宽屏利用，专业指标收纳到高级模式
- 验证：服务器实测生成成功，公网 HTTP 200，所有新特性在页面中

## [2026-08-24] 多端协作规范建立 + Server酱推送打通
- 改动：新增 `AGENTS.md`、`CONTRIBUTING.md`、`CHANGELOG.md`、`docs/部署手册.md`；服务器 run_daily.sh / watch_evening.sh 接入新脚本
- 原因：办公/家里双电脑协作开发，需统一流程规范；开通微信推送
- 影响：双端开发有据可依；晚间净值公布后微信秒推
- 验证：Server酱测试推送成功（pushid 52474167）

## [2026-08-24] 修复 fund_valuation_v2.py 语法错误（L190）
- 改动：`src/fund_valuation_v2.py` L190，`errs_p3\,errs_p1 = ...` 一行拆回两行
- 原因：两行被错误合并成一行（多出 `\,`），导致主脚本 SyntaxError 无法运行
- 影响：主估值脚本恢复可运行；GitHub 远端同步修复（commit fac66d6）
- 验证：`python3 -m py_compile` 通过；完整流程跑通（08-25 预测 -4.09%）

## [2026-08-24] 进化升级：持仓自动更新/误差日报/看门狗/候选评测/误差走势图
- 改动：新增 `scripts/update_holdings.py`（季报持仓自动更新）、`scripts/daily_errors.py`（误差日报+error_history.json）、`scripts/health_check.py`（看门狗）、`scripts/candidate_eval.py`（候选模型评测淘汰）、`deploy/run_cloud.sh`；`src/core.py` 持仓动态化；`scripts/gen_static.py` 误差走势卡
- 原因：让持仓/误差自动积累，模型可自进化；部署可自愈
- 影响：前十大持仓随季报自动更新；每日误差入库；异常自动告警
- 验证：各脚本单测通过；update_holdings 识别 2026-06-30 报告期；candidate_eval 完成 PCB 动量评测（dropped）

## [2026-08-23] 腾讯云 Lighthouse 部署（lhins-6tc2yllt / ap-guangzhou）
- 改动：部署到 `/opt/006010-valuation`，Nginx 站点 valuation（80端口），cron 定时任务（09:00/15:10 日跑、19:30 守望），venv 隔离环境
- 原因：替代 CloudStudio，实现稳定长期运行 + 公网访问 + 定时推送
- 影响：页面 `http://106.55.94.208/` 上线；晚间净值守望自动推送
- 验证：页面 200；估值跑通（08-24 预测 0.5582 置信度 86/100）

## [2026-08-23] 修复 core.py 语法错误（rmse 括号）
- 改动：`src/core.py` L624，`"rmse": round(float(np.sqrt(np.mean(e ** 2)))), 4)` 括号位置修正
- 原因：括号错位导致 SyntaxError
- 影响：core.py 恢复可导入
- 验证：py_compile 通过

## [2026-08-21] v4 模型优化：MAE 0.545% → 0.476%（-12.6%）
- 改动：`src/core.py` MAE_GATE 1.00→1.30、新增 MIN_GROUP_WEIGHT=0.05（组保底）；`src/fund_valuation_v2.py` basket_returns → basket_returns_robust(trim_mad=3.0)
- 原因：放宽淘汰阈值保留多模型，MAD 截尾抵御极端股
- 影响：集成 MAE 降 12.6%，权重分布健康（P1-P4 均衡），急跌日 MAE 改善 52%
- 验证：回测对比（改动前 0.545% → 改动后 0.476%）

## [2026-08-20] v4 可信度强化（9 项）
- 改动：核心算法迁入 core.py 纯函数层；数据源上线体检；集成权重分组去重（P3/P4 同源合并）；EWMA+med20 偏差修正；置信度 0-100 连续分；估算语义显式化；日期错配自检；覆盖率核心指标；行情分篮子降级
- 原因：提升可信度与验证严谨性
- 影响：v4 版本基线
- 验证：tests/backtest_v3.py 回测通过

---

*首次建立：2026-08-24*
