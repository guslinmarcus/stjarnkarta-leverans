# Leveransportal – personliga stjärnkartor (Moodly Sverige)
- worker/: Cloudflare Worker (portal + kö i KV). Deploy: `cd worker && npx wrangler deploy`
- fulfil/: tillverkare + kvalitetsgrind. Körs var 10:e minut av GitHub Actions (.github/workflows/fulfil.yml)

Attribution: Yale Bright Star Catalogue (Hoffleit & Warren 1991) · JPL DE421 · d3-celestial © Olaf Frohn (BSD-3) ·
GeoNames (CC BY 4.0, geonames.org) · Noto fonts (SIL OFL 1.1).
