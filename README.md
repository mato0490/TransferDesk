# TransferDesk

TransferDesk est une application Windows et macOS pour copier, trier,
transférer et vérifier des fichiers. Elle sert notamment à vider un support amovible,
organiser des fichiers, trouver des doublons exacts et envoyer des fichiers
entre deux ordinateurs.

## Télécharger l'application

Les versions prêtes à utiliser sont publiées sur GitHub Releases :

<https://github.com/mato0490/transferdesk/releases>

Téléchargez le fichier correspondant à votre système :

| Système | Fichier à télécharger |
| --- | --- |
| Windows | `TransferDesk-windows-vX.Y.Z.zip` |
| macOS | `TransferDesk-macos-vX.Y.Z.zip` |

Les fichiers `.sha256` servent à vérifier l'intégrité du téléchargement. Ils
sont aussi utilisés automatiquement par le système de mise à jour intégré.

## Installation sur Windows

1. Téléchargez `TransferDesk-windows-vX.Y.Z.zip` depuis la page Releases.
2. Décompressez le fichier `.zip`.
3. Ouvrez le dossier `TransferDesk`.
4. Lancez `TransferDesk.exe`.

Si Windows affiche un avertissement SmartScreen, choisissez **Informations
complémentaires**, puis **Exécuter quand même** si vous faites confiance à la
version téléchargée depuis ce dépôt.

## Installation sur macOS

1. Téléchargez `TransferDesk-macos-vX.Y.Z.zip` depuis la page Releases.
2. Décompressez le fichier `.zip`.
3. Déplacez `TransferDesk.app` dans le dossier **Applications**.
4. Ouvrez l'application.

Si macOS bloque l'ouverture parce que l'app n'est pas notarisée, ouvrez
**Réglages système > Confidentialité et sécurité**, puis choisissez
**Ouvrir quand même** pour TransferDesk.

## Mettre à jour

Dans l'application :

1. Ouvrez l'onglet **Aide**.
2. Cliquez sur **Vérifier les mises à jour**.
3. Si une nouvelle version existe, confirmez le téléchargement.
4. Après vérification SHA-256, confirmez l'installation.

TransferDesk ne télécharge jamais une mise à jour sans confirmation et ne
l'installe jamais sans une deuxième confirmation.

Sur Windows, une version empaquetée peut se remplacer automatiquement après
fermeture de l'application. Sur macOS, TransferDesk ouvre l'archive vérifiée
pour que vous terminiez l'installation.

## Utilisation rapide

- **Transfert de fichiers** : choisissez une source, une destination, puis
  lancez l'aperçu ou le transfert.
- **Doublons** : choisissez un dossier à analyser, puis déplacez ou supprimez
  uniquement les doublons vérifiés.
- **Historique** : consultez les opérations récentes et exportez un rapport.
- **P2P / réseau local** : utilisez l'onglet P2P pour envoyer des fichiers à un
  autre ordinateur TransferDesk.
- **Aide** : vérifiez la version installée, le code de ce PC et les mises à jour.

## Configuration réseau optionnelle

Les transferts locaux fonctionnent sans configuration si les deux ordinateurs
sont sur le même réseau.

Pour le transfert Internet par code, TransferDesk peut utiliser un service de
rendez-vous compatible. Créez `transferdesk-network.json` à la racine de l'application
ou du projet :

```json
{
  "rendezvous_url": "https://rendezvous.example.com"
}
```

Vous pouvez aussi définir la variable d'environnement `TRANSFERDESK_RENDEZVOUS_URL`.
Sans cette configuration, les modes locaux et manuels restent utilisables.

## Compiler depuis le code source

Prérequis : Python 3.12 ou 3.13.

### Windows

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install -r requirements-build.txt
py -m unittest discover -v
py -m PyInstaller --noconfirm --clean TransferDesk.spec
```

Le paquet est créé dans :

```text
dist\TransferDesk
```

### macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-build.txt
python -m unittest discover -v
python -m PyInstaller --noconfirm --clean TransferDesk.spec
```

L'application est créée dans :

```text
dist/TransferDesk.app
```

Pour les détails de signature macOS, voir [BUILD-MACOS.md](BUILD-MACOS.md).

## Lancer en mode développement

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
py transferdesk_qt.py
```

Tests :

```powershell
py -m unittest discover -v
```

Auto-test réseau d'un paquet Windows :

```powershell
.\dist\TransferDesk\TransferDesk.exe --self-test-network
```

## Publier une nouvelle version

La version de l'application est définie dans `transferdesk_version.py`.

Après avoir mis à jour la version et validé les tests, poussez un tag :

```powershell
git tag vX.Y.Z
git push origin vX.Y.Z
```

GitHub Actions construit alors automatiquement les paquets Windows et macOS,
crée les fichiers `.sha256`, puis publie la GitHub Release.

Pour compléter une release déjà créée à la main, ouvrez **Actions > Release >
Run workflow**, indiquez le tag existant, par exemple `v8.0.3`, puis lancez le
workflow. GitHub reconstruit alors les assets Windows/macOS et les remplace dans
la release.

## Liens utiles

- [BUILD-MACOS.md](BUILD-MACOS.md) : construction, signature et validation macOS.
- [RELEASE-CHECKLIST.md](RELEASE-CHECKLIST.md) : recette avant livraison.
- [CHANGELOG.md](CHANGELOG.md) : historique des changements visibles.
- [MIGRATION-QT.md](MIGRATION-QT.md) : notes de migration technique vers Qt.
