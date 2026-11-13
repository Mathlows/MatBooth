"use strict";

const $ = (id) => document.getElementById(id);
const IMAGE_VIDE = "data:image/gif;base64,R0lGODlhAQABAAAAACw=";

let etat = null;        // réglages et état renvoyés par /api/etat
let seance = null;      // { id, mode, total, cadres }
let choix = { filtre: "aucun", cadre: null };
let occupe = false;
let minuteurInactivite = null;
let photoGalerie = null;  // photo ouverte dans la galerie : { id, restantes, impressions, emails }
let minuteurGalerie = null;

/** QR code et e-mail visent la séance en cours, ou la photo ouverte dans la galerie. */
function cibleEnvoi() {
  if (photoGalerie) return { chemin: `/api/galerie/${photoGalerie.id}`, corps: {} };
  if (seance) return { chemin: `/api/seances/${seance.id}`, corps: choix };
  return null;
}

// ---------- Outils ----------

const attendre = (ms) => new Promise((ok) => setTimeout(ok, ms));

async function api(chemin, options = {}) {
  const reponse = await fetch(chemin, { headers: { "Content-Type": "application/json" }, ...options });
  const donnees = await reponse.json().catch(() => ({}));
  if (!reponse.ok) throw new Error(donnees.erreur || "Un problème est survenu.");
  return donnees;
}
const envoyer = (chemin, corps = {}, methode = "POST") =>
  api(chemin, { method: methode, body: JSON.stringify(corps) });

function afficher(nom) {
  document.querySelectorAll(".ecran").forEach((e) => e.classList.toggle("actif", e.id === `ecran-${nom}`));
  if (nom !== "capture") $("apercu").src = IMAGE_VIDE; // coupe le flux de l'aperçu
  if (nom !== "resultat") arreterInactivite();
  if (nom !== "galerie" && nom !== "photo-galerie") {
    clearTimeout(minuteurGalerie);
    minuteurGalerie = null;
  }
}

function erreur(message) {
  const bandeau = $("bandeau-erreur");
  bandeau.textContent = message;
  bandeau.hidden = false;
  clearTimeout(bandeau.minuteur);
  bandeau.minuteur = setTimeout(() => (bandeau.hidden = true), 5000);
}

function attente(texte) {
  $("texte-attente").textContent = texte;
  afficher("attente");
}

/** Petite fenêtre de choix. Renvoie la valeur du bouton touché. */
function demander(texte, boutons) {
  return new Promise((repondre) => {
    $("modale-texte").textContent = texte;
    const zone = $("modale-boutons");
    zone.innerHTML = "";
    for (const b of boutons) {
      const bouton = document.createElement("button");
      bouton.className = `bouton ${b.style || ""}`;
      bouton.textContent = b.texte;
      bouton.onclick = () => {
        $("modale").hidden = true;
        repondre(b.valeur);
      };
      zone.appendChild(bouton);
    }
    $("modale").hidden = false;
  });
}

// ---------- Accueil ----------

async function retourAccueil() {
  seance = null;
  photoGalerie = null;
  $("modale").hidden = true;
  $("fenetre-qr").hidden = true;
  $("fenetre-email").hidden = true;
  afficher("accueil");
  try {
    etat = await api("/api/etat");
  } catch {
    setTimeout(retourAccueil, 3000);
    return;
  }
  $("titre-evenement").textContent = etat.evenement;
  const zone = $("modes");
  zone.innerHTML = "";
  for (const mode of etat.modes) {
    const bouton = document.createElement("button");
    bouton.className = "mode";
    const icone = document.createElement("div");
    icone.className = `icone ${mode.bande ? "bande" : mode.prises === 4 ? "x4" : ""}`;
    for (let i = 0; i < mode.prises; i++) icone.appendChild(document.createElement("span"));
    const nom = document.createElement("span");
    nom.textContent = mode.nom;
    bouton.append(icone, nom);
    bouton.onclick = () => demarrer(mode.id);
    zone.appendChild(bouton);
  }
  $("btn-galerie").hidden = !(etat.galerie && etat.galerie.active);
}

// ---------- Galerie de l'événement ----------

function relancerGalerie() {
  clearTimeout(minuteurGalerie);
  minuteurGalerie = setTimeout(retourAccueil, (etat.delai_inactivite_resultat || 90) * 1000);
}

