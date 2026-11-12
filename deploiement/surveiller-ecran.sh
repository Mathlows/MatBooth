#!/bin/bash
# Surveillance de l'écran : relance Chromium si la page de la borne ne donne plus signe de vie
# (onglet planté « Aïe, aïe, aïe », page figée). Un Chromium fermé ou planté en entier est déjà
# relancé par matbooth-ecran.service ; ceci couvre le reste.
# Lancé toutes les 30 secondes par matbooth-ecran-surveillance.timer (service utilisateur).
set -u

PORT="${MATBOOTH_PORT:-8000}"
SILENCE_MAX=45   # s sans signe de vie avant relance (la page en envoie toutes les 10 s)
DEMARRAGE=90     # s laissées à l'écran pour démarrer

# Écran arrêté volontairement (« Quitter le plein écran ») : rien à faire.
systemctl --user is-active --quiet matbooth-ecran.service || exit 0

debut=$(systemctl --user show -p ActiveEnterTimestampMonotonic --value matbooth-ecran.service)
maintenant=$(awk '{printf "%d", $1 * 1000000}' /proc/uptime)
[ $(( (maintenant - debut) / 1000000 )) -lt "${DEMARRAGE}" ] && exit 0

# Serveur indisponible : la page l'attend d'elle-même, et sa surveillance s'en occupe.
etat=$(curl -fsS --max-time 5 "http://127.0.0.1:${PORT}/api/ecran") || exit 0

if python3 -c '
import json, sys
etat = json.loads(sys.argv[1])
age, silence_max, demarrage = etat["age"], float(sys.argv[2]), float(sys.argv[3])
muette = age > silence_max if age is not None else etat["depuis_demarrage"] > demarrage
sys.exit(0 if muette else 1)
' "${etat}" "${SILENCE_MAX}" "${DEMARRAGE}"; then
  echo "La page de la borne ne répond plus (${etat}) : relance de l'écran."
  systemctl --user restart matbooth-ecran.service
fi
