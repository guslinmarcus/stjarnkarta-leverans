// Leveransportal för personliga stjärnkartor (Moodly Sverige).
// Köparen fyller i uppgifterna -> jobb i KV -> GitHub Actions tillverkar + kvalitetsgranskar -> PDF i KV.
import GOLF_INDEX from "./golfindex.json";
import { golfCheckPage } from "./golfkolla.js";
const TTL = 60 * 60 * 24 * 90; // allt raderas efter 90 dagar

const TEXT_OK = /^[\p{Script=Latin}\p{N}\s.,&'’!?\-:+/()"“”]*$/u;
const TEXT_MSG = "Please use letters, numbers and simple punctuation only (symbols like hearts or emoji cannot be printed).";
const LANGS = { en: "English", sv: "Svenska", de: "Deutsch", fr: "Français" };
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
.row{display:flex;gap:12px}.card+.card{margin-top:18px}.card h2{font-weight:normal;font-size:21px;margin:0 0 6px}
.btn2{display:inline-block;margin-top:10px;padding:10px 18px;border:1px solid var(--gold);border-radius:6px;color:var(--gold);font:600 15px system-ui,sans-serif;text-decoration:none}.row>div{flex:1}.err{color:var(--err);font:15px system-ui,sans-serif}
small{color:var(--mute);font:13px system-ui,sans-serif}`;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function page(title, body, refresh, lang = "en") {
  return new Response(
    `<!doctype html><html lang="${lang === "sv" ? "sv" : "en"}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">` +
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
  date_out_of_range: "Please enter a date between 1 January 1900 and 31 December 2050.",
  people_count: "Family poster: please enter 2 to 6 people, each with a name and a date of birth (and a town and country, or the family's town above).",
  course_not_found: "We could not find a golf course with that name within 40 km of the town you entered. Please check the spelling, or pick one of the courses we found nearby (listed below) and submit again.",
  course_ambiguous: "More than one golf course matches that name. Please copy the exact name of your course from the list below and submit again.",
  course_holes_incomplete: "We found the course, but its holes are not completely drawn in OpenStreetMap yet (at least 9 numbered holes, each with a green, are needed for a correct map). Please contact us via Etsy messages and we will refund you.",
  hole_not_found: "The course does not have a hole with that number. Please check the hole number (or leave it empty) and submit again.",
  platser_for_langt_isar: "Two of your places are more than 15 km apart, which is too far for a printable find-your-way map. Please check the towns (or split them into two separate maps) and submit again.",
  ingen_vag_hittad: "We could not find a road route between two of your places in OpenStreetMap. Please check the towns and addresses, or contact us via Etsy messages and we will refund you.",
};

// --- Fler produkter: samma kö, fältet "product" väljer generator + grind i fulfil/fulfil.py (PRODUCTS).
// Länk från annonsens instruktions-PDF: /?p=formorkelse  eller  /?p=himmelskalender
const LANGS4 = { en: "English", sv: "Svenska", de: "Deutsch", es: "Español" };
const LANGS5 = { ...LANGS4, fr: "Français" }; // förmörkelsen har franska (fulfil PRODUCTS)
const PRODUCTS = {
  formorkelse: {
    h1: "Create your solar eclipse guide",
    intro: "Enter your place. We calculate exactly when the solar eclipse of 2 August 2027 begins, peaks and ends where you are, how much of the Sun is covered and whether you are in the path of totality. Usually ready within an hour.",
    textLabel: "Text on the guide and poster (optional, e.g. names)", textPh: "The Lind family",
    button: "Create my eclipse guide", ready: "Your eclipse guide is ready",
    readyNote: "Pages 1–3: your guide (A4). Page 4: your poster (A3 – also prints well at A4 and A2). The link works for 90 days.",
    creating: "Your eclipse guide is being created", creatingText: (c) => `We are calculating the eclipse as seen from ${c}.`,
    file: "solar-eclipse-2027.pdf", langs: LANGS5,
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
// --- Månfas-affischen (fulfil/manfas.py + grind_manfas.py). Länk: /?p=manfas  (valfritt &style=&mode=family&row=&heading=)
const MOON_STYLES = { mork: "Midnight & gold", ljus: "Minimal light", akvarell: "Watercolour", barnrum: "Nursery pastel" };
const MOON_ROWS = { none: "No extra row", month: "The Moon every day of that month", week: "The Moon the week around the day" };
const MOON_HEADINGS = { born: "The Moon on the day you were born", wedding: "The Moon on our wedding day", met: "The Moon on the night we met", none: "No heading" };
const MOON_MODES = { single: "One person", family: "Family – 2 to 6 people" };
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const TIME_RE = /^\d{2}:\d{2}$/;
function moonPick(src) {
  const pick = (o, k, d) => (Object.prototype.hasOwnProperty.call(o, src[k]) ? src[k] : d);
  return { style: pick(MOON_STYLES, "style", "mork"), row: pick(MOON_ROWS, "row", "none"), heading: pick(MOON_HEADINGS, "heading", "born"),
           mode: pick(MOON_MODES, "mode", "single") };
}
Object.assign(PRODUCTS, {
  manfas: {
    h1: "Create your Moon phase poster",
    intro: "Enter the date (and the time, if you know it) and the place. We calculate the exact phase of the Moon at that moment, how much of it was lit and which side – as seen from that place. Without a time we show the Moon at 12:00 noon. Usually ready within an hour.",
    textLabel: "Name on the poster (family poster: an optional title, e.g. “The Lind family”)", textPh: "Olivia",
    button: "Create my Moon poster", ready: "Your Moon phase poster is ready",
    readyNote: "Page 1: A3 portrait. Page 2: the same poster in A4. Vector PDF – also prints sharp at A2 and 30×40 / 50×70 cm (crop). The link works for 90 days.",
    creating: "Your Moon phase poster is being created", creatingText: (c) => `We are calculating the Moon as seen from ${c}.`,
    file: "moon-phase-poster.pdf", langs: LANGS5,
    fields: (v) => {
      const m = moonPick(v);
      const pl = v.people || [];
      let fam = "";
      for (let i = 0; i < 6; i++) {
        const p = pl[i] || {};
        fam += `<p style="margin:18px 0 0"><small>Person ${i + 1}${i < 2 ? "" : " (optional)"}</small></p>
<div class="row"><div><label for="pn${i}">Name</label><input id="pn${i}" name="pn${i}" maxlength="40" value="${esc(p.name)}"></div><div><label for="pd${i}">Date of birth</label><input id="pd${i}" name="pd${i}" type="date" min="1900-01-01" max="2050-12-31" value="${esc(p.date)}"></div></div>
<div class="row"><div><label for="pt${i}">Time (optional)</label><input id="pt${i}" name="pt${i}" type="time" value="${esc(p.time)}"></div><div><label for="pc${i}">Town (if not the family's)</label><input id="pc${i}" name="pc${i}" maxlength="60" value="${esc(p.city)}"></div><div><label for="pk${i}">Country</label><input id="pk${i}" name="pk${i}" maxlength="60" value="${esc(p.country)}"></div></div>`;
      }
      return `<label for="mode">Poster</label>${sel("mode", MOON_MODES, m.mode)}
<div id="single"><div class="row"><div><label for="date">Date</label><input id="date" name="date" type="date" min="1900-01-01" max="2050-12-31" value="${esc(v.date)}"></div>
<div><label for="time">Time (optional)</label><input id="time" name="time" type="time" value="${esc(v.time)}"></div></div>
<div class="row"><div><label for="heading">Heading</label>${sel("heading", MOON_HEADINGS, m.heading)}</div></div>
<label for="row">Extra row</label>${sel("row", MOON_ROWS, m.row)}</div>
${cityField(v)}<p><small>Family poster: this town is used for everyone who has no town of their own.</small></p>
<label for="style">Style</label>${sel("style", MOON_STYLES, m.style)}
<div id="family">${fam}</div>
<script>(function(){var s=document.getElementById("mode"),a=document.getElementById("single"),b=document.getElementById("family");function u(){var f=s.value==="family";a.style.display=f?"none":"";b.style.display=f?"":"none";}s.addEventListener("change",u);u();})();</script>`;
    },
    parse: (f) => {
      const m = moonPick(f);
      const people = [];
      if (m.mode === "family") {
        for (let i = 0; i < 6; i++) {
          const name = String(f[`pn${i}`] || "").trim().slice(0, 40), date = String(f[`pd${i}`] || "");
          const time = String(f[`pt${i}`] || "").slice(0, 5), city = String(f[`pc${i}`] || "").trim().slice(0, 60), country = String(f[`pk${i}`] || "").trim().slice(0, 60);
          if (name || date || city) people.push({ name, date, time: TIME_RE.test(time) ? time : "", city, country });
        }
      }
      const time = String(f.time || "").slice(0, 5);
      return { ...m, date: String(f.date || ""), time: TIME_RE.test(time) ? time : "", people, row: m.mode === "family" ? "none" : m.row };
    },
    validate: (v) => {
      if (!v.city || !v.country) return "Please fill in the town and the country.";
      const inRange = (d) => DATE_RE.test(d) && d >= "1900-01-01" && d <= "2050-12-31";
      if (v.mode === "family") {
        if (v.people.length < 2 || v.people.length > 6 || !v.people.every((p) => p.name && inRange(p.date))) return REASONS.people_count;
        if (!v.people.every((p) => TEXT_OK.test(p.name) && TEXT_OK.test(p.city) && TEXT_OK.test(p.country))) return TEXT_MSG;
        return "";
      }
      if (!v.text) return "Please enter the name for the poster.";
      if (!inRange(v.date)) return REASONS.date_out_of_range;
      return "";
    },
  },
});
// --- Golfbanekartan (fulfil/golfbana.py + grind_golfbana.py). Länk: /?p=golfbana (valfritt &style=)
const GOLF_STYLES = { klassisk: "Classic green", vintage: "Vintage drawing", minimal: "Minimal line", mork: "Dark & gold" };
Object.assign(PRODUCTS, {
  golfbana: {
    h1: "Create your golf course map",
    intro: "Enter the name of the golf course and the nearest town. We draw every hole, fairway, green, bunker and water hazard from OpenStreetMap, with the hole numbers and the par of each hole where it is mapped. You can mark one hole – for a hole-in-one or a favourite hole. Usually ready within an hour.",
    textLabel: "Line of text (optional, e.g. Hole in one, Our first round)", textPh: "Hole in one",
    button: "Create my golf course map", ready: "Your golf course map is ready",
    readyNote: "A3 portrait, vector PDF – prints sharp at A4, A3, A2 and 50×70 cm. The link works for 90 days.",
    creating: "Your golf course map is being created", creatingText: (c) => `We are drawing the course near ${c}.`,
    file: "golf-course-map.pdf", langs: LANGS3,
    fields: (v) => `<label for="course">Golf course name</label><input id="course" name="course" maxlength="80" required value="${esc(v.course)}" placeholder="Falsterbo Golfklubb">
<div class="row"><div><label for="city">Nearest town</label><input id="city" name="city" required value="${esc(v.city)}" placeholder="Höllviken"></div>
<div><label for="country">Country</label><input id="country" name="country" required value="${esc(v.country)}" placeholder="Sweden"></div></div>
<div class="row"><div><label for="hole">Hole to mark (optional)</label><input id="hole" name="hole" inputmode="numeric" maxlength="2" value="${esc(v.hole)}" placeholder="7"></div>
<div><label for="player">Player's name (optional)</label><input id="player" name="player" maxlength="40" value="${esc(v.player)}" placeholder="Anna Lind"></div></div>
<div class="row"><div><label for="date">Date (optional)</label><input id="date" name="date" type="date" min="1900-01-01" max="2100-12-31" value="${esc(v.date)}"></div>
<div><label for="style">Style</label>${sel("style", GOLF_STYLES, v.style || "klassisk")}</div></div>
<p><small>The course must be mapped in OpenStreetMap with its holes. <a style="color:#d9c9a0" href="/golf-kolla">Check your course first</a> – if it is not mapped well enough, you get a clear message here and a refund.</small></p>`,
    parse: (f) => {
      const hole = String(f.hole || "").replace(/\D/g, "").slice(0, 2);
      return { course: String(f.course || "").trim().slice(0, 80), hole, player: String(f.player || "").trim().slice(0, 40),
               date: DATE_RE.test(String(f.date || "")) ? String(f.date) : "", style: GOLF_STYLES[f.style] ? f.style : "klassisk" };
    },
    validate: (v) => {
      if (!v.course || !v.city || !v.country) return "Please fill in the golf course, the nearest town and the country.";
      if (!TEXT_OK.test(v.course) || !TEXT_OK.test(v.player)) return TEXT_MSG;
      if (v.hole && !(+v.hole >= 1 && +v.hole <= 36)) return REASONS.hole_not_found;
      return "";
    },
  },
});
// --- "Hitta hit"-kartan (fulfil/brollopskarta.py + grind_brollopskarta.py). Länk: /?p=brollopskarta
const BROLLOP_STYLES = { klassisk: "Classic (white)", natt: "Midnight gold", sepia: "Vintage sepia", blueprint: "Blueprint" };
const BROLLOP_ROLES = { vigsel: "Ceremony", mottagning: "Reception", hotell: "Hotel", parkering: "Parking", fest: "Party", annat: "Other place" };
Object.assign(PRODUCTS, {
  brollopskarta: {
    h1: "Create your find-your-way map",
    intro: "Enter 1 to 3 places for your wedding, party or celebration – the ceremony, the reception, a hotel or a car park. We draw a printable local map from OpenStreetMap with the places numbered and the route between them calculated on the real road network, with the distance and an approximate driving and walking time. Usually ready within an hour.",
    textLabel: "Names or title on the map", textPh: "Anna & Erik",
    button: "Create my map", ready: "Your find-your-way map is ready",
    readyNote: "One PDF with two pages: a card (A5) and a poster (A4), vector – prints sharp at both sizes. The link works for 90 days.",
    creating: "Your map is being created", creatingText: (c) => `We are drawing the roads and the route around ${c}.`,
    file: "find-your-way-map.pdf", langs: LANGS3,
    fields: (v) => {
      const pl = v.places || [];
      let h = `<label for="style">Style</label>${sel("style", BROLLOP_STYLES, v.style || "klassisk")}
<label for="date">Date (optional)</label><input id="date" name="date" type="date" min="1900-01-01" max="2100-12-31" value="${esc(v.date)}">`;
      for (let i = 0; i < 3; i++) {
        const p = pl[i] || {};
        h += `<p style="margin:18px 0 0"><small>Place ${i + 1}${i === 0 ? "" : " (optional)"}</small></p>
<div class="row"><div><label for="pr${i}">What is it</label>${sel(`pr${i}`, BROLLOP_ROLES, p.role || (i === 0 ? "vigsel" : i === 1 ? "mottagning" : "hotell"))}</div>
<div><label for="pl${i}">Your own label (optional)</label><input id="pl${i}" name="pl${i}" maxlength="30" value="${esc(p.label)}" placeholder="e.g. St Mary's Church"></div></div>
<div class="row"><div><label for="pc${i}">Town</label><input id="pc${i}" name="pc${i}" value="${esc(p.city)}"${i === 0 ? " required" : ""}></div><div><label for="pk${i}">Country</label><input id="pk${i}" name="pk${i}" value="${esc(p.country)}"${i === 0 ? " required" : ""}></div></div>
<label for="pa${i}">Street address (optional – leave empty to centre on the town)</label><input id="pa${i}" name="pa${i}" maxlength="80" value="${esc(p.address)}">`;
      }
      return h;
    },
    parse: (f) => {
      const places = [];
      for (let i = 0; i < 3; i++) {
        const city = String(f[`pc${i}`] || "").trim().slice(0, 60), country = String(f[`pk${i}`] || "").trim().slice(0, 60);
        if (!city && !country) continue;
        places.push({ role: BROLLOP_ROLES[f[`pr${i}`]] ? f[`pr${i}`] : "annat", label: String(f[`pl${i}`] || "").trim().slice(0, 30),
                     city, country, address: String(f[`pa${i}`] || "").trim().slice(0, 80) });
      }
      return { places, style: BROLLOP_STYLES[f.style] ? f.style : "klassisk", date: /^\d{4}-\d{2}-\d{2}$/.test(String(f.date || "")) ? String(f.date) : "",
               city: places[0] ? places[0].city : "", country: places[0] ? places[0].country : "" };
    },
    validate: (v) => {
      if (!(v.places.length >= 1 && v.places.length <= 3 && v.places[0].city && v.places[0].country)) return "Please fill in at least the first place (town and country).";
      if (!v.places.every((p) => TEXT_OK.test(p.city) && TEXT_OK.test(p.country) && TEXT_OK.test(p.address) && TEXT_OK.test(p.label))) return TEXT_MSG;
      return "";
    },
  },
});
// --- Födelsetavlan (fulfil/fodelsetavla.py + grind_fodelsetavla.py). Länk: /?p=fodelsetavla (valfritt &variant=husdjur&style=)
const FODELSE_STYLES = { natur: "Nature (warm, animals)", nordisk_minimal: "Nordic minimal (light)", nattstjarna: "Night stars (dark)", ballong: "Balloon (pastel)" };
const FODELSE_ACCENT = { rosa: "Blush pink", mint: "Soft mint" };
const FODELSE_VARIANTS = { barn: "A baby / child", husdjur: "A pet (“gotcha day”)" };
Object.assign(PRODUCTS, {
  fodelsetavla: {
    h1: "Create your birth poster",
    intro: "Enter the name, birth date and time, and place. We calculate the real night sky and the Moon's exact phase at that minute, as seen from that place – plus weight, length and, for Swedish places, the day's measured weather from SMHI. Usually ready within an hour.",
    textLabel: "Name", textPh: "Signe",
    button: "Create my birth poster", ready: "Your birth poster is ready",
    readyNote: "Page 1: A3 portrait. Page 2: the same poster in A4. Vector PDF – also prints sharp at A2 and 30×40 / 50×70 cm (crop). The link works for 90 days.",
    creating: "Your birth poster is being created", creatingText: (c) => `We are calculating the night sky and the Moon over ${c}.`,
    file: "birth-poster.pdf", langs: LANGS3,
    fields: (v) => {
      const variant = FODELSE_VARIANTS[v.variant] ? v.variant : "barn";
      const fam = v.family || [];
      let famH = "";
      for (let i = 0; i < 4; i++) famH += `<div><label for="fam${i}">Family member ${i + 1} (optional)</label><input id="fam${i}" name="fam${i}" maxlength="30" value="${esc(fam[i])}" placeholder="${["Mum Anna", "Dad Erik", "Big sister Wilma", "Big brother Max"][i]}"></div>`;
      return `<label for="variant">This poster is for</label>${sel("variant", FODELSE_VARIANTS, variant)}
<div class="row"><div><label for="date">${variant === "husdjur" ? "The day they came home" : "Date of birth"}</label><input id="date" name="date" type="date" min="1900-01-01" max="2050-12-31" required value="${esc(v.date)}"></div>
<div id="timewrap"><label for="time">Time (optional)</label><input id="time" name="time" type="time" value="${esc(v.time)}"></div></div>
${cityField(v)}
<label for="style">Style</label>${sel("style", FODELSE_STYLES, FODELSE_STYLES[v.style] ? v.style : "natur")}
<div id="accentwrap"><label for="accent">Accent colour (balloon style only)</label>${sel("accent", FODELSE_ACCENT, FODELSE_ACCENT[v.accent] ? v.accent : "rosa")}</div>
<div id="barnwrap">
<div class="row"><div><label for="weight_g">Weight in grams (optional)</label><input id="weight_g" name="weight_g" type="number" min="200" max="8000" value="${esc(v.weight_g)}" placeholder="3450"></div>
<div><label for="height_cm">Length in cm (optional)</label><input id="height_cm" name="height_cm" type="number" min="15" max="100" step="0.1" value="${esc(v.height_cm)}" placeholder="51"></div></div>
<label for="note">One extra line on the poster (optional)</label><input id="note" name="note" maxlength="60" value="${esc(v.note)}" placeholder="Our little wonder">
<p><small>Family names (optional, up to 4 – parents and siblings). Shown as a separate line.</small></p>${famH}
<label style="display:flex;align-items:center;gap:8px;font-weight:normal"><input type="checkbox" name="weather" value="1" style="width:auto"${v.weather ? " checked" : ""}> Include the day's weather (temperature, rain, sunshine) – Swedish places only, from SMHI's open data</label>
</div>
<script>(function(){var s=document.getElementById("variant"),bw=document.getElementById("barnwrap"),st=document.getElementById("style"),aw=document.getElementById("accentwrap");function u(){var pet=s.value==="husdjur";bw.style.display=pet?"none":"";aw.style.display=st.value==="ballong"?"":"none";}s.addEventListener("change",u);st.addEventListener("change",u);u();})();</script>`;
    },
    parse: (f) => {
      const variant = FODELSE_VARIANTS[f.variant] ? f.variant : "barn";
      const family = [];
      if (variant === "barn") for (let i = 0; i < 4; i++) { const n = String(f[`fam${i}`] || "").trim().slice(0, 30); if (n) family.push(n); }
      const time = String(f.time || "").slice(0, 5);
      return { variant, date: String(f.date || ""), time: variant === "husdjur" ? "" : (TIME_RE.test(time) ? time : ""),
               style: FODELSE_STYLES[f.style] ? f.style : "natur", accent: FODELSE_ACCENT[f.accent] ? f.accent : "rosa",
               weight_g: variant === "barn" ? String(f.weight_g || "").slice(0, 6) : "", height_cm: variant === "barn" ? String(f.height_cm || "").slice(0, 6) : "",
               note: variant === "barn" ? String(f.note || "").trim().slice(0, 60) : "", family, weather: variant === "barn" && f.weather === "1" };
    },
    validate: (v) => {
      if (!v.city || !v.country) return "Please fill in the town and the country.";
      if (!(DATE_RE.test(v.date) && v.date >= "1900-01-01" && v.date <= "2050-12-31")) return REASONS.date_out_of_range;
      if (!v.text) return "Please enter the name for the poster.";
      if (!v.family.every((n) => TEXT_OK.test(n))) return TEXT_MSG;
      if (v.note && !TEXT_OK.test(v.note)) return TEXT_MSG;
      return "";
    },
  },
});
function normProduct(s) {
  const k = String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  if (["historisk", "history", "address-through-time", "genom-tiden"].includes(k)) return "historisk";
  if (["stadskarta", "city-map", "citymap", "city"].includes(k)) return "stadskarta";
  if (["karlekskarta", "love-map", "lovemap", "love-story-map"].includes(k)) return "karlekskarta";
  if (["formorkelse", "solformorkelse", "eclipse", "solar-eclipse"].includes(k)) return "formorkelse";
  if (["fodelsetavla", "birth-poster", "birthposter", "birth-stats", "geburtsposter", "gotcha-day"].includes(k)) return "fodelsetavla";
  if (["himmelskalender", "kalender", "calendar", "sky-calendar"].includes(k)) return "himmelskalender";
  if (["golfbana", "golf", "golf-course", "golf-course-map", "golfplatz", "campo-de-golf"].includes(k)) return "golfbana";
  if (["manfas", "moon", "moon-phase", "moonphase", "birth-moon", "mondphase", "fase-lunar", "phase-lune"].includes(k)) return "manfas";
  if (["brollopskarta", "wedding-map", "weddingmap", "find-your-way-map", "findyourwaymap", "hochzeitskarte"].includes(k)) return "brollopskarta";
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
${v.options && v.options.length ? `<p><small>Courses we found:</small></p><ul>${v.options.map((o) => `<li><small>${esc(o)}</small></li>`).join("")}</ul>` : ""}
<button type="submit">${esc(P.button)}</button>
<p><small>We use these details only to make your file. They are deleted automatically after 90 days. Moodly Sverige.</small></p>
</form>`);
}

// --- Klubbkalendern 2027 (fulfil/foreningskalender.py + grind_foreningskalender.py). Länk: /kalender
// Eget sortiment: säljs direkt till föreningar (inte via Etsy), därför eget formulär/flöde utanför PRODUCTS ovan
// (inget Etsy-ordernummer, inget språkval – bara svenska helgdagar/namnsdagar, se FORENINGAR.md).
const KAL_STYLES = { klassisk: "Klassisk", mork: "Midnight", lekfull: "Lekfull", minimal: "Minimal" };
const KAL_TYP = { match: "Match", cup: "Cup", training: "Träning/läger", event: "Övrigt" };
const KAL_HEX = /^#[0-9a-fA-F]{6}$/;
const KAL_EVENT_ROWS = 8, KAL_SPONSOR_ROWS = 4;
const KAL_REASONS = {
  club_name_missing: "Fyll i klubbens namn.",
  club_color_invalid: "Ange minst en giltig klubbfärg.",
  quality_gate: "Vår automatiska kvalitetskontroll stoppade kalendern. Skicka in igen – hör av dig till oss om det händer två gånger.",
  internal: "Något gick fel på vår sida. Försök igen om en liten stund.",
};
function kalenderEventRow(i, e = {}) {
  const opt = Object.entries(KAL_TYP).map(([k, n]) => `<option value="${k}"${e.type === k ? " selected" : ""}>${n}</option>`).join("");
  return `<div class="row" style="margin-top:10px"><div style="flex:0 0 150px"><label for="ed${i}">Datum</label><input id="ed${i}" name="ed${i}" type="date" min="2027-01-01" max="2027-12-31" value="${esc(e.date)}"></div>
<div><label for="et${i}">Rubrik</label><input id="et${i}" name="et${i}" maxlength="40" value="${esc(e.title)}" placeholder="Hemma vs Any BK"></div>
<div style="flex:0 0 140px"><label for="ey${i}">Typ</label><select id="ey${i}" name="ey${i}">${opt}</select></div>
<div style="flex:0 0 80px"><label style="visibility:hidden">Hemma</label><label style="display:flex;align-items:center;gap:6px;font:15px system-ui,sans-serif;color:var(--ink)"><input type="checkbox" id="eh${i}" name="eh${i}" value="1" style="width:auto"${e.home ? " checked" : ""}> Hemma</label></div></div>`;
}
function kalenderSponsorRow(i, s = "") {
  return `<div style="margin-top:8px"><label for="s${i}" style="${i ? "position:absolute;width:1px;height:1px;overflow:hidden" : ""}">Sponsor ${i + 1}</label><input id="s${i}" name="s${i}" maxlength="30" value="${esc(s)}" placeholder="Sponsor AB"></div>`;
}
function foreningskalenderForm(v = {}, error = "") {
  const club = v.club || {};
  const events = v.events || []; const sponsors = v.sponsors || [];
  const optStyle = Object.entries(KAL_STYLES).map(([k, n]) => `<label style="display:inline-flex;align-items:center;gap:6px;background:#0d1426;border:1px solid #33405f;border-radius:6px;padding:8px 12px;margin:4px 8px 0 0;font:15px system-ui,sans-serif"><input type="radio" name="style" value="${k}" style="width:auto"${(v.style || "klassisk") === k ? " checked" : ""}> ${n}</label>`).join("");
  return page("Klubbkalender 2027", `
<h1>Klubbkalender 2027</h1>
<p>Fyll i klubbens uppgifter, matcher och andra viktiga datum. Vi skapar en tryckfärdig väggkalender (13 sidor: omslag
+ jan–dec) med svenska helgdagar, namnsdagar och månfaser automatiskt. Klart inom en timme.</p>
${error ? `<p class="err">${esc(error)}</p>` : ""}
<form method="post" action="/kalender">
<label for="namn">Klubbens namn</label><input id="namn" name="namn" required maxlength="60" value="${esc(club.name)}" placeholder="IFK Exempel">
<div class="row"><div><label for="lag">Lag/grupp (valfritt)</label><input id="lag" name="lag" maxlength="20" value="${esc(club.team)}" placeholder="P14"></div>
<div><label for="sport">Idrott (valfritt)</label><input id="sport" name="sport" maxlength="30" value="${esc(club.sport)}" placeholder="Fotboll"></div></div>
<label for="venue">Hemmaplan (valfritt)</label><input id="venue" name="venue" maxlength="60" value="${esc(club.venue)}" placeholder="Exempelvallen">
<div class="row"><div><label for="farg1">Klubbfärg 1</label><input id="farg1" name="farg1" type="color" style="width:56px;padding:2px" value="${esc((club.colors || [])[0]) || "#1a6b3a"}"></div>
<div><label for="farg2">Klubbfärg 2 (valfri)</label><input id="farg2" name="farg2" type="color" style="width:56px;padding:2px" value="${esc((club.colors || [])[1]) || "#ffffff"}"></div></div>
<label>Stil</label><div>${optStyle}</div>
<label for="format">Tryckformat (liggande)</label><select id="format" name="format">
<option value="A4"${(v.format || "A4") === "A4" ? " selected" : ""}>A4 liggande – går att beställa tryckt</option>
<option value="A3"${v.format === "A3" ? " selected" : ""}>A3 liggande – bara digital pdf för eget tryck</option>
</select>
<label>Matcher och viktiga datum (valfritt, upp till ${KAL_EVENT_ROWS})</label>
${Array.from({ length: KAL_EVENT_ROWS }, (_, i) => kalenderEventRow(i, events[i])).join("")}
<label style="margin-top:18px">Sponsorer i sidfoten (valfritt, upp till ${KAL_SPONSOR_ROWS})</label>
${Array.from({ length: KAL_SPONSOR_ROWS }, (_, i) => kalenderSponsorRow(i, sponsors[i])).join("")}
<label for="kontakt">Kontakt (e-post eller telefon, för leverans av kalendern)</label><input id="kontakt" name="kontakt" required maxlength="80" value="${esc(v.kontakt)}" placeholder="namn@klubben.se">
<p style="margin-top:16px"><label style="display:flex;align-items:flex-start;gap:8px;font:15px system-ui,sans-serif;color:var(--ink)">
<input type="checkbox" name="samtycke" required value="1" style="width:auto;margin-top:3px">
Jag intygar att klubben har vårdnadshavarnas samtycke för de uppgifter som eventuellt läggs till, och har läst
<a style="color:var(--gold)" href="/kalender-villkor" target="_blank">villkoren</a> om personuppgifter och radering.</label></p>
<button type="submit">Skapa min klubbkalender</button>
<p><small>Vi använder uppgifterna bara för att skapa er kalender. Text raderas automatiskt efter 12 månader, eventuella
bilder efter högst 60 dagar. Moodly Sverige.</small></p>
</form>`, undefined, "sv");
}
function kalenderVillkorPage() {
  return page("Villkor – Klubbkalender 2027", `<h1>Villkor – Klubbkalender 2027</h1>
<p><b>Vem ansvarar för vad.</b> Föreningen som beställer kalendern ansvarar för innehållet ni skickar in: klubbens
namn, matcher, sponsorer och eventuella bilder. Moodly Sverige (org.nr 802556-3845) levererar den tekniska tjänsten
men är inte den som samlat in eller publicerat era uppgifter.</p>
<p><b>Bilder av barn och unga.</b> Om ni längre fram laddar upp bilder som visar spelare: föreningen ansvarar för
samtycke från vårdnadshavarna. Bilderna visas aldrig offentligt av oss – bara i er egen kalenderfil. Vi raderar
uppladdade bilder senast 60 dagar efter att kalendern är levererad. Vi rekommenderar förnamn (inte efternamn) om ni
tar med namn.</p>
<p><b>Vad som sker med er data.</b> Klubbnamn, lag, färger, matcher, sponsorer (text): lagras hos oss till dess ni
raderar beställningen, dock max 12 månader. Uppladdade bilder (senare tillval): max 60 dagar efter leverans.
Kontaktuppgiften ni anger: så länge som krävs för bokföring (7 år) för själva köpet.</p>
<p><b>Vad ni får.</b> En pdf-fil med den färdiga kalendern och, om ni valt tryck, en tryckfärdig fil som skickas till
vårt tryckeri för produktion. Moodly Sverige är personuppgiftsbiträde för uppgifter föreningen skickar in om sina
medlemmar – föreningen är personuppgiftsansvarig.</p>
<p><small>Utkast – inte juridisk rådgivning. Frågor: <a style="color:var(--gold)" href="mailto:guslinmarcus@gmail.com">guslinmarcus@gmail.com</a>.</small></p>`, undefined, "sv");
}
function parseKalender(f) {
  const events = [];
  for (let i = 0; i < KAL_EVENT_ROWS; i++) {
    const date = String(f[`ed${i}`] || ""), title = String(f[`et${i}`] || "").trim().slice(0, 40);
    if (!date && !title) continue;
    events.push({ date, title, type: KAL_TYP[f[`ey${i}`]] ? f[`ey${i}`] : "event", home: f[`eh${i}`] === "1" });
  }
  const sponsors = [];
  for (let i = 0; i < KAL_SPONSOR_ROWS; i++) { const s = String(f[`s${i}`] || "").trim().slice(0, 30); if (s) sponsors.push(s); }
  const colors = [String(f.farg1 || "").trim(), String(f.farg2 || "").trim()].filter((h) => KAL_HEX.test(h) && h.toLowerCase() !== "#ffffff");
  const farg1 = String(f.farg1 || "").trim();
  if (KAL_HEX.test(farg1) && !colors.includes(farg1)) colors.unshift(farg1);
  return {
    club: { name: String(f.namn || "").trim().slice(0, 60), team: String(f.lag || "").trim().slice(0, 20),
            sport: String(f.sport || "").trim().slice(0, 30), venue: String(f.venue || "").trim().slice(0, 60), colors: colors.slice(0, 2) },
    events, sponsors, style: KAL_STYLES[f.style] ? f.style : "klassisk", format: f.format === "A3" ? "A3" : "A4",
    kontakt: String(f.kontakt || "").trim().slice(0, 80),
  };
}
function validateKalender(v) {
  if (!v.club.name) return KAL_REASONS.club_name_missing;
  if (!v.club.colors.length) return KAL_REASONS.club_color_invalid;
  if (!TEXT_OK.test(v.club.name) || !TEXT_OK.test(v.club.team) || !TEXT_OK.test(v.club.sport) || !TEXT_OK.test(v.club.venue)) return TEXT_MSG;
  if (!v.events.every((e) => e.date && e.title)) return "Varje datum behöver både datum och en rubrik – ta bort ofullständiga rader.";
  if (!v.events.every((e) => TEXT_OK.test(e.title))) return TEXT_MSG;
  if (!v.sponsors.every((s) => TEXT_OK.test(s))) return TEXT_MSG;
  if (v.kontakt.length < 5) return "Fyll i en kontaktuppgift (e-post eller telefon) så vi kan nå er om något är oklart.";
  return "";
}

// --- Efter nedladdning: be om en ärlig recension + visa EN relaterad produkt för samma datum/ort.
// Etsys regler (Reviews/Extortion/Shilling): inga motprestationer för en recension, inget tryck, inte be om "5 stjärnor".
// Därför: ingen rabatt, ingen koppling mellan recension och erbjudande, och erbjudandet visas för alla köpare lika.
// Erbjudandet länkar bara till Etsy-annonsen (ingen egen kassa). Listing-id fylls i när annonserna är publicerade –
// antingen här eller via miljövariabeln ETSY_LISTINGS (JSON {"formorkelse":"123..."}). Saknas id visas inget erbjudande
// (DEV_PLACEHOLDERS=1 visar platshållare vid lokal test).
const LISTING_IDS = {
  stjarnkarta: "LISTING_ID_STJARNKARTA", stjarnkarta_manfas: "LISTING_ID_STJARNKARTA_MANFAS",
  formorkelse: "LISTING_ID_FORMORKELSE", himmelskalender: "LISTING_ID_HIMMELSKALENDER",
  historisk: "LISTING_ID_HISTORISK", stadskarta: "LISTING_ID_STADSKARTA", karlekskarta: "LISTING_ID_KARLEKSKARTA",
  golfbana: "LISTING_ID_GOLFBANA", brollopskarta: "LISTING_ID_BROLLOPSKARTA",
};
const ECLIPSE_BEFORE = "2027-08-02"; // förmörkelseguiden erbjuds bara före händelsen
function crossSell(job, today = new Date().toISOString().slice(0, 10)) {
  const prod = PRODUCTS[job.product] ? job.product : "stjarnkarta";
  const c = esc(job.city), d = esc(job.date);
  const eclipse = today < ECLIPSE_BEFORE ? { key: "formorkelse", title: "The solar eclipse over " + c,
    text: `On 2 August 2027 the Moon covers the Sun across Europe and North Africa. A personal guide for ${c} shows the exact times and how much of the Sun is covered there.` } : null;
  const calendar = { key: "himmelskalender", title: "The sky of 2027 over " + c,
    text: `A printable 2027 calendar calculated for ${c}: Moon phases, sunrise and sunset, meteor showers and the planets.` };
  const table = {
    stjarnkarta: job.style === "manfas" ? [eclipse, calendar] : [
      { key: "stjarnkarta_manfas", title: "The same night, with the Moon",
        text: `A star map for ${c} on ${d} with a row of seven Moons showing the Moon's phase that week.` }, eclipse],
    formorkelse: [calendar],
    himmelskalender: [eclipse, { key: "stjarnkarta", title: "The night sky over " + c,
      text: `A star map of the sky over ${c} on a date that matters to you.` }],
    historisk: [{ key: "stadskarta", title: c + " today, as a city map", text: `A minimal poster of the streets, water and parks of ${c} from OpenStreetMap.` }],
    stadskarta: [(job.country || "").toLowerCase().match(/^(sverige|sweden|se)$/)
      ? { key: "historisk", title: c + " through time", text: `The same place on historical maps from the 1800s to today, next to today's map.` }
      : { key: "stjarnkarta", title: "The night sky over " + c, text: `A star map of the sky over ${c} on a date that matters to you.` }],
    golfbana: [{ key: "stadskarta", title: c + " as a city map", text: `A minimal poster of the streets, water and parks of ${c} from OpenStreetMap.` }],
    karlekskarta: [{ key: "stjarnkarta", title: "The sky over your first place",
      text: `A star map of the night sky over ${c} on ${esc((job.places && job.places[0] && job.places[0].date) || "the date of your first place")}.` }],
    brollopskarta: [{ key: "stjarnkarta", title: "The night sky over " + c,
      text: `A star map of the sky over ${c} on the day itself, to go with your find-your-way map.` }],
  };
  return (table[prod] || []).filter(Boolean)[0] || null;
}
function listingUrl(key, env) {
  let ids = LISTING_IDS;
  try { if (env && env.ETSY_LISTINGS) ids = { ...LISTING_IDS, ...JSON.parse(env.ETSY_LISTINGS) }; } catch (e) {}
  const id = String(ids[key] || "");
  if (/^\d{6,}$/.test(id)) return `https://www.etsy.com/listing/${id}`;
  return env && env.DEV_PLACEHOLDERS === "1" ? `https://www.etsy.com/listing/${id}` : "";
}
function afterDownload(job, env) {
  const review = `<div class="card"><h2>A small favour</h2><p>We are a very small shop. If you have a minute once you have seen your file, an honest review on Etsy helps other people find us – whatever you think of it.</p><a class="btn2" href="https://www.etsy.com/your/purchases" rel="noopener">Leave a review on Etsy</a><p><small>Etsy: Your account › Purchases and reviews. Something not right? Send us an Etsy message and we will fix it.</small></p></div>`;
  const x = crossSell(job), href = x && listingUrl(x.key, env);
  const offer = x && href ? `<div class="card"><h2>${x.title}</h2><p>${x.text}</p><a class="btn2" href="${esc(href)}" rel="noopener">See it on Etsy</a><p><small>Available to everyone, whether or not you leave a review.</small></p></div>` : "";
  return review + offer;
}

