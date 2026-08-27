#!/bin/bash
# ============================================
# 006010 盘中估值 · 云端完整模型自动更新脚本
# 由 cron 每日收盘后触发：抓数→反推→多模型→生成页面→部署→推送
# ============================================
set -e
APP=/opt/006010-valuation
cd "$APP"
export PATH="/usr/local/bin:$PATH"
LOG="$APP/run.log"
SENDKEY="SCT401609TYQYPkAVCITdAEPhTSiTqD1nt"

echo "===== $(date '+%F %T') 开始 =====" >> $LOG

echo "[1/6] 更新代码" >> $LOG
# 服务器每次运行会改写 cache/*.json 与 docs/*（已跟踪产物），导致 git pull 冲突、代码永远停在旧版。
# 改为：先 stash 本地数据改动 → 强拉 main → 丢弃 stash（产物本轮会重新生成，无需保留）
git stash push -m "cron-auto-$(date +%Y%m%d-%H%M)" >/dev/null 2>&1 || true
git pull --ff-only origin main >>$LOG 2>&1 || { echo "[错误] git pull 失败，中止本轮（stash 已保留可查）" >>$LOG; exit 1; }
git stash drop >/dev/null 2>&1 || true

echo "[2/6] 抓取数据 fetch_cloud" >> $LOG
python3 scripts/fetch_cloud.py >> $LOG 2>&1

echo "[3/6] 个股反推 fund_holdings_infer" >> $LOG
python3 src/fund_holdings_infer.py >> $LOG 2>&1

echo "[4/6] 多模型估值 fund_valuation_v2" >> $LOG
python3 src/fund_valuation_v2.py >> $LOG 2>&1

echo "[5/6] 生成页面 gen_static" >> $LOG
python3 scripts/gen_static.py >> $LOG 2>&1

echo "[6/6] 部署到 nginx + Server酱推送" >> $LOG
cp -r docs/* /var/www/html/ >> $LOG 2>&1
python3 scripts/notify.py "$SENDKEY" >> $LOG 2>&1

echo "===== $(date '+%F %T') 完成 =====" >> $LOG
