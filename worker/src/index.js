// Fetches the current public Snapchat stories of a fixed set of accounts and
// returns them as JSON for the story player page.

const ACCOUNTS = ["trendnews24", "saudi.panther"];
const UA =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1";
const ALLOWED_ORIGINS = ["https://musalah.github.io"];
const CACHE_SECONDS = 60;
const MAX_AGE_MS = 2 * 24 * 3600 * 1000; // drop snaps older than two days

async function fetchAccount(username) {
  const res = await fetch(`https://www.snapchat.com/@${username}`, {
    headers: { "User-Agent": UA, "Accept-Language": "ar,en;q=0.8" },
    cf: { cacheTtl: CACHE_SECONDS, cacheEverything: true },
  });
  if (!res.ok) throw new Error(`${username}: HTTP ${res.status}`);
  const html = await res.text();
  const m = html.match(/<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/);
  if (!m) throw new Error(`${username}: no __NEXT_DATA__`);
  const pp = JSON.parse(m[1]).props?.pageProps ?? {};
  const profile = pp.userProfile?.publicProfileInfo ?? {};
  const snaps = (pp.story?.snapList ?? [])
    .map(s => ({
      v: s.snapMediaType === 1,
      ts: Number(s.timestampInSec?.value ?? s.timestampInSec) || 0,
      id: s.snapId?.value ?? s.snapUrls?.mediaUrl,
      m: s.snapUrls?.mediaUrl,
      ...(s.isSponsored ? { ad: 1 } : {}), // always null so far; OCR in detect-ads.py does the real work
    }))
    .filter(s => s.m && (!s.ts || s.ts * 1000 > Date.now() - MAX_AGE_MS)); // oldest first, like Snapchat
  return {
    u: profile.username ?? username,
    t: profile.title ?? username,
    p: profile.profilePictureUrl ?? "",
    snaps,
  };
}

function corsHeaders(request) {
  const origin = request.headers.get("Origin") ?? "";
  const allowed = ALLOWED_ORIGINS.includes(origin) || /^https?:\/\/localhost(:\d+)?$/.test(origin);
  return allowed ? { "Access-Control-Allow-Origin": origin, Vary: "Origin" } : { Vary: "Origin" };
}

export default {
  async fetch(request) {
    const cors = corsHeaders(request);
    if (request.method === "OPTIONS") return new Response(null, { headers: { ...cors, "Access-Control-Allow-Methods": "GET" } });
    if (request.method !== "GET") return new Response("Method not allowed", { status: 405, headers: cors });

    const results = await Promise.allSettled(ACCOUNTS.map(fetchAccount));
    const accounts = results.filter(r => r.status === "fulfilled").map(r => r.value);
    const errors = results.filter(r => r.status === "rejected").map(r => String(r.reason?.message ?? r.reason));
    const status = accounts.length ? 200 : 502;
    return Response.json(
      { accounts, errors, fetchedAt: Math.floor(Date.now() / 1000) },
      { status, headers: { ...cors, "Cache-Control": `public, max-age=${CACHE_SECONDS}` } },
    );
  },
};
