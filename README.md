# Leveransportal – personliga stjärnkartor (Moodly Sverige)
- worker/: Cloudflare Worker (portal + kö i KV). Deploy: `cd worker && npx wrangler deploy`
- fulfil/: tillverkare + kvalitetsgrind. Körs var 10:e minut av GitHub Actions (.github/workflows/fulfil.yml)

Attribution: Yale Bright Star Catalogue (Hoffleit & Warren 1991) · JPL DE421 · d3-celestial © Olaf Frohn (BSD-3) ·
GeoNames (CC BY 4.0, geonames.org) · Noto fonts (SIL OFL 1.1).

## Produkter (fältet "product" i jobbet, se fulfil/fulfil.py PRODUCTS)
| product | Portal-länk | Generator | Grind |
|---|---|---|---|
| (saknas) / stjarnkarta | `/` (+ ?style=…) | stjarnkarta.py | kvalitetsgrind.py |
| formorkelse | `/?p=formorkelse` | formorkelse.py | grind_formorkelse.py |
| himmelskalender | `/?p=himmelskalender` | himmelskalender.py | grind_himmelskalender.py |
| historisk | `/?p=historisk` | historisk.py (+ lmkartor.py, passning.py) | grind_historisk.py |
| stadskarta | `/?p=stadskarta` | stadskarta.py (+ osmdata.py) | grind_stadskarta.py |
| karlekskarta | `/?p=karlekskarta` | karlekskarta.py | grind_karlekskarta.py |

Kartprodukternas data: Lantmäteriets öppna FTP (Häradsekonomiska, Generalstabs- och Ekonomiska kartan, CC0; bladindex i
fulfil/data/lm/kartblad_index.json.gz, bladen cachas i fulfil/data/lm/cache/ – ej i git, 70–160 MB/blad) · OpenStreetMap ur
Geofabrik-extrakt bearbetade lokalt med pyosmium (fulfil/osmextract.py; ingen extern OSM-API i drift; Overpass bara vid
utveckling med OSM_SOURCE=overpass), adresser ur samma extrakt, "© OpenStreetMap contributors" + ODbL tryckt ·
Natural Earth 1:50m hela världen (fulfil/data/ne_50m_lander_varld.json.gz). Historisk och stadskarta körs i
.github/workflows/fulfil_kartor.yml (45 min, cache); fulfil.py har tidsbudget (FULFIL_BUDGET_S) och lämnar kvar jobb.
Test: `python fulfil/verktyg/produktionstest_kartor.py` (5 exempelordrar/produkt + felinjektion).

Oberoende facit för grindarna: fulfil/oberoende.py (Meeus, NOAA, JPL Standish, Besselska element) och
fulfil/data/nasa/ (hämtat 2026-09-27 från eclipse.gsfc.nasa.gov: Besselska element, NASA:s bana, månförmörkelser,
referensorter körda med NASA:s JSEX via fulfil/verktyg/nasa_jsex_referens.js – "Eclipse Predictions by Fred Espenak, NASA's GSFC").
Karta: Natural Earth 1:50m (public domain), utdrag i fulfil/data/ne_50m_lander_utdrag.json.gz.
Test: `python fulfil/verktyg/produktionstest_nya.py` (NASA-jämförelse, 5+5 exempelordrar, felinjektion).
