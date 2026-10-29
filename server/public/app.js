// The page's script: the class picker, the best-maps chart, the latest-runs bar and a little motion.
// (Its own file so the site can tell browsers to run no other scripts: see _headers.)

// The site's old address sends people to the new one, keeping their choices (level, class...). Only the page is
// moved: the old /api/ address keeps answering for overlays from before the move. Only the exact old host, so
// preview deployments and a local test server are left alone.
const OLD_HOST = "maple-classic-exp-overlay.pages.dev", NEW_SITE = "https://mcwo.app";
function movedTo(loc) {
  return loc.hostname === OLD_HOST ? NEW_SITE + loc.pathname + loc.search + loc.hash : null;
}
if (typeof location !== "undefined" && movedTo(location)) {
  location.replace(movedTo(location));
  throw new Error("moved to " + NEW_SITE);          // (stops this script: the page is being replaced)
}
const $ = (id) => document.getElementById(id);
const fmt = (n) => (n === null || n === undefined) ? "?" : Math.round(n).toLocaleString("en-US");
const short = (n) => n >= 1e6 ? (n / 1e6).toFixed(2) + "M" : n >= 1e5 ? Math.round(n / 1e3) + "k"
  : n >= 1e4 ? (n / 1e3).toFixed(1) + "k" : fmt(n);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const partyName = (p) => p === "solo" ? "solo" : "in a party";
const soloOrParty = (p) => p === "solo" || p === "1" ? "solo"          // also party sizes, from the
  : p === "party" || /^[2-6]$/.test(p ?? "") ? "party" : null;         // overlay's link or older visits
const plural = (n, word) => `${fmt(n)} ${word}${n === 1 ? "" : "s"}`;
const mostPlayers = (list) => [...list].sort((a, b) => b.players - a.players)[0];
const JOIN = `<a class="more" href="#overlay">Get the overlay and share your farming</a>`;
// Hours of farming: "45 min", "2.5 hours", "1,204 hours"
const hours = (h) => {
  if (h < 1) return `${Math.max(1, Math.round(h * 60))} min`;
  const n = h < 10 ? Math.round(h * 10) / 10 : Math.round(h);
  return `${n.toLocaleString("en-US")} hour${n === 1 ? "" : "s"}`;
};
const SECOND_JOB = 30;   // the level paths start at (2nd job); before it, a class is one group
let classes = [];        // from the server: Warrior, Magician, Archer, Thief, each with its paths
let wantPath = null;     // the path last picked, kept while the level is under SECOND_JOB

function saved() {
  try { return JSON.parse(localStorage.getItem("ask") || "null") || {}; } catch { return {}; }
}

/** What the overlay's "Best maps" link asks for, e.g. ?level=32&party=1&job=Bandit (party: its size). */
function fromLink(q = new URLSearchParams(location.search)) {
  const job = q.get("job"), out = {};
  const level = parseInt(q.get("level"), 10);
  if (level >= 1 && level <= 250) out.level = level;
  if (soloOrParty(q.get("party"))) out.party = soloOrParty(q.get("party"));
  for (const c of classes) {
    if (c.name === job || (job === "Bowman" && c.name === "Archer")) out.job = c.name;
    for (const p of c.paths) {
      const at = p.steps.indexOf(job);
      if (at >= 0) out.job = c.name;
      if (at > 0) out.path = p.name;      // a 2nd to 4th job: that path (a 1st job is on all of them)
    }
  }
  const asked = q.get("path");
  if (out.job && asked && classes.find((c) => c.name === out.job)?.paths.some((p) => p.name === asked)) out.path = asked;
  if (Object.hasOwn(SORTS, q.get("sort"))) out.sort = q.get("sort");
  if (q.get("hide") === "early") out.showEarly = false;      // (links from when early data showed by default)
  if (q.get("early") === "1") out.showEarly = true;
  if (q.get("view") === "ladder") out.view = "ladder";
  if (q.get("new") === "1") out.withNew = true;
  return out;
}

function chosenClass() {
  const picked = document.querySelector('input[name="job"]:checked');
  return classes.find((c) => c.name === picked?.value);
}

const firstJob = () => parseInt($("level").value, 10) < SECOND_JOB;

/** Picks a class (the one asked for, or the one most players are on) and fills in its paths. */
function pickClass(name) {
  const c = classes.find((c) => c.name === name) || mostPlayers(classes);
  document.querySelector(`input[name="job"][value="${esc(c.name)}"]`).checked = true;
  fillPaths();
}

/** The class's paths, or, under Lv 30, its 1st job with Path greyed out. */
function fillPaths() {
  const c = chosenClass();
  if (!c) return;
  const off = firstJob();
  $("path").disabled = off;
  $("path-field").classList.toggle("off", off);
  if (off) {
    $("path").innerHTML = `<option>${esc(c.paths[0].steps[0])} (1st job)</option>`;
    $("path-hint").textContent = `Paths start at Lv ${SECOND_JOB}, with your 2nd job.`;
    return;
  }
  $("path").innerHTML = c.paths.map((p) => `<option value="${esc(p.name)}">${esc(p.name)}${p.players
    ? ` · ${plural(p.players, "player")}` : ""}</option>`).join("");
  $("path").value = c.paths.some((p) => p.name === wantPath) ? wantPath : mostPlayers(c.paths).name;
  showSteps();
}

function showSteps() {         // e.g. "Rogue → Bandit → Chief Bandit → Shadower"
  const p = chosenClass()?.paths.find((p) => p.name === $("path").value);
  $("path-hint").textContent = p ? p.steps.map((job) => job.replace(/ \(.*\)$/, "")).join(" → ") : "";
}

