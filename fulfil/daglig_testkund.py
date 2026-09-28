"""DAGLIG TESTKUND – syntetisk E2E-vakt för leveransportalen (https://stjarnkarta-leverans.moodly-sverige.workers.dev).

Körs en gång om dagen (.github/workflows/daglig_testkund.yml) och gör precis det en riktig köpare gör, fast
med ett tydligt taggat testordernummer och ingen riktig betalning:

  1. Instruktions-PDF-länkar: öppnar den riktiga instruktions-PDF:en (samma fil köparen får på Etsy) för ett
     representativt urval publicerade annonser och kontrollerar att portallänken SOM STÅR I PDF:EN faktiskt
     fungerar (200 + rätt formulär på sidan) – inte en återskapad länk, utan den länk en riktig köpare skulle klicka på.
  2. Hela beställningskedjan: POST:ar en syntetisk order till portalens EGNA formulär (samma /order-endpoint som
     köparens webbläsare postar till – portalen kräver inget betalningsbevis, den litter bara efter ett siffror-
     ordernummer, se leverans/worker/src/index.js:POST /order), väntar in att leveranskedjan (repot
     guslinmarcus/stjarnkarta-leverans, cron var 10:e minut, se leverans/.github/workflows/fulfil.yml där det
     repot ligger lokalt speglat under agentbutik/leverans) plockar upp jobbet, kör generatorn + kvalitetsgrinden
     och laddar upp PDF:en, och kontrollerar sedan att nedladdningslänken (/f/<id>) svarar 200, content-type
     application/pdf och att filen faktiskt börjar med PDF-magibytena %PDF- och har en rimlig storlek.

Städning: portalen har ingen radera-endpoint (bara /api/queue, /api/done, /api/fail, /api/order, /api/tryckfil,
/api/proof – se index.js). Varje jobb i KV har redan expirationTtl = 90 dagar inbyggt ("allt raderas efter 90
dagar", index.js rad 5) – det är portalens EGEN mekanism för att aldrig spara data för evigt, och gäller lika för
riktiga och syntetiska ordrar. Testordernumret är därför bara tydligt TAGGAT (prefix "999" + dagens datum, texten
"E2E testkund") så att det går att känna igen i loggar/KV om någon tittar, men lämnas kvar och självdör inom 90
dagar precis som allt annat i portalen. Skriptet självt skapar INGA lokala filer i repot (bara HTTP-anrop), så
GitHub Actions-körningen lämnar inget skräp i vare sig agentbutik-fabrik eller portalen utöver detta.

Inga riktiga mejl skickas (ingen del av flödet skickar mejl – leveransen sker via nedladdningslänken på /s/<id>).
Inga Etsy-, Anthropic- eller betalanrop görs – bara vanliga GET/POST mot den publika portalen.

Körning:
    python daglig_testkund.py [--torr]   (--torr: skriv resultat men rör inga GitHub-issues)
Miljövariabler (valfria): GITHUB_TOKEN, GITHUB_REPOSITORY – för issue-hanteringen (etikett "puls", samma
mönster som fabrik/resursvakt.py:issues() / fabrik/fabrikspuls.py – en öppen issue per problem, stängs
automatiskt när dagens körning blir grön igen).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent          # leverans/fulfil
ROOT = HERE.parent.parent                        # repo-rot
ETSY = ROOT / "etsy"
KO_PATH = ETSY / "PUBLICERINGSKO.json"
PORTAL = os.environ.get("PORTAL_URL_TEST", "https://stjarnkarta-leverans.moodly-sverige.workers.dev")

PRIORITET = ["stjarnkarta", "formorkelse", "himmelskalender", "manfas", "karlekskarta",
             "historisk", "stadskarta", "golfbana", "brollopskarta", "fodelsetavla"]
POLL_INTERVAL_S = 30
POLL_BUDGET_S = 15 * 60   # produktionens leveranskedja (stjarnkarta-leverans) kör var 10:e minut – 15 min ger marginal
MIN_PDF_BYTES = 20_000    # riktiga stjärnkarts-PDF:er ligger på ~150-350 kB, instruktions-PDF:er ~30 kB


# ------------------------------------------------------------------ HTTP
def http(method, path, data=None, timeout=30):
    try:
        import truststore; truststore.inject_into_ssl()  # ofarligt om det saknas (bara Marcus dator behöver det)
    except Exception:
        pass
    url = path if path.startswith("http") else PORTAL + path
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers={"User-Agent": "moodly-daglig-testkund/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.geturl(), r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.geturl(), e.read(), e.headers
    except Exception as e:
        return 0, url, repr(e).encode(), {}


# ------------------------------------------------------------------ 1. instruktions-PDF-länkar (representativt urval)
def valda_annonser(n=4):
    """Ett urval publicerade annonser av olika produkttyp (fristående flaggskepp) + en variant för stilspridning.
    Filpaket (pusselbuntar) och fysiska annonser har ingen portallänk alls (se publiceringsgrind.annons()) och
    utesluts därför här – de har ingen instruktions-PDF-portallänk att kontrollera."""
    ko = json.loads(KO_PATH.read_text(encoding="utf-8"))["ko"]
    kandidater = {}
    for x in ko:
        if x["typ"] not in ("variant", "fristaende"):
            continue
        if x.get("status") != "klar" or not x.get("publicerad"):
            continue
        prod = x["produkt"]
        if prod not in kandidater or (x["typ"] == "fristaende" and kandidater[prod]["typ"] == "variant"):
            kandidater[prod] = x
    ordnade = sorted(kandidater.values(), key=lambda x: (PRIORITET.index(x["produkt"]) if x["produkt"] in PRIORITET else 99, x["id"]))
    valda = ordnade[:n]
    if not any(x["typ"] == "variant" for x in valda):
        for x in ko:
            if x["typ"] == "variant" and x.get("status") == "klar" and x.get("publicerad"):
                valda.append(x)
                break
    return valda


def kontrollera_instruktion(x):
    """Öppnar annonsens RIKTIGA instruktions-PDF, hämtar portallänken som faktiskt står i den (samma som en
    köpare skulle klicka på) och kontrollerar att den svarar 200 med rätt formulär (samma markörer som
    publiceringsgrind.py:s teknikgrind kräver, se den filens portal_get/portal_marker)."""
    if x["typ"] == "variant":
        d = ETSY / x["mapp"]
        a = json.loads((d / "annons.json").read_text(encoding="utf-8"))
        digital_fil = d / a["digital_fil"]
        forvantad = a.get("portal_url", "").split(PORTAL, 1)[-1].lstrip("/") or f"?style={a['stil']}"
        marker = ['name="style"', f'value="{a["stil"]}"'] + ([f'value="{a["produkt"]}"'] if a.get("produkt") not in (None, "stjarnkarta") else [])
    else:
        prod = x["produkt"]
        digital_fil = ETSY / x["digital_fil"]
        forvantad = "" if prod == "stjarnkarta" else f"?p={prod}"
        marker = [k.split(":", 1)[1] for k in x.get("krav", []) if k.startswith("portal:")]
    if not digital_fil.exists():
        return {"id": x["id"], "ok": False, "detalj": f"instruktions-pdf saknas: {digital_fil}"}
    import fitz
    doc = fitz.open(digital_fil)
    lankar = [l.get("uri", "") for pg in doc for l in pg.get_links()]
    portal_lankar = [u for u in lankar if PORTAL in u]
    if not portal_lankar:
        return {"id": x["id"], "ok": False, "detalj": f"ingen portallänk i {digital_fil.name} (länkar hittade: {lankar[:3]})"}
    lank = portal_lankar[0]
    if forvantad and forvantad.lstrip("?") not in lank:
        return {"id": x["id"], "ok": False, "detalj": f"länken {lank!r} matchar inte förväntad query {forvantad!r}"}
    stig = lank.split(PORTAL, 1)[-1]
    status, _, body, _ = http("GET", stig)
    html = body.decode("utf-8", "replace")
    saknas = [m for m in marker if m not in html]
    ok = status == 200 and not saknas
    detalj = f"GET {stig[:70]} -> {status}" + (f"; saknas i sidan: {saknas}" if saknas else "; formuläret finns")
    return {"id": x["id"], "produkt": x["produkt"], "ok": ok, "lank": lank, "detalj": detalj}


# ------------------------------------------------------------------ 2. hela beställningskedjan (syntetisk order)
def kor_testorder():
    idag = date.today()
    order_nr = f"999{idag:%Y%m%d}"   # tydligt taggat (prefix 999) + unikt per dag, krockar aldrig med ett riktigt Etsy-ordernummer
    falt = {
        "order": order_nr, "text": "E2E testkund - raderas automatiskt",
        "city": "Stockholm", "country": "Sweden", "date": "2020-06-21", "time": "21:00",
        "lang": "en", "style": "minimal",
    }
    status, slut_url, _, _ = http("POST", "/order", falt)
    if status != 200 or "/s/" not in slut_url:
        return {"ok": False, "fel": f"POST /order gav {status} ({slut_url})", "logg": f"beställning {order_nr}: FEL vid POST /order ({status})"}
    job_id = slut_url.rstrip("/").rsplit("/s/", 1)[-1]

    t0 = time.time()
    lage = None
    while time.time() - t0 < POLL_BUDGET_S:
        st, _, body, _ = http("GET", f"/s/{job_id}")
        html = body.decode("utf-8", "replace")
        if st == 200 and 'href="/f/' in html:
            lage = "klar"; break
        if st == 200 and "<form" in html:
            lage = "misslyckad"; break
        time.sleep(POLL_INTERVAL_S)
    vantetid = time.time() - t0

    if lage is None:
        return {"ok": False, "fel": f"order {job_id} (produkt stjärnkarta) blev aldrig klar inom {POLL_BUDGET_S // 60} min "
                                     f"- leveranskedjan (repot stjarnkarta-leverans, workflow 'leverera', cron */10) har inte "
                                     f"plockat upp jobbet. Kolla `gh run list -R guslinmarcus/stjarnkarta-leverans`: GitHub "
                                     f"stryper ofta */10-scheman (2026-09-28: 6 h mellan schemalagda körningar) - då väntar "
                                     f"även riktiga köpare i timmar",
                "logg": f"beställning {job_id}: TIMEOUT efter {vantetid:.0f}s"}
    if lage == "misslyckad":
        return {"ok": False, "fel": f"order {job_id} misslyckades i generatorn/kvalitetsgrinden (sidan visade felformuläret igen)",
                "logg": f"beställning {job_id}: MISSLYCKAD i generatorn/grinden efter {vantetid:.0f}s"}

    status, _, pdf, headers = http("GET", f"/f/{job_id}")
    ct = (headers.get("Content-Type") or "") if headers else ""
    if status != 200:
        return {"ok": False, "fel": f"GET /f/{job_id} gav {status} (förväntade 200)", "logg": f"nedladdning {job_id}: fel status {status}"}
    if "application/pdf" not in ct:
        return {"ok": False, "fel": f"GET /f/{job_id} content-type {ct!r} (förväntade application/pdf)",
                "logg": f"nedladdning {job_id}: fel content-type {ct!r}"}
    if pdf[:5] != b"%PDF-":
        return {"ok": False, "fel": f"nedladdad fil saknar PDF-magibytena %PDF- (fick {pdf[:5]!r})",
                "logg": f"nedladdning {job_id}: inte en giltig PDF"}
    if len(pdf) < MIN_PDF_BYTES:
        return {"ok": False, "fel": f"nedladdad pdf orimligt liten: {len(pdf)} byte (golv {MIN_PDF_BYTES})",
                "logg": f"nedladdning {job_id}: bara {len(pdf)} byte"}
    return {"ok": True, "fel": None,
            "logg": f"beställning {job_id} (order {order_nr}): klar efter {vantetid:.0f}s, pdf {len(pdf):,} byte, content-type {ct} - OK".replace(",", " ")}


# ------------------------------------------------------------------ GitHub-issue (etikett "puls", samma mönster som fabrik/resursvakt.py:issues())
def gh(method, path, **kw):
    import requests
    token = os.environ.get("GITHUB_TOKEN", "")
    r = requests.request(method, f"https://api.github.com{path}", timeout=30, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "moodly-daglig-testkund/1.0"}, **kw)
    r.raise_for_status()
    return r.json() if r.content else {}


def hantera_issue(ok, problem, detaljer):
    repo, token = os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_TOKEN", "")
    if not repo or not token:
        print("(ingen GITHUB_REPOSITORY/GITHUB_TOKEN - hoppar över issue-hantering, t.ex. lokal körning)")
        return
    oppen, sida = None, 1
    while True:
        lst = gh("GET", f"/repos/{repo}/issues", params={"labels": "puls", "state": "open", "per_page": 100, "page": sida})
        for i in lst:
            if "<!-- puls:testkund -->" in (i.get("body") or ""):
                oppen = i
        if len(lst) < 100:
            break
        sida += 1
    nu = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    if ok:
        if oppen:
            gh("POST", f"/repos/{repo}/issues/{oppen['number']}/comments", json={"body": f"Grön igen {nu} UTC. Stänger."})
            gh("PATCH", f"/repos/{repo}/issues/{oppen['number']}", json={"state": "closed", "state_reason": "completed"})
            print(f"stänger issue #{oppen['number']} (testkund grön igen)")
        return
    titel = "Daglig testkund RÖD: leveransportalen"
    text = "\n".join([
        "<!-- puls:testkund -->",
        "@guslinmarcus – den dagliga syntetiska testkunden hittade ett fel i leveranskedjan "
        "(https://stjarnkarta-leverans.moodly-sverige.workers.dev).", "",
        *[f"- {p}" for p in problem], "",
        "Detaljer:", *[f"- {d}" for d in detaljer], "",
        f"Senast körd {nu} UTC. leverans/fulfil/daglig_testkund.py uppdaterar det här ärendet dagligen och "
        "stänger det när kedjan blir grön igen."])
    if oppen:
        gh("PATCH", f"/repos/{repo}/issues/{oppen['number']}", json={"title": titel, "body": text})
        gh("POST", f"/repos/{repo}/issues/{oppen['number']}/comments", json={"body": f"Fortfarande rött {nu} UTC: " + "; ".join(problem)})
        print(f"uppdaterar issue #{oppen['number']}")
    else:
        n = gh("POST", f"/repos/{repo}/issues", json={"title": titel, "body": text, "labels": ["puls"]})
        print(f"skapar issue #{n['number']}")


# ------------------------------------------------------------------
def main():
    torr = "--torr" in sys.argv
    problem, detaljer = [], []

    for x in valda_annonser():
        r = kontrollera_instruktion(x)
        detaljer.append(f"instruktion {x['id']} ({x['produkt']}): {'OK' if r['ok'] else 'FEL'} - {r['detalj']}")
        if not r["ok"]:
            problem.append(f"instruktions-PDF-länk trasig för {x['id']}: {r['detalj']}")

    resultat = kor_testorder()
    detaljer.append(resultat["logg"])
    if not resultat["ok"]:
        problem.append(resultat["fel"])

    ok = not problem
    print("\n".join(detaljer))
    print("\nDAGLIG TESTKUND: " + ("OK - hela kedjan (instruktions-länkar + beställning -> generering -> nedladdning) fungerar." if ok
                                    else "FEL\n" + "\n".join(f"  - {p}" for p in problem)))
    if not torr:
        hantera_issue(ok, problem, detaljer)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
