// Förhandskollen för golfbanekartan (/golf-kolla): kan banan ritas? Svarar ur golfindex.json (fulfil/verktyg/golfindex.py),
// som byggs ur samma register och samma grind som ordern. Matchningen är en JS-kopia av fulfil/golfdata.py resolve()
// och fulfil.geocode(); fulfil/verktyg/test_golfkolla.py jämför de två för varje bana i indexet.

// fulfil.norm (orter, länder)
export function normPlace(s) {
  return String(s || "").toLowerCase().normalize("NFKD").replace(/\p{Mn}/gu, "").trim();
}
// golfdata.norm (bannamn)
function normName(s) {
  return String(s || "").normalize("NFKD").toLowerCase().replace(/\p{Mn}/gu, "")
    .replace(/&/g, " and ").replace(/ß/g, "ss").replace(/ø/g, "o").replace(/æ/g, "ae").replace(/ł/g, "l");
}
function tokens(s, stop) {
  return (normName(s).match(/[a-z0-9]+/g) || []).filter((t) => !stop.has(t));
}
// rapidfuzz fuzz.ratio = 100 · 2·LCS / (|a| + |b|)
function ratio(a, b) {
  const m = a.length, n = b.length;
  if (!m && !n) return 100;
  let prev = new Array(n + 1).fill(0);
  for (let i = 1; i <= m; i++) {
    const cur = new Array(n + 1).fill(0);
    for (let j = 1; j <= n; j++) cur[j] = a[i - 1] === b[j - 1] ? prev[j - 1] + 1 : Math.max(prev[j], cur[j - 1]);
    prev = cur;
  }
  return (200 * prev[n]) / (m + n);
}
function tokMatch(q, cand) {
  for (const c of cand) if (q === c || (q.length >= 5 && c.length >= 5 && ratio(q, c) >= 88)) return true;
  return false;
}
function distKm(la1, lo1, la2, lo2) {
  const p = Math.PI / 180;
  const a = 0.5 - Math.cos((la2 - la1) * p) / 2 + Math.cos(la1 * p) * Math.cos(la2 * p) * (1 - Math.cos((lo2 - lo1) * p)) / 2;
  return 12742 * Math.asin(Math.sqrt(a));
}
function ringContains(ring, x, y) {
  let ins = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi || 1e-300) + xi) ins = !ins;
  }
  return ins;
}
// osmextract.region_for_bbox: minsta Geofabrik-region (bland de byggda) som täcker rutan ±0,005° kring orten.
// Ordern använder den regionens register – därför matchar kollen bara banor ur samma register.
function regionFor(idx, lat, lon) {
  const corners = [[lon - 0.005, lat - 0.005], [lon + 0.005, lat - 0.005], [lon + 0.005, lat + 0.005], [lon - 0.005, lat + 0.005]];
  let best = null;
  for (const [rid, r] of Object.entries(idx.regioner)) {
    if (corners.every(([x, y]) => r.ringar.some((ring) => ringContains(ring, x, y))) && (!best || r.yta < best[1])) best = [rid, r.yta];
  }
  return best ? best[0] : "";
}

function prep(idx) {
  if (idx._prep) return idx._prep;
  const stop = new Set(idx.stop);
  const byId = new Map(idx.banor.map((c) => [c.id, c]));
  for (const c of idx.banor) c._own = new Set(c.t);  // förberäknade i golfindex.py med golfdata.tokens
  for (const c of idx.banor) {
    const par = byId.get(c.pid);
    const pt = par ? par._own : new Set(tokens(c.p, stop));
    c._ctx = new Set([...c._own, ...pt]);
  }
  idx._prep = { stop, byId };
  return idx._prep;
}

function kidsOf(idx, c) {
  return idx.banor.filter((o) => o.pid === c.id && o.h >= idx.min_hal);
}