async function loadOptions() {
  let data = null;
  try { data = await (await fetch("/api/options", { cache: "no-store" })).json(); } catch {}
  if (!data?.classes?.length) {
    $("results").innerHTML = `<div class="note">Couldn't load the maps. Try again in a moment.</div>`;
    return;
  }
  classes = data.classes;
  $("counts").style.display = data.runs ? "" : "none";       // no big zeros on day one
  $("c-hours").textContent = (data.hours || 0) < 10 ? String(Math.round((data.hours || 0) * 10) / 10) : fmt(data.hours);
  $("c-players").textContent = fmt(data.players || 0);
  newPlayers = data.players_new || 0;
  $("c-players-l").textContent = newPlayers ? `players (+${fmt(newPlayers)} new)` : "players";
  $("c-players-l").title = newPlayers ? "New players count for everyone after a day and 4 runs. \"Include new players\" in Best maps shows them now." : "";
  $("c-maps").textContent = fmt(data.maps || 0);
  ["c-hours", "c-players", "c-maps"].forEach((id) => countUp($(id)));
  $("classes").innerHTML = classes.map((c) => `<label><input type="radio" name="job" value="${esc(c.name)}">` +
    `<span>${esc(c.name)}</span></label>`).join("");
  const link = fromLink(), linked = Object.keys(link).length > 0;
  const last = { ...saved(), ...link };
  if (Object.hasOwn(SORTS, last.sort)) view.sort = last.sort;
  view.hideEarly = !last.showEarly;          // (the old "hide" setting is ignored: hidden is the new default)
  view.withNew = !!last.withNew;
  view.tab = link.view === "ladder" ? "ladder" : "maps";
  setTabs();
  if (last.level) $("level").value = last.level;
  if (soloOrParty(String(last.party))) $("party").value = soloOrParty(String(last.party));
  wantPath = last.path || null;
  pickClass(last.job);
  if (!data.runs) {
    $("results-title").textContent = "The community map is just getting started";
    $("results-sub").textContent = "";
    $("results").innerHTML = `<div class="note"><h3>Be one of the first to share!</h3>
      Every 15 minutes farmed by a player using the overlay shows up here. As more players join in,
      this fills with the best maps for every class, path and level.<br>${JOIN}</div>`;
    if (linked) $("results-section").scrollIntoView({ block: "start" });
    return;
  }
  ask(null, linked);
}

// What the page is showing: which view, how it's sorted, and the last answer (so changing the sort or
// the tab never asks the server again, and asking the same thing twice within 15 minutes doesn't either).
const SORTS = {
  exp: ["EXP/hr", (m) => m.exp_hr, "Best pick"],
  mesos: ["Mesos/hr", (m) => m.mesos_hr ?? -1, "Most mesos"],
};
const TOP = 5;                               // maps shown before "Show more" (all of them come in one answer)
// (early data is hidden unless the visitor shows it; showAll lasts until the next question)
const view = { sort: "exp", hideEarly: true, showAll: false, withNew: false, tab: "maps" };
const NEW_TIP = "Players who joined lately. They count for everyone after a day and 4 runs.";
const newBox = () => `<label class="chk" title="${NEW_TIP}"><input type="checkbox" id="with-new"${view.withNew ? " checked" : ""}> Include new players</label>`;
/** Ticking "Include new players" asks again (it's in the chart's toolbar, the ladder's, and the empty answers). */
function hookNew() {
  $("with-new")?.addEventListener("change", (e) => { view.withNew = e.target.checked; remember(); ask(null, false); });
}
let newPlayers = 0;                          // players who joined lately and don't count yet (from /api/options)
let current = null;                          // { data, level, party, job, path }
const memo = new Map();
const MEMO_MS = 15 * 60 * 1000;

/** An answer from the server, kept for 15 minutes: the same question isn't asked twice. */
async function getJson(url) {
  const hit = memo.get(url);
  if (hit && Date.now() - hit.at < MEMO_MS) return hit.data;
  const data = await (await fetch(url, { cache: "no-store" })).json();
  if (!data.error) memo.set(url, { at: Date.now(), data });
  return data;
}

/** The address follows what's chosen, so any view can be shared or bookmarked. */
function syncUrl() {
  const c = chosenClass();
  if (!c) return;
  const q = new URLSearchParams({ level: $("level").value, party: $("party").value, job: c.name });
  if (!firstJob() && $("path").value) q.set("path", $("path").value);
  if (view.sort !== "exp") q.set("sort", view.sort);
  if (!view.hideEarly) q.set("early", "1");
  if (view.withNew) q.set("new", "1");
  if (view.tab === "ladder") q.set("view", "ladder");
  try { history.replaceState(null, "", `${location.pathname}?${q}`); } catch {}
}

function setTabs() {
  for (const [id, tab] of [["tab-maps", "maps"], ["tab-ladder", "ladder"]]) {
    $(id).setAttribute("aria-selected", String(view.tab === tab));
    $(id).tabIndex = view.tab === tab ? 0 : -1;
  }
}

