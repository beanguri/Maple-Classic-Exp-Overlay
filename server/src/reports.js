// Health reports (POST /api/health) and player reports (POST /api/report). Numbers and text only; nothing here is
// ever served back. Contract: the shared examples in tests/health_contract_v2.json.
//
// How the limits stay atomic: one env.DB.batch() (a transaction) runs, in order,
//   1. the INSERT of the report row, with a WHERE that checks every counter is under its cap;
//   2. one counter bump per limit, each only if the statement just before it changed a row.
// SQLite's changes() is the row count of the last statement, so if the INSERT in 1 was refused (0 rows) every
// bump after it is skipped too, and a refused request writes nothing. The answer comes from the INSERT's
// meta.changes: 1 = stored, 0 = over a limit (429).

const DAY = 86400000;
const KEEP_REPORTS_DAYS = 90;
const KEEP_LIMITS_DAYS = 3;
const CAPS = { healthConn: 3, healthSite: 5000, reportId: 3, reportConn: 3, reportSite: 200, purgeEvery: 100 };

const HEALTH_MAX_BYTES = 4096;
const REPORT_MAX_BYTES = 16384;

const HEALTH_KEYS = ["id", "version", "day", "os", "screens", "game", "capture", "tesseract", "seconds_tracking",
  "looks", "exp"];
const HEALTH_OPTIONAL = ["level", "map", "class", "mesos", "potions", "errors", "ms", "shadow", "ui", "shadow_mesos",
  "shadow_party", "shadow_why"];
const EXP_KEYS = ["read", "tesseract", "unclear", "not_found", "covered", "no_window", "percent_only"];
const PAIRS = {            // the optional "tried / read" counters and their exact keys
  level: ["read", "unclear"], map: ["read", "unread"], class: ["read", "unread"],
  mesos: ["read", "unclear"], potions: ["read", "unclear"],
};
const SHADOW_KEYS = ["agree", "disagree", "v2_only", "old_only", "neither", "skipped", "errors"];
// Added for 0.9.14 (all optional, so 0.9.13's reports stay valid). The counts are only range-checked here, and for
// ui and shadow_why also <= looks; no cross-sums, so a client counting slip can never lose a whole day.
const UI_KEYS = ["default", "ideal", "keyboard", "controller"];
const WHY_KEYS = ["not_found", "unclear"];
const MS_KEYS = ["look_avg", "look_p95", "v2_avg", "mesos_v2_avg", "party_v2_avg"];

const VERSION = /^\d+\.\d+\.\d+(-[a-z0-9.]+)?$/;
const HEX_ID = /^[0-9a-f]{16}$/;
const LINK = /http|www\.|discord\.gg|\.com\//i;

/** The limits for this request: the real ones, except that a LOCAL test server can lower them through
 * REPORTS_TEST (JSON such as {"healthSite": 8}). Production never sets it. */
function caps(env, url) {
  if (env.REPORTS_TEST && (url.hostname === "localhost" || url.hostname === "127.0.0.1")) {
    try { return { ...CAPS, ...JSON.parse(env.REPORTS_TEST) }; } catch { /* use the real ones */ }
  }
  return CAPS;
}

const isObject = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const isInt = (v, lo, hi) => Number.isInteger(v) && v >= lo && v <= hi;
const count = (v) => isInt(v, 0, 10_000_000);
const sameKeys = (o, keys) => isObject(o) && Object.keys(o).length === keys.length && keys.every((k) => Object.hasOwn(o, k));

