"""Onglet ADMIN : accès réservé aux comptes Google listés ici (authentification Google refaite à chaque ouverture)."""

# Liste volontairement écrite dans le programme (et non dans catalog.json) : modifier les administrateurs
# demande une nouvelle version du lanceur, donc un accès au dépôt GitHub.
ADMIN_EMAILS = ["tkochanski@gmail.com"]


def is_admin(email):
    return str(email or "").strip().lower() in ADMIN_EMAILS
