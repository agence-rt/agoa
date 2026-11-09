"""Service d'arrière-plan du launcher : surveille Vectorworks, tient à jour la liste des opérations (Ragic) et envoie
chaque vendredi à 18 h la synthèse des temps de ce poste."""
import datetime as dt
import email.message
import json
import os
import re
import socket
import threading
import time
import urllib.parse

import agoa_connections as conn
import agoa_temps as T

TO = "etudes@remithollet.fr"
OPS_SHEET = "operations/10"
CODE_FIELD = "CODE TRAVAUX"


def pc_name():
    return (os.environ.get("COMPUTERNAME") or socket.gethostname() or "PC").upper()


def user_name():
    return os.environ.get("USERNAME") or os.environ.get("USER") or ""


def dropbox_roots():
    roots = []
    for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("APPDATA")):
        if not base:
            continue
        info = _load(os.path.join(base, "Dropbox", "info.json"), None)
        if isinstance(info, dict):
            for v in info.values():
                if isinstance(v, dict) and v.get("path"):
                    roots.append(v["path"])
    home = os.path.expanduser("~")
    roots += [os.path.join(home, "Dropbox"), os.path.join(home, "T&K Dropbox"), home]
    seen = []
    for r in roots:
        if r not in seen and os.path.isdir(r):
            seen.append(r)
    return seen


def _subdirs(d):
    try:
        return [n for n in os.listdir(d) if os.path.isdir(os.path.join(d, n))]
    except OSError:
        return []


def find_shared_dir(override=None):
    """Dossier Dropbox commun où chaque poste dépose ses temps : 09 - BDD / AGOA / TEMPS (créé au besoin)."""
    if override and os.path.isdir(override):
        return override
    for root in dropbox_roots():
        parents = [root] + [os.path.join(root, n) for n in _subdirs(root) if re.match(r"(?i)agence|t&k|thollet", n)]
        for p in parents:
            for bdd in _subdirs(p):
                if re.match(r"(?i)^09\b.*bdd", bdd):
                    d = os.path.join(p, bdd, "AGOA", "TEMPS")
                    try:
                        os.makedirs(d, exist_ok=True)
                        return d
                    except OSError:
                        continue
    return None


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


