// The overlay versions that were really released (git tags v0.9.0-beta ...). Runs, health reports and Send reports are
// taken only from these, so a changed copy of the overlay (a fork that renamed its version) can't send to mcwo.app.
// A fork that keeps a real version number can't be told apart: the code is public.
//
// RELEASE STEP: add the new VERSION here in the release commit, and deploy the site BEFORE pressing Publish. (build.yml
// refuses to build a tag whose VERSION is missing here.) Until then a newer version is told "try later", never refused
// for good, so real players lose nothing if the deploy comes late.
export const RELEASED = [
  "0.9.0-beta", "0.9.1-beta", "0.9.2-beta", "0.9.3-beta", "0.9.4-beta", "0.9.5-beta", "0.9.6-beta", "0.9.7-beta",
  "0.9.8-beta", "0.9.9-beta", "0.9.10-beta", "0.9.11-beta", "0.9.12-beta", "0.9.13-beta", "0.9.14-beta", "0.9.15-beta",
];

/** [0, 9, 11] for "0.9.11-beta"; [] when it isn't a version. */
export const versionKey = (v) => (typeof v === "string" && v.match(/^\d+(\.\d+)*/)?.[0].split(".").map(Number)) || [];

/** Is version key `a` older than `b`? */
export function olderThan(a, b) {
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    if ((a[i] || 0) !== (b[i] || 0)) return (a[i] || 0) < (b[i] || 0);
  }
  return false;
}

const NEWEST = RELEASED.map(versionKey).reduce((a, b) => (olderThan(a, b) ? b : a));

/** "ok" (a released version), "later" (newer than any released one: the site may not be deployed yet, so try again
 * later) or "refused" (anything else: a changed copy, refused for good). Runs from 0.9.0-0.9.2 carry no version: the
 * runs endpoint takes those (`missing` = "ok") before asking here. */
export function versionStatus(version) {
  if (typeof version !== "string") return "refused";
  if (RELEASED.includes(version)) return "ok";
  return olderThan(NEWEST, versionKey(version)) ? "later" : "refused";
}