async function ask(event, scroll = !!event) {
  if (event) event.preventDefault();
  const c = chosenClass();
  if (!c) return;
  const level = Math.max(1, Math.min(250, parseInt($("level").value, 10) || 30)), party = $("party").value;
  $("level").value = level;
  fillPaths();
  const job = c.name, path = level < SECOND_JOB ? null : $("path").value;
  try {
    localStorage.setItem("ask", JSON.stringify({ job, path: path || wantPath, level, party, sort: view.sort, showEarly: !view.hideEarly, withNew: view.withNew }));
  } catch {}
  syncUrl();
  if (view.tab === "ladder") return showLadder(job, path, level, party, scroll);
  const who = path ? `${esc(path)} (${esc(job)})` : esc(job);
  $("results-title").textContent = "Best maps";
  $("results-sub").innerHTML = `For <b>${who}</b> at Lv ${level}, ${partyName(party)}.`;
  $("results").innerHTML = `<div class="note">Looking...</div>`;
  if (scroll) $("results-section").scrollIntoView({ behavior: "smooth", block: "start" });
  let data;
  try {
    const query = new URLSearchParams({ class: job, ...(path ? { path } : {}), level, party, ...(view.withNew ? { new: "1" } : {}) });
    data = await getJson(`/api/stats?${query}`);
    if (data.busy) { $("results").innerHTML = `<div class="note">Lots of people are asking right now. Try again in a few minutes.</div>`; return; }
    if (data.error) throw new Error(data.error);
  } catch {
    $("results").innerHTML = `<div class="note">Couldn't load the maps. Try again in a moment.</div>`;
    return;
  }
  if (!Array.isArray(data.early)) data.early = [];       // (an answer from before early data: none)
  current = { data, level, party, job, path };
  const [lo, hi] = data.levels;
  $("results-sub").innerHTML = `For <b>${who}</b> at Lv ${level}, ${partyName(party)}. Each map uses players at Lv ${lo}–${hi},`
    + ` and goes wider only if it needs more players (the row says so).` + (view.withNew ? " Includes new players." : "") +
    (data.newest_run ? ` Newest run shared ${esc(ago(data.newest_run))}.` : "");
  if (!data.maps.length && !data.early.length) {
    $("results").innerHTML = `<div class="note"><h3>No maps here yet</h3>
      A map shows up once a player around your level has shared a run there.
      Help fill it in by sharing your own farming.<br>${JOIN}</div>${newPlayers || view.withNew ? `<div class="tools">${newBox()}</div>` : ""}`;
    hookNew();
    return;
  }
  view.showAll = false;
  showChart(true);
}

// ---------------------------------------------------------------- the chart
// One row per map, all on one scale: a dot for every player (their typical run), the
// typical EXP/hr (gold line), what the middle half of players get (the bar), and the runs
// left out as unusual (hollow rings).

const motion = () => !!window.gsap && !matchMedia("(prefers-reduced-motion: reduce)").matches;
const compact = (n) => n >= 1e6 ? `${+(n / 1e6).toFixed(2)}M` : n >= 1e3 ? `${+(n / 1e3).toFixed(n >= 1e5 ? 0 : 1)}k`
  : String(Math.round(n));
let shown = null;                          // what's on the chart now, for the details on hover
let watcher = null;                        // starts each row's animation as it scrolls into view

/** A scale with round numbers that fits every map's dots and bars. (Left-out runs can be far
 * off: they're pinned to the edge with their number.) */
function scaleFor(maps) {
  const all = maps.flatMap((m) => [m.exp_hr, m.exp_hr_low, m.exp_hr_high, ...(m.dots || [])]).filter((n) => n > 0);
  let lo = Math.min(...all), hi = Math.max(...all);
  if (hi - lo < hi * 0.15) { lo *= 0.85; hi *= 1.12; }
  const pad = (hi - lo) * 0.08;
  lo = Math.max(0, lo - pad); hi += pad;
  const rough = (hi - lo) / 4, p = 10 ** Math.floor(Math.log10(rough)), f = rough / p;
  const step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p;
  lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;
  const ticks = Array.from({ length: Math.round((hi - lo) / step) + 1 }, (_, i) => lo + i * step);
  return { lo, hi, ticks, x: (n) => Math.max(0, Math.min(100, (n - lo) / (hi - lo) * 100)) };
}

/** "Steady" when most players get about the same, "Varies a lot" when it depends on you. */
function spread(m) {
  if (m.players < 5) return "";
  const r = (m.exp_hr_high - m.exp_hr_low) / m.exp_hr;
  return r < 0.15 ? `<span class="chip steady" title="Most players get about the same here">Steady</span>`
    : r > 0.35 ? `<span class="chip varies" title="Gear, skills and how busy the map is make a big difference here">Varies a lot</span>`
    : "";
}

/** 0 to 4: a player's level against yours: 4+ lower, 1-3 lower, the same, 1-3 higher, 4+ higher. */
const bucket = (lv) => { const d = lv - current.level; return d <= -4 ? 0 : d < 0 ? 1 : d === 0 ? 2 : d < 4 ? 3 : 4; };
const BUCKETS = ["4+ levels lower", "1–3 lower", "your level", "1–3 higher", "4+ higher"];
const off = (lv) => { const d = lv - current.level; return d === 0 ? "your level" : `${d > 0 ? "+" : "−"}${Math.abs(d)} vs you`; };