/** The first bad field of a health report (as a name), or null if it is fine. `today` is "YYYY-MM-DD" (UTC). */
export function badHealthField(b, today) {
  if (!isObject(b)) return "body";
  for (const k of Object.keys(b)) if (!HEALTH_KEYS.includes(k) && !HEALTH_OPTIONAL.includes(k)) return k;
  for (const k of HEALTH_KEYS) if (!Object.hasOwn(b, k)) return k;

  if (typeof b.id !== "string" || !HEX_ID.test(b.id)) return "id";
  if (typeof b.version !== "string" || b.version.length > 20 || !VERSION.test(b.version)) return "version";
  if (!goodDay(b.day, today)) return "day";
  if (typeof b.os !== "string" || !/^[\x20-\x7e]{1,60}$/.test(b.os)) return "os";
  if (!Array.isArray(b.screens) || b.screens.length > 4 || !b.screens.every((s) => Array.isArray(s) && s.length === 3 &&
      isInt(s[0], 100, 20000) && isInt(s[1], 100, 20000) && (s[2] === null || isInt(s[2], 50, 500)))) return "screens";
  if (b.game !== null) {
    const g = b.game;
    if (!sameKeys(g, ["w", "h", "fullscreen", "gh"]) || !isInt(g.w, 100, 20000) || !isInt(g.h, 100, 20000) ||
        typeof g.fullscreen !== "boolean" ||
        !(g.gh === null || (typeof g.gh === "number" && g.gh >= 0 && g.gh <= 64 &&
          Math.abs(g.gh * 10 - Math.round(g.gh * 10)) < 1e-9))) return "game";
  }
  if (!["screen", "window", "region"].includes(b.capture)) return "capture";
  if (!["ok", "missing", "no_eng"].includes(b.tesseract)) return "tesseract";
  if (!isInt(b.seconds_tracking, 0, 86400)) return "seconds_tracking";
  if (!isInt(b.looks, 0, 200000)) return "looks";
  if (!sameKeys(b.exp, EXP_KEYS) || !EXP_KEYS.every((k) => count(b.exp[k]))) return "exp";
  const e = b.exp;
  if (e.read + e.tesseract + e.unclear + e.not_found + e.covered + e.no_window !== b.looks ||
      e.percent_only > e.read + e.tesseract) return "exp";

  for (const [name, keys] of Object.entries(PAIRS)) {
    if (Object.hasOwn(b, name) && (!sameKeys(b[name], keys) || !keys.every((k) => count(b[name][k])))) return name;
  }
  if (Object.hasOwn(b, "errors")) {
    const er = b.errors;
    if (!isObject(er) || Object.keys(er).length > 10 ||
        !Object.entries(er).every(([k, v]) => /^[A-Za-z]{1,40}$/.test(k) && count(v))) return "errors";
  }
  if (Object.hasOwn(b, "ms")) {
    const m = b.ms, ms = (v) => typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 10000;
    if (!isObject(m) || Object.keys(m).some((k) => !MS_KEYS.includes(k)) ||
        !ms(m.look_avg) || !ms(m.look_p95) ||
        MS_KEYS.slice(2).some((k) => Object.hasOwn(m, k) && !ms(m[k]))) return "ms";
  }
  if (Object.hasOwn(b, "shadow") && (!sameKeys(b.shadow, SHADOW_KEYS) || !SHADOW_KEYS.every((k) => count(b.shadow[k])))) {
    return "shadow";
  }
  for (const name of ["shadow_mesos", "shadow_party"]) {
    if (Object.hasOwn(b, name) && (!sameKeys(b[name], SHADOW_KEYS) || !SHADOW_KEYS.every((k) => count(b[name][k])))) return name;
  }
  for (const [name, keys] of [["ui", UI_KEYS], ["shadow_why", WHY_KEYS]]) {
    if (Object.hasOwn(b, name) && (!sameKeys(b[name], keys) || !keys.every((k) => isInt(b[name][k], 0, b.looks)))) return name;
  }
  return null;
}

/** A real date, not after today (UTC) and not more than 8 days before it. */
function goodDay(day, today) {
  if (typeof day !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(day)) return false;
  const t = Date.parse(day + "T00:00:00Z");
  if (!Number.isFinite(t) || new Date(t).toISOString().slice(0, 10) !== day) return false;
  const now = Date.parse(today + "T00:00:00Z");
  return t <= now && t >= now - 8 * DAY;
}

/** The first bad field of a player report, or null. */
export function badReportField(b) {
  if (!isObject(b)) return "body";
  for (const k of Object.keys(b)) if (!["id", "version", "message", "details"].includes(k)) return k;
  for (const k of ["id", "version", "message", "details"]) if (!Object.hasOwn(b, k)) return k;
  if (typeof b.id !== "string" || !HEX_ID.test(b.id)) return "id";
  if (typeof b.version !== "string" || b.version.length > 20 || !VERSION.test(b.version)) return "version";
  const message = typeof b.message === "string" ? b.message.trim() : "";
  if (!message || message.length > 2000 || LINK.test(message)) return "message";
  if (typeof b.details !== "string" || b.details.length > 12000) return "details";
  return null;
}

/** Reads the body, stopping at `max` bytes (the Content-Length is not trusted). Returns the text, or null if too big. */
async function readCapped(request, max) {
  if (Number(request.headers.get("content-length") || 0) > max) return null;
  if (!request.body) return "";
  const reader = request.body.getReader();
  const chunks = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.length;
    if (total > max) { await reader.cancel(); return null; }
    chunks.push(value);
  }
  const all = new Uint8Array(total);
  let at = 0;
  for (const c of chunks) { all.set(c, at); at += c.length; }
  return new TextDecoder().decode(all);
}

const reply = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: {
  "content-type": "application/json; charset=utf-8", "x-content-type-options": "nosniff" } });

