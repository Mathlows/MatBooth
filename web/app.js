"use strict";

const $ = (id) => document.getElementById(id);
const IMAGE_VIDE = "data:image/gif;base64,R0lGODlhAQABAAAAACw=";

let etat = null;        // réglages et état renvoyés par /api/etat
let seance = null;      // { id, mode, total, cadres }
let choix = { filtre: "aucun", cadre: null };
let occupe = false;
let minuteurInactivite = null;

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
  $("modale").hidden = true;
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

async function ouvrirResultat() {
  seance.cadres = await api(`/api/cadres?mode=${encodeURIComponent(seance.mode)}`);
  majChoix();
  majImpression();
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

document.addEventListener("pointerdown", () => {
  if (minuteurInactivite) relancerInactivite();
});
document.addEventListener("contextmenu", (e) => e.preventDefault());
const KIOSQUE = new URLSearchParams(location.search).has("kiosque");
if (KIOSQUE) document.body.classList.add("kiosque");

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
