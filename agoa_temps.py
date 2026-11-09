"""Suivi du temps de travail par opération (launcher AGOA).

Sources : DTG et CCTP écrivent chacune un journal dans <ROOT>\\_temps\\journal\\<source>.jsonl ; le launcher surveille
Vectorworks (fenêtre au premier plan + inactivité) et écrit le sien. Chaque vendredi à 18 h, le launcher regroupe les
temps par opération (noms proches rapprochés), écrit une synthèse par poste et l'envoie par mail.

Ligne de journal : {"ts": "2026-11-07T11:35:00", "src": "dtg", "ref": "BELLEVILLE112-A", "sec": 60, "file": "…"}
  ts = fin du créneau (heure locale du poste), sec = secondes de travail réellement actif.
"""
import csv
import datetime as dt
import difflib
import io
import json
import os
import re
import threading
import unicodedata

SOURCES = {"dtg": "DTG", "cctp": "CCTP", "vectorworks": "Vectorworks", "multidoc": "Multidoc", "chrome": "Chrome"}
IDLE_LIMIT = 300            # secondes sans clavier/souris : le temps ne compte plus
SIMILAR = 0.85              # seuil de ressemblance entre deux noms d'opération
NO_REF = "(sans référence)"


# --- journal -------------------------------------------------------------------------------------------------------
def journal_dir(temps_dir):
    return os.path.join(temps_dir, "journal")


def append_entry(temps_dir, src, ref, sec, ts=None, file=None):
    """Ajoute un créneau au journal de la source (une ligne JSON, écriture atomique)."""
    if sec <= 0:
        return
    d = journal_dir(temps_dir)
    os.makedirs(d, exist_ok=True)
    e = {"ts": (ts or dt.datetime.now()).replace(microsecond=0).isoformat(), "src": src, "ref": ref, "sec": int(round(sec))}
    if file:
        e["file"] = file
    with open(os.path.join(d, src + ".jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")


def read_entries(temps_dir, start, end):
    """Créneaux dont la fin est dans [start, end[ (tous les fichiers du journal)."""
    d = journal_dir(temps_dir)
    out = []
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith(".jsonl"))
    except OSError:
        return out
    for n in names:
        try:
            with open(os.path.join(d, n), encoding="utf-8") as f:
                for line in f:
                    try:
                        e = json.loads(line)
                        t = dt.datetime.fromisoformat(e["ts"])
                        e["sec"] = float(e["sec"])
                    except (ValueError, KeyError, TypeError):
                        continue
                    if start <= t < end and e["sec"] > 0 and e.get("src") in SOURCES:
                        e["_t"] = t
                        out.append(e)
        except OSError:
            continue
    return out


# --- noms d'opération --------------------------------------------------------------------------------------------
def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]+", "-", s).strip("-")


def split_ref(n):
    """'PIGALLE67-71-A' -> ('PIGALLE', '67-71', 'A') : lettres du nom, chiffres, indice (lettre finale seule)."""
    m = re.match(r"^([A-Z]*?)([0-9][0-9-]*[0-9]|[0-9])?(?:-([A-Z]{1,2}))?$", n)
    if not m:
        return n, "", ""
    return m.group(1), m.group(2) or "", m.group(3) or ""


def same_ref(a, b):
    """Deux noms désignent la même opération : mêmes chiffres, même indice (A, B… = dossiers différents), lettres proches."""
    a, b = norm(a), norm(b)
    if a == b:
        return True
    la, da, ia = split_ref(a)
    lb, db, ib = split_ref(b)
    if da != db or ia != ib or not da:
        return False
    return difflib.SequenceMatcher(None, la, lb).ratio() >= SIMILAR


def vw_file_from_title(title):
    """Nom du fichier .vwx dans le titre d'une fenêtre Vectorworks, ou None."""
    for part in re.split(r"\s[-–—]\s", title or ""):
        m = re.search(r"([^\\/:*?\"<>|\[\]]+?\.vwx\w*)", part, re.I)
        if m:
            return m.group(1).strip()
    return None


_CODE = re.compile(r"[A-Za-zÀ-ÿ]{3,}[0-9]+(?:-[0-9]+)*(?:-[A-Za-z])?(?![A-Za-z0-9])")


