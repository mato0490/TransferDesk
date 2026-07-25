# Interface PySide6 / Qt Quick

L'entrée principale est désormais `autosd_qt.py`. Le moteur métier partagé se
trouve dans `autosd_core.py` ; les formats JSON et les modules réseau restent
compatibles. L'interface QML se trouve dans `qml/Main.qml` et est intégrée au
build PyInstaller.

## Développement

```powershell
py -m pip install -r requirements.txt
py autosd_qt.py
py -m unittest discover -v
```

## Build Windows

```powershell
py -m PyInstaller --clean AutoSD-FileManager.spec
dist\AutoSD-FileManager.exe --self-test-network
```

Qt Quick utilise un cadre sans bordure, un thème clair Liquid Glass par défaut et
un thème sombre. Sans composition graphique, les panneaux opaques/translucides
constituent automatiquement le rendu de secours.

L'interface Qt prend en charge la découverte des autres instances AutoSD sur le
réseau local, la sélection des fichiers, l'acceptation côté destinataire et la
négociation automatique du code d'appairage. Le service de découverte est lancé
et arrêté avec l'application.
Les réponses provenant d'une adresse IPv4 appartenant au PC expéditeur sont
filtrées afin que le sélecteur ne propose que les autres ordinateurs.

L'envoi local accepte aussi un dossier complet depuis un sélecteur dédié. Le
module réseau développe récursivement son contenu, signe les chemins relatifs et
recrée l'arborescence chez le destinataire sans autoriser un chemin à sortir du
dossier de réception.

Les erreurs de transfert sont conservées par le pont Qt et présentées dans un
dialogue sélectionnable et copiable. Le dialogue s'ouvre automatiquement à
l'échec et reste accessible depuis la barre d'état ou l'indicateur P2P, ce qui
évite de perdre un diagnostic tronqué dans une notification.

La page P2P expose également une connexion WebRTC manuelle sans serveur de
rendez-vous. Deux zones de texte distinctes servent à coller les informations
reçues et à copier l'offre ou la réponse produite. Le pont Qt conserve
l'expéditeur en arrière-plan pendant l'échange, accepte ensuite la réponse et
reprend le transfert sans bloquer l'interface ; l'annulation globale interrompt
aussi cette attente.

Les pages Qt exposent également l'export de l'historique en rapport texte et
l'éjection sécurisée du support sélectionné comme source.

Avant une copie, Qt affiche désormais le plan détaillé, l'espace requis et
l'espace disponible. L'utilisateur peut exclure des fichiers et résoudre chaque
conflit lorsque la politique « Demander » est choisie. Les filtres de dates,
l'organisation et le nom du sous-dossier sont configurables dans la page de
transfert.

Tous les libellés QML utilisent le catalogue partagé de `translations.py`.
Changer la langue actualise les pages et dialogues en français, anglais ou
hébreu ; cette dernière langue active aussi la mise en page droite-à-gauche.

Le pont Qt importe directement `autosd_core`, sans chargement dynamique du
fichier historique portant un espace dans son nom. Le client Tkinter réutilise
les classes extraites, tandis que PyInstaller n'embarque plus ce client ni ses
dépendances d'interface.

Les pages longues Transfert et P2P sont placées dans des vues défilables. La
taille initiale et les dimensions minimales de la fenêtre tiennent compte de la
zone de bureau disponible ; les groupes de commandes se replient lorsque la
largeur diminue. Cette règle évite de masquer des options sur un écran 1280×720
avec une mise à l'échelle Windows de 150 %.
