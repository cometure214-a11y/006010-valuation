#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_static.py —— 云端版：渲染手机端估值页（信息分级版 v4）
v4 美化点：
  - Hero 底部内嵌近 10 日 NAV 迷你走势 sparkline（含今日估算）
  - 合理区间改为可视进度条（含当前估值标记）
  - 实时口径卡重排：两数并排 + 归一化 tooltip + 行业热力条
  - 主数字微光晕效果，模型版本徽章
  - 底部精简为一行
  - 置信度加入 MAE 数值徽章
"""
import json, os, time, math

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "cache")
DOCS = os.path.join(ROOT, "docs")
os.makedirs(DOCS, exist_ok=True)

def load_json(name):
    p = os.path.join(CACHE, name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None

TOP10 = [
    ("sh688498", 9.63), ("sh688048", 9.36), ("sz300502", 9.36), ("sh688313", 8.42),
    ("sz300620", 8.35), ("sz300548", 8.24), ("sz300570", 8.18), ("sh688025", 7.97),
    ("sz300394", 7.73), ("sz300308", 5.92),
]

CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0b0f15;color:#e6e9ef;
     font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
     -webkit-font-smoothing:antialiased;padding-bottom:calc(env(safe-area-inset-bottom) + 8px)}
.wrap{max-width:520px;margin:0 auto;padding:14px 16px 28px}
.up{color:#f04e3c}.down{color:#30a46c}.flat{color:#e6e9ef}
.up-bg{background:rgba(240,78,60,.12)}.down-bg{background:rgba(48,164,108,.12)}

.hd{display:flex;justify-content:space-between;align-items:flex-start;padding:4px 2px 14px}
.fund{font-size:15.5px;font-weight:700;line-height:1.4}
.fund small{display:block;font-size:11px;color:#8892a3;font-weight:500;margin-top:2px;letter-spacing:.5px}
.time{font-size:11px;color:#6b7280;text-align:right;line-height:1.6}
.time .live{display:inline-block;width:6px;height:6px;border-radius:50%;background:#30a46c;
            animation:pulse 1.6s infinite;margin-right:5px;vertical-align:middle}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}

/* ─── Hero 常驻层 ─── */
.hero{position:relative;background:linear-gradient(165deg,#111820,#0d1319 70%);
      border:1px solid rgba(255,255,255,.07);border-radius:20px;padding:24px 22px 18px;text-align:center;
      margin-bottom:10px;overflow:hidden}
.hero::before{content:'';position:absolute;top:-40px;right:-40px;width:160px;height:160px;
              background:radial-gradient(circle,rgba(240,78,60,.10) 0%,transparent 70%);pointer-events:none}
.hero::after{content:'';position:absolute;bottom:-30px;left:-30px;width:140px;height:140px;
             background:radial-gradient(circle,rgba(78,201,176,.07) 0%,transparent 70%);pointer-events:none}
.hero .top-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.hero .lbl{font-size:11.5px;color:#8892a3;letter-spacing:1.5px}
.badge{font-size:10px;padding:3px 10px;border-radius:20px;background:rgba(78,201,176,.12);
       color:#4ec9b0;font-weight:700;letter-spacing:.5px}
.hero .main{font-size:76px;font-weight:800;line-height:1;letter-spacing:-3px;
            font-variant-numeric:tabular-nums;
            text-shadow:0 0 30px rgba(240,78,60,.25)}
.hero .main.down{text-shadow:0 0 30px rgba(48,164,108,.25)}
.hero .sub{font-size:16px;font-weight:600;margin-top:8px;color:#c6ccd6}
.hero .sub b{font-size:22px;font-weight:800;color:#e6e9ef}
.hero .ref{font-size:11.5px;color:#6b7280;margin-top:8px}
.hero .ref b{color:#b8c0cc}

/* sparkline */
.spark{margin-top:14px;padding-top:12px;border-top:1px solid rgba(255,255,255,.05)}
.spark svg{display:block;margin:0 auto;width:100%;height:44px}
.spark .lbl-row{display:flex;justify-content:space-between;font-size:10px;color:#5c6470;margin-top:2px}

/* ─── 关注层 ─── */
.metrics{display:grid;grid-template-columns:1.4fr 1fr 1fr;gap:8px;margin-bottom:10px}
.m{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);border-radius:12px;
   padding:12px 8px;text-align:center;position:relative}
.m .v{font-size:14.5px;font-weight:700;font-variant-numeric:tabular-nums;margin-bottom:4px}
.m .l{font-size:10px;color:#8892a3}
.m .sub-v{font-size:10px;color:#5c6470;margin-top:2px}
.m-range .bar{position:relative;height:8px;border-radius:4px;
              background:linear-gradient(90deg,#1a2634,#2a3a4a);margin:8px 6px 4px;overflow:hidden}
.m-range .bar .fill{position:absolute;left:0;top:0;height:100%;border-radius:4px;
                    background:linear-gradient(90deg,rgba(48,164,108,.4),rgba(240,78,60,.4))}
.m-range .bar .marker{position:absolute;top:50%;width:14px;height:14px;border-radius:50%;
                      background:#e6e9ef;transform:translate(-50%,-50%);
                      box-shadow:0 0 0 3px rgba(230,233,239,.2)}
.pill{display:inline-block;padding:2px 10px;border-radius:20px;font-size:11px;font-weight:700}
.pill.strong{background:rgba(240,78,60,.15);color:#f04e3c}
.pill.mid{background:rgba(220,202,106,.14);color:#dcdcaa}
.pill.weak{background:rgba(139,148,163,.14);color:#a3adbd}
.pill.high{background:rgba(48,164,108,.14);color:#30a46c}

/* ─── 详情层 ─── */
details.card{background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.06);
             border-radius:14px;margin-bottom:8px;overflow:hidden}
summary{padding:12px 16px;font-size:13px;font-weight:600;color:#c6ccd6;cursor:pointer;
        display:flex;justify-content:space-between;align-items:center;list-style:none}
summary::-webkit-details-marker{display:none}
summary .arr{color:#6b7280;font-size:11px;transition:transform .15s}
details[open] summary .arr{transform:rotate(90deg)}
.detail{padding:0 16px 12px}
.detail .row{display:flex;justify-content:space-between;padding:6px 0;font-size:12.5px;
             border-top:1px solid rgba(255,255,255,.04);align-items:center}
.detail .row .k{color:#8892a3}.detail .row .v2{font-weight:600;font-variant-numeric:tabular-nums}
.detail .row .v2 small{font-size:10px;color:#5c6470;font-weight:500;margin-left:4px}

/* 实时口径双数 + 归一化 tooltip */
.rt-hero{display:flex;gap:10px;margin-bottom:10px}
.rt-box{flex:1;background:rgba(255,255,255,.03);border-radius:10px;padding:10px 8px;text-align:center;
        border:1px solid rgba(255,255,255,.05);position:relative}
.rt-box .rt-lbl{font-size:10px;color:#8892a3;margin-bottom:4px}
.rt-box .rt-val{font-size:19px;font-weight:800;font-variant-numeric:tabular-nums;line-height:1.2}
.rt-box.norm::after{content:'?';position:absolute;top:6px;right:8px;width:16px;height:16px;
                    border-radius:50%;background:rgba(255,255,255,.08);color:#8892a3;
                    font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center}
.rt-tip{font-size:10.5px;color:#6b7280;line-height:1.6;padding:8px 10px;background:rgba(255,255,255,.025);
        border-radius:8px;margin-bottom:10px}

/* 行业热力条 */
.sector-row{display:flex;align-items:center;gap:8px;padding:5px 0}
.sector-row .s-name{width:52px;font-size:11px;color:#8892a3;text-align:right}
.sector-row .s-bar{flex:1;height:5px;border-radius:3px;background:#1a2030;overflow:hidden}
.sector-row .s-fill{height:100%;border-radius:3px}
.sector-row .s-val{width:52px;font-size:11px;font-weight:700;font-variant-numeric:tabular-nums}

/* 个股列表 */
.list{display:flex;flex-direction:column;gap:5px;padding-top:8px}
.item{display:flex;justify-content:space-between;align-items:center;
      background:rgba(255,255,255,.025);border-radius:8px;padding:7px 11px;font-size:12px}
.item .code{color:#8892a3;font-size:10.5px}.item .w{color:#5c6470;font-size:10.5px;margin-left:6px}
.item .ret{font-weight:700;font-variant-numeric:tabular-nums;font-size:12px}

/* 底部 */
.foot{display:flex;flex-direction:column;gap:6px;margin-top:10px}
.btn{display:block;width:100%;padding:12px;border:none;border-radius:12px;font-size:13px;font-weight:700;cursor:pointer;
     background:#1c2530;color:#c6ccd6;border:1px solid rgba(255,255,255,.07)}
.btn:active{background:#26313f}
.hint{font-size:10px;color:#5c6470;text-align:center;line-height:1.7}

.loading{color:#8892a3;font-size:12px;text-align:center;padding:14px 0}
#rt_box{display:none}
.tip{font-size:10.5px;color:#6b7280;line-height:1.6;padding-top:6px}
"""

