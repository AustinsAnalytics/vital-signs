/* Vital Signs: the cover-sheet replay, charts, explorer, replay and the Tigers board.
   Every number comes from data/*.json, written by the pipeline. Colors come from CSS tokens (see style.css),
   so vellum and blueprint share one set of drawing code. */
const $ = (s) => document.querySelector(s);
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const pct = (x, d = 0) => (100 * x).toFixed(d) + "%";
const minus = (s) => String(s).replace(/-/g, "−");
const signed = (x, d = 1) => {
  const r = +x.toFixed(d);
  return r === 0 ? (0).toFixed(d) : minus((r > 0 ? "+" : "") + r.toFixed(d));
};
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const parse = d3.timeParse("%Y-%m-%d");
const fmtDate = d3.timeFormat("%b %-d, %Y");
const flip = (n) => n.split(", ").reverse().join(" ");
const ARM = new Set(["elbow_forearm", "shoulder_lat"]);
const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
let S = null, TEAM = null, PITCHER = null, REPLAYS = null, REPLAY = null, HERO = null;
const teamCache = {};
const C = () => ({
  s1: css("--series-1"), s1soft: css("--series-1-soft"), s2: css("--series-2"), crit: css("--critical"),
  critSoft: css("--critical-soft"), neu: css("--neutral"), neuSoft: css("--neutral-soft"), ink2: css("--ink-2"), ink3: css("--ink-3"),
  sheet: css("--sheet"),
});

