"""Lokal testserver för klubbkalenderns portalformulär (portal_foreningskalender.html).

Detta är INTE leveransportalen (den riktiga är en Cloudflare Worker, leverans/worker/) och ska ALDRIG
deployas. Den finns bara för att kunna testa hela kedjan formulär -> order -> generator -> grind -> pdf
på egen dator, enligt byggsteget "Portalformulär, testat lokalt. Deploya inte portalen."

Körning:  python portal_lokal_server.py [port]   (standard 8765)
Öppna sedan portal_foreningskalender.html i en webbläsare (den pratar med http://localhost:<port>).

Flöde vid POST /api/foreningskalender:
  1. fulfil.make_foreningskalender(job) – samma validering som skulle användas i skarp drift.
  2. foreningskalender.generate(order) – bygger pdf:en.
  3. grind_foreningskalender.run(meta) – kvalitetsgrind. Bara godkänd pdf länkas tillbaka till formuläret.
Allt skrivs till leverans/fulfil/ut/ (samma katalog som alla andra produkters testkörningar).
"""
import json
import sys
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
import fulfil  # noqa: E402
import foreningskalender as FK  # noqa: E402
import grind_foreningskalender as GK  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        # serverar bara pdf:er ur ut/ (för förhandsvisningslänken formuläret får tillbaka)
        if self.path.startswith("/ut/") and self.path.endswith(".pdf"):
            p = ROOT / self.path.lstrip("/")
            if p.resolve().parent == (ROOT / "ut").resolve() and p.exists():
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self._cors()
                self.end_headers()
                self.wfile.write(p.read_bytes())
                return
        self.send_response(404)
        self._cors()
        self.end_headers()

    def do_POST(self):
        if self.path != "/api/foreningskalender":
            self.send_response(404); self._cors(); self.end_headers(); return
        length = int(self.headers.get("Content-Length", 0))
        try:
            job = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._svara(400, {"ok": False, "fel": "ogiltig json"})
        job["id"] = job.get("id") or f"kk-portal-{uuid.uuid4().hex[:10]}"
        job["product"] = "foreningskalender"
        try:
            order, fel = fulfil.make_foreningskalender(job)
            if fel:
                return self._svara(400, {"ok": False, "fel": fel})
            order_path = ROOT / "ut" / f"{order['id']}_order.json"
            (ROOT / "ut").mkdir(exist_ok=True)
            order_path.write_text(json.dumps(order, ensure_ascii=False), encoding="utf-8")
            pdf_path = FK.generate(str(order_path))
            meta_path = pdf_path.with_name(f"{order['id']}_meta.json")
            qc = GK.run(str(meta_path))
            if not qc["godkand"]:
                return self._svara(422, {"ok": False, "fel": "kvalitetsgrinden underkände kalendern",
                                          "detaljer": qc["fel"]})
            return self._svara(200, {"ok": True, "id": order["id"], "pdf_url": f"/ut/{order['id']}.pdf",
                                      "sidor": 13, "stil": order["style"], "format": order["format"]})
        except Exception as e:  # en trasig testkörning ska aldrig krascha servern
            traceback.print_exc()
            return self._svara(500, {"ok": False, "fel": f"internt fel: {e}"})

    def _svara(self, kod, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(kod)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print("[portal-lokal]", fmt % args)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"Lokal testserver (ENDAST localhost, aldrig deployad) på http://localhost:{port}")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