JS = """
const TOP10 = [
__TOP10_JS__
];
function loadQuotes(){
  const s = TOP10.map(t=>t.s).join(',');
  const sc = document.createElement('script');
  sc.src = 'https://qt.gtimg.cn/q=' + s;
  sc.onload = calcRealtime;
  sc.onerror = function(){
    document.getElementById('rt_loading').innerHTML =
      '<div class="loading">实时行情获取失败（非交易时段或网络限制）</div>';
  };
  document.body.appendChild(sc);
}
function fmt(v,d){return (v>0?'+':'') + v.toFixed(d) + '%';}
function cl(v){return v>0.005?'up':(v<-0.005?'down':'flat');}

function calcRealtime(){
  let num=0, wsum=0, n=0, rows='', sectors='';
  TOP10.forEach(t=>{
    const v = window['v_'+t.s];
    if(!v) return;
    const p = v.split('~');
    const cur = parseFloat(p[3]), prev = parseFloat(p[4]);
    if(!prev) return;
    const r = (cur-prev)/prev*100;
    num += (t.w/100)*r;
    wsum += t.w/100;
    n++;
    rows += '<div class="item"><span><span class="code">'+p[2]+'</span><span class="w">'+t.w.toFixed(2)+'%</span></span>' +
            '<span class="ret '+cl(r)+'">'+(r>=0?'▲':'▼')+' '+fmt(r,2)+'</span></div>';
  });
  if(n<5){document.getElementById('rt_loading').innerHTML='<div class="loading">行情解析异常，请稍后刷新</div>';return;}
  const naive = num, norm = num/wsum;
  document.getElementById('rt_box').style.display='block';
  document.getElementById('rt_loading').style.display='none';
  const chg = document.getElementById('rt_chg');
  chg.textContent = fmt(naive,2);
  chg.className = 'rt-val '+cl(naive);
  document.getElementById('rt_norm_val').textContent = fmt(norm,2);
  document.getElementById('rt_norm_val').className = 'rt-val '+cl(norm);
  document.getElementById('now').textContent = new Date().toLocaleString('zh-CN',{hour12:false});
  document.getElementById('rt_list').innerHTML = rows;
}
loadQuotes();
setInterval(loadQuotes, 60000);
"""

