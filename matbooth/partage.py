"""Envoi des photos aux clients : QR code (réseau local), e-mail (file d'attente), clé USB.

Règle commune : tant que la séance du client est ouverte, il peut encore supprimer sa photo.
Elle n'est donc envoyée par e-mail et copiée sur clé USB qu'une fois la séance terminée ;
une photo supprimée par le client ne quitte jamais la borne (hors téléchargement qu'il a fait lui-même).
"""

import html
import logging
import os
import re
import shutil
import smtplib
import socket
import ssl
import threading
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Callable

import segno
import segno.helpers

journal = logging.getLogger(__name__)

ADRESSE_EMAIL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")


def adresse_email_valide(adresse: str) -> bool:
    return len(adresse) <= 254 and bool(ADRESSE_EMAIL.match(adresse))


# --- QR code --------------------------------------------------------------------


def adresse_locale() -> str:
    """Adresse IPv4 de la borne sur le réseau local (aucun paquet n'est envoyé)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # adresse de documentation : sert juste à choisir l'interface
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def qr_svg(texte: str) -> str:
    """QR code en SVG, à insérer tel quel dans la page."""
    return segno.make(texte, error="m").svg_inline(scale=8, border=2, dark="#000", light="#fff")


def qr_wifi_svg(nom: str, mot_de_passe: str) -> str:
    qr = segno.helpers.make_wifi(ssid=nom, password=mot_de_passe or None, security="WPA" if mot_de_passe else None)
    return qr.svg_inline(scale=8, border=2, dark="#000", light="#fff")


def page_telechargement(evenement: str, jeton: str) -> str:
    """Petite page pour le téléphone du client : la photo et un bouton de téléchargement."""
    nom = html.escape(evenement)
    return f"""<!doctype html>
