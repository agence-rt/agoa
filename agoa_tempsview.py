"""Interface ADMIN : temps de travail de chaque poste, semaine par semaine (semaine en cours comprise)."""
import datetime as dt
import threading
import tkinter as tk
from tkinter import ttk

import agoa_temps as T

BG, INK, MUTED, RED, LINE = "#F3F2EE", "#1F2328", "#6B6F76", "#B22222", "#DCDAD3"


def hm(sec):
    return "" if not sec else T.fmt_dur(sec)


def collate(data):
    """data = {pc: payload} -> (semaines [(label, courante)], lignes). Les semaines sont triées de la plus récente à la plus ancienne."""
    weeks = {}
    for p in data.values():
        for w in p.get("weeks", []):
            cur = weeks.get(w["label"], False) or w.get("current", False)
            weeks[w["label"]] = cur
    labels = sorted(weeks, reverse=True)[:10]
    rows = []
    for pc in sorted(data):
        p = data[pc]
        byw = {w["label"]: w for w in p.get("weeks", [])}
        ops = {}
        for w in p.get("weeks", []):
            for o in w["operations"]:
                ops.setdefault(o["ref"], {})[w["label"]] = o["total_sec"]
        tot = {l: byw[l]["total_sec"] for l in labels if l in byw}
        rows.append({"pc": pc, "user": p.get("user", ""), "updated": p.get("updated", ""), "totals": tot,
                     "ops": sorted(ops.items(), key=lambda kv: -sum(kv[1].values()))})
    return [(l, weeks[l]) for l in labels], rows


class TempsWindow(tk.Toplevel):
    def __init__(self, app, service):
        super().__init__(app)
        self.app, self.svc = app, service
        self.title("AGOA — Temps de travail par poste")
        self.configure(bg=BG)
        S = getattr(app, "S", 1.0)
        self.geometry(f"{min(int(1180 * S), self.winfo_screenwidth() - 60)}x{min(int(640 * S), self.winfo_screenheight() - 100)}")
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=14, pady=(12, 6))
        tk.Label(top, text="TEMPS DE TRAVAIL", font=("Consolas", 10, "bold"), bg=INK, fg="white", padx=8, pady=3).pack(side="left")
        self.info = tk.Label(top, text="", font=("Segoe UI", 9), bg=BG, fg=MUTED)
        self.info.pack(side="left", padx=12)
        tk.Button(top, text="Actualiser", command=self.load, relief="flat", bg=INK, fg="white", padx=12, pady=3).pack(side="right")
        self.tree = ttk.Treeview(self, show="tree headings", selectmode="browse")
        sb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", padx=(0, 6), pady=(0, 10))
        self.tree.pack(fill="both", expand=True, padx=(14, 0), pady=(0, 10))
        self.tree.tag_configure("pc", font=("Segoe UI", 10, "bold"), background="#E9E7E0")
        self.tree.tag_configure("all", font=("Segoe UI", 10, "bold"), background="#F6D9D9")
        self.tree.tag_configure("old", foreground="#9A9A9A")
        self.load()

    def load(self):
        self.info.config(text="Récupération des temps…")

        def work():
            try:
                data, d = self.svc.fetch_all()
                err = None
            except Exception as e:
                data, d, err = {}, None, str(e)[:120]
            try:
                self.after(0, lambda: self.show(data, d, err))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def show(self, data, folder, err):
        if not self.winfo_exists():
            return
        weeks, rows = collate(data)
        cols = [f"w{i}" for i in range(len(weeks))]
        self.tree.configure(columns=cols)
        self.tree.heading("#0", text="Poste / Opération")
        self.tree.column("#0", width=300, anchor="w", stretch=True)
        for c, (lab, cur) in zip(cols, weeks):
            self.tree.heading(c, text=lab + (" ● en cours" if cur else ""))
            self.tree.column(c, width=105, anchor="e", stretch=False)
        self.tree.delete(*self.tree.get_children())
        all_tot = {}
        for r in rows:
            for l, v in r["totals"].items():
                all_tot[l] = all_tot.get(l, 0) + v
        if rows:
            self.tree.insert("", "end", iid="__all", text="Total agence", values=[hm(all_tot.get(l, 0)) for l, _ in weeks], tags=("all",))
        for r in rows:
            old = False
            try:
                old = (dt.datetime.now() - dt.datetime.fromisoformat(r["updated"])).days >= 2
            except ValueError:
                pass
            label = f"{r['pc']}" + (f"  ({r['user']})" if r["user"] else "") + ("  — pas de synchro récente" if old else "")
            pid = self.tree.insert("", "end", iid="pc:" + r["pc"], text=label, values=[hm(r["totals"].get(l, 0)) for l, _ in weeks],
                                   tags=("pc", "old") if old else ("pc",), open=True)
            for i, (ref, per) in enumerate(r["ops"]):
                self.tree.insert(pid, "end", text="    " + ref, values=[hm(per.get(l, 0)) for l, _ in weeks])
        if err:
            msg = "Erreur : " + err
        elif not folder:
            msg = "Dossier Dropbox des temps introuvable : seul ce poste est affiché."
        else:
            msg = f"{len(rows)} poste(s) · lu à {dt.datetime.now():%H:%M:%S} · {folder}"
        self.info.config(text=msg)
