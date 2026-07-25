# Construire AutoSD pour macOS

La version macOS utilise le même protocole P2P v2 que la version Windows.
Les deux applications peuvent donc créer et rejoindre les mêmes codes de transfert.

## Prérequis

- un Mac Intel ou Apple Silicon ;
- Python 3.12 ou 3.13 ;
- les outils de ligne de commande Xcode.

## Construction

Dans Terminal, à la racine du projet :

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-build.txt
python -m unittest discover -v
python -m PyInstaller --noconfirm --clean AutoSD-FileManager.spec
```

L'application est créée dans `dist/AutoSD File Manager.app`.

Le paquet principal contient `autosd_core.py` et l'interface Qt Quick. L'ancien
client Tkinter `auto sd v5.py` n'est pas intégré à l'application générée.
Le fichier de spécification utilise une construction en dossier (`COLLECT`),
puis crée le paquet `.app`. Cette organisation permet à `codesign` de parcourir
et de vérifier les bibliothèques Python et Qt incluses.
Un hook QML propre au projet n'embarque que Qt Quick, Controls, Layouts et
Dialogs, ce qui exclut notamment WebEngine et les modules 3D inutilisés.
PyInstaller convertit `assets/autosd-icon.png` en icône du paquet macOS.

## Archive de publication

Lorsqu'un tag `vX.Y.Z` est pousse sur GitHub, le workflow Release reconstruit
l'application macOS, applique la signature ad hoc de validation, puis publie
`AutoSD-FileManager-macos-vX.Y.Z.zip` et son fichier
`AutoSD-FileManager-macos-vX.Y.Z.zip.sha256` dans GitHub Releases. Cette archive
est le format attendu par le verificateur de mises a jour de l'application.

## Signature locale de test

```bash
codesign --force --deep --sign - "dist/AutoSD File Manager.app"
codesign --verify --deep --strict --verbose=2 "dist/AutoSD File Manager.app"
"dist/AutoSD File Manager.app/Contents/MacOS/AutoSD-FileManager" --self-test-network
```

Pour distribuer l'application à d'autres utilisateurs sans avertissement Gatekeeper,
il faut ensuite la signer avec un certificat Apple Developer ID et la notariser.

La CI reproduit automatiquement cette construction sur un exécuteur macOS,
applique une signature locale ad hoc et lance l'auto-test des composants réseau.
Elle ne remplace pas la signature Developer ID, la notarisation ni l'essai sur
un Mac physique avant publication.

## Compatibilité

- Utiliser la nouvelle version optimisée sur Windows et la version macOS issue de ce projet.
- Les anciennes versions Windows utilisant le protocole P2P v1 ne sont pas compatibles.
- La détection et l'éjection des cartes SD utilisent `diskutil` sur macOS.
