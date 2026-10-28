// Maple Classic Exp Overlay's shared farming stats, on Cloudflare Pages with a D1 database.
//
//   POST /api/runs       the overlay sends finished runs (only if the player agreed)
//   GET  /api/stats      best maps for a class and path, level, solo or in a party
//   GET  /api/ladder     the best maps for each band of 5 levels, for a class
//   GET  /api/options    the classes and paths, how many players each has, and totals
//   GET  /api/recent     the latest runs shared, for the bar at the top of the site
//   GET  /api/v1/maps    partner: same as /api/stats, slim (no chart dots)
//   GET  /api/v1/ladder  partner: same as /api/ladder
//   GET  /api/v1/options partner: same as /api/options
//   GET  /api/v1/recent  partner: same as /api/recent
//   everything else      the website, from public/ (served by Cloudflare without running this)
//
// Partner docs: https://mcwo.app/partners.html  CORS is open (*). Credit mcwo.app.
//
// Anyone can send anything to a public server, so nothing is taken on trust: every run is
// checked for being a plausible run (including no more EXP than its levels hold), each
// install and connection can only send so many a day (and only a few installs can share
// from one connection), and the numbers shown are medians in which a player counts once,
// with runs far from what other players get (same map, class, level, solo or party) left out.
// Posts must be JSON, so other websites can't make their visitors' browsers send runs.

import { canonicalMap, isListed } from "./mapnames.js";
import { handleReports } from "./reports.js";

const MIN_PLAYERS = 3;              // different players needed before a map is ranked
const MAX_MAPS = 40;                // ranked maps in one answer
const MAX_EARLY = 8;                // maps with fewer players shown (greyed out) as early data
const LATEST = 12;                  // runs in the bar at the top of the site
const LATEST_DAYS = 14;             // how far back it looks
const BAND = [0.5, 2.0];            // runs outside this share of the median are outliers
const LEVEL_WINDOW = 5;             // "around your level" for the latest-runs bar: within this many levels
const RADII = [3, 5];               // the levels either way a map's numbers come from: the smallest that has MIN_PLAYERS
const MAX_REVIEW = 500;             // names not on the game's map list that are kept for review
const MAX_PER_REQUEST = 50;
const MAX_MESOS_HR = 2e7;          // mesos an hour: more than any player makes
// The most % of a level an hour a run can gain, from this level up (a run's own level). Past the lists below the
// limit is the old EXP-an-hour one. Chosen well above what real players get (see server/README.md to check them).
const PCT_CAPS = [[150, 100], [100, 150], [60, 200], [30, 300]];
const pctCap = (level) => (PCT_CAPS.find(([from]) => level >= from) || [0, null])[1];
const RUN_SECONDS = [890, 3720];   // a run is 15 minutes (an hour from 0.9.3, still taken)
const MAX_PER_INSTALL_DAY = 70;     // a day holds 96 runs of 15 minutes and nobody farms 24 hours
const MAX_MAPS_PER_INSTALL_DAY = 12; // nobody farms a dozen different maps in a day
const MAX_NEW_INSTALLS_DAY = 300;   // brand-new players in a day: more is a flood, not a crowd (they try again tomorrow)
const MAX_RUNS_DAY = 6000;          // runs from new players in a day (the free database allows 100,000 writes a day)
const MAX_REVIEW_PER_DAY = 50;      // names for review added in a day
const VET_HOURS = 24;               // a new player's runs count after this long...
const VET_RUNS = 4;                 // ...and this many runs (until then they're kept but not shown anywhere)
const UNLISTED_DAYS = 2;            // a map not on the game's list shows only once players shared it on this many days
const MAX_PER_IP_DAY = 400;
const MAX_INSTALLS_PER_IP_DAY = 6;  // copies of the overlay sharing from one connection a day (a party)
const MAX_BODY = 200_000;           // bytes in one post (50 runs are about 25,000)
const DAYS_BACK = 60;               // how old a run can be when it's sent (and up to 2 days ahead)
const RECENT_DAYS = 60;             // the map figures and the ladder use runs received this recently
// Overlays older than MESOS_VERSION counted mesos at shops and trades: from MESOS_RULE_FROM (UTC, "" = not yet)
// their runs are taken with EXP only. Mesos already stored stay.
const MESOS_VERSION = "0.9.11";
const MESOS_RULE_FROM = "2026-10-29T00:00:00Z";      // one week after the 0.9.11-beta release
// How long answers are kept (see cached()): new runs show up on the site within a few minutes.
const EDGE_SECONDS = 60;            // in Cloudflare's cache, where the visitor is
const KEEP = { stats: 120, latest: 120, options: 600, ladder: 21600 };   // (the ladder changes slowly, and it reads a lot)   // in the database, for everyone

// The four classes, each split into paths named after the job they end in (4th job). A run
// counts for the path its job is on. 1st-job runs come before the path is picked, and play
// the same whichever way the player goes, so they count for every path of their class.
// Beginners (and any job not listed here) are kept but not shown.
export const CLASSES = {
  Warrior: {
    first: ["Swordman", "Warrior"],
    paths: {
      "Hero": ["Fighter", "Crusader", "Hero"],
      "Paladin": ["Page", "White Knight", "Paladin"],
      "Dark Knight": ["Spearman", "Dragon Knight", "Dark Knight"],
    },
  },
  Magician: {
    first: ["Magician"],
    paths: {
      "Arch Mage (Fire, Poison)": ["Wizard (Fire,Poison)", "Mage (Fire,Poison)", "Arch Mage (Fire,Poison)"],
      "Arch Mage (Ice, Lightning)": ["Wizard (Ice,Lightning)", "Mage (Ice,Lightning)", "Arch Mage (Ice,Lightning)"],
      "Bishop": ["Cleric", "Priest", "Bishop"],
    },
  },
  Archer: {
    first: ["Archer", "Bowman"],
    paths: {
      "Bowmaster": ["Hunter", "Ranger", "Bowmaster"],
      "Marksman": ["Crossbowman", "Sniper", "Marksman"],
    },
  },
  Thief: {
    first: ["Rogue", "Thief"],
    paths: {
      "Night Lord": ["Assassin", "Hermit", "Night Lord"],
      "Shadower": ["Bandit", "Chief Bandit", "Shadower"],
    },
  },
};

// Every job the overlay can read from the status bar (its JOBS list). Anything else isn't a job.
const OTHER_JOBS = ["Beginner", "Pirate", "Brawler", "Marauder", "Buccaneer", "Gunslinger", "Outlaw", "Corsair",
  "Warrior", "Bowman", "Thief"];