async function ouvrirGalerie() {
  if (occupe) return;
  photoGalerie = null;
  let photos;
  try {
    etat = await api("/api/etat"); // état de l'imprimante à jour
    photos = await api("/api/galerie");
  } catch (e) {
    erreur(e.message);
    return;
  }
  $("grille-galerie").replaceChildren(...photos.map((p) => {
    const b = document.createElement("button");
    b.className = "vignette-galerie";
    const img = document.createElement("img");
    img.loading = "lazy";
    img.src = `/api/galerie/${p.id}/miniature.jpg`;
    b.appendChild(img);
    if (p.impressions) {
      const pastille = document.createElement("span");
      pastille.className = "pastille";
      pastille.textContent = `Imprimée ${p.impressions}×`;
      b.appendChild(pastille);
    }
    b.onclick = () => ouvrirPhotoGalerie(p);
    return b;
  }));
  $("galerie-vide").hidden = photos.length > 0;
  afficher("galerie");
  relancerGalerie();
}

function ouvrirPhotoGalerie(p) {
  photoGalerie = { ...p, emails: [] };
  $("image-galerie").src = `/api/galerie/${p.id}.jpg`;
  majPhotoGalerie();
  afficher("photo-galerie");
  relancerGalerie();
}

function copiesPossiblesGalerie() {
  let maximum = photoGalerie.restantes;
  if (etat.impressions_restantes !== null) maximum = Math.min(maximum, etat.impressions_restantes);
  return maximum;
}

function majPhotoGalerie() {
  const p = photoGalerie;
  const options = etat.partage || {};
  $("btn-galerie-qr").hidden = !options.qr;
  $("btn-galerie-email").hidden = !options.email;
  $("actions-galerie-partage").hidden = !(options.qr || options.email);
  $("btn-galerie-supprimer").hidden = !(etat.galerie && etat.galerie.suppression);
  $("info-galerie-partage").textContent = p.emails.length ? `Envoi prévu à : ${p.emails.join(", ")}` : "";

  const bouton = $("btn-galerie-imprimer");
  const info = $("info-galerie");
  bouton.disabled = false;
  info.textContent = "";
  if (!etat.imprimante.ok) {
    bouton.disabled = true;
    info.textContent = "Imprimante indisponible pour le moment.";
  } else if (p.restantes <= 0) {
    bouton.disabled = true;
    info.textContent = `Déjà imprimée ${p.impressions} fois : c'est le maximum pour une photo.`;
  } else if (copiesPossiblesGalerie() <= 0) {
    bouton.disabled = true;
    info.textContent = "Le nombre maximum d'impressions est atteint.";
  } else if (p.impressions) {
    info.textContent = `Déjà imprimée ${p.impressions} fois.`;
  }
}

async function imprimerGalerie() {
  if (occupe || !photoGalerie) return;
  occupe = true;
  try {
    const maximum = copiesPossiblesGalerie();
    let copies = 1;
    if (maximum > 1) {
      const boutons = [];
      for (let n = 1; n <= maximum; n++) boutons.push({ texte: `${n}`, valeur: n, style: "principal" });
      boutons.push({ texte: "Annuler", valeur: 0 });
      copies = await demander("Combien d'exemplaires ?", boutons);
      if (!copies) return relancerGalerie();
    }
    attente("Envoi à l'imprimante…");
    await envoyer(`/api/galerie/${photoGalerie.id}/imprimer`, { copies });
    merci("Votre photo s'imprime !", "Elle sera prête dans quelques instants.");
  } catch (e) {
    erreur(e.message);
    occupe = false;
    await ouvrirGalerie();
  } finally {
    occupe = false;
  }
}

async function supprimerGalerie() {
  if (occupe || !photoGalerie) return;
  occupe = true;
  try {
    const oui = await demander("Effacer définitivement cette photo ? Elle ne pourra plus être imprimée ni envoyée.", [
      { texte: "Annuler", valeur: false },
      { texte: "Effacer", valeur: true, style: "danger" },
    ]);
    if (!oui) return relancerGalerie();
    await envoyer(`/api/galerie/${photoGalerie.id}`, {}, "DELETE");
  } catch (e) {
    erreur(e.message);
  } finally {
    occupe = false;
  }
  if ($("ecran-photo-galerie").classList.contains("actif")) await ouvrirGalerie();
}

