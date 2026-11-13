# Matbooth

Logiciel de photobooth pour Raspberry Pi, pensé pour la location (anniversaires, mariages, événements).
Matériel visé : Raspberry Pi 5, appareil photo Canon (via gphoto2), imprimante DNP DS-RX1HS, écran tactile en paysage.

## Parcours du client

1. Il choisit un mode : **photo unique**, **collage 4 photos** ou **bandes photo** (4 photos sur une bande 5x15 ;
   chaque impression donne deux bandes identiques, découpées par l'imprimante).
2. Un compte à rebours de 3 secondes s'affiche sur l'aperçu en direct, puis la photo est prise.
3. Il voit sa photo et choisit un **filtre** et un **cadre**.
4. Selon l'événement, il peut **récupérer sa photo sur son téléphone** (QR code) ou **la recevoir par e-mail**.
5. Il peut **imprimer** (nombre d'exemplaires limité), **terminer sans imprimer**, **recommencer** ou **supprimer**.
   Une photo supprimée n'est conservée nulle part (même si elle avait déjà été partagée : lien et e-mails
   en attente disparaissent avec elle). S'il s'en va sans rien choisir, la photo est gardée.

## Avancement

- [x] Étape 1 : parcours client complet, avec caméra et imprimante simulées
- [x] Étape 2 : appareil Canon (aperçu en direct, ISO aperçu / prise de vue, reconnexion)
- [x] Étape 3 : imprimante DNP RX1HS (10x15, bandes 5x15, compteur, alertes)
- [x] Étape 4 : administration (accès caché + code PIN, profils d'événement, cadres, réglages couleur, galerie)
- [x] Étape 5 : envoi des photos (QR code, e-mail, clé USB), activable par l'admin
- [x] Étape 6 : mise en service (plein écran, démarrage automatique, surveillance)
- [ ] Plus tard : choix de la langue

## Essayer

Sur le Raspberry Pi (ou n'importe quel ordinateur) :

```bash
git clone https://github.com/Mathlows/Matbooth.git ~/matbooth
cd ~/matbooth
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m matbooth
```

Puis ouvrir <http://localhost:8000> dans le navigateur.
Sur la borne, ajouter `?kiosque=1` à l'adresse pour masquer le curseur de la souris
(c'est ce que fait la mise en service ci-dessous).

## Mise en service sur la borne

Une seule commande installe tout (à relancer sans risque après une mise à jour du code) :

```bash
cd ~/matbooth
sudo ./deploiement/installer.sh
sudo reboot
```

Au démarrage, sans clavier ni souris :

| Élément | Rôle |
|---|---|
| `matbooth.service` (système) | Le serveur, avec l'appareil Canon et l'imprimante DNP. Relancé en 2 s s'il plante. |
| `matbooth-surveillance.timer` (système) | Chaque minute, vérifie que le serveur répond ; s'il est bloqué, le relance (en moins d'une minute). |
| `matbooth-ecran.service` (utilisateur) | Chromium en plein écran sur `http://localhost:8000/?kiosque=1`, relancé s'il se ferme ou plante. |
| `matbooth-ecran-surveillance.timer` (utilisateur) | Si la page plante (« Aïe aïe aïe ») ou se fige, relance l'écran (en moins d'une minute). |
| `matbooth-bouton.service` (système) | Bouton physique sur GPIO3 (broche 5, avec la masse) : **appui bref = redémarrer**, **maintenu 3 s = éteindre**. Borne éteinte : un appui la rallume. Ignoré les 30 s qui suivent le démarrage. |

Le script installe aussi les paquets (gphoto2, Gutenprint), l'environnement Python, la file d'impression DNP
si l'imprimante est branchée, désactive la mise en veille de l'écran, et autorise l'administration à
redémarrer ou éteindre la borne.

**Dans l'administration, onglet Réglages › Borne** : quitter le plein écran (retour au bureau, pour la
maintenance), relancer l'écran de la borne, redémarrer, **éteindre** (à faire avant de couper le courant :
une coupure brutale peut abîmer la carte mémoire). Depuis le bureau, le menu propose aussi « Borne photo ».

