# CHANGELOG — 006010 估值系统变更日志

> 格式：`## [日期] 一句话标题` → 改动 / 原因 / 影响 / 验证
> **规则：每次代码变更必须在此追加一条记录（见 CONTRIBUTING.md §三）**

---

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