/* ---------------- shared */
function theme() {
  const root = document.documentElement, btn = $("#theme");
  const isDark = () => (root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches);
  const label = () => btn.setAttribute("aria-label", isDark() ? "Blueprint (dark) is on. Switch to vellum (light)" : "Vellum (light) is on. Switch to blueprint (dark)");
  try { const t = localStorage.getItem("vs-theme"); if (t) root.dataset.theme = t; } catch { /* storage blocked */ }
  label();
  btn.addEventListener("click", () => {
    root.dataset.theme = isDark() ? "light" : "dark";
    try { localStorage.setItem("vs-theme", root.dataset.theme); } catch { /* storage blocked */ }
    label();
    drawAll();
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { label(); drawAll(); });
}
const tip = (() => {
  const t = document.createElement("div");
  t.className = "tip"; t.setAttribute("role", "tooltip"); document.body.appendChild(t);
  return {
    show(h, ev) {
      t.innerHTML = h; t.style.opacity = 1;
      t.style.left = Math.max(8, Math.min(ev.clientX + 14, innerWidth - t.offsetWidth - 8)) + "px";
      t.style.top = (ev.clientY + 14 + t.offsetHeight > innerHeight ? ev.clientY - t.offsetHeight - 10 : ev.clientY + 14) + "px";
    },
    hide() { t.style.opacity = 0; },
  };
})();
function describe(el, text) { el.setAttribute("role", "img"); el.setAttribute("aria-label", text); }
function frame(el, h, m) {
  el.innerHTML = "";
  const w = Math.max(220, el.clientWidth);
  const svg = d3.select(el).append("svg").attr("width", w).attr("height", h);
  return { svg, m, g: svg.append("g").attr("transform", `translate(${m.l},${m.t})`), w: w - m.l - m.r, h: h - m.t - m.b };
}
function hatch(f, id, color) {
  const p = f.svg.append("defs").append("pattern").attr("id", id).attr("patternUnits", "userSpaceOnUse")
    .attr("width", 6).attr("height", 6).attr("patternTransform", "rotate(45)");
  p.append("rect").attr("width", 6).attr("height", 6).attr("fill", color).attr("opacity", 0.07);
  p.append("line").attr("x1", 1).attr("x2", 1).attr("y1", 0).attr("y2", 6).attr("stroke", color).attr("stroke-width", 1.6).attr("opacity", 0.55);
  return `url(#${id})`;
}
function axes(f, x, y, o = {}) {
  f.g.append("g").attr("class", "grid").call(d3.axisLeft(y).ticks(o.yt || 5).tickSize(-f.w).tickFormat(""))
    .call((g) => g.select(".domain").remove());
  if (x.segs) seasonAxis(f, x);
  else {
    const xa = d3.axisBottom(x).ticks(o.xt || 6).tickSizeOuter(0);
    if (o.xf) xa.tickFormat(o.xf);
    if (o.xv) xa.tickValues(o.xv);
    f.g.append("g").attr("class", "axis").attr("transform", `translate(0,${f.h})`).call(xa);
  }
  const ya = d3.axisLeft(y).ticks(o.yt || 5).tickSize(0).tickPadding(8);
  if (o.yf) ya.tickFormat(o.yf);
  f.g.append("g").attr("class", "axis").call(ya).call((g) => g.select(".domain").remove());
  if (o.xl) f.g.append("text").attr("class", "axl").attr("x", f.w / 2).attr("y", f.h + 36).attr("text-anchor", "middle").text(o.xl);
  if (o.yl) f.g.append("text").attr("class", "axl").attr("transform", "rotate(-90)").attr("x", -f.h / 2).attr("y", 13 - f.m.l)
    .attr("text-anchor", "middle").text(o.yl);
}
function endLabels(f, items, gap = 15) {
  items.sort((a, b) => a.y - b.y);
  for (let i = 1; i < items.length; i++) items[i].y = Math.max(items[i].y, items[i - 1].y + gap);
  const over = items.length ? items[items.length - 1].y - (f.h - 2) : 0;
  if (over > 0) items.forEach((it) => { it.y -= over; });
  items.forEach((it) => f.g.append("text").attr("class", "lab").attr("x", f.w + 8).attr("y", it.y + 4).text(it.t));
}
// A hand-lettered note, the way a checker marks up a drawing: text at (tx, ty), one line per entry, and a curved
// arrow from the nearest edge of the text to the point (x, y).
function note(f, { x, y, tx, ty, lines, red = false, anchor = "start", bend = 0.25 }) {
  const cls = red ? " red" : "";
  const t = f.g.append("text").attr("class", "ann" + cls).attr("text-anchor", anchor);
  lines.forEach((ln, i) => t.append("tspan").attr("x", tx).attr("y", ty + i * 17).text(ln));
  const b0 = t.node().getBBox(), lo = 4 - f.m.l, hi = f.w + f.m.r - 4;   // keep the note on the sheet
  const shift = b0.x + b0.width > hi ? hi - (b0.x + b0.width) : b0.x < lo ? lo - b0.x : 0;
  if (shift) t.selectAll("tspan").attr("x", tx + shift);
  if (x == null) return;
  const b = t.node().getBBox(), pad = 4;
  const sx = Math.max(b.x - pad, Math.min(x, b.x + b.width + pad)), sy = Math.max(b.y - pad, Math.min(y, b.y + b.height + pad));
  if (Math.hypot(x - sx, y - sy) < 12) return;
  const mx = (sx + x) / 2 + (y - sy) * bend, my = (sy + y) / 2 - (x - sx) * bend;
  f.g.append("path").attr("class", "ann-line" + cls).attr("d", `M${sx},${sy} Q${mx},${my} ${x},${y}`);
  const a = Math.atan2(y - my, x - mx), L = 7;
  f.g.append("path").attr("class", "ann-line" + cls)
    .attr("d", `M${x - L * Math.cos(a - 0.5)},${y - L * Math.sin(a - 0.5)} L${x},${y} L${x - L * Math.cos(a + 0.5)},${y - L * Math.sin(a + 0.5)}`);
}

/* ---------------- detail A: a real outing replayed pitch by pitch, drawn as a control chart */
let monTimer = null, monObs = null;
function heroMonitor() {
  const wave = $("#mon-wave"), mon = $("#mon"), stamp = $("#mon-stamp");
  if (!wave || !HERO) return;
  clearTimeout(monTimer);
  monObs?.disconnect();
  const R = HERO, P = R.pitches.map(([i, v, x, c]) => ({ i, v, x, c })).filter((p) => p.v != null);
  const days = Math.round((parse(R.il_date) - parse(R.date)) / 864e5), when = days === 1 ? "the next day" : `${days} days later`;
  $("#mon-who").textContent = `${R.name} · ${R.team} · ${R.role === "SP" ? "starter" : "reliever"} · ${fmtDate(parse(R.date))}`;
  $("#rd-thr").textContent = `/ ${R.h.toFixed(1)}`;
  $("#stamp-sub").textContent = `Fastball ${R.alert_at} of ${R.n}`;
  mon.setAttribute("aria-label", `Replay of a real outing: ${R.name}, ${fmtDate(parse(R.date))}. The alert fired on fastball ${R.alert_at} of ${R.n}; he went on the injured list ${when}.`);
  const W = wave.clientWidth, H = wave.clientHeight, m = { t: 16, r: 12, b: 24, l: 36 }, gap = 22;
  const inner = H - m.t - m.b - gap, hTop = Math.round(inner * 0.63), y0 = hTop + gap, y1 = y0 + inner - hTop;
  wave.innerHTML = "";
  const svg = d3.select(wave).append("svg").attr("width", W).attr("height", H);
  const g = svg.append("g").attr("transform", `translate(0,${m.t})`);
  const x = d3.scaleLinear([0.5, R.n + 0.5], [m.l, W - m.r]);
  const vs = P.flatMap((p) => [p.v, p.x]);
  const y = d3.scaleLinear([Math.floor(d3.min(vs) - 0.3), Math.ceil(d3.max(vs) + 0.3)], [hTop, 0]);
  const yc = d3.scaleLinear([0, Math.max(R.h * 1.3, d3.max(P, (p) => p.c) * 1.05)], [y1, y0]);
  g.append("text").attr("class", "tk").attr("x", m.l - 7).attr("y", -5).attr("text-anchor", "end").text("mph");
  y.ticks(4).filter((t) => y(t) > 10).forEach((t) => g.append("text").attr("class", "tk").attr("x", m.l - 7).attr("y", y(t) + 3.5).attr("text-anchor", "end").text(t.toFixed(0)));
  g.append("line").attr("class", "sep").attr("x1", m.l).attr("x2", W - m.r).attr("y1", hTop + gap / 2).attr("y2", hTop + gap / 2);
  g.append("text").attr("class", "tk").attr("x", m.l + 2).attr("y", y0 + 9).text("alert total");
  g.append("line").attr("class", "base").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y1).attr("y2", y1);
  g.append("text").attr("class", "tk").attr("x", m.l - 7).attr("y", y1 + 3.5).attr("text-anchor", "end").text("0");
  g.append("line").attr("class", "lim").attr("x1", m.l).attr("x2", W - m.r).attr("y1", yc(R.h)).attr("y2", yc(R.h));
  g.append("text").attr("class", "tk lim-t").attr("x", W - m.r - 2).attr("y", yc(R.h) - 5).attr("text-anchor", "end").text(`limit ${R.h.toFixed(1)}`);
  const every = R.n > 30 ? 10 : R.n > 14 ? 5 : 2;
  P.filter((p) => p.i === 1 || p.i % every === 0).forEach((p) => g.append("text").attr("class", "tk").attr("x", x(p.i)).attr("y", y1 + 15)
    .attr("text-anchor", "middle").text(p.i));
  g.append("path").attr("class", "exp").attr("d", d3.line().x((p) => x(p.i)).y((p) => y(p.x))(P));
  g.append("text").attr("class", "tk exp-t").attr("x", x(1)).attr("y", y(P[0].x) - 7).text("expected");
  const ax = x(R.alert_at), al = g.append("line").attr("class", "al").attr("x1", ax).attr("x2", ax).attr("y1", -6).attr("y2", y1).style("display", "none");
  const area = g.append("path").attr("class", "cus-area"), cus = g.append("path").attr("class", "cus");
  const trace = g.append("path").attr("class", "trace"), dots = g.append("g");
  const pen = g.append("g").attr("class", "pen").style("display", "none");
  pen.append("line").attr("x1", -10).attr("x2", 10); pen.append("line").attr("y1", -10).attr("y2", 10); pen.append("circle").attr("r", 5);
  const line = d3.line().x((p) => x(p.i)).y((p) => y(p.v));
  const stepLine = d3.line().x((p) => x(p.i)).y((p) => yc(p.c)).curve(d3.curveStepAfter);
  const stepArea = d3.area().x((p) => x(p.i)).y0(y1).y1((p) => yc(p.c)).curve(d3.curveStepAfter);
  const placeStamp = () => {                      // beside the alert line, in the alert-total panel, so no fastball is covered
    const sw = stamp.offsetWidth, sh = stamp.offsetHeight, left = wave.offsetLeft + ax, room = mon.clientWidth - 10;
    stamp.style.left = `${left + 16 + sw <= room ? left + 16 : Math.max(10, left - 16 - sw)}px`;
    stamp.style.top = `${wave.offsetTop + m.t + (y0 + y1) / 2 - sh / 2 - 4}px`;
  };
  const render = (k) => {
    const seen = P.slice(0, k), p = seen[seen.length - 1], alarm = !!p && p.i >= R.alert_at;
    const cs = seen.length ? [{ i: 0.5, c: 0 }, ...seen] : [];
    trace.attr("d", seen.length > 1 ? line(seen) : null);
    dots.selectAll("circle").data(seen).join("circle").attr("class", "pt").attr("r", 2.7).attr("cx", (q) => x(q.i)).attr("cy", (q) => y(q.v));
    cus.attr("d", cs.length ? stepLine(cs) : null);
    area.attr("d", cs.length ? stepArea(cs) : null);
    if (alarm && !mon.classList.contains("alarm")) placeStamp();
    mon.classList.toggle("alarm", alarm);
    al.style("display", alarm ? null : "none");
    pen.style("display", p ? null : "none");
    if (!p) {
      ["#rd-velo", "#rd-exp"].forEach((s) => { $(s).textContent = "--.-"; });
      $("#rd-cus").textContent = "0.0"; $("#rd-bar").style.width = "0%";
      $("#mon-status").textContent = "A real outing, scored pitch by pitch.";
      return;
    }
    pen.attr("transform", `translate(${x(p.i)},${y(p.v)})`);
    $("#rd-velo").textContent = p.v.toFixed(1);
    $("#rd-exp").textContent = p.x.toFixed(1);
    $("#rd-cus").textContent = p.c.toFixed(1);
    $("#rd-bar").style.width = `${Math.min(100, (100 * p.c) / R.h)}%`;
    $("#mon-status").textContent = k >= P.length
      ? `Injured list ${when}: ${R.list_days}-day, ${R.injury.toLowerCase()}.`
      : alarm ? `Alert on fastball ${R.alert_at} of ${R.n}, with ${R.n - R.alert_at} still to come.`
        : `Fastball ${p.i} of ${R.n}: ${signed(p.v - p.x)} mph against expected.`;
  };
  mon.classList.remove("alarm");
  if (still) { render(P.length); return; }
  let k = 0, on = false, seen = false;
  const step = () => {
    if (!on) return;
    k += 1;
    render(k);
    if (k >= P.length) monTimer = setTimeout(() => { k = 0; render(0); monTimer = setTimeout(step, 900); }, 6500);
    else monTimer = setTimeout(step, 640);
  };
  const go = () => { if (!on && seen && !document.hidden) { on = true; clearTimeout(monTimer); monTimer = setTimeout(step, 500); } };
  const halt = () => { on = false; clearTimeout(monTimer); };
  render(0);
  monObs = new IntersectionObserver(([e]) => { seen = e.isIntersecting; if (seen) go(); else halt(); }, { threshold: 0.25 });
  monObs.observe(mon);
  document.onvisibilitychange = () => (document.hidden ? halt() : go());
}

