#!/bin/bash
# Surveillance : relance le serveur Matbooth s'il est bloqué (il tourne mais ne répond plus).
# Un plantage simple est déjà rattrapé par systemd (Restart=always) ; ceci couvre les blocages.
# Lancé chaque minute par matbooth-surveillance.timer.
set -u

PORT="${MATBOOTH_PORT:-8000}"
[ -f /etc/default/matbooth ] && . /etc/default/matbooth
PORT="${MATBOOTH_PORT:-$PORT}"

# Arrêté volontairement, ou en train de (re)démarrer : systemd s'en occupe.
systemctl is-active --quiet matbooth.service || exit 0

# Plusieurs essais espacés : une photo ou une impression en cours peut occuper la borne quelques secondes.
if curl -fsS -o /dev/null --max-time 15 --retry 2 --retry-delay 5 --retry-all-errors "http://127.0.0.1:${PORT}/api/sante"; then
  exit 0
fi

echo "Le serveur Matbooth ne répond plus : redémarrage."
systemctl restart matbooth.service
