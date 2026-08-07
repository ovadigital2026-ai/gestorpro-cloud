#!/usr/bin/env python3
# GestorPro Cloud - compatible Render free
#!/usr/bin/env python3
"""GestorPro Cloud — multi-usuario, un solo URL (stdlib only)."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import hashlib
import time
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "gestorpro.db"
STATIC = ROOT / "static"

ADMIN_USER = "admin"
ADMIN_PASS = "050221"
SALT = "gestorpro_cloud_salt_v1"

DEFAULT_SERVICES = [
    {"id": "svc_netflix", "name": "Netflix", "category": "streaming", "cost": 5, "sellPrice": 8, "durationDays": 30, "description": "Perfil Netflix", "icon": "netflix", "color": "#E50914"},
    {"id": "svc_disney", "name": "Disney+", "category": "streaming", "cost": 4, "sellPrice": 7, "durationDays": 30, "description": "Perfil Disney+", "icon": "disney", "color": "#113CCF"},
    {"id": "svc_hbo", "name": "HBO Max / Max", "category": "streaming", "cost": 5.5, "sellPrice": 9, "durationDays": 30, "description": "Perfil Max", "icon": "hbo", "color": "#5B2C6F"},
    {"id": "svc_prime", "name": "Prime Video", "category": "streaming", "cost": 3.5, "sellPrice": 6, "durationDays": 30, "description": "Prime Video", "icon": "prime", "color": "#00A8E1"},
    {"id": "svc_spotify", "name": "Spotify Premium", "category": "streaming", "cost": 3, "sellPrice": 5.5, "durationDays": 30, "description": "Spotify", "icon": "spotify", "color": "#1DB954"},
    {"id": "svc_youtube", "name": "YouTube Premium", "category": "streaming", "cost": 4, "sellPrice": 7, "durationDays": 30, "description": "YouTube Premium", "icon": "youtube", "color": "#FF0000"},
    {"id": "svc_internet_m", "name": "Internet móvil 10GB", "category": "internet", "cost": 8, "sellPrice": 12, "durationDays": 30, "description": "Plan 10GB", "icon": "internet", "color": "#F59E0B"},
    {"id": "svc_internet_l", "name": "Internet móvil 30GB", "category": "internet", "cost": 15, "sellPrice": 22, "durationDays": 30, "description": "Plan 30GB", "icon": "internet", "color": "#D97706"},
]

_lock = threading.Lock()


def hash_password(password: str) -> str:
    return hashlib.sha256((password + "::" + SALT).encode()).hexdigest()[:48]


def db():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _lock:
        conn = db()
        c = conn.cursor()
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              id TEXT PRIMARY KEY,
              username TEXT UNIQUE NOT NULL,
              password_hash TEXT NOT NULL,
              name TEXT,
              role TEXT DEFAULT 'user',
              active INTEGER DEFAULT 1,
              created_at REAL
            );
            CREATE TABLE IF NOT EXISTS user_data (
              user_id TEXT PRIMARY KEY,
              payload TEXT NOT NULL,
              updated_at REAL
            );
            CREATE TABLE IF NOT EXISTS defaults (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
              token TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              created_at REAL
            );
            """
        )
        c.execute("SELECT id FROM users WHERE username=?", (ADMIN_USER,))
        if not c.fetchone():
            uid = "user_admin"
            c.execute(
                "INSERT INTO users VALUES (?,?,?,?,?,?,?)",
                (uid, ADMIN_USER, hash_password(ADMIN_PASS), "Administrador", "admin", 1, time.time()),
            )
            payload = json.dumps(
                {
                    "clients": [],
                    "services": DEFAULT_SERVICES,
                    "contracts": [],
                    "settings": {
                        "currency": "USD",
                        "currencySymbol": "$",
                        "businessName": "OvaDigital",
                        "alertDays": 5,
                    },
                    "meta": {"version": 2, "userId": uid},
                },
                ensure_ascii=False,
            )
            c.execute("INSERT INTO user_data VALUES (?,?,?)", (uid, payload, time.time()))
            c.execute(
                "INSERT INTO defaults VALUES (1, ?)",
                (json.dumps(DEFAULT_SERVICES, ensure_ascii=False),),
            )
        conn.commit()
        conn.close()