<html lang="fr" translate="no"><head><meta charset="utf-8">
<meta name="google" content="notranslate">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Votre photo – {nom}</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin: 0; background: #121014; color: #f6f1ea; font-family: system-ui, sans-serif; text-align: center; }}
  main {{ max-width: 640px; margin: 0 auto; padding: 24px 16px 40px; }}
  h1 {{ font-size: 22px; margin: 4px 0 18px; }}
  p.sur {{ color: #d6ad66; letter-spacing: .15em; text-transform: uppercase; font-size: 13px; margin: 0; }}
  img {{ width: 100%; max-height: 70vh; object-fit: contain; border-radius: 8px; box-shadow: 0 8px 30px rgba(0,0,0,.6); }}
  a.bouton {{ display: block; margin: 22px 0 12px; padding: 16px; border-radius: 12px; background: #d6ad66;
             color: #1b1408; font-weight: 700; font-size: 18px; text-decoration: none; }}
  p.aide {{ color: #b9b0a6; font-size: 14px; line-height: 1.5; }}
</style></head>
<body><main>
  <p class="sur">Photobooth</p>
  <h1>{nom}</h1>
  <img src="/p/{jeton}/photo.jpg" alt="Votre photo">
  <a class="bouton" href="/p/{jeton}/photo.jpg?telecharger=1">Télécharger la photo</a>
  <p class="aide">Si le téléchargement ne démarre pas : appui long sur la photo,
  puis « Enregistrer l'image » (ou « Ajouter à Photos » sur iPhone).</p>
</main></body></html>"""


# --- E-mails --------------------------------------------------------------------


def expliquer_erreur_smtp(erreur: Exception) -> str:
    """Message compréhensible par le loueur."""
    if isinstance(erreur, smtplib.SMTPAuthenticationError):
        return "Identifiant ou mot de passe refusé par le serveur e-mail"
    if isinstance(erreur, smtplib.SMTPRecipientsRefused):
        return "Adresse refusée par le serveur e-mail"
    if isinstance(erreur, smtplib.SMTPSenderRefused):
        return "Adresse d'expéditeur refusée par le serveur e-mail"
    if isinstance(erreur, ssl.SSLError):
        return "Échec de la connexion sécurisée (vérifier la sécurité et le port)"
    if isinstance(erreur, socket.gaierror):
        return "Serveur e-mail introuvable (pas d'internet, ou nom du serveur incorrect)"
    if isinstance(erreur, (TimeoutError, socket.timeout)):
        return "Le serveur e-mail ne répond pas (pas d'internet ?)"
    if isinstance(erreur, OSError):
        return f"Connexion au serveur e-mail impossible (pas d'internet ?) : {erreur.strerror or erreur}"
    if isinstance(erreur, smtplib.SMTPException):
        return f"Erreur du serveur e-mail : {erreur}"
    return str(erreur)


def _erreur_definitive(erreur: Exception) -> bool:
    """Erreurs propres à l'adresse du client : inutile de réessayer."""
    if isinstance(erreur, smtplib.SMTPRecipientsRefused):
        return all(500 <= code < 600 for code, _ in erreur.recipients.values())
    return False


def smtp_configure(smtp: dict) -> bool:
    return bool(smtp.get("hote") and smtp.get("expediteur"))


def construire_email(smtp: dict, destinataire: str, evenement: str, photo: Path | None) -> EmailMessage:
    message = EmailMessage()
    message["From"] = formataddr((smtp.get("nom_expediteur") or "", smtp["expediteur"]))
    message["To"] = destinataire
    message["Subject"] = (smtp.get("sujet") or "Votre photo").replace("{evenement}", evenement)
    message["Date"] = formatdate(localtime=True)
    domaine = smtp["expediteur"].rpartition("@")[2] or None
    message["Message-ID"] = make_msgid(domain=domaine)
    message.set_content((smtp.get("message") or "").replace("{evenement}", evenement))
    if photo is not None:
        nom = re.sub(r"[^\w-]+", "-", evenement, flags=re.ASCII).strip("-").lower() or "photo"
        message.add_attachment(photo.read_bytes(), maintype="image", subtype="jpeg", filename=f"photo-{nom}.jpg")
    return message


def envoyer_smtp(smtp: dict, message: EmailMessage, delai: float = 30) -> None:
    hote, port = smtp["hote"], int(smtp.get("port") or 587)
    securite = smtp.get("securite", "starttls")
    contexte = ssl.create_default_context()
    if securite == "ssl":
        serveur = smtplib.SMTP_SSL(hote, port, timeout=delai, context=contexte)
    else:
        serveur = smtplib.SMTP(hote, port, timeout=delai)
    with serveur:
        if securite == "starttls":
            serveur.starttls(context=contexte)
        if smtp.get("utilisateur"):
            serveur.login(smtp["utilisateur"], smtp.get("mot_de_passe") or "")
        serveur.send_message(message)


class FileEmails:
    """Envoie les e-mails en attente ; réessaie (de plus en plus espacé) tant qu'internet manque."""

    _INTERVALLE = 10  # s entre deux passages
    _DELAI_MAX = 30 * 60  # s entre deux essais pour un même e-mail

    def __init__(self, cabine, envoyer: Callable[[dict, EmailMessage], None] = envoyer_smtp):
        self.cabine = cabine
        self._envoyer = envoyer
        self._reveil = threading.Event()
        self._arret = threading.Event()
        self._verrou = threading.Lock()
        self._fil: threading.Thread | None = None

    def demarrer(self) -> None:
        self._fil = threading.Thread(target=self._boucle, name="envoi-emails", daemon=True)
        self._fil.start()

    def arreter(self) -> None:
        self._arret.set()
        self._reveil.set()

    def reveiller(self) -> None:
        self._reveil.set()

    def _boucle(self) -> None:
        while not self._arret.is_set():
            try:
                self.traiter()
            except Exception:
                journal.exception("File des e-mails")
            self._reveil.wait(self._INTERVALLE)
            self._reveil.clear()

    def traiter(self) -> int:
        """Un passage dans la file. Renvoie le nombre d'e-mails partis."""
        smtp = self.cabine.reglages["smtp"]
        if not smtp_configure(smtp):
            return 0
        partis = 0
        with self._verrou:
            for envoi in self.cabine.base.envois_a_faire(time.time()):
                if envoi["photo_id"] == self.cabine.photo_en_cours():
                    continue  # le client peut encore supprimer sa photo
                fichier = self.cabine.dossier_donnees / envoi["fichier"]
                try:
                    message = construire_email(smtp, envoi["adresse"], envoi["nom_evenement"] or "", fichier)
                    self._envoyer(smtp, message)
                except Exception as erreur:
                    definitive = _erreur_definitive(erreur) or isinstance(erreur, FileNotFoundError)
                    attente = min(self._DELAI_MAX, 60 * 2 ** min(envoi["essais"], 5))
                    texte = "Photo introuvable" if isinstance(erreur, FileNotFoundError) else expliquer_erreur_smtp(erreur)
                    self.cabine.base.envoi_rate(envoi["id"], texte, time.time() + attente, abandonner=definitive)
                    journal.warning("E-mail à %s non envoyé (%s)%s", envoi["adresse"], texte, "" if definitive else ", nouvel essai plus tard")
                    if not definitive:
                        break  # serveur ou réseau indisponible : inutile d'essayer les suivants maintenant
                    continue
                self.cabine.base.envoi_reussi(envoi["id"])
                partis += 1
                journal.info("Photo envoyée par e-mail à %s", envoi["adresse"])
        return partis

    def essai(self, adresse: str) -> None:
        """E-mail de test depuis l'administration (sans photo). Lève une exception lisible en cas d'échec."""
        smtp = self.cabine.reglages["smtp"]
        if not smtp_configure(smtp):
            raise ValueError("Renseignez au moins le serveur et l'adresse d'expéditeur.")
        message = construire_email(
            {**smtp, "message": "Ceci est un e-mail de test envoyé par la borne photo. Les réglages fonctionnent !"},
            adresse,
            "test",
            None,
        )
        message.replace_header("Subject", "Test de la borne photo")
        try:
            self._envoyer(smtp, message)
        except Exception as erreur:
            raise ValueError(expliquer_erreur_smtp(erreur)) from erreur

    def etat(self) -> dict:
        bilan = self.cabine.base.bilan_envois()
        actif = self.cabine.profil["partage"]["email"]
        configure = smtp_configure(self.cabine.reglages["smtp"])
        alertes = []
        if actif and not configure:
            alertes.append({"niveau": "erreur", "message": "Serveur e-mail non configuré (onglet Réglages)"})
        if bilan["en_attente"] and bilan["derniere_erreur"]:
            alertes.append({"niveau": "attention", "message": f"Dernier essai : {bilan['derniere_erreur']}"})
        if bilan["abandonnes"]:
            alertes.append({"niveau": "attention", "message": f"{bilan['abandonnes']} adresse(s) refusée(s), e-mail abandonné"})
        morceaux = [f"{bilan['envoyes']} envoyé(s)"]
        if bilan["en_attente"]:
            morceaux.insert(0, f"{bilan['en_attente']} en attente")
        message = " · ".join(morceaux) if (actif or bilan["envoyes"] or bilan["en_attente"]) else "Désactivé pour cet événement"
        return {
            "nom": "Envoi par e-mail",
            "ok": not (actif and not configure),
            "message": message,
            "alertes": alertes,
        }


# --- Clé USB ----------------------------------------------------------------------


def _decoder_montage(texte: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), texte)


def cles_usb_montees(montages: Path = Path("/proc/mounts"), blocs: Path = Path("/sys/block")) -> list[Path]:
    """Points de montage des clés USB / disques amovibles, en écriture."""
    cles = []
    try:
        lignes = montages.read_text().splitlines()
    except OSError:
        return cles
    for ligne in lignes:
        morceaux = ligne.split()
        if len(morceaux) < 4 or not morceaux[0].startswith("/dev/sd"):
            continue
        disque = re.match(r"/dev/(sd[a-z]+)", morceaux[0]).group(1)
        systeme = blocs / disque
        try:
            amovible = (systeme / "removable").read_text().strip() == "1" or "/usb" in os.path.realpath(systeme)
        except OSError:
            amovible = False
        if amovible and "rw" in morceaux[3].split(","):
            cles.append(Path(_decoder_montage(morceaux[1])))
    return cles


class SauvegardeUSB:
    """Copie les photos gardées sur toute clé USB branchée, pour les événements qui l'ont activé."""

    _INTERVALLE = 10  # s
    DOSSIER = "Matbooth"

    def __init__(self, cabine, trouver_cles: Callable[[], list[Path]] = cles_usb_montees):
        self.cabine = cabine
        self._trouver_cles = trouver_cles
        self._arret = threading.Event()
        self._verrou = threading.Lock()
        # Ce qu'on sait déjà copié sur chaque clé : {clé: {chemin relatif: taille}}
        self._copies: dict[Path, dict[str, int]] = {}
        self._dernier_bilan: dict = {"cles": [], "copiees": 0, "erreur": None, "le": None}

    def demarrer(self) -> None:
        threading.Thread(target=self._boucle, name="sauvegarde-usb", daemon=True).start()

    def arreter(self) -> None:
        self._arret.set()

    def _boucle(self) -> None:
        while not self._arret.wait(self._INTERVALLE):
            try:
                self.synchroniser()
            except Exception:
                journal.exception("Sauvegarde USB")

    def _photos_a_sauver(self) -> list[dict]:
        en_cours = self.cabine.photo_en_cours()
        actifs = {e["id"] for e in self.cabine.base.lister_evenements() if e["profil"]["partage"]["usb"]}
        return [p for p in self.cabine.base.lister_toutes_photos() if p["evenement"] in actifs and p["id"] != en_cours]

    def synchroniser(self) -> int:
        """Copie ce qui manque sur les clés branchées. Renvoie le nombre de photos copiées."""
        with self._verrou:
            cles = self._trouver_cles()
            for oubliee in set(self._copies) - set(cles):
                del self._copies[oubliee]  # clé retirée : on revérifiera tout à son retour
            copiees = 0
            erreur = None
            photos = self._photos_a_sauver() if cles else []
            for cle in cles:
                deja = self._copies.setdefault(cle, {})
                try:
                    for photo in photos:
                        source = self.cabine.dossier_donnees / photo["fichier"]
                        try:
                            taille = source.stat().st_size
                        except FileNotFoundError:
                            continue
                        relatif = f"{self.DOSSIER}/{photo['evenement']}/{source.name}"
                        if deja.get(relatif) == taille:
                            continue
                        cible = cle / relatif
                        if not (cible.exists() and cible.stat().st_size == taille):
                            cible.parent.mkdir(parents=True, exist_ok=True)
                            temporaire = cible.with_name(cible.name + ".tmp")
                            with open(source, "rb") as entree, open(temporaire, "wb") as sortie:
                                shutil.copyfileobj(entree, sortie, 1024 * 1024)
                                sortie.flush()
                                os.fsync(sortie.fileno())  # la clé peut être retirée sans « éjecter »
                            os.replace(temporaire, cible)
                            copiees += 1
                        deja[relatif] = taille
                except OSError as e:
                    erreur = f"Copie impossible sur {cle.name} : {e.strerror or e}"
                    journal.warning(erreur)
            if copiees:
                journal.info("Sauvegarde USB : %d photo(s) copiée(s)", copiees)
            self._dernier_bilan = {
                "cles": [c.name for c in cles],
                "copiees": copiees,
                "erreur": erreur,
                "le": time.strftime("%H:%M") if copiees else self._dernier_bilan.get("le"),
                "sur_cle": max((len(v) for v in self._copies.values()), default=0),
            }
            return copiees

    def etat(self) -> dict:
        actif = self.cabine.profil["partage"]["usb"]
        bilan = self._dernier_bilan
        alertes = []
        if bilan["erreur"]:
            alertes.append({"niveau": "erreur", "message": bilan["erreur"]})
        if not actif:
            message = "Désactivée pour cet événement"
        elif not bilan["cles"]:
            message = "Aucune clé branchée"
            alertes.append({"niveau": "attention", "message": "Branchez une clé : les photos y seront copiées automatiquement"})
        else:
            message = f"Clé « {', '.join(bilan['cles'])} » : {bilan.get('sur_cle', 0)} photo(s) sauvegardée(s)"
            if bilan["le"]:
                message += f", dernière copie à {bilan['le']}"
        return {"nom": "Sauvegarde sur clé USB", "ok": not bilan["erreur"], "message": message, "alertes": alertes}