function rowHtml(m, i, s, early, need, rank = i + 1) {
  const x = s.x, dots = m.dots || [], out = m.unusual || [], dl = m.dot_levels || [], ul = m.unusual_levels || [];
  const dn = m.dot_new || [], un = m.unusual_new || [];
  const lo = x(m.exp_hr_low), hi = x(m.exp_hr_high), mid = x(m.exp_hr);
  const band = early || m.players < 2 ? "" : `<b class="band" style="left:${lo}%;width:${Math.max(0.8, hi - lo)}%;` +
    `transform-origin:${hi > lo ? ((mid - lo) / (hi - lo)) * 100 : 50}% 50%"></b>`;
  const edge = (d) => d > s.hi ? `<span class="edge" style="right:0">${compact(d)} ›</span>`
    : d < s.lo ? `<span class="edge" style="left:0">‹ ${compact(d)}</span>` : "";
  const lvAttr = (lv) => Number.isFinite(lv) ? ` data-lv="${Number(lv)}" data-b="${bucket(Number(lv))}"` : "";
  const marks = dots.map((d, k) => `<i class="dot${dn[k] ? " fresh" : ""}" style="left:${x(d)}%" data-v="${Number(d)}"${lvAttr(dl[k])}></i>`).join("") +
    out.map((d, k) => `<i class="dot out${un[k] ? " fresh" : ""}" style="left:${x(d)}%" data-v="${Number(d)}"${lvAttr(ul[k])}></i>${edge(d)}`).join("");
  const best = i === 0 && !early && !m.widened;
  const levels = m.levels ? `Lv ${Number(m.levels[0])}–${Number(m.levels[1])}` : "";
  const widened = m.widened ? `<span class="chip widen" title="Not enough players at your level, so this map uses players from a wider range of levels">wider range</span>` : "";
  const fresh = m.new_players ? `<span class="chip fresh" title="${plural(m.new_players, "player")} here joined lately and don't count for everyone yet (a day and 4 runs)">includes ${plural(m.new_players, "new player")}</span>` : "";
  const id = `${early ? "e" : "m"}${i}`;
  return `<div class="row${best ? " best" : ""}${early ? " early" : ""}" data-at="${id}">
    <div class="rank">${early ? "" : rank}</div>
    <div class="info"><h3>${esc(m.map)}${best ? `<span class="tag">${esc(SORTS[view.sort][2])}</span>` : ""}</h3>
      <div class="meta">${plural(m.players, "player")} · ${hours(m.hours)}<span class="wide-only"> of farming</span></div>
      <div class="meta">${levels}${widened ? " " + widened : ""}${fresh ? " " + fresh : ""}</div>
      ${early ? `<span class="chip wait">Needs ${plural(need, "more player")}</span>` : spread(m)}
      <button class="open" type="button" aria-expanded="false" aria-controls="d-${id}">Details <i aria-hidden="true"></i></button></div>
    <div class="track" aria-hidden="true"><i class="base"></i>${s.ticks.map((t) => `<i class="grid" style="left:${x(t)}%"></i>`).join("")}
      ${band}<b class="med" style="left:${mid}%"></b>${marks}</div>
    <div class="nums"><div class="exp"><span data-n="${Number(m.exp_hr)}">${fmt(m.exp_hr)}</span><small>EXP/hr</small></div>
      <div class="mesos">${m.mesos_hr === null ? "mesos/hr not known" : `${fmt(m.mesos_hr)} mesos/hr`}</div></div>
    <span class="sr-only">Typical ${fmt(m.exp_hr)} EXP/hr${m.players > 1 ? `; half of players get ${fmt(m.exp_hr_low)} to ${fmt(m.exp_hr_high)}` : ""}.</span>
  </div>`;
}

/** The panel under a map: EXP/hr by party size (solo, 2 to 6) and by level. */
function detailHtml(m, id) {
  const sizes = m.by_party || [];          // (sizes nobody farmed here aren't in it)
  const top = Math.max(1, ...sizes.map((b) => b.exp_hr));
  const mine = (n) => current.party === "solo" ? n === 1 : n >= 2;
  const party = `<div class="d-card"><h4>EXP/hr by party size</h4>${sizes.length ? `<div class="pt-rows">${sizes.map((b) =>
    `<div class="pt${mine(b.party) ? " me" : ""}"><span>${b.party === 1 ? "Solo" : `Party of ${Number(b.party)}`}<small>${plural(b.players, "player")}</small></span>
      <i aria-hidden="true"><i style="width:${Math.max(4, b.exp_hr / top * 100).toFixed(0)}%"></i></i><b>${fmt(b.exp_hr)}</b></div>`).join("")}</div>`
    : `<p class="d-none">No runs by party size yet.</p>`}</div>`;
  const tall = Math.max(1, ...m.by_level.map((b) => b.exp_hr));
  const bars = m.by_level.map((b) => `<div class="lvb${b.level === current.level ? " me" : ""}" tabindex="0" data-lv="${Number(b.level)}" data-exp="${Number(b.exp_hr)}" data-n="${Number(b.players)}">
      <i style="height:${Math.max(6, b.exp_hr / tall * 100).toFixed(0)}%"></i><span>${Number(b.level)}</span></div>`).join("");
  const levels = `<div class="d-card"><h4>EXP/hr by level</h4>${m.by_level.length > 1
    ? `<div class="lv-bars" role="group" aria-label="EXP per hour at each level, from ${Number(m.by_level[0].level)} to ${Number(m.by_level.at(-1).level)}">${bars}</div>
      <div class="lv-tip" role="status" aria-live="polite"><b></b><span></span></div>`
    : `<p class="d-none">Players here are all at one level so far.</p>`}</div>`;
  return `<div class="detail" id="d-${id}" role="region" aria-label="Details for ${esc(m.map)}">${party}${levels}</div>`;
}

