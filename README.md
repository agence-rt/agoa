# AGOA Launcher
Lanceur Windows des applications AGOA (installation, mise à jour, ouverture) — installe tout sous `C:\AGOA`.

- `catalog.json` : liste des programmes et de leurs dépôts GitHub (relu automatiquement par le lanceur).
- Publier une version du lanceur : `git tag v0.2.0 && git push --tags` → GitHub Actions compile `AGOA-Launcher.exe` et crée la release.
- Chaque programme publie une release contenant un `.zip`.