// ---------- Prise de vue ----------

async function decompte(secondes) {
  const zone = $("decompte");
  for (let n = secondes; n >= 1; n--) {
    zone.textContent = n;
    zone.classList.remove("anime");
    void zone.offsetWidth; // relance l'animation
    zone.classList.add("anime");
    await attendre(1000);
  }
  zone.textContent = "";
  zone.classList.remove("anime");
}

function flash() {
  const f = $("flash");
  f.classList.remove("actif");
  void f.offsetWidth;
  f.classList.add("actif");
}

async function demarrer(mode) {
  if (occupe) return;
  occupe = true;
  try {
    const nouvelle = await envoyer("/api/seances", { mode });
    const total = etat.modes.find((m) => m.id === mode).prises;
    seance = { ...nouvelle, total };
    choix = { filtre: "aucun", cadre: null };

    $("vignettes-prises").innerHTML = "";
    $("progression").textContent = "";
    const apercu = $("apercu");
    apercu.classList.toggle("miroir", etat.apercu_miroir);
    apercu.src = `/api/apercu.mjpg?t=${Date.now()}`;
    afficher("capture");

    for (let numero = 1; numero <= total; numero++) {
      if (total > 1) $("progression").textContent = `Photo ${numero} / ${total}`;
      $("message-capture").textContent = numero === 1 ? "Préparez-vous !" : "Photo suivante…";
      await attendre(numero === 1 ? 1200 : 1800);
      $("message-capture").textContent = "";
      await decompte(etat.compte_a_rebours);
      flash();
      await envoyer(`/api/seances/${seance.id}/prise`);
      const vignette = document.createElement("img");
      vignette.src = `/api/seances/${seance.id}/prises/${numero}.jpg?largeur=240`;
      $("vignettes-prises").appendChild(vignette);
    }

    attente("Préparation de votre photo…");
    await ouvrirResultat();
  } catch (e) {
    erreur(e.message);
    retourAccueil();
  } finally {
    occupe = false;
  }
}

// ---------- Résultat ----------

function urlRendu(filtre, cadre, largeur) {
  const p = new URLSearchParams({ filtre, largeur });
  if (cadre) p.set("cadre", cadre);
  return `/api/seances/${seance.id}/rendu.jpg?${p}`;
}

function bouton(libelle, image, selectionne, action) {
  const b = document.createElement("button");
  if (selectionne) b.classList.add("selection");
  const img = document.createElement("img");
  img.src = image;
  const texte = document.createElement("span");
  texte.textContent = libelle;
  b.append(img, texte);
  b.onclick = action;
  return b;
}

function majChoix() {
  const zoneFiltres = $("filtres");
  zoneFiltres.innerHTML = "";
  for (const f of etat.filtres) {
    zoneFiltres.appendChild(
      bouton(f.nom, urlRendu(f.id, choix.cadre, 260), f.id === choix.filtre, () => {
        choix.filtre = f.id;
        majChoix();
      })
    );
  }

  const zoneCadres = $("cadres");
  zoneCadres.innerHTML = "";
  for (const c of [{ id: null, nom: "Sans cadre" }, ...seance.cadres]) {
    zoneCadres.appendChild(
      bouton(c.nom, urlRendu(choix.filtre, c.id, 260), c.id === choix.cadre, () => {
        choix.cadre = c.id;
        majChoix();
      })
    );
  }

  const rendu = $("rendu");
  const largeur = Math.min(1800, Math.round(window.innerWidth * 0.64 * (window.devicePixelRatio || 1)));
  rendu.classList.add("chargement");
  rendu.onload = () => rendu.classList.remove("chargement");
  rendu.src = urlRendu(choix.filtre, choix.cadre, largeur);
}

function majImpression() {
  const btn = $("btn-imprimer");
  const info = $("info-impression");
  btn.disabled = false;
  info.textContent = "";
  if (!etat.imprimante.ok) {
    btn.disabled = true;
    info.textContent = "Imprimante indisponible pour le moment.";
    if (etat.imprimante.message) info.textContent += ` (${etat.imprimante.message})`;
  } else if (etat.impressions_restantes === 0) {
    btn.disabled = true;
    info.textContent = "Le nombre maximum d'impressions est atteint.";
  }
}