// golfdata.resolve: -> {c} | {err: "course_not_found", nearby} | {err: "course_ambiguous", options: [{label, c}]}
function resolve(idx, name, lat, lon, region) {
  const { stop } = prep(idx);
  const q = tokens(name, stop);
  const near = idx.banor.filter((c) => c.r === region && distKm(lat, lon, c.lat, c.lon) <= idx.sokradie_km);
  const nearby = () => near.map((c) => [distKm(lat, lon, c.lat, c.lon), c]).sort((a, b) => a[0] - b[0]).slice(0, 8).map((x) => x[1]);
  if (!q.length) return { err: "course_not_found", nearby: nearby() };
  const scored = near.map((c) => {
    const cov = [...c._own].filter((t) => q.includes(t)).length / Math.max(1, c._own.size);
    return [q.filter((t) => tokMatch(t, c._ctx)).length / q.length, q.filter((t) => tokMatch(t, c._own)).length, c, cov];
  });
  if (!scored.length || Math.max(...scored.map((s) => s[0])) < 0.66) return { err: "course_not_found", nearby: nearby() };
  const best = Math.max(...scored.map((s) => s[0]));
  let top = scored.filter((s) => s[0] === best);
  const mo = Math.max(...top.map((s) => s[1]));
  top = top.filter((s) => s[1] === mo);
  const mc = Math.max(...top.map((s) => s[3]));
  top = top.filter((s) => s[3] === mc);
  let uniq = [];
  for (const s of top.sort((a, b) => b[2].h - a[2].h || (a[2].id < b[2].id ? -1 : 1))) {
    const c = s[2];
    if (!uniq.some((u) => u.n === c.n && distKm(c.lat, c.lon, u.lat, u.lon) < 0.3)) uniq.push(c);
  }
  if (uniq.length > 1) {
    const ids = new Set(uniq.map((c) => c.id));
    const kids = uniq.filter((c) => ids.has(c.pid));
    if (kids.length && kids.length === uniq.length - 1) uniq = kids;
  }
  if (uniq.length > 1) {
    const dd = uniq.map((c) => [distKm(lat, lon, c.lat, c.lon), c]).sort((a, b) => a[0] - b[0]);
    if (dd[0][0] <= 10 && dd[0][0] < dd[1][0] / 3) uniq = [dd[0][1]];
  }
  if (uniq.length > 1) {
    return { err: "course_ambiguous", options: uniq.map((c) => ({
      label: uniq.filter((u) => u.n === c.n).length > 1 ? `${c.n} (${Math.round(distKm(lat, lon, c.lat, c.lon))} km)` : c.n, c })) };
  }
  const c = uniq[0];
  const kids = c.fac ? kidsOf(idx, c) : [];
  if (kids.length >= 2) return { err: "course_ambiguous", facility: c, options: kids.map((k) => ({ label: `${c.n} – ${k.n}`, c: k })) };
  return { c };
}

// golfdata.resolve_with_facility
function resolveWithFacility(idx, name, lat, lon, region) {
  const m = String(name).split(/\s[–-]\s/);
  if (m.length >= 2) {
    const first = m[0], rest = String(name).slice(first.length).replace(/^\s[–-]\s/, "");
    const r = resolve(idx, first, lat, lon, region);
    const par = r.err === "course_ambiguous" ? r.facility : r.c;
    if (par) {
      const hit = idx.banor.filter((k) => k.pid === par.id && normName(k.n) === normName(rest));
      if (hit.length === 1) return { c: hit[0] };
    }
  }
  return resolve(idx, name, lat, lon, region);
}

// Hela kollen: land -> ort (samma regel som fulfil.geocode) -> bana -> grindens svar ur indexet.
export function checkCourse(idx, course, city, country) {
  course = String(course || "").trim().slice(0, 80);
  const cc = idx.lander[normPlace(country)];
  if (!cc) return { status: "country_not_found" };
  const town = (idx.orter[cc] || {})[normPlace(city)];
  if (!course) return { status: "course_not_found", nearby: [] };
  if (!town) return { status: "not_covered", cc };
  const [lat, lon] = town;
  const region = regionFor(idx, lat, lon);
  if (!region) return { status: "not_covered", cc };
  const r = resolveWithFacility(idx, course, lat, lon, region);
  if (r.err === "course_not_found") return { status: "course_not_found", nearby: r.nearby, region };
  if (r.err === "course_ambiguous") return { status: "course_ambiguous", options: r.options };
  return { status: r.c.s === "ok" ? "ok" : "course_holes_incomplete", course: r.c };
}

