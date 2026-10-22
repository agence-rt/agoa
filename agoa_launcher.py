"""AGOA Launcher — installation, mise à jour et lancement des programmes AGOA.

Windows, Python 3.9+ (tkinter uniquement, aucune dépendance).
Les versions sont lues sur GitHub (dernière « Release » de chaque dépôt).
Tout est installé sous C:\\AGOA (une sous-dossier par programme).
"""
import fnmatch
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import ssl
import socket
import urllib.error
import urllib.request
import urllib.parse
import zipfile
import ctypes
import glob
import time
from ctypes import wintypes
import webbrowser
import agoa_connections as conn
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

APP_NAME = "AGOA Launcher"
LAUNCHER_VERSION = "0.5.5"
LAUNCHER_REPO = "agence-rt/agoa"  # dépôt du lanceur (mises à jour)

# --- Dossier unique d'installation ---------------------------------------
ROOT = os.environ.get("AGOA_HOME", r"C:\AGOA")
try:
    os.makedirs(ROOT, exist_ok=True)
except OSError:  # droits insuffisants sur C:\ -> repli utilisateur
    ROOT = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "AGOA")
    os.makedirs(ROOT, exist_ok=True)
APPS_DIR = ROOT  # C:\AGOA\<id>\
CONFIG_DIR = os.path.join(ROOT, "_launcher")
STATE_FILE = os.path.join(CONFIG_DIR, "state.json")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
CATALOG_CACHE = os.path.join(CONFIG_DIR, "catalog.cache.json")
CONN_FILE = os.path.join(ROOT, "_connexions", "connexions.dat")   # connexions partagées (chiffré DPAPI)
INSTALL_DIR = os.path.join(ROOT, "Launcher")           # C:\\AGOA\\Launcher\\AGOA-Launcher.exe
SETUP_PATTERN = "AGOA-Launcher-Setup-*.exe"

# --- Charte AGOA : rouge agence, carré + code en gras --------------------
RED = "#B3261E"  # rouge agence (identique aux autres applications AGOA)
BG = "#F3F2EE"
CARD = "#F3F2EE"
INK = "#111111"
MUTED = "#7A7A75"
LINE = "#D6D4CC"
BLUE_BG, BLUE_FG = "#E3ECF7", "#2563B8"
GREEN_BG, GREEN_FG = "#E1F2E7", "#1E7A46"
ORANGE_BG, ORANGE_FG = "#FBEBD9", "#B45F06"
GREY_BG, GREY_FG = "#E8E6DF", "#7A7A75"
MONO = ("Consolas", 8)
MONO_B = ("Consolas", 8, "bold")


def resource_path(name):
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, name)


def is_installed_launcher():
    """Vrai si l'exe tourne depuis C:\\AGOA\\Launcher (installé par l'installateur)."""
    if not getattr(sys, "frozen", False):
        return False
    here = os.path.normcase(os.path.abspath(os.path.dirname(sys.executable)))
    return here == os.path.normcase(os.path.abspath(INSTALL_DIR))


def base_dir():
    return os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))) or (0,)


def clean_tag(tag):
    return str(tag).lstrip("vV")


def file_version(path):
    """Version d'un .exe Windows (X.Y.Z) lue dans ses ressources ; None si illisible."""
    try:
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        ver.GetFileVersionInfoW(path, 0, size, buf)
        ptr, ln = ctypes.c_void_p(), ctypes.c_uint()
        if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(ln)):
            return None
        info = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint32 * 6)).contents
        ms, ls = info[2], info[3]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}"
    except Exception:
        return None


# --- GitHub ----------------------------------------------------------------
class _NoAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """N'envoie pas le jeton GitHub vers un autre domaine (stockage des assets)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlparse(newurl).netloc != urllib.parse.urlparse(req.full_url).netloc:
            new.remove_header("Authorization")
        return new


_opener = urllib.request.build_opener(_NoAuthOnRedirect)                       # proxy du système
_opener_direct = urllib.request.build_opener(_NoAuthOnRedirect, urllib.request.ProxyHandler({}))  # sans proxy


class _CurlResp:
    """Réponse minimale (comme urllib) lue depuis un fichier temporaire téléchargé par curl.exe."""

    def __init__(self, path, url, length):
        self._path, self._url = path, url
        self._f = open(path, "rb")
        self.headers = {"Content-Length": str(length)}

    def read(self, n=-1):
        return self._f.read(n)

    def geturl(self):
        return self._url

    def close(self):
        try:
            self._f.close()
            os.remove(self._path)
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _is_ssl_error(e):
    return isinstance(e, ssl.SSLError) or isinstance(getattr(e, "reason", None), ssl.SSLError)


def _curl_open(url, headers, timeout, data=None):
    """Repli TLS : curl.exe (inclus dans Windows 10/11) utilise le magasin de certificats de Windows,
    comme le navigateur, ce qui fonctionne derrière un antivirus ou un pare-feu qui inspecte HTTPS."""
    exe = shutil.which("curl.exe") or shutil.which("curl")
    if not exe:
        raise OSError("curl introuvable")
    fd, tmp = tempfile.mkstemp(prefix="agoa-dl-")
    os.close(fd)
    big = "octet-stream" in headers.get("Accept", "")
    cmd = [exe, "-sSL", "--ssl-no-revoke", "--max-time", "1800" if big else str(max(timeout, 30)),
           "-o", tmp, "-w", "%{url_effective}|%{http_code}", "-A", headers.get("User-Agent", "AGOA-Launcher")]
    for k, v in headers.items():
        if k != "User-Agent":
            cmd += ["-H", f"{k}: {v}"]
    if data is not None:
        cmd += ["-X", "POST", "--data-binary", "@-"]
    cmd.append(url)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags, timeout=1900 if big else 60,
                           input=data.decode("utf-8") if data is not None else None)
        if r.returncode != 0:
            raise OSError((r.stderr or "").strip() or f"curl a échoué ({r.returncode})")
        final, _, code = (r.stdout or "").rpartition("|")
        code = int(code or 0)
        if code >= 400:
            raise urllib.error.HTTPError(url, code, "", {}, None)
        return _CurlResp(tmp, final or url, os.path.getsize(tmp))
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def gh_open(url, token=None, accept="application/vnd.github+json", timeout=20, extra=None, data=None):
    """Ouvre une URL GitHub : proxy du système, puis connexion directe, puis (si le certificat est refusé)
    curl.exe avec le magasin de certificats de Windows. Les erreurs HTTP sont remontées telles quelles."""
    headers = {"User-Agent": "AGOA-Launcher", "Accept": accept}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers.update(extra or {})
    last = None
    for op in (_opener, _opener_direct):
        try:
            return op.open(urllib.request.Request(url, headers=headers, data=data, method="POST" if data is not None else "GET"), timeout=timeout)
        except urllib.error.HTTPError:
            raise
        except Exception as e:  # réseau, DNS, TLS, délai…
            last = e
    if _is_ssl_error(last):
        try:
            return _curl_open(url, headers, timeout, data)
        except urllib.error.HTTPError:
            raise
        except Exception:
            pass
    raise last


def http_call(url, headers=None, data=None, timeout=20):
    """Requête HTTP(S) générique (GET, ou POST si data) avec les mêmes repli réseau que pour GitHub."""
    with gh_open(url, None, accept=(headers or {}).get("Accept", "application/json"), timeout=timeout, extra=headers, data=data) as r:
        return r.read()


def net_reason(e, what="GitHub"):
    """Cause lisible d'un échec réseau (court, affichable sur la carte)."""
    r = getattr(e, "reason", e)
    if isinstance(e, urllib.error.HTTPError):
        if e.code == 407:
            return "Proxy : authentification requise"
        if e.code in (401, 403):
            return f"Accès refusé par {what} (limite ou filtrage)"
        return f"{what} a répondu {e.code}"
    if isinstance(r, ssl.SSLError):
        return "Certificat refusé (pare-feu / antivirus qui filtre HTTPS)"
    if isinstance(r, socket.gaierror):
        return f"DNS : {what} introuvable (réseau filtré ?)"
    if isinstance(r, (socket.timeout, TimeoutError)):
        return f"Délai dépassé : {what} ne répond pas"
    if isinstance(r, (ConnectionRefusedError, ConnectionResetError, ConnectionAbortedError)):
        return "Connexion bloquée (pare-feu / proxy)"
    return f"{what} injoignable : " + (str(r)[:70] or type(r).__name__)


