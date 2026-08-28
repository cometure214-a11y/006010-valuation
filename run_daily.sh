#!/bin/bash
# 006010 每日完整流程：自更新→持仓→抓数→反推→估值→快照→页面→同步→误差→看门狗
cd /opt/006010-valuation
LOG=/tmp/006010_daily.log
echo "===== $(date '+%Y-%m-%d %H:%M:%S') ===" >> $LOG
echo "[0/8] git pull 自更新" >> $LOG
git pull --ff-only origin main >> $LOG 2>&1 || echo "pull 跳过（网络或本地改动）" >> $LOG
./venv/bin/python scripts/update_holdings.py >> $LOG 2>&1
./venv/bin/python scripts/fetch_cloud.py >> $LOG 2>&1
./venv/bin/python scripts/fetch_sina.py >> $LOG 2>&1
./venv/bin/python src/fund_holdings_infer.py >> $LOG 2>&1
./venv/bin/python src/fund_valuation_v2.py >> $LOG 2>&1
./venv/bin/python scripts/snapshot_estimate.py >> $LOG 2>&1
./venv/bin/python scripts/gen_static.py >> $LOG 2>&1
cp -r docs/* /var/www/html/ >> $LOG 2>&1
./venv/bin/python scripts/daily_errors.py >> $LOG 2>&1
./venv/bin/python scripts/health_check.py >> $LOG 2>&1
echo "--- done ---" >> $LOG
