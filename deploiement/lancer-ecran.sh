#!/bin/bash
# Écran de la borne : Chromium en plein écran sur l'interface Matbooth.
# Lancé par le service utilisateur matbooth-ecran.service (relancé automatiquement s'il se ferme).
set -u

PORT="${MATBOOTH_PORT:-8000}"
ADRESSE="http://localhost:${PORT}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
PROFIL="${HOME}/.local/share/matbooth/chromium"
# Messages de Chromium en français (ex. si une page plante).
export LANGUAGE=fr

# 1. Attendre le bureau (au démarrage, ce service peut partir avant lui).
for _ in $(seq 1 180); do
  [ -S "${XDG_RUNTIME_DIR}/${WAYLAND_DISPLAY}" ] && break
  sleep 1
done
if [ ! -S "${XDG_RUNTIME_DIR}/${WAYLAND_DISPLAY}" ]; then
  echo "Bureau introuvable (${XDG_RUNTIME_DIR}/${WAYLAND_DISPLAY}) : nouvel essai bientôt." >&2
  exit 1
fi

# 2. Attendre le serveur Matbooth, pour ne pas afficher une page d'erreur.
for _ in $(seq 1 180); do
  curl -fsS -o /dev/null --max-time 3 "${ADRESSE}/api/sante" && break
  sleep 1
done

# 3. Après un arrêt brutal, Chromium proposerait de « restaurer les pages » : on l'en empêche.
mkdir -p "${PROFIL}"
PREFERENCES="${PROFIL}/Default/Preferences"
if [ -f "${PREFERENCES}" ]; then
  sed -i -e 's/"exited_cleanly":false/"exited_cleanly":true/' \
         -e 's/"exit_type":"[^"]*"/"exit_type":"Normal"/' "${PREFERENCES}"
fi

exec chromium \
  --kiosk \
  --ozone-platform=wayland \
  --user-data-dir="${PROFIL}" \
  --no-first-run \
  --noerrdialogs \
  --disable-infobars \
  --hide-crash-restore-bubble \
  --disable-session-crashed-bubble \
  --disable-features=Translate,TranslateUI \
  --disable-pinch \
  --overscroll-history-navigation=0 \
  --check-for-update-interval=31536000 \
  --password-store=basic \
  --lang=fr \
  "${ADRESSE}/?kiosque=1"
