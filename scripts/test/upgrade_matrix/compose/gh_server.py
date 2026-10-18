"""Fake github.com / api.github.com for the compose e2e (releases only)."""
import http.server, json, os, ssl, sys
ROOT = sys.argv[1]  # ROOT/latest = tag ; ROOT/<tag>/<asset>
P = "/TwinRocket/meshloom/releases/"
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        latest = open(os.path.join(ROOT, "latest")).read().strip()
        if host == "api.github.com" and self.path.startswith("/repos/TwinRocket/meshloom/releases/latest"):
            body = json.dumps({"tag_name": latest, "assets": []}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        if host == "github.com" and self.path == P + "latest":
            loc = open(os.path.join(ROOT, "location")).read().strip() if os.path.exists(os.path.join(ROOT, "location")) else f"https://github.com{P}tag/{latest}"
            self.send_response(302); self.send_header("Location", loc); self.send_header("Content-Length", "0"); self.end_headers(); return
        if host == "github.com" and self.path.startswith(P + "download/"):
            rel = self.path[len(P + "download/"):]
            f = os.path.normpath(os.path.join(ROOT, rel))
            if f.startswith(ROOT) and os.path.isfile(f):
                data = open(f, "rb").read()
                self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
        self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()
srv = http.server.ThreadingHTTPServer(("0.0.0.0", 443), H)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.minimum_version = ssl.TLSVersion.TLSv1_2
ctx.load_cert_chain(sys.argv[2], sys.argv[3])
srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
srv.serve_forever()