export const KNOWN_JOBS = new Set([...OTHER_JOBS, ...Object.values(CLASSES).flatMap((c) => [...c.first, ...Object.values(c.paths).flat()])]);

// EXP needed to finish each level in MapleStory Classic World (index 0 = level 1): the same
// table as the overlay's (maple_exp.py), so a run can't claim more EXP than its levels hold.
const EXP_TABLE = [
  15, 34, 57, 92, 135, 372, 560, 840, 1242, 1716, 2360, 3216, 4200, 5460, 7050, 8840, 11040, 13716, 16680,
  20216, 24402, 28980, 34320, 40512, 47216, 54900, 63666, 73080, 83720, 95700, 108480, 122760, 138666,
  155540, 174216, 194832, 216600, 240500, 266682, 294216, 324240, 356916, 391160, 428280, 468450, 510420,
  555680, 604416, 655200, 709716, 748608, 789631, 832902, 878545, 926689, 977471, 1031036, 1087536,
  1147132, 1209994, 1276301, 1346242, 1420016, 1497832, 1579913, 1666492, 1757815, 1854143, 1955750,
  2062925, 2175973, 2295216, 2420993, 2553663, 2693603, 2841212, 2996910, 3161140, 3334370, 3517093,
  3709829, 3913127, 4127566, 4353756, 4592341, 4844001, 5109452, 5389449, 5684790, 11938059, 25069923,
  52646838, 110558359, 232172553, 487562361, 1023880958, 2150150011, 4515315023, 9482161548
];

/** Could a run from level `start` to `end` have gained `exp`? At most all of those levels'
 * EXP (with one level to spare, in case the level was read a moment late), and at least
 * half of the whole levels in between. Past the table's end: can't tell, so yes. */
export function expPossible(start, end, exp) {
  if (end + 1 > EXP_TABLE.length) return true;
  let most = EXP_TABLE[end], least = 0;
  for (let level = start; level <= end; level++) {
    most += EXP_TABLE[level - 1];
    if (level > start && level < end) least += EXP_TABLE[level - 1];
  }
  return exp <= most && exp >= least / 2;
}

/** The jobs whose runs count for a class and path (no path: the whole class), or null. */
export function jobsFor(name, path) {
  const c = Object.hasOwn(CLASSES, name) ? CLASSES[name] : null;
  if (!c) return null;
  if (!path) return [...c.first, ...Object.values(c.paths).flat()];
  return Object.hasOwn(c.paths, path) ? [...c.first, ...c.paths[path]] : null;
}

const PARTNER_SOURCE = "https://mcwo.app";
const PARTNER_CREDIT = "Data from Maple Classic Exp Overlay players (https://mcwo.app). Please credit mcwo.app.";

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const now = clock(request, url);
    try {
      if (request.method === "OPTIONS" && url.pathname.startsWith("/api/")) {
        return corsPreflight();
      }
      if (url.pathname === "/api/health" || url.pathname === "/api/report") {
        return await handleReports(request, env, now, url, { connectionCode, connectionPart });
      }
      if (request.method === "POST" && url.pathname === "/api/runs") return await postRuns(request, env, now);
      if (request.method === "GET" && url.pathname === "/api/stats") return await getStats(url, env, ctx, now);
      if (request.method === "GET" && url.pathname === "/api/ladder") return await getLadder(url, env, ctx, now);
      if (request.method === "GET" && url.pathname === "/api/options") {
        return await cached(url, env, ctx, "options", KEEP.options, () => getOptions(env, now));
      }
      // (/api/latest is what pages open before 2026-10-19 ask for; it's a static file now, see public/api/latest)
      if (request.method === "GET" && (url.pathname === "/api/recent" || url.pathname === "/api/latest")) {
        return await cached(url, env, ctx, "latest", KEEP.latest, () => getLatest(env, now));
      }
      // Partner API (v1): same numbers as the site, slim payloads for third-party apps.
      if (request.method === "GET" && url.pathname === "/api/v1/maps") {
        return await partnerFrom(getStats(url, env, ctx, now), (body) => partnerMaps(body, url));
      }
      if (request.method === "GET" && url.pathname === "/api/v1/ladder") {
        return await partnerFrom(getLadder(url, env, ctx, now), partnerLadder);
      }
      if (request.method === "GET" && url.pathname === "/api/v1/options") {
        return await partnerFrom(
          cached(url, env, ctx, "options", KEEP.options, () => getOptions(env, now)), partnerOptions);
      }
      if (request.method === "GET" && url.pathname === "/api/v1/recent") {
        return await partnerFrom(
          cached(url, env, ctx, "latest", KEEP.latest, () => getLatest(env, now)), partnerRecent);
      }
      if (request.method === "GET" && (url.pathname === "/api/v1" || url.pathname === "/api/v1/")) {
        return json({
          api: "v1", source: PARTNER_SOURCE, attribution: PARTNER_CREDIT, docs: `${PARTNER_SOURCE}/partners.html`,
          endpoints: {
            maps: "/api/v1/maps?class=&path=&level=&party=solo|party",
            ladder: "/api/v1/ladder?class=&path=&party=solo|party",
            options: "/api/v1/options",
            recent: "/api/v1/recent",
          },
        });
      }
      if (url.pathname.startsWith("/api/")) {
        return json({ error: "not found" }, ["GET", "POST"].includes(request.method) ? 404 : 405);
      }
      return env.ASSETS.fetch(request);
    } catch (e) {
      console.error(e);
      return json({ error: "something went wrong on the server" }, 500);
    }
  },
};

/** Strip chart-only fields from a /api/stats map row for partners. */
export function slimMapRow(m) {
  if (!m || typeof m !== "object") return null;
  return {
    map: m.map,
    exp_hr: m.exp_hr,
    exp_hr_low: m.exp_hr_low,
    exp_hr_high: m.exp_hr_high,
    mesos_hr: m.mesos_hr ?? null,
    pct_hr: m.pct_hr ?? null,
    hours_per_level: m.hours_per_level ?? null,
    players: m.players,
    runs: m.runs,
    hours: m.hours,
    levels: m.levels,
    widened: !!m.widened,
    by_party: Array.isArray(m.by_party) ? m.by_party : [],
  };
}

export function partnerMaps(body, url) {
  const level = parseInt(url.searchParams.get("level"), 10);
  return {
    api: "v1", source: PARTNER_SOURCE, attribution: PARTNER_CREDIT,
    class: body.class, path: body.path ?? null, level: Number.isFinite(level) ? level : null,
    party: body.party, min_players: body.min_players, newest_run: body.newest_run ?? null,
    maps: (body.maps || []).map(slimMapRow).filter(Boolean),
    early: (body.early || []).map(slimMapRow).filter(Boolean),
  };
}

