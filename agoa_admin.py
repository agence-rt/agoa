"""Onglet ADMIN : code PIN unique. Seule l'empreinte (PBKDF2 salée) est dans le programme, jamais le code lui-même.
Pour changer le code : générer une nouvelle paire avec  python agoa_admin.py <nouveau code>  et remplacer SALT / HASH."""
import hashlib
import hmac
import sys
import time

ITERATIONS = 600_000
SALT = "0cb10e051f276a15f04605807d5bf0fb"
HASH = "fcbe4be4bf9c610888c5c753907091249d2395eefda42471d9b6da6e3d09ee78"
MAX_FAILS = 5          # après 5 erreurs : blocage, de plus en plus long
LOCK_STEP = 60         # secondes de blocage par série de MAX_FAILS erreurs


def digest(pin, salt_hex=SALT):
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt_hex), ITERATIONS).hex()


def check(pin, salt_hex=SALT, hash_hex=HASH):
    return bool(pin) and hmac.compare_digest(digest(pin, salt_hex), hash_hex)


def locked_for(state, now=None):
    """Secondes de blocage restantes (state = {"fails": n, "until": t} conservé dans les réglages)."""
    return max(0, int(float((state or {}).get("until", 0)) - (now if now is not None else time.time())))


def register_fail(state, now=None):
    now = now if now is not None else time.time()
    state["fails"] = int(state.get("fails", 0)) + 1
    if state["fails"] % MAX_FAILS == 0:
        state["until"] = now + LOCK_STEP * (state["fails"] // MAX_FAILS)
    return state


def register_ok(state):
    state["fails"], state["until"] = 0, 0
    return state


if __name__ == "__main__":
    import os
    if len(sys.argv) != 2:
        sys.exit("usage : python agoa_admin.py <nouveau code>")
    s = os.urandom(16).hex()
    print(f'SALT = "{s}"\nHASH = "{digest(sys.argv[1], s)}"')
