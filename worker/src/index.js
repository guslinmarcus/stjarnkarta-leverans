// Leveransportal för personliga stjärnkartor (Moodly Sverige).
// Köparen fyller i uppgifterna -> jobb i KV -> GitHub Actions tillverkar + kvalitetsgranskar -> PDF i KV.
const TTL = 60 * 60 * 24 * 90; // allt raderas efter 90 dagar

const LANGS = { en: "English", sv: "Svenska", de: "Deutsch" };

const css = `
:root{--bg:#0a0f1e;--card:#121a30;--ink:#f3eee0;--mute:#b9b3a3;--gold:#d9c9a0;--err:#ff9b8a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.55 Georgia,serif}
main{max-width:560px;margin:0 auto;padding:40px 16px}h1{font-weight:normal;font-size:30px;margin:0 0 8px}
p{color:var(--mute)}form,.card{background:var(--card);padding:24px;border-radius:10px}
label{display:block;margin:14px 0 4px;font:14px/1.3 system-ui,sans-serif;color:var(--gold)}
input,select{width:100%;padding:11px;border-radius:6px;border:1px solid #33405f;background:#0d1426;color:var(--ink);font:16px system-ui,sans-serif}
button,.btn{display:inline-block;margin-top:22px;padding:13px 22px;border:0;border-radius:6px;background:var(--gold);color:#111;font:600 16px system-ui,sans-serif;text-decoration:none;cursor:pointer}
.row{display:flex;gap:12px}.row>div{flex:1}.err{color:var(--err);font:15px system-ui,sans-serif}
small{color:var(--mute);font:13px system-ui,sans-serif}`;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function page(title, body, refresh) {
  return new Response(
    `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">` +
      (refresh ? `<meta http-equiv="refresh" content="${refresh}">` : "") +
      `<meta name="robots" content="noindex"><title>${esc(title)}</title><style>${css}</style></head><body><main>${body}<footer style="margin-top:40px;font:13px/1.5 system-ui,sans-serif;color:#8a8577">Moodly Sverige (org.nr 802556-3845) · Contact: <a style="color:#b9b3a3" href="mailto:guslinmarcus@gmail.com">guslinmarcus@gmail.com</a> · <a style="color:#b9b3a3" href="/privacy">Privacy &amp; terms</a><br>The term 'Etsy' is a trademark of Etsy, Inc. This application uses the Etsy API but is not endorsed or certified by Etsy, Inc.</footer></main></body></html>`,
    { headers: { "content-type": "text/html; charset=utf-8" } }
  );
}

function form(v = {}, error = "") {
  const opt = Object.entries(LANGS).map(([k, n]) => `<option value="${k}"${v.lang === k ? " selected" : ""}>${n}</option>`).join("");
  return page("Create your star map", `
<h1>Create your star map</h1>
<p>Enter the moment you want to remember. Your map is calculated from the real sky and is usually ready within an hour.</p>
${error ? `<p class="err">${esc(error)}</p>` : ""}
<form method="post" action="/order">
<label for="order">Etsy order number</label><input id="order" name="order" inputmode="numeric" required value="${esc(v.order)}" placeholder="e.g. 3456789012">
<label for="text">Text on the poster (names or a short message)</label><input id="text" name="text" maxlength="40" required value="${esc(v.text)}" placeholder="Anna & Erik">
<div class="row"><div><label for="city">City or town</label><input id="city" name="city" required value="${esc(v.city)}" placeholder="Stockholm"></div>
<div><label for="country">Country</label><input id="country" name="country" required value="${esc(v.country)}" placeholder="Sweden"></div></div>
<div class="row"><div><label for="date">Date</label><input id="date" name="date" type="date" min="1900-01-01" max="2100-12-31" required value="${esc(v.date)}"></div>
<div><label for="time">Time (local)</label><input id="time" name="time" type="time" value="${esc(v.time || "21:00")}"></div></div>
<label for="lang">Language on the poster</label><select id="lang" name="lang">${opt}</select>
<button type="submit">Create my star map</button>
<p><small>We use these details only to make your map. They are deleted automatically after 90 days. Moodly Sverige.</small></p>
</form>`);
}

const REASONS = {
  city_not_found: "We could not find that city. Please check the spelling (or try the nearest larger town) and submit again.",
  quality_gate: "Our automatic quality check stopped this map. Please submit again – if it happens twice, contact us via Etsy messages.",
  internal: "Something went wrong on our side. Please submit again in a few minutes.",
};

