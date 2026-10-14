# AGOA Launcher
Lanceur Windows des applications AGOA (installation, mise à jour, ouverture) — installe tout sous `C:\AGOA`.

- `catalog.json` : liste des programmes et de leurs dépôts GitHub (relu automatiquement par le lanceur).
- Publier une version du lanceur : augmenter `LAUNCHER_VERSION` dans `agoa_launcher.py` puis pousser sur `main` → GitHub Actions compile `AGOA-Launcher.exe` et crée la release (étiquette automatique).
- Chaque programme publie une release contenant un `.zip`.
