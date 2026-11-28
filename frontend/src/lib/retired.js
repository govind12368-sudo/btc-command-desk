// Taken off the menu for everyone, admin too (owner, 28 Nov 2026): pages built on the scalp signals and the Plan A / Day-type
// switch, which did not show an edge. Nothing is deleted: the routes, Auto-Trade, alerts and background jobs keep running, and
// removing a path here brings the page back. Users also get 403 from their data (backend ui_prefs.RETIRED).
export const RETIRED = new Set(["/scalping", "/swing", "/pillars", "/validation", "/plan-a", "/observer", "/journal", "/regime"]);
export const isRetired = (to) => RETIRED.has(String(to || "").split(/[?#]/)[0]);
