#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fund_web.py —— 006010 盘中估值 · 手机端 Web 面板（局域网，零依赖）

原理：本机启动轻量 HTTP 服务（标准库 http.server），读取 cache/result.json
渲染成移动端友好的暗色卡片页；手机与电脑连同一 WiFi，浏览器访问
http://<电脑局域网IP>:8080 即可查看；页面可触发「刷新」重跑双模型。

用法：
  python fund_web.py [port]      # 默认 8080，启动后打印局域网地址+二维码链接

端点：
  /            估值卡片页（移动端优化，自动60秒刷新）
  /api/data    返回 result.json + infer.json 的 JSON
  /api/refresh 重跑两个模型脚本（约30-60秒），返回最新数据
"""
import json, os, sys, subprocess, socket, time, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "cache")
VENV_PY = sys.executable
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080

UP = "#f0533d"; DOWN = "#2e9e5b"; FG = "#d4d4d4"; BG = "#1e1e1e"; PANEL = "#252526"
ACC = "#4ec9b0"

# 刷新互斥锁（防并发重复重跑）
_refresh_lock = threading.Lock()
_last_refresh = {"t": 0.0}

def load_json(name):
    p = os.path.join(CACHE, name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None

def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def run_models():
    with _refresh_lock:
        now = time.time()
        if now - _last_refresh["t"] < 5:   # 5秒内不重复触发
            return {"ok": False, "msg": "请求过于频繁，请稍候"}
        _last_refresh["t"] = now
    out = {}
    for script in ("src/fund_valuation_v2.py", "src/fund_holdings_infer.py"):
        try:
            r = subprocess.run([VENV_PY, os.path.join(HERE, script)],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=240, cwd=HERE)
            out[script] = "OK" if r.returncode == 0 else (r.stderr or r.stdout or "")[-200:]
        except Exception as e:
            out[script] = f"异常: {e}"
    return out

# ---------- 页面模板 ----------
def render_html(d, i):
    if not d:
        return """<html><head><meta charset="utf-8"><title>006010 估值</title></head>
        <body style="background:#1e1e1e;color:#d4d4d4;font-family:sans-serif;text-align:center;padding-top:40%">
        <h2>暂无估值数据</h2><p>请先在本机运行模型，或点下方刷新</p></body></html>"""
    center = d.get("P_final_corr", d.get("L3_center_pct", 0))
    band = d.get("band_pct", [0, 0])
    color = UP if center > 0 else (DOWN if center < 0 else FG)
    sign = "+" if center > 0 else ""
    models = d.get("models", {})
    m_line = "  ".join(f"{k.split('_')[1]} {v:+.2f}%" for k, v in models.items())
    t = time.strftime("%H:%M:%S")
    html = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>006010 盘中估值</title>
<style>
body{{margin:0;background:{BG};color:{FG};font-family:-apple-system,"Microsoft YaHei",sans-serif;}}
.wrap{{max-width:480px;margin:0 auto;padding:20px 16px 40px;}}
.top{{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;}}
.fund{{font-size:17px;font-weight:700;color:{ACC};}}
.time{{font-size:12px;color:#8a8a8a;}}
.sub{{font-size:13px;color:#8a8a8a;margin-bottom:14px;}}
.card{{background:{PANEL};border-radius:14px;padding:22px 18px;text-align:center;margin-bottom:12px;}}
.chg{{font-size:64px;font-weight:800;color:{color};line-height:1.1;}}
.nav{{font-size:22px;font-weight:600;margin-top:6px;}}
.meta{{font-size:14px;color:#a8a8a8;line-height:1.9;margin-top:12px;text-align:left;}}
.meta b{{color:{FG};}}
.row{{display:flex;gap:8px;margin-bottom:12px;}}
.box{{flex:1;background:{PANEL};border-radius:12px;padding:12px;text-align:center;}}
.box .v{{font-size:18px;font-weight:700;}}
.box .l{{font-size:11px;color:#8a8a8a;margin-top:2px;}}
.sig{{display:inline-block;padding:2px 10px;border-radius:20px;font-size:12px;font-weight:600;
  background:#3a3a3a;color:#dcdcaa;}}
.btn{{display:block;width:100%;padding:14px;border:none;border-radius:12px;font-size:16px;font-weight:700;
  background:{ACC};color:#111;cursor:pointer;margin-bottom:10px;}}
.btn:disabled{{opacity:.5}}
.hint{{font-size:11px;color:#5a5a5a;text-align:center;margin-top:8px;line-height:1.6;}}
</style></head><body><div class="wrap">
<div class="top"><div class="fund">006010 国融融银混合C</div><div class="time">{t} 更新</div></div>
<div class="sub">盘中估值 · 多模型组合（v3）</div>

<div class="card">
  <div class="chg">{sign}{center}%</div>
  <div class="nav">预计净值 <b>{d.get('nav_center','--')}</b></div>
  <div class="meta">
    合理区间：{band[0]}% ~ {band[1]}%<br>
    置信度：<b>{d.get('confidence','-')}</b>（MAE {d.get('mae',{}).get('P3','-')}%）<br>
    基准净值：{d.get('nav_prev','-')}（{d.get('cur_date','-')}）<br>
    偏差修正：{d.get('bias_correction',0):+.2f}%
  </div>
</div>

<div class="row">
  <div class="box"><div class="v">{d.get('model_spread_pct','-')}</div><div class="l">模型分歧(pct)</div></div>
  <div class="box"><div class="v">{d.get('theta_pcb',0)*100:.1f}%</div><div class="l">PCB替代比例</div></div>
  <div class="box"><div class="v">{d.get('pcb_signal','-')}</div><div class="l">PCB信号</div></div>
</div>

<div class="card" style="font-size:13px;color:#a8a8a8;line-height:1.8">
  各模型：<b style="color:{FG}">{m_line}</b>
</div>

<button class="btn" id="btn" onclick="doRefresh()">🔄 刷新估值（约30-60秒）</button>
<div class="hint">数据来自模型估算，非官方净值；最终以收盘官方净值为准。<br>
页面每60秒自动更新；点刷新可在手机上重跑模型。</div>
</div>
<script>
function doRefresh(){{
  var b=document.getElementById('btn'); b.disabled=true; b.textContent='计算中…请稍候';
  fetch('/api/refresh').then(function(r){{return r.json();}}).then(function(j){{
    b.disabled=false; b.textContent='🔄 刷新估值（约30-60秒）';
    if(j && j.ok===false){{alert('刷新失败：'+JSON.stringify(j));}}
    location.reload();
  }}).catch(function(e){{b.disabled=false;b.textContent='🔄 刷新估值（约30-60秒）';alert('刷新失败：'+e);}});
}}
setInterval(function(){{location.reload();}}, 60000);
</script></body></html>"""
    return html

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass  # 静默访问日志

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/" or path == "/index.html":
                d = load_json("result.json"); i = load_json("infer.json")
                body = render_html(d, i).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/data":
                d = load_json("result.json"); i = load_json("infer.json")
                body = json.dumps({"result": d, "infer": i}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/refresh":
                out = run_models()
                d = load_json("result.json")
                body = json.dumps({"ok": all(v == "OK" for v in out.values()),
                                   "detail": out, "result": d}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404); self.end_headers()
        except Exception as e:
            body = json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

if __name__ == "__main__":
    ip = local_ip()
    print("=" * 52)
    print("  006010 盘中估值 · 手机端面板已启动")
    print("=" * 52)
    print(f"  手机与本机连同一WiFi后，浏览器打开：")
    print(f"    http://{ip}:{PORT}")
    print(f"  本机自测： http://127.0.0.1:{PORT}")
    print(f"  二维码：  https://api.pwmqr.com/qrcode/create/?url=http://{ip}:{PORT}")
    print("  按 Ctrl+C 停止服务")
    print("=" * 52)
    srv = HTTPServer(("0.0.0.0", PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
