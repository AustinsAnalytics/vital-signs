"""Render the social share card (site/og.png, 1200x630) from the same replay the page's cover sheet plays.

    python tools/og.py          (needs Google Chrome; run after pipeline/export.py)

The card is a blueprint sheet: zone-marked border, the title lettered on guidelines, the replay drawn as a control chart
with its alert stamp, and a title block. Builds tools/og.html, then screenshots it with headless Chrome in a throwaway
profile.
"""
import json
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def chart_svg(r, w=440, h=262):
    """The replay's final state: fastballs against expected (top), the alert's running total (bottom)."""
    P = [(i, v, x, c) for i, v, x, c in r["pitches"] if v is not None]
    vals = [v for _, v, _, _ in P] + [x for _, _, x, _ in P]
    lo, hi = int(min(vals) - 0.3), int(max(vals) + 0.3) + 1
    left, right, top, gap = 34, w - 10, 14, 22
    inner = h - top - 24 - gap
    h_top = round(inner * 0.62)
    y0, y1 = top + h_top + gap, top + inner + gap
    X = lambda i: left + (i - 0.5) / r["n"] * (right - left)
    Y = lambda v: top + (hi - v) / (hi - lo) * h_top
    cmax = max(r["h"] * 1.3, max(c for *_, c in P) * 1.05)
    YC = lambda c: y1 - c / cmax * (y1 - y0)
    pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v, _, _ in P)
    exp = " ".join(f"{X(i):.1f},{Y(x):.1f}" for i, _, x, _ in P)
    steps, prev = [f"{X(0.5):.1f},{YC(0):.1f}"], 0.0
    for i, _, _, c in P:
        steps += [f"{X(i):.1f},{YC(prev):.1f}", f"{X(i):.1f},{YC(c):.1f}"]
        prev = c
    steps.append(f"{X(r['n'] + 0.5):.1f},{YC(prev):.1f}")
    area = " ".join(steps) + f" {X(r['n'] + 0.5):.1f},{y1:.1f}"
    ticks = "".join(f'<text x="{left - 7}" y="{Y(t) + 4:.1f}" text-anchor="end">{t}</text>' for t in range(lo, hi + 1) if Y(t) > top + 8)
    dots = "".join(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="3"/>' for i, v, _, _ in P)
    ax = X(r["alert_at"])
    return f"""<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">
  <text class="u" x="{left - 7}" y="{top - 2}" text-anchor="end">mph</text>{ticks}
  <line class="sep" x1="{left}" x2="{right}" y1="{top + h_top + gap / 2}" y2="{top + h_top + gap / 2}"/>
  <text class="u" x="{left + 2}" y="{y0 + 10}">alert total</text>
  <line class="base" x1="{left}" x2="{right}" y1="{y1}" y2="{y1}"/>
  <line class="lim" x1="{left}" x2="{right}" y1="{YC(r['h']):.1f}" y2="{YC(r['h']):.1f}"/>
  <text class="lt" x="{right - 2}" y="{YC(r['h']) - 6:.1f}" text-anchor="end">limit {r['h']:.1f}</text>
  <polygon class="ca" points="{area}"/><polyline class="cs" points="{' '.join(steps)}"/>
  <polyline class="e" points="{exp}"/>
  <line class="a" x1="{ax:.1f}" x2="{ax:.1f}" y1="{top - 6}" y2="{y1}"/>
  <polyline class="p" points="{pts}"/><g class="d">{dots}</g>
</svg>""", ax


def zones(w=1200, h=630, inset=20):
    """Drawing-sheet border: tick marks dividing each edge into zones, numbered across and lettered down."""
    out, cols, rows = [], 8, 4
    for k in range(1, cols):
        x = inset + k * (w - 2 * inset) / cols
        out += [f'<line x1="{x:.0f}" x2="{x:.0f}" y1="{inset}" y2="{inset + 10}"/>', f'<line x1="{x:.0f}" x2="{x:.0f}" y1="{h - inset - 10}" y2="{h - inset}"/>']
    for k in range(cols):
        x = inset + (k + 0.5) * (w - 2 * inset) / cols
        out += [f'<text x="{x:.0f}" y="{inset + 8.5}">{cols - k}</text>', f'<text x="{x:.0f}" y="{h - inset - 2.5}">{cols - k}</text>']
    for k in range(1, rows):
        y = inset + k * (h - 2 * inset) / rows
        out += [f'<line y1="{y:.0f}" y2="{y:.0f}" x1="{inset}" x2="{inset + 10}"/>', f'<line y1="{y:.0f}" y2="{y:.0f}" x1="{w - inset - 10}" x2="{w - inset}"/>']
    for k in range(rows):
        y = inset + (k + 0.5) * (h - 2 * inset) / rows + 3
        out += [f'<text x="{inset + 5}" y="{y:.0f}">{"DCBA"[k]}</text>', f'<text x="{w - inset - 5}" y="{y:.0f}">{"DCBA"[k]}</text>']
    return f'<svg class="z" width="{w}" height="{h}"><rect x="{inset}" y="{inset}" width="{w - 2 * inset}" height="{h - 2 * inset}"/>' \
           f'<rect class="in" x="{inset + 10}" y="{inset + 10}" width="{w - 2 * inset - 20}" height="{h - 2 * inset - 20}"/>{"".join(out)}</svg>'


def main():
    replays = json.loads((ROOT / "site" / "data" / "replays.json").read_text())
    summary = json.loads((ROOT / "site" / "data" / "summary.json").read_text())
    r = next((x for x in replays if x["team"] == "DET" and x["n"] - x["alert_at"] >= 5), replays[0])
    seasons = f"{summary['seasons'][0]}–{str(summary['seasons'][-1])[-2:]}"
    pitches = f"{summary['build']['pitches'] / 1e6:.1f}M"
    svg, ax = chart_svg(r)
    last = [p for p in r["pitches"] if p[1] is not None][-1]
    role = "starter" if r["role"] == "SP" else "reliever"
    days = (datetime.strptime(r["il_date"], "%Y-%m-%d") - datetime.strptime(r["date"], "%Y-%m-%d")).days
    when = "the next day" if days == 1 else f"{days} days later"
    stamp_left = 712 + 14 + ax + 16
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Architects+Daughter&family=Atkinson+Hyperlegible+Next:wght@400;500;700&family=Martian+Mono:wdth,wght@75..112.5,400..800&display=block" rel="stylesheet">
<style>
* {{ box-sizing: border-box; margin: 0; }}
body {{ width: 1200px; height: 630px; overflow: hidden; position: relative; background: #0e2a4b; color: #eef4fb; font-family: "Atkinson Hyperlegible Next"; }}
body::before {{ content: ""; position: absolute; inset: 0; opacity: .3; background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='220' height='220'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='3' stitchTiles='stitch'/%3E%3CfeColorMatrix values='0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 .7 -.32'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E"); }}
.z {{ position: absolute; inset: 0; }}
.z rect {{ fill: none; stroke: rgba(238,244,251,.8); stroke-width: 1.5; }} .z rect.in {{ stroke: rgba(214,230,250,.3); stroke-width: 1; }}
.z line {{ stroke: rgba(238,244,251,.8); }} .z text {{ fill: #93acc9; font: 600 10px "Martian Mono"; text-anchor: middle; }}
.l {{ position: absolute; left: 74px; top: 58px; width: 600px; }}
.k {{ font: 500 13px "Martian Mono"; font-stretch: 87.5%; letter-spacing: .12em; color: #93acc9; text-transform: uppercase; white-space: nowrap; }}
h1 {{ font: 800 104px/1 "Martian Mono"; font-stretch: 112.5%; text-transform: uppercase; letter-spacing: -.02em; margin: 22px 0 20px; }}
h1 span {{ display: block; position: relative; width: max-content; }} h1 span + span {{ margin-top: .14em; }}
h1 span::before, h1 span::after {{ content: ""; position: absolute; left: -16px; right: -56px; height: 1px; background: rgba(150,200,255,.34); }}
h1 span::before {{ top: .1em; }} h1 span::after {{ top: .9em; }}
.s {{ font-size: 24px; line-height: 1.35; color: #c3d4e8; max-width: 570px; }}
.gn {{ margin-top: 20px; width: 570px; }}
.gn div {{ display: flex; justify-content: space-between; align-items: baseline; padding: 8px 0; border-bottom: 1px dashed rgba(214,230,250,.3);
  font: 500 15px "Martian Mono"; font-stretch: 87.5%; color: #c3d4e8; letter-spacing: .04em; text-transform: uppercase; }}
.gn mark {{ font: 700 15px "Martian Mono"; font-stretch: 87.5%; color: #eef4fb; letter-spacing: .06em; padding: 1px 7px; background: rgba(255,205,60,.32); border-radius: 3px 7px 4px 8px; }}
.dt {{ position: absolute; left: 712px; top: 74px; width: 414px; background: #12325a; border: 1.5px solid rgba(238,244,251,.8); box-shadow: 4px 4px 0 rgba(0,0,0,.25); }}
.dh {{ display: flex; border-bottom: 1px solid rgba(238,244,251,.8); font: 600 11px "Martian Mono"; font-stretch: 87.5%; letter-spacing: .12em; text-transform: uppercase; white-space: nowrap; }}
.dh b {{ background: #eef4fb; color: #12325a; padding: 9px 11px; }} .dh span {{ padding: 9px 11px; color: #93acc9; overflow: hidden; }}
.pl {{ margin: 12px 12px 0; border: 1px solid rgba(214,230,250,.3); background-image: linear-gradient(rgba(150,200,255,.085) 1px, transparent 1px), linear-gradient(90deg, rgba(150,200,255,.085) 1px, transparent 1px); background-size: 20px 20px; }}
svg text {{ fill: #93acc9; font: 500 10.5px "Martian Mono"; }}
svg .sep {{ stroke: rgba(214,230,250,.3); }} svg .base {{ stroke: #93acc9; }}
svg .lim {{ stroke: #f05a4b; stroke-width: 1.4; stroke-dasharray: 5 4; }} svg .lt {{ fill: #f05a4b; font-weight: 700; }}
svg .ca {{ fill: rgba(240,90,75,.2); }} svg .cs {{ fill: none; stroke: #f05a4b; stroke-width: 1.8; }}
svg .e {{ fill: none; stroke: #9cc7f5; stroke-width: 1.6; stroke-dasharray: 6 4; }}
svg .a {{ stroke: #f05a4b; stroke-width: 1.6; }}
svg .p {{ fill: none; stroke: #eef4fb; stroke-width: 2.2; stroke-linejoin: round; }} svg .d circle {{ fill: #eef4fb; }}
.rd {{ display: grid; grid-template-columns: 1fr 1fr 1fr; margin: 12px; border: 1px solid rgba(238,244,251,.8); }}
.rd div {{ padding: 8px 10px; border-left: 1px solid rgba(238,244,251,.8); font: 600 10px "Martian Mono"; font-stretch: 87.5%; letter-spacing: .12em; color: #93acc9; text-transform: uppercase; }}
.rd div:first-child {{ border-left: 0; }}
.rd b {{ display: block; margin-top: 6px; font: 700 28px/1 "Martian Mono"; font-stretch: 87.5%; letter-spacing: -.02em; color: #eef4fb; }}
.rd .x b {{ color: #9cc7f5; }} .rd .c, .rd .c b {{ color: #f05a4b; }}
.st {{ position: absolute; left: {stamp_left:.0f}px; top: 290px; transform: rotate(-8deg); padding: 6px 12px 5px; border: 3px solid #f05a4b; outline: 1px solid #f05a4b; outline-offset: 2px;
  color: #f05a4b; text-align: center; filter: url(#ink); }}
.st b {{ display: block; font: 800 24px/1 "Martian Mono"; font-stretch: 112.5%; letter-spacing: .12em; }}
.st span {{ display: block; margin-top: 4px; font: 700 9.5px/1 "Martian Mono"; font-stretch: 87.5%; letter-spacing: .14em; }}
.tb {{ position: absolute; left: 712px; top: 486px; width: 414px; display: grid; grid-template-columns: 1.25fr 1fr 1fr; border: 1.5px solid rgba(238,244,251,.8); background: #12325a; }}
.tb div {{ padding: 8px 10px 9px; border-left: 1px solid rgba(238,244,251,.8); font: 600 13px "Martian Mono"; font-stretch: 87.5%; color: #eef4fb; }}
.tb div:first-child {{ border-left: 0; }}
.tb span {{ display: block; margin-bottom: 5px; font: 700 8.5px "Martian Mono"; letter-spacing: .18em; color: #93acc9; text-transform: uppercase; }}
.hand {{ position: absolute; left: 74px; top: 548px; font: 400 20px "Architects Daughter"; color: #ff7f70; }}
</style></head><body>
<svg width="0" height="0" style="position:absolute"><filter id="ink" x="-8%" y="-15%" width="116%" height="130%"><feTurbulence type="fractalNoise" baseFrequency=".95" numOctaves="2" seed="11" result="g"/><feColorMatrix in="g" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 -3.2 2.45" result="h"/><feComposite in="SourceGraphic" in2="h" operator="in" result="s"/><feDisplacementMap in="s" in2="g" scale="2.2" xChannelSelector="R" yChannelSelector="G"/></filter></svg>
{zones()}
<div class="l">
  <div class="k">Signal processing · Bayesian state-space · Causal inference</div>
  <h1><span>Vital</span><span>Signs</span></h1>
  <p class="s">What public pitch tracking can, and can't, tell you about a pitcher's arm.</p>
  <div class="gn"><div>See an injury coming? <mark>Not in advance</mark></div><div>Catch it in the game? <mark>Sometimes</mark></div><div>Does extra rest help? <mark>Not for velocity</mark></div></div>
</div>
<div class="hand">a real outing: alert on fastball {r["alert_at"]} of {r["n"]}, injured list {when} →</div>
<div class="dt">
  <div class="dh"><b>Detail A · Replay</b><span>{r["name"]} · {r["team"]} · {role} · {datetime.strptime(r["date"], "%Y-%m-%d"):%b %-d, %Y}</span></div>
  <div class="pl">{svg}</div>
  <div class="rd"><div>FB velo<b>{last[1]:.1f}</b></div><div class="x">Expected<b>{last[2]:.1f}</b></div><div class="c">Alert total<b>{last[3]:.1f}</b></div></div>
</div>
<div class="st"><b>ALERT</b><span>FASTBALL {r["alert_at"]} OF {r["n"]}</span></div>
<div class="tb"><div><span>Drawn by</span>Austin Gentile</div><div><span>Seasons</span>MLB {seasons}</div><div><span>Data</span>{pitches} pitches</div></div>
</body></html>"""
    page = ROOT / "tools" / "og.html"
    page.write_text(html, encoding="utf-8")
    out = ROOT / "site" / "og.png"
    profile = tempfile.mkdtemp(prefix="og-chrome-")
    try:
        subprocess.run([CHROME, "--headless=new", f"--user-data-dir={profile}", "--hide-scrollbars", "--force-device-scale-factor=1",
                        "--window-size=1200,630", "--virtual-time-budget=8000", f"--screenshot={out}", page.as_uri()],
                       check=True, capture_output=True, timeout=120)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    print("wrote", out, out.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()