def user_dict(row):
    return {
        "id": row["id"],
        "username": row["username"],
        "name": row["name"] or row["username"],
        "role": row["role"],
        "active": bool(row["active"]),
        "createdAt": row["created_at"],
    }


def empty_data(user_id, services=None):
    return {
        "clients": [],
        "services": services or DEFAULT_SERVICES,
        "contracts": [],
        "settings": {
            "currency": "USD",
            "currencySymbol": "$",
            "businessName": "OvaDigital",
            "alertDays": 5,
        },
        "meta": {"version": 2, "userId": user_id},
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Session-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _auth(self, conn):
        token = self.headers.get("X-Session-Token") or ""
        if not token:
            q = parse_qs(urlparse(self.path).query)
            token = (q.get("token") or [""])[0]
        if not token:
            return None
        row = conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
            (token,),
        ).fetchone()
        if not row or not row["active"]:
            return None
        return row

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            return self._api_get(path, parsed)
        return self._static(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            return self._api_post(path, parsed)
        self._json(404, {"error": "Not found"})

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            return self._api_put(path, parsed)
        self._json(404, {"error": "Not found"})

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            return self._api_delete(path, parsed)
        self._json(404, {"error": "Not found"})

    def _send_file(self, f: Path):
        data = f.read_bytes()
        ctype = "text/html; charset=utf-8"
        if f.suffix == ".js":
            ctype = "application/javascript; charset=utf-8"
        elif f.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        elif f.suffix == ".json":
            ctype = "application/json; charset=utf-8"
        elif f.suffix == ".svg":
            ctype = "image/svg+xml"
        elif f.suffix == ".png":
            ctype = "image/png"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _find_static(self, rel: str):
        """Busca el archivo en varias rutas (por si GitHub/Render lo guardó distinto)."""
        rel = (rel or "index.html").lstrip("/").replace("..", "")
        candidates = [
            STATIC / rel,
            ROOT / rel,
            ROOT / "static" / rel,
            STATIC / "index.html" if rel.endswith(".html") or rel == "index.html" else None,
        ]
        # también nombres planos tipo "static-index.html" no; pero sí static_index
        for c in candidates:
            if c is not None and c.is_file():
                return c
        # búsqueda por nombre de archivo
        name = Path(rel).name
        for folder in (STATIC, ROOT, ROOT / "static"):
            if folder.is_dir():
                hit = folder / name
                if hit.is_file():
                    return hit
                # subcarpeta única
                try:
                    for hit in folder.rglob(name):
                        if hit.is_file():
                            return hit
                except Exception:
                    pass
        return None

    def _static(self, path):
        if path == "/" or path == "" or path == "/index.html":
            rel = "index.html"
        elif path.startswith("/static/"):
            rel = path[len("/static/"):]
        else:
            rel = path.lstrip("/")

        f = self._find_static(rel)
        if not f and rel != "index.html":
            # SPA fallback
            f = self._find_static("index.html")
        if not f:
            # Mensaje útil en vez de 404 vacío
            try:
                listing = []
                for base in (ROOT, STATIC):
                    if base.exists():
                        for item in sorted(base.rglob("*"))[:80]:
                            if item.is_file():
                                listing.append(str(item.relative_to(ROOT)))
                body = (
                    "<h1>GestorPro: no se encontró index.html</h1>"
                    "<p>ROOT=" + str(ROOT) + "</p>"
                    "<p>Archivos vistos:</p><pre>"
                    + "\n".join(listing)
                    + "</pre>"
                    "<p>En GitHub debe existir la carpeta <b>static</b> con <b>index.html</b> y <b>cloud-bridge.js</b>.</p>"
                ).encode("utf-8")
            except Exception as e:
                body = ("404 static missing: " + str(e)).encode("utf-8")
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return self._send_file(f)

    def _api_get(self, path, parsed):
        conn = db()
        try:
            if path == "/api/health":
                return self._json(200, {"ok": True, "service": "GestorPro Cloud"})

            if path == "/api/me":
                u = self._auth(conn)
                if not u:
                    return self._json(401, {"error": "No autorizado"})
                return self._json(200, {"user": user_dict(u)})

            if path == "/api/users":
                u = self._auth(conn)
                if not u or u["role"] != "admin":
                    return self._json(403, {"error": "Solo admin"})
                rows = conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
                return self._json(200, {"users": [user_dict(r) for r in rows]})

            if path == "/api/data":
                u = self._auth(conn)
                if not u:
                    return self._json(401, {"error": "No autorizado"})
                q = parse_qs(parsed.query)
                target = u["id"]
                if u["role"] == "admin" and q.get("userId"):
                    target = q["userId"][0]
                row = conn.execute(
                    "SELECT payload FROM user_data WHERE user_id=?", (target,)
                ).fetchone()
                if not row:
                    return self._json(200, {"data": empty_data(target)})
                return self._json(200, {"data": json.loads(row["payload"])})

            if path == "/api/defaults":
                u = self._auth(conn)
                if not u:
                    return self._json(401, {"error": "No autorizado"})
                row = conn.execute("SELECT payload FROM defaults WHERE id=1").fetchone()
                return self._json(
                    200, {"defaults": json.loads(row["payload"]) if row else DEFAULT_SERVICES}
                )

            return self._json(404, {"error": "Not found"})
        finally:
            conn.close()

    def _api_post(self, path, parsed):
        conn = db()
        try:
            body = self._read_json()

            if path == "/api/login":
                username = (body.get("username") or "").strip()
                password = body.get("password") or ""
                row = conn.execute(
                    "SELECT * FROM users WHERE lower(username)=lower(?)", (username,)
                ).fetchone()
                if not row or row["password_hash"] != hash_password(password):
                    return self._json(401, {"error": "Usuario o contraseña incorrectos"})
                if not row["active"]:
                    return self._json(403, {"error": "Usuario desactivado"})
                token = os.urandom(18).hex()
                conn.execute(
                    "INSERT INTO sessions VALUES (?,?,?)", (token, row["id"], time.time())
                )
                conn.commit()
                return self._json(200, {"token": token, "user": user_dict(row)})

            if path == "/api/logout":
                token = self.headers.get("X-Session-Token")
                if token:
                    conn.execute("DELETE FROM sessions WHERE token=?", (token,))
                    conn.commit()
                return self._json(200, {"ok": True})

            if path == "/api/users":
                u = self._auth(conn)
                if not u or u["role"] != "admin":
                    return self._json(403, {"error": "Solo admin"})
                username = (body.get("username") or "").strip()
                password = body.get("password") or ""
                name = (body.get("name") or username).strip()
                if not username or not password:
                    return self._json(400, {"error": "Usuario y contraseña requeridos"})
                if len(password) < 4:
                    return self._json(400, {"error": "Contraseña mínimo 4 caracteres"})
                if conn.execute(
                    "SELECT id FROM users WHERE lower(username)=lower(?)", (username,)
                ).fetchone():
                    return self._json(400, {"error": "Ese usuario ya existe"})
                uid = "user_" + os.urandom(6).hex()
                conn.execute(
                    "INSERT INTO users VALUES (?,?,?,?,?,?,?)",
                    (uid, username, hash_password(password), name, "user", 1, time.time()),
                )
                defs = conn.execute("SELECT payload FROM defaults WHERE id=1").fetchone()
                services = json.loads(defs["payload"]) if defs else DEFAULT_SERVICES
                conn.execute(
                    "INSERT INTO user_data VALUES (?,?,?)",
                    (uid, json.dumps(empty_data(uid, services), ensure_ascii=False), time.time()),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
                return self._json(200, {"user": user_dict(row)})

            m = re.match(r"^/api/users/([^/]+)/reset$", path)
            if m:
                u = self._auth(conn)
                if not u or u["role"] != "admin":
                    return self._json(403, {"error": "Solo admin"})
                uid = m.group(1)
                defs = conn.execute("SELECT payload FROM defaults WHERE id=1").fetchone()
                services = json.loads(defs["payload"]) if defs else DEFAULT_SERVICES
                conn.execute(
                    "INSERT OR REPLACE INTO user_data VALUES (?,?,?)",
                    (uid, json.dumps(empty_data(uid, services), ensure_ascii=False), time.time()),
                )
                conn.commit()
                return self._json(200, {"ok": True})

            return self._json(404, {"error": "Not found"})
        finally:
            conn.close()

    def _api_put(self, path, parsed):
        conn = db()
        try:
            body = self._read_json()
            u = self._auth(conn)
            if not u:
                return self._json(401, {"error": "No autorizado"})

            if path == "/api/data":
                q = parse_qs(parsed.query)
                target = u["id"]
                if u["role"] == "admin" and q.get("userId"):
                    target = q["userId"][0]
                if not isinstance(body, dict):
                    return self._json(400, {"error": "JSON inválido"})
                body.setdefault("meta", {})["userId"] = target
                conn.execute(
                    "INSERT OR REPLACE INTO user_data VALUES (?,?,?)",
                    (target, json.dumps(body, ensure_ascii=False), time.time()),
                )
                conn.commit()
                return self._json(200, {"ok": True})

            if path == "/api/defaults":
                if u["role"] != "admin":
                    return self._json(403, {"error": "Solo admin"})
                if not isinstance(body, list):
                    return self._json(400, {"error": "Lista esperada"})
                conn.execute(
                    "INSERT OR REPLACE INTO defaults VALUES (1, ?)",
                    (json.dumps(body, ensure_ascii=False),),
                )
                conn.commit()
                return self._json(200, {"ok": True})

            m = re.match(r"^/api/users/([^/]+)$", path)
            if m:
                if u["role"] != "admin":
                    return self._json(403, {"error": "Solo admin"})
                uid = m.group(1)
                row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
                if not row:
                    return self._json(404, {"error": "No encontrado"})
                name = body.get("name", row["name"])
                active = body.get("active", bool(row["active"]))
                if row["username"] == ADMIN_USER and active is False:
                    return self._json(400, {"error": "No se puede desactivar admin"})
                if body.get("password"):
                    conn.execute(
                        "UPDATE users SET name=?, active=?, password_hash=? WHERE id=?",
                        (name, 1 if active else 0, hash_password(body["password"]), uid),
                    )
                else:
                    conn.execute(
                        "UPDATE users SET name=?, active=? WHERE id=?",
                        (name, 1 if active else 0, uid),
                    )
                conn.commit()
                row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
                return self._json(200, {"user": user_dict(row)})

            return self._json(404, {"error": "Not found"})
        finally:
            conn.close()

    def _api_delete(self, path, parsed):
        conn = db()
        try:
            u = self._auth(conn)
            if not u or u["role"] != "admin":
                return self._json(403, {"error": "Solo admin"})
            m = re.match(r"^/api/users/([^/]+)$", path)
            if not m:
                return self._json(404, {"error": "Not found"})
            uid = m.group(1)
            row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if not row:
                return self._json(404, {"error": "No encontrado"})
            if row["username"] == ADMIN_USER:
                return self._json(400, {"error": "No se puede eliminar admin"})
            conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            conn.execute("DELETE FROM user_data WHERE user_id=?", (uid,))
            conn.execute("DELETE FROM users WHERE id=?", (uid,))
            conn.commit()
            return self._json(200, {"ok": True})
        finally:
            conn.close()


def main():
    init_db()
    port = int(os.environ.get("PORT", "8080"))
    print("GestorPro ROOT=%s" % ROOT, flush=True)
    print("GestorPro STATIC=%s exists=%s" % (STATIC, STATIC.is_dir()), flush=True)
    for p in sorted(ROOT.rglob("*")):
        if p.is_file():
            print("  file:", p.relative_to(ROOT), flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print("GestorPro Cloud http://0.0.0.0:%s" % port, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