/* ---------------- calibration */
function chartCalib() {
  const K = C(), D = S.calibration, f = frame($("#ch-calib"), 270, { t: 10, r: 14, b: 44, l: 54 });
  const x = d3.scaleLinear([-4, 4], [0, f.w]);
  const y = d3.scaleLinear([0, d3.max(D, (d) => Math.max(d.observed, d.normal)) * 1.1], [f.h, 0]);
  axes(f, x, y, { xl: "Score: this outing against his own normal (SD)", yl: "Density", yf: (d) => d.toFixed(1), yt: 4 });
  const bw = Math.max(1, x(0.25) - x(0) - 2);
  f.g.selectAll("rect").data(D).join("rect").attr("x", (d) => x(d.z - 0.125) + 1).attr("width", bw).attr("rx", 1.5)
    .attr("y", (d) => y(d.observed)).attr("height", (d) => f.h - y(d.observed)).attr("fill", K.s1).attr("opacity", 0.8)
    .on("mousemove", (ev, d) => tip.show(`Scores near <b>${minus(d.z.toFixed(2))}</b><br>observed ${d.observed.toFixed(3)} · normal ${d.normal.toFixed(3)}`, ev))
    .on("mouseleave", tip.hide);
  f.g.append("path").datum(D).attr("fill", "none").attr("stroke", K.ink2).attr("stroke-width", 2)
    .attr("d", d3.line().x((d) => x(d.z)).y((d) => y(d.normal)).curve(d3.curveBasis));
  const c = S.signals.calibration.velo;
  if (f.w >= 300) {
    const tail = D.filter((d) => d.z >= 2.2 && d.z <= 2.6).sort((a, b) => b.observed - a.observed)[0] || D[D.length - 1];
    note(f, { x: x(tail.z), y: y(tail.observed) - 3, tx: x(1.55), ty: y(y.domain()[1] * 0.62),
      lines: [`${pct(c.share_abs_gt2, 1)} beyond ±2;`, "a normal predicts 4.6%"] });
  }
  describe($("#ch-calib"), `Histogram of velocity scores on ${S.calibration_n.toLocaleString()} healthy outings. It follows the standard normal curve: standard deviation ${c.sd.toFixed(2)}, ${pct(c.share_abs_gt2, 1)} beyond plus or minus 2.`);
}

