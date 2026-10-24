// Map names: the overlay reads them from the minimap with OCR, so the same map can arrive spelled
// a few ways ("ist Accompaniment Bonus" for "1st Accompaniment Bonus"). Names are matched to the
// game's own list (maps.js, from meowdb.com) so each map is one line, and towns (where nobody
// farms) are turned down. A name that isn't in the list (an event map, a new map) is NOT turned
// down: it's kept as read, and the caller notes it for review.
//
// The same rules are in the overlay (maple_overlay/vision/maps.py: snap_map_name), so a player's own log and
// the site agree.
import { MAPS } from "./maps.js";

/** The same clean-up the site gives every name it stores (see text() in index.js). */
export const tidy = (t) => t.replace(/[^\p{L}\p{N} '().,:&\/-]/gu, " ").replace(/\s+/g, " ").trim();

/** Only letters and digits, lower case: "Tree Dungeon, Monkey Forest II" and the same without the
 * comma are one key. */
export const keyOf = (name) => name.toLowerCase().replace(/[^a-z0-9]/g, "");

/** Ordinals OCR gets wrong: "ist" / "lst" / "|st" -> 1st, "Znd" -> 2nd, "Srd" / "5rd" / "Brd" -> 3rd. */
export function fixOrdinals(name) {
  return name
    .replace(/(^|\s)[il|!]st(?=\s|$)/gi, (_, a) => `${a}1st`)
    .replace(/(^|\s)znd(?=\s|$)/gi, (_, a) => `${a}2nd`)
    .replace(/(^|\s)[s5b]rd(?=\s|$)/gi, (_, a) => `${a}3rd`);
}

const ROMAN = /^(?:X{0,2})(?:IX|IV|V?I{0,3})$/;

/** A last word that is a Roman numeral OCR misread ("Vil" -> VII, "Vill" -> VIII, "lV" -> IV, "il" -> II,
 * "V1" -> VI). It must look like a numeral (only I, V, X or look-alikes, at least one letter among them), so a
 * plain number ("Free Market 11") and ordinary words are left alone. */
export function fixRomans(name) {
  return name.replace(/(\s)([IVXivxl1|!]{1,6})$/, (m, space, token) => {
    if (!/[IVXivxl|!]/.test(token)) return m;                        // only digits: a number
    const numeral = token.replace(/[li1|!]/gi, "I").toUpperCase();
    return numeral && ROMAN.test(numeral) ? space + numeral : m;
  });
}

// Screens and menus the overlay's text reader can mistake for a map (the region line under the minimap,
// the cash shop, settings).
const NOT_MAPS = /\b(?:cash deco|set ?up|blacklist|keyboard|mouse|controller|citizens|discord|telegram|whatsapp|giveaway|facebook|youtube|instagram|twitch|paypal|dot ?com)\b|\bwww\b|\bhttps?\b|\.gg\b|^(?:hidden street|world|victoria road)$/i;

/** Not a map name at all: too little text to be a name (no word of 4 letters), letters outside the English
 * alphabet (look-alikes of English ones are a way to make a second "map"), or a menu or advert. Only asked
 * of names that aren't on the game's list. */
export function junk(name) {
  return !/[A-Za-z]{4,}/.test(name) || /[^\x20-\x7e]/.test(name) || NOT_MAPS.test(name);
}

/** Roman numerals and numbers in a name: maps that differ in these are different maps. */
const numbers = (name) => (name.match(/\b(?:[IVX]+|\d+)\b/g) || []).join(",");

const entries = MAPS.map(([name, town]) => ({ name: tidy(name), key: keyOf(name), town: !!town, nums: numbers(tidy(name)) }));
const byKey = new Map(entries.map((e) => [e.key, e]));

/** The number of single-letter changes (add, drop, change) between two strings. */
export function editDistance(a, b) {
  if (a === b) return 0;
  if (!a.length || !b.length) return Math.max(a.length, b.length);
  let prev = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    const row = [i];
    for (let j = 1; j <= b.length; j++) {
      row[j] = Math.min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    }
    prev = row;
  }
  return prev[b.length];
}

/** How many letters of a name may be wrong and still be taken for a listed map: one, or two in a
 * long name. (Two in a short one could be a different map: West / East.) */
const allowed = (key) => (key.length >= 24 ? 2 : 1);

// Names seen lately and what they came to. A batch of runs is mostly the same few maps, and the
// fuzzy search costs a little CPU, so each name is worked out once (the free plan gives a request
// 10 ms of CPU). Bounded, so made-up names can't grow it.
const seen = new Map();

/** { name, town, listed } for a map name as the overlay read it (already through tidy()). */
export function canonicalMap(read) {
  let got = seen.get(read);
  if (!got) {
    got = lookup(read);
    if (seen.size >= 2000) seen.clear();
    seen.set(read, got);
  }
  return { ...got };
}

function lookup(read) {
  const fixed = tidy(fixRomans(fixOrdinals(read)));
  const key = keyOf(fixed);
  const exact = byKey.get(key);
  if (exact) return { name: exact.name, town: exact.town, listed: true, junk: false };
  const nums = numbers(fixed);
  let best = null, second = Infinity;
  for (const e of entries) {
    if (Math.abs(e.key.length - key.length) > 2 || e.nums !== nums) continue;
    const d = editDistance(key, e.key);
    if (!best || d < best.d) {
      second = best ? best.d : second;
      best = { e, d };
    } else if (d < second) second = d;
  }
  // one clear match: a letter misread. Two names about as close (or too far) are left alone.
  if (best && best.d <= allowed(key) && second - best.d >= 1) return { name: best.e.name, town: best.e.town, listed: true, junk: false };
  return { name: fixed, town: false, listed: false, junk: junk(fixed) };
}

export const LISTED = entries.length;

/** Is this (already cleaned) name on the game's list? */
export const isListed = (name) => byKey.has(keyOf(name));
