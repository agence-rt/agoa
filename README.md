# AGOA Launcher
Application Windows qui installe, met à jour et ouvre les applications AGOA de l'agence (dans `C:\AGOA`).

## Installer
Télécharger `AGOA-Launcher-Setup-x.y.z.exe` dans la [dernière Release](https://github.com/agence-rt/agoa/releases/latest) et le lancer.
Le lanceur s'installe dans `C:\AGOA\Launcher` (raccourcis bureau + menu Démarrer) et se met à jour seul au démarrage.

## Publier une version
Augmenter `LAUNCHER_VERSION` dans `agoa_launcher.py` puis pousser sur `main` : GitHub Actions compile le programme,
fabrique l'installateur (`installer.nsi`) et crée la Release (étiquette automatique).

## Catalogue
`catalog.json` liste les applications et leurs dépôts ; il est relu sur GitHub à chaque démarrage (pas besoin de recompiler).