def gh_latest_release_web(repo):
    """Repli sans api.github.com (souvent bloqué ou limité) : pages web publiques de github.com."""
    with gh_open(f"https://github.com/{repo}/releases/latest", None, accept="text/html", timeout=20) as r:
        final = r.geturl()
    m = re.search(r"/releases/tag/([^/?#]+)", final)
    if not m:
        raise RuntimeError("Aucune version publiée")
    tag = urllib.parse.unquote(m.group(1))
    with gh_open(f"https://github.com/{repo}/releases/expanded_assets/{m.group(1)}", None, accept="text/html", timeout=20) as r:
        html = r.read().decode("utf-8", "replace")
    assets, seen = [], set()
    for href in re.findall(r'href="(/' + re.escape(repo) + r'/releases/download/[^"]+)"', html):
        name = urllib.parse.unquote(href.rsplit("/", 1)[-1])
        if name not in seen:
            seen.add(name)
            assets.append({"name": name, "url": "https://github.com" + href, "size": 0})
    return {"version": clean_tag(tag), "date": "", "notes": "", "assets": assets}


def gh_latest_release(repo, token=None):
    """Version publiée (API GitHub, puis repli sur le site web). Lève RuntimeError avec une cause lisible."""
    try:
        return _gh_latest_release_api(repo, token)
    except RuntimeError as e:
        if "Aucune version" in str(e):
            raise
    except Exception:
        pass
    try:
        return gh_latest_release_web(repo)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(net_reason(e))


def _gh_latest_release_api(repo, token=None):
    """Retourne {'version','date','assets':[{name,url,size}]} ou lève une exception."""
    try:
        with gh_open(f"https://api.github.com/repos/{repo}/releases/latest", token) as r:
            d = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RuntimeError("Aucune version publiée (ou dépôt privé : renseigner le jeton GitHub)")
        if e.code in (401, 403):
            raise RuntimeError("Accès refusé par GitHub (jeton invalide ou limite atteinte)")
        raise
    return {
        "version": clean_tag(d["tag_name"]),
        "date": (d.get("published_at") or "")[:10],
        "notes": d.get("body") or "",
        "assets": [{"name": a["name"], "url": a["url"], "size": a.get("size", 0)} for a in d.get("assets", [])],
    }


def pick_asset(rel, pattern):
    for a in rel["assets"]:
        if fnmatch.fnmatch(a["name"].lower(), (pattern or "*.zip").lower()):
            return a
    raise RuntimeError(f"Aucun fichier « {pattern} » dans la release {rel['version']}")


def gh_download(asset, dest, token=None, progress=None):
    with gh_open(asset["url"], token, accept="application/octet-stream", timeout=60) as r, open(dest, "wb") as out:
        total = int(r.headers.get("Content-Length") or asset.get("size") or 0)
        done = 0
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(min(done / total, 0.95))