// ---------- Envoi au client : QR code et e-mail ----------

function majPartage() {
  const p = etat.partage || {};
  $("btn-qr").hidden = !p.qr;
  $("btn-email").hidden = !p.email;
  $("actions-partage").hidden = !(p.qr || p.email);
  const adresses = seance.emails || [];
  $("info-partage").textContent = adresses.length ? `Envoi prévu à : ${adresses.join(", ")}` : "";
}

function etapeQr(numero, titre, svg, detail) {
  const etape = document.createElement("div");
  etape.className = "qr-etape";
  const image = document.createElement("div");
  image.className = "qr-image";
  image.innerHTML = svg; // SVG produit par la borne elle-même
  const texte = document.createElement("p");
  texte.className = "detail";
  texte.append(...detail);
  const enTete = document.createElement("p");
  enTete.className = "titre";
  enTete.textContent = titre;
  if (numero) {
    const pastille = document.createElement("span");
    pastille.className = "numero";
    pastille.textContent = numero;
    etape.append(pastille);
  }
  etape.append(enTete, image, texte);
  return etape;
}

function etapesTexte(lignes) {
  const zone = document.createElement("div");
  zone.className = "qr-consignes";
  for (const [numero, titre, detail] of lignes) {
    const ligne = document.createElement("div");
    ligne.className = "qr-consigne";
    const pastille = document.createElement("span");
    pastille.className = "numero";
    pastille.textContent = numero;
    const texte = document.createElement("div");
    const t = document.createElement("p");
    t.className = "titre";
    t.textContent = titre;
    const d = document.createElement("p");
    d.className = "detail";
    d.textContent = detail;
    texte.append(t, d);
    ligne.append(pastille, texte);
    zone.appendChild(ligne);
  }
  return zone;
}

function gras(texte) {
  const b = document.createElement("strong");
  b.textContent = texte;
  return b;
}

async function ouvrirQr() {
  const cible = cibleEnvoi();
  if (occupe || !cible) return;
  occupe = true;
  try {
    const r = await envoyer(`${cible.chemin}/qr`, cible.corps);
    const etapes = [];
    if (r.wifi && r.borne) {
      // Wi-Fi de la borne : la photo s'ouvre dans la page « Se connecter au réseau » (portail).
      // Pas de second QR code : ouvert dans le navigateur, il passerait par les données mobiles (Android).
      const detail = ["Réseau ", gras(r.wifi.nom)];
      if (r.wifi.mot_de_passe) detail.push(document.createElement("br"), "Mot de passe ", gras(r.wifi.mot_de_passe));
      etapes.push(etapeQr("1", "Scannez pour rejoindre le Wi-Fi", r.wifi.qr, detail));
      etapes.push(etapesTexte([
        ["2", "Touchez « Se connecter au réseau »",
          "Dans la notification, ou dans les réglages Wi-Fi sur le réseau " + r.wifi.nom + ". Sur iPhone, la page s'ouvre toute seule."],
        ["3", "Touchez « Télécharger la photo »", "Votre photo s'affiche dans cette page."],
      ]));
    } else if (r.wifi) {
      const detail = ["Réseau ", gras(r.wifi.nom)];
      if (r.wifi.mot_de_passe) detail.push(document.createElement("br"), "Mot de passe ", gras(r.wifi.mot_de_passe));
      etapes.push(etapeQr("1", "Connectez-vous au Wi-Fi", r.wifi.qr, detail));
      etapes.push(etapeQr("2", "Scannez pour ouvrir la photo", r.qr, ["Puis touchez « Télécharger la photo »."]));
    } else {
      etapes.push(etapeQr("", "Scannez avec l'appareil photo de votre téléphone", r.qr,
        ["Votre téléphone doit être connecté au même Wi-Fi que la borne."]));
    }
    $("qr-etapes").replaceChildren(...etapes);
    $("fenetre-qr").hidden = false;
    relancerInactivite();
  } catch (e) {
    erreur(e.message);
  } finally {
    occupe = false;
  }
}