export function partnerLadder(body) {
  return {
    api: "v1", source: PARTNER_SOURCE, attribution: PARTNER_CREDIT,
    class: body.class, path: body.path ?? null, party: body.party,
    band: body.band, min_players: body.min_players,
    bands: body.bands || [],
  };
}

export function partnerOptions(body) {
  return { api: "v1", source: PARTNER_SOURCE, attribution: PARTNER_CREDIT, ...body };
}

export function partnerRecent(body) {
  return { api: "v1", source: PARTNER_SOURCE, attribution: PARTNER_CREDIT, runs: body.runs || [] };
}

async function partnerFrom(upstream, shape) {
  const res = await upstream;
  if (res.status !== 200) return res;
  const body = await res.json();
  const out = json(shape(body, null), 200);
  const cc = res.headers.get("cache-control");
  if (cc) out.headers.set("cache-control", cc);
  out.headers.set("x-mcwo-attribution", PARTNER_CREDIT);
  return out;
}

// ---------------------------------------------------------------- receiving runs

// No map in the game looks like a web address, and the site shows map names, so a "map"
// like "free-mesos.com" or "www.x" is turned down.
const WEB_ADDRESS = /:\/\/|www\.|\.(com|net|org|gg|io|co|me|ly|tv|xyz|ru|cn|info|biz|link|site|shop|app|dev|to|cc|top)\b/i;