# --- Etat local / installation ----------------------------------------------
class Store:
    def __init__(self):
        self.settings = load_json(SETTINGS_FILE, {})
        self.conn = conn.ConnectionStore(CONN_FILE)
        self.state = load_json(STATE_FILE, {})

    @property
    def token(self):
        return self.settings.get("github_token") or os.environ.get("GITHUB_TOKEN")

    def catalog_local(self):
        """Copie du catalogue embarquée dans l'exe (ou à côté du script)."""
        for d in (getattr(sys, "_MEIPASS", None), base_dir()):
            if d and os.path.exists(os.path.join(d, "catalog.json")):
                return load_json(os.path.join(d, "catalog.json"), {"apps": []})
        return {"apps": []}

    def load_catalog(self):
        """Catalogue : dépôt GitHub du lanceur (à jour) > cache > copie embarquée. Retourne (catalogue, erreur)."""
        err = None
        try:
            url = f"https://raw.githubusercontent.com/{LAUNCHER_REPO}/main/catalog.json?t={int(time.time())}"  # ?t= : contourne le cache de GitHub
            with gh_open(url, self.token, accept="*/*", timeout=10) as r:
                cat = json.loads(r.read().decode("utf-8"))
            if cat.get("apps"):
                save_json(CATALOG_CACHE, cat)
                return cat, None
        except Exception as e:
            err = f"Catalogue GitHub injoignable ({net_reason(e)})"
        cached = load_json(CATALOG_CACHE, None)
        if cached and cached.get("apps"):
            return cached, err
        return self.catalog_local(), err

    def exe_path(self, app):
        """Chemin de l'exécutable installé (C:\\AGOA\\<id>\\<exe>)."""
        d = self.app_dir(app["id"])
        exe = app.get("exe") or app.get("entry")
        if exe:
            p = os.path.join(d, exe)
            if os.path.exists(p):
                return p
            for root, _, files in os.walk(d):
                if exe in files:
                    return os.path.join(root, exe)
        return None

    def installed_version(self, app_id, app=None):
        """Version réellement installée : lue sur l'exe (les applications se mettent aussi à jour seules)."""
        if app is not None:
            p = self.exe_path(app)
            if p:
                return file_version(p) or self.state.get(app_id, {}).get("version") or "0"
            return None
        return self.state.get(app_id, {}).get("version")

    def app_dir(self, app_id):
        return os.path.join(APPS_DIR, app_id)

    def is_installed(self, app):
        return self.exe_path(app) is not None

    def status(self, app, rel):
        iv = self.installed_version(app["id"], app)
        if rel is None:
            return "ok" if iv else "inconnu"
        if iv:
            return "maj" if vtuple(rel["version"]) > vtuple(iv) else "ok"
        return "absent"

    def install(self, app, rel, progress=None):
        asset = pick_asset(rel, app.get("asset"))
        dest = self.app_dir(app["id"])
        tmpdir = tempfile.mkdtemp(prefix="agoa_")
        try:
            local = os.path.join(tmpdir, asset["name"])
            gh_download(asset, local, self.token, progress)
            if zipfile.is_zipfile(local):
                staging = dest + ".new"
                shutil.rmtree(staging, ignore_errors=True)
                os.makedirs(staging)
                with zipfile.ZipFile(local) as z:
                    z.extractall(staging)
                shutil.rmtree(dest, ignore_errors=True)
                os.replace(staging, dest)
            elif asset["name"].lower().endswith(".exe") and "setup" in asset["name"].lower():
                # Installateur NSIS (electron-builder) : /S = silencieux, /D = dossier (doit être en dernier)
                os.makedirs(dest, exist_ok=True)
                args = [local, "/S", f"/D={dest}"]
                subprocess.run(args, check=False, timeout=900)
                if not self.exe_path(app):
                    # repli : assistant visible, dossier proposé = C:\\AGOA\\<id>
                    subprocess.run([local, f"/D={dest}"], check=False, timeout=1800)
                if not self.exe_path(app):
                    raise RuntimeError("L'installation ne s'est pas terminée (installateur fermé ?)")
            else:
                os.makedirs(dest, exist_ok=True)
                shutil.copy(local, os.path.join(dest, asset["name"]))
            self.state[app["id"]] = {"version": rel["version"], "asset": asset["name"]}
            save_json(STATE_FILE, self.state)
            if progress:
                progress(1.0)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def uninstall(self, app):
        d = self.app_dir(app["id"])
        for u in glob.glob(os.path.join(d, "Uninstall*.exe")):
            try:
                subprocess.run([u, "/S", f"_?={d}"], check=False, timeout=300)
            except Exception:
                pass
        shutil.rmtree(d, ignore_errors=True)
        self.state.pop(app["id"], None)
        save_json(STATE_FILE, self.state)

    def launch(self, app):
        path = self.exe_path(app)
        if not path:
            raise FileNotFoundError(f"{app['nom']} est introuvable dans {self.app_dir(app['id'])}")
        extra = self.conn.env()
        if extra and os.name == "nt":       # les applications reçoivent les connexions partagées (clés Ragic, Claude, Google)
            env = os.environ.copy()
            env.update(extra)
            env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            subprocess.Popen([path], env=env, cwd=os.path.dirname(path), close_fds=True,
                             creationflags=0x00000008 | 0x00000200)   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        elif hasattr(os, "startfile"):
            os.startfile(path)
        else:
            subprocess.Popen([path])

    def update_launcher(self, rel, progress=None):
        """Télécharge l'installateur du lanceur (à défaut l'exe seul), puis l'applique après fermeture."""
        if not getattr(sys, "frozen", False):
            raise RuntimeError("Mise à jour automatique disponible uniquement dans le programme installé")
        os.makedirs(CONFIG_DIR, exist_ok=True)
        bat = os.path.join(CONFIG_DIR, "update.bat")
        try:
            asset = pick_asset(rel, SETUP_PATTERN)
        except RuntimeError:
            asset = None
        if asset:
            setup = os.path.join(CONFIG_DIR, asset["name"])
            gh_download(asset, setup, self.token, progress)
            target = os.path.join(INSTALL_DIR, "AGOA-Launcher.exe")
            # PYINSTALLER_RESET_ENVIRONMENT : sans cela, la nouvelle version relancée depuis ce processus réutilise le dossier
            # temporaire de l'ancienne (supprimé à sa fermeture) -> « Failed to load Python DLL ».
            lines = ["@echo off", "set PYINSTALLER_RESET_ENVIRONMENT=1", "timeout /t 3 /nobreak >nul", f'start /wait "" "{setup}" /S',
                     f'del "{setup}"', f'start "" "{target}"', 'del "%~f0"']
        else:  # ancien mode : remplacement de l'exe seul
            asset = pick_asset(rel, "AGOA-Launcher.exe")
            exe = sys.executable
            new = exe + ".new"
            gh_download(asset, new, self.token, progress)
            lines = ["@echo off", "set PYINSTALLER_RESET_ENVIRONMENT=1", "timeout /t 3 /nobreak >nul", f'move /Y "{new}" "{exe}"',
                     f'start "" "{exe}"', 'del "%~f0"']
        with open(bat, "w", encoding="utf-8") as f:
            f.write("\r\n".join(lines) + "\r\n")
        env = os.environ.copy()
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        for k in [k for k in env if k.startswith("_PYI") or k == "_MEIPASS2"]:
            env.pop(k)
        subprocess.Popen(["cmd", "/c", bat], creationflags=0x08000000, env=env)


class Flat(tk.Label):
    """Bouton plat façon capture : fond noir / contour, texte mono capitales."""

    def __init__(self, master, text, command, primary=True, **kw):
        fg, bg = ("#FFFFFF", INK) if primary else (INK, BG)
        super().__init__(master, text=text.upper(), font=MONO_B, fg=fg, bg=bg, cursor="hand2",
                         highlightthickness=1, highlightbackground=INK, **kw)
        self.command = command
        self.bind("<Button-1>", lambda e: self.command())
        self.bind("<Enter>", lambda e: self.config(bg="#333333" if primary else "#E8E6DF"))
        self.bind("<Leave>", lambda e: self.config(bg=INK if primary else BG))


def round_rect(cv, x0, y0, x1, y1, r, **kw):
    cv.create_rectangle(x0 + r, y0, x1 - r, y1, width=0, **kw)
    cv.create_rectangle(x0, y0 + r, x1, y1 - r, width=0, **kw)
    for cx, cy in ((x0, y0), (x1 - 2 * r, y0), (x0, y1 - 2 * r), (x1 - 2 * r, y1 - 2 * r)):
        cv.create_oval(cx, cy, cx + 2 * r, cy + 2 * r, width=0, **kw)


