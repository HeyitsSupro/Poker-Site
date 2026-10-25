// Runtime configuration. Edit this file and commit it; no rebuild needed
// for the copy in docs/, though rebuilding keeps the two in step.
//
// BACKEND_URL is the address of the Render web service, with no trailing
// slash. The WebSocket address is derived from it (https -> wss), so it
// must be the https:// one: a page served over HTTPS is not allowed to
// call an insecure address, and the browser blocks it before it reaches
// the network.
//
// SITE_NAME is the one place the site is named. Change it here and it
// changes everywhere, including the home-screen app name.
//
// These are the live values for this deployment. The matching server-side
// setting is ALLOWED_ORIGIN on Render, which must be exactly
// https://heyitssupro.github.io — the page's origin, with no path and no
// trailing slash, because that is all a browser ever sends.
window.POKER_CONFIG = {
  SITE_NAME: 'Poker Site',
  BACKEND_URL: 'https://poker-site-sdou.onrender.com',
};
