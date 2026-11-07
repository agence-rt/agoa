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
import unicodedata

SOURCES = {"dtg": "DTG", "cctp": "CCTP", "vectorworks": "Vectorworks"}
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
class ActivityTracker:
    """Cumule le temps passé dans Vectorworks, par fichier ouvert. sample(exe, title, idle) est appelé toutes les `step` secondes ;
    un créneau est écrit au journal quand le fichier change, au bout de `flush` secondes, ou à l'arrêt."""

    def __init__(self, temps_dir, step=5, flush=60, clock=dt.datetime.now):
        self.dir, self.step, self.flush, self.clock = temps_dir, step, flush, clock
        self.file, self.acc = None, 0.0

    def sample(self, exe, title, idle):
        f = None
        if exe and "vectorworks" in exe.lower() and idle < IDLE_LIMIT:
            f = vw_file_from_title(title) or "(fichier inconnu).vwx"
        if f != self.file:
            self.close()
        if f:
            self.file = f
            self.acc += self.step
            if self.acc >= self.flush:
                self._write()
        return f

    def _write(self):
        if self.file and self.acc > 0:
            try:
                append_entry(self.dir, "vectorworks", re.sub(r"\.vwx\w*$", "", self.file, flags=re.I), self.acc, self.clock(), self.file)
            except OSError:
                pass
        self.acc = 0.0

    def close(self):
        self._write()
        self.file = None


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
