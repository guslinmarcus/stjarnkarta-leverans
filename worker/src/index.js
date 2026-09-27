// Leveransportal för personliga stjärnkartor (Moodly Sverige).
// Köparen fyller i uppgifterna -> jobb i KV -> GitHub Actions tillverkar + kvalitetsgranskar -> PDF i KV.
const TTL = 60 * 60 * 24 * 90; // allt raderas efter 90 dagar

const TEXT_OK = /^[\p{Script=Latin}\p{N}\s.,&'’!?\-:+/()"“”]*$/u;
const TEXT_MSG = "Please use letters, numbers and simple punctuation only (symbols like hearts or emoji cannot be printed).";
const LANGS = { en: "English", sv: "Svenska", de: "Deutsch" };
// Stilar = samma som fulfil/stjarnkarta.py STYLES. Annonsens instruktions-PDF länkar hit med ?style=&palette=&frame=&font=
const STYLES = {
  midnatt: { label: "Midnight & gold", palettes: { guld: "Gold on midnight blue" } },
  minimal: { label: "Minimal", palettes: { svart: "Black on white", sand: "Charcoal on sand" } },
  akvarell: { label: "Watercolour sky", palettes: { natt: "Night blue", skymning: "Dusk violet", hav: "Ocean teal" } },
  hjarta: { label: "Heart of stars", palettes: { marin: "Navy and gold", vinrod: "Burgundy and gold" } },
  manfas: { label: "Moon phases", palettes: { kol: "Charcoal", skiffer: "Slate" } },
  barnrum: { label: "Nursery pastel", palettes: { rosa: "Soft pink", mint: "Mint", himmel: "Sky blue" } },
};
const FRAMES = ["ingen", "linje", "dubbel", "horn", "rundad"];
const FONTS = ["serif", "sans", "skrivstil", "rund"];
function pickStyle(src) {
  const st = STYLES[src.style] ? src.style : "";
  if (!st) return { style: "", palette: "", frame: "", font: "" };
  return { style: st, palette: STYLES[st].palettes[src.palette] ? src.palette : "",
           frame: FRAMES.includes(src.frame) ? src.frame : "", font: FONTS.includes(src.font) ? src.font : "" };
}

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

function styleFields(v) {
  if (!STYLES[v.style]) return "";
  const st = STYLES[v.style];
  const pal = Object.entries(st.palettes);
  const hidden = `<input type="hidden" name="style" value="${esc(v.style)}"><input type="hidden" name="frame" value="${esc(v.frame)}"><input type="hidden" name="font" value="${esc(v.font)}">`;
  if (pal.length < 2) return `${hidden}<input type="hidden" name="palette" value="${esc(pal[0][0])}"><p><small>Design: ${esc(st.label)}</small></p>`;
  const o = pal.map(([k, n]) => `<option value="${k}"${v.palette === k ? " selected" : ""}>${esc(n)}</option>`).join("");
  return `${hidden}<label for="palette">Colour (${esc(st.label)})</label><select id="palette" name="palette">${o}</select>`;
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
${styleFields(v)}
<button type="submit">Create my star map</button>
<p><small>We use these details only to make your map. They are deleted automatically after 90 days. Moodly Sverige.</small></p>
</form>`);
}

const REASONS = {
  city_not_found: "We could not find that city. Please check the spelling (or try the nearest larger town) and submit again.",
  quality_gate: "Our automatic quality check stopped this file. Please submit again – if it happens twice, contact us via Etsy messages.",
  internal: "Something went wrong on our side. Please submit again in a few minutes.",
  eclipse_not_visible: "The solar eclipse of 2 August 2027 cannot be seen from this place – the Moon's shadow does not reach it. Please check the place. If it is correct, contact us via Etsy messages and we will refund you.",
  address_not_found: "We could not find that address in Sweden. Please check the street name and number (or leave the address empty to centre the maps on the town) and submit again.",
  not_covered: "Lantmäteriet's historical maps do not cover this place well enough for your poster. Please contact us via Etsy messages and we will refund you.",
  places_count: "Please enter at least two places, each with a town, a country and a date.",
};

// --- Fler produkter: samma kö, fältet "product" väljer generator + grind i fulfil/fulfil.py (PRODUCTS).
// Länk från annonsens instruktions-PDF: /?p=formorkelse  eller  /?p=himmelskalender
const LANGS4 = { en: "English", sv: "Svenska", de: "Deutsch", es: "Español" };
const PRODUCTS = {
  formorkelse: {
    h1: "Create your solar eclipse guide",
    intro: "Enter your place. We calculate exactly when the solar eclipse of 2 August 2027 begins, peaks and ends where you are, how much of the Sun is covered and whether you are in the path of totality. Usually ready within an hour.",
    textLabel: "Text on the guide and poster (optional, e.g. names)", textPh: "The Lind family",
    button: "Create my eclipse guide", ready: "Your eclipse guide is ready",
    readyNote: "Pages 1–3: your guide (A4). Page 4: your poster (A3 – also prints well at A4 and A2). The link works for 90 days.",
    creating: "Your eclipse guide is being created", creatingText: (c) => `We are calculating the eclipse as seen from ${c}.`,
    file: "solar-eclipse-2027.pdf", langs: LANGS4,
  },
  himmelskalender: {
    h1: "Create your 2027 sky calendar",
    intro: "Enter your place. We calculate the Moon phases, sunrise and sunset for every day, the meteor showers, the eclipses and the planets of 2027 as seen from there. Usually ready within an hour.",
    textLabel: "Text on the cover (optional, e.g. a name)", textPh: "For Signe",
    button: "Create my sky calendar", ready: "Your sky calendar is ready",
    readyNote: "13 pages, A4 landscape: a cover and one page per month. The link works for 90 days.",
    creating: "Your sky calendar is being created", creatingText: (c) => `We are calculating the sky of 2027 over ${c}.`,
    file: "sky-calendar-2027.pdf", langs: LANGS4,
  },
};
// --- Kartprodukterna (fulfil/historisk.py, stadskarta.py, karlekskarta.py). Länkar: /?p=historisk, /?p=stadskarta, /?p=karlekskarta
const LANGS3 = { en: "English", sv: "Svenska", de: "Deutsch" };
const CITY_STYLES = { klassisk: "Classic (white)", natt: "Midnight gold", sepia: "Vintage sepia", blueprint: "Blueprint" };
const CITY_SIZES = { "1.5": "Neighbourhood (3 km across)", "3": "City centre (6 km across)", "5": "Whole city (10 km across)" };
const LOVE_STYLES = { ljus: "Light", natt: "Midnight" };
const sel = (name, opts, cur) => `<select id="${name}" name="${name}">` + Object.entries(opts).map(([k, n]) => `<option value="${k}"${String(cur) === k ? " selected" : ""}>${esc(n)}</option>`).join("") + `</select>`;
const cityField = (v) => `<div class="row"><div><label for="city">City or town</label><input id="city" name="city" required value="${esc(v.city)}" placeholder="Stockholm"></div>
<div><label for="country">Country</label><input id="country" name="country" required value="${esc(v.country)}" placeholder="Sweden"></div></div>`;
Object.assign(PRODUCTS, {
  historisk: {
    h1: "Your address through time",
    intro: "Enter an address in Sweden. We place the same view from three historical maps from Lantmäteriet (1827–1978) next to today's map, with years and a short guide. Usually ready within an hour.",
    textLabel: "Title on the poster (optional, e.g. a family name)", textPh: "The Nilsson family",
    button: "Create my maps", ready: "Your maps through time are ready",
    readyNote: "Page 1: your poster (A3, prints well at A4 and A2). Pages 2–5: your guide (A4). The link works for 90 days.",
    creating: "Your maps are being created", creatingText: (c) => `We are fetching the historical maps of ${c} from Lantmäteriet.`,
    file: "address-through-time.pdf", langs: { sv: "Svenska", en: "English" },
    fields: (v) => `<label for="address">Street address in Sweden (optional – leave empty to centre on the town)</label><input id="address" name="address" maxlength="80" value="${esc(v.address)}" placeholder="Stora Östergatan 20">
<label for="city">Town</label><input id="city" name="city" required value="${esc(v.city)}" placeholder="Ystad"><input type="hidden" name="country" value="Sverige">`,
    parse: (f) => ({ address: String(f.address || "").trim().slice(0, 80), country: "Sverige" }),
    validate: (v) => (v.city ? "" : "Please fill in the town."),
  },
  stadskarta: {
    h1: "Create your city map",
    intro: "Enter any town or city in the world. We draw a minimal poster of its streets, water and parks from OpenStreetMap, in the style you choose. Usually ready within an hour.",
    textLabel: "Title on the poster (optional – the city name is used if empty)", textPh: "Stockholm",
    button: "Create my city map", ready: "Your city map is ready",
    readyNote: "A3 portrait, vector PDF – prints sharp at A3, A2 and A1. The link works for 90 days.",
    creating: "Your city map is being created", creatingText: (c) => `We are drawing the streets of ${c}.`,
    file: "city-map.pdf", langs: LANGS3,
    fields: (v) => `${cityField(v)}<div class="row"><div><label for="style">Style</label>${sel("style", CITY_STYLES, v.style || "klassisk")}</div>
<div><label for="radius_km">Area</label>${sel("radius_km", CITY_SIZES, v.radius_km || "3")}</div></div>`,
    parse: (f) => ({ style: CITY_STYLES[f.style] ? f.style : "klassisk", radius_km: CITY_SIZES[f.radius_km] ? f.radius_km : "3" }),
  },
  karlekskarta: {
    h1: "Create your love story map",
    intro: "Enter 2–5 places that matter to you – where you met, your first home, where you got engaged – with their dates. We place them on a map, joined in date order, with the distance you have travelled together. Usually ready within an hour.",
    textLabel: "Names or title on the poster", textPh: "Anna & Erik",
    button: "Create my love map", ready: "Your love story map is ready",
    readyNote: "A3 portrait, vector PDF – prints sharp at A4, A3 and A2. The link works for 90 days.",
    creating: "Your love story map is being created", creatingText: (c) => `We are placing your places on the map, starting with ${c}.`,
    file: "love-story-map.pdf", langs: LANGS3,
    fields: (v) => {
      const pl = v.places || [];
      let h = `<label for="style">Style</label>${sel("style", LOVE_STYLES, v.style || "ljus")}`;
      for (let i = 0; i < 5; i++) {
        const p = pl[i] || {};
        h += `<p style="margin:18px 0 0"><small>Place ${i + 1}${i < 2 ? "" : " (optional)"}</small></p>
<div class="row"><div><label for="c${i}">Town</label><input id="c${i}" name="c${i}" value="${esc(p.city)}"${i < 2 ? " required" : ""}></div><div><label for="k${i}">Country</label><input id="k${i}" name="k${i}" value="${esc(p.country)}"${i < 2 ? " required" : ""}></div></div>
<div class="row"><div><label for="d${i}">Date</label><input id="d${i}" name="d${i}" type="date" min="1900-01-01" max="2100-12-31" value="${esc(p.date)}"${i < 2 ? " required" : ""}></div><div><label for="l${i}">Label (optional)</label><input id="l${i}" name="l${i}" maxlength="30" value="${esc(p.label)}" placeholder="${["Where we met", "First home", "Engaged", "Married", "Our home"][i]}"></div></div>`;
      }
      return h;
    },
    parse: (f) => {
      const places = [];
      for (let i = 0; i < 5; i++) {
        const city = String(f[`c${i}`] || "").trim().slice(0, 60), country = String(f[`k${i}`] || "").trim().slice(0, 60), date = String(f[`d${i}`] || "");
        if (city || country || date) places.push({ city, country, date, label: String(f[`l${i}`] || "").trim().slice(0, 30) });
      }
      return { places, style: LOVE_STYLES[f.style] ? f.style : "ljus", city: places[0] ? places[0].city : "", country: places[0] ? places[0].country : "" };
    },
    validate: (v) => (v.places.length >= 2 && v.places.every((p) => p.city && p.country && /^\d{4}-\d{2}-\d{2}$/.test(p.date)) ? "" : REASONS.places_count),
  },
});
function normProduct(s) {
  const k = String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  if (["historisk", "history", "address-through-time", "genom-tiden"].includes(k)) return "historisk";
  if (["stadskarta", "city-map", "citymap", "city"].includes(k)) return "stadskarta";
  if (["karlekskarta", "love-map", "lovemap", "love-story-map"].includes(k)) return "karlekskarta";
  if (["formorkelse", "solformorkelse", "eclipse", "solar-eclipse"].includes(k)) return "formorkelse";
  if (["himmelskalender", "kalender", "calendar", "sky-calendar"].includes(k)) return "himmelskalender";
  return "";
}
function productForm(prod, v = {}, error = "") {
  const P = PRODUCTS[prod];
  const opt = Object.entries(P.langs).map(([k, n]) => `<option value="${k}"${v.lang === k ? " selected" : ""}>${n}</option>`).join("");
  return page(P.h1, `
<h1>${esc(P.h1)}</h1>
<p>${esc(P.intro)}</p>
${error ? `<p class="err">${esc(error)}</p>` : ""}
<form method="post" action="/order">
<input type="hidden" name="product" value="${prod}">
<label for="order">Etsy order number</label><input id="order" name="order" inputmode="numeric" required value="${esc(v.order)}" placeholder="e.g. 3456789012">
<label for="text">${esc(P.textLabel)}</label><input id="text" name="text" maxlength="40" value="${esc(v.text)}" placeholder="${esc(P.textPh)}">
${P.fields ? P.fields(v) : `<div class="row"><div><label for="city">City or town</label><input id="city" name="city" required value="${esc(v.city)}" placeholder="Stockholm"></div>
<div><label for="country">Country</label><input id="country" name="country" required value="${esc(v.country)}" placeholder="Sweden"></div></div>`}
<label for="lang">Language</label><select id="lang" name="lang">${opt}</select>
<button type="submit">${esc(P.button)}</button>
<p><small>We use these details only to make your file. They are deleted automatically after 90 days. Moodly Sverige.</small></p>
</form>`);
}

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

    if (req.method === "GET" && p === "/") {
      const prod = normProduct(url.searchParams.get("p"));
      if (prod) return productForm(prod);
      return form(pickStyle(Object.fromEntries(url.searchParams)));
    }
    if (req.method === "GET" && p === "/privacy") return page("Privacy & terms", `<h1>Privacy &amp; terms</h1>
<p><b>Who we are.</b> This service is run by Moodly Sverige, a Swedish non-profit association (org.nr 802556-3845), Vaxholm, Sweden. Surplus funds support work for children's well-being.</p>
<p><b>What we collect.</b> Only what you enter: Etsy order number, the text for your poster, place, date, time and language. We use it only to create and deliver your file (star map, eclipse guide or sky calendar).</p>
<p><b>How long.</b> Everything, including your file, is deleted automatically after 90 days.</p>
<p><b>Sharing.</b> We never sell or share your details. They are processed by our hosting providers (Cloudflare, GitHub) only to run the service.</p>
<p><b>Your rights.</b> You can ask us to delete your data earlier or ask what we store, via the contact address below or Etsy messages.</p>
<p><b>Terms.</b> The star map is calculated from astronomical data for the place and time you enter. Please check your details before submitting; you can resubmit up to three times per order.</p>`);


    if (req.method === "POST" && p === "/order") {
      const f = Object.fromEntries((await req.formData()).entries());
      const prod = normProduct(f.product);
      if (prod) {
        const P = PRODUCTS[prod];
        const v = {
          product: prod, order: String(f.order || "").replace(/\D/g, "").slice(0, 14), text: String(f.text || "").trim().slice(0, 40),
          city: String(f.city || "").trim().slice(0, 60), country: String(f.country || "").trim().slice(0, 60),
          lang: P.langs[f.lang] ? f.lang : Object.keys(P.langs)[0],
        };
        if (P.parse) Object.assign(v, P.parse(f));
        if (!TEXT_OK.test(v.text)) return productForm(prod, v, TEXT_MSG);
        if (v.order.length < 6) return productForm(prod, v, "Please enter your Etsy order number (you find it in your Etsy purchase receipt).");
        const verr = P.validate ? P.validate(v) : (!v.city || !v.country ? "Please fill in the city and the country." : "");
        if (verr) return productForm(prod, v, verr);
        const used = parseInt((await env.JOBS.get(`order:${v.order}`)) || "0", 10);
        if (used >= 3) return productForm(prod, v, "This order number has already been used three times. Contact us via Etsy messages if you need a change.");
        const id = newId();
        const job = { id, ...v, status: "pending", created: new Date().toISOString() };
        await env.JOBS.put(`job:${id}`, JSON.stringify(job), { expirationTtl: TTL });
        await env.JOBS.put(`pending:${id}`, "1", { expirationTtl: TTL });
        await env.JOBS.put(`order:${v.order}`, String(used + 1), { expirationTtl: TTL });
        return Response.redirect(`${url.origin}/s/${id}`, 303);
      }
      const v = {
        order: String(f.order || "").replace(/\D/g, "").slice(0, 14), text: String(f.text || "").trim().slice(0, 40),
        city: String(f.city || "").trim().slice(0, 60), country: String(f.country || "").trim().slice(0, 60),
        date: String(f.date || ""), time: String(f.time || "21:00").slice(0, 5), lang: LANGS[f.lang] ? f.lang : "en",
        ...pickStyle(f),
      };
      if (v.order.length < 6) return form(v, "Please enter your Etsy order number (you find it in your Etsy purchase receipt).");
      if (!/^\d{4}-\d{2}-\d{2}$/.test(v.date) || !v.text || !v.city) return form(v, "Please fill in all fields.");
      if (!TEXT_OK.test(v.text)) return form(v, TEXT_MSG);
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
      if (PRODUCTS[job.product]) {
        const P = PRODUCTS[job.product];
        if (job.status === "ready")
          return page(P.ready, `<div class="card"><h1>${esc(P.ready)}</h1><p>${job.text ? esc(job.text) + " · " : ""}${esc(job.city)}</p><a class="btn" href="/f/${job.id}">Download PDF</a><p><small>${esc(P.readyNote)}</small></p></div>`);
        if (job.status === "failed") return productForm(job.product, job, REASONS[job.reason] || REASONS.internal);
        return page(P.creating, `<div class="card"><h1>${esc(P.creating)}</h1><p>${esc(P.creatingText(job.city))} This is usually done within an hour.</p><p>Bookmark this page – it updates by itself.</p></div>`, 60);
      }
      if (job.status === "ready")
        return page("Your star map is ready", `<div class="card"><h1>Your star map is ready</h1><p>${esc(job.text)} · ${esc(job.city)} · ${esc(job.date)}</p><a class="btn" href="/f/${job.id}">Download PDF</a><p><small>Prints sharp at A4, A3 and A2. The link works for 90 days.</small></p></div>`);
      if (job.status === "failed")
        return form(job, REASONS[job.reason] || REASONS.internal);
      return page("Creating your star map", `<div class="card"><h1>Your star map is being created</h1><p>We are calculating the sky over ${esc(job.city)} on ${esc(job.date)}. This is usually done within an hour.</p><p>Bookmark this page – it updates by itself.</p></div>`, 60);
    }

    if (req.method === "GET" && (m = p.match(/^\/f\/([0-9a-f]{24})$/))) {
      const pdf = await env.JOBS.get(`pdf:${m[1]}`, "arrayBuffer");
      if (!pdf) return new Response("Not found", { status: 404 });
      const fj = JSON.parse((await env.JOBS.get(`job:${m[1]}`)) || "null");
      const fname = fj && PRODUCTS[fj.product] ? PRODUCTS[fj.product].file : "star-map.pdf";
      return new Response(pdf, { headers: { "content-type": "application/pdf", "content-disposition": `attachment; filename="${fname}"` } });
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