def ref_from_filename(name, known=()):
    """Référence d'opération contenue dans un nom de fichier : code connu (Ragic) le plus long, sinon motif NOM12-A."""
    stem = re.sub(r"\.vwx\w*$", "", name or "", flags=re.I)
    n = norm(stem)
    best = None
    for k in known:
        kn = norm(k)
        if kn and re.search(r"(^|-)" + re.escape(kn) + r"(-|$)", n) and (best is None or len(kn) > len(norm(best))):
            best = k
    if best:
        return best
    m = _CODE.search(stem)
    if m:
        cand = m.group(0)
        for k in known:                                   # faute de frappe : on rapproche d'un code connu
            if same_ref(cand, k):
                return k
        return cand.upper()
    return None


def canonical_names(raws, known):
    """{nom brut: nom retenu}. Codes connus d'abord ; sinon les noms proches sont regroupés (le plus long / le plus fréquent gagne)."""
    out, groups = {}, []
    for r in raws:
        hit = next((k for k in known if same_ref(r, k)), None)
        if hit:
            out[r] = hit
            continue
        g = next((g for g in groups if any(same_ref(r, x) for x in g)), None)
        if g:
            g.append(r)
        else:
            groups.append([r])
    for g in groups:
        keep = max(g, key=lambda x: (g.count(x), len(x)))
        for x in g:
            out[x] = norm(keep) or keep
    return out


# --- synthèse ----------------------------------------------------------------------------------------------------
def build_summary(entries, known, start, end, pc, user):
    raws = {}
    for e in entries:
        ref = e.get("ref") or ""
        if e["src"] == "vectorworks":
            ref = ref_from_filename(e.get("file") or ref, known) or ""
        raws[id(e)] = ref.strip()
    canon = canonical_names([r for r in raws.values() if r], known)
    ops = {}
    for e in entries:
        r = raws[id(e)]
        key = canon.get(r, NO_REF) if r else NO_REF
        o = ops.setdefault(key, {"ref": key, "total_sec": 0, "by_source": {}, "aliases": set(), "files": set()})
        o["total_sec"] += e["sec"]
        o["by_source"][e["src"]] = o["by_source"].get(e["src"], 0) + e["sec"]
        if r and r != key:
            o["aliases"].add(r)
        if e["src"] == "vectorworks" and e.get("file"):
            o["files"].add(e["file"])
    kn = {norm(k) for k in known}
    rows = []
    for o in sorted(ops.values(), key=lambda o: -o["total_sec"]):
        rows.append({"ref": o["ref"], "total_sec": int(round(o["total_sec"])),
                     "by_source": {k: int(round(v)) for k, v in sorted(o["by_source"].items())},
                     "aliases": sorted(o["aliases"]), "files": sorted(o["files"]),
                     "known": norm(o["ref"]) in kn})
    return {"pc": pc, "user": user, "start": start.isoformat(timespec="minutes"), "end": end.isoformat(timespec="minutes"),
            "generated": dt.datetime.now().isoformat(timespec="seconds"),
            "total_sec": sum(r["total_sec"] for r in rows), "operations": rows}


def fmt_dur(sec):
    m = int(round(sec / 60))
    return f"{m // 60} h {m % 60:02d}"


def week_label(end):
    y, w, _ = (end - dt.timedelta(days=1)).isocalendar()
    return f"{y}-S{w:02d}"


def render_text(s):
    lines = [f"Temps de travail — poste {s['pc']} ({s['user']})",
             f"Période : du {s['start'].replace('T', ' ')} au {s['end'].replace('T', ' ')}", "",
             f"Total : {fmt_dur(s['total_sec'])}", ""]
    if not s["operations"]:
        lines.append("Aucune activité enregistrée sur la période.")
    for o in s["operations"]:
        det = ", ".join(f"{SOURCES.get(k, k)} {fmt_dur(v)}" for k, v in o["by_source"].items())
        lines.append(f"• {o['ref']} — {fmt_dur(o['total_sec'])}  ({det})" + ("" if o["known"] or o["ref"] == NO_REF else "  [absent de Ragic]"))
        if o["aliases"]:
            lines.append("    noms rapprochés : " + ", ".join(o["aliases"]))
    return "\n".join(lines)


def render_csv(s):
    b = io.StringIO()
    w = csv.writer(b, delimiter=";")
    w.writerow(["PC", "Utilisateur", "Début", "Fin", "Opération", "Source", "Secondes", "Heures"])
    for o in s["operations"]:
        for k, v in o["by_source"].items():
            w.writerow([s["pc"], s["user"], s["start"], s["end"], o["ref"], SOURCES.get(k, k), v, f"{v / 3600:.2f}".replace(".", ",")])
    return "﻿" + b.getvalue()


