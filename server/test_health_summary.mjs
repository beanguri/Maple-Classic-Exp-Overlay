// Checks the formatting part of tools/health_summary.mjs with canned query results (no database needed).
//   node server/test_health_summary.mjs
import assert from "node:assert/strict";
import fs from "node:fs";
import { summarize, summarizeReports, formatOne, oneReportQuery, queries, partyReader } from "./tools/health_summary.mjs";
import { RELEASED, versionStatus } from "./src/released.js";

const base = { looks: 0, secs: 0, gh: null, e_read: 0, e_tess: 0, e_unclear: 0, e_nf: 0, e_cov: 0, e_nowin: 0, e_pct: 0,
  map_r: 0, map_u: 0, cls_r: 0, cls_u: 0, mes_r: 0, mes_u: 0, pot_r: 0, pot_u: 0, sh_agree: 0, sh_dis: 0, sh_v2only: 0,
  sh_oldonly: 0, sh_neither: 0, sh_skipped: 0, sh_err: 0, ms_look_w: 0, ms_v2_w: 0, v2_looks: 0 };
// (the 0.9.14 columns are missing from "base" on purpose: the summary must cope with groups that lack them)

// Empty database.
assert.deepEqual(summarize({ groups: [] }, 7), ["No health reports yet in the last 7 days."]);
assert.deepEqual(summarizeReports([]), ["No Send reports in that time."]);

const data = {
  groups: [
    { ...base, v: "0.9.14-beta", w: 2560, h: 1440, fs: 1, tess: "ok", cap: "screen", players: 12, looks: 10000, gh: 14,
      e_read: 6500, e_tess: 200, e_unclear: 300, e_nf: 3000, e_cov: 0, e_nowin: 0, e_pct: 100,
      map_r: 90, map_u: 10, ms_look_w: 250000, ms_v2_w: 80000, v2_looks: 10000, sh_agree: 9000, sh_dis: 4 },
    { ...base, v: "0.9.14-beta", w: 1920, h: 1080, fs: 0, tess: "no_eng", cap: "region", players: 2, looks: 2000, gh: 10.5,
      e_read: 1990, e_nf: 10 },
  ],
  days: [{ day: "2026-10-25", v: "0.9.14-beta", players: 14 }, { day: "2026-10-26", v: "0.9.14-beta", players: 9 }],
  errors: [{ name: "TesseractError", n: 7, players: 2 }],
  disagree: [{ sid: "a1b2c3", d: 4 }],
  worst: [{ sid: "a1b2c3", v: "0.9.14-beta", w: 2560, h: 1440, fs: 1, looks: 800, bad: 400 }],
};
const text = summarize(data, 7).join("\n");
for (const want of [
  "14 players", "2026-10-25  14", "2026-10-26  9", "0.9.14-beta 14",
  "2560x1440 fullscreen", "12 players", "gh 14.0", "not found 30.0%", "Tesseract 2.0%", "bar only 1.0%",
  "1920x1080 windowed",
  "map name 90.0% (100 tries)",
  "no_eng 2 players", "TesseractError x7 (2 players)",
  "agree 9,000, disagree 4", "new reader 8.0", "whole look 20.8", "a1b2c3 x4",
  "a1b2c3  2560x1440 fullscreen", "50.0% of 800 looks",
  "2560x1440 fullscreen (0.9.14-beta): EXP not found/unclear 33.0% of looks (12 players)",
  'Tesseract "no_eng" on 2 players', "shadow mode disagreed 4 times",
]) assert.ok(text.includes(want), "missing: " + want + "\n" + text);
assert.ok(!/conn/i.test(text), "no connection codes in the summary");

// No shadow data and a healthy day: a quiet "what to look at".
const quiet = summarize({ groups: [{ ...base, v: "0.9.13-beta", w: 1920, h: 1080, fs: 0, tess: "ok", cap: "screen", players: 1,
  looks: 500, e_read: 500 }], days: [{ day: "2026-10-25", v: "0.9.13-beta", players: 1 }] }, 7).join("\n");
assert.ok(quiet.includes("no shadow data yet") && quiet.includes("nothing stands out"), quiet);