function newId() {
  const b = new Uint8Array(12); crypto.getRandomValues(b);
  return [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
}

async function authed(req, env) {
  return req.headers.get("authorization") === `Bearer ${env.FULFIL_SECRET}`;
}

// --- Julkulan (tryck/julkula_kedja.py + tryck/kallor.py:Portal.ladda_upp_proof, ursprungligt förslag i
// tryck/julkula_portal_tillagg.js, inlagt 2026-09-28): kundfoto-uppladdning + digital proof (godkänn/ändra,
// max 3 omgångar). Enda produkten som tar emot riktiga kundfoton - övriga produkter ovan tar bara text. Rent
// ADDITIVT: egna nycklar (julkulafoto:<token> för bildbytes, job:julkula-<ordernummer> för jobbet), rör aldrig
// job:<24-hex-id>-formatet ovan. ÅTERANVÄNDER dock orderjob:<order>:<id>-indexet oförändrat, så den redan
// driftade GET /api/order/<ordernummer> (nedan) hittar julkula-jobbet utan att den koden ändras.
const JULKULA_TTL = 60 * 60 * 24 * 60; // 60 dagar - GDPR (Marcus-uppdrag 2026-09-28): kundfoton raderas automatiskt
const JULKULA_BILDTYPER = { "image/jpeg": 1, "image/png": 1, "image/webp": 1 };
const JULKULA_MAX_BYTES = 10 * 1024 * 1024;

const julkulaJobId = (ordernummer) => `julkula-${ordernummer}`;

async function julkulaJobb(env, ordernummer) {
  return JSON.parse((await env.JOBS.get(`job:${julkulaJobId(ordernummer)}`)) || "null");
}

async function julkulaSpara(env, ordernummer, patch) {
  const id = julkulaJobId(ordernummer);
  const jobb = (await julkulaJobb(env, ordernummer)) || { id, product: "julkula", order: ordernummer, created: new Date().toISOString() };
  Object.assign(jobb, patch);
  await env.JOBS.put(`job:${id}`, JSON.stringify(jobb), { expirationTtl: JULKULA_TTL });
  await env.JOBS.put(`orderjob:${ordernummer}:${id}`, "1", { expirationTtl: JULKULA_TTL }); // samma index /api/order/ redan läser
  return jobb;
}

function julkulaToken() {
  return [...crypto.getRandomValues(new Uint8Array(16))].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function julkulaSparaBild(env, bytes, contentType, extraMeta) {
  const token = julkulaToken();
  await env.JOBS.put(`julkulafoto:${token}`, bytes, { expirationTtl: JULKULA_TTL, metadata: { content_type: contentType, ...extraMeta } });
  return token;
}

function julkulaB64ToBytes(b64) {
  const bin = atob(String(b64 || ""));
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function julkulaProofPage(ref, jobb, error) {
  const proof = jobb && jobb.proof;
  if (!proof) return page("Not ready yet", `<h1>Not ready yet</h1><p>Your ornament preview is not ready yet - we will email you a link as soon as it is (usually within a day or two).</p>`);
  const svarad = jobb.proof_svar && jobb.proof_svar.omgang === proof.omgang;
  return page("Your ornament preview", `
<h1>Your ornament preview</h1>
<p>Round ${proof.omgang} of 3. Please look closely at both sides before approving - once you approve, we send it to production.</p>
<div class="row"><div><img src="${esc(proof.framsida_url)}" alt="Front" style="width:100%;border-radius:10px"></div>
<div><img src="${esc(proof.baksida_url)}" alt="Back" style="width:100%;border-radius:10px"></div></div>
${error ? `<p class="err">${esc(error)}</p>` : ""}
${svarad ? `<div class="card"><p>Thank you - we have received your answer for round ${proof.omgang}. We will be in touch by email.</p></div>` : `
<form method="post" action="/proof/${esc(ref)}">
<input type="hidden" name="omgang" value="${proof.omgang}">
<button type="submit" name="val" value="godkann">Approve</button>
<p style="margin:18px 0 0"><small>Not quite right? Describe what to change, then request a change instead:</small></p>
<label for="feedback">What should we change (optional)</label><input id="feedback" name="feedback" maxlength="500">
<button type="submit" name="val" value="andra" class="btn2">Request a change</button>
</form>`}
<p><small>Up to 3 rounds of changes are included. Moodly Sverige.</small></p>`);
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const p = url.pathname;
    let m; // flyttad hit (2026-09-28, julkula-tillägget): används nu av rutter före /order också

    if (req.method === "GET" && p === "/") {
      const prod = normProduct(url.searchParams.get("p"));
      if (prod) {
        const q = Object.fromEntries(url.searchParams);
        return productForm(prod, prod === "manfas" ? moonPick(q) : prod === "golfbana" ? { style: GOLF_STYLES[q.style] ? q.style : "klassisk", course: String(q.course || "").slice(0, 80),
          city: String(q.city || "").slice(0, 60), country: String(q.country || "").slice(0, 60) } : {});
      }
      return form(pickStyle(Object.fromEntries(url.searchParams)));
    }
    // förhandskollen för golfbanekartan: fungerar banan innan köpet? (annonsen länkar hit)
    if (req.method === "GET" && ["/golf-kolla", "/golf-check", "/golf"].includes(p)) {
      return golfCheckPage(GOLF_INDEX, Object.fromEntries(url.searchParams), esc, page);
    }
    if (req.method === "GET" && p === "/kalender") return foreningskalenderForm({});
    if (req.method === "GET" && p === "/kalender-villkor") return kalenderVillkorPage();
    if (req.method === "POST" && p === "/kalender") {
      const f = Object.fromEntries((await req.formData()).entries());
      const v = parseKalender(f);
      const verr = validateKalender(v);
      if (verr) return foreningskalenderForm(v, verr);
      const id = newId();
      const job = { id, product: "foreningskalender", club: v.club, events: v.events, sponsors: v.sponsors,
                    style: v.style, format: v.format, kontakt: v.kontakt, status: "pending", created: new Date().toISOString() };
      await env.JOBS.put(`job:${id}`, JSON.stringify(job), { expirationTtl: TTL });
      await env.JOBS.put(`pending:${id}`, "1", { expirationTtl: TTL });
      return Response.redirect(`${url.origin}/s/${id}`, 303);
    }
    if (req.method === "GET" && p === "/privacy") return page("Privacy & terms", `<h1>Privacy &amp; terms</h1>
<p><b>Who we are.</b> This service is run by Moodly Sverige, a Swedish non-profit association (org.nr 802556-3845), Vaxholm, Sweden. Surplus funds support work for children's well-being.</p>
<p><b>What we collect.</b> Only what you enter: Etsy order number, the text for your poster, place(s), date, time, language and – for golf course maps – the course, hole and player name. We use it only to create and deliver your file (star map, eclipse guide, sky calendar, map, find-your-way map, golf course map or Moon phase poster). The club calendar (<a style="color:#d9c9a0" href="/kalender">/kalender</a>) is sold directly to sports clubs, not through Etsy – see its own <a style="color:#d9c9a0" href="/kalender-villkor">terms</a> for what is collected and deleted.</p>
<p><b>How long.</b> Everything, including your file, is deleted automatically after 90 days.</p>
<p><b>Sharing.</b> We never sell or share your details. They are processed by our hosting providers (Cloudflare, GitHub) only to run the service. For a physical print (poster, framed print or canvas), your print file and delivery address are also shared with our print partner Gelato, who prints and ships the item directly to you.</p>
<p><b>Custom ornament photos.</b> If you order a custom cartoon ornament, the photo(s) you upload are used only to make your ornament and its preview, and shared with our print partner Printify for manufacturing. They are deleted automatically 60 days after upload - sooner than our other files.</p>
<p><b>Your rights.</b> You can ask us to delete your data earlier or ask what we store, via the contact address below or Etsy messages.</p>
<p><b>Terms.</b> The star map is calculated from astronomical data for the place and time you enter. Please check your details before submitting; you can resubmit up to three times per order.</p>`);


    // --- Julkulan: kundfoto-uppladdning (publik, kopplad till Etsy-ordernumret - tryck/julkula_kedja.py:s_vantar_foto)
    if (req.method === "POST" && p === "/julkula/foto") {
      let body;
      try { body = await req.json(); } catch (e) { return new Response("bad json", { status: 400 }); }
      const ordernummer = String(body.ordernummer || "").replace(/\D/g, "");
      if (!/^\d{6,14}$/.test(ordernummer)) return new Response("ogiltigt ordernummer", { status: 400 });
      const bilder = Array.isArray(body.bilder) ? body.bilder : [];
      if (!bilder.length || bilder.length > 2) return new Response("1-2 bilder krävs", { status: 400 });
      const sparade = [];
      for (const b of bilder) {
        const ct = String((b && b.content_type) || "");
        if (!JULKULA_BILDTYPER[ct]) return new Response("filtyp (endast jpeg/png/webp)", { status: 400 });
        let bytes;
        try { bytes = julkulaB64ToBytes(b && b.data_base64); } catch (e) { return new Response("ogiltig bilddata", { status: 400 }); }
        if (!bytes.byteLength) return new Response("tom bild", { status: 400 });
        if (bytes.byteLength > JULKULA_MAX_BYTES) return new Response("för stor fil (max 10 MB)", { status: 413 });
        const typ = ["framsida", "baksida"].includes(b.typ) ? b.typ : "framsida";
        const token = await julkulaSparaBild(env, bytes, ct, { ordernummer, typ });
        sparade.push({ typ, url: `${url.origin}/julkula/f/${token}` });
      }
      const jobb = await julkulaJobb(env, ordernummer);
      const foton = (jobb && jobb.foton) || [];
      for (const s of sparade) {
        const i = foton.findIndex((f) => f.typ === s.typ);
        if (i >= 0) foton[i] = s; else foton.push(s);
      }
      await julkulaSpara(env, ordernummer, { foton: foton.slice(-2) });
      return Response.json({ ok: true, foton: sparade });
    }
    if (req.method === "GET" && (m = p.match(/^\/julkula\/f\/([0-9a-f]{32})$/))) {
      const { value, metadata } = await env.JOBS.getWithMetadata(`julkulafoto:${m[1]}`, "arrayBuffer");
      if (!value) return new Response("Not found", { status: 404 });
      return new Response(value, { headers: { "content-type": (metadata && metadata.content_type) || "application/octet-stream",
                                                "cache-control": "private, no-store", "x-robots-tag": "noindex" } });
    }
    // Proof-sidan kunden ser (ingen inloggning - länken i mejlet har ordernumret, samma mönster som /t/<token>.pdf)
    if (req.method === "GET" && (m = p.match(/^\/proof\/(etsy-\d+-\d+)$/))) {
      const jobb = await julkulaJobb(env, m[1].split("-")[1]);
      return julkulaProofPage(m[1], jobb);
    }
    if (req.method === "POST" && (m = p.match(/^\/proof\/(etsy-\d+-\d+)$/))) {
      const ordernummer = m[1].split("-")[1];
      const f = Object.fromEntries((await req.formData()).entries());
      const val = f.val === "andra" ? "andra" : f.val === "godkann" ? "godkann" : "";
      const omgang = parseInt(f.omgang, 10);
      if (!val || !omgang) return new Response("bad request", { status: 400 });
      const jobb = await julkulaJobb(env, ordernummer);
      if (!jobb || !jobb.proof || jobb.proof.omgang !== omgang)
        return julkulaProofPage(m[1], jobb, "This preview round is no longer active - please check your latest email for a current link.");
      await julkulaSpara(env, ordernummer, { proof_svar: { val, omgang, feedback: val === "andra" ? String(f.feedback || "").slice(0, 500) : undefined } });
      return page("Thank you", `<h1>Thank you</h1><p>${val === "godkann" ? "Your ornament has been approved - we will send it to production." : "We received your requested change and will send you a new preview soon."}</p>`);
    }

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
        await env.JOBS.put(`orderjob:${v.order}:${id}`, "1", { expirationTtl: TTL }); // index för tryckkedjan (fysiska annonser)
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
      await env.JOBS.put(`orderjob:${v.order}:${id}`, "1", { expirationTtl: TTL }); // index för tryckkedjan (fysiska annonser)
      return Response.redirect(`${url.origin}/s/${id}`, 303);
    }

    if (req.method === "GET" && (m = p.match(/^\/s\/([0-9a-f]{24})$/))) {
      const job = JSON.parse((await env.JOBS.get(`job:${m[1]}`)) || "null");
      if (!job) return page("Not found", `<h1>Link not found</h1><p>This link has expired or does not exist.</p><a class="btn" href="/">Create a star map</a>`);
      if (job.product === "foreningskalender") {
        const namn = (job.club && job.club.name) || "";
        if (job.status === "ready")
          return page("Din klubbkalender är klar", `<div class="card"><h1>Din klubbkalender är klar</h1><p>${esc(namn)}</p><a class="btn" href="/f/${job.id}">Ladda ner pdf</a><p><small>13 sidor (omslag + jan–dec), ${esc(job.format)} liggande. Länken fungerar i 90 dagar.</small></p></div>`, undefined, "sv");
        if (job.status === "failed") return foreningskalenderForm(job, KAL_REASONS[job.reason] || REASONS[job.reason] || KAL_REASONS.internal);
        return page("Din klubbkalender skapas", `<div class="card"><h1>Din klubbkalender skapas</h1><p>Vi lägger in svenska helgdagar, namnsdagar, månfaser och ${esc(namn)}s egna datum. Klart inom en timme.</p><p>Spara den här sidan – den uppdateras automatiskt.</p></div>`, 60, "sv");
      }
      if (PRODUCTS[job.product]) {
        const P = PRODUCTS[job.product];
        if (job.status === "ready")
          return page(P.ready, `<div class="card"><h1>${esc(P.ready)}</h1><p>${job.text ? esc(job.text) + " · " : ""}${esc(job.city)}</p><a class="btn" href="/f/${job.id}">Download PDF</a><p><small>${esc(P.readyNote)}</small></p></div>${afterDownload(job, env)}`);
        if (job.status === "failed") return productForm(job.product, job, REASONS[job.reason] || REASONS.internal);
        return page(P.creating, `<div class="card"><h1>${esc(P.creating)}</h1><p>${esc(P.creatingText(job.city))} This is usually done within an hour.</p><p>Bookmark this page – it updates by itself.</p></div>`, 60);
      }
      if (job.status === "ready")
        return page("Your star map is ready", `<div class="card"><h1>Your star map is ready</h1><p>${esc(job.text)} · ${esc(job.city)} · ${esc(job.date)}</p><a class="btn" href="/f/${job.id}">Download PDF</a><p><small>Prints sharp at A4, A3 and A2. The link works for 90 days.</small></p></div>${afterDownload(job, env)}`);
      if (job.status === "failed")
        return form(job, REASONS[job.reason] || REASONS.internal);
      return page("Creating your star map", `<div class="card"><h1>Your star map is being created</h1><p>We are calculating the sky over ${esc(job.city)} on ${esc(job.date)}. This is usually done within an hour.</p><p>Bookmark this page – it updates by itself.</p></div>`, 60);
    }

    if (req.method === "GET" && (m = p.match(/^\/f\/([0-9a-f]{24})$/))) {
      const pdf = await env.JOBS.get(`pdf:${m[1]}`, "arrayBuffer");
      if (!pdf) return new Response("Not found", { status: 404 });
      const fj = JSON.parse((await env.JOBS.get(`job:${m[1]}`)) || "null");
      const fname = fj && fj.product === "foreningskalender" ? "klubbkalender-2027.pdf" : fj && PRODUCTS[fj.product] ? PRODUCTS[fj.product].file : "star-map.pdf";
      return new Response(pdf, { headers: { "content-type": "application/pdf", "content-disposition": `attachment; filename="${fname}"` } });
    }

    // Tryckkedjans osynliga fil-URL (Gelato hämtar filen härifrån) - oförutsägbar token, ingen autentisering
    // (Gelato skickar ingen Bearer-token), men noindex och ingen lista över tokens finns.
    if (req.method === "GET" && (m = p.match(/^\/t\/([0-9a-f]{32})\.pdf$/))) {
      const pdf = await env.JOBS.get(`tryck:${m[1]}`, "arrayBuffer");
      if (!pdf) return new Response("Not found", { status: 404 });
      return new Response(pdf, { headers: { "content-type": "application/pdf", "cache-control": "private, no-store", "x-robots-tag": "noindex" } });
    }

    // --- API för tillverkaren (GitHub Actions) ---
    if (p.startsWith("/api/")) {
      if (!(await authed(req, env))) return new Response("unauthorized", { status: 401 });
      // Tryckkedjan (fysiska annonser, tryck/kallor.py): jobb kopplade till ett Etsy-ordernummer.
      if (req.method === "GET" && (m = p.match(/^\/api\/order\/(\d{6,14})$/))) {
        const list = await env.JOBS.list({ prefix: `orderjob:${m[1]}:`, limit: 20 });
        const jobs = [];
        for (const k of list.keys) {
          const j = await env.JOBS.get(`job:${k.name.split(":")[2]}`);
          if (j) jobs.push(JSON.parse(j));
        }
        jobs.sort((a, b) => (a.created < b.created ? -1 : 1));
        return Response.json({ jobs });
      }
      // Tryckkedjan: lagrar den godkända tryckfilen och ger tillbaka en osynlig länk (/t/<token>.pdf) som Gelato hämtar.
      if (req.method === "PUT" && (m = p.match(/^\/api\/tryckfil\/(etsy-\d+-\d+)$/))) {
        const body = await req.arrayBuffer();
        if (body.byteLength > 24 * 1024 * 1024) return new Response("too large", { status: 413 }); // KV-gräns 25 MiB
        const head = new TextDecoder().decode(body.slice(0, 5));
        if (head !== "%PDF-") return new Response("not a pdf", { status: 400 });
        const token = [...crypto.getRandomValues(new Uint8Array(16))].map((b) => b.toString(16).padStart(2, "0")).join("");
        await env.JOBS.put(`tryck:${token}`, body, { expirationTtl: 60 * 60 * 24 * 60, metadata: { ref: m[1] } }); // 60 dagar
        return Response.json({ url: `${url.origin}/t/${token}.pdf` });
      }
      // Julkulan: agent-sessionen laddar upp de två genererade proof-bilderna (tryck/kallor.py:Portal.ladda_upp_proof,
      // anropad av julkula_kedja.py:generera_och_skicka_proof EFTER tvåstegsmetoden STILTEST.md §14 - se den
      // filens docstring för varför det steget inte körs obevakat).
      if (req.method === "POST" && (m = p.match(/^\/api\/proof\/(\d{6,14})$/))) {
        const ordernummer = m[1];
        let body;
        try { body = await req.json(); } catch (e) { return new Response("bad json", { status: 400 }); }
        const omgang = parseInt(body.omgang, 10);
        if (!(omgang >= 1 && omgang <= 3)) return new Response("bad omgang (1-3)", { status: 400 });
        const urls = {};
        for (const typ of ["framsida", "baksida"]) {
          const b64 = body[`${typ}_base64`];
          if (!b64) return new Response(`${typ}_base64 saknas`, { status: 400 });
          const ct = JULKULA_BILDTYPER[body[`${typ}_content_type`]] ? body[`${typ}_content_type`] : "image/png";
          let bytes;
          try { bytes = julkulaB64ToBytes(b64); } catch (e) { return new Response(`ogiltig ${typ}_base64`, { status: 400 }); }
          if (!bytes.byteLength) return new Response(`tom ${typ}`, { status: 400 });
          if (bytes.byteLength > JULKULA_MAX_BYTES) return new Response("för stor fil (max 10 MB)", { status: 413 });
          const token = await julkulaSparaBild(env, bytes, ct, { ordernummer, typ: `proof_${typ}` });
          urls[typ] = `${url.origin}/julkula/f/${token}`;
        }
        await julkulaSpara(env, ordernummer, { proof: { omgang, framsida_url: urls.framsida, baksida_url: urls.baksida } });
        return Response.json({ ok: true, framsida_url: urls.framsida, baksida_url: urls.baksida });
      }
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
          // golfbanan: namnen på banorna köparen kan välja bland (bara text, högst 8, visas escapade i formuläret)
          if (b.detail && Array.isArray(b.detail.options)) job.options = b.detail.options.slice(0, 8).map((x) => String(x).slice(0, 80));
          else delete job.options;
        }
        await env.JOBS.put(`job:${id}`, JSON.stringify(job), { expirationTtl: TTL });
        await env.JOBS.delete(`pending:${id}`);
        return Response.json({ ok: true });
      }
    }
    return new Response("Not found", { status: 404 });
  },
};
