# AutoSD File Manager

AutoSD est une application de bureau Windows et macOS pour classer, copier et
transférer des fichiers. Son interface actuelle utilise **PySide6 / Qt Quick** et
son moteur prend notamment en charge les cartes amovibles, les doublons, le
transfert sur le réseau local et le transfert P2P par Internet.

> Dernière mise à jour de cette documentation : 26 juillet 2026.

## Fonctionnalités

- copie de fichiers entre un dossier source et un dossier de destination ;
- filtrage par extensions, vérification des copies et conservation de l'arborescence ;
- aperçu sélectionnable avant copie, filtres de dates et organisation par date ou type ;
- politiques de conflit : renommer, ignorer, remplacer, garder le plus récent ou décider fichier par fichier ;
- détection et suppression contrôlée des doublons ;
- profils de réglages et historique des opérations ;
- export de l'historique dans un rapport texte ;
- détection et éjection sécurisée des supports amovibles sous Windows et macOS ;
- transfert direct entre appareils sur le réseau local ;
- transfert P2P par Internet avec WebRTC, par serveur de rendez-vous ou par
  échange manuel, STUN et relais TURN facultatif ;
- transfert P2P par socket TCP direct avec code stable du PC receveur, sans
  serveur de rendez-vous ni offre/réponse WebRTC à copier ;
- reprise automatique du transfert socket direct après coupure : les fichiers
  partiels `.autosd-part` sont conservés, l'envoi reprend à l'offset disponible
  et l'intégrité SHA-256 est revérifiée avant finalisation ; l'expéditeur et le
  receveur reconnectent en continu jusqu'au succès ou à l'annulation ;
- progression détaillée des transferts P2P, statut de fin visible et ajout des
  transferts P2P/réseau local dans l'historique ;
- onglet Aide avec version installée, code persistant de ce PC et accès à la
  page de mise à jour configurée par `AUTOSD_UPDATE_URL` ;
- interface claire ou sombre, disponible en français, anglais et hébreu avec
  mise en page droite-à-gauche ;
- fenêtre adaptative aux petits écrans et aux fortes mises à l'échelle Windows,
  avec défilement des pages longues.

## Installation pour le développement