def build_sparkline(navs_dates, est_nav=None):
    """Build inline SVG sparkline from NAV history + optional estimated point."""
    if len(navs_dates) < 3:
        return ''
    # Take last 10 real points + 1 estimated
    pts = navs_dates[-10:]
    if est_nav is not None:
        pts.append(est_nav)
    vals = [p[1] for p in pts]
    vmin, vmax = min(vals), max(vals)
    vrange = vmax - vmin or 1
    W, H = 440, 44
    pad = 6
    innerW = W - pad*2
    innerH = H - pad*2
    n = len(vals)
    step = innerW / (n-1) if n > 1 else 0
    # Normalize: higher NAV -> lower Y
    coords = []
    for i, v in enumerate(vals):
        x = pad + i * step
        y = pad + (1 - (v - vmin)/vrange) * innerH
        coords.append((x, y))
    # Path
    d = 'M' + ' L'.join(f'{x:.1f},{y:.1f}' for x,y in coords)
    # Area
    area_d = d + f' L{coords[-1][0]:.1f},{H-pad} L{coords[0][0]:.1f},{H-pad} Z'
    # Color based on trend
    up = vals[-1] >= vals[0]
    color = '#30a46c' if up else '#f04e3c'
    glow = '#48c9b0' if up else '#f04e3c'
    # Estimate dot (last point)
    lx, ly = coords[-1]
    last_date = pts[-1][0].split('-')[1]+'-'+pts[-1][0].split('-')[2] if '-' in pts[-1][0] else ''
    first_date = pts[0][0].split('-')[1]+'-'+pts[0][0].split('-')[2] if '-' in pts[0][0] else ''
    date_labels = f'<span>{first_date}</span><span>{last_date}</span>'
    svg = f'''
<div class="spark">
<svg viewBox="0 0 {W} {H}" preserveAspectRatio="none">
  <defs>
    <linearGradient id="spArea" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{color}" stop-opacity="0.25"/>
      <stop offset="100%" stop-color="{color}" stop-opacity="0"/>
    </linearGradient>
  </defs>
  <path d="{area_d}" fill="url(#spArea)"/>
  <path d="{d}" fill="none" stroke="{color}" stroke-width="1.5" stroke-linejoin="round"/>
  <circle cx="{lx:.1f}" cy="{ly:.1f}" r="3" fill="{glow}" stroke="#0b0f15" stroke-width="1.5"/>
</svg>
<div class="lbl-row">{date_labels}</div>
</div>'''
    return svg


