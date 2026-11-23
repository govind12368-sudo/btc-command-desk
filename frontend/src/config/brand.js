// One place to change the site name and tagline. Override at build time with REACT_APP_NAME.
// Renamed from "Vantage BTC" on 23 Nov 2026. Servers installed earlier still pass the old name at build time, so it is treated as "not set".
const given = (process.env.REACT_APP_NAME || "").trim();
export const APP_NAME = !given || given.toLowerCase() === "vantage btc" ? "My BTC Partner" : given;
export const APP_SHORT = process.env.REACT_APP_SHORT_NAME || (APP_NAME === "My BTC Partner" ? "BTC Partner" : APP_NAME);
export const APP_OLD_NAME = "Vantage BTC"; // what an app added to a phone before the rename is still called there
export const APP_TAGLINE = "Bitcoin decision desk";
