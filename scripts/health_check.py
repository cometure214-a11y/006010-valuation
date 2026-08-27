#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
health_check.py —— 部署看门狗（自愈告警）
用法: python scripts/health_check.py [SENDKEY]

检查项:
  1. docs/index.html 是否过旧（交易日 10:00-23:00 超过 6 小时未更新 → 告警）
  2. nginx(80) 是否正常响应
  3. 是否有卡死的日跑/守望进程（可选告警）
异常时 Server酱推送「⚠️ 006010 系统异常」。正常时静默退出。
仅标准库。
"""
import json, os, sys, time, datetime, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOCS = os.path.join(ROOT, "docs")
SCKEY = sys.argv[1] if len(sys.argv) > 1 else ""
SITE = "http://127.0.0.1/"


def push(title, desp):
    if not SCKEY:
        print(f"[health] 告警: {title} | {desp}")
        return
    data = urllib.parse.urlencode({"title": title, "desp": desp}).encode("utf-8")
    try:
        req = urllib.request.Request(f"https://sctapi.ftqq.com/{SCKEY}.send", data=data,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        urllib.request.urlopen(req, timeout=15).read()
        print(f"[health] 已推送告警: {title}")
    except Exception as e:
        print(f"[health] 告警推送失败: {e}")


def main():
    problems = []
    now = datetime.datetime.now()
    is_weekday = now.weekday() < 5

    # ① 页面新鲜度
    idx = os.path.join(DOCS, "index.html")
    if not os.path.exists(idx):
        problems.append("docs/index.html 不存在！")
    else:
        mtime = os.path.getmtime(idx)
        age_h = (time.time() - mtime) / 3600
        # 交易日 10:00-23:00 且超过 6 小时未更新 → 异常（周末/节假日/非时段不打扰）
        if is_weekday and 10 <= now.hour <= 23 and age_h > 6:
            problems.append(f"页面已 {age_h:.1f} 小时未更新（{time.strftime('%m-%d %H:%M', time.localtime(mtime))}）")

    # ② nginx 响应
    try:
        r = urllib.request.urlopen(SITE, timeout=8)
        if r.status != 200:
            problems.append(f"nginx 返回 HTTP {r.status}")
    except Exception as e:
        problems.append(f"nginx 无法访问: {e}")

    # ③ 卡死进程（守望应每 2 分钟动一次，若 run_daily 超过 2 小时仍在跑 → 可疑）
    try:
        import subprocess
        out = subprocess.run(["ps", "-eo", "etime,cmd"], capture_output=True, text=True).stdout
        for line in out.splitlines():
            if "run_daily.sh" in line or "watch_evening.sh" in line or "fund_valuation_v2" in line:
                et = line.split()[0]
                parts = et.split("-")
                if len(parts) == 2 and int(parts[0]) >= 3:   # 运行超过 3 天
                    problems.append(f"疑似卡死进程（运行 {et}）: {line.strip()[:80]}")
    except Exception:
        pass

    if problems:
        push("⚠️ 006010 系统异常", "\n\n".join(problems))
        print("[health] 异常项:", "; ".join(problems))
        return 1
    print(f"[health] OK ({time.strftime('%F %T')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
