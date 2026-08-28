#!/bin/bash
# 006010 晚间净值守望：19:40 启动，每2分钟检测官方净值，公布即全流程+推送
cd /opt/006010-valuation
LOG=/tmp/006010_watch.log
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 守望启动 ===" >> $LOG
echo "[0/8] git pull 自更新" >> $LOG
git pull --ff-only origin main >> $LOG 2>&1 || echo "pull 跳过（网络或本地改动）" >> $LOG
for i in $(seq 1 125); do
  OUT=$(./venv/bin/python scripts/nav_watch.py --check 2>&1)
  echo "$(date '+%H:%M:%S') [$i] $OUT" >> $LOG
  if echo "$OUT" | grep -q 'updated=1'; then
    echo ">>> 净值公布! 触发全流程 <<<" >> $LOG
    ./venv/bin/python scripts/update_holdings.py >> $LOG 2>&1
    ./venv/bin/python scripts/fetch_cloud.py >> $LOG 2>&1
    ./venv/bin/python scripts/fetch_sina.py >> $LOG 2>&1
    ./venv/bin/python src/fund_holdings_infer.py >> $LOG 2>&1
    ./venv/bin/python src/fund_valuation_v2.py >> $LOG 2>&1
    ./venv/bin/python scripts/snapshot_estimate.py >> $LOG 2>&1
    ./venv/bin/python scripts/gen_static.py >> $LOG 2>&1
    cp -r docs/* /var/www/html/ >> $LOG 2>&1
    ./venv/bin/python scripts/daily_errors.py >> $LOG 2>&1
    ./venv/bin/python scripts/candidate_eval.py >> $LOG 2>&1
    SCKEY=$(cat /opt/006010-valuation/cache/sckey.txt 2>/dev/null)
    if [ -n "$SCKEY" ]; then
      ./venv/bin/python scripts/notify.py "$SCKEY" >> $LOG 2>&1
    else
      echo "[推送] 未配置 SCKEY，跳过" >> $LOG
    fi
    echo "=== 完成 $(date '+%H:%M:%S') ===" >> $LOG
    exit 0
  fi
  sleep 120
  [ $(date +%H%M) -gt 2350 ] && break
done
echo "=== 超时未公布，退出 ===" >> $LOG
