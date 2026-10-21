import json, os, sys, tempfile, threading, time, unittest, urllib.error, urllib.parse, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agoa_connections as C


class Store(unittest.TestCase):
    def test_roundtrip_and_env(self):
        with tempfile.TemporaryDirectory() as d:
            s = C.ConnectionStore(os.path.join(d, "x", "c.dat"))
            self.assertEqual(s.env(), {})
            s.set("ragic", {"key": "K" * 20, "host": "eu2.ragic.com", "apname": "agoa"})
            s.set("anthropic", {"key": "sk-ant-abc"})
            s.set("google", {"refresh_token": "rt", "email": "a@remithollet.fr", "client_id": "cid"})
            e = s.env()
            self.assertEqual(e["AGOA_RAGIC_HOST"], "eu2.ragic.com")
            self.assertEqual(e["AGOA_ANTHROPIC_API_KEY"], "sk-ant-abc")
            self.assertEqual(e["AGOA_GOOGLE_REFRESH_TOKEN"], "rt")
            s.remove("ragic")
            self.assertIsNone(s.get("ragic"))
            self.assertEqual(C.ConnectionStore(s.path).get("anthropic")["key"], "sk-ant-abc")

    def test_clean_key(self):
        self.assertEqual(C.clean_key("ragic", " “ab+c/d==”\u200b\r\n"), "ab+c/d==")
        self.assertEqual(C.clean_key("anthropic", " sk-ant-a_b\n"), "sk-ant-a_b")


class Checks(unittest.TestCase):
    def test_ragic_picks_working_host(self):
        seen = []

        def call(url, headers=None, data=None):
            seen.append(url)
            if "eu2.ragic.com" in url:
                return b'{"1": {"_ragicId": 1}}'
            return b'{"status":"ERROR","code":1,"msg":"You are currently accessing as: guest account"}'
        self.assertEqual(C.check_ragic("K" * 24, call), "eu2.ragic.com")
        self.assertEqual(len(seen), 2)

    def test_ragic_refused(self):
        def call(url, headers=None, data=None):
            raise urllib.error.HTTPError(url, 401, "", {}, None)
        with self.assertRaises(C.ConnError):
            C.check_ragic("K" * 24, call)

    def test_ragic_all_guest(self):
        def call(url, headers=None, data=None):
            return b'{"status":"ERROR","msg":"guest account"}'
        with self.assertRaisesRegex(C.ConnError, "invité"):
            C.check_ragic("K" * 24, call)

    def test_anthropic(self):
        ok = lambda url, headers=None, data=None: b"{}"
        self.assertTrue(C.check_anthropic("sk-ant-" + "a" * 40, ok))
        with self.assertRaises(C.ConnError):
            C.check_anthropic("abc", ok)

        def bad(url, headers=None, data=None):
            raise urllib.error.HTTPError(url, 401, "", {}, None)
        with self.assertRaisesRegex(C.ConnError, "refusée"):
            C.check_anthropic("sk-ant-" + "a" * 40, bad)


class Google(unittest.TestCase):
    def _idtoken(self, email, verified=True):
        b = lambda o: C._b64url(json.dumps(o).encode())
        return b({"alg": "none"}) + "." + b({"email": email, "email_verified": verified, "name": "Test"}) + ".x"

    def _run(self, email):
        opened = {}

        def open_url(u):
            opened["u"] = u
            q = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
            cb = q["redirect_uri"][0] + "?" + urllib.parse.urlencode({"state": q["state"][0], "code": "CODE"})
            threading.Thread(target=lambda: urllib.request.urlopen(cb).read(), daemon=True).start()

        def call(url, headers=None, data=None):
            self.assertEqual(url, C.TOKEN_URL)
            f = urllib.parse.parse_qs(data.decode())
            self.assertEqual(f["code"], ["CODE"])
            self.assertIn("code_verifier", f)
            return json.dumps({"id_token": self._idtoken(email), "refresh_token": "RT", "scope": "x"}).encode()
        r = C.google_sign_in({"clientId": "cid", "clientSecret": "sec"}, call, open_url, timeout=10)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(opened["u"]).query)
        self.assertEqual(q["code_challenge_method"], ["S256"])
        self.assertIn("gmail.send", q["scope"][0])
        return r

    def test_sign_in(self):
        r = self._run("dieu@remithollet.fr")
        self.assertEqual((r["refresh_token"], r["email"], r["client_id"]), ("RT", "dieu@remithollet.fr", "cid"))

    def test_other_domain_refused(self):
        with self.assertRaises(C.ConnError):
            self._run("x@gmail.com")

    def test_state_mismatch_ignored_then_timeout(self):
        def open_url(u):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
            cb = q["redirect_uri"][0] + "?state=WRONG&code=Z"
            threading.Thread(target=lambda: [urllib.request.urlopen(cb).read() for _ in [0]] if False else None).start()
        with self.assertRaises(C.ConnError):
            C.google_sign_in({"clientId": "c", "clientSecret": "s"}, lambda *a, **k: b"", open_url, timeout=1)


if __name__ == "__main__":
    unittest.main()