def build_range_bar(lo, hi, cur):
    """Build horizontal range bar with marker."""
    lo_f, hi_f, cur_f = float(lo), float(hi), float(cur)
    span = hi_f - lo_f or 0.01
    pos = max(0, min(1, (cur_f - lo_f)/span)) * 100
    fill_pct = max(pos, 8)
    svg = f'''
<div class="bar">
  <div class="fill" style="width:{fill_pct:.1f}%"></div>
  <div class="marker" style="left:{pos:.1f}%"></div>
</div>
<div style="display:flex;justify-content:space-between;font-size:10px;color:#5c6470;margin-top:2px;padding:0 4px">
  <span>{lo_f:+.2f}%</span><span>{hi_f:+.2f}%</span>
</div>'''
    return svg


def build_sector_bars(intraday):
    """Build sector heat bars."""
    if not intraday:
        return ''
    rows = ''
    items = [
        ('光通信', intraday.get('optical', 0)),
        ('PCB', intraday.get('pcb', 0)),
        ('半导体', intraday.get('semis', 0)),
        ('市场', intraday.get('market', 0)),
    ]
    max_abs = max(abs(v) for _,v in items) or 1
    for name, val in items:
        w = abs(val)/max_abs * 100
        color = '#f04e3c' if val > 0 else '#30a46c'
        rows += f'''
<div class="sector-row">
  <span class="s-name">{name}</span>
  <span class="s-bar"><span class="s-fill" style="width:{w:.1f}%;background:{color}"></span></span>
  <span class="s-val" style="color:{color}">{val:+.2f}%</span>
</div>'''
    return rows