/** Handles /api/health and /api/report. `tools` brings connectionCode and connectionPart from index.js. */
export async function handleReports(request, env, now, url, tools) {
  const isHealth = url.pathname === "/api/health";
  const host = url.hostname;
  // Only the real site: the pages.dev address can't be rate-limited, so it doesn't answer these.
  if (!(host === "mcwo.app" || host === "localhost" || host === "127.0.0.1")) return reply({ error: "not found" }, 404);
  if (request.method !== "POST") return reply({ error: "POST only" }, 405);
  if (!(request.headers.get("content-type") || "").toLowerCase().startsWith("application/json")) {
    return reply({ error: "send JSON" }, 415);
  }
  if (!env.IP_SALT) { console.error("IP_SALT is not set: reports refused"); return reply({ error: "server not ready" }, 500); }

  const raw = await readCapped(request, isHealth ? HEALTH_MAX_BYTES : REPORT_MAX_BYTES);
  if (raw === null) return reply({ error: "too big" }, 413);
  let body;
  try { body = JSON.parse(raw); } catch { return reply({ error: "not JSON" }, 400); }

  const today = new Date(now).toISOString().slice(0, 10);
  const bad = isHealth ? badHealthField(body, today) : badReportField(body);
  if (bad) return reply({ error: bad }, 400);

  const limit = caps(env, url);
  const conn = await tools.connectionCode("reports " + tools.connectionPart(request.headers.get("CF-Connecting-IP") || "?"),
    today, env.IP_SALT);
  const stamp = new Date(now).toISOString();
  const answer = isHealth
    ? await storeHealth(env, body, today, conn, stamp, limit)
    : await storeReport(env, body, today, conn, stamp, limit);
  // About 1 in 100 stored reports also clears old rows (only after a store, so a refused request writes nothing).
  if (answer.status === 200 && !answer.dup && Math.floor(Math.random() * limit.purgeEvery) === 0) {
    await env.DB.batch(purgeStatements(env, now));
  }
  return answer;
}

/** Old rows go: reports after 90 days, the daily counters after 3. */
function purgeStatements(env, now) {
  const reports = new Date(now - KEEP_REPORTS_DAYS * DAY).toISOString();
  const limits = new Date(now - KEEP_LIMITS_DAYS * DAY).toISOString().slice(0, 10);
  return [
    env.DB.prepare("DELETE FROM health_reports WHERE received < ?1").bind(reports),
    env.DB.prepare("DELETE FROM user_reports WHERE received < ?1").bind(reports),
    env.DB.prepare("DELETE FROM limits WHERE day < ?1").bind(limits),
  ];
}

/** `INSERT ... n = n + 1`, run only when the statement before it changed a row. */
function bump(env, day, key) {
  return env.DB.prepare(
    `INSERT INTO limits (day, key, n) SELECT ?1, ?2, 1 WHERE changes() = 1
     ON CONFLICT(day, key) DO UPDATE SET n = n + 1`).bind(day, key);
}

async function storeHealth(env, b, today, conn, stamp, limit) {
  // A repeat of the same day is answered from a one-row read; it writes nothing and doesn't count against the connection.
  const have = await env.DB.prepare("SELECT 1 AS x FROM health_reports WHERE id = ?1 AND day = ?2").bind(b.id, b.day).first();
  if (have) return Object.assign(reply({ ok: true, dup: true }), { dup: true });
  // What is stored is the checked body written compactly, so spaces or repeated keys can't pad a row up to 4 KB.
  const connKey = "h:conn:" + conn;
  const insert = env.DB.prepare(
    `INSERT OR IGNORE INTO health_reports (id, day, received, conn, body)
     SELECT ?1, ?2, ?3, ?4, ?5
     WHERE COALESCE((SELECT n FROM limits WHERE day = ?6 AND key = ?7), 0) < ?8
       AND COALESCE((SELECT n FROM limits WHERE day = ?6 AND key = 'h:site'), 0) < ?9`
  ).bind(b.id, b.day, stamp, conn, JSON.stringify(b), today, connKey, limit.healthConn, limit.healthSite);
  const results = await env.DB.batch([insert, bump(env, today, connKey), bump(env, today, "h:site")]);
  return results[0].meta.changes === 1 ? reply({ ok: true }) : reply({ retry: true }, 429);
}

async function storeReport(env, b, today, conn, stamp, limit) {
  const idKey = "r:id:" + b.id, connKey = "r:conn:" + conn;
  const insert = env.DB.prepare(
    `INSERT INTO user_reports (id, received, conn, version, message, details)
     SELECT ?1, ?2, ?3, ?4, ?5, ?6
     WHERE COALESCE((SELECT n FROM limits WHERE day = ?7 AND key = ?8), 0) < ?9
       AND COALESCE((SELECT n FROM limits WHERE day = ?7 AND key = ?10), 0) < ?11
       AND COALESCE((SELECT n FROM limits WHERE day = ?7 AND key = 'r:site'), 0) < ?12`
  ).bind(b.id, stamp, conn, b.version, b.message.trim(), b.details, today, idKey, limit.reportId, connKey,
    limit.reportConn, limit.reportSite);
  const results = await env.DB.batch([insert, bump(env, today, idKey), bump(env, today, connKey), bump(env, today, "r:site")]);
  return results[0].meta.changes === 1 ? reply({ ok: true }) : reply({ retry: true }, 429);
}
