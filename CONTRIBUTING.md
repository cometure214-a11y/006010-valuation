# CONTRIBUTING.md — 多端协作开发流程

> 适用场景：**办公电脑 + 家里电脑 + AI 助手** 共同维护本项目，腾讯云负责部署。
> 本文件是给**人**看的操作手册；给 AI 看的规则见 [AGENTS.md](./AGENTS.md)。

---

## 一、拓扑总览

```
办公电脑 ──┐
          ├──→ GitHub 仓库 ──→ 腾讯云服务器（部署+定时任务）
家里电脑 ──┘     （唯一真源）
```

**核心原则：一切代码变更走 GitHub，服务器只从 GitHub 拉取。**

## 二、日常开发流程

### 场景 A：改代码（两台电脑都适用）

```bash
# 1. 拉最新（重要！防止基于旧代码修改）
cd 006010-valuation
git pull origin main

# 2. 修改代码（改 src/ 或 scripts/）

# 3. 本地验证
python -m py_compile src/你的文件.py          # 语法检查
python scripts/fetch_all.py                    # 抓数据（如有数据依赖）
python src/fund_valuation_v2.py                # 跑估值（验证主流程）

# 4. 更新 CHANGELOG.md（必须！见下文"变更记录"）

# 5. 提交推送
git add -A
git commit -m "feat: 新增xxx功能"               # 中文描述
git push origin main
```

### 场景 B：push 被拒（远端有新提交）

```bash
git pull --rebase origin main   # 把本地改动变基到最新
# 解决冲突（如果有）
git push origin main
```

### 场景 C：部署到腾讯云

```bash
# 方式1：服务器上手动拉取（推荐，最直接）
ssh root@106.55.94.208   # 或腾讯云 Web 终端
cd /opt/006010-valuation
git pull origin main
# 如依赖变化：./venv/bin/pip install -r requirements.txt

# 方式2：依赖 GitHub Actions（如已配置 workflow_dispatch）
# 仓库 → Actions → 手动 Run
```

> ⚠️ **不要在服务器上直接改代码**——服务器的 git 工作区会被 `git pull` 覆盖。

## 三、变更记录规范（必须执行）

每次提交代码，**同时**更新 `CHANGELOG.md`：

```markdown
## [2026-08-24] 修复 fund_valuation_v2.py 语法错误
- 改动：src/fund_valuation_v2.py L190（errs_p1 行拆开）
- 原因：两行被错误合并成一行导致脚本无法运行
- 影响：主估值脚本恢复可运行
- 验证：python3 -m py_compile 通过；完整流程跑通
```

**大变更**（改核心算法）额外要求：
1. 更新 `006010_盘中估值方法文档_v4.md` 对应章节
2. 在 `docs/复盘_YYYYMMDD.md` 写复盘（改了啥/回测对比/结论）
3. 跑 `tests/backtest_v3.py` 验证回测

## 四、双电脑冲突规避

| 规则 | 原因 |
|------|------|
| 改前必 `git pull` | 避免基于旧代码修改 |
| 小步提交、及时推送 | 减少冲突面 |
| 不 push -f | 防止覆盖对方提交 |
| 修改算法前先看 AGENTS.md §3 | 了解数据纪律红线 |
| 服务器上不直接改代码 | git pull 会覆盖工作区 |

## 五、常用命令速查

```bash
# 本地跑完整流程（开发验证）
python scripts/fetch_all.py
python src/fund_holdings_infer.py
python src/fund_valuation_v2.py
python scripts/gen_static.py

# 语法检查所有文件
python -m py_compile src/*.py scripts/*.py

# 回测
python tests/backtest_v3.py

# 服务器同步
cd /opt/006010-valuation && git pull origin main

# 服务器看日志
tail -f /tmp/006010_daily.log      # 日跑日志
tail -f /tmp/006010_watch.log      # 晚间守望日志
```

---

*最后更新：2026-08-24*