def build_html(d):
    top10_js = ",\n".join(f'  {{s:"{s}", w:{w}}}' for s, w in TOP10)
    js = JS.replace("__TOP10_JS__", top10_js)

    def cl(v):
        return "up" if v > 0.0001 else ("down" if v < -0.0001 else "flat")

    # Load sparkline data
    spark_html = ''
    spark_center = None
    nav_data = load_json("nav.json")
    est_nav = None
    if nav_data:
        dates = nav_data.get('dates', [])
        navs = nav_data.get('navs', [])
        pts = list(zip(dates, navs))
        if d:
            est_nav = d.get('nav_center', None)
            if est_nav:
                spark_center = ('今日估', est_nav)
        spark_html = build_sparkline(pts, spark_center)

    hero = '<div class="hero"><div class="top-row"><span class="lbl">今日估算涨跌幅</span></div>' \
           '<div class="main flat">--</div><div class="sub">预计净值 <b>--</b></div>' \
           '<div class="ref">暂无完整模型数据</div></div>'
    metrics = ""
    details = ""
    spark_in_hero = ''

    if d:
        center = d.get("P_final_corr", d.get("L3_center_pct", 0))
        band = d.get("band_pct", [0, 0])
        nav_center = d.get("nav_center", "--")
        nav_prev = d.get("nav_prev", "--")
        cur = d.get("cur_date", "")
        conf = d.get("confidence", "-")
        models = d.get("models", {})
        mae = d.get("mae", {})
        pcb_sig = d.get("pcb_signal", "无信号")
        pill = {"强信号":"strong","中等信号":"mid","弱信号":"weak"}.get(pcb_sig,"weak")
        m_line = "  ".join(f"{k.split('_')[1]} {v:+.2f}%" for k,v in models.items())
        conf_pill = {"高":"high","中等":"mid","低":"weak"}.get(conf,"weak")

        mae_p1 = mae.get("P1", 0)
        mae_p2 = mae.get("P2", 0)
        mae_p3 = mae.get("P3", 0)
        mae_txt = f"P1 {mae_p1:.2f} | P2 {mae_p2:.2f} | P3 {mae_p3:.2f}"

        spark_in_hero = spark_html
        # 估算目标日：cur=最新已公布净值日；若今天净值已公布(cur==今天) → 目标=下一交易日
        import datetime as _dt
        cur_dt = _dt.date.fromisoformat(cur)
        today = _dt.date.today()
        if cur_dt >= today:
            nxt = cur_dt + _dt.timedelta(days=1)
            while nxt.weekday() >= 5:   # 跳过周末
                nxt += _dt.timedelta(days=1)
            target = nxt.isoformat()
            lbl = f"下一交易日估算（{target}）"
        else:
            target = cur
            lbl = f"今日盘中估算（{cur}）"
        ref_txt = f"较上一交易日 <b>{nav_prev}</b>（{cur}）"

        range_bar = build_range_bar(band[0], band[1], center)
        intraday = d.get("intraday", {})
        sector_bars = build_sector_bars(intraday)

        hero = f"""
<div class="hero">
  <div class="top-row">
    <span class="lbl">{lbl}</span>
    <span class="badge">模型 v3</span>
  </div>
  <div class="main {cl(center)}">{center:+.2f}%</div>
  <div class="sub">预计净值 <b>{nav_center}</b></div>
  <div class="ref">{ref_txt}</div>
  {spark_in_hero}
</div>"""

        metrics = f"""
<div class="metrics">
  <div class="m m-range">
    <div class="v">{band[0]:+.2f}% ~ {band[1]:+.2f}%</div>
    <div class="l">合理区间（{center:+.2f}%）</div>
    {range_bar}
  </div>
  <div class="m">
    <div class="v"><span class="pill {conf_pill}">{conf}</span></div>
    <div class="l">置信度</div>
    <div class="sub-v">MAE {mae_p3:.2f}%</div>
  </div>
  <div class="m">
    <div class="v"><span class="pill {pill}">{pcb_sig}</span></div>
    <div class="l">PCB 调仓</div>
    <div class="sub-v">θ {d.get('theta_pcb',0)*100:.1f}%</div>
  </div>
</div>"""

        details = f"""
<details class="card" id="rt_box"><summary>实时行情快照（Q2口径） <span class="arr">›</span></summary>
<div class="detail">
<div class="rt-hero">
  <div class="rt-box"><div class="rt-lbl">简单加权</div><div class="rt-val" id="rt_chg">--</div></div>
  <div class="rt-box norm"><div class="rt-lbl">归一化</div><div class="rt-val" id="rt_norm_val">--</div></div>
</div>
<div class="rt-tip">
<b>简单加权</b>=前十大股票实际权重×涨跌之和，仅覆盖约 85% 仓位。<br>
<b>归一化</b>=把前十大缩放至 100%，假设"前十大=整个基金"——用于估算若持仓不变时的理论涨跌幅，与模型主数字交叉参考。
</div>
{sector_bars}
<div class="list" id="rt_list"></div>
</div></details>
<div class="card" id="rt_loading"><div class="loading">正在获取实时行情…</div></div>

<details class="card"><summary>各模型预测 <span class="arr">›</span></summary><div class="detail">
  <div class="row"><span class="k">模型中心（误差加权）</span><span class="v2">{d.get('P_final',0):+.2f}%</span></div>
  <div class="row"><span class="k">历史偏差修正</span><span class="v2">{d.get('bias_correction',0):+.2f}%</span></div>
  <div class="row"><span class="k">各模型 MAE</span><span class="v2"><small>{mae_txt}</small></span></div>
  <div class="row"><span class="k">模型明细</span><span class="v2" style="font-size:11.5px">{m_line}</span></div>
</div></details>
<details class="card"><summary>PCB 调仓信号 <span class="arr">›</span></summary><div class="detail">
  <div class="row"><span class="k">有效替代比例 θ</span><span class="v2">{d.get('theta_pcb',0)*100:.1f}%</span></div>
  <div class="row"><span class="k">信号强度</span><span class="v2"><span class="pill {pill}">{pcb_sig}</span></span></div>
  <div class="row"><span class="k">暴露：光通信</span><span class="v2">{d.get('exposure',{}).get('光通信',0)*100:.1f}%</span></div>
  <div class="row"><span class="k">暴露：PCB</span><span class="v2">{d.get('exposure',{}).get('PCB',0)*100:.1f}%</span></div>
  <div class="tip">θ=模型估计从 Q2 组合切到 PCB 的有效比例，非真实持仓；光通信与 PCB 相关性 r≈0.71，存在识别误差。</div>
</div></details>"""
    else:
        metrics = '<div class="metrics"><div class="m"><div class="v">--</div><div class="l">合理区间</div></div><div class="m"><div class="v">--</div><div class="l">置信度</div></div><div class="m"><div class="v">--</div><div class="l">PCB调仓</div></div></div>'
        details = '<div class="card"><div class="loading">暂无数据，请先运行模型</div></div>'

    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<meta http-equiv="refresh" content="600">
<title>006010 盘中估值</title>
<style>{CSS}</style></head><body><div class="wrap">

<div class="hd">
  <div class="fund">006010 国融融银混合C<small>盘中估值 · 打开即刷新</small></div>
  <div class="time"><span class="live"></span><span id="now">--</span><br>上次模型 {d.get('snapshot_time','--') if d else '--'}</div>
</div>

{hero}
{metrics}

{details}

<div class="foot">
<button class="btn" onclick="location.reload()">立即刷新</button>
<div class="hint">主数字=完整模型估算（v3 五模型集成+偏差修正）· 详情=实时口径/模型明细/调仓信号<br>
更新完整模型：本地「一键更新并推送.bat」或 GitHub Actions 手动 Run · 数据非官方净值</div>
</div>

<script>{js}</script>
</div></body></html>"""

if __name__ == "__main__":
    d = load_json("result.json")
    if d:
        d["snapshot_time"] = time.strftime("%Y-%m-%d %H:%M")
    html = build_html(d)
    with open(os.path.join(DOCS, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    if d:
        json.dump(d, open(os.path.join(DOCS, "data.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
    print(f"[static] docs/index.html 已生成（快照中心 {d.get('P_final_corr') if d else 'N/A'}%）")