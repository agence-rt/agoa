"""Code PIN de l'onglet ADMIN : empreinte PBKDF2 (jamais le code en clair) et blocage après des échecs répétés."""
import hashlib
import hmac
import os
import time

ITERATIONS = 200_000
MIN_LEN = 4
MAX_FAILS = 5          # après 5 erreurs : blocage, de plus en plus long
LOCK_STEP = 60         # secondes de blocage par série de MAX_FAILS erreurs


def make_record(pin, salt=None):
    salt = salt or os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, ITERATIONS)
    return {"salt": salt.hex(), "hash": h.hex(), "iter": ITERATIONS, "fails": 0, "until": 0}


def check(record, pin):
    if not record or not pin:
        return False
    try:
        h = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(record["salt"]), int(record.get("iter", ITERATIONS)))
        return hmac.compare_digest(h.hex(), record["hash"])
    except (KeyError, ValueError, TypeError):
        return False


def locked_for(record, now=None):
    """Secondes restantes de blocage (0 = libre)."""
    return max(0, int(float((record or {}).get("until", 0)) - (now if now is not None else time.time())))


def register_fail(record, now=None):
    now = now if now is not None else time.time()
    record["fails"] = int(record.get("fails", 0)) + 1
    if record["fails"] % MAX_FAILS == 0:
        record["until"] = now + LOCK_STEP * (record["fails"] // MAX_FAILS)
    return record


def register_ok(record):
    record["fails"] = 0
    record["until"] = 0
    return record