const CLAVIER = [
  ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"],
  ["a", "z", "e", "r", "t", "y", "u", "i", "o", "p"],
  ["q", "s", "d", "f", "g", "h", "j", "k", "l", "m"],
  ["w", "x", "c", "v", "b", "n", ".", "-", "_", "⌫"],
];
const RACCOURCIS = ["@", "@gmail.com", "@hotmail.fr", "@orange.fr", "@icloud.com", "@yahoo.fr", ".com", ".fr"];
const FORME_EMAIL = /^[^\s@]+@[^\s@]+\.[a-z]{2,}$/i;
let texteEmail = "";

function majSaisieEmail(message = "", estErreur = false) {
  $("texte-email").textContent = texteEmail;
  $("saisie-email").classList.toggle("vide", !texteEmail);
  const aide = $("aide-email");
  aide.textContent = message || "La photo partira dès que la borne aura accès à internet.";
  aide.classList.toggle("erreur", estErreur);
}

function taper(touche) {
  if (touche === "⌫") texteEmail = texteEmail.slice(0, -1);
  else if (texteEmail.length < 254) {
    // Un seul @ : un raccourci de domaine remplace ce qui suivait le @.
    if (touche.startsWith("@") && texteEmail.includes("@")) texteEmail = texteEmail.slice(0, texteEmail.indexOf("@"));
    texteEmail += touche;
  }
  majSaisieEmail();
}

function construireClavier() {
  const rangee = (touches, classe = "") => {
    const r = document.createElement("div");
    r.className = `rangee ${classe}`;
    for (const t of touches) {
      const b = document.createElement("button");
      b.textContent = t;
      if (t === "⌫") b.classList.add("large");
      b.onclick = () => taper(t);
      r.appendChild(b);
    }
    return r;
  };
  $("clavier").replaceChildren(...CLAVIER.map((t) => rangee(t)), rangee(RACCOURCIS, "raccourcis"));
}

function ouvrirEmail() {
  if (occupe || !cibleEnvoi()) return;
  texteEmail = "";
  majSaisieEmail();
  $("fenetre-email").hidden = false;
}

function fermerEmail() {
  $("fenetre-email").hidden = true;
}

async function envoyerEmail() {
  const cible = cibleEnvoi();
  if (occupe || !cible) return;
  if (!FORME_EMAIL.test(texteEmail)) {
    majSaisieEmail("Cette adresse e-mail ne semble pas complète.", true);
    return;
  }
  occupe = true;
  try {
    const r = await envoyer(`${cible.chemin}/email`, { ...cible.corps, adresse: texteEmail });
    fermerEmail();
    if (photoGalerie) {
      photoGalerie.emails = r.adresses;
      majPhotoGalerie();
    } else {
      seance.emails = r.adresses;
      majPartage();
    }
  } catch (e) {
    majSaisieEmail(e.message, true);
  } finally {
    occupe = false;
  }
}

document.addEventListener("keydown", (e) => {
  if ($("fenetre-email").hidden) return;
  if (e.key === "Backspace") taper("⌫");
  else if (e.key === "Enter") envoyerEmail();
  else if (e.key === "Escape") fermerEmail();
  else if (e.key.length === 1 && /[\w.@+-]/.test(e.key)) taper(e.key.toLowerCase());
  else return;
  e.preventDefault();
});

async function ouvrirResultat() {
  seance.cadres = await api(`/api/cadres?mode=${encodeURIComponent(seance.mode)}`);
  seance.emails = [];
  majChoix();
  majImpression();
  majPartage();
  afficher("resultat");
  relancerInactivite();
}

function merci(titre, sousTitre) {
  $("texte-merci").textContent = titre;
  $("sous-texte-merci").textContent = sousTitre;
  afficher("merci");
  setTimeout(retourAccueil, 4000);
}

async function action(fonction) {
  if (occupe || !seance) return;
  occupe = true;
  try {
    return await fonction();
  } catch (e) {
    erreur(e.message);
    retourAccueil();
  } finally {
    occupe = false;
  }
}

const imprimer = () =>
  action(async () => {
    let maximum = etat.copies_max_par_photo;
    if (etat.impressions_restantes !== null) maximum = Math.min(maximum, etat.impressions_restantes);
    let copies = 1;
    if (maximum > 1) {
      arreterInactivite();
      const boutons = [];
      for (let n = 1; n <= maximum; n++) boutons.push({ texte: `${n}`, valeur: n, style: "principal" });
      boutons.push({ texte: "Annuler", valeur: 0 });
      copies = await demander("Combien d'exemplaires ?", boutons);
      relancerInactivite();
      if (!copies) return;
    }
    attente("Envoi à l'imprimante…");
    await envoyer(`/api/seances/${seance.id}/imprimer`, { ...choix, copies });
    merci("Votre photo s'imprime !", "Elle sera prête dans quelques instants.");
  });