// 0.9.14 numbers: UI size and layout, mesos and party shadow readers, the EXP "why" split.
const none = { shm_agree: 0, shm_dis: 0, shm_v2only: 0, shm_oldonly: 0, shm_neither: 0, shm_skipped: 0, shm_err: 0,
  shp_agree: 0, shp_dis: 0, shp_v2only: 0, shp_oldonly: 0, shp_neither: 0, shp_skipped: 0, shp_err: 0,
  ui_default: 0, ui_ideal: 0, ui_kb: 0, ui_ctl: 0, why_nf: 0, why_unclear: 0, ms_mes_w: 0, ms_par_w: 0, mes_looks: 0, par_looks: 0 };
const v2 = summarize({ groups: [{ ...base, ...none, v: "0.9.14", w: 1920, h: 1080, fs: 1, tess: "ok", cap: "screen", players: 3,
  looks: 1000, e_read: 1000, ui_default: 600, ui_ideal: 200, ui_kb: 700, ui_ctl: 100, why_nf: 30, why_unclear: 5,
  shm_agree: 80, shm_dis: 2, shm_v2only: 3, ms_mes_w: 3000, mes_looks: 1000, shp_agree: 50, sh_oldonly: 1 }],
  days: [{ day: "2026-10-25", v: "0.9.14", players: 3 }] }, 7).join("\n");
for (const want of ["UI size: Default 600 (75.0%), Ideal 200 (25.0%)", "keyboard 700 (87.5%), controller 100 (12.5%)",
  "mesos: agree 80, disagree 2, v2 only 3", "ms per look 3.0", "party size, 0.9.14 (reader v2): agree 50", "not found 30, unclear 5",
  "mesos shadow disagreed 2 times"]) assert.ok(v2.includes(want), "missing: " + want + "\n" + v2);

// The party shadow is reader v2 in 0.9.14 and v3 after it: one line per version, never added up.
const both = summarize({ groups: [
  { ...base, ...none, v: "0.9.14-beta", w: 1920, h: 1080, fs: 0, tess: "ok", cap: "screen", players: 2, looks: 500, e_read: 500,
    shp_agree: 10, shp_dis: 40, ms_par_w: 4500, par_looks: 500 },
  { ...base, ...none, v: "0.9.15-beta", w: 1920, h: 1080, fs: 0, tess: "ok", cap: "screen", players: 2, looks: 500, e_read: 500,
    shp_agree: 49, shp_dis: 1, ms_par_w: 2000, par_looks: 500 }],
  days: [{ day: "2026-10-31", v: "0.9.15-beta", players: 2 }] }, 7).join("\n");
for (const want of ["party size, 0.9.14-beta (reader v2): agree 10, disagree 40", "ms per look 9.0",
  "party size, 0.9.15-beta (reader v3): agree 49, disagree 1", "ms per look 4.0",
  "party size shadow (reader v2, 0.9.14-beta) disagreed 40 times", "party size shadow (reader v3, 0.9.15-beta) disagreed 1 times"]) {
  assert.ok(both.includes(want), "missing: " + want + "\n" + both);
}
assert.ok(!both.includes("agree 59"), "v2 and v3 party numbers are never added up\n" + both);
for (const [v, want] of [["0.9.14-beta", "reader v2"], ["0.9.14", "reader v2"], ["0.9.9", "reader v2"], ["0.9.15-beta", "reader v3"],
  ["0.9.140", "reader v3"], ["1.0.0", "reader v3"]]) assert.equal(partyReader(v), want, v);
assert.ok(quiet.includes("no UI size or layout data yet") && quiet.includes("mesos: no shadow data yet"), quiet);

// Send report lists and one report in full.
const lines = summarizeReports([{ n: 12, d: "2026-10-25", version: "0.9.14-beta", id: "0123456789abcdef", m: "it says not found" }]);
assert.ok(lines[1].includes("#12") && lines[1].includes("it says not found"));
assert.ok(formatOne({ n: 12, id: "x", received: "t", version: "v", message: "hello", details: "" }).join("\n").includes("(none)"));
assert.deepEqual(formatOne(undefined), ["No such report."]);

// The report id/number is checked before it goes near SQL.
assert.ok(oneReportQuery("12").includes("n = 12"));
assert.ok(oneReportQuery("0123ab").includes("LIKE '0123ab%'"));
for (const bad of ["1; DROP TABLE x", "abc' OR 1=1", "ABCDEF", ""]) assert.throws(() => oneReportQuery(bad), bad);

// Every query filters on `received` (the indexed column).
for (const sql of Object.values(queries("2026-10-01T00:00:00.000Z"))) assert.ok(sql.includes("received >= '2026-10-01T00:00:00.000Z'"));