Prérequis : Python 3.12 ou 3.13.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
```

Lancer l'application :

```powershell
py autosd_qt.py
```

Le moteur métier indépendant de l'interface se trouve dans `autosd_core.py`.
L'ancien client Tkinter reste disponible dans `auto sd v5.py` pour compatibilité,
mais il consomme désormais ce moteur partagé et n'est plus inclus dans le paquet
Qt.

## Tests

```powershell
py -m unittest discover -v
```

Test rapide des composants réseau dans une version empaquetée :

```powershell
.\dist\AutoSD-FileManager\AutoSD-FileManager.exe --self-test-network
```

Le workflow `.github/workflows/ci.yml` exécute les tests Python sous Windows et
macOS, puis construit et contrôle les deux paquets PyInstaller. Les artefacts ne
sont pas publiés automatiquement : une livraison signée reste une opération
volontaire.

## Construction de l'application

Installer les dépendances de construction puis utiliser le fichier de
spécification fourni :

```powershell
py -m pip install -r requirements-build.txt
py -m PyInstaller --noconfirm --clean AutoSD-FileManager.spec
```

Sous Windows, le résultat est placé dans `dist/AutoSD-FileManager`. Pour macOS,
voir [BUILD-MACOS.md](BUILD-MACOS.md).

Le binaire Windows reçoit automatiquement les métadonnées de version définies
dans `autosd_version.py`. Le build de référence utilise un dossier `dist-release`
distinct afin d'éviter de mélanger les anciens essais et le livrable validé.
Sous macOS, le fichier de spécification produit un paquet en dossier avant de
l'intégrer à l'application `.app`, afin que ses dépendances Qt puissent être
signées et vérifiées séparément.

Le hook placé dans `pyinstaller_hooks/` limite les modules QML embarqués à ceux
utilisés par l'interface. Il évite notamment d'ajouter Qt WebEngine, la 3D et les
graphiques à une application qui ne les importe pas.
La version majeure de PyInstaller acceptée est bornée dans
`requirements-build.txt`, séparément des dépendances nécessaires à l'exécution.
L'icône source se trouve dans `assets/autosd-icon.svg` ; sa version PNG 1024 px
est utilisée par PyInstaller pour produire les ressources propres à chaque OS.

## Transfert P2P par Internet

Le service de rendez-vous compatible ne transporte pas les fichiers : il
échange seulement les informations de connexion WebRTC. Le secret inclus dans
le code temporaire reste entre les deux appareils et authentifie la connexion.

AutoSD n'embarque plus d'adresse publique ni d'intégration Cloudflare. Pour
utiliser le transfert Internet par code, fournir l'adresse d'un service de
rendez-vous compatible dans `autosd-network.json` :

```json
{
  "rendezvous_url": "https://rendezvous.example.com"
}
```

La variable d'environnement `AUTOSD_RENDEZVOUS_URL` peut aussi fournir cette
adresse sans reconstruire l'application. En l'absence de configuration, le
champ reste vide et le transfert local continue de fonctionner normalement.
Les domaines Cloudflare connus (`workers.dev`, `pages.dev` et domaines
Cloudflare directs) sont refusés par le client.
Le service choisi peut fournir ses propres serveurs STUN/TURN ; sinon AutoSD
utilise uniquement un serveur STUN public de secours pour tenter une connexion
directe.

Le groupe **Connexion manuelle sans serveur** permet aussi de remplacer
entièrement le service de rendez-vous par deux copier-coller :

1. sur le PC expéditeur, choisir les fichiers puis cliquer sur **Créer l’offre** ;
2. copier l’offre affichée vers le PC destinataire par le moyen de son choix ;
3. sur le destinataire, coller l’offre, choisir le dossier de réception et
   cliquer sur **Créer la réponse** ;
4. recopier cette réponse sur l’expéditeur puis cliquer sur
   **Importer la réponse**.

L’offre et la réponse sont signées, limitées en taille et expirent après environ
15 minutes. Elles contiennent les informations réseau WebRTC nécessaires : elles
ne doivent être partagées qu’avec l’autre participant. Les fichiers empruntent
ensuite le même canal WebRTC chiffré que dans le mode par code. Cette méthode
supprime le besoin d’un rendez-vous, mais pas les limites NAT : sans relais TURN,
certains réseaux ou accès CGNAT peuvent encore empêcher la connexion directe.

Les deux appareils doivent utiliser la même version récente d'AutoSD pour le
transfert WebRTC par code. Une ancienne installation peut employer une version
incompatible du protocole P2P, même si elle se trouve sur le même réseau local.
En cas d'échec, AutoSD ouvre le détail de l'erreur et le conserve dans le bouton
**Afficher l'erreur** de la barre d'état ; ce texte peut être copié pour le
diagnostic. Un délai de connexion signale aussi l'absence de relais TURN lorsque
le service configuré n'en fournit pas.

## Transfert sur le réseau local

Les deux appareils doivent exécuter AutoSD sur le même réseau. Dans l'onglet
**P2P**, le destinataire choisit d'abord son dossier de réception. L'expéditeur
recherche ensuite les appareils, sélectionne le destinataire et ses fichiers,
puis lance l'envoi. Le destinataire doit accepter la demande affichée par
AutoSD ; le code d'appairage est alors négocié automatiquement.
La recherche exclut toutes les instances AutoSD exécutées sur le PC expéditeur,
même si elles utilisent un identifiant d'instance différent.

L'expéditeur peut sélectionner des fichiers individuels ou un dossier complet.
Dans ce dernier cas, tous les fichiers sont envoyés récursivement et
l'arborescence du dossier est recréée dans le dossier de réception. L'envoi de
dossiers nécessite la version actuelle sur le PC destinataire ; l'envoi de
fichiers individuels reste compatible avec l'ancien protocole local.

Ce mode local, accessible par **Rechercher** puis **Envoi local**, est à préférer
au transfert Internet par code lorsque les deux PC sont sur le même réseau. Le
client Qt actuel et le client Tkinter `auto sd v5.py` fourni dans ce dépôt
partagent le même module local ; cette garantie ne s'étend pas à un ancien
exécutable installé avant l'ajout du protocole actuel.

## Architecture

| Élément | Rôle |
| --- | --- |
| `autosd_qt.py` | point d'entrée PySide6 et pont entre QML et Python |
| `qml/Main.qml` | interface Qt Quick |
| `autosd_core.py` | moteur de copie, doublons, profils, historique et supports amovibles |
| `auto sd v5.py` | ancienne interface Tkinter, conservée hors du paquet principal |
| `network_transfer.py` | découverte et transferts sur le réseau local |
| `webrtc_transfer.py` | négociation par rendez-vous ou échange manuel et transferts WebRTC par Internet |
| `themes_config.py` | palettes et thèmes de l'ancienne interface |
| `translations.py` | traductions de l'ancienne interface |
| `AutoSD-FileManager.spec` | configuration PyInstaller Windows/macOS |
| `pyinstaller_hooks/` | collecte PyInstaller limitée aux modules QML utilisés |

Les profils et l'historique sont enregistrés en JSON dans le dossier de données
de l'utilisateur, et non dans le dépôt.

La version de l'application est définie une seule fois dans
`autosd_version.py`. Les environnements locaux, caches, anciens dossiers de
construction et configurations propres à une machine sont exclus par
`.gitignore`. Le fichier `autosd-network.example.json` reste le modèle à copier
pour une configuration locale.

## Documentation du projet

- [MIGRATION-QT.md](MIGRATION-QT.md) : migration de Tkinter vers Qt Quick ;
- [BUILD-MACOS.md](BUILD-MACOS.md) : compilation et signature sous macOS ;
- [RELEASE-CHECKLIST.md](RELEASE-CHECKLIST.md) : recette Windows, macOS et réseau
  sur des machines réelles avant livraison ;
- [CHANGELOG.md](CHANGELOG.md) : historique des changements visibles.

## Règle de maintenance

Toute modification fonctionnelle, technique, de configuration, d'installation ou
d'utilisation doit mettre à jour cette documentation dans le même changement.
Toute évolution visible doit également être ajoutée à la section `Non publié` du
fichier `CHANGELOG.md`. Cette règle est aussi enregistrée dans `AGENTS.md` afin
qu'elle soit appliquée lors des prochaines interventions sur le dépôt.
