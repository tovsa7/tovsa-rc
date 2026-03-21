#!/usr/bin/env python3
"""
Tovsa RC Agent v1.3
HTTPS на localhost:7071 (нужен для getDisplayMedia)
HTTP  на localhost:7070 (fallback)
Требования: Python 3.7+, python-cryptography (pkg install python-cryptography)
"""

import json
import os
import ssl
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from datetime import datetime, timezone, timedelta

PORT_HTTPS = 7071
PORT_HTTP  = 7070
HOST       = "localhost"
INSTALL_DIR = Path.home() / ".tovsa"
CERT_FILE   = INSTALL_DIR / "cert.pem"
KEY_FILE    = INSTALL_DIR / "key.pem"


# ── Cert generation via cryptography ──────────────────
def _gen_cert() -> bool:
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        import ipaddress

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
        ])
        now = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            ]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256())
        )
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        KEY_FILE.write_bytes(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
        CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        return True
    except Exception as e:
        print(f"  ✗  Ошибка генерации сертификата: {e}")
        return False


def _ensure_cert() -> bool:
    if CERT_FILE.exists() and KEY_FILE.exists():
        return True
    print("  ⚙  Генерирую TLS сертификат...")
    ok = _gen_cert()
    if ok:
        print(f"  ✓  {CERT_FILE}")
        print(f"  ℹ  Открой https://localhost:{PORT_HTTPS} в браузере и прими сертификат (один раз)")
    return ok


def _make_ssl_ctx():
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(CERT_FILE), str(KEY_FILE))
        return ctx
    except Exception as e:
        print(f"  ✗  SSL: {e}")
        return None


# ── Screen ─────────────────────────────────────────────
_screen_size = None

def _get_screen_size():
    global _screen_size
    if _screen_size:
        return _screen_size
    try:
        r = subprocess.run(["wm", "size"], capture_output=True, text=True, timeout=3)
        for token in r.stdout.split():
            if "x" in token:
                parts = token.split("x")
                if len(parts) == 2 and all(p.isdigit() for p in parts):
                    _screen_size = (int(parts[0]), int(parts[1]))
                    return _screen_size
    except Exception:
        pass
    _screen_size = (1080, 1920)
    return _screen_size


def _capture_jpeg(quality=65, scale=1.0):
    try:
        from PIL import Image
        import io
        r = subprocess.run(["screencap", "-p"], capture_output=True, timeout=4)
        if r.returncode != 0 or not r.stdout:
            return None
        img = Image.open(io.BytesIO(r.stdout)).convert("RGB")
        if scale < 1.0:
            w, h = img.size
            img = img.resize((int(w*scale), int(h*scale)), Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=False)
        return buf.getvalue()
    except Exception:
        return None


def _capture_png():
    try:
        r = subprocess.run(["screencap", "-p"], capture_output=True, timeout=4)
        return r.stdout if r.returncode == 0 and r.stdout else None
    except Exception:
        return None


try:
    from PIL import Image as _pil
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

IS_TERMUX = Path("/data/data/com.termux/files/home").exists()

CWD = str(Path.home() / "storage" / "shared") if IS_TERMUX and (Path.home() / "storage" / "shared").exists() else str(Path.home())