/** The chart with the current sort and filter. `animate` is false when only the order changed. */
function showChart(animate = true) {
  const { data } = current, key = SORTS[view.sort][1];
  const bySort = (a, b) => key(b) - key(a);
  const near = data.maps.filter((m) => !m.widened).sort(bySort), wide = data.maps.filter((m) => m.widened).sort(bySort);
  const maps = [...near, ...wide];                  // (the details look maps up by this order)
  const early = view.hideEarly ? [] : data.early;
  shown = { maps, early };
  const touch = matchMedia("(hover: none)").matches;
  const minPlayers = data.min_players, far = Number(data.ranges[data.ranges.length - 1]);
  const tools = `<div class="tools">
      <label>Sort by <select id="sort">${Object.entries(SORTS).map(([k, v]) => `<option value="${k}"${k === view.sort ? " selected" : ""}>${esc(v[0])}</option>`).join("")}</select></label>
      ${data.early.length ? `<label class="chk"><input type="checkbox" id="hide-early"${view.hideEarly ? " checked" : ""}> Hide early data</label>` : ""}
      ${newPlayers || view.withNew ? newBox() : ""}
    </div>`;
  const hook = () => {
    $("sort").addEventListener("change", (e) => { view.sort = e.target.value; remember(); showChart(false); });
    $("hide-early")?.addEventListener("change", (e) => { view.hideEarly = e.target.checked; remember(); showChart(false); });
    hookNew();
  };
  if (!maps.length && !early.length) {             // nothing ranked, and early data is hidden
    $("results").innerHTML = `<div class="chart">${tools}<div class="early-head none-head"><b>Nothing ranked here yet</b>
      <span>No map has ${Number(minPlayers)} players around your level yet.</span>
      <button class="more-btn inline" type="button" id="show-early">Show early data (${plural(data.early.length, "map")})</button></div></div>`;
    $("show-early").addEventListener("click", () => { view.hideEarly = false; remember(); showChart(true); });
    hook();
    return;
  }
  const s = scaleFor([...maps, ...early]);
  // In order: maps at your level, the wider range, early data. Only the first TOP maps show until "Show more".
  const parts = near.map((m, i) => ({ row: true, html: rowHtml(m, i, s, false) }));
  if (wide.length) {
    parts.push({ html: `<div class="early-head wide-head"><b>Wider range</b>
      <span>${near.length ? "Not enough players within " + Number(data.ranges[0]) + " levels of you for these, so they use players up to " + far + " levels away. Less specific to your level."
        : "Nobody has shared enough runs within " + Number(data.ranges[0]) + " levels of you yet, so these use players up to " + far + " levels away."}</span></div>` });
    wide.forEach((m, j) => parts.push({ row: true, html: rowHtml(m, near.length + j, s, false, 0, j + 1) }));
  }
  if (early.length) {
    parts.push({ html: `<div class="early-head"><b>${maps.length ? "Early data" : "Nothing ranked here yet"}</b>
      <span>${maps.length ? `Fewer than ${Number(minPlayers)} players so far, so not ranked yet.`
        : `No map has ${Number(minPlayers)} players around your level yet. Here's what's come in so far.`}</span></div>` });
    early.forEach((m, i) => parts.push({ row: true, html: rowHtml(m, i, s, true, minPlayers - m.players) }));
  }
  const rows = parts.filter((p) => p.row).length, extra = Math.max(0, rows - TOP);
  let cut = parts.length;
  for (let k = 0, seen = 0; k < parts.length; k++) if (parts[k].row && ++seen === TOP) { cut = k + 1; break; }
  const html = (list) => list.map((p) => p.html).join("");
  const moreLabel = () => view.showAll ? `Show the top ${TOP} only` : `Show ${plural(extra, "more map")}`;
  $("results").innerHTML = `<div class="chart">
    ${tools}
    <div class="chart-top">
      <div class="legend" aria-hidden="true"><span><i class="lg-dot"></i>One player</span><span><i class="lg-med"></i>Typical</span>
        <span><i class="lg-band"></i>Middle half of players</span><span><i class="lg-out"></i>Left out (unusual)</span>${view.withNew ? `<span><i class="lg-dot lg-fresh"></i>New player</span>` : ""}</div>
      <div class="legend lv-legend" aria-hidden="true"><b>Dot colour = player's level</b>${BUCKETS.map((t, k) => `<span><i class="lg-dot" data-b="${k}"></i>${t}</span>`).join("")}</div>
      <small>${touch ? "Tap" : "Point at"} a dot for details</small>
    </div>
    <div class="axis" aria-hidden="true"><div class="scale">${s.ticks.map((t) => `<b style="left:${s.x(t)}%">${compact(t)}</b>`).join("")}</div></div>
    ${html(parts.slice(0, cut))}
    ${extra ? `<div id="more-rows"${view.showAll ? "" : " hidden"}>${html(parts.slice(cut))}</div>
      <button class="more-btn" type="button" id="more-btn" aria-expanded="${view.showAll}" aria-controls="more-rows">${moreLabel()}</button>` : ""}
    <div class="chart-foot">Each player counts once, however much they play, and a new player's runs start counting after a day.
      ${JOIN.replace('class="more" ', "")}</div>
  </div><div class="tip" role="status"><b></b><span></span></div>`;
  spreadDots();
  hookDetails();
  if (animate) animateChart();
  $("more-btn")?.addEventListener("click", (e) => {
    view.showAll = !view.showAll;
    $("more-rows").hidden = !view.showAll;
    e.currentTarget.setAttribute("aria-expanded", String(view.showAll));
    e.currentTarget.textContent = moreLabel();
    if (view.showAll) spreadDots();            // (hidden rows had no width to spread their dots on)
    else e.currentTarget.scrollIntoView({ block: "nearest" });
  });
  hook();
}

/** Keeps the sort and filter for next time, and in the address. */
function remember() {
  try { localStorage.setItem("ask", JSON.stringify({ ...saved(), sort: view.sort, showEarly: !view.hideEarly, withNew: view.withNew })); } catch {}
  syncUrl();
}

// A map's details open under it (a button, or a click on its name or numbers; the dots keep their own pointing).
$("results").addEventListener("click", (e) => {
  const row = e.target.closest(".row");
  if (!row || !current || e.target.closest(".track")) return;
  const open = row.nextElementSibling?.classList.contains("detail");
  const button = row.querySelector(".open");
  if (open) {
    row.nextElementSibling.remove();
    button.setAttribute("aria-expanded", "false");
    return;
  }
  const at = row.dataset.at;
  const m = (at[0] === "e" ? shown.early : shown.maps)[+at.slice(1)];
  row.insertAdjacentHTML("afterend", detailHtml(m, at));
  button.setAttribute("aria-expanded", "true");
});