function text(value, max) {
  if (typeof value !== "string") return null;
  const t = value.replace(/[^\p{L}\p{N} '().,:&\/-]/gu, " ").replace(/\s+/g, " ").trim();
  return t && t.length <= max ? t : null;
}

function inRange(value, lo, hi, integer = false) {
  return typeof value === "number" && Number.isFinite(value) && value >= lo && value <= hi &&
    (!integer || Number.isInteger(value)) ? value : null;
}

function minutes(started) {          // "2026-10-12T14:05" -> minutes since 1970 (as if UTC)
  return Date.parse(started + ":00Z") / 60000;
}

/** A plausible run, cleaned up, or a reason it isn't. */
export function checkRun(r, now = Date.now()) {
  if (!r || typeof r !== "object") return { why: "not a run" };
  const map = text(r.map_read ?? r.map, 60);
  const job = text(r.class, 40);
  const started = typeof r.started === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(r.started)
    ? r.started : null;
  const levelStart = inRange(r.level_start, 1, 250, true);
  const levelEnd = inRange(r.level_end, 1, 250, true);
  const party = inRange(r.party, 1, 6, true);
  const seconds = inRange(r.seconds, ...RUN_SECONDS);
  const exp = inRange(r.exp, 0, 1e11);
  const mesos = inRange(r.mesos ?? 0, -1e10, 1e11);
  const mesosSeconds = inRange(r.mesos_seconds ?? 0, 0, RUN_SECONDS[1]);
  if (!map || !job || !started || !minutes(started)) return { why: "missing map, class or start time" };
  if (!KNOWN_JOBS.has(job)) return { why: "not a job" };
  if (WEB_ADDRESS.test(map) || WEB_ADDRESS.test(job)) return { why: "not a map name" };
  const named = canonicalMap(map);                   // the game's spelling (fixes OCR slips like "ist" for "1st")
  if (named.town) return { why: "town" };            // nobody farms in a town: the overlay was on a stale name
  if (named.junk) return { why: "not a map name" };    // a menu or a misread with no real word in it
  const ago = now / 60000 - minutes(started);       // (the overlay sends its local time)
  if (ago > DAYS_BACK * 1440 || ago < -2 * 1440) return { why: "start time out of range" };
  if (!levelStart || !levelEnd || !party) return { why: "bad level or party size" };
  if (levelEnd < levelStart || levelEnd - levelStart > 10) return { why: "impossible level change" };
  if (!seconds) return { why: "not a 15-minute run" };
  if (exp === null || mesos === null || mesosSeconds === null) return { why: "bad EXP or mesos" };
  const expHr = exp / seconds * 3600;
  if (expHr > 5e6 || !expPossible(levelStart, levelEnd, exp)) return { why: "impossible EXP for those levels" };
  const cap = pctCap(levelStart), pct = pctPerHour({ exp_hr: expHr, level_start: levelStart, level_end: levelEnd });
  if (cap && pct > cap) return { why: "impossible EXP for that level" };
  // Mesos/hr only when the mesos were seen for at least 10 minutes of the run, and nothing about them is odd.
  const trusted = mesos >= 0 && mesosSeconds <= seconds && !oldMesos(r.version, now);
  const mesosHr = trusted && mesosSeconds >= 600 ? mesos / mesosSeconds * 3600 : null;
  if (mesosHr !== null && mesosHr > MAX_MESOS_HR) return { why: "impossible mesos/hr" };
  return {
    run: {
      map: named.name, unlisted: !named.listed, job, started, level_start: levelStart, level_end: levelEnd, party, exp, seconds,
      exp_hr: expHr, mesos, mesos_seconds: mesosSeconds, mesos_hr: mesosHr,
      version: text(r.version, 24) || "?",
    },
  };
}

/** A code for a connection on one day. With the IP_SALT secret (set on the live site) it
 * can't be worked back to the internet address by trying them all. */
async function connectionCode(ip, day, salt) {
  const data = new TextEncoder().encode(`${ip} ${day}`);
  let digest;
  if (salt) {
    const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(salt), { name: "HMAC", hash: "SHA-256" },
      false, ["sign"]);
    digest = await crypto.subtle.sign("HMAC", key, data);
  } else {
    digest = await crypto.subtle.digest("SHA-256", data);     // (only when testing locally)
  }
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
}

/** The server's idea of "now": the real time, except that a LOCAL test server takes it from an X-Test-Now header
 * (milliseconds), so the tests can show what a day later looks like. Never honoured on the live site. */
function clock(request, url) {
  if (url.hostname === "localhost" || url.hostname === "127.0.0.1") {
    const t = Number(request.headers.get("x-test-now"));
    if (Number.isFinite(t) && t > 0) return t;
  }
  return Date.now();
}

/** The part of an internet address that stands for one connection: the whole of an IPv4 address, but
 * only the first 64 bits of an IPv6 one (one home gets billions of IPv6 addresses to rotate through). */
export function connectionPart(ip) {
  const mapped = /^::ffff:(\d{1,3}(?:\.\d{1,3}){3})$/i.exec(ip);
  if (mapped) return mapped[1];
  if (!ip.includes(":")) return ip;                  // IPv4 (or "?")
  const [head, tail] = ip.toLowerCase().split("::");
  const a = head ? head.split(":") : [], b = tail === undefined ? [] : tail ? tail.split(":") : [];
  const groups = tail === undefined ? a : [...a, ...Array(Math.max(0, 8 - a.length - b.length)).fill("0"), ...b];
  return groups.slice(0, 4).map((g) => g.padStart(4, "0")).join(":");
}

async function postRuns(request, env, now) {
  if (!(request.headers.get("content-type") || "").toLowerCase().startsWith("application/json")) {
    return json({ error: "send JSON" }, 415);
  }
  if (Number(request.headers.get("content-length") || 0) > MAX_BODY) return json({ error: "too big" }, 413);
  let body;
  try {
    const raw = await request.text();
    if (raw.length > MAX_BODY) return json({ error: "too big" }, 413);
    body = JSON.parse(raw);
  } catch {
    return json({ error: "not JSON" }, 400);
  }
  const install = typeof body?.install === "string" && /^[0-9a-f]{16}$/.test(body.install) ? body.install : null;
  if (!install || !Array.isArray(body.runs)) return json({ error: "expected {install, runs: [...]}" }, 400);

  // Everything that can be checked from the runs alone is checked BEFORE the database is touched, so a flood of
  // garbage costs one cheap call and no reads or writes.
  const checked = body.runs.slice(0, MAX_PER_REQUEST).map((raw) => {
    const started = typeof raw?.started === "string" ? raw.started.slice(0, 16) : null;
    return { started, ...checkRun(raw, now) };
  });
  const results = checked.map((c) => (c.run ? null : { started: c.started, ok: false, why: c.why }));
  if (!checked.some((c) => c.run)) return json({ received: 0, results }, 200, false);

  const day = new Date(now).toISOString().slice(0, 10);
  const ipHash = await connectionCode(connectionPart(request.headers.get("CF-Connecting-IP") || "?"), day, env.IP_SALT);
  const counts = await env.DB.prepare(
    `SELECT (SELECT COUNT(*) FROM runs WHERE install = ?1 AND received_day = ?2) AS mine,
            (SELECT COUNT(*) FROM runs WHERE ip_hash = ?3 AND received_day = ?2) AS ip,
            (SELECT COUNT(DISTINCT install) FROM runs WHERE ip_hash = ?3 AND received_day = ?2) AS installs,
            (SELECT COUNT(*) FROM runs WHERE ip_hash = ?3 AND received_day = ?2 AND install = ?1) AS here,
            (SELECT banned FROM installs WHERE install = ?1) AS banned,
            (SELECT COUNT(*) FROM installs WHERE install = ?1) AS known,
            COALESCE((SELECT runs FROM daily WHERE day = ?2), 0) AS day_runs,
            COALESCE((SELECT new_installs FROM daily WHERE day = ?2), 0) AS day_new`
  ).bind(install, day, ipHash).first();
  const refuse = (why, retry = false) => checked.forEach((c, i) => {
    if (c.run) results[i] = { started: c.started, ok: false, why, ...(retry ? { retry: true } : {}) };
  });
  if (counts.banned) { refuse("refused"); return json({ received: 0, results }, 200, false); }
  // A flood of brand-new installs (made-up players) or of runs can't use up the database's daily writes: over the
  // limit, new installs wait for tomorrow (known ones carry on).
  if (!counts.known && (counts.day_new >= MAX_NEW_INSTALLS_DAY || counts.day_runs >= MAX_RUNS_DAY)) {
    refuse("too many new players today", true);
    return json({ received: 0, results }, 200, false);
  }
  let mine = counts.mine, fromIp = counts.ip;
  // A connection that has already shared for a few copies of the overlay today can't add
  // more (made-up players to push a map past MIN_PLAYERS). They're sent again tomorrow.
  const crowded = counts.installs >= MAX_INSTALLS_PER_IP_DAY && !counts.here;
  // This install's runs, to catch runs that overlap in time (one person can't farm two at once), and the maps it
  // has farmed today (nobody farms a dozen maps in a day).
  const { results: earlier } = await env.DB.prepare(
    "SELECT started, seconds, map, received_day FROM runs WHERE install = ?1 ORDER BY started DESC LIMIT 2000"
  ).bind(install).all();
  const taken = earlier.map((e) => ({ at: minutes(e.started), long: e.seconds / 60 }));
  const mapsToday = new Set(earlier.filter((e) => e.received_day === day).map((e) => e.map));
  // Two runs overlap if each starts before the other ends. Start times are to the minute, and
  // the next run starts as one ends, so a minute's leeway.
  const overlaps = (a, b) => a.at < b.at + b.long - 1 && b.at < a.at + a.long - 1;

  let saved = 0;
  for (let i = 0; i < checked.length; i++) {
    const { run, started } = checked[i];
    if (!run) continue;
    const at = { at: minutes(run.started), long: run.seconds / 60 };
    if (taken.some((t) => t.at === at.at)) {
      results[i] = { started, ok: true, already: true };       // sent before: fine
      continue;
    }
    if (taken.some((t) => overlaps(t, at))) {
      results[i] = { started, ok: false, why: "overlaps another run" };
      continue;
    }
    if (!mapsToday.has(run.map) && mapsToday.size >= MAX_MAPS_PER_INSTALL_DAY) {
      results[i] = { started, ok: false, why: "too many different maps in a day" };
      continue;
    }
    if (mine >= MAX_PER_INSTALL_DAY || fromIp >= MAX_PER_IP_DAY || crowded) {
      results[i] = { started, ok: false, why: crowded ? "too many players on one connection today" : "too many runs today",
        retry: true };
      continue;
    }
    const insert = await env.DB.prepare(
      `INSERT OR IGNORE INTO runs (received, received_day, install, ip_hash, started, map, job, level_start,
         level_end, party, exp, seconds, exp_hr, mesos, mesos_seconds, mesos_hr, version)
       VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17)`
    ).bind(new Date(now).toISOString(), day, install, ipHash, run.started, run.map, run.job, run.level_start,
      run.level_end, run.party, run.exp, run.seconds, run.exp_hr, run.mesos, run.mesos_seconds,
      run.mesos_hr, run.version).run();
    if (insert.meta.changes) {
      await addToSummaries(env, run, install);
      if (run.unlisted) await noteForReview(env, run.map, new Date(now).toISOString(), day);
      saved++;
    }
    taken.push(at);
    mapsToday.add(run.map);
    mine++;
    fromIp++;
    results[i] = { started, ok: true };
  }
  if (saved) {                                       // the install's record (its age and run count are what vet it)
    await env.DB.batch([
      env.DB.prepare("INSERT OR IGNORE INTO installs (install, first_seen, runs, banned) VALUES (?1, ?2, 0, 0)")
        .bind(install, new Date(now).toISOString()),
      env.DB.prepare("UPDATE installs SET runs = runs + ?2 WHERE install = ?1").bind(install, saved),
      env.DB.prepare(`INSERT INTO daily (day, runs, new_installs) VALUES (?1, ?2, ?3)
                      ON CONFLICT (day) DO UPDATE SET runs = runs + excluded.runs, new_installs = new_installs + excluded.new_installs`)
        .bind(day, saved, counts.known ? 0 : 1),
    ]);
  }
  return json({ received: results.filter((r) => r.ok).length, results }, 200, false);
}

/** A map name that isn't on the game's list (an event map, a new map, or a misread) is kept as
 * it is and noted here, so it can be looked at: SELECT from map_review (the query is in schema.sql).
 * Capped (in all, and per day), so nobody can fill it up with made-up names. */
function noteForReview(env, map, at, day) {
  return env.DB.prepare(
    `INSERT OR IGNORE INTO map_review (map, first_seen) SELECT ?1, ?2
     WHERE (SELECT COUNT(*) FROM map_review) < ?3 AND (SELECT COUNT(*) FROM map_review WHERE first_seen >= ?4) < ?5`
  ).bind(map, at, MAX_REVIEW, day, MAX_REVIEW_PER_DAY).run();
}

/** Adds a newly saved run to the small summary tables (see schema.sql), so the site can
 * count players, maps and hours without reading every run. */
function addToSummaries(env, run, install) {
  return env.DB.batch([
    env.DB.prepare("INSERT INTO map_players (map, install) VALUES (?1, ?2) ON CONFLICT DO NOTHING").bind(run.map, install),
    env.DB.prepare(`INSERT INTO job_players (job, install, runs) VALUES (?1, ?2, 1)
                    ON CONFLICT (job, install) DO UPDATE SET runs = runs + 1`).bind(run.job, install),
    env.DB.prepare(`INSERT INTO totals (id, runs, seconds) VALUES (1, 1, ?1)
                    ON CONFLICT (id) DO UPDATE SET runs = runs + 1, seconds = seconds + excluded.seconds`).bind(run.seconds),
  ]);
}

// ---------------------------------------------------------------- the numbers

export function median(values) {
  const v = [...values].sort((a, b) => a - b);
  if (!v.length) return null;
  const mid = v.length >> 1;
  return v.length % 2 ? v[mid] : (v[mid - 1] + v[mid]) / 2;
}

function quantile(values, q) {
  const v = [...values].sort((a, b) => a - b);
  if (!v.length) return null;
  const pos = (v.length - 1) * q, lo = Math.floor(pos), hi = Math.ceil(pos);
  return v[lo] + (v[hi] - v[lo]) * (pos - lo);
}

/** One number per player: the median of their runs, so nobody counts more than once. */
function perPlayer(runs, field) {
  const by = new Map();
  for (const r of runs) {
    if (r[field] === null || r[field] === undefined) continue;
    if (!by.has(r.install)) by.set(r.install, []);
    by.get(r.install).push(r[field]);
  }
  return [...by.values()].map(median);
}

/** Each player's EXP/hr, the level they were at (the median of their runs) and 1 if they're a new player (only
 * when the page asked for them), lowest EXP/hr first: [[exp_hr, level, new], ...]. No ids. */
function playerDots(runs) {
  const by = new Map();
  for (const r of runs) {
    if (!by.has(r.install)) by.set(r.install, { exp: [], level: [], fresh: r.confirmed === 0 });
    by.get(r.install).exp.push(r.exp_hr);
    by.get(r.install).level.push(r.level_start);
  }
  return [...by.values()].map((p) => [median(p.exp), Math.round(median(p.level)), p.fresh ? 1 : 0]).sort((a, b) => a[0] - b[0]);
}

/** 3 significant figures (74,213 -> 74,200): plenty for a dot on a chart. */
const roughly = (n) => Number(n.toPrecision(3));

/**
 * For one map: drop runs far from what players typically get there (outliers), then the
 * typical EXP/hr and mesos/hr of what's left. `ranked` once MIN_PLAYERS players remain.
 * `dots` is each player's typical EXP/hr (no IDs), and `unusual` the same for the runs left
 * out, so the site can show every player and what was dropped.
 */
export function summarize(runs) {
  const typical = median(perPlayer(runs, "exp_hr"));
  if (typical === null) return null;
  const usual = (r) => r.exp_hr >= BAND[0] * typical && r.exp_hr <= BAND[1] * typical;
  const kept = runs.filter(usual), left = runs.filter((r) => !usual(r));
  const players = new Set(kept.map((r) => r.install)).size;
  const exp = perPlayer(kept, "exp_hr"), mesos = perPlayer(kept, "mesos_hr"), pct = perPlayer(kept, "pct_hr");
  const dots = playerDots(kept), unusual = playerDots(left);
  return {
    ranked: players >= MIN_PLAYERS,
    exp_hr: Math.round(median(exp)),
    exp_hr_low: Math.round(quantile(exp, 0.25)),
    exp_hr_high: Math.round(quantile(exp, 0.75)),
    mesos_hr: mesos.length ? Math.round(median(mesos)) : null,
    pct_hr: pct.length ? Math.round(median(pct) * 100) / 100 : null,           // % of a level per hour
    pct_hr_low: pct.length ? Math.round(quantile(pct, 0.25) * 100) / 100 : null,
    pct_hr_high: pct.length ? Math.round(quantile(pct, 0.75) * 100) / 100 : null,
    hours_per_level: pct.length && median(pct) > 0 ? Math.round(1000 / median(pct)) / 10 : null,
    players,
    runs: kept.length,
    hours: Math.round(kept.reduce((sum, r) => sum + r.seconds, 0) / 360) / 10,
    outliers: left.length,
    dots: dots.map((d) => roughly(d[0])),
    dot_levels: dots.map((d) => d[1]),                   // the level of each dot, in the same order
    new_players: dots.filter((d) => d[2]).length,        // players who don't count yet (only with "include new players")
    dot_new: dots.map((d) => d[2]),
    unusual: unusual.map((d) => roughly(d[0])),
    unusual_levels: unusual.map((d) => d[1]),
    unusual_new: unusual.map((d) => d[2]),
  };
}

async function getStats(url, env, ctx, now) {
  const name = url.searchParams.get("class"), path = url.searchParams.get("path") || null;
  const jobs = jobsFor(name, path);
  const level = parseInt(url.searchParams.get("level"), 10);
  // Solo, or in a party of any size: parties of 2 to 6 are put together so maps fill in sooner.
  const asked = url.searchParams.get("party") || "solo";
  const party = asked === "solo" || asked === "1" ? "solo" : asked === "party" || /^[2-6]$/.test(asked) ? "party" : null;
  if (!jobs || !(level >= 1 && level <= 250) || !party) {
    return json({ error: "give class (and path), level (1-250) and party (solo or party)" }, 400);
  }
  const withNew = wantsNew(url);
  return cached(url, env, ctx, `stats3/${name}/${path || ""}/${level}/${party}${withNew ? "/new" : ""}`, KEEP.stats,
    () => findMaps(env, name, path, jobs, level, party, now, withNew));
}

/** Who counts: not banned, seen for VET_HOURS, and at least VET_RUNS runs. The SQL pieces: `from` joins the
 * installs table, `where` is the condition, `args` its two values (put at the numbers given). */
const vetting = (now, withNew = false) => withNew ? { cutoff: "9999", runs: 0 }
  : { cutoff: new Date(now - VET_HOURS * 3600e3).toISOString(), runs: VET_RUNS };

/** "Include new players" (new=1): everyone who isn't banned. Their runs are marked (confirmed = 0) so the page can say
 * so, and a map name that isn't on the game's list never comes from them. */
const wantsNew = (url) => url.searchParams.get("new") === "1";
const onlyListed = (rows) => rows.filter((r) => r.confirmed || isListed(r.map));
const VETTED = (n1, n2) => `i.banned = 0 AND i.runs >= ?${n1} AND i.first_seen <= ?${n2}`;

/** In how many different days were these (listed-or-not) maps shared by players who count? */
async function daysSeen(env, maps, now) {
  const v = vetting(now), days = new Map();
  for (let i = 0; i < maps.length; i += 80) {
    const part = maps.slice(i, i + 80);
    const { results } = await env.DB.prepare(
      `SELECT r.map, COUNT(DISTINCT r.received_day) AS days FROM runs r JOIN installs i ON i.install = r.install
       WHERE ${VETTED(1, 2)} AND r.map IN (${part.map((_, j) => `?${j + 3}`).join(", ")}) GROUP BY r.map`
    ).bind(v.runs, v.cutoff, ...part).all();
    for (const r of results) days.set(r.map, r.days);
  }
  return days;
}

/** A map name that is on the game's list shows as soon as it has numbers; one that isn't (an event map, a new
 * map, or made-up text) shows only when ranked AND shared on UNLISTED_DAYS different days. */
function visibleMap(f, ranked, days) {
  return isListed(f.map) || (ranked && (days.get(f.map) || 0) >= UNLISTED_DAYS);
}

/** % of its level's EXP a run gets per hour (its own level, or the middle of two if it levelled):
 * comparable between levels, which is what lets a map's numbers come from a few levels either side. */
export function pctPerHour(r) {
  const need = (EXP_TABLE[r.level_start - 1] + EXP_TABLE[Math.min(r.level_end, r.level_start + 10) - 1]) / 2;
  return need > 0 ? r.exp_hr / need * 100 : null;
}

const soloOf = (r) => r.party === 1;

/** [0, 9, 11] for "0.9.11-beta"; [] when it isn't a version. */
const versionKey = (v) => (typeof v === "string" && v.match(/^\d+(\.\d+)*/)?.[0].split(".").map(Number)) || [];

function olderThan(a, b) {
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    if ((a[i] || 0) !== (b[i] || 0)) return (a[i] || 0) < (b[i] || 0);
  }
  return false;
}

