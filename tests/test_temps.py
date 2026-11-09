import datetime as dt
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agoa_temps as T

KNOWN = ["BELLEVILLE112", "PIGALLE67-71-A", "PIGALLE67-71-B", "LANDY67-A", "CHARCOT15-19"]


class Names(unittest.TestCase):
    def test_same(self):
        self.assertTrue(T.same_ref("Belleville112", "BELLEVILLE112"))
        self.assertTrue(T.same_ref("BELVILLE112", "BELLEVILLE112"))       # faute de frappe
        self.assertFalse(T.same_ref("PIGALLE67-71-A", "PIGALLE67-71-B"))   # indices = dossiers différents
        self.assertFalse(T.same_ref("LANDY67-A", "LANDY68-A"))
        self.assertFalse(T.same_ref("LANDY67", "LANDY67-A"))

    def test_title(self):
        self.assertEqual(T.vw_file_from_title("Vectorworks Architect 2021 SP4 - [LANDY67-A_RDC.vwx : Plan]"), "LANDY67-A_RDC.vwx")
        self.assertEqual(T.vw_file_from_title("LANDY67-A existant.vwx* - Vectorworks 2021"), "LANDY67-A existant.vwx")
        self.assertIsNone(T.vw_file_from_title("Vectorworks 2021"))

    def test_ref_from_file(self):
        self.assertEqual(T.ref_from_filename("LANDY67-A_Plans RDC.vwx", KNOWN), "LANDY67-A")
        self.assertEqual(T.ref_from_filename("pigalle67-71-b facade.vwx", KNOWN), "PIGALLE67-71-B")
        self.assertEqual(T.ref_from_filename("Belleville112 - coupes.vwx", KNOWN), "BELLEVILLE112")
        self.assertEqual(T.ref_from_filename("ORFILA6-A plan.vwx", []), "ORFILA6-A")
        self.assertIsNone(T.ref_from_filename("brouillon.vwx", KNOWN))


class Summary(unittest.TestCase):
    def test_week(self):
        with tempfile.TemporaryDirectory() as d:
            t = dt.datetime(2026, 11, 4, 10, 0)
            T.append_entry(d, "dtg", "belleville112", 600, t)
            T.append_entry(d, "cctp", "BELVILLE112", 1200, t)
            T.append_entry(d, "vectorworks", "x", 1800, t, "Belleville112 coupes.vwx")
            T.append_entry(d, "vectorworks", "x", 300, t, "brouillon.vwx")
            T.append_entry(d, "dtg", "NOUVEAU9", 100, t)
            T.append_entry(d, "dtg", "BELLEVILLE112", 999, dt.datetime(2026, 11, 20))   # hors période
            e = T.read_entries(d, dt.datetime(2026, 10, 30, 18), dt.datetime(2026, 11, 6, 18))
            s = T.build_summary(e, KNOWN, dt.datetime(2026, 10, 30, 18), dt.datetime(2026, 11, 6, 18), "PC1", "dieu")
            ops = {o["ref"]: o for o in s["operations"]}
            self.assertEqual(ops["BELLEVILLE112"]["total_sec"], 3600)
            self.assertEqual(ops["BELLEVILLE112"]["by_source"], {"cctp": 1200, "dtg": 600, "vectorworks": 1800})
            self.assertEqual(ops[T.NO_REF]["total_sec"], 300)
            self.assertFalse(ops["NOUVEAU9"]["known"])
            self.assertEqual(s["total_sec"], 4000)
            self.assertIn("BELLEVILLE112", T.render_text(s))
            self.assertIn("Vectorworks", T.render_csv(s))

    def test_cluster_unknown(self):
        c = T.canonical_names(["ORFILA6-A", "orfila6-a", "ORFILLA6-A", "ORFILA6-B"], [])
        self.assertEqual(c["ORFILLA6-A"], c["ORFILA6-A"])
        self.assertNotEqual(c["ORFILA6-B"], c["ORFILA6-A"])


class Calendar(unittest.TestCase):
    def test_cutoff(self):
        self.assertEqual(T.last_cutoff(dt.datetime(2026, 11, 6, 18, 5)), dt.datetime(2026, 11, 6, 18))
        self.assertEqual(T.last_cutoff(dt.datetime(2026, 11, 6, 17, 59)), dt.datetime(2026, 10, 30, 18))
        self.assertEqual(T.last_cutoff(dt.datetime(2026, 11, 9, 8)), dt.datetime(2026, 11, 6, 18))

    def test_due(self):
        last = dt.datetime(2026, 10, 23, 18)
        due = T.due_periods(last, dt.datetime(2026, 11, 7, 9))
        self.assertEqual(due, [(dt.datetime(2026, 10, 23, 18), dt.datetime(2026, 10, 30, 18)),
                               (dt.datetime(2026, 10, 30, 18), dt.datetime(2026, 11, 6, 18))])
        self.assertEqual(T.due_periods(dt.datetime(2026, 11, 6, 18), dt.datetime(2026, 11, 7)), [])


class Tracker(unittest.TestCase):
    def test_tracker(self):
        with tempfile.TemporaryDirectory() as d:
            tr = T.ActivityTracker(d, step=5, flush=60, clock=lambda: dt.datetime(2026, 11, 4, 9, 0))
            for _ in range(12):
                tr.sample("Vectorworks2021.exe", "Vectorworks 2021 - [LANDY67-A_RDC.vwx]", 1)
            for _ in range(3):
                tr.sample("Vectorworks2021.exe", "LANDY67-A_RDC.vwx - Vectorworks", 900)   # inactif : non compté
            tr.sample("chrome.exe", "Google", 1)
            e = T.read_entries(d, dt.datetime(2026, 11, 1), dt.datetime(2026, 11, 8))
            self.assertEqual(sum(x["sec"] for x in e), 60)
            self.assertEqual(e[0]["file"], "LANDY67-A_RDC.vwx")

    def test_multidoc_chrome(self):
        with tempfile.TemporaryDirectory() as d:
            tr = T.ActivityTracker(d, step=30, flush=60, clock=lambda: dt.datetime(2026, 11, 4, 9, 0), known_fn=lambda: KNOWN)
            for _ in range(2):
                tr.sample("chrome.exe", "Fiche BELLEVILLE112 - Ragic - Google Chrome", 1)
            for _ in range(2):
                tr.sample("chrome.exe", "YouTube - Google Chrome", 1)
            for _ in range(2):
                tr.sample("Multidoc.exe", "Multidoc - ORFILA6-A courrier", 1)
            tr.sample("msedge.exe", "Multidoc tarifs", 1)           # navigateur : ne compte pas comme Multidoc
            tr.close()
            e = T.read_entries(d, dt.datetime(2026, 11, 1), dt.datetime(2026, 11, 8))
            got = sorted((x["src"], x["ref"], x["sec"]) for x in e)
            self.assertEqual(got, [("chrome", "", 60), ("chrome", "BELLEVILLE112", 60), ("multidoc", "ORFILA6-A", 60)])
            self.assertTrue(all("title" not in x for x in e))


if __name__ == "__main__":
    unittest.main()