// Point at (or tap, or tab to) a level's bar in a map's details: what it is, in words.
{
  const show = (bar) => {
    const card = bar.closest(".d-card"), tip = card.querySelector(".lv-tip");
    card.querySelectorAll(".lvb.on").forEach((b) => b.classList.remove("on"));
    bar.classList.add("on");
    const lv = Number(bar.dataset.lv), n = Number(bar.dataset.n);
    tip.querySelector("b").textContent = `Lv ${lv} · ${fmt(Number(bar.dataset.exp))} EXP/hr`;
    tip.querySelector("span").textContent = `${plural(n, "player")} · ${off(lv)}`;
    const c = card.getBoundingClientRect(), r = bar.querySelector("i").getBoundingClientRect(), half = tip.offsetWidth / 2;
    tip.style.left = `${Math.max(half + 2, Math.min(c.width - half - 2, r.left + r.width / 2 - c.left))}px`;
    tip.style.top = `${r.top - c.top - 6}px`;
    tip.classList.add("show");
  };
  const hide = (card) => { card?.querySelectorAll(".lvb.on").forEach((b) => b.classList.remove("on")); card?.querySelector(".lv-tip")?.classList.remove("show"); };
  const box = $("results");
  box.addEventListener("pointerover", (e) => { const b = e.target.closest?.(".lvb"); if (b) show(b); });
  box.addEventListener("pointerdown", (e) => { const b = e.target.closest?.(".lvb"); if (b) show(b); });
  box.addEventListener("focusin", (e) => { const b = e.target.closest?.(".lvb"); if (b) show(b); });
  box.addEventListener("pointerout", (e) => { if (e.pointerType !== "touch" && e.target.closest?.(".lvb") && !e.relatedTarget?.closest?.(".lvb")) hide(e.target.closest(".d-card")); });
  box.addEventListener("focusout", (e) => { if (e.target.closest?.(".lvb")) hide(e.target.closest(".d-card")); });
  document.addEventListener("pointerdown", (e) => { if (!e.target.closest?.(".lvb")) box.querySelectorAll(".d-card").forEach(hide); });
}

// ---------------------------------------------------------------- the leveling ladder
// For the class and path chosen: the best maps in each band of 5 levels, to plan the whole climb.

async function showLadder(job, path, level, party, scroll) {
  const who = path ? `${esc(path)} (${esc(job)})` : esc(job);
  $("results-title").textContent = "Leveling ladder";
  $("results-sub").innerHTML = `The best maps at each stage for <b>${who}</b>, ${partyName(party)}. Click a map to see it for that level.`;
  $("results").innerHTML = `<div class="note">Looking...</div>`;
  if (scroll) $("results-section").scrollIntoView({ behavior: "smooth", block: "start" });
  let data;
  try {
    const query = new URLSearchParams({ class: job, ...(path ? { path } : {}), party, ...(view.withNew ? { new: "1" } : {}) });
    data = await getJson(`/api/ladder?${query}`);
    if (data.busy) { $("results").innerHTML = `<div class="note">Lots of people are asking right now. Try again in a few minutes.</div>`; return; }
    if (data.error) throw new Error(data.error);
  } catch {
    $("results").innerHTML = `<div class="note">Couldn't load the ladder. Try again in a moment.</div>`;
    return;
  }
  if (!data.bands.length) {
    $("results").innerHTML = `<div class="note"><h3>No ladder yet</h3>A stage shows up once ${Number(data.min_players)} players have shared a map there.<br>${JOIN}</div>${newPlayers || view.withNew ? `<div class="tools">${newBox()}</div>` : ""}`;
    hookNew();
    return;
  }
  $("results").innerHTML = `<div class="chart ladder">${newPlayers || view.withNew ? `<div class="tools">${newBox()}</div>` : ""}${data.bands.map((b) => {
    const me = level >= b.from && level <= b.to;
    return `<div class="stage${me ? " me" : ""}"><div class="lv">Lv ${Number(b.from)}–${Number(b.to)}${me ? "<small>you are here</small>" : ""}</div>
      <div class="picks">${b.maps.map((m, i) => `<button type="button" class="pick${i === 0 ? " top" : ""}" data-level="${Math.max(1, Number(b.from) + 2)}">
        <b>${esc(m.map)}</b><span>${fmt(m.exp_hr)} EXP/hr · ${m.pct_hr === null ? "" : `${Number(m.pct_hr)}% a hour · `}${plural(m.players, "player")}${m.new_players ? ` (${m.new_players} new)` : ""}</span></button>`).join("")}</div></div>`;
  }).join("")}<div class="chart-foot">Each stage ranks maps for players in those 5 levels (3 players needed).${view.withNew ? " Includes new players." : ""} ${JOIN.replace('class="more" ', "")}</div></div>`;
  hookNew();
  $("results").querySelector(".stage.me")?.scrollIntoView({ block: "nearest" });
}

$("results").addEventListener("click", (e) => {
  const pick = e.target.closest(".pick");
  if (!pick) return;
  $("level").value = pick.dataset.level;
  view.tab = "maps";
  setTabs();
  ask(null, true);
});

for (const [id, tab] of [["tab-maps", "maps"], ["tab-ladder", "ladder"]]) {
  $(id).addEventListener("click", () => { view.tab = tab; setTabs(); ask(null, false); });
}

$("copy-link").addEventListener("click", async () => {
  syncUrl();
  const button = $("copy-link"), text = button.textContent;
  try {
    await navigator.clipboard.writeText(location.href);
    button.textContent = "Link copied";
  } catch {
    button.textContent = "Copy it from the address bar";
  }
  setTimeout(() => { button.textContent = text; }, 2200);
});

/** Dots close together are moved up or down a little, so each one can be seen. */
function spreadDots() {
  const lanes = [0, -7, 7, -14, 14];
  document.querySelectorAll("#results .track").forEach((track) => {
    const w = track.clientWidth, last = new Map();
    [...track.querySelectorAll(".dot")].map((el) => ({ el, at: parseFloat(el.style.left) / 100 * w }))
      .sort((a, b) => a.at - b.at).forEach(({ el, at }) => {
        const lane = lanes.find((y) => !last.has(y) || at - last.get(y) >= 10) ??
          lanes.reduce((a, b) => (last.get(a) <= last.get(b) ? a : b));
        last.set(lane, at);
        el.style.setProperty("--y", `${lane}px`);
      });
  });
}