/** A run from an overlay that counted shop and trade mesos, received once that rule is on. */
const oldMesos = (version, now) => MESOS_RULE_FROM !== "" && now >= Date.parse(MESOS_RULE_FROM)
  && olderThan(versionKey(version), versionKey(MESOS_VERSION));

const recentCutoff = (now) => new Date(now - RECENT_DAYS * 864e5).toISOString();

/**
 * The best maps for a class and level. Each map's numbers come from the players at the fewest
 * levels either side of yours (RADII: 3, then 5) that give it MIN_PLAYERS players, so a
 * busy level stays precise and a quiet one still fills in (the map says how wide it had to go).
 * Everything the page shows, including EXP/hr by party size and by level, is
 * worked out here from the same rows: one request and one query.
 */
async function findMaps(env, name, path, jobs, level, party, now, withNew = false) {
  const widest = RADII[RADII.length - 1], v = vetting(now, withNew), real = vetting(now), n = jobs.length;
  const { results } = await env.DB.prepare(
    `SELECT r.install, r.map, r.seconds, r.exp_hr, r.mesos_hr, r.level_start, r.level_end, r.party, r.started, r.received,
            (i.runs >= ?${n + 5} AND i.first_seen <= ?${n + 6}) AS confirmed
     FROM runs r JOIN installs i ON i.install = r.install
     WHERE ${VETTED(n + 3, n + 4)} AND r.job IN (${jobs.map((_, i) => `?${i + 3}`).join(", ")})
       AND r.level_start BETWEEN ?1 AND ?2 AND r.received >= ?${n + 7}`
  ).bind(level - widest, level + widest, ...jobs, v.runs, v.cutoff, real.runs, real.cutoff, recentCutoff(now)).all();
  const rows = onlyListed(results).map((r) => ({ ...r, pct_hr: pctPerHour(r) }));
  const mine = rows.filter((r) => (party === "solo") === soloOf(r));
  const theirs = rows.filter((r) => (party === "solo") !== soloOf(r));
  const grouped = (list) => {
    const by = new Map();
    for (const r of list) {
      if (!by.has(r.map)) by.set(r.map, []);
      by.get(r.map).push(r);
    }
    return by;
  };
  const myMaps = grouped(mine), theirMaps = grouped(theirs);
  const within = (list, radius) => list.filter((r) => Math.abs(r.level_start - level) <= radius);

  const found = [];
  for (const [map, list] of myMaps) {
    let used = widest, s = null;
    for (const radius of RADII) {                        // the narrowest range that has enough players
      const part = within(list, radius);
      const got = part.length ? summarize(part) : null;
      if (got && (got.ranked || radius === widest)) { s = got; used = radius; break; }
    }
    if (!s) continue;
    // EXP/hr for each party size (solo, 2 to 6) at the same levels, from both sides (solo and party rows)
    const both = [...within(list, used), ...within(theirMaps.get(map) || [], used)];
    const byParty = [];
    for (let size = 1; size <= 6; size++) {
      const each = both.filter((r) => r.party === size);
      const got = each.length ? summarize(each) : null;
      if (got) byParty.push({ party: size, players: got.players, exp_hr: got.exp_hr });
    }
    const levels = [];
    for (let l = level - widest; l <= level + widest; l++) {
      const here = list.filter((r) => r.level_start === l);
      const each = perPlayer(here, "exp_hr");
      if (each.length) levels.push({ level: l, players: each.length, exp_hr: roughly(median(each)) });
    }
    found.push({
      map, ...s, levels: [Math.max(1, level - used), level + used], widened: used > RADII[0],
      by_party: byParty, by_level: levels,
    });
  }
  // Ranked maps, best first. Maps with fewer players are early data: shown, but not ranked, most players first.
  // Only players who count are in any of this (see vetting), and a map name that isn't on the game's list shows
  // only once it's ranked and was shared on UNLISTED_DAYS different days.
  const odd = found.filter((f) => !isListed(f.map)).map((f) => f.map);
  const days = odd.length ? await daysSeen(env, odd, now) : new Map();
  const maps = [], early = [];
  for (const { ranked, ...f } of found) {
    if (!visibleMap(f, ranked, days)) continue;
    (ranked ? maps : early).push(f);
  }
  maps.sort((a, b) => a.widened - b.widened || b.exp_hr - a.exp_hr);      // those at your level first
  maps.length = Math.min(maps.length, MAX_MAPS);        // (the answer's size and cost stay bounded)
  early.sort((a, b) => b.players - a.players || b.runs - a.runs || b.exp_hr - a.exp_hr);
  const newest = rows.reduce((m, r) => (r.received > m ? r.received : m), "");
  return json({
    class: name, path, jobs, party, levels: [Math.max(1, level - RADII[0]), level + RADII[0]], ranges: RADII,
    min_players: MIN_PLAYERS, with_new: withNew, maps, early: early.slice(0, MAX_EARLY), maps_waiting_for_players: early.length,
    newest_run: newest ? `${newest.slice(0, 16)}Z` : null,
  });
}

