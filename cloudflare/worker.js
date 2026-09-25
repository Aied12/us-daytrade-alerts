/**
 * Cloudflare Worker — بث حي للوحة التنبيهات
 *
 * يخزّن آخر status/prices في KV ويقدّمها فوراً للزوار بدون GitHub Pages.
 *
 * Bindings المطلوبة:
 * - LIVE_KV (KV namespace)
 * - INGEST_TOKEN (secret)
 *
 * Deploy:
 *   npx wrangler kv namespace create LIVE_KV
 *   npx wrangler secret put INGEST_TOKEN
 *   npx wrangler deploy
 */

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    const cors = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
      "Access-Control-Allow-Headers": "Authorization,Content-Type",
      "Cache-Control": "no-store, max-age=0",
    };

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors });
    }

    // صحة البث
    if (path === "/health" || path === "/api/health") {
      const hb = await env.LIVE_KV.get("heartbeat");
      const age = hb ? Math.max(0, Math.floor(Date.now() / 1000 - Number(hb))) : null;
      return json({ ok: true, heartbeat_age_sec: age, service: "us-daytrade-live" }, cors);
    }

    // قراءة البيانات (عام)
    if (request.method === "GET") {
      if (path === "/status.json" || path === "/api/status") {
        const body = await env.LIVE_KV.get("status");
        if (!body) return json({ ok: false, error: "no status yet" }, cors, 404);
        return new Response(body, {
          headers: { ...cors, "Content-Type": "application/json; charset=utf-8" },
        });
      }
      if (path === "/prices-live.json" || path === "/api/prices") {
        const body = await env.LIVE_KV.get("prices");
        if (!body) return json({ ok: false, error: "no prices yet" }, cors, 404);
        return new Response(body, {
          headers: { ...cors, "Content-Type": "application/json; charset=utf-8" },
        });
      }
      if (path === "/news-live.json" || path === "/api/news") {
        const body = await env.LIVE_KV.get("news");
        if (!body) return json({ ok: false, error: "no news yet" }, cors, 404);
        return new Response(body, {
          headers: { ...cors, "Content-Type": "application/json; charset=utf-8" },
        });
      }
      return json(
        {
          ok: true,
          endpoints: ["/health", "/status.json", "/prices-live.json", "/news-live.json", "/ingest"],
        },
        cors
      );
    }

    // كتابة من البوت / Actions
    if (request.method === "POST" && (path === "/ingest" || path === "/ingest/prices" || path === "/ingest/news")) {
      const auth = request.headers.get("Authorization") || "";
      const token = auth.replace(/^Bearer\s+/i, "").trim();
      if (!env.INGEST_TOKEN || token !== env.INGEST_TOKEN) {
        return json({ ok: false, error: "unauthorized" }, cors, 401);
      }
      const text = await request.text();
      try {
        JSON.parse(text);
      } catch {
        return json({ ok: false, error: "invalid json" }, cors, 400);
      }
      const key = path.endsWith("/prices") ? "prices" : path.endsWith("/news") ? "news" : "status";
      await env.LIVE_KV.put(key, text);
      await env.LIVE_KV.put("heartbeat", String(Math.floor(Date.now() / 1000)));
      return json({ ok: true, stored: key }, cors);
    }

    return json({ ok: false, error: "not found" }, cors, 404);
  },
};

function json(obj, cors, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { ...cors, "Content-Type": "application/json; charset=utf-8" },
  });
}