/** Point at (or tap) a row: the nearest dot, the gold line or the bar says what it is. */
function hookDetails() {
  const box = $("results"), tip = box.querySelector(".tip");
  let lit = null;
  const hide = () => { tip.classList.remove("show"); lit?.classList.remove("on"); lit = null; };
  const say = (el, title, text, track) => {
    if (lit !== el) { lit?.classList.remove("on"); el.classList.add("on"); lit = el; }
    tip.querySelector("b").textContent = title;
    tip.querySelector("span").textContent = text;
    const wrap = box.getBoundingClientRect(), r = el.getBoundingClientRect(), t = track.getBoundingClientRect();
    const half = tip.offsetWidth / 2;
    const left = Math.max(half + 4, Math.min(wrap.width - half - 4, r.left + r.width / 2 - wrap.left));
    if (!tip.classList.contains("show")) { tip.classList.add("jump"); void tip.offsetWidth; }
    tip.style.left = `${left}px`;
    tip.style.top = `${t.top - wrap.top + 4}px`;
    tip.classList.add("show");
    requestAnimationFrame(() => tip.classList.remove("jump"));
  };
  box.querySelectorAll(".track").forEach((track) => {
    const row = track.closest(".row"), at = row.dataset.at;
    const m = (at[0] === "e" ? shown.early : shown.maps)[+at.slice(1)], early = at[0] === "e";
    const move = (e) => {
      const px = e.clientX - track.getBoundingClientRect().left, reach = e.pointerType === "touch" ? 26 : 16;
      let near = null, gap = reach;
      for (const el of track.querySelectorAll(".dot, .med")) {
        const d = Math.abs(el.offsetLeft + el.offsetWidth / 2 - px);
        if (d < gap) { near = el; gap = d; }
      }
      const band = track.querySelector(".band");
      if (!near && band && px >= band.offsetLeft && px <= band.offsetLeft + band.offsetWidth) near = band;
      if (!near) return hide();
      const v = Number(near.dataset.v);
      if (near.classList.contains("out")) {
        const lv = near.dataset.lv ? ` · Lv ${near.dataset.lv}` : "";
        say(near, `${fmt(v)} EXP/hr · left out${lv}`, v > m.exp_hr
          ? "Over double what others get here: a leech, or very different gear."
          : "Under half what others get here: AFK, or not really farming.", track);
      } else if (near.classList.contains("dot")) {
        const lv = near.dataset.lv;
        say(near, `${fmt(v)} EXP/hr${lv ? ` · Lv ${lv}` : ""}`,
          lv ? `One player's typical run here (${off(Number(lv))}).` : "One player's typical run here.", track);
      } else if (near.classList.contains("med")) {
        say(near, `${fmt(m.exp_hr)} EXP/hr typical`, early ? `So far, from ${plural(m.players, "player")}.`
          : "Half of players get more, half less.", track);
      } else {
        say(near, `${short(m.exp_hr_low)} – ${short(m.exp_hr_high)} EXP/hr`, "What the middle half of players get.", track);
      }
    };
    track.addEventListener("pointermove", move);
    track.addEventListener("pointerdown", move);
    track.addEventListener("pointerleave", (e) => { if (e.pointerType !== "touch") hide(); });
    track.addEventListener("pointercancel", hide);
  });
  hideDetails = hide;
}
let hideDetails = () => {};
document.addEventListener("pointerdown", (e) => { if (!e.target.closest?.("#results .track")) hideDetails(); });

/** Each row comes in as it scrolls into view: the bar grows out from the typical line,
 * the dots pop in from left to right and the number counts up. */
function animateChart() {
  watcher?.disconnect();
  if (!motion()) return;
  const box = $("results");
  gsap.from(box.querySelectorAll(".legend span, .chart-top small, .axis b"),
    { autoAlpha: 0, duration: 0.8, stagger: 0.035, ease: "power2.out", clearProps: "opacity,visibility" });
  watcher = new IntersectionObserver((entries) => {
    entries.filter((e) => e.isIntersecting).forEach((e, n) => {
      watcher.unobserve(e.target);
      gsap.delayedCall(n * 0.08, () => e.target._play.play());
    });
  }, { rootMargin: "0px 0px -6% 0px" });
  box.querySelectorAll(".row").forEach((row) => {
    const clear = "transform,opacity,visibility";
    const tl = gsap.timeline({ paused: true, defaults: { ease: "power3.out" } });
    tl.from(row, { autoAlpha: 0, y: 18, duration: 0.8, clearProps: clear });
    const band = row.querySelector(".band"), dots = row.querySelectorAll(".dot, .edge"), num = row.querySelector("[data-n]");
    if (band) tl.from(band, { scaleX: 0, duration: 1.2, ease: "expo.out", clearProps: clear }, 0.15);
    tl.from(row.querySelector(".med"), { scaleY: 0, duration: 0.7, ease: "back.out(2)", clearProps: clear }, 0.3);
    if (dots.length) {
      tl.from(dots, { scale: 0, autoAlpha: 0, duration: 0.5, ease: "back.out(2.6)", clearProps: clear,
        stagger: Math.min(0.035, 0.7 / dots.length) }, 0.35);
    }
    const end = Number(num.dataset.n), n = { v: 0 };
    tl.to(n, { v: end, duration: 1.3, ease: "power2.out", onUpdate: () => { num.textContent = fmt(n.v); } }, 0.15);
    row._play = tl;
    watcher.observe(row);
  });
}

let resizing = 0;
addEventListener("resize", () => { clearTimeout(resizing); resizing = setTimeout(spreadDots, 150); });

/** Counts a number up from 0, keeping how it's written ("2.5", "1,204"). */
function countUp(el) {
  const text = el.textContent, end = parseFloat(text.replace(/,/g, "")), decimals = (text.split(".")[1] || "").length;
  if (!motion() || !(end > 0)) return;
  const n = { v: 0 };
  el.textContent = "0";
  gsap.to(n, { v: end, duration: 1.8, delay: 0.35, ease: "power3.out",
    onUpdate: () => { el.textContent = decimals ? n.v.toFixed(decimals) : fmt(n.v); },
    onComplete: () => { el.textContent = text; } });
}