# --- calendrier : vendredi 18 h ------------------------------------------------------------------------------------
def last_cutoff(now, weekday=4, hour=18):
    """Dernier vendredi 18 h (≤ now)."""
    c = now.replace(hour=hour, minute=0, second=0, microsecond=0) - dt.timedelta(days=(now.weekday() - weekday) % 7)
    return c if c <= now else c - dt.timedelta(days=7)


def due_periods(last_sent, now, max_weeks=4):
    """[(début, fin)] des synthèses à produire : chaque vendredi 18 h écoulé depuis last_sent (datetime de la dernière coupure envoyée)."""
    latest = last_cutoff(now)
    if last_sent is None:
        return []
    out, c = [], last_sent + dt.timedelta(days=7)
    while c <= latest:
        out.append((c - dt.timedelta(days=7), c))
        c += dt.timedelta(days=7)
    return out[-max_weeks:]


# --- surveillance de Vectorworks -----------------------------------------------------------------------------------
BROWSERS = ("chrome.exe", "msedge.exe", "firefox.exe")


def classify(exe, title):
    """Logiciel surveillé au premier plan : vectorworks, multidoc, chrome ou None."""
    e = (exe or "").lower()
    if "vectorworks" in e:
        return "vectorworks"
    if "multidoc" in e or (e and e not in BROWSERS and "multidoc" in (title or "").lower()):
        return "multidoc"
    if e == "chrome.exe":
        return "chrome"
    return None


def ref_from_title(title, known, loose=False):
    """Opération citée dans un titre de fenêtre : code connu (Ragic) ; loose=True : aussi un motif NOM12-A."""
    r = ref_from_filename(title or "", known)
    if r and (loose or any(norm(r) == norm(k) for k in known)):
        return r
    return ""


class ActivityTracker:
    """Cumule le temps passé dans Vectorworks, Multidoc et Chrome, rattaché à l'opération citée dans la fenêtre.
    sample(exe, title, idle) est appelé toutes les `step` secondes ; un créneau est écrit au journal quand le fichier / l'opération
    change, au bout de `flush` secondes, ou à l'arrêt. Les titres de fenêtres ne sont JAMAIS enregistrés (sauf le nom du fichier
    Vectorworks) : seul le code d'opération reconnu est conservé."""

    def __init__(self, temps_dir, step=5, flush=60, clock=dt.datetime.now, known_fn=lambda: ()):
        self.dir, self.step, self.flush, self.clock, self.known_fn = temps_dir, step, flush, clock, known_fn
        self.key, self.acc = None, 0.0
        self.lock = threading.RLock()

    def sample(self, exe, title, idle):
        with self.lock:
            return self._sample(exe, title, idle)

    def _sample(self, exe, title, idle):
        src = classify(exe, title) if idle < IDLE_LIMIT else None
        key = None
        if src == "vectorworks":
            f = vw_file_from_title(title) or "(fichier inconnu).vwx"
            key = (src, re.sub(r"\.vwx\w*$", "", f, flags=re.I), f)
        elif src:
            try:
                known = list(self.known_fn())
            except Exception:
                known = []
            key = (src, ref_from_title(title, known, loose=(src == "multidoc")), None)
        if key != self.key:
            self.close()
        if key:
            self.key = key
            self.acc += self.step
            if self.acc >= self.flush:
                self._write()
        return key

    def _write(self):
        with self.lock:
            if self.key and self.acc > 0:
                src, ref, f = self.key
                try:
                    append_entry(self.dir, src, ref, self.acc, self.clock(), f)
                except OSError:
                    pass
            self.acc = 0.0

    def flush_now(self):
        """Écrit tout de suite le créneau en cours (avant une publication) sans changer de fichier."""
        self._write()

    def close(self):
        self._write()
        self.key = None


