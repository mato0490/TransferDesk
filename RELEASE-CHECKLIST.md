# Recette avant livraison

Cette checklist complète les tests automatisés. Elle doit être exécutée sur les
machines et réseaux réellement visés avant de publier une version. Conserver les
résultats, les journaux utiles et les numéros de version avec la livraison.

## Identification

- [ ] version testée : `__________` ;
- [ ] commit ou archive source : `__________` ;
- [ ] Windows et architecture : `__________` ;
- [ ] macOS, modèle et architecture : `__________` ;
- [ ] URL de rendez-vous utilisée : `__________` ;
- [ ] TURN activé : oui / non.

## Contrôles automatisés

- [ ] la CI est verte pour les tests Python Windows et macOS ;
- [ ] les paquets Windows et macOS sont construits sans erreur ;
- [ ] le tag `vX.Y.Z` a créé une GitHub Release contenant les deux archives
  `AutoSD-FileManager-*-vX.Y.Z.zip` et leurs fichiers `.sha256` ;
- [ ] `AutoSD-FileManager.exe --self-test-network` réussit ;
- [ ] `codesign --verify --deep --strict` réussit sur le paquet macOS ;
- [ ] l'auto-test réseau du binaire contenu dans le paquet macOS réussit.

## Fonctions locales sur chaque système

- [ ] l'application démarre et se ferme sans processus résiduel ;
- [ ] une copie avec aperçu conserve l'arborescence et les dates attendues ;
- [ ] les politiques renommer, ignorer, remplacer et garder le plus récent sont
  contrôlées avec de vrais conflits ;
- [ ] l'annulation d'une copie ne laisse aucun fichier partiel ;
- [ ] l'analyse de doublons ne supprime jamais un original non revérifié ;
- [ ] un profil peut être créé, rechargé et supprimé ;
- [ ] l'historique peut être exporté puis relu ;
- [ ] l'onglet Aide détecte une publication plus récente, demande confirmation
  avant téléchargement, valide le checksum, puis demande confirmation avant
  installation ;
- [ ] une carte ou clé USB reconnue comme amovible peut être éjectée proprement ;
- [ ] le français, l'anglais et l'hébreu sont lisibles ; l'hébreu est bien en RTL.

## Réseau local, deux machines

Utiliser deux machines distinctes sur le même sous-réseau, d'abord sans VPN. Pour
chaque transfert, comparer les tailles et les empreintes SHA-256 à la source et à
la destination.

- [ ] A découvre B et B découvre A ;
- [ ] A envoie plusieurs petits fichiers à B ;
- [ ] A envoie un dossier imbriqué à B et toute son arborescence est conservée ;
- [ ] B envoie un fichier d'au moins 4 Gio à A ;
- [ ] le refus côté destinataire est correctement signalé à l'expéditeur ;
- [ ] sans URL de rendez-vous, l'expéditeur peut créer une offre manuelle, le
  destinataire peut l'importer et produire une réponse, puis l'expéditeur peut
  importer cette réponse et terminer un transfert chiffré ;
- [ ] une offre manuelle expirée, tronquée ou modifiée est refusée avec un
  message d'erreur visible et copiable ;
- [ ] une annulation en cours supprime le fichier partiel ;
- [ ] le même envoi réussit immédiatement après cette annulation ;
- [ ] un nom déjà présent est conservé et le fichier reçu est renommé ;
- [ ] la découverte et le transfert sont retestés avec le pare-feu habituel actif.

## Internet WebRTC

Les deux machines doivent être placées sur deux accès Internet distincts, par
exemple une connexion fixe et un partage mobile. Le service de rendez-vous
compatible configuré ne doit recevoir que la signalisation, jamais le contenu
des fichiers. Aucun service Cloudflare n'est fourni ni configuré par le projet.

- [ ] le destinataire crée un code et l'expéditeur rejoint la salle ;
- [ ] un transfert direct réussit entre les deux réseaux ;
- [ ] un code modifié ou expiré est refusé ;
- [ ] une annulation en cours ne laisse aucun fichier partiel ;
- [ ] un second transfert réussit après l'annulation ;
- [ ] un fichier d'au moins 4 Gio est reçu avec la même empreinte SHA-256 ;
- [ ] sur un réseau qui bloque le chemin direct, le transfert échoue proprement
  sans TURN, puis réussit après activation de TURN ;
- [ ] aucun secret TURN, jeton de rôle ou code d'appairage n'apparaît dans les
  captures, journaux ou rapports destinés à être conservés.

## Décision de livraison

- [ ] les anomalies bloquantes sont corrigées et retestées ;
- [ ] les limitations acceptées sont consignées dans les notes de version ;
- [ ] le paquet Windows provient de la source identifiée ci-dessus ;
- [ ] le paquet macOS est signé Developer ID et notarisé pour une diffusion hors
  développement ;
- [ ] la documentation correspond exactement au comportement livré.

Résultat : accepté / refusé. Responsable et date : `________________________`.
