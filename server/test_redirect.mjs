// The old address redirect in public/app.js:  node server/test_redirect.mjs
// Pulls the real function out of the page's script and checks where it sends people.
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("./public/app.js", import.meta.url), "utf8");
const start = source.indexOf('const OLD_HOST');
const end = source.indexOf("if (typeof location");
if (start < 0 || end < 0) throw new Error("the redirect isn't in app.js any more");
const movedTo = vm.runInNewContext(source.slice(start, end) + "; movedTo");

let failures = 0;
const check = (name, ok, extra = "") => { console.log(`${ok ? "PASS" : "FAIL"} ${name}${extra ? ` (${extra})` : ""}`); failures += !ok; };
const at = (hostname, pathname = "/", search = "", hash = "") => ({ hostname, pathname, search, hash });

check("the old address goes to the new one", movedTo(at("maple-classic-exp-overlay.pages.dev")) === "https://mcwo.app/");
check("...keeping the level, class and the rest of the choices",
      movedTo(at("maple-classic-exp-overlay.pages.dev", "/", "?level=41&job=Thief&path=Night%20Lord&party=solo&sort=pct", "#results-section"))
      === "https://mcwo.app/?level=41&job=Thief&path=Night%20Lord&party=solo&sort=pct#results-section");
check("...and other pages of the site (like the sitemap)", movedTo(at("maple-classic-exp-overlay.pages.dev", "/robots.txt")) === "https://mcwo.app/robots.txt");
check("the new address stays where it is", movedTo(at("mcwo.app")) === null && movedTo(at("www.mcwo.app")) === null);
check("a preview deployment is left alone (it's for testing)", movedTo(at("abc123.maple-classic-exp-overlay.pages.dev")) === null
      && movedTo(at("main.maple-classic-exp-overlay.pages.dev")) === null);
check("a local test server is left alone", movedTo(at("localhost")) === null && movedTo(at("127.0.0.1")) === null);
check("a look-alike host isn't moved (only the exact old address)", movedTo(at("maple-classic-exp-overlay.pages.dev.evil.com")) === null
      && movedTo(at("evilmaple-classic-exp-overlay.pages.dev")) === null);
check("the redirect never goes anywhere but the new site (path can't change the host)",
      movedTo(at("maple-classic-exp-overlay.pages.dev", "//evil.com/x")).startsWith("https://mcwo.app//evil.com") === true);
check("the API address isn't touched by this file (the page only moves the page)", !/api\/runs/.test(source.slice(start, end)));

// Choices from a link or the browser's saved settings: a name every object has ("constructor") is not a sort (B05-4).
const sortsAt = source.indexOf("const SORTS = {"), sortsEnd = source.indexOf("};", sortsAt) + 2;
const helperAt = source.indexOf("const soloOrParty"), helperEnd = source.indexOf("const plural");
const linkAt = source.indexOf("function fromLink"), linkEnd = source.indexOf("function chosenClass");
if (sortsAt < 0 || helperAt < 0 || linkAt < 0) throw new Error("fromLink or SORTS moved in app.js");
const fromLink = vm.runInNewContext("let classes = [];\n" + source.slice(sortsAt, sortsEnd) + source.slice(helperAt, helperEnd) +
  source.slice(linkAt, linkEnd) + "; fromLink");
const sortOf = (v) => fromLink(new URLSearchParams({ sort: v })).sort;
check("a real sort in a link is kept", sortOf("mesos") === "mesos" && sortOf("exp") === "exp");
check("...but names every object has (constructor, __proto__, toString) are not sorts",
      sortOf("constructor") === undefined && sortOf("__proto__") === undefined && sortOf("toString") === undefined);
check("the saved sort is checked the same way", /Object\.hasOwn\(SORTS, last\.sort\)/.test(source));

console.log(failures ? `\n${failures} FAILED` : "\nALL PASSED");
process.exit(failures ? 1 : 0);
