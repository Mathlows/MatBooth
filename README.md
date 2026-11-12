# Matbooth

Logiciel de photobooth pour Raspberry Pi, pensé pour la location (anniversaires, mariages, événements).
Matériel visé : Raspberry Pi 5, appareil photo Canon (via gphoto2), imprimante DNP DS-RX1HS, écran tactile en paysage.

## Parcours du client

1. Il choisit un mode : **photo unique** ou **collage 4 photos**.
2. Un compte à rebours de 3 secondes s'affiche sur l'aperçu en direct, puis la photo est prise.
3. Il voit sa photo et choisit un **filtre** et un **cadre**.
4. Il peut **imprimer** (nombre d'exemplaires limité), **terminer sans imprimer**, **recommencer** ou **supprimer**.
   Une photo supprimée n'est conservée nulle part. S'il s'en va sans rien choisir, la photo est gardée.

## Avancement

- [x] Étape 1 : parcours client complet, avec caméra et imprimante simulées
- [ ] Étape 2 : appareil Canon (aperçu en direct, ISO aperçu / prise de vue, reconnexion)
- [ ] Étape 3 : imprimante DNP RX1HS (10x15, bandes 5x15, compteur, alertes)
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

## Où sont les données

Tout ce qui concerne les clients est rangé **hors du code**, dans `~/matbooth-donnees/` :

| Dossier / fichier | Contenu |
|---|---|
| `cadres/` | Cadres proposés dans tous les modes |
| `cadres/unique/`, `cadres/collage4/` | Cadres réservés à un mode |
| `evenements/<nom>/photos/` | Photos gardées, au format d'impression |
| `reglages.json` | Réglages généraux (code d'accès, ISO, événement actif) |
| `matbooth.sqlite` | Événements et leurs réglages, registre des photos et des impressions |

### Ajouter un cadre

Un cadre est une image **PNG de 1800 x 1200 pixels** (10x15 en paysage à 300 dpi), transparente là où la photo
doit apparaître. Elle est simplement posée par-dessus la photo. Il suffit de la déposer dans le bon dossier :
le nom du fichier devient le nom affiché (`fleurs_roses.png` → « Fleurs roses »).

## Réglages techniques (variables d'environnement)

| Variable | Défaut | Rôle |
|---|---|---|
| `MATBOOTH_DONNEES` | `~/matbooth-donnees` | Dossier des données |
| `MATBOOTH_CAMERA` | `simule` | Caméra utilisée |
| `MATBOOTH_IMPRIMANTE` | `simule` | Imprimante utilisée |
| `MATBOOTH_PORT` | `8000` | Port du serveur |

## Développement

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

La police Inter (`web/fonts/`) est distribuée sous licence SIL Open Font License.
