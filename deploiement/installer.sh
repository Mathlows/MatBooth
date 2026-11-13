#!/bin/bash
# Mise en service de Matbooth sur le Raspberry Pi (à relancer sans risque après une mise à jour).
#
#   cd ~/matbooth && sudo ./deploiement/installer.sh
#
# Après installation, au démarrage :
#   - le serveur (matbooth.service) démarre et redémarre tout seul en cas de plantage ;
#   - une surveillance le relance s'il ne répond plus (matbooth-surveillance.timer) ;
#   - l'écran (matbooth-ecran.service) ouvre Chromium en plein écran et le relance s'il se ferme ;
#   - une surveillance relance l'écran si la page plante (matbooth-ecran-surveillance.timer) ;
#   - l'écran ne se met jamais en veille.
set -euo pipefail

if [ "$(id -u)" != 0 ]; then
  echo "À lancer avec sudo : sudo $0" >&2
  exit 1
fi
UTILISATEUR="${SUDO_USER:-}"
if [ -z "${UTILISATEUR}" ] || [ "${UTILISATEUR}" = root ]; then
  echo "À lancer avec sudo depuis le compte qui affiche la borne (ex. : mat)." >&2
  exit 1
fi
RACINE="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOIEMENT="${RACINE}/deploiement"
ID_UTILISATEUR="$(id -u "${UTILISATEUR}")"
MAISON="$(getent passwd "${UTILISATEUR}" | cut -d: -f6)"
en_utilisateur() {
  sudo -u "${UTILISATEUR}" XDG_RUNTIME_DIR="/run/user/${ID_UTILISATEUR}" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${ID_UTILISATEUR}/bus" "$@"
}
remplir() {  # remplace @RACINE@ et @UTILISATEUR@ dans un modèle
  sed -e "s#@RACINE@#${RACINE}#g" -e "s#@UTILISATEUR@#${UTILISATEUR}#g" "$1"
}
etape() { echo; echo "==> $*"; }

etape "Paquets système"
apt-get install -y --no-install-recommends gphoto2 printer-driver-gutenprint cups curl python3-venv iw

etape "Environnement Python"
if [ ! -x "${RACINE}/.venv/bin/python" ]; then
  en_utilisateur python3 -m venv "${RACINE}/.venv"
fi
en_utilisateur "${RACINE}/.venv/bin/pip" install --quiet -r "${RACINE}/requirements.txt"

etape "Droits : imprimante (lp) et appareil photo (plugdev)"
usermod -aG lp,plugdev "${UTILISATEUR}"
# Le bureau monte l'appareil photo comme un disque et le bloque pour gphoto2.
en_utilisateur systemctl --user mask gvfs-gphoto2-volume-monitor.service >/dev/null 2>&1 || true
pkill -u "${UTILISATEUR}" -x gvfsd-gphoto2 || true

etape "Imprimante DNP (si elle est branchée)"
if lpstat -p DNP_RX1 >/dev/null 2>&1; then
  echo "File d'impression DNP_RX1 déjà présente."
else
  ADRESSE_DNP="$(lpinfo -v 2>/dev/null | awk '/gutenprint53\+usb:\/\/dnp-dsrx1/ {print $2; exit}')"
  if [ -n "${ADRESSE_DNP}" ]; then
    lpadmin -p DNP_RX1 -E -D "DNP DS-RX1HS" -m gutenprint.5.3://dnp-dsrx1/expert -v "${ADRESSE_DNP}"
    lpadmin -p DNP_RX1 -o PageSize=w288h432 -o printer-error-policy=retry-current-job
    echo "File d'impression DNP_RX1 créée (${ADRESSE_DNP})."
  else
    echo "Imprimante DNP non détectée : relancez ce script une fois qu'elle est branchée et allumée."
  fi
fi

etape "Serveur et surveillance (services système)"
chmod +x "${DEPLOIEMENT}/lancer-ecran.sh" "${DEPLOIEMENT}/surveiller.sh" "${DEPLOIEMENT}/surveiller-ecran.sh"
remplir "${DEPLOIEMENT}/matbooth.service" > /etc/systemd/system/matbooth.service
remplir "${DEPLOIEMENT}/matbooth-surveillance.service" > /etc/systemd/system/matbooth-surveillance.service
install -m 644 "${DEPLOIEMENT}/matbooth-surveillance.timer" /etc/systemd/system/matbooth-surveillance.timer
if [ ! -f /etc/default/matbooth ]; then
  cat > /etc/default/matbooth <<'FIN'
# Réglages propres à cette borne (lus au démarrage du serveur Matbooth).
# Après modification : sudo systemctl restart matbooth
#MATBOOTH_PORT=8000
#MATBOOTH_CAMERA=gphoto2
#MATBOOTH_IMPRIMANTE=dnp
#MATBOOTH_FILE_IMPRESSION=DNP_RX1
FIN
fi

etape "Redémarrer et éteindre depuis l'administration"
REGLE=/etc/sudoers.d/matbooth
echo "${UTILISATEUR} ALL=(root) NOPASSWD: /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff" > "${REGLE}.tmp"
chmod 440 "${REGLE}.tmp"
visudo -cf "${REGLE}.tmp" >/dev/null
mv "${REGLE}.tmp" "${REGLE}"