const terminer = () =>
  action(async () => {
    attente("Enregistrement…");
    await envoyer(`/api/seances/${seance.id}/terminer`, choix);
    merci("Merci !", "Votre photo est enregistrée.");
  });

async function recommencer() {
  const mode = await action(async () => {
    arreterInactivite();
    const oui = await demander("Recommencer ? Cette photo sera supprimée.", [
      { texte: "Annuler", valeur: false },
      { texte: "Recommencer", valeur: true, style: "principal" },
    ]);
    if (!oui) return relancerInactivite();
    await envoyer(`/api/seances/${seance.id}`, {}, "DELETE");
    return seance.mode;
  });
  if (mode) demarrer(mode);
}

const supprimer = () =>
  action(async () => {
    arreterInactivite();
    const oui = await demander("Supprimer définitivement cette photo ?", [
      { texte: "Annuler", valeur: false },
      { texte: "Supprimer", valeur: true, style: "danger" },
    ]);
    if (!oui) return relancerInactivite();
    await envoyer(`/api/seances/${seance.id}`, {}, "DELETE");
    merci("Photo supprimée", "Elle n'a été conservée nulle part.");
  });

// Si le client s'en va sans rien choisir, la photo est gardée (comme « Terminer »).
function relancerInactivite() {
  arreterInactivite();
  minuteurInactivite = setTimeout(terminer, etat.delai_inactivite_resultat * 1000);
}
function arreterInactivite() {
  clearTimeout(minuteurInactivite);
  minuteurInactivite = null;
}

// ---------- Démarrage ----------

$("btn-imprimer").onclick = imprimer;
$("btn-terminer").onclick = terminer;
$("btn-reprendre").onclick = recommencer;
$("btn-supprimer").onclick = supprimer;
$("btn-qr").onclick = ouvrirQr;
$("btn-galerie").onclick = ouvrirGalerie;
$("btn-galerie-accueil").onclick = retourAccueil;
$("btn-galerie-liste").onclick = ouvrirGalerie;
$("btn-galerie-imprimer").onclick = imprimerGalerie;
$("btn-galerie-supprimer").onclick = supprimerGalerie;
$("btn-galerie-qr").onclick = ouvrirQr;
$("btn-galerie-email").onclick = ouvrirEmail;
$("btn-fermer-qr").onclick = () => ($("fenetre-qr").hidden = true);
$("btn-email").onclick = ouvrirEmail;
$("btn-annuler-email").onclick = fermerEmail;
$("btn-envoyer-email").onclick = envoyerEmail;
construireClavier();

document.addEventListener("pointerdown", () => {
  if (minuteurGalerie) relancerGalerie();
  if (minuteurInactivite) relancerInactivite();
});
document.addEventListener("contextmenu", (e) => e.preventDefault());
const KIOSQUE = new URLSearchParams(location.search).has("kiosque");
if (KIOSQUE) {
  document.body.classList.add("kiosque");
  // Demande au système de ne pas mettre l'écran en veille tant que la borne est affichée.
  const garderEveille = () => navigator.wakeLock?.request("screen").catch(() => {});
  garderEveille();
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") garderEveille();
  });
  // Signe de vie pour la surveillance de l'écran (un onglet planté se tait et l'écran est relancé).
  const battement = () => fetch("/api/ecran/battement", { method: "POST" }).catch(() => {});
  battement();
  setInterval(battement, 10000);
}

// Accès caché à l'administration : appui long de 3 secondes en haut à gauche de l'accueil.
(() => {
  const zone = $("acces-admin");
  let minuteur = null;
  const annuler = () => clearTimeout(minuteur);
  zone.addEventListener("pointerdown", () => {
    annuler();
    minuteur = setTimeout(() => (location.href = KIOSQUE ? "/admin?kiosque=1" : "/admin"), 3000);
  });
  for (const evenement of ["pointerup", "pointerleave", "pointercancel"]) zone.addEventListener(evenement, annuler);
})();

retourAccueil();