// ---------------------------------------------------------------- the leveling ladder

const LADDER_BAND = 5;                       // levels in a band of the ladder
const LADDER_ROWS = 20000;            // the most runs read for one ladder (a limit on what one answer costs)

/** For a class (and path), solo or party: the best maps in each band of 5 levels. */
async function getLadder(url, env, ctx, now) {
  const name = url.searchParams.get("class"), path = url.searchParams.get("path") || null;
  const jobs = jobsFor(name, path);
  const asked = url.searchParams.get("party") || "solo";
  const party = asked === "solo" || asked === "1" ? "solo" : asked === "party" || /^[2-6]$/.test(asked) ? "party" : null;
  if (!jobs || !party) return json({ error: "give class (and path) and party (solo or party)" }, 400);
  const withNew = wantsNew(url);
  return cached(url, env, ctx, `ladder/${name}/${path || ""}/${party}${withNew ? "/new" : ""}`, KEEP.ladder, async () => {
    const v = vetting(now, withNew), real = vetting(now), n = jobs.length;
    const { results } = await env.DB.prepare(
      `SELECT r.install, r.map, r.seconds, r.exp_hr, r.mesos_hr, r.level_start, r.level_end,
              (i.runs >= ?${n + 4} AND i.first_seen <= ?${n + 5}) AS confirmed
       FROM runs r JOIN installs i ON i.install = r.install
       WHERE ${VETTED(n + 2, n + 3)} AND r.job IN (${jobs.map((_, i) => `?${i + 2}`).join(", ")})
         AND r.party ${party === "solo" ? "= 1" : ">= 2"} AND r.received >= ?${n + 6}
       ORDER BY r.received DESC LIMIT ?1`
    ).bind(LADDER_ROWS, ...jobs, v.runs, v.cutoff, real.runs, real.cutoff, recentCutoff(now)).all();
    const bands = new Map();
    for (const r of onlyListed(results)) {
      const from = Math.floor(r.level_start / LADDER_BAND) * LADDER_BAND;
      if (!bands.has(from)) bands.set(from, new Map());
      const maps = bands.get(from);
      if (!maps.has(r.map)) maps.set(r.map, []);
      maps.get(r.map).push({ ...r, pct_hr: pctPerHour(r) });
    }
    const out = [];
    for (const from of [...bands.keys()].sort((a, b) => a - b)) {
      const ranked = [];
      for (const [map, list] of bands.get(from)) {
        const s = summarize(list);
        if (s?.ranked) ranked.push({ map, exp_hr: s.exp_hr, pct_hr: s.pct_hr, mesos_hr: s.mesos_hr, players: s.players, ...(withNew ? { new_players: s.new_players } : {}) });
      }
      ranked.sort((a, b) => b.exp_hr - a.exp_hr);
      if (ranked.length) out.push({ from, to: from + LADDER_BAND - 1, maps: ranked.slice(0, 3) });
    }
    const odd = [...new Set(out.flatMap((b) => b.maps.map((m) => m.map)))].filter((m) => !isListed(m));
    const days = odd.length ? await daysSeen(env, odd, now) : new Map();
    for (const b of out) b.maps = b.maps.filter((m) => visibleMap(m, true, days));
    return json({ class: name, path, party, band: LADDER_BAND, min_players: MIN_PLAYERS, with_new: withNew, bands: out.filter((b) => b.maps.length) });
  });
}