/* ---------------- explorer: one axis segment per season, off-seasons cut out */
async function loadTeam(t) {
  if (!teamCache[t]) teamCache[t] = await fetch(`data/teams/${t}.json`).then((r) => r.json());
  return teamCache[t];
}
function seasonsOf(P) {
  const D = P.outings.map((o) => parse(o[0]));
  return [...new Set(D.map((d) => d.getFullYear()))].sort().map((yr) => {
    const ds = D.filter((d) => d.getFullYear() === yr);
    let a = d3.min(ds), b = d3.max(ds);
    P.il.forEach(([s, days, , , back]) => {
      const sd = parse(s);
      if (sd.getFullYear() !== yr) return;
      const end = d3.min([back ? parse(back) : d3.timeDay.offset(sd, days), new Date(yr, 9, 1)]);
      if (sd < a) a = sd;
      if (end > b) b = end;
    });
    return [d3.timeDay.offset(a, -5), d3.timeDay.offset(b, 5)];
  });
}
function segScale(segs, width, gap = 22) {
  const total = d3.sum(segs, ([a, b]) => b - a), avail = width - gap * (segs.length - 1), dom = [], rng = [];
  let x0 = 0;
  segs.forEach(([a, b]) => { const w = (avail * (b - a)) / total; dom.push(a, b); rng.push(x0, x0 + w); x0 += w + gap; });
  const x = d3.scaleTime(dom, rng).clamp(true);
  x.segs = segs.map(([a, b], i) => ({ a, b, x0: rng[2 * i], x1: rng[2 * i + 1], year: a.getFullYear() }));
  return x;
}
function seasonAxis(f, x) {
  const ticks = x.segs.flatMap((s) => {
    let m = d3.timeMonth.range(d3.timeMonth.ceil(s.a), s.b);
    if ((s.x1 - s.x0) / Math.max(1, m.length) < 34) m = m.filter((d) => d.getMonth() % 2 === 1);
    return m;
  });
  f.g.append("g").attr("class", "axis").attr("transform", `translate(0,${f.h})`)
    .call(d3.axisBottom(x).tickValues(ticks).tickFormat(d3.timeFormat("%b")).tickSizeOuter(0))
    .call((g) => g.select(".domain").remove());
  x.segs.forEach((s, i) => {
    f.g.append("line").attr("class", "base").attr("x1", s.x0).attr("x2", s.x1).attr("y1", f.h).attr("y2", f.h);
    if (s.x1 - s.x0 > 28) f.g.append("text").attr("class", "yr").attr("x", (s.x0 + s.x1) / 2).attr("y", f.h + 33)
      .attr("text-anchor", "middle").text(s.year);
    if (i) {
      const gx = (x.segs[i - 1].x1 + s.x0) / 2;
      f.g.append("line").attr("class", "brk").attr("x1", gx).attr("x2", gx).attr("y1", 0).attr("y2", f.h);
    }
  });
}
function series(el, P, segs, col, lvl, sdCol, fmt, h, label) {
  const K = C();
  const O = P.outings.map((o) => ({ d: parse(o[0]), v: o[col], l: lvl != null ? o[lvl] : null, sd: sdCol != null ? o[sdCol] : null,
    alert: o[10], z: o[4], p: o[9] })).filter((o) => o.v != null);
  const f = frame(el, h, { t: 8, r: 12, b: 42, l: 48 });
  if (!O.length) { describe(el, `${label}: no readings.`); return; }
  const x = segScale(segs, f.w);
  const lo = d3.min(O, (o) => Math.min(o.v, o.l != null && o.sd != null ? o.l - 2 * o.sd : o.v));
  const hi = d3.max(O, (o) => Math.max(o.v, o.l != null && o.sd != null ? o.l + 2 * o.sd : o.v));
  const pad = (hi - lo) * 0.08 || 1, y = d3.scaleLinear([lo - pad, hi + pad], [f.h, 0]);
  axes(f, x, y, { yt: 4, yf: fmt });
  const armFill = hatch(f, `${el.id}-hatch`, K.crit);
  P.il.forEach(([date, days, inj, body, back]) => {
    const s = parse(date), e = back ? parse(back) : d3.timeDay.offset(s, Math.max(days, 10));
    const x0 = x(s), x1 = x(e);
    if (x1 - x0 < 1) return;
    f.g.append("rect").attr("x", x0).attr("width", x1 - x0).attr("y", 0).attr("height", f.h)
      .attr("fill", ARM.has(body) ? armFill : K.neuSoft)
      .on("mousemove", (ev) => tip.show(`<b>Injured list</b> from ${fmtDate(s)}<br>${esc(inj) || "no detail given"}${back ? `<br>pitched again ${fmtDate(e)}` : ""}`, ev))
      .on("mouseleave", tip.hide);
  });
  x.segs.forEach((sg) => {
    const L = O.filter((o) => o.l != null && o.d >= sg.a && o.d <= sg.b);
    if (sdCol != null) f.g.append("path").datum(L.filter((o) => o.sd != null)).attr("fill", K.s1soft)
      .attr("d", d3.area().x((o) => x(o.d)).y0((o) => y(o.l - 2 * o.sd)).y1((o) => y(o.l + 2 * o.sd)));
    f.g.append("path").datum(L).attr("fill", "none").attr("stroke", K.s1).attr("stroke-width", 2)
      .attr("d", d3.line().x((o) => x(o.d)).y((o) => y(o.l)));
  });
  const low = (o) => col === 1 && o.z != null && o.z <= -2;
  f.g.selectAll(".pt").data(O).join("circle").attr("cx", (o) => x(o.d)).attr("cy", (o) => y(o.v)).attr("r", (o) => (low(o) ? 3.6 : 2.6))
    .attr("fill", (o) => (low(o) ? K.crit : K.ink3)).attr("opacity", (o) => (low(o) ? 1 : 0.75))
    .on("mousemove", (ev, o) => tip.show(`<b>${fmtDate(o.d)}</b><br>${fmt(o.v)}${col === 1 && o.z != null ? ` · score ${signed(o.z)}` : ""}<br>${o.p} pitches${o.alert ? "<br>in-game alert fired" : ""}`, ev))
    .on("mouseleave", tip.hide);
  if (col === 1) f.g.selectAll(".al").data(O.filter((o) => o.alert)).join("circle").attr("cx", (o) => x(o.d)).attr("cy", (o) => y(o.v))
    .attr("r", 7).attr("fill", "none").attr("stroke", K.crit).attr("stroke-width", 2).attr("pointer-events", "none");
  const last = O[O.length - 1];
  describe(el, `${label} for ${flip(P.name)}, ${O.length} outings over ${x.segs.length} season${x.segs.length > 1 ? "s" : ""}; latest outing ${fmt(last.v)}.`);
}
function drawPitcher() {
  if (!PITCHER) return;
  const P = PITCHER, last = P.outings[P.outings.length - 1], segs = seasonsOf(P);
  const alerts = P.outings.filter((o) => o[10]).length;
  $("#x-title").textContent = `${flip(P.name)} · ${P.role === "SP" ? "starter" : "reliever"}`;
  $("#x-sub").textContent = `Fastball velocity, mph. ${P.outings.length} outings; latest estimate ${last[2] != null ? last[2].toFixed(1) : "–"} mph; in-game alert fired ${alerts} time${alerts === 1 ? "" : "s"}.`;
  series($("#ch-velo"), P, segs, 1, 2, 3, (v) => v.toFixed(1), 260, "Fastball velocity");
  series($("#ch-spin"), P, segs, 5, 6, null, (v) => d3.format(",")(Math.round(v)), 180, "Spin rate");
  series($("#ch-arm"), P, segs, 7, 8, null, (v) => v.toFixed(0) + "°", 180, "Arm angle");
  $("#x-il").innerHTML = P.il.length
    ? "<b>Injured list:</b> " + P.il.map(([d, days, inj, , back]) => `${fmtDate(parse(d))}, ${days}-day, ${esc(inj) || "no detail given"}${back ? ` (pitched again ${fmtDate(parse(back))})` : ""}`).join(" · ")
    : "No injured-list placements in the data.";
}
function selectPitcher(team, k) {
  TEAM = team;
  $("#x-pitcher").innerHTML = TEAM.map((p, i) => `<option value="${i}">${esc(p.name)}</option>`).join("");
  $("#x-pitcher").value = k;
  PITCHER = TEAM[k];
  drawPitcher();
}
async function initExplorer() {
  const ts = $("#x-team"), ps = $("#x-pitcher");
  ts.innerHTML = Object.keys(S.teams).sort().map((t) => `<option ${t === "DET" ? "selected" : ""}>${t}</option>`).join("");
  ts.onchange = async () => selectPitcher(await loadTeam(ts.value), 0);
  ps.onchange = () => { PITCHER = TEAM[+ps.value]; drawPitcher(); };
  const det = await loadTeam("DET");
  selectPitcher(det, Math.max(0, det.findIndex((p) => p.name.startsWith("Flaherty"))));
}

