import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agoa_admin as ad


class Admin(unittest.TestCase):
    def test_check(self):
        salt = "00" * 16
        h = ad.digest("4821", salt)
        self.assertTrue(ad.check("4821", salt, h))
        self.assertFalse(ad.check("4822", salt, h))
        self.assertFalse(ad.check("", salt, h))

    def test_code_reel_refuse_les_essais_courants(self):
        for bad in ("", "0000", "123456", "000000", "admin"):
            self.assertFalse(ad.check(bad))
        self.assertNotIn("145300", open(ad.__file__, encoding="utf-8").read())

    def test_blocage(self):
        st = {}
        for _ in range(ad.MAX_FAILS - 1):
            ad.register_fail(st, now=1000)
        self.assertEqual(ad.locked_for(st, now=1000), 0)
        ad.register_fail(st, now=1000)
        self.assertEqual(ad.locked_for(st, now=1000), ad.LOCK_STEP)
        self.assertEqual(ad.locked_for(st, now=1000 + ad.LOCK_STEP), 0)
        ad.register_ok(st)
        self.assertEqual(st["fails"], 0)


if __name__ == "__main__":
    unittest.main()
