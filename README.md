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

## Connexions partagées (Ragic, Google, Claude)

Le bouton **CONNEXIONS** du launcher enregistre une seule fois la clé API Ragic, le compte Google de l'agence et la clé API Claude.
Les applications AGOA les relisent au lieu de les redemander.

**Stockage** : `C:\AGOA\_connexions\connexions.dat`, chiffré avec la protection de session Windows (DPAPI, utilisateur courant).
Seul le compte Windows qui l'a créé peut le relire.

**Contenu** (JSON, avant chiffrement) :

```json
{ "ragic":     { "key": "…", "host": "eu2.ragic.com", "apname": "agoa" },
  "google":    { "refresh_token": "…", "email": "prenom@remithollet.fr", "name": "…", "scopes": "…", "client_id": "…" },
  "anthropic": { "key": "sk-ant-…" } }
```

**Lecture par une application**

1. Lancée depuis le launcher, elle reçoit des variables d'environnement :
   `AGOA_RAGIC_KEY`, `AGOA_RAGIC_HOST`, `AGOA_RAGIC_APNAME`, `AGOA_ANTHROPIC_API_KEY`,
   `AGOA_GOOGLE_REFRESH_TOKEN`, `AGOA_GOOGLE_EMAIL`, `AGOA_GOOGLE_CLIENT_ID`.
2. Lancée autrement (raccourci Windows), elle lit le fichier. Exemple Node.js (Electron) :

```js
const { execFileSync } = require("child_process");
function agoaConnections() {
  const f = "C:\\AGOA\\_connexions\\connexions.dat";
  const ps = `Add-Type -AssemblyName System.Security; [Text.Encoding]::UTF8.GetString([Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes('${f}'),$null,'CurrentUser'))`;
  try { return JSON.parse(execFileSync("powershell", ["-NoProfile", "-Command", ps], { windowsHide: true }).toString()); }
  catch (e) { return {}; }   // fichier absent : proposer d'ouvrir le launcher
}
```

Le jeton Google doit être utilisé avec le même `client_id` que celui qui l'a émis (secrets `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`
du dépôt, également nécessaires à la connexion Google du launcher).
