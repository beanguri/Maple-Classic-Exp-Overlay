/** Unit tests for the partner (v1) payload shapers. Run: node server/test_partner.mjs */
import assert from "node:assert/strict";
import { slimMapRow, partnerMaps, partnerLadder, partnerOptions, partnerRecent } from "./src/index.js";

let failed = 0;
function check(name, ok) {
  console.log((ok ? "PASS " : "FAIL ") + name);
  failed += !ok;
}

const fat = {
  map: "Dangerous Steam",
  exp_hr: 67072,
  exp_hr_low: 58704,
  exp_hr_high: 69150,
  mesos_hr: 52056,
  pct_hr: 52.6,
  hours_per_level: 1.9,
  players: 3,
  runs: 7,
  hours: 1.8,
  levels: [27, 33],
  widened: false,
  by_party: [{ party: 1, players: 3, exp_hr: 67072 }],
  dots: [50300, 67100, 71200],
  dot_levels: [30, 32, 33],
  unusual: [],
  unusual_levels: [],
  new_players: 0,
  by_level: [{ level: 30, players: 1, exp_hr: 50300 }],
};

const slim = slimMapRow(fat);
check("slimMapRow drops chart dots", !("dots" in slim) && !("dot_levels" in slim) && !("unusual" in slim));
check("slimMapRow keeps rates", slim.exp_hr === 67072 && slim.mesos_hr === 52056 && slim.map === "Dangerous Steam");
check("slimMapRow keeps by_party", slim.by_party.length === 1);

const url = new URL("https://mcwo.app/api/v1/maps?class=Thief&path=Night%20Lord&level=30&party=solo");
const maps = partnerMaps({
  class: "Thief", path: "Night Lord", party: "solo", min_players: 3,
  newest_run: "2026-10-28T05:32Z", maps: [fat], early: [fat],
}, url);
check("partnerMaps tags api/source/attribution", maps.api === "v1" && maps.source.includes("mcwo.app") && maps.attribution.includes("mcwo.app"));
check("partnerMaps sets level from query", maps.level === 30);
check("partnerMaps slims maps and early", !("dots" in maps.maps[0]) && !("dots" in maps.early[0]));

const ladder = partnerLadder({ class: "Thief", path: "Night Lord", party: "solo", band: 5, min_players: 3,
  bands: [{ from: 30, to: 34, maps: [{ map: "X", exp_hr: 1, players: 3 }] }] });
check("partnerLadder keeps bands", ladder.bands.length === 1 && ladder.api === "v1");

const opts = partnerOptions({ classes: [], runs: 1, hours: 2, players: 3, maps: 4, players_new: 0 });
check("partnerOptions keeps totals", opts.runs === 1 && opts.api === "v1");

const recent = partnerRecent({ runs: [{ map: "A", job: "Bandit", level: 30, party: 1, exp_hr: 1 }] });
check("partnerRecent keeps runs", recent.runs.length === 1 && recent.api === "v1");

check("slimMapRow on junk is null", slimMapRow(null) === null);

if (failed) {
  console.error(`${failed} failed`);
  process.exit(1);
}
console.log("ALL PASSED");
