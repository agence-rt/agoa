import json, os, sys, threading, unittest, urllib.parse, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agoa_admin as ad
import agoa_connections as C


class Admin(unittest.TestCase):
    def test_is_admin(self):
        self.assertTrue(ad.is_admin("tkochanski@gmail.com"))
        self.assertTrue(ad.is_admin(" TKochanski@Gmail.com "))
        self.assertFalse(ad.is_admin("dieu@remithollet.fr"))
        self.assertFalse(ad.is_admin(""))

    def _run(self, email, verified=True, aud="cid"):
        seen = {}

        def open_url(u):
            seen["q"] = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
            q = seen["q"]
            cb = q["redirect_uri"][0] + "?" + urllib.parse.urlencode({"state": q["state"][0], "code": "CODE"})
            threading.Thread(target=lambda: urllib.request.urlopen(cb).read(), daemon=True).start()

        def call(url, headers=None, data=None):
            b = lambda o: C._b64url(json.dumps(o).encode())
            tok = b({"alg": "none"}) + "." + b({"email": email, "email_verified": verified, "aud": aud, "name": "T"}) + ".x"
            return json.dumps({"id_token": tok}).encode()   # pas de refresh_token : aucune session durable
        r = C.google_sign_in({"clientId": "cid", "clientSecret": "s"}, call, open_url, timeout=10, emails=ad.ADMIN_EMAILS)
        return r, seen["q"]

    def test_admin_accepte_et_reconnexion_forcee(self):
        r, q = self._run("tkochanski@gmail.com")
        self.assertEqual(r["email"], "tkochanski@gmail.com")
        self.assertEqual(q["scope"], ["openid email"])          # aucun droit Gmail/Agenda demandé
        self.assertIn("login", q["prompt"][0])                  # Google redemande le mot de passe
        self.assertEqual(q["max_age"], ["0"])
        self.assertNotIn("access_type", q)

    def test_autres_comptes_refuses(self):
        for email, ver, aud in [("dieu@remithollet.fr", True, "cid"), ("tkochanski@gmail.com", False, "cid"),
                                ("tkochanski@gmail.com", True, "autre")]:
            with self.assertRaises(C.ConnError):
                self._run(email, ver, aud)


if __name__ == "__main__":
    unittest.main()
