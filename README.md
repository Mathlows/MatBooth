# Matbooth

Logiciel de photobooth pour Raspberry Pi, pensé pour la location (anniversaires, mariages, événements).
Matériel visé : Raspberry Pi 5, appareil photo Canon (via gphoto2), imprimante DNP DS-RX1HS, écran tactile en paysage.

## Parcours du client

1. Il choisit un mode : **photo unique**, **collage 4 photos** ou **bandes photo** (4 photos sur une bande 5x15 ;
   chaque impression donne deux bandes identiques, découpées par l'imprimante).
2. Un compte à rebours de 3 secondes s'affiche sur l'aperçu en direct, puis la photo est prise.
3. Il voit sa photo et choisit un **filtre** et un **cadre**.
4. Il peut **imprimer** (nombre d'exemplaires limité), **terminer sans imprimer**, **recommencer** ou **supprimer**.
   Une photo supprimée n'est conservée nulle part. S'il s'en va sans rien choisir, la photo est gardée.

## Avancement

- [x] Étape 1 : parcours client complet, avec caméra et imprimante simulées
- [x] Étape 2 : appareil Canon (aperçu en direct, ISO aperçu / prise de vue, reconnexion)
- [x] Étape 3 : imprimante DNP RX1HS (10x15, bandes 5x15, compteur, alertes)
- [x] Étape 4 : administration (accès caché + code PIN, profils d'événement, cadres, réglages couleur, galerie)
- [ ] Étape 5 : envoi des photos (QR code, e-mail, clé USB), activable par l'admin
- [ ] Étape 6 : mise en service (plein écran, démarrage automatique, surveillance)
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
Sur la borne, ajouter `?kiosque=1` à l'adresse pour masquer le curseur de la souris.

## Administration

- **Sur la borne** : appui long de 3 secondes dans le coin en haut à gauche de l'écran d'accueil.
- **Depuis un téléphone** sur le même réseau Wi-Fi : `http://<adresse-du-pi>:8000/admin`.

Code par défaut : **1234** (à changer dans l'onglet Réglages ; 5 erreurs bloquent l'accès 1 minute).

| Onglet | Contenu |
|---|---|
| Tableau de bord | Photos prises, impressions, espace libre, état de l'appareil et de l'imprimante |
| Événements | Créer, activer, supprimer un événement ; pour chacun : nom affiché, modes, compte à rebours, exemplaires max par photo, limite d'impressions, filtres et cadres proposés, couleurs (luminosité, contraste, saturation) avec aperçu en direct |
| Cadres | Ajouter un PNG (pour tous les modes ou un seul), supprimer |
| Photos | Galerie par événement, photo en grand, téléchargement (une photo ou tout en ZIP), suppression |
| Réglages | ISO de l'aperçu et de la prise de vue, code d'accès |

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
| `reglages.json` | Réglages généraux (code d'accès, ISO, événement actif) |
| `matbooth.sqlite` | Événements et leurs réglages, registre des photos et des impressions |

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

## Développement

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

La police Inter (`web/fonts/`) est distribuée sous licence SIL Open Font License.