/* ---------------- event study and the early-warning table */
function chartEvent() {
  const K = C(), E = S.event_study, f = frame($("#ch-event"), 280, { t: 10, r: 70, b: 44, l: 46 });
  const x = d3.scaleLinear([-6.4, -0.6], [0, f.w]);
  const ext = d3.extent(E.flatMap((e) => [e.velo - 1.96 * e.velo_se, e.velo + 1.96 * e.velo_se]));
  const y = d3.scaleLinear([Math.min(ext[0], -0.5), Math.max(ext[1], 0.3)], [f.h, 0]).nice();
  axes(f, x, y, { xv: [-6, -5, -4, -3, -2, -1], xf: (d) => (d === -1 ? "final" : `${-d}`), xl: "Outings before the injured list",
    yf: (d) => minus(d.toFixed(1)), yt: 5 });
  f.g.append("line").attr("x1", 0).attr("x2", f.w).attr("y1", y(0)).attr("y2", y(0)).attr("stroke", K.ink3).attr("stroke-width", 1);
  const labs = [];
  [["arm", K.crit, "Arm", 0.08], ["non_arm", K.neu, "Non-arm", -0.08]].forEach(([g, c, lab, off]) => {
    const pts = E.filter((e) => e.grp === g).sort((a, b) => a.k - b.k);
    f.g.append("path").datum(pts).attr("fill", "none").attr("stroke", c).attr("stroke-width", 2)
      .attr("d", d3.line().x((e) => x(e.k + off)).y((e) => y(e.velo)));
    f.g.selectAll(null).data(pts).join("line").attr("x1", (e) => x(e.k + off)).attr("x2", (e) => x(e.k + off))
      .attr("y1", (e) => y(e.velo - 1.96 * e.velo_se)).attr("y2", (e) => y(e.velo + 1.96 * e.velo_se)).attr("stroke", c).attr("stroke-width", 2)
      .attr("stroke-linecap", "round");
    f.g.selectAll(null).data(pts).join("circle").attr("cx", (e) => x(e.k + off)).attr("cy", (e) => y(e.velo)).attr("r", 5).attr("fill", c)
      .attr("stroke", K.sheet).attr("stroke-width", 2)
      .on("mousemove", (ev, e) => tip.show(`<b>${lab} injuries</b>, ${e.k === -1 ? "final outing" : -e.k + " outings before"}<br>velocity ${signed(e.velo, 2)} SD · spin ${signed(e.spin, 2)} SD<br>${e.n} stints`, ev))
      .on("mouseleave", tip.hide);
    labs.push({ y: y(pts[pts.length - 1].velo), t: lab });
  });
  endLabels(f, labs);
  const fin = E.find((e) => e.grp === "arm" && e.k === -1), room = x(-1) - x(-6.1) - 24;   // only where it fits
  if (fin && room > 185) note(f, { x: x(-1 + 0.08) - 7, y: y(fin.velo) + 2, tx: x(-6.1), ty: y(fin.velo) + 4, red: true, bend: -0.12,
    lines: room > 275 ? ["the drop arrives on the final outing"] : ["drop on the final outing"] });
  const T = S.event_study_events.tests;
  describe($("#ch-event"), `Mean velocity score in the six outings before an injured-list stint. Arm injuries average ${signed(T.pre.arm, 2)} before the final outing and ${signed(T.final.arm, 2)} on it; non-arm injuries ${signed(T.pre.non_arm, 2)} and ${signed(T.final.non_arm, 2)}.`);
}
function tableWarning() {
  const names = { ensemble: "Ensemble (boosting + logistic)", velo_drop_rule: "Rule: fastball 1.5 mph under season average",
    velocity_signal_only: "Velocity score only", workload_only: "Workload only" };
  const R = S.warning.results.filter((r) => names[r.model]);
  $("#t-warning").insertAdjacentHTML("beforeend", `<thead><tr><th>Tested on ${S.warning.test.season}</th><th class="r">AUC</th><th class="r">Lift, top 2%</th><th class="r">Caught early</th></tr></thead><tbody>${R.map((r) =>
    `<tr><td>${names[r.model]}</td><td class="r num">${r.auc.toFixed(2)}</td><td class="r num">${r.lift_at_flags.toFixed(1)}×</td><td class="r num">${pct(r.caught_early)}</td></tr>`).join("")}</tbody>`);
}