Pour suivre ce qui se passe :

```bash
journalctl -u matbooth -f                  # serveur (photos, impressions, e-mails, clé USB)
journalctl --user -u matbooth-ecran -f     # écran
sudo systemctl restart matbooth            # relancer le serveur
```

Réglages propres à la borne (port, type d'appareil…) : `/etc/default/matbooth`, puis `sudo systemctl restart matbooth`.
**Branchement du bouton** : un fil sur la broche **5** (GPIO3), l'autre sur la broche **6** (masse), sans
résistance. Si le bouton a trois bornes, utiliser **COM** (vers la masse) et **NO** (vers GPIO3), jamais **NC** :
branché sur NC, le bouton paraît enfoncé en permanence ; il reste alors inactif et le journal le signale.

Pour essayer le bouton sans rien éteindre :
`sudo systemctl stop matbooth-bouton && sudo env MATBOOTH_BOUTON_ESSAI=1 PYTHONPATH=$PWD python3 -m matbooth.bouton`
(puis `sudo systemctl start matbooth-bouton`).

## Administration

- **Sur la borne** : appui long de 3 secondes dans le coin en haut à gauche de l'écran d'accueil.
  Les champs de texte (nom d'événement, réglages e-mail) se remplissent plus facilement depuis un téléphone.
- **Depuis un téléphone** sur le même réseau Wi-Fi : `http://<adresse-du-pi>:8000/admin`.

Code par défaut : **1234** (à changer dans l'onglet Réglages ; 5 erreurs bloquent l'accès 5 minutes).

| Onglet | Contenu |
|---|---|
| Tableau de bord | Photos prises, impressions, espace libre, état de l'appareil et de l'imprimante |
| Événements | Créer, activer, supprimer un événement ; pour chacun : nom affiché, modes, compte à rebours, exemplaires max par photo, limite d'impressions, filtres et cadres proposés, couleurs (luminosité, contraste, saturation) avec aperçu en direct |
| Cadres | Ajouter un PNG (pour tous les modes ou un seul), supprimer |
| Photos | Galerie par événement, photo en grand, téléchargement (une photo ou tout en ZIP), suppression |
| Réglages | ISO de l'aperçu et de la prise de vue, réseau (QR code), serveur e-mail, code d'accès |

## Envoi des photos aux clients

Trois options, à activer **pour chaque événement** (onglet Événements, « Envoi des photos aux clients ») :

- **QR code** : sur l'écran de résultat, le client scanne un QR code et télécharge sa photo depuis la borne,
  par le réseau local (pas besoin d'internet). Chaque photo a son propre lien secret. Son téléphone doit être
  sur le même réseau que la borne, et un premier QR code permet de rejoindre ce réseau en un geste :
  - **Wi-Fi de la borne (recommandé)** : Réglages › Wi-Fi de la borne. La borne crée son propre réseau
    (« Photobooth », ouvert ou avec mot de passe) et y a l'adresse 10.42.0.1. Fonctionne partout. Le Pi n'ayant
    qu'une puce Wi-Fi, il n'a plus internet par Wi-Fi pendant ce temps : les e-mails partent quand on le
    désactive (ou immédiatement avec un câble Ethernet). Il reste actif après un redémarrage.
  - **Wi-Fi existant** (box, salle) : indiquez-le dans Réglages › Wi-Fi existant. Attention, beaucoup de box et
    de Wi-Fi de salles isolent les appareils entre eux : le téléphone ne peut alors pas joindre la borne.
- **E-mail** : le client tape son adresse sur un clavier à l'écran. Les e-mails attendent dans la borne et partent
  **dès qu'internet est disponible** (nouvel essai de plus en plus espacé, jusqu'à toutes les 30 minutes ;
  une adresse refusée par le serveur est abandonnée). Réglages › E-mail : serveur SMTP, identifiant, objet et
  message (`{evenement}` est remplacé par le nom de l'événement), et un bouton d'e-mail de test.
  Avec Gmail, il faut un [mot de passe d'application](https://myaccount.google.com/apppasswords).
  Le bouton n'est proposé aux clients que si le serveur est réglé.
- **Clé USB** : les photos gardées sont copiées toutes les 10 secondes sur toute clé USB branchée, dans
  `Matbooth/<événement>/`. On peut la brancher en cours de route : ce qui manque est copié. Chaque fichier
  est écrit sur la clé immédiatement, on peut la retirer sans « éjecter » (en évitant de le faire pendant une copie).

### Galerie pour les invités

Option par événement (onglet Événements › « Galerie pour les invités ») : un bouton **Galerie des photos**
apparaît sur l'accueil. Les invités y retrouvent les photos de l'événement et peuvent :

- les **réimprimer**, au plus « exemplaires max par photo » au total pour une même photo (impressions déjà
  faites comprises) et dans la limite d'impressions de l'événement ;
- les récupérer **sur leur téléphone** ou **par e-mail**, si ces options sont cochées pour l'événement ;
- les **effacer**, seulement si l'option « Permettre aux invités d'effacer des photos » est cochée (désactivée
  par défaut : n'importe quel invité pourrait effacer n'importe quelle photo). Une photo effacée disparaît
  de la borne, des e-mails en attente, du lien QR et de la clé USB si elle est branchée.

La galerie n'est accessible que **sur l'écran de la borne** : les téléphones du réseau (Wi-Fi de la borne
compris) ne peuvent ni la voir ni effacer de photo. Retour automatique à l'accueil après le délai d'inactivité.

Une photo n'est envoyée par e-mail ou copiée sur clé USB **qu'une fois la séance du client terminée** : tant qu'il
est devant l'écran, il peut encore la supprimer, et une photo supprimée ne quitte jamais la borne.
Le tableau de bord indique les e-mails envoyés / en attente (avec la dernière erreur) et l'état de la clé USB.

## Brancher l'appareil photo Canon

Testé avec un **Canon EOS 200D** en USB. Sur le Raspberry Pi :

```bash
sudo apt install gphoto2          # outil de diagnostic (la bibliothèque Python est dans requirements.txt)
# Le bureau monte l'appareil comme un disque et le bloque : on désactive ce montage automatique.
systemctl --user mask --now gvfs-gphoto2-volume-monitor.service
pkill gvfsd-gphoto2
gphoto2 --auto-detect             # doit afficher le modèle de l'appareil
MATBOOTH_CAMERA=gphoto2 .venv/bin/python -m matbooth
```

Sur l'appareil, de préférence :

- molette en **M** ou **Tv** (en mode automatique vert, l'ISO ne peut pas être changé à distance) ;
- objectif en **MF** (mise au point manuelle) : en autofocus, une pièce sombre peut faire échouer la prise ;
- **Arrêt auto : Désactivé** dans les menus, et idéalement une batterie factice sur secteur.

Si l'appareil se met quand même en veille ou est débranché, l'aperçu affiche « Connexion à l'appareil photo… »
et Matbooth le reconnecte tout seul dès qu'il est rallumé (pas besoin de redémarrer le logiciel).
Les photos passent par la mémoire vive de l'appareil : rien n'est enregistré sur sa carte SD.

### ISO de l'aperçu et de la prise de vue

Avec un flash, on photographie à ISO bas, ce qui rendrait l'aperçu presque noir. On peut donc régler un ISO
différent pour l'aperçu, dans l'administration (onglet **Réglages**). La liste propose les valeurs
acceptées par l'appareil branché ; « Réglage de l'appareil » garde celui choisi sur l'appareil.

## Brancher l'imprimante DNP DS-RX1HS

Matbooth imprime via CUPS avec le pilote libre Gutenprint, et lit directement l'état de l'imprimante
(papier restant, compteur, erreurs) toutes les 20 secondes. À faire une seule fois sur le Raspberry Pi,
imprimante branchée et allumée :

```bash
sudo apt install printer-driver-gutenprint
sudo lpinfo -v | grep dnp      # donne l'adresse de l'imprimante, ex. gutenprint53+usb://dnp-dsrx1/CB2D59200123
sudo lpadmin -p DNP_RX1 -E -D "DNP DS-RX1HS" -m gutenprint.5.3://dnp-dsrx1/expert \
     -v 'gutenprint53+usb://dnp-dsrx1/NUMERO_DE_SERIE'
# 10x15 par défaut, et réessayer au lieu de bloquer la file en cas de bourrage ou de papier vide
sudo lpadmin -p DNP_RX1 -o PageSize=w288h432 -o printer-error-policy=retry-current-job
# Lire l'état de l'imprimante sans être administrateur (prend effet après redémarrage ou reconnexion)
sudo usermod -aG lp $USER
```

Puis lancer avec `MATBOOTH_IMPRIMANTE=dnp`.

- **10x15** : la photo est imprimée bord à bord. Le massicot peut mordre d'environ 1 mm : dans les cadres,
  garder textes et logos à **au moins 3 mm (40 pixels) du bord**.
- **Bandes 5x15** : deux bandes identiques sur une feuille 10x15, coupées au milieu par l'imprimante
  (une impression = un tirage de papier = deux bandes).
- **Alertes** (tableau de bord de l'administration, et bouton Imprimer désactivé si besoin) : imprimante
  éteinte ou débranchée, capot ouvert, bourrage, papier ou ruban terminé, papier bientôt terminé
  (moins de 30 tirages), impressions en attente. Le tableau de bord affiche aussi le papier restant et le
  compteur total de l'imprimante.

## Où sont les données

Tout ce qui concerne les clients est rangé **hors du code**, dans `~/matbooth-donnees/` :

| Dossier / fichier | Contenu |
|---|---|
| `cadres/` | Cadres proposés dans tous les modes |
| `cadres/unique/`, `cadres/collage4/` | Cadres réservés à un mode |
| `cadres/bandes/` | Cadres des bandes 5x15 (les cadres communs 10x15 ne s'y appliquent pas) |
| `evenements/<nom>/photos/` | Photos gardées, au format d'impression |
| `reglages.json` | Réglages généraux (code d'accès, ISO, événement actif, réseau, serveur e-mail et son mot de passe) |
| `matbooth.sqlite` | Événements et leurs réglages, registre des photos et des impressions, e-mails en attente, liens QR |

### Ajouter un cadre

Un cadre est une image **PNG de 1800 x 1200 pixels** (10x15 en paysage à 300 dpi), transparente là où la photo
doit apparaître. Elle est simplement posée par-dessus la photo. Il suffit de la déposer dans le bon dossier :
le nom du fichier devient le nom affiché (`fleurs_roses.png` → « Fleurs roses »).

Un cadre de **bande** est un PNG de **600 x 1800 pixels** (5x15 en portrait), dans `cadres/bandes/`.
Les quatre photos occupent le haut de la bande ; le bas est libre pour un logo ou un texte.

## Réglages techniques (variables d'environnement)

| Variable | Défaut | Rôle |
|---|---|---|
| `MATBOOTH_DONNEES` | `~/matbooth-donnees` | Dossier des données |
| `MATBOOTH_CAMERA` | `simule` | Caméra utilisée : `simule` ou `gphoto2` |
| `MATBOOTH_IMPRIMANTE` | `simule` | Imprimante utilisée : `simule` ou `dnp` |
| `MATBOOTH_FILE_IMPRESSION` | `DNP_RX1` | Nom de la file CUPS de l'imprimante DNP |
| `MATBOOTH_PORT` | `8000` | Port du serveur |
| `MATBOOTH_ECRAN_DISTANT` | (vide) | `1` : l'interface de l'écran répond aussi aux autres ordinateurs du réseau (pour développer ; **jamais en événement**). Par défaut, seule la borne elle-même peut l'utiliser |

## Développement

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

La police Inter (`web/fonts/`) est distribuée sous licence SIL Open Font License.
