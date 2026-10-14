"""AGOA Launcher — installation, mise à jour et lancement des programmes AGOA.

Windows, Python 3.9+ (tkinter uniquement, aucune dépendance).
Les versions sont lues sur GitHub (dernière « Release » de chaque dépôt).
Tout est installé sous C:\\AGOA (une sous-dossier par programme).
"""
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import urllib.parse
import zipfile
import ctypes
import glob
from ctypes import wintypes
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

APP_NAME = "AGOA Launcher"
LAUNCHER_VERSION = "0.3.0"
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

# --- Charte AGOA : rouge agence, carré + code en gras --------------------
RED = "#C8102E"
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


_opener = urllib.request.build_opener(_NoAuthOnRedirect)


def gh_open(url, token=None, accept="application/vnd.github+json", timeout=20):
    headers = {"User-Agent": "AGOA-Launcher", "Accept": accept}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return _opener.open(urllib.request.Request(url, headers=headers), timeout=timeout)


def gh_latest_release(repo, token=None):
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
        self.state = load_json(STATE_FILE, {})

    @property
    def token(self):
        return self.settings.get("github_token") or os.environ.get("GITHUB_TOKEN")

    def catalog_local(self):
        return load_json(os.path.join(base_dir(), "catalog.json"), {"apps": []})

    def load_catalog(self):
        """Catalogue : celui du dépôt du lanceur (à jour) sinon cache sinon fichier local."""
        local = self.catalog_local()
        repo = (local.get("launcher") or {}).get("repo")
        if repo:
            try:
                url = f"https://raw.githubusercontent.com/{repo}/main/catalog.json"
                with gh_open(url, self.token, accept="*/*", timeout=10) as r:
                    cat = json.loads(r.read().decode("utf-8"))
                save_json(CATALOG_CACHE, cat)
                return cat
            except Exception:
                cached = load_json(CATALOG_CACHE, None)
                if cached:
                    return cached
        return local

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
        if hasattr(os, "startfile"):
            os.startfile(path)
        else:
            subprocess.Popen([path])

    def update_launcher(self, rel, pattern, progress=None):
        """Télécharge le nouvel exe puis le remplace après fermeture du lanceur."""
        if not getattr(sys, "frozen", False):
            raise RuntimeError("Mise à jour automatique disponible uniquement dans AGOA-Launcher.exe")
        asset = pick_asset(rel, pattern or "*.exe")
        exe = sys.executable
        new = exe + ".new"
        gh_download(asset, new, self.token, progress)
        bat = os.path.join(CONFIG_DIR, "update.bat")
        with open(bat, "w", encoding="utf-8") as f:
            f.write(f'@echo off\r\ntimeout /t 2 /nobreak >nul\r\nmove /Y "{new}" "{exe}"\r\n'
                    f'start "" "{exe}"\r\ndel "%~f0"\r\n')
        subprocess.Popen(["cmd", "/c", bat], creationflags=0x08000000)


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