# ── Handler ────────────────────────────────────────────
class AgentHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"  [{self.address_string()}] {fmt % args}")

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()

    def do_GET(self):
        global CWD

        STATIC = {
            "/app":           ("index.html",   "text/html; charset=utf-8"),
            "/app/":          ("index.html",   "text/html; charset=utf-8"),
            "/index.html":    ("index.html",   "text/html; charset=utf-8"),
            "/manifest.json": ("manifest.json","application/manifest+json"),
            "/sw.js":         ("sw.js",        "application/javascript"),
        }
        if self.path in STATIC:
            fname, mime = STATIC[self.path]
            fpath = INSTALL_DIR / fname
            if not fpath.exists():
                self.send_json({"error": f"{fname} not found"}, 404); return
            body = fpath.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._cors()
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/":
            self.send_json({
                "name": "Tovsa RC Agent", "version": "1.3",
                "platform": sys.platform, "cwd": CWD,
                "python": sys.version.split()[0],
                "termux": IS_TERMUX,
                "storage": str(Path.home() / "storage") if IS_TERMUX else None,
                "pillow": HAS_PILLOW,
                "tls": getattr(self.server, 'is_https', False),
            })
            return

        if self.path == "/cwd":
            self.send_json({"cwd": CWD}); return

        if self.path == "/ls":
            try:
                entries = []
                p = Path(CWD)
                if IS_TERMUX and str(p) == str(Path.home()):
                    sr = p / "storage"
                    if sr.exists():
                        for sub in sorted(sr.iterdir()):
                            if sub.is_symlink() or sub.is_dir():
                                entries.append({"name": sub.name, "type": "dir", "size": None, "_path": str(sub.resolve())})
                for e in sorted(p.iterdir()):
                    if e.name == "storage" and IS_TERMUX: continue
                    try:
                        st = e.stat()
                        entries.append({"name": e.name, "type": "dir" if e.is_dir() else "file",
                                        "size": st.st_size if e.is_file() else None})
                    except PermissionError:
                        pass
                self.send_json({"cwd": CWD, "entries": entries})
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        if self.path == "/screeninfo":
            w, h = _get_screen_size()
            self.send_json({"width": w, "height": h, "pillow": HAS_PILLOW}); return

        if self.path.startswith("/stream"):
            quality, scale = 65, 1.0
            if "q=" in self.path:
                try: quality = int(self.path.split("q=")[1].split("&")[0])
                except: pass
            if "s=" in self.path:
                try: scale = float(self.path.split("s=")[1].split("&")[0])
                except: pass
            quality = max(10, min(95, quality))
            scale   = max(0.2, min(1.0, scale))

            self.send_response(200)
            self.send_header("Content-Type",  "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache, no-store")
            self._cors()
            self.end_headers()
            try:
                while True:
                    t0 = time.monotonic()
                    frame = _capture_jpeg(quality, scale) if HAS_PILLOW else _capture_png()
                    mime  = b"image/jpeg" if HAS_PILLOW else b"image/png"
                    if frame is None:
                        time.sleep(0.5); continue
                    self.wfile.write(
                        b"--frame\r\nContent-Type: " + mime +
                        b"\r\nContent-Length: " + str(len(frame)).encode() +
                        b"\r\n\r\n" + frame + b"\r\n"
                    )
                    self.wfile.flush()
                    elapsed = time.monotonic() - t0
                    sleep = max(0.0, 0.066 - elapsed)
                    if sleep: time.sleep(sleep)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            return

        self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        global CWD
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length))
        except:
            self.send_json({"error": "Invalid JSON"}, 400); return

        if self.path == "/run":
            cmd = data.get("cmd","").strip()
            cwd = data.get("cwd", CWD)
            timeout = min(int(data.get("timeout", 30)), 120)
            if not cmd: self.send_json({"error": "Empty command"}, 400); return
            try:
                r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True,
                                   text=True, timeout=timeout, encoding="utf-8", errors="replace")
                self.send_json({"stdout": r.stdout, "stderr": r.stderr, "code": r.returncode, "cmd": cmd})
            except subprocess.TimeoutExpired:
                self.send_json({"error": f"Timeout ({timeout}s)", "code": -1})
            except Exception as e:
                self.send_json({"error": str(e), "code": -1})
            return

        if self.path == "/cd":
            path = data.get("path","").strip()
            if not path: self.send_json({"error": "Empty path"}, 400); return
            np = (Path(CWD) / path if not os.path.isabs(path) else Path(path)).resolve()
            if not np.exists(): self.send_json({"error": f"Not found: {np}"}, 404); return
            if not np.is_dir(): self.send_json({"error": f"Not a dir: {np}"}, 400); return
            CWD = str(np)
            self.send_json({"cwd": CWD}); return

        if self.path == "/open":
            path = data.get("path","").strip()
            full = (Path(CWD) / path if not os.path.isabs(path) else Path(path))
            if not full.exists(): self.send_json({"error": f"Not found: {full}"}, 404); return
            try:
                if sys.platform == "darwin": subprocess.Popen(["open", str(full)])
                elif sys.platform == "win32": os.startfile(str(full))
                else: subprocess.Popen(["xdg-open", str(full)])
                self.send_json({"ok": True})
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        if self.path == "/write":
            path = data.get("path","").strip()
            full = (Path(CWD) / path if not os.path.isabs(path) else Path(path))
            try:
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_text(data.get("content",""), encoding="utf-8")
                self.send_json({"ok": True})
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        if self.path == "/read":
            path = data.get("path","").strip()
            full = (Path(CWD) / path if not os.path.isabs(path) else Path(path))
            if not full.exists(): self.send_json({"error": f"Not found: {full}"}, 404); return
            size = full.stat().st_size
            if size > 1_000_000: self.send_json({"error": "Too large"}, 400); return
            self.send_json({"content": full.read_text(encoding="utf-8", errors="replace"), "size": size}); return

        if self.path == "/input":
            kind = data.get("type","")
            try:
                w, h = _get_screen_size()
                if kind == "tap":
                    subprocess.run(["input","tap",str(int(float(data["x"])*w)),str(int(float(data["y"])*h))],timeout=3)
                elif kind == "swipe":
                    subprocess.run(["input","swipe",
                        str(int(float(data["x1"])*w)),str(int(float(data["y1"])*h)),
                        str(int(float(data["x2"])*w)),str(int(float(data["y2"])*h)),
                        str(int(data.get("ms",200)))],timeout=3)
                elif kind == "text":
                    subprocess.run(["input","text",str(data.get("text","")).replace(" ","%s")],timeout=3)
                elif kind == "key":
                    subprocess.run(["input","keyevent",str(data.get("keycode",""))],timeout=3)
                self.send_json({"ok": True})
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        self.send_json({"error": "Unknown endpoint"}, 404)


class AgentServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    is_https = False

    def get_request(self):
        sock, addr = super().get_request()
        if self._ssl_ctx:
            sock = self._ssl_ctx.wrap_socket(sock, server_side=True)
        return sock, addr

    def __init__(self, addr, handler, ssl_ctx=None):
        self._ssl_ctx = ssl_ctx
        self.is_https  = ssl_ctx is not None
        super().__init__(addr, handler)


def main():
    has_tls = _ensure_cert()
    ssl_ctx = _make_ssl_ctx() if has_tls else None

    servers = []

    # Always start HTTP
    try:
        http = AgentServer((HOST, PORT_HTTP), AgentHandler, ssl_ctx=None)
        servers.append((http, f"http://{HOST}:{PORT_HTTP}"))
    except OSError as e:
        print(f"  ✗  HTTP port {PORT_HTTP} busy: {e}")

    # Start HTTPS if cert available
    if ssl_ctx:
        try:
            https = AgentServer((HOST, PORT_HTTPS), AgentHandler, ssl_ctx=ssl_ctx)
            servers.append((https, f"https://{HOST}:{PORT_HTTPS}"))
        except OSError as e:
            print(f"  ✗  HTTPS port {PORT_HTTPS} busy: {e}")

    if not servers:
        print("  ✗  No ports available"); sys.exit(1)

    https_url = f"https://{HOST}:{PORT_HTTPS}" if ssl_ctx else None

    print(f"""
╔════════════════════════════════════════╗
║         Tovsa RC Agent v1.3            ║
╚════════════════════════════════════════╝

  HTTP:  http://{HOST}:{PORT_HTTP}/app
  HTTPS: {https_url+'/app' if https_url else '✗ недоступен'}
  Папка: {CWD}
  JPEG:  {'✓ Pillow' if HAS_PILLOW else '✗ PNG'}

{'  ┌─ Первый раз: открой '+https_url+' в браузере' if https_url else ''}
{'  │  нажми Дополнительно → Всё равно перейти' if https_url else ''}
{'  └─ затем используй HTTPS адрес в агенте' if https_url else ''}

  Ctrl+C для остановки
""")

    import threading
    for srv, url in servers[1:]:
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()

    try:
        servers[0][0].serve_forever()
    except KeyboardInterrupt:
        print("\n  Агент остановлен.")
        for srv, _ in servers:
            srv.server_close()


if __name__ == "__main__":
    main()
