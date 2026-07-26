# Journal des modifications

Ce fichier recense les changements importants du projet. Les nouvelles entrées
sont ajoutées dans la section **Non publié**, puis regroupées par version lors
d'une livraison.

## Non publié

### Fonctionnalités

- renommage public du projet en TransferDesk, avec suppression des
  références utilisateur à l'ancien nom et aux cartes mémoire spécialisées dans
  l'interface, les paquets, la CI et la documentation ;
- préparation du nouveau slug GitHub `mato0490/transferdesk` pour les liens
  publics et le vérificateur de mises à jour ;
- refonte de `README.md` en guide utilisateur centré sur le téléchargement,
  l'installation Windows/macOS, les mises à jour et la compilation ;
- ajout d'un onglet Aide affichant la version installée, le code persistant du
  PC et l'état de vérification des mises à jour ;
- ajout d'un vérificateur de mises à jour basé sur GitHub Releases avec
  comparaison de version, sélection de l'archive Windows/macOS, double
  confirmation utilisateur et vérification SHA-256 avant installation ;
- ajout d'un workflow GitHub Release déclenché par les tags `vX.Y.Z`, générant
  les archives Windows/macOS et leurs fichiers `.sha256` ;
- ajout d'un bloc de statistiques P2P indiquant progression, volume transféré,
  vitesse, fichier courant et état de fin ;
- ajout d'un mode P2P par socket TCP direct : le receveur lance une écoute,
  partage un code `123456@adresse`, et l'expéditeur envoie fichiers ou dossiers
  avec suivi continu de la progression ;
- ajout de la reprise automatique des transferts socket directs après coupure :
  conservation des fichiers partiels `.transferdesk-part`, négociation des offsets,
  reconnexion continue côté expéditeur et réécoute continue côté receveur
  jusqu'au succès ou à l'annulation, avec vérification SHA-256 finale ;
- ajout des transferts P2P et réseau local dans l'historique des opérations ;
- ajout d'un code appareil persistant utilisé par la découverte locale afin de
  stabiliser l'identité d'un PC entre deux lancements ;
- ajout d'une connexion WebRTC manuelle sans serveur de rendez-vous : échange
  signé et expirable d'une offre puis d'une réponse dans deux zones de texte,
  avant transfert direct chiffré entre les deux PC ;
- ajout d'une icône TransferDesk propre aux paquets Windows et macOS ;
- finalisation du transfert réseau local dans l'interface Qt : découverte et
  sélection d'un appareil, choix des fichiers, demande d'autorisation côté
  destinataire et négociation automatique du code d'appairage ;
- ajout de l'envoi local d'un dossier complet avec parcours récursif et
  conservation sécurisée de son arborescence chez le destinataire ;
- ajout dans l'interface Qt de l'export de l'historique et de l'éjection
  sécurisée du support source ;
- ajout d'un aperçu de copie sélectionnable avec contrôle de l'espace disque et
  résolution fichier par fichier des conflits ;
- exposition dans Qt des filtres de dates, de l'organisation, du sous-dossier et
  de toutes les politiques de conflit du moteur ;
- internationalisation complète de l'interface Qt en français, anglais et
  hébreu, avec actualisation immédiate et mise en page RTL.

### Corrections

- ajout d'un déclenchement manuel au workflow Release afin de reconstruire et
  compléter une release existante avec les assets Windows/macOS depuis GitHub
  Actions ;
- génération d'une icône macOS `.icns` dans le workflow Release pour fiabiliser
  le paquet `TransferDesk.app` produit par PyInstaller ;
- correction des versions d'actions GitHub utilisées par la CI et le workflow
  Release afin de relancer correctement la publication des paquets ;
- correction du README pour retirer la mention de date documentaire, lisser les
  titres et corriger la présentation du support amovible ;
- priorisation du mode P2P manuel sans serveur en haut de l'onglet P2P ;
- correction du bouton Effacer du P2P manuel, qui nettoie maintenant la saisie,
  les fichiers, le dossier, le code et la sortie générée quand aucun transfert
  n'est actif ;
- adaptation de la fenêtre à la zone de bureau disponible et ajout du
  défilement/repli des pages Transfert et P2P afin que les options ne soient plus
  coupées sur les petits écrans ou avec une mise à l'échelle de 150 %.
- conservation et affichage automatique du détail des erreurs de transfert dans
  un dialogue copiable, également accessible depuis la barre d'état et
  l'indicateur P2P ; remplacement des erreurs vides par un diagnostic explicite ;
- enrichissement des délais WebRTC avec les causes à vérifier sur le réseau
  local et l'indication de l'absence de relais TURN.
- exclusion des autres instances TransferDesk lancées sur le PC expéditeur dans la
  liste des appareils découverts pour un envoi local.

### Maintenance

- passage de la version canonique à `8.0.4` pour déclencher une nouvelle
  publication GitHub Actions complète avec asset macOS ;
- centralisation de la version de l'application dans `transferdesk_version.py` ;
- extraction du moteur métier dans `transferdesk_core.py`, import direct depuis Qt et
  retrait du client Tkinter du paquet PyInstaller ;
- retrait complet de l'intégration Cloudflare, de l'adresse publique embarquée,
  du Worker, de ses outils de déploiement et de ses tests ; le P2P WebRTC reste
  configurable avec un service de rendez-vous compatible fourni séparément et
  les domaines Cloudflare connus sont explicitement refusés par le client ;
- ajout automatique des métadonnées de version Windows au binaire PyInstaller ;
- ajout d'une intégration continue Windows/macOS pour les tests Python, la
  construction PyInstaller et l'auto-test des paquets ;
- passage du paquet macOS à une construction en dossier, avec métadonnées de
  version, signature locale et vérification automatisée du bundle ;
- réduction du paquet PyInstaller par une collecte QML ciblée excluant notamment
  WebEngine, la 3D, les graphiques et le multimédia non utilisés ;
- séparation des dépendances de construction et bornage de PyInstaller à sa
  version majeure 6 ; tests CI exécutés avec Python 3.12 et 3.13 ;
- ajout d'un `.gitignore` pour les environnements, caches, configurations locales
  et artefacts de construction ;
- ajout de délais maximaux aux étapes de négociation du test WebRTC afin qu'une
  panne réseau échoue explicitement au lieu de bloquer toute la suite ;
- ajout de tests de chargement QML et du raccordement de la découverte locale ;
- ajout d'un test d'intégration vérifiant le nettoyage puis la relance réussie
  d'un transfert local volumineux interrompu.

### Documentation

- traduction de `README.md` en anglais pour la page GitHub publique ;
- ajout d'une documentation centrale du projet ;
- ajout d'une checklist de recette couvrant Windows, macOS, le réseau local,
  WebRTC direct, TURN, l'intégrité des gros fichiers et le RTL ;
- ajout d'une règle permanente imposant sa mise à jour avec le code.

## État documenté au 20 juillet 2026

- interface principale migrée vers PySide6 et Qt Quick ;
- moteur de copie et de gestion des doublons disponible ;
- transferts locaux et P2P WebRTC disponibles ;
- transfert P2P configurable sans service Cloudflare ni adresse publique embarquée ;
- construction Windows et macOS avec PyInstaller.