class AgoaLauncher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} — Suite AGOA")
        self.geometry("1320x720")
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
        self.asked_update = False
        self.build_chrome()
        self.refresh()

    # --- barre supérieure -------------------------------------------------
    def build_chrome(self):
        top = tk.Frame(self, bg=BG, height=44)
        top.pack(fill="x")
        top.pack_propagate(False)
        logo = tk.Canvas(top, width=34, height=34, bg=BG, highlightthickness=0)
        logo.pack(side="left", padx=(14, 8), pady=5)
        logo.create_rectangle(0, 0, 34, 34, fill=RED, outline=RED)
        logo.create_text(17, 17, text="AG", fill="white", font=("Segoe UI", 12, "bold"))
        tk.Label(top, text="AGOA", font=("Segoe UI", 15, "bold"), bg=BG, fg=INK).pack(side="left")
        tk.Label(top, text="LAUNCHER", font=MONO_B, bg=INK, fg="white", padx=8, pady=3).pack(side="left", padx=(16, 6))
        tk.Label(top, text=f"SUITE D'APPLICATIONS AGENCE · {ROOT}", font=MONO, bg=BG, fg=MUTED).pack(side="left", padx=8)
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
    def refresh(self):
        self.releases = {}
        self.errors = {}
        self.launcher_rel = None

        def work():
            tok = self.store.token
            # 1) le lanceur se vérifie lui-même en premier
            try:
                self.launcher_rel = gh_latest_release(LAUNCHER_REPO, tok)
                if vtuple(self.launcher_rel["version"]) > vtuple(LAUNCHER_VERSION) and not self.asked_update:
                    self.asked_update = True
                    self.after(0, self.prompt_launcher_update)
            except Exception:
                pass
            # 2) puis le catalogue et les programmes
            cat = self.store.load_catalog()
            self.after(0, self.set_catalog, cat)
            for a in cat.get("apps", []):
                try:
                    self.releases[a["id"]] = gh_latest_release(a["repo"], tok)
                except Exception as e:
                    self.releases[a["id"]] = None
                    self.errors[a["id"]] = str(e) if isinstance(e, RuntimeError) else "GitHub injoignable"
                self.after(0, self.render)
            self.after(0, self.render)
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

        for w in self.banner_host.winfo_children():
            w.destroy()
        lr = self.launcher_rel
        if lr and vtuple(lr["version"]) > vtuple(LAUNCHER_VERSION):
            self.banner(f"AGOA Launcher {lr['version']} est disponible.", "Mettre à jour et redémarrer", self.do_update_launcher)
        if c["maj"]:
            self.banner(f"{c['maj']} programme(s) AGOA ont une nouvelle version.", "Tout mettre à jour", self.update_all)

        for w in self.grid_frame.winfo_children():
            w.destroy()
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

        tk.Label(self.grid_frame, text="01 / PROGRAMMES AGOA", font=MONO, bg=BG, fg=MUTED).grid(
            row=0, column=0, sticky="w", pady=(10, 8), columnspan=6)
        width = max(self.canvas.winfo_width(), 900)
        cols = max(1, width // 330)
        for i in range(cols):
            self.grid_frame.columnconfigure(i, weight=1, uniform="c")
        for idx, (a, s) in enumerate(apps):
            self.card(a, s, idx).grid(row=1 + idx // cols, column=idx % cols, sticky="nsew")
        self.foot.config(text=f"Versions lues sur GitHub (dernière release de chaque dépôt) · installation dans {ROOT} · "
                              f"Launcher v{LAUNCHER_VERSION}")

    def banner(self, text, action, cmd):
        b = tk.Frame(self.banner_host, bg=BLUE_BG, highlightbackground=BLUE_FG, highlightthickness=1)
        b.pack(fill="x", pady=(10, 0))
        tk.Label(b, text="MISE À JOUR", font=MONO_B, bg=BLUE_FG, fg="white", padx=8, pady=3).pack(side="left", padx=10, pady=8)
        tk.Label(b, text=text, bg=BLUE_BG, fg=INK, font=("Segoe UI", 10)).pack(side="left")
        Flat(b, action, cmd, padx=14, pady=6).pack(side="right", padx=10)

    def icon(self, parent, code):
        cv = tk.Canvas(parent, width=60, height=60, bg=CARD, highlightthickness=0)
        cv.create_rectangle(0, 0, 60, 60, fill=RED, outline=RED)
        size = 20 if len(code) <= 2 else 15 if len(code) == 3 else 12
        cv.create_text(30, 30, text=code, fill="white", font=("Segoe UI", size, "bold"))
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
        tk.Label(txt, text=a["description"], font=("Segoe UI", 9), bg=CARD, fg="#444", wraplength=210,
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
        tk.Label(f, text=info, font=("Segoe UI", 9), bg=CARD, fg=INK, wraplength=260, justify="left",
                 anchor="w").pack(anchor="w", pady=(10, 12), fill="x")

        row = tk.Frame(f, bg=CARD)
        row.pack(fill="x")
        if a["id"] in self.busy:
            ttk.Progressbar(row, maximum=1.0, value=self.busy[a["id"]]).pack(fill="x", ipady=6)
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
        m.add_command(label="Ouvrir le dossier C:\\AGOA", command=lambda: os.startfile(ROOT))
        m.add_command(label="Jeton GitHub (dépôts privés)…", command=self.ask_token)
        m.add_command(label="Actualiser", command=self.refresh)
        m.tk_popup(event.x_root, event.y_root)

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

    def done(self, a, err):
        self.busy.pop(a["id"], None)
        if err:
            messagebox.showerror(APP_NAME, f"Échec de l'installation de {a['nom']} :\n\n{err}")
        self.render()

    def update_all(self):
        for a in self.catalog.get("apps", []):
            if self.st(a) == "maj":
                self.do_install(a)

    def prompt_launcher_update(self):
        v = self.launcher_rel["version"]
        if not getattr(sys, "frozen", False):
            return
        if messagebox.askyesno(APP_NAME, f"AGOA Launcher {v} est disponible (vous avez {LAUNCHER_VERSION}).\n\n"
                                         "Mettre à jour maintenant ? Le lanceur redémarrera."):
            self.do_update_launcher()

    def do_update_launcher(self):
        pattern = (self.catalog.get("launcher") or {}).get("asset", "AGOA-Launcher.exe")

        def work():
            try:
                self.store.update_launcher(self.launcher_rel, pattern)
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


if __name__ == "__main__":
    AgoaLauncher().mainloop()