etape "Wi-Fi de la borne (piloté depuis l'administration)"
# Le serveur tourne hors de la session du bureau : on l'autorise explicitement à piloter le Wi-Fi.
cat > /etc/polkit-1/rules.d/50-matbooth.rules <<FIN
// Matbooth : le serveur de la borne (compte ${UTILISATEUR}) peut créer / arrêter le Wi-Fi de la borne.
polkit.addRule(function(action, subject) {
    if (action.id.indexOf("org.freedesktop.NetworkManager.") == 0 && subject.user == "${UTILISATEUR}") {
        return polkit.Result.YES;
    }
});
FIN
chmod 644 /etc/polkit-1/rules.d/50-matbooth.rules
# Portail captif : sur le Wi-Fi de la borne, tous les noms mènent à la borne, ce qui fait afficher
# « Se connecter au réseau » aux téléphones (voir matbooth/app.py, portail_captif).
install -d /etc/NetworkManager/dnsmasq-shared.d
cat > /etc/NetworkManager/dnsmasq-shared.d/matbooth.conf <<'FIN'
# Matbooth : sur le Wi-Fi de la borne, tous les noms de domaine mènent à la borne (portail captif).
address=/#/10.42.0.1
FIN

etape "Écran toujours allumé"
if command -v raspi-config >/dev/null; then
  raspi-config nonint do_blanking 1 || echo "(raspi-config n'a pas pu désactiver la mise en veille)"
fi

etape "Écran de la borne (Chromium en plein écran au démarrage)"
loginctl enable-linger "${UTILISATEUR}"
install -d -o "${UTILISATEUR}" -g "${UTILISATEUR}" "${MAISON}/.config/systemd/user"
for unite in matbooth-ecran.service matbooth-ecran-surveillance.service matbooth-ecran-surveillance.timer; do
  remplir "${DEPLOIEMENT}/${unite}" > "${MAISON}/.config/systemd/user/${unite}"
  chown "${UTILISATEUR}:" "${MAISON}/.config/systemd/user/${unite}"
done
# Raccourci « Borne photo » dans le menu, pour revenir en plein écran après une maintenance.
install -d -o "${UTILISATEUR}" -g "${UTILISATEUR}" "${MAISON}/.local/share/applications"
cat > "${MAISON}/.local/share/applications/matbooth.desktop" <<FIN
[Desktop Entry]
Type=Application
Name=Borne photo
Comment=Revenir à la borne Matbooth en plein écran
Exec=systemctl --user start matbooth-ecran.service
Icon=${DEPLOIEMENT}/matbooth.png
Terminal=false
Categories=Graphics;
FIN
chown "${UTILISATEUR}:" "${MAISON}/.local/share/applications/matbooth.desktop"
# Même raccourci sur le bureau…
BUREAU="$(en_utilisateur xdg-user-dir DESKTOP 2>/dev/null || echo "${MAISON}/Desktop")"
install -d -o "${UTILISATEUR}" -g "${UTILISATEUR}" "${BUREAU}"
install -m 755 -o "${UTILISATEUR}" -g "${UTILISATEUR}" "${MAISON}/.local/share/applications/matbooth.desktop" "${BUREAU}/matbooth.desktop"
# … et en premier dans la barre des tâches (wf-panel-pi).
PANNEAU="${MAISON}/.config/wf-panel-pi/wf-panel-pi.ini"
install -d -o "${UTILISATEUR}" -g "${UTILISATEUR}" "$(dirname "${PANNEAU}")"
touch "${PANNEAU}" && chown "${UTILISATEUR}:" "${PANNEAU}"
if ! grep -q "^launchers=.*matbooth" "${PANNEAU}"; then
  if grep -q "^launchers=" "${PANNEAU}"; then
    sed -i 's/^launchers=/launchers=matbooth /' "${PANNEAU}"
  else
    DEFAUT="$(grep -m1 '^launchers=' /etc/xdg/wf-panel-pi/wf-panel-pi.ini 2>/dev/null | cut -d= -f2-)"
    grep -q "^\[panel\]" "${PANNEAU}" || echo "[panel]" >> "${PANNEAU}"
    sed -i "/^\[panel\]/a launchers=matbooth ${DEFAUT:-x-www-browser pcmanfm x-terminal-emulator}" "${PANNEAU}"
  fi
  pkill -u "${UTILISATEUR}" -x wf-panel-pi || true  # relancé aussitôt par le bureau
fi

etape "Activation"
systemctl daemon-reload
systemctl enable --now matbooth.service matbooth-surveillance.timer
en_utilisateur systemctl --user daemon-reload
en_utilisateur systemctl --user enable matbooth-ecran.service
en_utilisateur systemctl --user enable --now matbooth-ecran-surveillance.timer

echo
echo "Matbooth est installé. Au prochain démarrage, la borne s'ouvrira en plein écran."
echo "Pour l'ouvrir tout de suite : systemctl --user start matbooth-ecran"