/* ---------------- in the game */
function chartIngame() {
  const K = C(), f = frame($("#ch-ingame"), 280, { t: 10, r: 78, b: 44, l: 58 });
  const x = d3.scaleLinear([0, 0.11], [0, f.w]);
  const top = d3.max(["relievers", "starters"].flatMap((k) => S.ingame[k].points.map((p) => p.caught || 0)));
  const y = d3.scaleLinear([0, Math.max(0.3, top * 1.15)], [f.h, 0]).nice();
  axes(f, x, y, { xf: (d) => pct(d), yf: (d) => pct(d), xl: "Alerts on ordinary outings", yl: "Injury outings caught", yt: 5 });
  f.g.append("line").attr("x1", x(0)).attr("y1", y(0)).attr("x2", x(0.11)).attr("y2", y(0.11)).attr("stroke", K.ink3)
    .attr("stroke-dasharray", "5 4").attr("stroke-width", 1.5);
  f.g.append("line").attr("class", "cl").attr("x1", x(0.05)).attr("x2", x(0.05)).attr("y1", 0).attr("y2", f.h);
  note(f, { tx: x(0.05) + 6, ty: 12, lines: f.w >= 330 ? ["the setting used on this page"] : ["setting used here"] });
  const labs = [];
  [["relievers", K.s1, "Relievers"], ["starters", K.s2, "Starters"]].forEach(([k, c, lab]) => {
    const pts = S.ingame[k].points.map((p) => [p.false_alert_rate, p.caught || 0, p.median_fastballs_left, p.final_outings]);
    f.g.append("path").datum(pts).attr("fill", "none").attr("stroke", c).attr("stroke-width", 2).attr("d", d3.line().x((p) => x(p[0])).y((p) => y(p[1])));
    f.g.selectAll(null).data(pts).join("circle").attr("cx", (p) => x(p[0])).attr("cy", (p) => y(p[1])).attr("r", 5).attr("fill", c)
      .attr("stroke", K.sheet).attr("stroke-width", 2)
      .on("mousemove", (ev, p) => tip.show(`<b>${lab}</b>: ${pct(p[1])} of ${p[3]} caught<br>at ${pct(p[0])} false alerts<br>median ${p[2] ?? "–"} fastballs left`, ev))
      .on("mouseleave", tip.hide);
    labs.push({ y: y(pts[pts.length - 1][1]), t: lab });
  });
  endLabels(f, labs);
  const B = S.ingame.breakdown;
  describe($("#ch-ingame"), `Share of arm-injury outings the alert caught at false-alert rates from 1% to 10%, for relievers and starters. At 5%, ${pct(B.all.caught)} of all ${B.all.n} were caught.`);
}
async function initReplay() {
  REPLAYS = await fetch("data/replays.json").then((r) => r.json());
  if (!REPLAYS.length) { $("#replay").hidden = true; $("#mon").hidden = true; return; }
  const sel = $("#r-pick");
  sel.innerHTML = REPLAYS.map((r, i) => `<option value="${i}">${esc(r.name)} (${r.team}), ${fmtDate(parse(r.date))}</option>`).join("");
  const pick = Math.max(0, REPLAYS.findIndex((r) => r.team === "DET" && r.n - r.alert_at >= 5));
  sel.value = pick;
  REPLAY = HERO = REPLAYS[pick];
  sel.onchange = () => { REPLAY = REPLAYS[+sel.value]; drawReplay(true); };
  $("#r-play").onclick = () => drawReplay(true);
  drawReplay(false);
  heroMonitor();
}
function drawReplay(animate) {
  if (!REPLAY) return;
  const K = C(), R = REPLAY, P = R.pitches.map(([i, v, x, c]) => ({ i, v, x, c }));
  const f = frame($("#ch-replay"), 350, { t: 14, r: 16, b: 44, l: 56 });
  const hTop = Math.round(f.h * 0.58), gap = 30, hBot = f.h - hTop - gap;
  const x = d3.scaleLinear([0.5, R.n + 0.5], [0, f.w]);
  const vs = P.flatMap((p) => [p.v, p.x]).filter((v) => v != null);
  const yv = d3.scaleLinear([d3.min(vs) - 0.5, d3.max(vs) + 0.5], [hTop, 0]).nice();
  const yc = d3.scaleLinear([0, Math.max(R.h * 1.3, d3.max(P, (p) => p.c) * 1.08)], [hBot, 0]).nice();
  const top = f.g.append("g"), bot = f.g.append("g").attr("transform", `translate(0,${hTop + gap})`);
  [[top, yv, 4, hTop, "mph"], [bot, yc, 3, hBot, "alert total"]].forEach(([g, y, n, h, lab]) => {
    g.append("g").attr("class", "grid").call(d3.axisLeft(y).ticks(n).tickSize(-f.w).tickFormat("")).call((s) => s.select(".domain").remove());
    g.append("g").attr("class", "axis").call(d3.axisLeft(y).ticks(n).tickSize(0).tickPadding(8)).call((s) => s.select(".domain").remove());
    g.append("text").attr("class", "axl").attr("transform", "rotate(-90)").attr("x", -h / 2).attr("y", -42).attr("text-anchor", "middle").text(lab);
  });
  bot.append("g").attr("class", "axis").attr("transform", `translate(0,${hBot})`)
    .call(d3.axisBottom(x).ticks(Math.min(R.n, f.w < 400 ? 6 : 12)).tickFormat(d3.format("d")).tickSizeOuter(0));
  bot.append("text").attr("class", "axl").attr("x", f.w / 2).attr("y", hBot + 36).attr("text-anchor", "middle").text("Fastball number in the outing");
  bot.append("line").attr("x1", 0).attr("x2", f.w).attr("y1", yc(R.h)).attr("y2", yc(R.h)).attr("stroke", K.crit)
    .attr("stroke-dasharray", "5 4").attr("stroke-width", 1.5);
  bot.append("text").attr("class", "lab halo").attr("x", 4).attr("y", yc(R.h) - 6).text("alert threshold");
  // everything below is revealed left to right on replay
  const cid = "clip-replay";
  const clip = f.svg.append("defs").append("clipPath").attr("id", cid).append("rect").attr("x", -8).attr("y", -14)
    .attr("height", f.h + 28).attr("width", f.w + 16);
  const layer = f.g.append("g").attr("clip-path", `url(#${cid})`);
  const lt = layer.append("g"), lb = layer.append("g").attr("transform", `translate(0,${hTop + gap})`);
  lt.append("path").datum(P).attr("fill", "none").attr("stroke", K.ink3).attr("stroke-width", 2).attr("stroke-dasharray", "6 4")
    .attr("d", d3.line().x((p) => x(p.i)).y((p) => yv(p.x)));
  lt.append("text").attr("class", "lab halo").attr("x", x(1)).attr("y", yv(P[0].x) - 9).text("expected");
  lt.selectAll("circle").data(P.filter((p) => p.v != null)).join("circle").attr("cx", (p) => x(p.i)).attr("cy", (p) => yv(p.v)).attr("r", 5)
    .attr("fill", (p) => (p.v < p.x ? K.crit : K.neu)).attr("stroke", K.sheet).attr("stroke-width", 1.5)
    .on("mousemove", (ev, p) => tip.show(`<b>Fastball ${p.i}</b>: ${p.v.toFixed(1)} mph<br>expected ${p.x.toFixed(1)} · alert total ${p.c.toFixed(1)}`, ev))
    .on("mouseleave", tip.hide);
  lb.append("path").datum(P).attr("fill", K.critSoft)
    .attr("d", d3.area().x((p) => x(p.i)).y0(hBot).y1((p) => yc(p.c)).curve(d3.curveStepAfter));
  lb.append("path").datum(P).attr("fill", "none").attr("stroke", K.crit).attr("stroke-width", 2)
    .attr("d", d3.line().x((p) => x(p.i)).y((p) => yc(p.c)).curve(d3.curveStepAfter));
  const ax = x(R.alert_at), left = R.n - R.alert_at;
  layer.append("line").attr("x1", ax).attr("x2", ax).attr("y1", -4).attr("y2", f.h).attr("stroke", K.crit).attr("stroke-width", 2);
  const al = layer.append("text").attr("class", "lab halo").attr("y", 6);
  const place = (txt) => {                        // right of the line if it fits, else left of it
    al.text(txt).attr("text-anchor", "start").attr("x", ax + 6);
    if (ax + 6 + al.node().getComputedTextLength() <= f.w + 12) return true;
    al.attr("text-anchor", "end").attr("x", ax - 6);
    return ax - 6 - al.node().getComputedTextLength() >= -f.m.l + 4;
  };
  if (!place(`Alert: fastball ${R.alert_at}, ${left} still to come`)) place(`Alert: ${left} to go`);
  const days = Math.round((parse(R.il_date) - parse(R.date)) / 864e5);
  $("#r-cap").innerHTML = `<b>${esc(R.name)}</b> (${R.team}, ${R.role === "SP" ? "starter" : "reliever"}), ${fmtDate(parse(R.date))}. He came in expected to sit at ${R.level.toFixed(1)} mph. The alert fired on fastball ${R.alert_at} of ${R.n}, with ${left} still to come. Injured list effective ${days === 1 ? "the next day" : `${days} days later`}: ${R.list_days}-day, ${esc(R.injury.toLowerCase())}.`;
  describe($("#ch-replay"), `${R.name}, ${fmtDate(parse(R.date))}: each fastball against his expected velocity, and the alert's running total, which crosses the threshold on fastball ${R.alert_at} of ${R.n}.`);
  if (animate && !still) {
    clip.attr("width", 0).transition().duration(Math.min(5200, 220 + R.n * 150)).ease(d3.easeLinear).attr("width", f.w + 16);
  }
}

