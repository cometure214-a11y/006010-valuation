#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_static.py —— 云端版：渲染手机端估值页（v2 改版）

v2 改版（2026-08-24，基于多模态 AI 评审意见）：
  [1] 首屏重构：只留 基金名+时间 / 大数字涨跌+官方净值+置信度大白话 / 大幅走势图
  [2] 配色统一：红涨绿跌（.up 红 / .down 绿），走势图折线随涨跌变色，进度条同色系渐变
  [3] 桌面响应式：>=1024px 左右分栏（左65% 核心信息 / 右35% 辅助信息）
  [4] 普通/高级模式：默认普通（只显示结论），右上角开关切高级（PCB/θ/MAE/模型明细/误差）
  [5] 文案精简：删重复日期/净值，「较上一交易日」改「基准净值」；底部开发说明默认隐藏

v4 基础功能（保留）：
  - Hero 底部内嵌近 10 日 NAV 迷你走势 sparkline（含今日估算）
  - 合理区间进度条 + 实时行情三口径 + 行业热力条 + 个股列表
  - 误差走势卡（error_history.json）
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

# PCB 候选篮子（模型调仓识别用，前端拉实时行情做调仓修正口径）
PCB_BASKET = ["sz002916", "sz002463", "sz300476"]

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
      border:1px solid rgba(255,255,255,.07);border-radius:20px;padding:22px 20px 16px;text-align:center;
      margin-bottom:10px;overflow:hidden}
.hero::before{content:'';position:absolute;top:-40px;right:-40px;width:160px;height:160px;
              background:radial-gradient(circle,rgba(240,78,60,.10) 0%,transparent 70%);pointer-events:none}
.hero::after{content:'';position:absolute;bottom:-30px;left:-30px;width:140px;height:140px;
             background:radial-gradient(circle,rgba(78,201,176,.07) 0%,transparent 70%);pointer-events:none}