// ---------------------------------------------------------------- the bar at the top
// The latest runs players shared, one at a time (the server picks them: one per player,
// nothing unusual). Clicking one shows the best maps for that level, class and party.

let news = [], newsAt = 0, newsHeld = false;
const ago = (iso) => {
  const min = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60000));
  return min < 2 ? "just now" : min < 60 ? `${min} min ago` : min < 36 * 60 ? `${Math.round(min / 60)} h ago`
    : `${Math.round(min / 1440)} days ago`;
};
const jobName = (job) => job.replace(" (Fire,Poison)", " (F/P)").replace(" (Ice,Lightning)", " (I/L)");

function newsItem(r) {
  const party = r.party > 1 ? `party of ${Number(r.party)}` : "solo";
  const level = Number(r.level), size = Number(r.party);
  return `<button type="button" data-level="${level}" data-party="${size}" data-job="${esc(r.job)}"
    title="See the best maps for Lv ${level}, ${esc(jobName(r.job))}, ${size > 1 ? "in a party" : "solo"}">` +
    `<b>Lv ${level} ${esc(jobName(r.job))}</b><span class="says"> got</span> · <span class="x"><span class="n-full">${fmt(r.exp_hr)}</span><span class="n-short">${compact(r.exp_hr)}</span> EXP/hr</span> · ` +
    `<b class="m">${esc(r.map)}</b><span class="p"> · ${party}</span> · <span class="ago">${ago(r.shared)}</span></button>`;
}

/** The next run slides up into the bar (or just appears, without motion). */
function nextNews(first = false) {
  const box = $("ticker"), old = [...box.children];
  box.insertAdjacentHTML("beforeend", newsItem(news[newsAt % news.length]));
  const el = box.lastElementChild;
  if (motion() && !first) {
    gsap.fromTo(el, { yPercent: 90, autoAlpha: 0 }, { yPercent: 0, autoAlpha: 1, duration: 0.8, ease: "expo.out" });
    gsap.to(old, { yPercent: -90, autoAlpha: 0, duration: 0.45, ease: "power2.in", onComplete: () => old.forEach((o) => o.remove()) });
  } else {
    old.forEach((o) => o.remove());
    if (motion()) gsap.from(el, { autoAlpha: 0, duration: 0.8, delay: 0.4, ease: "power2.out" });
  }
}

async function loadNews() {
  let data = null;
  try { data = await (await fetch("/api/recent", { cache: "no-store" })).json(); } catch {}
  if (!data?.runs) return;
  if (!data.runs.length) {
    if (!news.length) $("news").hidden = true;            // nothing shared yet
    return;
  }
  const first = !news.length;
  news = data.runs;
  if (first) {
    newsAt = 0;
    $("news-more").textContent = "Click one for its best maps";
    nextNews(true);
  }
}

$("ticker").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-job]");
  if (!b || !classes.length) return;
  const want = fromLink(new URLSearchParams({ level: b.dataset.level, party: b.dataset.party, job: b.dataset.job }));
  if (want.level) $("level").value = want.level;
  if (want.party) $("party").value = want.party;
  wantPath = want.path || null;
  pickClass(want.job);
  ask(null, true);
});
for (const [on, held] of [["pointerenter", true], ["pointerleave", false], ["focusin", true], ["focusout", false]]) {
  $("news").addEventListener(on, () => { newsHeld = held; });
}
setInterval(() => {                       // a new one every 5 seconds, unless you're on it
  if (news.length > 1 && !newsHeld && !document.hidden) { newsAt++; nextNews(); }
}, 5000);
// The latest runs are asked for only while the page is on screen, and at most every 5 minutes: a tab
// left open in the background must not keep calling the server all day (each call counts toward the
// free plan's daily limit).
const NEWS_EVERY = 5 * 60 * 1000;
let newsAsked = 0;
function refreshNews() {
  if (document.hidden || Date.now() - newsAsked < NEWS_EVERY) return;
  newsAsked = Date.now();
  loadNews();
}
setInterval(refreshNews, 30000);
document.addEventListener("visibilitychange", refreshNews);   // back on the tab: catch up
refreshNews();

// "Coming next": shown unless the visitor closed this exact list (saved in their browser; a changed list shows again).
{
  const box = $("next");
  const key = "next:" + [...box.querySelectorAll("li")].map((li) => li.textContent.trim()).join("|");
  let closed = false;
  try { closed = localStorage.getItem("nextClosed") === key; } catch {}
  box.hidden = closed;
  $("next-x").addEventListener("click", () => {
    box.hidden = true;
    try { localStorage.setItem("nextClosed", key); } catch {}
  });
}

// The hero: the headline rises into place line by line, the rest fades up.
if (motion()) {
  const clear = "transform,opacity,visibility";
  gsap.timeline({ defaults: { ease: "expo.out", duration: 1.2 } })
    .from(".hero .eyebrow", { autoAlpha: 0, y: 10, clearProps: clear })
    .from(".hero h1 .ln > span", { yPercent: 110, duration: 1.3, stagger: 0.1, clearProps: "transform" }, 0.05)
    .from(".hero p.sub, .counts", { autoAlpha: 0, y: 16, stagger: 0.1, clearProps: clear }, 0.3)
    .from(".finder", { autoAlpha: 0, y: 28, duration: 1.4, clearProps: clear }, 0.2);
}
document.documentElement.classList.remove("anim");

$("classes").addEventListener("change", (e) => pickClass(e.target.value));
$("path").addEventListener("change", () => { wantPath = $("path").value; showSteps(); });
$("level").addEventListener("input", () => { if (!isNaN(parseInt($("level").value, 10))) fillPaths(); });
$("ask").addEventListener("submit", ask);
loadOptions();
