"""Tiny local UI for msync:  python -m msync.web  ->  http://127.0.0.1:8765"""
import collections, json, pathlib, subprocess, sys, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs
from .config import DATA, PLAYLISTS, CATALOG, ROOT
from . import adb

LOG = collections.deque(maxlen=4000)
STATE = {"running": None}
PY = sys.executable

def run_job(label, args):
    STATE["running"] = label
    LOG.append(f"$ msync {' '.join(args)}")
    p = subprocess.Popen([PY, "-u", "-m", "msync", *args], cwd=ROOT, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    for line in p.stdout:
        LOG.append(line.rstrip())
    LOG.append(f"— {label} finished (exit {p.wait()})")
    STATE["running"] = None

PAGE = (pathlib.Path(__file__).parent / "ui" / "index.html").read_text(encoding="utf-8")

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, body, ctype="text/html; charset=utf-8"):
        b = body.encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", len(b)); self.end_headers()
        self.wfile.write(b)
    def do_GET(self):
        if self.path == "/state":
            cat = json.loads(CATALOG.read_text()) if CATALOG.exists() else {}
            pls = list(json.loads(PLAYLISTS.read_text())) if PLAYLISTS.exists() else []
            try: phone = adb.device_state()
            except Exception: phone = "adb error"
            return self.send(200, json.dumps({"songs": sum(1 for r in cat.values() if r.get("path")), "playlists": pls,
                                              "phone": phone, "running": STATE["running"], "log": list(LOG)[-600:]}), "application/json")
        self.send(200, PAGE)
    def do_POST(self):
        if STATE["running"]:
            return self.send(409, "busy")
        what = self.path.rsplit("/", 1)[-1]
        n = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
        if what == "sync":
            f = DATA / "last_links.txt"; f.write_text(form.get("links", ""))
            args = ["sync", form.get("name", "Playlist").strip(), str(f)]
        else:
            args = {"backup": ["backup"], "build": ["build"], "push": ["push"], "push-apply": ["push", "--apply"],
                    "cleanup": ["cleanup"]}.get(what)
            if not args:
                return self.send(404, "?")
        threading.Thread(target=run_job, args=(what, args), daemon=True).start()
        self.send(200, "ok")

def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"Music sync UI -> http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    main()
