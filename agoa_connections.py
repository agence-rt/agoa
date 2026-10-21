"""Connexions partagées AGOA : clés et comptes saisis une seule fois dans le launcher,
relus ensuite par toutes les applications AGOA (voir README, section « Connexions partagées »).

Stockage : un seul fichier, chiffré avec la protection de session Windows (DPAPI, compte utilisateur courant).
Aucune clé n'est écrite en clair sur le disque. Les validations réseau passent par la fonction `http` fournie
par le launcher (proxy, repli curl/certificats Windows)."""
import base64
import hashlib
import http.server
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse

GOOGLE_SCOPES = ["openid", "email", "profile", "https://www.googleapis.com/auth/gmail.send",
                 "https://www.googleapis.com/auth/calendar.readonly"]   # Gmail (AGOA TD) et Agenda (AGOA DTG)
ALLOWED_DOMAIN = "remithollet.fr"
RAGIC_APNAME = "agoa"
RAGIC_HOSTS = ["www.ragic.com", "eu2.ragic.com", "na3.ragic.com", "ap5.ragic.com"]
RAGIC_PROBE = "/rendez-vous/9"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"


class ConnError(Exception):
    """Erreur affichable telle quelle à l'utilisateur."""


# --- chiffrement DPAPI -------------------------------------------------------
def _dpapi(data, protect):
    if os.name != "nt":                      # hors Windows (tests) : pas de chiffrement
        return b"PLAIN:" + data if protect else data[len(b"PLAIN:"):]
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    src = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("Chiffrement Windows (DPAPI) impossible")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        k = ctypes.windll.kernel32
        k.LocalFree.argtypes = [ctypes.c_void_p]
        k.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


class ConnectionStore:
    """{ragic:{key,host,apname}, google:{refresh_token,email,name,scopes,client_id}, anthropic:{key}}"""

    def __init__(self, path):
        self.path = path

    def _read(self):
        try:
            with open(self.path, "rb") as f:
                return json.loads(_dpapi(f.read(), False).decode("utf-8"))
        except (OSError, ValueError):
            return {}

    def _write(self, data):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(_dpapi(json.dumps(data, ensure_ascii=False).encode("utf-8"), True))
        os.replace(tmp, self.path)

    def get(self, name):
        return self._read().get(name) or None

    def set(self, name, value):
        d = self._read()
        d[name] = value
        self._write(d)

    def remove(self, name):
        d = self._read()
        if d.pop(name, None) is not None:
            self._write(d)

    def env(self, client=None):
        """Variables d'environnement transmises aux applications lancées depuis le launcher."""
        d, e = self._read(), {}
        r, g, a = d.get("ragic"), d.get("google"), d.get("anthropic")
        if r:
            e.update(AGOA_RAGIC_KEY=r.get("key", ""), AGOA_RAGIC_HOST=r.get("host", ""), AGOA_RAGIC_APNAME=r.get("apname", RAGIC_APNAME))
        if a:
            e["AGOA_ANTHROPIC_API_KEY"] = a.get("key", "")
        if g:
            e.update(AGOA_GOOGLE_REFRESH_TOKEN=g.get("refresh_token", ""), AGOA_GOOGLE_EMAIL=g.get("email", ""))
            if g.get("client_id"):
                e["AGOA_GOOGLE_CLIENT_ID"] = g["client_id"]
        return {k: v for k, v in e.items() if v}


# --- saisie et validation ----------------------------------------------------
def clean_key(kind, raw):
    """Retire espaces, guillemets, retours à la ligne et caractères invisibles d'une clé collée."""
    return re.sub(r"[^A-Za-z0-9+/=_-]" if kind == "ragic" else r"[^A-Za-z0-9_-]", "", str(raw or ""))


def check_ragic(key, http):
    """Cherche le serveur Ragic qui accepte la clé. Retourne le nom d'hôte ou lève ConnError."""
    if len(key) < 16:
        raise ConnError("Cette clé Ragic semble trop courte : recopiez-la en entier.")
    last = None
    for host in RAGIC_HOSTS:
        url = f"https://{host}/{RAGIC_APNAME}{RAGIC_PROBE}?api&v=3&limit=1&naming=EID&APIKey={urllib.parse.quote(key, safe='')}"
        try:
            raw = http(url, {"Accept": "application/json"})
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise ConnError("Clé Ragic refusée par Ragic.")
            last = e
            continue
        except Exception as e:  # réseau : on essaie le serveur suivant, l'erreur est remontée à la fin
            last = e
            continue
        try:
            j = json.loads(raw.decode("utf-8"))
        except ValueError:
            last = ConnError("Réponse Ragic illisible.")
            continue
        if isinstance(j, dict) and j.get("status") == "ERROR":
            msg = str(j.get("msg", ""))
            last = ConnError("Ragic ne reconnaît pas cette clé (accès « invité »)." if re.search(r"guest", msg, re.I) else "Ragic : " + msg[:160])
            continue
        return host
    raise last or ConnError("Aucun serveur Ragic ne répond.")