def win_foreground():
    """(nom de l'exécutable, titre de la fenêtre, secondes d'inactivité) — Windows uniquement."""
    import ctypes
    from ctypes import wintypes
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    u.GetForegroundWindow.restype = wintypes.HWND
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    k.CloseHandle.argtypes = [wintypes.HANDLE]

    class LII(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    lii = LII()
    lii.cbSize = ctypes.sizeof(LII)
    idle = 0.0
    if u.GetLastInputInfo(ctypes.byref(lii)):
        idle = ((k.GetTickCount() & 0xFFFFFFFF) - lii.dwTime & 0xFFFFFFFF) / 1000.0
    hwnd = u.GetForegroundWindow()
    if not hwnd:
        return None, "", idle
    buf = ctypes.create_unicode_buffer(512)
    u.GetWindowTextW(hwnd, buf, 512)
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = None
    h = k.OpenProcess(0x1000, False, pid.value)           # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        n = wintypes.DWORD(520)
        p = ctypes.create_unicode_buffer(520)
        if k.QueryFullProcessImageNameW(h, 0, p, ctypes.byref(n)):
            exe = os.path.basename(p.value)
        k.CloseHandle(h)
    return exe, buf.value, idle


# --- vue hebdomadaire (publiée dans la Dropbox pour l'interface ADMIN) ---------------------------------------------
def weekly(temps_dir, known, now, n=8):
    """Semaines (vendredi 18 h → vendredi 18 h) des n dernières, la semaine en cours en premier."""
    cur_start = last_cutoff(now)
    cur_end = cur_start + dt.timedelta(days=7)
    out = []
    for i in range(n):
        s = cur_start - dt.timedelta(days=7 * i)
        e = s + dt.timedelta(days=7)
        entries = read_entries(temps_dir, s, e)
        if not entries and i > 0:
            continue
        sm = build_summary(entries, known, s, e, "", "")
        out.append({"label": week_label(e), "start": sm["start"], "end": sm["end"], "current": i == 0,
                    "total_sec": sm["total_sec"],
                    "operations": [{"ref": o["ref"], "total_sec": o["total_sec"], "by_source": o["by_source"]} for o in sm["operations"]]})
    return out


# --- camemberts : temps par logiciel et par ordinateur ---------------------------------------------------------------
COLORS = {"dtg": "#B22222", "cctp": "#1F6FB2", "vectorworks": "#2E8B57", "multidoc": "#D98E04", "chrome": "#7B52AB"}


def source_slices(operations):
    """[(nom, secondes, couleur)] : temps par logiciel, tout opérations confondues, du plus long au plus court."""
    tot = {}
    for o in operations:
        for k, v in (o.get("by_source") or {}).items():
            tot[k] = tot.get(k, 0) + v
    return [(SOURCES.get(k, k), v, COLORS.get(k, "#888888")) for k, v in sorted(tot.items(), key=lambda kv: -kv[1]) if v > 0]


def pie_svg(slices, size=220):
    import math
    total = sum(v for _, v, _ in slices)
    r = size / 2 - 4
    c = size / 2
    if total <= 0:
        return f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}"><circle cx="{c}" cy="{c}" r="{r}" fill="#EEE"/></svg>'
    out = [f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" xmlns="http://www.w3.org/2000/svg">']
    a0 = -math.pi / 2
    for name, v, col in slices:
        frac = v / total
        if frac >= 0.9999:
            out.append(f'<circle cx="{c}" cy="{c}" r="{r}" fill="{col}"><title>{name}</title></circle>')
            break
        a1 = a0 + frac * 2 * math.pi
        x0, y0, x1, y1 = c + r * math.cos(a0), c + r * math.sin(a0), c + r * math.cos(a1), c + r * math.sin(a1)
        out.append(f'<path d="M{c:.1f} {c:.1f} L{x0:.2f} {y0:.2f} A{r:.1f} {r:.1f} 0 {1 if frac > 0.5 else 0} 1 {x1:.2f} {y1:.2f} Z" '
                   f'fill="{col}" stroke="#fff" stroke-width="1.5"><title>{name}</title></path>')
        a0 = a1
    out.append("</svg>")
    return "".join(out)


def pie_png(slices, size=300):
    """Camembert en PNG (sans bibliothèque externe) pour l'insérer dans un mail."""
    import math
    import struct
    import zlib
    total = sum(v for _, v, _ in slices)
    cols = [tuple(int(col[i:i + 2], 16) for i in (1, 3, 5)) for _, _, col in slices]
    bounds, acc = [], 0.0
    for _, v, _ in slices:
        acc += v / total if total else 0
        bounds.append(acc)
    R, c = size / 2 - 3, size / 2
    sub = (0.25, 0.75)
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            r = g = b = 0
            for sy in sub:
                for sx in sub:
                    dx, dy = x + sx - c, y + sy - c
                    if total > 0 and dx * dx + dy * dy <= R * R:
                        t = (math.atan2(dx, -dy) / (2 * math.pi)) % 1.0       # 0 en haut, sens horaire
                        k = next((i for i, bnd in enumerate(bounds) if t < bnd), len(bounds) - 1)
                        pr, pg, pb = cols[k]
                    else:
                        pr = pg = pb = 255
                    r += pr
                    g += pg
                    b += pb
            row += bytes((r // 4, g // 4, b // 4))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def legend_rows(slices):
    total = sum(v for _, v, _ in slices) or 1
    return "".join(f'<tr><td style="padding:2px 6px"><span style="display:inline-block;width:12px;height:12px;background:{col}"></span></td>'
                   f'<td style="padding:2px 10px 2px 0">{_esc(n)}</td><td style="padding:2px 10px 2px 0;text-align:right"><b>{fmt_dur(v)}</b></td>'
                   f'<td style="padding:2px 0;text-align:right;color:#777">{100 * v / total:.0f} %</td></tr>' for n, v, col in slices)


def report_html(label, start, end, pcs):
    """Rapport de la semaine : un camembert par ordinateur (temps par logiciel) + détail par opération.
    pcs = [{'pc', 'name' (surnom éventuel), 'user', 'total_sec', 'operations': [{'ref','total_sec','by_source'}]}]."""
    cards = []
    for p in pcs:
        sl = source_slices(p["operations"])
        ops = "".join(f'<tr><td style="padding:2px 10px 2px 0">{_esc(o["ref"])}</td><td style="text-align:right;padding:2px 0"><b>{fmt_dur(o["total_sec"])}</b></td>'
                      f'<td style="padding:2px 0 2px 10px;color:#777">{_esc(", ".join(SOURCES.get(k, k) for k in sorted(o.get("by_source") or {})))}</td></tr>'
                      for o in p["operations"][:12])
        title = _esc(p.get("name") or p["pc"]) + (f' <span style="color:#777;font-weight:400">· {_esc(p["pc"])}</span>' if p.get("name") else "")
        cards.append(f'<section><h2>{title} <small>{_esc(p.get("user", ""))}</small></h2>'
                     f'<div class="row"><div>{pie_svg(sl)}</div><div><div class="tot">{fmt_dur(p["total_sec"])}</div>'
                     f'<table>{legend_rows(sl) or "<tr><td>Aucune activité</td></tr>"}</table></div></div>'
                     f'<table class="ops">{ops}</table></section>')
    css = ("body{font:14px Segoe UI,Arial,sans-serif;background:#F3F2EE;color:#1F2328;margin:0;padding:24px}h1{font-size:20px;margin:0 0 4px}"
           ".sub{color:#777;margin-bottom:18px}section{background:#fff;border:1px solid #DCDAD3;padding:16px 20px;margin:0 0 16px;max-width:760px}"
           "h2{font-size:16px;margin:0 0 10px}h2 small{color:#777;font-weight:400}.row{display:flex;gap:24px;align-items:center}"
           ".tot{font-size:26px;font-weight:700;margin-bottom:6px}.ops{margin-top:12px;border-top:1px solid #eee;padding-top:8px;font-size:13px}")
    return (f'<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>AGOA TEMPS {label}</title><style>{css}</style></head><body>'
            f'<h1>AGOA TEMPS — semaine {label}</h1><div class="sub">du {start.replace("T", " ")} au {end.replace("T", " ")} · temps par logiciel et par ordinateur</div>'
            + "".join(cards) + "</body></html>")


def email_html(s):
    """Corps HTML du mail : camembert (image jointe cid:pie) + légende."""
    sl = source_slices(s["operations"])
    return (f'<div style="font:14px Segoe UI,Arial,sans-serif"><h3 style="margin:0 0 4px">Temps de travail — {_esc(s["pc"])} ({_esc(s["user"])})</h3>'
            f'<div style="color:#777;margin-bottom:10px">du {s["start"].replace("T", " ")} au {s["end"].replace("T", " ")} · total {fmt_dur(s["total_sec"])}</div>'
            f'<table><tr><td valign="top"><img src="cid:pie" width="220" height="220" alt="Camembert"></td><td valign="middle"><table>{legend_rows(sl)}</table></td></tr></table>'
            f'<pre style="font:13px Consolas,monospace;margin-top:12px">{_esc(render_text(s))}</pre></div>')