// One row that isn't JSON (it can't arrive through the site, only by hand) must not stop the whole summary.
let sqlite = null;
try { sqlite = await import("node:sqlite"); } catch { console.log("health_summary: SKIPPED the bad-row check (this Node has no node:sqlite)"); }
if (sqlite) {
  const db = new sqlite.DatabaseSync(":memory:");
  db.exec("CREATE TABLE health_reports (id TEXT, day TEXT, received TEXT, conn TEXT, body TEXT)");
  const put = db.prepare("INSERT INTO health_reports VALUES (?, '2026-10-25', '2026-10-25T10:00:00Z', 'c', ?)");
  put.run("aaaaaaaaaaaaaaaa", JSON.stringify({ version: "0.9.14-beta", looks: 10, exp: { read: 10 }, errors: { OSError: 1 } }));
  put.run("bbbbbbbbbbbbbbbb", "not json {{");
  for (const [name, sql] of Object.entries(queries("2026-10-01T00:00:00.000Z"))) {
    assert.doesNotThrow(() => db.prepare(sql).all(), `query ${name} survives a row that isn't JSON`);
  }
}

// What a player typed is printed to the owner's terminal (and read by Claude): terminal escape codes must not get through
// (clear screen, a new window title, writing to the clipboard), and the text is marked as data (B05-3).
{
  const evil = "hi \x1b[2J\x1b[H\x1b]0;PWNED\x07 \x1b]52;c;ZXZpbA==\x07 ok \x9b31m ‮RTL⁦ \r overwritten \x00 end\ttab";
  const shown = [...formatOne({ n: 3, id: "0123456789abcdef", received: "t\x1b[2J", version: "0.9.14", message: evil, details: evil + "\nsecond line" }),
    ...summarizeReports([{ n: 3, d: "d", version: "v", id: "i", m: evil }])].join("\n");
  assert.ok(!/[\x00-\x08\x0b-\x1f\x7f-\x9f‪-‮⁦-⁩]/.test(shown.replace(/\n/g, "")), "no control characters reach the terminal");
  assert.ok(shown.includes("hi ") && shown.includes("second line") && shown.includes("ok"), "the readable text stays");
  assert.ok(shown.includes("\nsecond line"), "line breaks in the long text stay");
  assert.ok(/data/i.test(formatOne({ n: 1, id: "x", received: "t", version: "v", message: "m", details: "d" }).join("\n")), "says it is the player's text, to be read as data");
}


// Only released versions are counted (src/released.js); other versions get one line of their own.
const q = queries("2026-10-01T00:00:00.000Z");
for (const k of ["groups", "days", "errors", "disagree", "worst"]) {
  assert.ok(q[k].includes("IN ('0.9.0-beta'") && q[k].includes(`'${RELEASED.at(-1)}')`), `${k} counts released versions only`);
}
assert.ok(/NOT \(json_extract\(body,'\$\.version'\) IN \(/.test(q.unknown), "the unknown query takes the others");
const forks = summarize({ ...data, unknown: [{ v: "0.9.16-beta", installs: 1 }, { v: "1.0.0\x1b[2J", installs: 3 }] }, 7).join("\n");
assert.ok(forks.includes("not counted (not a released version: a changed copy of the overlay):  0.9.16-beta 1 install, 1.0.0"), forks);
assert.ok(forks.includes("3 installs") && !forks.includes("\x1b"), "a fork's version is printed as plain data");
assert.ok(!summarize(data, 7).join("\n").includes("not counted"), "no line when every version is released");

// The release step can't be forgotten: the overlay's VERSION must be in released.js (build.yml checks it too).
const VERSION = fs.readFileSync(new URL("../maple_exp.py", import.meta.url), "utf8").match(/^VERSION = "([^"]+)"/m)[1];
assert.ok(RELEASED.includes(VERSION), `maple_exp.py VERSION ${VERSION} is missing from server/src/released.js (release step)`);
assert.equal(versionStatus(VERSION), "ok");
for (const [v, want] of [["0.9.14-beta", "ok"], ["0.9.0-beta", "ok"], ["0.9.14", "refused"], ["0.9.14-myfork", "refused"],
  ["0.8.9-beta", "refused"], ["", "refused"], [null, "refused"], ["0.9.99-beta", "later"], ["0.10.0", "later"], ["1.0.0", "later"]]) {
  assert.equal(versionStatus(v), want, `versionStatus(${JSON.stringify(v)})`);
}

console.log("health_summary: all checks passed");