class ProgressBar(tk.Canvas):
    """Barre d'avancement animée : remplissage lissé + reflet qui glisse (activité visible même sans pourcentage)."""

    def __init__(self, master, S, **kw):
        super().__init__(master, height=int(30 * S), bg="#E8E6DF", highlightthickness=1, highlightbackground=INK, **kw)
        self.S = S
        self.target = None      # None = indéterminé
        self.shown = 0.0
        self.label = "Préparation…"
        self.phase = 0.0

    def set(self, value, label=None):
        self.target = value
        if label:
            self.label = label

    def step(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4:
            return
        goal = 0.0 if self.target is None else self.target
        self.shown += (goal - self.shown) * 0.18
        self.phase = (self.phase + 0.022) % 1.0
        self.delete("all")
        fw = int(w * self.shown) if self.target is not None else int(w * 0.12)
        if self.target is None:                      # indéterminé : bloc qui va et vient
            x = int((w - fw) * (0.5 - 0.5 * math.cos(self.phase * 2 * math.pi)))
            self.create_rectangle(x, 0, x + fw, h, fill=RED, width=0)
        else:
            self.create_rectangle(0, 0, fw, h, fill=RED, width=0)
            sw = max(int(w * 0.18), 24)              # reflet qui traverse la partie remplie
            sx = int((fw + sw) * self.phase) - sw
            for i in range(sw):
                a, b = max(sx + i, 0), min(sx + i + 1, fw)
                if a < b:
                    t = 1 - abs(i - sw / 2) / (sw / 2)
                    self.create_rectangle(a, 0, b, h, fill="#%02X%02X%02X" % (int(179 + 60 * t), int(38 + 90 * t), int(30 + 90 * t)), width=0)
        txt = self.label if self.target is None else f"{self.label} {int(self.target * 100)} %"
        self.create_text(w // 2, h // 2, text=txt.upper(), font=MONO_B, fill=INK if self.shown < 0.5 else "white")


class ConnectionsWindow(tk.Toplevel):
    """Écran « Connexions » : Ragic, Google et Claude, saisis une seule fois pour toutes les applications AGOA."""

    def __init__(self, app):
        super().__init__(app, bg=BG)
        self.app, self.store, self.S = app, app.store.conn, app.S
        self.title("AGOA · Connexions")
        try:
            self.iconbitmap(resource_path("icon.ico"))
        except Exception:
            pass
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(int(600 * self.S), sw - 40), min(int(740 * self.S), sh - 100)
        self.geometry(f"{w}x{h}+{max((sw - w) // 2, 0)}+{max((sh - h) // 3, 0)}")
        self.minsize(min(int(480 * self.S), w), min(int(420 * self.S), h))
        self.transient(app)
        self.editing, self.working, self.cancel = set(), False, False
        self.vars = {"ragic": tk.StringVar(), "anthropic": tk.StringVar()}
        self.protocol("WM_DELETE_WINDOW", self.close)
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=16, pady=(14, 2))
        tk.Label(top, text="CONNEXIONS", font=MONO_B, bg=INK, fg="white", padx=8, pady=3).pack(side="left")
        tk.Label(self, text="Saisies une seule fois ici, ensuite utilisées par toutes les applications AGOA. "
                            "Les clés sont chiffrées pour votre session Windows.",
                 font=("Segoe UI", 9), bg=BG, fg=MUTED, wraplength=int(540 * self.S), justify="left").pack(anchor="w", padx=16, pady=(4, 8))
        self.msg = tk.Label(self, text="", font=("Segoe UI", 10, "bold"), bg=BG, anchor="w", justify="left", wraplength=int(540 * self.S))
        self.msg.pack(fill="x", padx=16, pady=(0, 6))          # message en haut : toujours visible, même sur petit écran
        self.body = tk.Frame(self, bg=BG)
        self.body.pack(fill="both", expand=True, padx=16)
        self.build()

    # -- utilitaires
    def close(self):
        self.cancel = True
        self.destroy()

    def say(self, text, ok=None):
        try:
            self.msg.config(text=text, fg=GREEN_FG if ok else RED if ok is False else MUTED)
        except tk.TclError:
            pass

    def section(self, num, title, desc, ok):
        f = tk.Frame(self.body, bg=CARD, highlightbackground=LINE, highlightthickness=1, padx=16, pady=12)
        f.pack(fill="x", pady=(0, 10))
        head = tk.Frame(f, bg=CARD)
        head.pack(fill="x")
        tk.Label(head, text=num, font=MONO_B, bg=RED, fg="white", padx=6, pady=2).pack(side="left")
        tk.Label(head, text=title, font=("Segoe UI", 12, "bold"), bg=CARD, fg=INK).pack(side="left", padx=8)
        bg, fg = (GREEN_BG, GREEN_FG) if ok else (GREY_BG, GREY_FG)
        tk.Label(head, text="CONNECTÉ" if ok else "NON CONFIGURÉ", font=MONO, bg=bg, fg=fg, padx=5, pady=2,
                 highlightthickness=1, highlightbackground=fg).pack(side="right")
        tk.Label(f, text=desc, font=("Segoe UI", 9), bg=CARD, fg="#444", wraplength=int(500 * self.S), justify="left",
                 anchor="w").pack(anchor="w", pady=(6, 8))
        return f

    def key_row(self, f, kind, label):
        row = tk.Frame(f, bg=CARD)
        row.pack(fill="x")
        e = tk.Entry(row, textvariable=self.vars[kind], show="•", font=("Segoe UI", 10), relief="solid", bd=1, bg="#F7F6F2")
        e.pack(side="left", fill="x", expand=True, ipady=5)
        Flat(row, "Valider", lambda: self.save_key(kind), True, padx=14, pady=6).pack(side="left", padx=(8, 0))
        if kind in self.editing:
            Flat(row, "Annuler", lambda: self.stop_edit(kind), False, padx=10, pady=6).pack(side="left", padx=(6, 0))
        tk.Label(f, text=label, font=MONO, bg=CARD, fg=MUTED).pack(anchor="w", pady=(4, 0))

    def saved_row(self, f, kind, detail):
        tk.Label(f, text=detail, font=("Segoe UI", 9), bg=CARD, fg=INK, anchor="w").pack(anchor="w", pady=(0, 8))
        row = tk.Frame(f, bg=CARD)
        row.pack(fill="x")
        Flat(row, "Remplacer", lambda: self.start_edit(kind), False, padx=12, pady=6).pack(side="left")
        Flat(row, "Supprimer", lambda: self.remove(kind), False, padx=12, pady=6).pack(side="left", padx=(6, 0))

    def start_edit(self, kind):
        self.editing.add(kind)
        self.build()

    def stop_edit(self, kind):
        self.editing.discard(kind)
        self.vars[kind].set("")
        self.build()

    # -- construction
    def build(self):
        for w in self.body.winfo_children():
            w.destroy()
        r, g, a = self.store.get("ragic"), self.store.get("google"), self.store.get("anthropic")

        f = self.section("01", "RAGIC", "Clé API de l'agence (Ragic > Compte > API). Le serveur est détecté automatiquement.", bool(r))
        if r and "ragic" not in self.editing:
            self.saved_row(f, "ragic", f"Serveur {r.get('host', '?')} · base « {r.get('apname', conn.RAGIC_APNAME)} »")
        else:
            self.key_row(f, "ragic", "CLÉ API RAGIC")

        f = self.section("02", "GOOGLE", f"Compte Google de l'agence (@{conn.ALLOWED_DOMAIN}) : identification, envoi de courriels (Gmail) et lecture de l'agenda.", bool(g))
        if g:
            self.saved_row_google(f, g)
        else:
            row = tk.Frame(f, bg=CARD)
            row.pack(fill="x")
            Flat(row, "Se connecter avec Google", self.google_in, True, padx=14, pady=8).pack(side="left")
            if self.client is None:
                tk.Label(f, text="Cette version ne contient pas les identifiants Google de l'application "
                                 "(secrets GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET du dépôt GitHub).",
                         font=("Segoe UI", 8), bg=CARD, fg=ORANGE_FG, wraplength=int(500 * self.S), justify="left").pack(anchor="w", pady=(6, 0))

        f = self.section("03", "CLAUDE", "Clé API Anthropic (console.anthropic.com > API keys), pour les fonctions d'IA des applications.", bool(a))
        if a and "anthropic" not in self.editing:
            k = a.get("key", "")
            self.saved_row(f, "anthropic", f"Clé {k[:7]}…{k[-4:]}")
        else:
            self.key_row(f, "anthropic", "CLÉ API CLAUDE (sk-ant-…)")

    @property
    def client(self):
        return conn.google_client([resource_path("google_client.json"), os.path.join(base_dir(), "google_client.json")])

    def saved_row_google(self, f, g):
        tk.Label(f, text=f"{g.get('name', '')} · {g.get('email', '')}", font=("Segoe UI", 9), bg=CARD, fg=INK, anchor="w").pack(anchor="w", pady=(0, 8))
        if "calendar.readonly" not in str(g.get("scopes", "")):
            tk.Label(f, text="Autorisation Google Agenda manquante (utile à AGOA DTG) : cliquez sur « Changer de compte » "
                             "et acceptez toutes les autorisations demandées.", font=("Segoe UI", 8), bg=CARD, fg=ORANGE_FG,
                     wraplength=int(500 * self.S), justify="left").pack(anchor="w", pady=(0, 8))
        row = tk.Frame(f, bg=CARD)
        row.pack(fill="x")
        Flat(row, "Changer de compte", self.google_in, False, padx=12, pady=6).pack(side="left")
        Flat(row, "Se déconnecter", lambda: self.remove("google"), False, padx=12, pady=6).pack(side="left", padx=(6, 0))

    # -- actions (réseau dans un thread, retour dans l'interface)
    def run(self, label, work, done):
        if self.working:
            return
        self.working = True
        self.say(label)

        def thread():
            try:
                res, err = work(), None
            except conn.ConnError as e:
                res, err = None, str(e)
            except Exception as e:
                try:
                    res, err = None, net_reason(e, "le service")
                except Exception:
                    res, err = None, "Échec de la vérification : " + repr(e)[:120]
            try:
                self.after(0, lambda: self.finish(res, err, done))
            except Exception:
                self.working = False
        threading.Thread(target=thread, daemon=True).start()

    def finish(self, res, err, done):
        self.working = False
        try:
            if err:
                self.say(err, False)
                return
            done(res)
            self.build()
            self.app.after(0, self.app.render)
        except tk.TclError:
            pass
        except Exception as e:
            self.say("Erreur : " + str(e)[:150], False)

    def save_key(self, kind):
        key = conn.clean_key(kind, self.vars[kind].get())
        if self.working:
            self.say("Une vérification est déjà en cours…")
            return
        if not key:
            self.say("Saisissez la clé avant de valider.", False)
            return
        if kind == "ragic":
            self.run("Vérification de la clé Ragic…", lambda: conn.check_ragic(key, http_call),
                     lambda host: (self.store.set("ragic", {"key": key, "host": host, "apname": conn.RAGIC_APNAME}),
                                   self.after_save("ragic", f"Ragic connecté (serveur {host}).")))
        else:
            self.run("Vérification de la clé Claude…", lambda: conn.check_anthropic(key, http_call),
                     lambda _: (self.store.set("anthropic", {"key": key}), self.after_save("anthropic", "Clé Claude enregistrée.")))

    def after_save(self, kind, text):
        self.vars[kind].set("")
        self.editing.discard(kind)
        self.say(text, True)

    def google_in(self):
        client = self.client
        if not client:
            self.say("Identifiants Google absents de cette version du launcher.", False)
            return
        self.cancel = False
        self.run("Connexion Google : terminez dans votre navigateur…",
                 lambda: conn.google_sign_in(client, http_call, webbrowser.open, cancel=lambda: self.cancel),
                 lambda g: (self.store.set("google", g), self.say(f"Google connecté : {g['email']}.", True)))

    def remove(self, kind):
        old = self.store.get(kind)
        self.store.remove(kind)
        self.editing.discard(kind)
        if kind == "google" and old:
            threading.Thread(target=conn.google_revoke, args=(old.get("refresh_token", ""), http_call), daemon=True).start()
        self.say("Connexion supprimée.", True)
        self.build()
        self.app.render()


class Splash(tk.Toplevel):
    """Écran de démarrage (même présentation que les autres applications AGOA)."""

    def __init__(self, master, S, version):
        super().__init__(master, bg="#FFFFFF")
        self.S = S
        self.overrideredirect(True)
        w, h = int(520 * S), int(340 * S)
        x, y = (self.winfo_screenwidth() - w) // 2, (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.attributes("-topmost", True)
        box = tk.Frame(self, bg="#FFFFFF", highlightthickness=1, highlightbackground="#DCDCDC")
        box.pack(fill="both", expand=True)
        mid = tk.Frame(box, bg="#FFFFFF")
        mid.place(relx=0.5, rely=0.42, anchor="center")
        px = int(112 * S)
        cv = tk.Canvas(mid, width=px, height=px, bg="#FFFFFF", highlightthickness=0)
        round_rect(cv, 0, 0, px, px, int(22 * S), fill=RED)
        cv.create_text(px // 2, px // 2, text="AGOA", fill="white", font=("Segoe UI", 26, "bold"))
        cv.pack()
        t = tk.Frame(mid, bg="#FFFFFF")
        t.pack(pady=(int(18 * S), 0))
        tk.Label(t, text="AGOA ", font=("Segoe UI", 20, "bold"), bg="#FFFFFF", fg="#1F2328").pack(side="left")
        tk.Label(t, text="LAUNCHER", font=("Segoe UI", 20, "bold"), bg="#FFFFFF", fg=RED).pack(side="left")
        tk.Label(mid, text="Installation et mise à jour des applications de l'agence", font=("Segoe UI", 10),
                 bg="#FFFFFF", fg="#666666").pack(pady=(6, 0))
        self.status = tk.Label(box, text="", font=("Segoe UI", 9), bg="#FFFFFF", fg="#777777")
        self.status.place(relx=0.5, rely=1.0, y=-int(28 * S), anchor="s")
        tk.Label(box, text=f"v{version}", font=("Segoe UI", 8), bg="#FFFFFF", fg="#9A9A9A").place(
            relx=1.0, rely=1.0, x=-12, y=-int(10 * S), anchor="se")
        self.track = tk.Canvas(box, height=3, bg="#FFFFFF", highlightthickness=0)
        self.track.place(x=0, rely=1.0, relwidth=1.0, anchor="sw")
        self.pct, self.pos = None, 0.0
        self.update_idletasks()
        self.tick()

    def set_status(self, text, pct=None):
        self.status.config(text=text or "")
        self.pct = pct

    def tick(self):
        try:
            self.track.delete("all")
            w = max(self.track.winfo_width(), 1)
            if self.pct is None:
                self.pos = (self.pos + 0.012) % 1.3
                x0 = int((self.pos - 0.3) * w)
                self.track.create_rectangle(max(x0, 0), 0, min(x0 + int(0.3 * w), w), 3, fill=RED, width=0)
            else:
                self.track.create_rectangle(0, 0, int(w * max(2, min(100, self.pct)) / 100), 3, fill=RED, width=0)
            self.after(16, self.tick)
        except tk.TclError:
            pass


class AgoaLauncher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} — Suite AGOA")
        self.S = max(1.0, self.winfo_fpixels("1i") / 96.0)  # échelle d'affichage (125 %, 150 %…)
        w = min(int(1320 * self.S), self.winfo_screenwidth() - 80)
        h = min(int(720 * self.S), self.winfo_screenheight() - 120)
        self.geometry(f"{w}x{h}")
        self.minsize(900, 560)
        self.configure(bg=BG)
        self.store = Store()
        self.catalog = {"apps": []}
        self.releases = {}      # id -> release | None (inconnu) | "..." (en cours)
        self.errors = {}        # id -> message
        self.launcher_rel = None
        self.filter = "all"
        self.query = tk.StringVar()
        self.query.trace_add("write", lambda *a: self.render())
        self.busy = {}
        self.bars = {}          # id -> ProgressBar
        self.cards = {}         # id -> (signature, frame) : on ne reconstruit que ce qui change
        self.checking = False
        self.banner_sig = None
        self.catalog_error = None
        try:
            self.iconbitmap(resource_path("icon.ico"))
        except Exception:
            pass
        self.build_chrome()
        self.after(100, self.animate)
        self.withdraw()                       # la fenêtre principale attend la fin du démarrage
        self.t0 = time.time()
        self.splash = Splash(self, self.S, LAUNCHER_VERSION)
        self.splash.set_status("Recherche de mises à jour…")
        threading.Thread(target=self.startup_work, daemon=True).start()

    # --- barre supérieure -------------------------------------------------
    def build_chrome(self):
        top = tk.Frame(self, bg=BG, height=int(44 * self.S))
        top.pack(fill="x")
        top.pack_propagate(False)
        logo = tk.Canvas(top, width=34, height=34, bg=BG, highlightthickness=0)
        logo.pack(side="left", padx=(14, 8), pady=5)
        logo.create_rectangle(0, 0, 34, 34, fill=RED, outline=RED)
        logo.create_text(17, 17, text="AG", fill="white", font=("Segoe UI", 12, "bold"))
        tk.Label(top, text="AGOA", font=("Segoe UI", 15, "bold"), bg=BG, fg=INK).pack(side="left")
        tk.Label(top, text="LAUNCHER", font=MONO_B, bg=INK, fg="white", padx=8, pady=3).pack(side="left", padx=(16, 6))
        tk.Label(top, text=f"SUITE D'APPLICATIONS AGENCE · {ROOT}", font=MONO, bg=BG, fg=MUTED).pack(side="left", padx=8)
        cx = tk.Label(top, text="CONNEXIONS", font=MONO_B, bg=BG, fg=INK, cursor="hand2", padx=8, pady=3,
                      highlightthickness=1, highlightbackground=INK)
        cx.pack(side="right", padx=(0, 6))
        cx.bind("<Button-1>", lambda e: self.open_connections())
        gear = tk.Label(top, text="⚙", font=("Segoe UI", 14), bg=BG, fg=INK, cursor="hand2")
        gear.pack(side="right", padx=10)
        gear.bind("<Button-1>", self.settings_menu)
        rf = tk.Label(top, text="⟳", font=("Segoe UI", 14), bg=BG, fg=INK, cursor="hand2")
        rf.pack(side="right", padx=4)
        rf.bind("<Button-1>", lambda e: self.refresh())
        tk.Frame(self, bg=LINE, height=1).pack(fill="x")

        self.banner_host = tk.Frame(self, bg=BG)
        self.banner_host.pack(fill="x", padx=10)

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=10, pady=(12, 4))
        self.tabs = {}
        for key in ("all", "ok", "maj", "absent"):
            lab = tk.Label(bar, font=MONO, padx=10, pady=6, cursor="hand2")
            lab.pack(side="left")
            lab.bind("<Button-1>", lambda e, k=key: self.set_filter(k))
            self.tabs[key] = lab
        tk.Entry(bar, textvariable=self.query, font=("Segoe UI", 10), width=28, relief="solid", bd=1,
                 bg="#F7F6F2").pack(side="right", ipady=4)
        tk.Label(bar, text="RECHERCHE", font=MONO, bg=BG, fg=MUTED).pack(side="right", padx=8)

        wrap = tk.Frame(self, bg=BG)
        wrap.pack(fill="both", expand=True, padx=10)
        self.canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.grid_frame = tk.Frame(self.canvas, bg=BG)
        self.win = self.canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        self.grid_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self.on_resize)
        self.canvas.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))

        self.foot = tk.Label(self, text="", font=("Segoe UI", 8), fg=MUTED, bg=BG, anchor="w")
        self.foot.pack(fill="x", padx=10, pady=6)

    def on_resize(self, e):
        self.canvas.itemconfig(self.win, width=e.width)
        self.render()

    # --- données ---------------------------------------------------------
    def startup_work(self):
        """Thread de démarrage : version du lanceur, puis catalogue."""
        try:
            self.launcher_rel = gh_latest_release(LAUNCHER_REPO, self.store.token)
        except Exception:
            self.launcher_rel = None
        self.after(0, self.splash.set_status, "Chargement des applications…")
        cat, cerr = self.store.load_catalog()
        self.catalog, self.catalog_error = cat, cerr
        self.after(0, self.startup_decide)

    def startup_decide(self):
        lr = self.launcher_rel
        if getattr(sys, "frozen", False) and lr:
            if vtuple(lr["version"]) > vtuple(LAUNCHER_VERSION):
                return self.run_launcher_update(lr)        # mise à jour automatique, comme les autres applications
            has_setup = any(fnmatch.fnmatch(a["name"].lower(), SETUP_PATTERN.lower()) for a in lr["assets"])
            if has_setup and not is_installed_launcher() and not self.store.settings.get("no_install_prompt"):
                self.splash.withdraw()
                ok = messagebox.askyesno(APP_NAME, "Installer AGOA Launcher sur ce poste ?\n\n"
                                         f"Il sera placé dans {INSTALL_DIR} avec un raccourci sur le bureau et dans le menu "
                                         "Démarrer, et se mettra à jour automatiquement.\n"
                                         "Vos applications AGOA déjà installées sont conservées.")
                self.splash.deiconify()
                if ok:
                    return self.run_launcher_update(lr)
                self.store.settings["no_install_prompt"] = True
                save_json(SETTINGS_FILE, self.store.settings)
        self.finish_startup()

    def run_launcher_update(self, lr):
        v = lr["version"]
        self.splash.set_status(f"Téléchargement de la version {v}…", 0)

        def prog(p):
            self.after(0, self.splash.set_status, f"Téléchargement de la version {v}… {int(p * 100)} %", p * 100)

        def work():
            try:
                self.store.update_launcher(lr, progress=prog)
                self.after(0, self.splash.set_status, "Installation en cours — le lanceur va redémarrer…", None)
                self.after(1500, self.destroy)
            except Exception as e:
                self.after(0, self.update_failed, e)
        threading.Thread(target=work, daemon=True).start()

    def update_failed(self, err):
        self.splash.withdraw()
        messagebox.showwarning(APP_NAME, f"La mise à jour du lanceur a échoué :\n\n{err}\n\n"
                                         "Le lanceur démarre avec la version actuelle.")
        self.splash.deiconify()
        self.finish_startup()

    def finish_startup(self):
        wait = max(0, int((0.9 - (time.time() - self.t0)) * 1000))   # splash visible au moins ~1 s
        self.splash.set_status("Prêt", 100)
        self.after(wait, self.show_main)

    def show_main(self):
        try:
            self.splash.destroy()
        except Exception:
            pass
        self.deiconify()
        self.lift()
        self.render()
        threading.Thread(target=self.fetch_releases, daemon=True).start()

    def fetch_releases(self):
        tok = self.store.token
        for a in self.catalog.get("apps", []):
            try:
                self.releases[a["id"]] = gh_latest_release(a["repo"], tok)
                self.errors.pop(a["id"], None)
            except Exception as e:
                self.releases[a["id"]] = None
                self.errors[a["id"]] = str(e) if isinstance(e, RuntimeError) else net_reason(e)
            self.after(0, self.render)
        self.checking = False
        self.after(0, self.render)

    def refresh(self):
        """Bouton ⟳ : revérifie le lanceur, le catalogue et les programmes."""
        if self.checking:
            return
        self.checking = True            # les cartes gardent leur contenu pendant la vérification (pas de clignotement)
        self.render()

        def work():
            try:
                self.launcher_rel = gh_latest_release(LAUNCHER_REPO, self.store.token)
            except Exception:
                pass
            self.catalog, self.catalog_error = self.store.load_catalog()
            self.fetch_releases()
        threading.Thread(target=work, daemon=True).start()

    def set_catalog(self, cat):
        self.catalog = cat
        self.render()

    def set_filter(self, k):
        self.filter = k
        self.render()

    def st(self, a):
        if a["id"] not in self.releases:
            return "verif"
        return self.store.status(a, self.releases[a["id"]])

    def counts(self):
        apps = self.catalog.get("apps", [])
        st = [self.st(a) for a in apps]
        return {"all": len(apps), "ok": st.count("ok") + st.count("maj"), "maj": st.count("maj"), "absent": st.count("absent")}

    # --- rendu -----------------------------------------------------------
    def render(self):
        if not hasattr(self, "grid_frame"):
            return
        c = self.counts()
        names = {"all": "TOUTES", "ok": "INSTALLÉES", "maj": "MISES À JOUR", "absent": "NON INSTALLÉES"}
        for k, lab in self.tabs.items():
            on = k == self.filter
            lab.config(text=f"{names[k]} {c[k]}", bg=INK if on else BG, fg="white" if on else MUTED)

        lr = self.launcher_rel
        banners = []
        if lr and vtuple(lr["version"]) > vtuple(LAUNCHER_VERSION):
            banners.append((f"AGOA Launcher {lr['version']} est disponible.", "Mettre à jour et redémarrer", self.do_update_launcher))
        if self.catalog_error:
            banners.append((self.catalog_error + " — liste affichée hors ligne.", "Réessayer", self.refresh))
        if c["maj"]:
            banners.append((f"{c['maj']} programme(s) AGOA ont une nouvelle version.", "Tout mettre à jour", self.update_all))
        bsig = tuple((t, ac) for t, ac, _ in banners)
        if bsig != self.banner_sig:                   # bandeaux reconstruits seulement s'ils changent
            self.banner_sig = bsig
            for w in self.banner_host.winfo_children():
                w.destroy()
            for t, ac, cmd in banners:
                self.banner(t, ac, cmd)

        q = self.query.get().strip().lower()
        apps = []
        for a in self.catalog.get("apps", []):
            s = self.st(a)
            if self.filter == "ok" and s not in ("ok", "maj"):
                continue
            if self.filter == "maj" and s != "maj":
                continue
            if self.filter == "absent" and s != "absent":
                continue
            if q and q not in (a["nom"] + a["description"] + a["code"]).lower():
                continue
            apps.append((a, s))

        if not hasattr(self, "grid_title"):
            self.grid_title = tk.Label(self.grid_frame, font=MONO, bg=BG, fg=MUTED)
            self.grid_title.grid(row=0, column=0, sticky="w", pady=(10, 8), columnspan=6)
        self.grid_title.config(text="01 / PROGRAMMES AGOA" + ("   ·   ACTUALISATION…" if self.checking else ""))
        width = max(self.canvas.winfo_width(), 900)
        cols = max(1, width // int(330 * self.S))
        for i in range(cols):
            self.grid_frame.columnconfigure(i, weight=1, uniform="c")

        wanted = {a["id"] for a, _ in apps}
        for k in list(self.cards):                     # cartes qui ne sont plus affichées
            if k not in wanted:
                self.cards.pop(k)[1].destroy()
                self.bars.pop(k, None)
        for idx, (a, s) in enumerate(apps):
            rel = self.releases.get(a["id"])
            sig = (idx, s, rel["version"] if rel else None, rel["date"] if rel else None, self.errors.get(a["id"]),
                   self.store.installed_version(a["id"], a), a["id"] in self.busy)
            old = self.cards.get(a["id"])
            if old is None or old[0] != sig:
                if old:
                    old[1].destroy()
                    self.bars.pop(a["id"], None)
                self.cards[a["id"]] = (sig, self.card(a, s, idx))
            f = self.cards[a["id"]][1]
            pos = dict(row=1 + idx // cols, column=idx % cols)
            gi = f.grid_info()
            if not gi or int(gi.get("row", -1)) != pos["row"] or int(gi.get("column", -1)) != pos["column"]:
                f.grid(sticky="nsew", **pos)
        self.foot.config(text=f"Versions lues sur GitHub (dernière release de chaque dépôt) · installation dans {ROOT} · "
                              f"Launcher v{LAUNCHER_VERSION}")

    def banner(self, text, action, cmd):
        b = tk.Frame(self.banner_host, bg=BLUE_BG, highlightbackground=BLUE_FG, highlightthickness=1)
        b.pack(fill="x", pady=(10, 0))
        tk.Label(b, text="MISE À JOUR", font=MONO_B, bg=BLUE_FG, fg="white", padx=8, pady=3).pack(side="left", padx=10, pady=8)
        tk.Label(b, text=text, bg=BLUE_BG, fg=INK, font=("Segoe UI", 10)).pack(side="left")
        Flat(b, action, cmd, padx=14, pady=6).pack(side="right", padx=10)

    def icon(self, parent, code):
        px = int(60 * self.S)
        cv = tk.Canvas(parent, width=px, height=px, bg=CARD, highlightthickness=0)
        cv.create_rectangle(0, 0, px, px, fill=RED, outline=RED)
        size = 20 if len(code) <= 2 else 15 if len(code) == 3 else 12
        cv.create_text(px // 2, px // 2, text=code, fill="white", font=("Segoe UI", size, "bold"))
        return cv

    def badge(self, parent, text, bg, fg):
        return tk.Label(parent, text=text.upper(), font=MONO, bg=bg, fg=fg, padx=5, pady=2,
                        highlightthickness=1, highlightbackground=fg)

    def card(self, a, s, idx):
        rel = self.releases.get(a["id"])
        f = tk.Frame(self.grid_frame, bg=CARD, highlightbackground=LINE, highlightthickness=1, padx=18, pady=14)
        head = tk.Frame(f, bg=CARD)
        head.pack(fill="x")
        tk.Label(head, text=f"{idx + 1:02d}", font=MONO_B, bg=RED, fg="white", padx=6, pady=2).pack(side="left")
        tk.Label(head, text=f"GITHUB ↗", font=MONO, bg=CARD, fg=MUTED, cursor="hand2").pack(side="right")
        head.winfo_children()[-1].bind("<Button-1>", lambda e: os.startfile(f"https://github.com/{a['repo']}") if hasattr(os, "startfile") else None)

        mid = tk.Frame(f, bg=CARD)
        mid.pack(fill="x", pady=(14, 10))
        self.icon(mid, a["code"]).pack(side="left")
        txt = tk.Frame(mid, bg=CARD)
        txt.pack(side="left", padx=12, fill="x", expand=True)
        title = tk.Frame(txt, bg=CARD)
        title.pack(anchor="w")
        tk.Label(title, text="AGOA ", font=("Segoe UI", 15, "bold"), bg=CARD, fg=INK).pack(side="left")
        tk.Label(title, text=a["code"], font=("Segoe UI", 15, "bold"), bg=CARD, fg=RED).pack(side="left")
        tk.Label(txt, text=a["description"], font=("Segoe UI", 9), bg=CARD, fg="#444", wraplength=int(210 * self.S),
                 justify="left", anchor="w").pack(anchor="w", pady=(2, 0))

        iv = self.store.installed_version(a["id"], a)
        if s == "verif":
            self.badge(f, "Vérification…", GREY_BG, GREY_FG).pack(anchor="w")
        elif s == "inconnu":
            self.badge(f, "Version indisponible", GREY_BG, GREY_FG).pack(anchor="w")
        elif s == "absent":
            self.badge(f, "Disponible à l'installation", BLUE_BG, BLUE_FG).pack(anchor="w")
        elif s == "maj":
            self.badge(f, f"Installé v{iv} · mise à jour", ORANGE_BG, ORANGE_FG).pack(anchor="w")
        else:
            self.badge(f, "Installé · à jour", GREEN_BG, GREEN_FG).pack(anchor="w")

        if rel:
            info = f"Version {rel['version']} · {rel['date']}"
        elif a["id"] in self.errors:
            info = self.errors[a["id"]]
        else:
            info = " "
        tk.Label(f, text=info, font=("Segoe UI", 9), bg=CARD, fg=INK, wraplength=int(260 * self.S), justify="left",
                 anchor="w").pack(anchor="w", pady=(10, 12), fill="x")

        row = tk.Frame(f, bg=CARD)
        row.pack(fill="x")
        if a["id"] in self.busy:
            pb = ProgressBar(row, self.S)
            pb.pack(fill="x")
            pb.set(self.busy[a["id"]], "Téléchargement")
            self.bars[a["id"]] = pb
        else:
            if s == "absent":
                Flat(row, "Installer", lambda: self.do_install(a), True, pady=8).pack(side="left", fill="x", expand=True)
            elif s == "maj":
                Flat(row, "Mettre à jour", lambda: self.do_install(a), True, pady=8).pack(side="left", fill="x", expand=True)
                Flat(row, "Ouvrir", lambda: self.do_open(a), False, padx=10, pady=8).pack(side="left", padx=(6, 0))
            elif s == "ok":
                Flat(row, "Ouvrir", lambda: self.do_open(a), False, pady=8).pack(side="left", fill="x", expand=True)
            else:
                Flat(row, "Indisponible", lambda: None, False, pady=8).pack(side="left", fill="x", expand=True)
            more = Flat(row, "...", lambda: None, False, padx=10, pady=8)
            more.pack(side="left", padx=(6, 0))
            more.bind("<Button-1>", lambda e, ap=a: self.menu(e, ap))
        return f

    def menu(self, event, a):
        m = tk.Menu(self, tearoff=0)
        if self.store.is_installed(a):
            m.add_command(label="Ouvrir le dossier d'installation", command=lambda: os.startfile(self.store.app_dir(a["id"])))
            if self.releases.get(a["id"]):
                m.add_command(label="Réinstaller", command=lambda: self.do_install(a))
            m.add_command(label="Désinstaller", command=lambda: self.do_uninstall(a))
        elif self.releases.get(a["id"]):
            m.add_command(label="Installer", command=lambda: self.do_install(a))
        m.tk_popup(event.x_root, event.y_root)

    def settings_menu(self, event):
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="Connexions (Ragic, Google, Claude)…", command=self.open_connections)
        m.add_command(label="Ouvrir le dossier C:\\AGOA", command=lambda: os.startfile(ROOT))
        m.add_command(label="Jeton GitHub (dépôts privés)…", command=self.ask_token)
        m.add_command(label="Actualiser", command=self.refresh)
        m.tk_popup(event.x_root, event.y_root)

    def open_connections(self):
        w = getattr(self, "conn_win", None)
        try:
            if w and w.winfo_exists():
                w.lift()
                return
        except tk.TclError:
            pass
        self.conn_win = ConnectionsWindow(self)

    def ask_token(self):
        t = simpledialog.askstring(APP_NAME, "Jeton d'accès GitHub (lecture des releases).\n"
                                   "Nécessaire seulement si les dépôts sont privés. Vide = aucun.",
                                   show="*", parent=self)
        if t is not None:
            self.store.settings["github_token"] = t.strip()
            save_json(SETTINGS_FILE, self.store.settings)
            self.refresh()

    # --- actions ---------------------------------------------------------
    def do_install(self, a):
        rel = self.releases.get(a["id"])
        if a["id"] in self.busy or not rel:
            return
        self.busy[a["id"]] = 0.0
        self.render()

        def work():
            try:
                self.store.install(a, rel, progress=lambda p: self.after(0, self.set_progress, a["id"], p))
                err = None
            except Exception as e:
                err = e
            self.after(0, self.done, a, err)
        threading.Thread(target=work, daemon=True).start()

    def set_progress(self, app_id, p):
        self.busy[app_id] = p
        pb = self.bars.get(app_id)
        if pb:
            if p >= 0.95:
                pb.set(None, "Installation")       # téléchargement fini : l'installeur travaille, animation continue
            else:
                pb.set(p, "Téléchargement")

    def animate(self):
        for pb in list(self.bars.values()):
            try:
                pb.step()
            except tk.TclError:
                pass
        self.after(33, self.animate)

    def done(self, a, err):
        self.busy.pop(a["id"], None)
        if err:
            messagebox.showerror(APP_NAME, f"Échec de l'installation de {a['nom']} :\n\n{err}")
        self.render()

    def update_all(self):
        for a in self.catalog.get("apps", []):
            if self.st(a) == "maj":
                self.do_install(a)

    def do_update_launcher(self):
        def work():
            try:
                self.store.update_launcher(self.launcher_rel)
                self.after(0, self.destroy)
            except Exception as e:
                self.after(0, lambda: messagebox.showerror(APP_NAME, str(e)))
        threading.Thread(target=work, daemon=True).start()

    def do_open(self, a):
        try:
            self.store.launch(a)
        except Exception as e:
            messagebox.showerror(APP_NAME, str(e))

    def do_uninstall(self, a):
        if messagebox.askyesno(APP_NAME, f"Désinstaller {a['nom']} ?"):
            self.store.uninstall(a)
            self.render()


def enable_hidpi():
    """Rend le processus « DPI aware » : sinon Windows étire la fenêtre en bitmap (texte flou)."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


if __name__ == "__main__":
    enable_hidpi()
    AgoaLauncher().mainloop()