def check_anthropic(key, http):
    if not key.startswith("sk-ant-") or len(key) < 30:
        raise ConnError("Une clé Claude commence par « sk-ant- » : recopiez-la en entier.")
    try:
        http("https://api.anthropic.com/v1/models?limit=1", {"x-api-key": key, "anthropic-version": "2023-06-01", "Accept": "application/json"})
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ConnError("Clé Claude refusée par Anthropic.")
        raise ConnError(f"Anthropic a répondu {e.code}.")
    return True


# --- Google (OAuth « application installée » : navigateur + PKCE + retour local) ---
def google_client(paths, env=os.environ):
    for p in paths:
        try:
            with open(p, encoding="utf-8-sig") as f:
                j = json.load(f)
            if j.get("clientId") and j.get("clientSecret"):
                return j
        except (OSError, ValueError):
            pass
    if env.get("GOOGLE_CLIENT_ID") and env.get("GOOGLE_CLIENT_SECRET"):
        return {"clientId": env["GOOGLE_CLIENT_ID"], "clientSecret": env["GOOGLE_CLIENT_SECRET"]}
    return None


def _b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def decode_id_token(t):
    part = t.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


_PAGE = ("<!doctype html><meta charset='utf-8'><title>{t}</title><body style=\"font-family:Segoe UI,sans-serif;background:#F3F2EE;"
         "display:grid;place-items:center;height:100vh;margin:0\"><div style='border-top:4px solid #B3261E;background:#fff;"
         "padding:28px 36px;max-width:420px'><h2 style='margin:0 0 8px'>{t}</h2><p style='margin:0;color:#555'>{m}</p></div>")


def google_sign_in(client, call, open_url, cancel=None, timeout=300):
    """Ouvre le navigateur, attend le retour de Google, retourne {refresh_token,email,name,scopes,client_id}."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    state = _b64url(secrets.token_bytes(16))

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            if u.path != "/callback":
                self.send_response(404); self.end_headers(); return
            ok = q.get("state", [""])[0] == state
            code, err = q.get("code", [""])[0], q.get("error", [""])[0]
            title, msg = ("Connexion réussie", "Vous pouvez fermer cet onglet et revenir au launcher AGOA.") if ok and code and not err \
                else ("Connexion annulée", "Vous pouvez fermer cet onglet et revenir au launcher AGOA.")
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_PAGE.format(t=title, m=msg).encode("utf-8"))
            if ok:
                self.server.result = ("" if err else code, err)

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    srv.result = None
    srv.timeout = 0.3
    redirect = f"http://127.0.0.1:{srv.server_address[1]}/callback"
    q = urllib.parse.urlencode({
        "client_id": client["clientId"], "redirect_uri": redirect, "response_type": "code", "scope": " ".join(GOOGLE_SCOPES),
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
        "access_type": "offline", "prompt": "consent", "hd": ALLOWED_DOMAIN})
    try:
        open_url(f"{AUTH_URL}?{q}")
        deadline = time.time() + timeout
        while srv.result is None:
            if (cancel and cancel()) or time.time() > deadline:
                raise ConnError("Connexion Google non terminée (délai dépassé ou annulée).")
            srv.handle_request()
    finally:
        srv.server_close()
    code, err = srv.result
    if err or not code:
        raise ConnError("Connexion Google annulée.")
    body = urllib.parse.urlencode({"code": code, "client_id": client["clientId"], "client_secret": client["clientSecret"],
                                   "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier}).encode()
    try:
        tok = json.loads(call(TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}, body).decode())
    except urllib.error.HTTPError:
        raise ConnError("Google a refusé la connexion (identifiants de l'application incorrects ?).")
    p = decode_id_token(tok["id_token"])
    email = str(p.get("email", "")).lower()
    if not p.get("email_verified") or not email.endswith("@" + ALLOWED_DOMAIN):
        raise ConnError(f"Seuls les comptes Google @{ALLOWED_DOMAIN} sont acceptés.")
    if not tok.get("refresh_token"):
        raise ConnError("Google n'a pas fourni de session durable. Réessayez.")
    return {"refresh_token": tok["refresh_token"], "email": email, "name": p.get("name") or email,
            "scopes": tok.get("scope", ""), "client_id": client["clientId"]}


def google_revoke(token, call):
    try:
        call(REVOKE_URL, {"Content-Type": "application/x-www-form-urlencoded"}, urllib.parse.urlencode({"token": token}).encode())
    except Exception:
        pass  # sans importance : la session est de toute façon supprimée ici
