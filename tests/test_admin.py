import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import agoa_admin as ad


def test_pin_ok_et_ko():
    r = ad.make_record("4821")
    assert "4821" not in str(r)
    assert ad.check(r, "4821")
    assert not ad.check(r, "4822")
    assert not ad.check(r, "")
    assert not ad.check({}, "4821")


def test_blocage_apres_echecs():
    r = ad.make_record("4821")
    for _ in range(ad.MAX_FAILS - 1):
        ad.register_fail(r, now=1000)
    assert ad.locked_for(r, now=1000) == 0
    ad.register_fail(r, now=1000)
    assert ad.locked_for(r, now=1000) == ad.LOCK_STEP
    assert ad.locked_for(r, now=1000 + ad.LOCK_STEP) == 0
    ad.register_ok(r)
    assert r["fails"] == 0