/* ---------------- rest: every specification on one axis */
function chartRest() {
  const K = C(), R = S.rest, rb = R.robustness, ci = (b, se) => [b - 1.96 * se, b + 1.96 * se];
  const rows = [
    { lab: "Natural experiment (main estimate)", b: R.velo.iv_per_extra_day, ci: R.velo.iv_ci, kind: "main" },
    { lab: "Same, turns of 3 to 8 days' rest only", b: rb.rest_3_to_8.velo.iv_per_extra_day, ci: rb.rest_3_to_8.velo.iv_ci },
    { lab: "Same, off days counted over the whole gap", b: rb.whole_gap_instrument.velo.iv_per_extra_day, ci: rb.whole_gap_instrument.velo.iv_ci },
    { lab: "Naive comparison (confounded)", b: R.velo.ols_per_extra_day, ci: ci(R.velo.ols_per_extra_day, R.velo.ols_se), kind: "naive" },
  ];
  const rowH = 52, f = frame($("#ch-rest"), rows.length * rowH + 52, { t: 4, r: 14, b: 46, l: 14 });
  const lim = Math.max(0.15, d3.max(rows, (r) => Math.max(-r.ci[0], r.ci[1])) * 1.2);
  const x = d3.scaleLinear([-lim, lim], [0, f.w]).nice();
  f.g.append("g").attr("class", "grid").attr("transform", `translate(0,${f.h})`)
    .call(d3.axisBottom(x).ticks(5).tickSize(-f.h).tickFormat("")).call((g) => g.select(".domain").remove());
  f.g.append("g").attr("class", "axis").attr("transform", `translate(0,${f.h})`).call(d3.axisBottom(x).ticks(5).tickFormat((d) => signed(d, 2)).tickSizeOuter(0));
  f.g.append("text").attr("class", "axl").attr("x", f.w / 2).attr("y", f.h + 38).attr("text-anchor", "middle").text("mph of next-start velocity per extra day of rest");
  f.g.append("line").attr("x1", x(0)).attr("x2", x(0)).attr("y1", 0).attr("y2", f.h).attr("stroke", K.ink3).attr("stroke-width", 1.5);
  note(f, { x: x(0) + 3, y: rowH + 31, tx: x(0) + 40, ty: rowH + 36, lines: ["no effect"], bend: -0.25 });
  rows.forEach((r, i) => {
    const y0 = i * rowH, c = r.kind === "main" ? K.s1 : r.kind === "naive" ? K.neu : K.ink2;
    f.g.append("text").attr("class", r.kind === "main" ? "lab halo" : "axl halo").attr("x", 0).attr("y", y0 + 16).text(r.lab);
    f.g.append("line").attr("x1", x(r.ci[0])).attr("x2", x(r.ci[1])).attr("y1", y0 + 33).attr("y2", y0 + 33).attr("stroke", c)
      .attr("stroke-width", r.kind === "main" ? 3 : 2).attr("stroke-linecap", "round");
    f.g.append("circle").attr("cx", x(r.b)).attr("cy", y0 + 33).attr("r", r.kind === "main" ? 6.5 : 5)
      .attr("fill", r.kind === "naive" ? K.sheet : c).attr("stroke", c).attr("stroke-width", r.kind === "naive" ? 2 : 0)
      .on("mousemove", (ev) => tip.show(`<b>${r.lab}</b><br>${signed(r.b, 3)} mph per day<br>95% CI ${signed(r.ci[0], 3)} to ${signed(r.ci[1], 3)}`, ev))
      .on("mouseleave", tip.hide);
  });
  describe($("#ch-rest"), `Four estimates of what an extra day of rest does to next-start velocity, each with a 95% interval. The main estimate is ${signed(R.velo.iv_per_extra_day, 3)} mph per day; all four sit near zero.`);
}