/**
 * The latest runs shared, newest first, for the bar at the top of the site. It's the front
 * page, so: one run per player (nobody fills it), only maps at least 2 players have shared
 * (a made-up map name can't be put there), only the classes the site shows, and not runs far
 * from what other players get on that map (solo or party, around that level). Rounded, and
 * nothing about who it was.
 */
async function getLatest(env, now) {
  const since = new Date(Date.now() - LATEST_DAYS * 864e5).toISOString().slice(0, 10);
  const shown = new Set(Object.keys(CLASSES).flatMap((name) => jobsFor(name)));
  const v = vetting(now);
  const { results: recent } = await env.DB.prepare(
    `SELECT r.install, r.received, r.map, r.job, r.level_start, r.party, r.exp_hr, r.mesos_hr
     FROM runs r JOIN installs i ON i.install = r.install
     WHERE ${VETTED(2, 3)} AND r.received >= ?1 ORDER BY r.received DESC, r.started DESC LIMIT 200`
  ).bind(since, v.runs, v.cutoff).all();                     // (runs are sent a few at a time)
  const picked = [], players = new Set();
  for (const r of recent) {                                  // (only maps on the game's list: it's the front page)
    if (!shown.has(r.job) || !isListed(r.map) || players.has(r.install)) continue;
    players.add(r.install);
    picked.push(r);
    if (picked.length >= LATEST * 2) break;
  }
  // What other players get on those maps, around those levels (to leave out unusual runs).
  const maps = [...new Set(picked.map((r) => r.map))];
  const levels = picked.map((r) => r.level_start);
  const { results: others } = maps.length ? await env.DB.prepare(
    `SELECT r.install, r.map, r.level_start, r.party, r.exp_hr FROM runs r JOIN installs i ON i.install = r.install
     WHERE ${VETTED(maps.length + 3, maps.length + 4)} AND r.map IN (${maps.map((_, i) => `?${i + 3}`).join(", ")})
       AND r.level_start BETWEEN ?1 AND ?2 AND r.received >= ?${maps.length + 5}`
  ).bind(Math.min(...levels) - LEVEL_WINDOW, Math.max(...levels) + LEVEL_WINDOW, ...maps, v.runs, v.cutoff,
         recentCutoff(now)).all() : { results: [] };
  const usual = (r) => {
    const like = others.filter((o) => o.map === r.map && (o.party >= 2) === (r.party >= 2) &&
      Math.abs(o.level_start - r.level_start) <= LEVEL_WINDOW);
    const each = perPlayer(like, "exp_hr");
    if (each.length < MIN_PLAYERS) return true;          // too few to tell (its EXP was checked when sent)
    const typical = median(each);
    return r.exp_hr >= BAND[0] * typical && r.exp_hr <= BAND[1] * typical;
  };
  const runs = picked.filter(usual).slice(0, LATEST).map((r) => ({
    map: r.map, job: r.job, level: r.level_start, party: r.party,
    exp_hr: roughly(r.exp_hr), mesos_hr: r.mesos_hr === null ? null : roughly(r.mesos_hr),
    shared: `${r.received.slice(0, 16)}Z`,                 // to the minute
  }));
  return json({ runs });
}