// ---------------------------------------------------------------- sidan
const T = {
  en: {
    title: "Check your golf course", h1: "Will your golf course work?",
    intro: "Before you order a golf course map, check here that your course is mapped well enough in OpenStreetMap. We need every hole numbered, with a green at the end of each hole. Many courses are, some are not yet – this page tells you in a second.",
    course: "Golf course name", city: "Nearest town", country: "Country", button: "Check my course",
    ok: "Your course works ✓", okText: (n, h) => `We found <b>${n}</b> with all ${h} holes numbered and a green at every hole. Here is a preview of the course outline from the map data:`,
    okOrder: "When you order, enter the course and the town exactly like this:", orderBtn: "Go to the order form",
    inc: "Sorry – the map data for this course is not complete. Please do not buy.",
    incText: (n, h, nr) => `We found <b>${n}</b>, but in OpenStreetMap only ${nr} of its ${h} mapped holes have a hole number, or some holes are missing or have no green. We cannot draw a correct map of it yet, so please do not order it.`,
    amb: "Several courses match – which one is yours?", ambText: "Each course is checked separately:",
    works: "works ✓", notWorks: "map data incomplete – do not buy",
    nf: "We could not find that course near the town you entered.",
    nfText: "Check the spelling, or pick your course from the courses we know near that town:", nfNone: "We have no courses on file near that town.",
    nc: "We could not find that town near any course we have checked – please do not buy yet.",
    ncText: (r) => `Check the spelling of the town, or try the nearest larger town. So far we have checked every course in ${r}. If your course is somewhere else, please send us a message on Etsy before ordering and we will check it for you.`,
    cnf: "We do not recognise that country. Please write it in English, for example Sweden, United Kingdom, United States or Spain.",
    credit: "Map data © OpenStreetMap contributors (ODbL). Checked", other: "På svenska",
  },
  sv: {
    title: "Kolla din golfbana", h1: "Fungerar din golfbana?",
    intro: "Innan du beställer en golfbanekarta: kolla här att banan är tillräckligt väl karterad i OpenStreetMap. Varje hål måste ha ett nummer och en green i slutet. Många banor har det, en del inte ännu – svaret kommer direkt.",
    course: "Golfbanans namn", city: "Närmaste ort", country: "Land", button: "Kolla min bana",
    ok: "Din bana fungerar ✓", okText: (n, h) => `Vi hittade <b>${n}</b> med alla ${h} hål numrerade och en green vid varje hål. Så här ser banans kontur ut i kartdatan:`,
    okOrder: "Skriv bana och ort precis så här när du beställer:", orderBtn: "Till beställningen",
    inc: "Tyvärr saknas fullständig kartdata för banan – köp inte.",
    incText: (n, h, nr) => `Vi hittade <b>${n}</b>, men i OpenStreetMap har bara ${nr} av banans ${h} karterade hål ett nummer, eller så saknas hål eller green. Vi kan inte rita en korrekt karta ännu, så beställ inte den här banan.`,
    amb: "Flera banor passar – vilken är din?", ambText: "Varje bana är kollad för sig:",
    works: "fungerar ✓", notWorks: "kartdata saknas – köp inte",
    nf: "Vi hittade ingen bana med det namnet nära orten.",
    nfText: "Kontrollera stavningen, eller välj din bana bland dem vi känner till nära orten:", nfNone: "Vi har inga banor nära den orten.",
    nc: "Vi hittade inte orten nära någon bana vi har kollat – köp inte ännu.",
    ncText: (r) => `Kontrollera stavningen, eller prova närmaste större ort. Hittills har vi kollat alla banor i ${r}. Ligger din bana någon annanstans? Skicka ett meddelande på Etsy innan du beställer så kollar vi åt dig.`,
    cnf: "Vi känner inte igen landet. Skriv det på engelska eller svenska, till exempel Sverige, Sweden, United Kingdom eller Spain.",
    credit: "Kartdata © OpenStreetMap contributors (ODbL). Kollat", other: "In English",
  },
};
const REGION_NAMES = {
  en: { sweden: "Sweden", scotland: "Scotland", england: "England", merseyside: "Merseyside", andalucia: "Andalusia (Spain)", "us/georgia": "Georgia (USA)", norcal: "Northern California (USA)" },
  sv: { sweden: "Sverige", scotland: "Skottland", england: "England", merseyside: "Merseyside", andalucia: "Andalusien (Spanien)", "us/georgia": "Georgia (USA)", norcal: "norra Kalifornien (USA)" },
};

function regionList(idx, lang) {
  const n = [...new Set(Object.entries(idx.regioner).map(([r, x]) => (x.grupp === "england" ? "england" : r)))]
    .map((r) => REGION_NAMES[lang][r] || (idx.regioner[r] || {}).namn || r);
  return n.length > 1 ? `${n.slice(0, -1).join(", ")} ${lang === "sv" ? "och" : "and"} ${n[n.length - 1]}` : n.join("");
}