class TimeService:
    def __init__(self, temps_dir, conn_store, client_fn, call, foreground=None, now=dt.datetime.now):
        self.dir, self.conn, self.client_fn, self.call = temps_dir, conn_store, client_fn, call
        self.foreground, self.now = foreground, now
        self.state_path = os.path.join(temps_dir, "etat.json")
        self.ops_path = os.path.join(temps_dir, "operations.json")
        self.tracker = T.ActivityTracker(temps_dir)
        self.stop_ev = threading.Event()
        self.last_error = ""
        self._last_ops = 0.0
        self._last_due = 0.0

    # --- cycle -----------------------------------------------------------------
    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self.stop_ev.set()
        try:
            self.tracker.close()
        except Exception:
            pass

    def _run(self):
        while not self.stop_ev.wait(5):
            try:
                if self.foreground:
                    exe, title, idle = self.foreground()
                    self.tracker.sample(exe, title, idle)
                t = time.time()
                if t - self._last_ops > 6 * 3600:
                    self._last_ops = t
                    self.refresh_operations()
                if t - getattr(self, "_last_pub", 0) > 120:
                    self._last_pub = t
                    self.publish()
                if t - self._last_due > 60:
                    self._last_due = t
                    self.send_due()
            except Exception as e:          # le suivi ne doit jamais faire tomber le launcher
                self.last_error = str(e)[:200]

    # --- publication des temps de ce poste dans la Dropbox (lue par l'interface ADMIN) -----
    def shared_dir(self):
        return find_shared_dir(self.shared_override() if self.shared_override else None)

    shared_override = None

    def local_payload(self):
        return {"pc": pc_name(), "user": user_name(), "updated": self.now().isoformat(timespec="seconds"),
                "weeks": T.weekly(self.dir, self.known(), self.now())}

    def publish(self, force=False):
        d = self.shared_dir()
        if not d:
            return False
        self.tracker.flush_now()
        p = self.local_payload()
        body = {k: v for k, v in p.items() if k != "updated"}
        sig = json.dumps(body, sort_keys=True)
        if not force and sig == getattr(self, "_last_sig", None):
            return True
        _save(os.path.join(d, pc_name() + ".json"), p)
        self._last_sig = sig
        return True

    def fetch_all(self):
        """Temps de tous les postes (fichiers de la Dropbox) ; le poste local est recalculé à l'instant."""
        d = self.shared_dir()
        res = {}
        if d:
            for n in os.listdir(d):
                if n.lower().endswith(".json"):
                    j = _load(os.path.join(d, n), None)
                    if isinstance(j, dict) and j.get("pc"):
                        res[j["pc"]] = j
        try:
            self.publish(force=True)
        except Exception:
            pass
        res[pc_name()] = self.local_payload()
        return res, d

    # --- surnoms des postes (fichier partagé dans la Dropbox, sinon local) --------------------
    def nick_path(self):
        d = self.shared_dir()
        return os.path.join(d, "_surnoms.json") if d else os.path.join(self.dir, "surnoms.json")

    def nicknames(self):
        j = _load(self.nick_path(), {})
        return j if isinstance(j, dict) else {}

    def set_nickname(self, pc, nick):
        j = self.nicknames()
        nick = (nick or "").strip()
        if nick:
            j[pc] = nick
        else:
            j.pop(pc, None)
        _save(self.nick_path(), j)

    # --- opérations connues (Ragic) -------------------------------------------------
    def known(self):
        return _load(self.ops_path, [])

    def refresh_operations(self):
        r = self.conn.get("ragic")
        if not r or not r.get("key"):
            return False
        host = re.sub(r"^https?://", "", r.get("host") or "eu2.ragic.com").strip("/")
        base = f"https://{host}/{r.get('apname') or conn.RAGIC_APNAME}/{OPS_SHEET}"
        codes, offset = set(), 0
        while offset < 20000:
            url = f"{base}?api&v=3&limit=1000&offset={offset}&APIKey={urllib.parse.quote(conn.clean_key('ragic', r['key']), safe='')}"
            j = json.loads(self.call(url, {"Accept": "application/json"}).decode("utf-8"))
            recs = [v for k, v in j.items() if str(k).isdigit() and isinstance(v, dict)] if isinstance(j, dict) else []
            for v in recs:
                c = str(v.get(CODE_FIELD) or "").strip()
                if c:
                    codes.add(c)
            if len(recs) < 1000:
                break
            offset += 1000
        if codes:
            _save(self.ops_path, sorted(codes))
        return bool(codes)

    # --- synthèse hebdomadaire ------------------------------------------------------
    def send_due(self):
        st = _load(self.state_path, {})
        now = self.now()
        if not st.get("last_cutoff"):             # premier lancement : pas de rattrapage, on part de la dernière coupure
            st["last_cutoff"] = T.last_cutoff(now).isoformat()
            _save(self.state_path, st)
            return
        last = dt.datetime.fromisoformat(st["last_cutoff"])
        for start, end in T.due_periods(last, now):
            self.tracker.close()
            s = self.make_summary(start, end)
            sent = False
            try:
                self.send_mail(s)
                sent = True
            except Exception as e:
                self.last_error = "Envoi de la synthèse : " + str(e)[:150]
            if not sent:                           # on réessaiera à la prochaine minute (Google non connecté, réseau…)
                return
            st["last_cutoff"] = end.isoformat()
            _save(self.state_path, st)

    def make_summary(self, start, end):
        entries = T.read_entries(self.dir, start, end)
        s = T.build_summary(entries, self.known(), start, end, pc_name(), user_name())
        name = f"{T.week_label(end)}_{pc_name()}"
        out = os.path.join(self.dir, "syntheses")
        os.makedirs(out, exist_ok=True)
        _save(os.path.join(out, name + ".json"), s)
        with open(os.path.join(out, name + ".csv"), "w", encoding="utf-8", newline="") as f:
            f.write(T.render_csv(s))
        return s

    def send_mail(self, s):
        g = self.conn.get("google")
        client = self.client_fn()
        if not g or not g.get("refresh_token") or not client:
            raise RuntimeError("compte Google non connecté dans le launcher")
        m = email.message.EmailMessage()
        m["To"] = TO
        m["Subject"] = f"[AGOA] Temps de travail {T.week_label(dt.datetime.fromisoformat(s['end']))} — {s['pc']}"
        m.set_content(T.render_text(s) + "\n\nMessage automatique du launcher AGOA.")
        m.add_attachment(T.render_csv(s).encode("utf-8"), maintype="text", subtype="csv",
                         filename=f"temps_{T.week_label(dt.datetime.fromisoformat(s['end']))}_{s['pc']}.csv")
        tok = conn.google_access_token(client, g["refresh_token"], self.call)
        conn.gmail_send(tok, m.as_bytes(), self.call)