async function getOptions(env, now) {
  const v = vetting(now);
  const [{ results }, { results: [totals] }] = await env.DB.batch([
    env.DB.prepare(`SELECT j.job, j.install, j.runs FROM job_players j JOIN installs i ON i.install = j.install
                    WHERE ${VETTED(1, 2)}`).bind(v.runs, v.cutoff),
    env.DB.prepare(
      `SELECT COALESCE((SELECT runs FROM totals WHERE id = 1), 0) AS runs,
              ROUND(COALESCE((SELECT seconds FROM totals WHERE id = 1), 0) / 3600.0, 1) AS hours,
              (SELECT COUNT(DISTINCT j.install) FROM job_players j JOIN installs i ON i.install = j.install WHERE ${VETTED(1, 2)}) AS players,
              (SELECT COUNT(DISTINCT j.install) FROM job_players j JOIN installs i ON i.install = j.install WHERE i.banned = 0) AS players_all,
              (SELECT COUNT(DISTINCT m.map) FROM map_players m JOIN installs i ON i.install = m.install WHERE ${VETTED(1, 2)}) AS maps`)
      .bind(v.runs, v.cutoff),
  ]);
  const count = (jobs) => {
    const want = new Set(jobs), players = new Set();
    let runs = 0;
    for (const r of results) {
      if (want.has(r.job)) {
        players.add(r.install);
        runs += r.runs;
      }
    }
    return { runs, players: players.size };
  };
  const classes = Object.entries(CLASSES).map(([name, c]) => ({
    name, ...count(jobsFor(name)),
    paths: Object.entries(c.paths).map(([path, jobs]) => ({
      name: path, steps: [c.first[0], ...jobs], ...count(jobsFor(name, path)),   // steps: 1st job to 4th
    })),
  }));
  const { players_all, ...rest } = totals;
  return json({ classes, ...rest, players_new: Math.max(0, players_all - totals.players) });   // (kept, not shown yet)
}

// ---------------------------------------------------------------- helpers

function jsonHeaders(anySite = true) {
  const headers = { "content-type": "application/json; charset=utf-8", "x-content-type-options": "nosniff" };
  if (anySite) headers["access-control-allow-origin"] = "*";      // the numbers are public
  return headers;
}

function corsPreflight() {
  return new Response(null, {
    status: 204,
    headers: {
      "access-control-allow-origin": "*",
      "access-control-allow-methods": "GET, POST, OPTIONS",
      "access-control-allow-headers": "content-type",
      "access-control-max-age": "86400",
    },
  });
}

function json(data, status = 200, anySite = true) {
  return new Response(JSON.stringify(data), { status, headers: jsonHeaders(anySite) });
}

/**
 * An answer kept for a while, so the database isn't asked again for every visitor: first in
 * Cloudflare's cache where the visitor is (a minute at most), then in the database itself,
 * shared by every Cloudflare location, so each answer is worked out at most once every
 * `seconds` however many people visit, wherever they are. The key is made from the question
 * itself, so extra bits in the address can't skip the cache. (Not when testing locally,
 * unless CACHE_LOCALLY is set.)
 */
async function cached(url, env, ctx, key, seconds, make) {
  if (!env.CACHE_LOCALLY && (url.hostname === "localhost" || url.hostname === "127.0.0.1")) return make();
  const edge = caches.default;
  const id = new Request(`${url.origin}/__cache/${encodeURIComponent(key)}`);
  const hit = await edge.match(id);
  if (hit) return hit;
  const now = Date.now();
  const kept = await env.DB.prepare("SELECT body, at FROM answers WHERE key = ?1").bind(key).first();
  let body = kept && now - kept.at < seconds * 1000 ? kept.body : null;
  if (body === null) {
    const fresh = await make();
    if (fresh.status !== 200) return fresh;
    body = await fresh.text();
    ctx.waitUntil(env.DB.prepare("INSERT OR REPLACE INTO answers (key, body, at) VALUES (?1, ?2, ?3)")
      .bind(key, body, now).run());
  }
  const response = new Response(body, { headers: {
    ...jsonHeaders(), "cache-control": `public, max-age=${Math.min(EDGE_SECONDS, seconds)}` } });
  ctx.waitUntil(edge.put(id, response.clone()));
  return response;
}