function svg(g, esc) {
  if (!g) return "";
  const pad = 30, W = Math.max(g.w, 1) + 2 * pad, H = Math.max(g.h, 1) + 2 * pad;
  const d = (pts) => pts.map((p, i) => `${i ? "L" : "M"}${p[0] + pad} ${p[1] + pad}`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc("course outline")}" style="width:100%;max-height:420px;background:#f4f1e6;border-radius:8px;margin-top:10px">` +
    `<path d="${g.o.map((r) => d(r) + "Z").join("")}" fill="#b9d3a0" stroke="#3f6b3a" stroke-width="3" fill-rule="evenodd"/>` +
    `<path d="${g.l.map(d).join("")}" fill="none" stroke="#1d3b26" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>` +
    g.l.map((l) => `<circle cx="${l[l.length - 1][0] + pad}" cy="${l[l.length - 1][1] + pad}" r="7" fill="#1d3b26"/>`).join("") + `</svg>`;
}

export function golfCheckPage(idx, q, esc, page) {
  const lang = q.lang === "sv" ? "sv" : "en";
  const t = T[lang];
  const v = { course: String(q.course || "").slice(0, 80), city: String(q.city || "").slice(0, 60), country: String(q.country || "").slice(0, 60) };
  const other = `/golf-kolla?${new URLSearchParams({ ...v, lang: lang === "sv" ? "en" : "sv" })}`;
  let out = "";
  if (v.course || v.city || v.country) {
    const r = checkCourse(idx, v.course, v.city, v.country);
    const orderUrl = (name) => `/?${new URLSearchParams({ p: "golfbana", course: name, city: v.city, country: v.country })}`;
    if (r.status === "ok") {
      out = `<div class="card"><h2 style="color:#9fdc8f">${t.ok}</h2><p>${t.okText(esc(r.course.n), r.course.h)}</p>${svg(r.course.g, esc)}
<p><small>${t.okOrder}</small><br><b>${esc(r.course.n)}</b> · ${esc(v.city)} · ${esc(v.country)}</p><a class="btn" href="${orderUrl(r.course.n)}">${t.orderBtn}</a></div>`;
    } else if (r.status === "course_holes_incomplete") {
      out = `<div class="card"><h2 style="color:var(--err)">${t.inc}</h2><p>${t.incText(esc(r.course.n), r.course.h, r.course.nr)}</p></div>`;
    } else if (r.status === "course_ambiguous") {
      out = `<div class="card"><h2>${t.amb}</h2><p>${t.ambText}</p><ul>${r.options.map((o) =>
        `<li><b>${esc(o.label)}</b> – ${o.c.s === "ok" ? `<span style="color:#9fdc8f">${t.works}</span>` : `<span class="err">${t.notWorks}</span>`}</li>`).join("")}</ul>
<p><small>${lang === "sv" ? "Kolla ett av namnen igen för förhandsvisningen." : "Check one of the names again to see its preview."}</small></p></div>`;
    } else if (r.status === "course_not_found") {
      const list = (r.nearby || []).map((c) => `<li>${esc(c.n)} – ${c.s === "ok" ? `<span style="color:#9fdc8f">${t.works}</span>` : `<span class="err">${t.notWorks}</span>`}</li>`).join("");
      out = `<div class="card"><h2 style="color:var(--err)">${t.nf}</h2><p>${list ? t.nfText : t.nfNone}</p>${list ? `<ul>${list}</ul>` : ""}</div>`;
    } else if (r.status === "not_covered") {
      out = `<div class="card"><h2 style="color:var(--err)">${t.nc}</h2><p>${t.ncText(esc(regionList(idx, lang)))}</p></div>`;
    } else {
      out = `<div class="card"><p class="err">${t.cnf}</p></div>`;
    }
  }
  const body = `<h1>${t.h1}</h1><p>${t.intro}</p>
<form method="get" action="/golf-kolla"><input type="hidden" name="lang" value="${lang}">
<label for="course">${t.course}</label><input id="course" name="course" maxlength="80" required value="${esc(v.course)}">
<div class="row"><div><label for="city">${t.city}</label><input id="city" name="city" maxlength="60" required value="${esc(v.city)}"></div>
<div><label for="country">${t.country}</label><input id="country" name="country" maxlength="60" required value="${esc(v.country)}"></div></div>
<button type="submit">${t.button}</button></form>${out ? `<div style="margin-top:18px">${out}</div>` : ""}
<p><small>${t.credit} ${esc(Object.values(idx.osm_datum).sort()[0] || "")} · <a style="color:#b9b3a3" href="${esc(other)}">${t.other}</a></small></p>`;
  return page(t.title, body, 0, lang);
}