.hero .top-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
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
/* 置信度大白话（v2 新增） */
.conf-plain{display:inline-block;margin-top:10px;padding:6px 14px;border-radius:20px;
            background:rgba(48,164,108,.12);color:#4ec9b0;font-size:12px;font-weight:600}
.conf-plain.low{background:rgba(220,202,106,.12);color:#dcdcaa}

/* sparkline（v2 加大：44 -> 110px，含更多刻度） */
.spark{margin-top:14px;padding-top:12px;border-top:1px solid rgba(255,255,255,.05)}
.spark svg{display:block;margin:0 auto;width:100%;height:110px}
.spark .lbl-row{display:flex;justify-content:space-between;font-size:10px;color:#5c6470;margin-top:4px}

/* ─── 关注层（v2 精简为 2 卡：区间 + 模式） ─── */
.metrics{display:grid;grid-template-columns:1.6fr 1fr;gap:8px;margin-bottom:10px}
.m{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);border-radius:12px;
   padding:12px 8px;text-align:center;position:relative}
.m .v{font-size:14.5px;font-weight:700;font-variant-numeric:tabular-nums;margin-bottom:4px}
.m .l{font-size:10px;color:#8892a3}
.m .sub-v{font-size:10px;color:#5c6470;margin-top:2px}
.m-range .bar{position:relative;height:8px;border-radius:4px;
              background:rgba(255,255,255,.07);margin:8px 6px 4px;overflow:hidden}
.m-range .bar .fill{position:absolute;left:0;top:0;height:100%;border-radius:4px;
                    background:linear-gradient(90deg,rgba(48,164,108,.45),rgba(48,164,108,.15))}
.m-range .bar .fill.pos{background:linear-gradient(90deg,rgba(240,78,60,.15),rgba(240,78,60,.45))}
.m-range .bar .marker{position:absolute;top:50%;width:14px;height:14px;border-radius:50%;
                      background:#e6e9ef;transform:translate(-50%,-50%);
                      box-shadow:0 0 0 3px rgba(230,233,239,.2)}
.pill{display:inline-block;padding:2px 10px;border-radius:20px;font-size:11px;font-weight:700}
.pill.strong{background:rgba(240,78,60,.15);color:#f04e3c}
.pill.mid{background:rgba(220,202,106,.14);color:#dcdcaa}
.pill.weak{background:rgba(139,148,163,.14);color:#a3adbd}
.pill.high{background:rgba(48,164,108,.14);color:#30a46c}

/* ─── 高级模式（v2 新增：默认隐藏专业指标，开关切换） ─── */
.adv{display:none}
body.adv-on .adv{display:block}
body.adv-on .adv-inline{display:inline-block}
.adv-toggle{position:relative;font-size:10px;color:#6b7280;background:none;border:1px solid rgba(255,255,255,.12);
            border-radius:20px;padding:3px 10px;cursor:pointer;margin-left:8px;vertical-align:middle}
body.adv-on .adv-toggle{color:#4ec9b0;border-color:rgba(78,201,176,.4)}

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
.rt-hero{display:flex;gap:8px;margin-bottom:8px}
.rt-box{flex:1;background:rgba(255,255,255,.03);border-radius:10px;padding:10px 6px;text-align:center;
        border:1px solid rgba(255,255,255,.05);position:relative}
.rt-box.rt-main{background:rgba(78,201,176,.07);border:1px solid rgba(78,201,176,.25)}
.rt-box .rt-lbl{font-size:10px;color:#8892a3;margin-bottom:4px}
.rt-box .rt-val{font-size:18px;font-weight:800;font-variant-numeric:tabular-nums;line-height:1.2}
.rt-box .rt-sub{font-size:9px;color:#5c6470;margin-top:3px}
.rt-band{font-size:11px;color:#b8c0cc;background:rgba(255,255,255,.03);border-radius:8px;
         padding:7px 10px;margin-bottom:8px;text-align:center}
.rt-band b{font-variant-numeric:tabular-nums}
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

/* 底部（v2：开发说明默认隐藏，仅高级模式显示） */
.foot{display:flex;flex-direction:column;gap:6px;margin-top:10px}
.btn{display:block;width:100%;padding:12px;border:none;border-radius:12px;font-size:13px;font-weight:700;cursor:pointer;
     background:#1c2530;color:#c6ccd6;border:1px solid rgba(255,255,255,.07)}
.btn:active{background:#26313f}
.hint{font-size:10px;color:#5c6470;text-align:center;line-height:1.7}

.loading{color:#8892a3;font-size:12px;text-align:center;padding:14px 0}
#rt_box{display:none}
.tip{font-size:10.5px;color:#6b7280;line-height:1.6;padding-top:6px}

/* ─── v4：数据质量横幅 / 估算模式徽标 / 覆盖率条 ─── */
.banner{display:flex;gap:8px;align-items:flex-start;border-radius:12px;padding:10px 12px;
        margin-bottom:10px;font-size:11.5px;line-height:1.6}
.banner.warn{background:rgba(220,202,106,.10);border:1px solid rgba(220,202,106,.22);color:#dcdcaa}
.banner.err{background:rgba(240,78,60,.10);border:1px solid rgba(240,78,60,.26);color:#f0806f}
.banner .ico{flex:none;font-weight:700}
.banner b{color:#e6e9ef}
.mode{font-size:10px;padding:3px 9px;border-radius:20px;font-weight:700;letter-spacing:.3px}
.mode.settled{background:rgba(48,164,108,.14);color:#30a46c}
.mode.intraday{background:rgba(240,78,60,.13);color:#f04e3c}
.mode.next{background:rgba(139,148,163,.14);color:#a3adbd}
.cov{margin-top:8px}
.cov .bar{height:6px;border-radius:4px;background:rgba(255,255,255,.06);overflow:hidden;display:flex}
.cov .bar i{display:block;height:100%}
.cov .bar i.a{background:linear-gradient(90deg,#4ec9b0,#30a46c)}
.cov .bar i.b{background:rgba(139,148,163,.30)}
.cov .lg{display:flex;justify-content:space-between;font-size:10px;color:#6b7280;margin-top:4px}
.drop{font-size:10.5px;color:#6b7280;line-height:1.7;padding-top:6px;
      border-top:1px dashed rgba(255,255,255,.07);margin-top:6px}
.drop code{color:#a3adbd;font-size:10px}
.wbar{display:flex;height:5px;border-radius:3px;overflow:hidden;background:rgba(255,255,255,.05);margin-top:5px}
.wbar i{display:block;height:100%}

/* ─── v2 桌面响应式：>=1024px 左右分栏 ─── */
@media (min-width:1024px){
  .wrap{max-width:1120px;padding:18px 24px 32px}
  .grid-main{display:grid;grid-template-columns:minmax(0,65%) minmax(0,35%);gap:14px;align-items:start}
  .col-side{display:grid;grid-template-columns:1fr;gap:8px}
  .hero{padding:26px 28px 18px}
  .hero .main{font-size:92px}
  .spark svg{height:130px}
  .metrics{grid-template-columns:1.6fr 1fr}
}
"""

JS = """
const TOP10 = [
__TOP10_JS__
];
// 模型参数（由 gen_static 注入，用于三口径实时估算）
const THETA_PCB = __THETA_PCB__;      // 光通信→PCB 有效替代比例
const THETA_MKT = __THETA_MKT__;
const PCB_CODES = __PCB_CODES__;      // PCB 篮子（模型候选池）

const EM_PREFIX = {sh:'1', sz:'0'};
let emFired = false;
function loadQuotes(){
  const s = TOP10.map(t=>t.s).join(',') + ',' + PCB_CODES.join(',') + ',sz000852';
  const sc = document.createElement('script');
  sc.src = 'https://qt.gtimg.cn/q=' + s;
  sc.onload = function(){ if(!calcRealtime()) loadEM(); };
  sc.onerror = function(){ loadEM(); };
  document.body.appendChild(sc);
}
function loadEM(){
  if(emFired) return;
  emFired = true;
  const all = TOP10.map(t=>t.s).concat(PCB_CODES, ['sz000852']);
  let done = 0;
  all.forEach(code=>{
    const m = code.match(/^(sh|sz)(\d+)$/);
    const secid = EM_PREFIX[m[1]] + '.' + m[2];
    const cb = 'em_cb_' + m[2];
    window[cb] = function(d){
      const p = d && d.data;
      if(p && p.f170 != null){
        const chg = p.f170 || 0;
        window['v_'+code] = ['', p.f58 || code, code, (100+chg).toFixed(2), '100'].join('~');
      }
      if(++done >= all.length) calcRealtime();
    };
    const sc = document.createElement('script');
    sc.src = 'https://push2.eastmoney.com/api/qt/stock/get?secid=' + secid +
             '&fields=f58,f170&fltt=2&invt=2&cb=' + cb;
    document.body.appendChild(sc);
  });
}
function fmt(v,d){return (v>0?'+':'') + v.toFixed(d) + '%';}
function cl(v){return v>0.005?'up':(v<-0.005?'down':'flat');}

// ── v2 高级模式开关（localStorage 记忆）──
function initAdvMode(){
  const on = localStorage.getItem('006010_adv') === '1';
  document.body.classList.toggle('adv-on', on);
  const b = document.getElementById('adv_btn');
  if(b) b.textContent = on ? '高级模式 ●' : '高级模式';
}
function toggleAdv(){
  const on = !document.body.classList.contains('adv-on');
  document.body.classList.toggle('adv-on', on);
  localStorage.setItem('006010_adv', on ? '1' : '0');
  const b = document.getElementById('adv_btn');
  if(b) b.textContent = on ? '高级模式 ●' : '高级模式';
}

function calcRealtime(){
  let num=0, wsum=0, n=0, rows='';
  let pcbVals=[], optVals=[];
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
    optVals.push(r);
    rows += '<div class="item"><span><span class="code">'+p[2]+'</span><span class="w">'+t.w.toFixed(2)+'%</span></span>' +
            '<span class="ret '+cl(r)+'">'+(r>=0?'▲':'▼')+' '+fmt(r,2)+'</span></div>';
  });
  PCB_CODES.forEach(c=>{
    const v = window['v_'+c];
    if(!v) return;
    const p = v.split('~');
    const cur = parseFloat(p[3]), prev = parseFloat(p[4]);
    if(!prev) return;
    pcbVals.push((cur-prev)/prev*100);
  });
  if(n<5){
    if(!emFired){ return false; }
    document.getElementById('rt_loading').innerHTML='<div class="loading">行情解析异常，请稍后刷新</div>';
    return true;
  }
  // ── 三口径实时估算 ──
  const conservative = num;                    // ① 保守：Q2权重，剩余仓位≈现金/未知
  const pcbMean = pcbVals.length ? pcbVals.reduce((a,b)=>a+b,0)/pcbVals.length : 0;
  const adjusted = num + THETA_PCB*(pcbMean - num) + THETA_MKT*(mktPct() - num);  // ② 调仓修正
  const normalized = num/wsum;                 // ③ 归一化：前十大=全仓假设
  const optMean = optVals.length ? optVals.reduce((a,b)=>a+b,0)/optVals.length : 0;

  document.getElementById('rt_box').style.display='block';
  document.getElementById('rt_loading').style.display='none';
  document.getElementById('now').textContent = new Date().toLocaleString('zh-CN',{hour12:false});
  // 保守口径
  setVal('rt_con', conservative, 'rt_con_box');
  // 调仓口径（主推）
  setVal('rt_adj', adjusted, 'rt_adj_box');
  // 归一化口径
  setVal('rt_norm_val', normalized, 'rt_norm_box');
  // 区间提示
  const lo = Math.min(conservative, adjusted, normalized);
  const hi = Math.max(conservative, adjusted, normalized);
  const spread = hi - lo;
  document.getElementById('rt_band').innerHTML =
    '三口径区间 <b>' + fmt(lo,2) + ' ~ ' + fmt(hi,2) + '</b>（跨度 ' + fmt(spread,2) + '）';
  document.getElementById('rt_list').innerHTML = rows;
  return true;
}
function setVal(id, v, boxId){
  const el = document.getElementById(id);
  el.textContent = fmt(v,2);
  el.className = 'rt-val ' + cl(v);
  document.getElementById(boxId).className = 'rt-box' + (boxId==='rt_adj_box' ? ' rt-main' : '');
}
function mktPct(){
  const v = window['v_sz000852'];
  if(!v) return 0;
  const p = v.split('~');
  const cur = parseFloat(p[3]), prev = parseFloat(p[4]);
  return prev ? (cur-prev)/prev*100 : 0;
}
initAdvMode();
loadQuotes();
setInterval(loadQuotes, 60000);
"""

def build_sparkline(navs_dates, est_nav=None):
    """Build inline SVG sparkline from NAV history + optional estimated point."""
    if len(navs_dates) < 3:
        return ''
    pts = navs_dates[-10:]
    if est_nav is not None:
        pts.append(est_nav)
    vals = [p[1] for p in pts]
    vmin, vmax = min(vals), max(vals)
    vrange = vmax - vmin or 1
    W, H = 440, 110
    pad = 8
    innerW = W - pad*2
    innerH = H - pad*2
    n = len(vals)
    step = innerW / (n-1) if n > 1 else 0
    coords = []
    for i, v in enumerate(vals):
        x = pad + i * step
        y = pad + (1 - (v - vmin)/vrange) * innerH
        coords.append((x, y))
    d = 'M' + ' L'.join(f'{x:.1f},{y:.1f}' for x,y in coords)
    area_d = d + f' L{coords[-1][0]:.1f},{H-pad} L{coords[0][0]:.1f},{H-pad} Z'
    up = vals[-1] >= vals[0]
    color = '#f04e3c' if up else '#30a46c'
    glow = '#f04e3c' if up else '#30a46c'
    lx, ly = coords[-1]
    def md(s):
        return s.split('-')[1]+'-'+s.split('-')[2] if '-' in s else ''
    first_date = md(pts[0][0])
    mid_date = md(pts[len(pts)//2][0])
    last_date = md(pts[-1][0])
    date_labels = f'<span>{first_date}</span><span>{mid_date}</span><span>{last_date}</span>'
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
  <path d="{d}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round"/>
  <circle cx="{lx:.1f}" cy="{ly:.1f}" r="3.5" fill="{glow}" stroke="#0b0f15" stroke-width="1.5"/>
</svg>
<div class="lbl-row">{date_labels}</div>
</div>'''
    return svg

def build_range_bar(lo, hi, cur):
    """Build horizontal range bar with marker（v2：同色系深浅渐变，按当前方向）. """
    lo_f, hi_f, cur_f = float(lo), float(hi), float(cur)
    span = hi_f - lo_f or 0.01
    pos = max(0, min(1, (cur_f - lo_f)/span)) * 100
    fill_pct = max(pos, 8)
    fill_cls = 'pos' if cur_f >= 0 else ''
    svg = f'''
<div class="bar">
  <div class="fill {fill_cls}" style="width:{fill_pct:.1f}%"></div>
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

def build_error_spark(records):
    """近20日估值误差折线（围绕0线），返回 SVG 字符串。records: [{date, err, ...}]"""
    recs = records[-20:]
    if len(recs) < 3:
        return ''
    vals = [float(r.get("err", 0)) for r in recs]
    vmin, vmax = min(vals + [0]), max(vals + [0])
    vrange = vmax - vmin or 1
    W, H = 440, 90
    pad = 8
    innerW, innerH = W - pad * 2, H - pad * 2
    n = len(vals)
    step = innerW / (n - 1) if n > 1 else 0
    coords = []
    for i, v in enumerate(vals):
        x = pad + i * step
        y = pad + (1 - (v - vmin) / vrange) * innerH
        coords.append((x, y))
    d = 'M' + ' L'.join(f'{x:.1f},{y:.1f}' for x, y in coords)
    y0 = pad + (1 - (0 - vmin) / vrange) * innerH
    zero = (f'<line x1="{pad}" y1="{y0:.1f}" x2="{W - pad}" y2="{y0:.1f}" '
            f'stroke="rgba(255,255,255,.14)" stroke-width="0.5" stroke-dasharray="3 3"/>')
    mae = sum(abs(v) for v in vals) / n
    color = '#30a46c' if mae < 0.5 else ('#dcdcaa' if mae < 1.0 else '#f04e3c')
    first_d = recs[0].get('date', '')[-5:]
    last_d = recs[-1].get('date', '')[-5:]
    svg = f'''<div class="spark">
<svg viewBox="0 0 {W} {H}" preserveAspectRatio="none">
{zero}
<path d="{d}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round"/>
</svg>
<div class="lbl-row"><span>{first_d} ~ {last_d}</span><span>近{n}日 MAE {mae:.2f}%</span></div>
</div>'''
    return svg

def build_html(d):
    top10_js = ",\n".join(f'  {{s:"{s}", w:{w}}}' for s, w in TOP10)
    theta_pcb = (d or {}).get("theta_pcb", 0) or 0
    theta_mkt = (d or {}).get("theta_mkt", 0) or 0
    pcb_codes = "[" + ",".join(f'"{c}"' for c in PCB_BASKET) + "]"
    js = JS.replace("__TOP10_JS__", top10_js)
    js = js.replace("__THETA_PCB__", f"{theta_pcb:.4f}")
    js = js.replace("__THETA_MKT__", f"{theta_mkt:.4f}")
    js = js.replace("__PCB_CODES__", pcb_codes)

    def cl(v):
        return "up" if v > 0.0001 else ("down" if v < -0.0001 else "flat")

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
    conf_plain = ''
    mae_p3_txt = ''

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
        mae_p4 = mae.get("P4", mae_p3)
        mae_txt = f"P1 {mae_p1:.2f} | P2 {mae_p2:.2f} | P3 {mae_p3:.2f} | P4 {mae_p4:.2f}"
        mae_p3_txt = f"{mae_p3:.2f}%"

        spark_in_hero = spark_html
        import datetime as _dt
        cur = d.get("cur_date", "")
        target = d.get("target_date", cur)
        nav_prev_date = d.get("nav_prev_date", cur)
        official_nav = d.get("official_nav")
        official_chg = d.get("official_chg")
        official_date = d.get("official_date")

        conf_lvl = conf_pill
        if conf_lvl == "high":
            conf_plain_html = f'<span class="conf-plain">高准确度 · 历史误差约 ±{mae_p3:.1f}%</span>'
        elif conf_lvl == "mid":
            conf_plain_html = '<span class="conf-plain" style="background:rgba(220,202,106,.12);color:#dcdcaa">中等准确度 · 参考谨慎</span>'
        else:
            conf_plain_html = '<span class="conf-plain low">低准确度 · 参考需谨慎</span>'

        if official_nav is not None and official_chg is not None:
            hero_main = official_chg
            hero_sub = f"官方净值 <b>{official_nav}</b>"
            hero_ref = f"基准净值 {nav_prev}（{nav_prev_date}）· 模型估算 {center:+.2f}%"
            diff = official_chg - center
            lbl = f"今日官方净值（{official_date}）"
            badge = "官方已公布"
            if abs(diff) > 0.005:
                hero_ref += f" · 模型差 {diff:+.2f}pp"
            else:
                hero_ref += " · 模型一致"
        else:
            hero_main = center
            hero_sub = f"预计净值 <b>{nav_center}</b>"
            hero_ref = f"基准净值 {nav_prev}（{nav_prev_date}）"
            today = _dt.date.today().isoformat()
            lbl = f"今日盘中估算（{cur}）" if cur >= today else f"盘中估算（{cur}）"
            badge = "模型估算"

        range_bar = build_range_bar(band[0], band[1], float(hero_main))
        intraday = d.get("intraday", {})
        sector_bars = build_sector_bars(intraday)

        hero = f"""
<div class="hero">
  <div class="top-row">
    <span class="lbl">{lbl}</span>
    <span class="badge">{badge}</span>
  </div>
  <div class="main {cl(float(hero_main))}">{hero_main:+.2f}%</div>
  <div class="sub">{hero_sub}</div>
  <div class="ref">{hero_ref}</div>
  {conf_plain_html}
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
</div>"""

        details = f"""
<div class="col-main">
<details class="card" id="rt_box" open><summary>实时行情快照（三口径） <span class="arr">›</span></summary>
<div class="detail">
<div class="rt-hero">
  <div class="rt-box" id="rt_con_box"><div class="rt-lbl">保守口径</div><div class="rt-val" id="rt_con">--</div><div class="rt-sub">剩余仓位≈现金</div></div>
  <div class="rt-box rt-main" id="rt_adj_box"><div class="rt-lbl">调仓修正 ★</div><div class="rt-val" id="rt_adj">--</div><div class="rt-sub">θ_pcb={theta_pcb*100:.1f}%</div></div>
  <div class="rt-box norm" id="rt_norm_box"><div class="rt-lbl">归一化</div><div class="rt-val" id="rt_norm_val">--</div><div class="rt-sub">前十大=全仓</div></div>
</div>
<div class="rt-tip">
<b>保守口径</b>=Q2前十大加权，剩余~15%仓位按现金/未知计（下限）。<br>
<b>调仓修正</b>=叠加模型θ_pcb调仓信号（光通信→PCB），最贴近真实持仓（★主推）。<br>
<b>归一化</b>=假设前十大=整个基金（上限），与保守口径构成合理区间。
</div>
<div class="rt-band" id="rt_band"></div>
{sector_bars}
<div class="list" id="rt_list"></div>
</div></details>
<div class="card" id="rt_loading"><div class="loading">正在获取实时行情…</div></div>

<details class="card" open><summary>官方 vs 模型 <span class="arr">›</span></summary><div class="detail">
  <div class="row"><span class="k">官方净值（{official_date or '未公布'}）</span><span class="v2">{official_nav if official_nav is not None else '--'} {f'({official_chg:+.2f}%)' if official_chg is not None else ''}</span></div>
  <div class="row"><span class="k">模型估算（{target}）</span><span class="v2">{center:+.2f}% → 预计 {nav_center}</span></div>
  <div class="row"><span class="k">基准净值</span><span class="v2">{nav_prev}（{nav_prev_date}）</span></div>
  <div class="row"><span class="k">合理区间</span><span class="v2">{band[0]:+.2f}% ~ {band[1]:+.2f}%</span></div>
</div></details>
</div>

<div class="col-side adv">
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
</div></details>
"""

        wts = d.get("model_weights", {})
        top_model = max(wts, key=wts.get) if wts else None
        top_w = wts.get(top_model, 0) if top_model else 0
        if top_model and top_w > 0.7:
            warn_banner = f"""
<div class="banner warn"><span class="ico">!</span>
<span><b>单模型独大</b>：{top_model} 权重 {top_w*100:.0f}%，其他模型被淘汰（MAE 超闸门 {d.get('ensemble_audit',{}).get('gate',1.0)} 倍）。
<button class="adv-toggle" onclick="toggleAdv()">查看高级模式</button></span></div>"""
            details = warn_banner + details

        error_hist = load_json("error_history.json")
        _recs = (error_hist or {}).get("records") or []
        err_card = ''
        if len(_recs) >= 3:
            err_card = f"""<details class="card"><summary>估值误差走势 <span class="arr">›</span></summary><div class="detail">
{build_error_spark(_recs)}
<div class="tip">误差 = 官方涨跌 − 模型估算（pp），越贴近 0 越准。累计 {len(_recs)} 日 · 全期 MAE <b>{sum(abs(float(r.get('err',0))) for r in _recs)/len(_recs):.2f}%</b> · 最近一日 {float(_recs[-1].get('err',0)):+.2f}pp。<br>误差库由每日守望自动积累，模型据此滚动自进化。</div>
</div></details>"""
        elif _recs:
            err_card = '<details class="card"><summary>估值误差走势 <span class="arr">›</span></summary><div class="detail"><div class="tip">误差样本不足（≥3日生效），净值公布后自动积累。</div></div></details>'
        else:
            err_card = '<details class="card"><summary>估值误差走势 <span class="arr">›</span></summary><div class="detail"><div class="tip">暂无误差数据（今晚净值公布后自动积累）。</div></div></details>'
        details += err_card + '</div>'
    else:
        metrics = '<div class="metrics"><div class="m"><div class="v">--</div><div class="l">合理区间</div></div><div class="m"><div class="v">--</div><div class="l">置信度</div></div></div>'
        details = '<div class="card"><div class="loading">暂无数据，请先运行模型</div></div>'

    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<meta http-equiv="refresh" content="600">
<title>006010 盘中估值</title>
<style>{CSS}</style></head><body><div class="wrap">

<div class="hd">
  <div class="fund">006010 国融融银混合C<small>盘中估值 · 打开即刷新</small></div>
  <div class="time"><span class="live"></span><span id="now">--</span><br>上次模型 {d.get('snapshot_time','--') if d else '--'}
  <button class="adv-toggle" id="adv_btn" onclick="toggleAdv()">高级模式</button></div>
</div>

<div class="grid-main">
{hero}
{metrics}

{details}
</div>

<div class="foot">
<button class="btn" onclick="location.reload()">立即刷新</button>
<div class="hint adv">主数字=完整模型估算（v4 五模型集成+偏差修正）· 详情=实时口径/模型明细/调仓信号<br>
更新完整模型：本地「一键更新并推送.bat」或 GitHub Actions 手动 Run · 数据非官方净值</div>
</div>

<script>{js}</script>
</div></body></html>"""

if __name__ == "__main__":
    d = load_json("result.json")
    if d:
        d["snapshot_time"] = time.strftime("%Y-%m-%d %H:%M")
    html = build_html(d)
    out = os.path.join(DOCS, "index.html")
    open(out, "w", encoding="utf-8").write(html)
    print(f"[static] docs/index.html 已生成（快照中心 {d.get('P_final_corr','--') if d else '--'}%）")
    if d:
        data_out = os.path.join(DOCS, "data.json")
        json.dump(d, open(data_out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