function newId() {
  const b = new Uint8Array(12); crypto.getRandomValues(b);
  return [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
}

async function authed(req, env) {
  return req.headers.get("authorization") === `Bearer ${env.FULFIL_SECRET}`;
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const p = url.pathname;

    if (req.method === "GET" && p === "/") return form();
    if (req.method === "GET" && p === "/privacy") return page("Privacy & terms", `<h1>Privacy &amp; terms</h1>
<p><b>Who we are.</b> This service is run by Moodly Sverige, a Swedish non-profit association (org.nr 802556-3845), Vaxholm, Sweden. Surplus funds support work for children's well-being.</p>
<p><b>What we collect.</b> Only what you enter: Etsy order number, the text for your poster, place, date, time and language. We use it only to create and deliver your star map.</p>
<p><b>How long.</b> Everything, including your file, is deleted automatically after 90 days.</p>
<p><b>Sharing.</b> We never sell or share your details. They are processed by our hosting providers (Cloudflare, GitHub) only to run the service.</p>
<p><b>Your rights.</b> You can ask us to delete your data earlier or ask what we store, via the contact address below or Etsy messages.</p>
<p><b>Terms.</b> The star map is calculated from astronomical data for the place and time you enter. Please check your details before submitting; you can resubmit up to three times per order.</p>`);


    if (req.method === "POST" && p === "/order") {
      const f = Object.fromEntries((await req.formData()).entries());
      const v = {
        order: String(f.order || "").replace(/\D/g, "").slice(0, 14), text: String(f.text || "").trim().slice(0, 40),
        city: String(f.city || "").trim().slice(0, 60), country: String(f.country || "").trim().slice(0, 60),
        date: String(f.date || ""), time: String(f.time || "21:00").slice(0, 5), lang: LANGS[f.lang] ? f.lang : "en",
      };
      if (v.order.length < 6) return form(v, "Please enter your Etsy order number (you find it in your Etsy purchase receipt).");
      if (!/^\d{4}-\d{2}-\d{2}$/.test(v.date) || !v.text || !v.city) return form(v, "Please fill in all fields.");
      if (!/^\d{2}:\d{2}$/.test(v.time)) v.time = "21:00";
      const used = parseInt((await env.JOBS.get(`order:${v.order}`)) || "0", 10);
      if (used >= 3) return form(v, "This order number has already been used three times. Contact us via Etsy messages if you need a change.");
      const id = newId();
      const job = { id, ...v, status: "pending", created: new Date().toISOString() };
      await env.JOBS.put(`job:${id}`, JSON.stringify(job), { expirationTtl: TTL });
      await env.JOBS.put(`pending:${id}`, "1", { expirationTtl: TTL });
      await env.JOBS.put(`order:${v.order}`, String(used + 1), { expirationTtl: TTL });
      return Response.redirect(`${url.origin}/s/${id}`, 303);
    }

    let m;
    if (req.method === "GET" && (m = p.match(/^\/s\/([0-9a-f]{24})$/))) {
      const job = JSON.parse((await env.JOBS.get(`job:${m[1]}`)) || "null");
      if (!job) return page("Not found", `<h1>Link not found</h1><p>This link has expired or does not exist.</p><a class="btn" href="/">Create a star map</a>`);
      if (job.status === "ready")
        return page("Your star map is ready", `<div class="card"><h1>Your star map is ready</h1><p>${esc(job.text)} · ${esc(job.city)} · ${esc(job.date)}</p><a class="btn" href="/f/${job.id}">Download PDF</a><p><small>Prints sharp at A4, A3 and A2. The link works for 90 days.</small></p></div>`);
      if (job.status === "failed")
        return form(job, REASONS[job.reason] || REASONS.internal);
      return page("Creating your star map", `<div class="card"><h1>Your star map is being created</h1><p>We are calculating the sky over ${esc(job.city)} on ${esc(job.date)}. This is usually done within an hour.</p><p>Bookmark this page – it updates by itself.</p></div>`, 60);
    }

    if (req.method === "GET" && (m = p.match(/^\/f\/([0-9a-f]{24})$/))) {
      const pdf = await env.JOBS.get(`pdf:${m[1]}`, "arrayBuffer");
      if (!pdf) return new Response("Not found", { status: 404 });
      return new Response(pdf, { headers: { "content-type": "application/pdf", "content-disposition": `attachment; filename="star-map.pdf"` } });
    }

    // --- API för tillverkaren (GitHub Actions) ---
    if (p.startsWith("/api/")) {
      if (!(await authed(req, env))) return new Response("unauthorized", { status: 401 });
      if (req.method === "GET" && p === "/api/queue") {
        const list = await env.JOBS.list({ prefix: "pending:", limit: 100 });
        const jobs = [];
        for (const k of list.keys) {
          const j = await env.JOBS.get(`job:${k.name.slice(8)}`);
          if (j) jobs.push(JSON.parse(j)); else await env.JOBS.delete(k.name);
        }
        return Response.json({ jobs });
      }
      if (req.method === "POST" && (m = p.match(/^\/api\/(done|fail)\/([0-9a-f]{24})$/))) {
        const [, kind, id] = m;
        const job = JSON.parse((await env.JOBS.get(`job:${id}`)) || "null");
        if (!job) return new Response("no job", { status: 404 });
        if (kind === "done") {
          await env.JOBS.put(`pdf:${id}`, await req.arrayBuffer(), { expirationTtl: TTL });
          job.status = "ready"; job.done = new Date().toISOString();
        } else {
          const b = await req.json().catch(() => ({}));
          job.status = "failed"; job.reason = b.reason || "internal";
        }
        await env.JOBS.put(`job:${id}`, JSON.stringify(job), { expirationTtl: TTL });
        await env.JOBS.delete(`pending:${id}`);
        return Response.json({ ok: true });
      }
    }
    return new Response("Not found", { status: 404 });
  },
};