/* ---------------- Tigers board */
function spark(el, outs, il, year) {
  const K = C(), O = outs.filter((o) => o[2] != null).map((o) => ({ d: parse(o[0]), l: o[2] }));
  if (O.length < 2) return;
  const W = 140, H = 30, svg = d3.select(el).append("svg").attr("width", W).attr("height", H).attr("aria-hidden", "true");
  const x = d3.scaleTime([new Date(year, 2, 20), new Date(year, 9, 1)], [2, W - 6]).clamp(true);
  const [a, b] = d3.extent(O, (o) => o.l), mid = (a + b) / 2, half = Math.max(1, (b - a) / 2 + 0.2);
  const y = d3.scaleLinear([mid - half, mid + half], [H - 4, 4]);
  const armFill = hatch({ svg }, `${el.id}-hatch`, K.crit);
  il.filter((i) => i[0].startsWith(String(year))).forEach(([s, days, , body, back]) => {
    const x0 = x(parse(s)), x1 = x(back ? parse(back) : d3.timeDay.offset(parse(s), days));
    if (x1 - x0 >= 1) svg.append("rect").attr("x", x0).attr("width", x1 - x0).attr("y", 0).attr("height", H)
      .attr("fill", ARM.has(body) ? armFill : K.neuSoft);
  });
  svg.append("path").datum(O).attr("fill", "none").attr("stroke", K.s1).attr("stroke-width", 1.6)
    .attr("d", d3.line().x((o) => x(o.d)).y((o) => y(o.l)));
  const e = O[O.length - 1];
  svg.append("circle").attr("cx", x(e.d)).attr("cy", y(e.l)).attr("r", 2.6).attr("fill", K.s1);
}
async function tableTigers() {
  const T = await loadTeam("DET");
  const year = +d3.max(T.flatMap((p) => p.outings.map((o) => o[0].slice(0, 4))));
  const rows = T.map((p, idx) => {
    const season = p.outings.filter((o) => o[0].startsWith(String(year)));
    const O = season.filter((o) => o[2] != null), last = O[O.length - 1], ten = O[Math.max(0, O.length - 11)];
    return { idx, p, name: flip(p.name), role: p.role, n: season.length, season, level: last ? last[2] : null,
      change: O.length >= 5 ? last[2] - ten[2] : null, alerts: season.filter((o) => o[10]).length,
      ilArm: p.il.filter((i) => ARM.has(i[3])).length, il: p.il.length };
  }).filter((r) => r.n > 0).sort((a, b) => (a.role === b.role ? b.n - a.n : a.role === "SP" ? -1 : 1));
  $("#t-tigers").innerHTML = `<caption class="sr">Detroit pitchers by the monitor</caption><thead><tr><th>Pitcher</th><th>Role</th><th>${year} fastball, filtered</th><th class="r">Outings</th><th class="r">Now, mph</th><th class="r">Change, last 10</th><th class="r">In-game alerts</th><th class="r">IL stints (arm)</th></tr></thead><tbody>${rows.map((r, k) =>
    `<tr><td class="nm"><a href="#explorer" data-k="${r.idx}">${esc(r.name)}</a></td><td>${r.role}</td><td class="spark" id="sp-${k}"></td><td class="r num">${r.n}</td>
    <td class="r num">${r.level != null ? r.level.toFixed(1) : "–"}</td><td class="r num ${r.change != null && r.change <= -0.8 ? "flag" : ""}">${r.change != null ? signed(r.change) : "–"}</td>
    <td class="r num">${r.alerts}</td><td class="r num">${r.il} (${r.ilArm})</td></tr>`).join("")}</tbody>`;
  rows.forEach((r, k) => spark($(`#sp-${k}`), r.season, r.p.il, year));
  document.querySelectorAll("#t-tigers a[data-k]").forEach((a) => a.addEventListener("click", () => {
    $("#x-team").value = "DET";
    selectPitcher(T, +a.dataset.k);
  }));
  const drop = rows.filter((r) => r.change != null).sort((a, b) => a.change - b.change)[0];
  const most = [...rows].sort((a, b) => b.alerts - a.alerts)[0];
  const lead = drop.name === most.name
    ? `${esc(drop.name)} stands out twice: the biggest fall over his last ten outings (${signed(drop.change)} mph) and the most in-game alerts in ${year} (${most.alerts}). That isn't a diagnosis, but it's where I'd look first.`
    : `Biggest fall over the last ten outings: ${esc(drop.name)} (${signed(drop.change)} mph). Most in-game alerts in ${year}: ${esc(most.name)} (${most.alerts}). Neither is a diagnosis; both are where I'd look first.`;
  $("#t-note").innerHTML = `${lead} ▼ marks a fall of 0.8 mph or more. In the trend column, hatched red is an arm injured-list stint and gray is any other.`;
}

function drawAll() {
  if (!S) return;
  [chartCalib, chartEvent, chartIngame, chartRest, drawPitcher, () => drawReplay(false)].forEach((fn) => {
    try { fn(); } catch (e) { console.error(e); }
  });
  if (document.querySelector("#t-tigers tbody")) tableTigers();
}
theme();
document.fonts?.ready.then(() => { if (S) drawAll(); });
fetch("data/summary.json").then((r) => r.json()).then(async (d) => {
  S = d;
  tableWarning();
  [chartCalib, chartEvent, chartIngame, chartRest].forEach((fn) => { try { fn(); } catch (e) { console.error(e); } });
  await Promise.all([initExplorer(), initReplay(), tableTigers()]);
});
let rt, lastW = innerWidth;
addEventListener("resize", () => {
  if (innerWidth === lastW) return;               // ignore mobile toolbar height changes
  lastW = innerWidth; clearTimeout(rt); rt = setTimeout(() => { drawAll(); heroMonitor(); }, 150);
});
