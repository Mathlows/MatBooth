"use strict";

const $ = (id) => document.getElementById(id);
const KIOSQUE = new URLSearchParams(location.search).has("kiosque");
const CLE_JETON = "matbooth-jeton";
const RETOUR_AUTO_KIOSQUE = 5 * 60 * 1000;

let jeton = sessionStorage.getItem(CLE_JETON) || "";
let catalogue = null;     // événements, modes et filtres
let cadresConnus = [];    // tous les cadres
let evenementEdite = null;

// ---------- Outils ----------

/** Crée un élément : el("div", { class: "x", onclick: f }, "texte", autreElement) */
function el(balise, attributs = {}, ...enfants) {
  const e = document.createElement(balise);
  for (const [cle, valeur] of Object.entries(attributs)) {
    if (valeur === undefined || valeur === null || valeur === false) continue;
    if (cle.startsWith("on")) e.addEventListener(cle.slice(2), valeur);
    else if (cle === "class") e.className = valeur;
    else if (valeur === true) e.setAttribute(cle, "");
    else e.setAttribute(cle, valeur);
  }
  for (const enfant of enfants.flat()) {
    if (enfant !== null && enfant !== undefined) e.append(enfant);
  }
  return e;
}

function message(texte, erreur = false) {
  const zone = $("message");
  zone.textContent = texte;
  zone.className = `message${erreur ? " erreur" : ""}`;
  zone.hidden = false;
  clearTimeout(zone.minuteur);
  zone.minuteur = setTimeout(() => (zone.hidden = true), erreur ? 5000 : 2500);
}

async function api(chemin, options = {}) {
  const entetes = { "X-Jeton": jeton, ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData)) entetes["Content-Type"] = "application/json";
  const reponse = await fetch(chemin, { ...options, headers: entetes });
  const donnees = await reponse.json().catch(() => ({}));
  if (reponse.status === 401) {
    deconnecter(false);
    throw new Error(donnees.erreur || "Session expirée.");
  }
  if (!reponse.ok) throw new Error(donnees.erreur || "Un problème est survenu.");
  return donnees;
}
const envoyer = (chemin, corps, methode = "POST") =>
  api(chemin, { method: methode, body: corps instanceof FormData ? corps : JSON.stringify(corps ?? {}) });

/** Adresse d'une image ou d'un téléchargement protégé (le jeton passe dans l'adresse). */
const urlProtegee = (chemin, params = {}) => `${chemin}?${new URLSearchParams({ ...params, jeton })}`;

async function essayer(fonction) {
  try {
    return await fonction();
  } catch (e) {
    message(e.message, true);
  }
}

function confirmer(texte) {
  return window.confirm(texte);
}

function retourBorne() {
  location.href = KIOSQUE ? "/?kiosque=1" : "/";
}

// ---------- Code PIN ----------

let saisie = "";

function dessinerPoints() {
  const zone = $("points");
  zone.replaceChildren(...Array.from({ length: Math.max(4, saisie.length) }, (_, i) =>
    el("span", { class: i < saisie.length ? "plein" : "" })
  ));
}

function construirePave() {
  const touches = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "⌫", "0", "OK"];
  $("pave").replaceChildren(...touches.map((t) =>
    el("button", {
      class: t === "OK" ? "valider" : "",
      onclick: () => {
        $("erreur-pin").textContent = "";
        if (t === "⌫") saisie = saisie.slice(0, -1);
        else if (t === "OK") return valider();
        else if (saisie.length < 8) saisie += t;
        dessinerPoints();
      },
    }, t)
  ));
  dessinerPoints();
}

async function valider() {
  if (!saisie) return;
  try {
    const reponse = await envoyer("/api/admin/connexion", { pin: saisie });
    jeton = reponse.jeton;
    sessionStorage.setItem(CLE_JETON, jeton);
    saisie = "";
    dessinerPoints();
    ouvrir();
  } catch (e) {
    saisie = "";
    dessinerPoints();
    $("erreur-pin").textContent = e.message;
  }
}

document.addEventListener("keydown", (e) => {
  if ($("connexion").hidden) return;
  if (/^\d$/.test(e.key) && saisie.length < 8) saisie += e.key;
  else if (e.key === "Backspace") saisie = saisie.slice(0, -1);
  else if (e.key === "Enter") return valider();
  else return;
  dessinerPoints();
});

function deconnecter(prevenirServeur = true) {
  if (prevenirServeur && jeton) api("/api/admin/deconnexion", { method: "POST" }).catch(() => {});
  jeton = "";
  sessionStorage.removeItem(CLE_JETON);
  $("admin").hidden = true;
  $("connexion").hidden = false;
}

// ---------- Onglets ----------

const chargeurs = {
  tableau: chargerTableau,
  evenements: chargerEvenements,
  cadres: chargerCadres,
  photos: chargerPhotos,
  reglages: chargerReglages,
};

function afficherOnglet(nom) {
  document.querySelectorAll("#onglets button").forEach((b) => b.classList.toggle("actif", b.dataset.onglet === nom));
  document.querySelectorAll(".onglet").forEach((o) => o.classList.toggle("actif", o.id === `onglet-${nom}`));
  essayer(chargeurs[nom]);
}

document.querySelectorAll("#onglets button").forEach((b) => (b.onclick = () => afficherOnglet(b.dataset.onglet)));

async function ouvrir() {
  $("connexion").hidden = true;
  $("admin").hidden = false;
  afficherOnglet("tableau");
}

// ---------- Tableau de bord ----------

async function chargerTableau() {
  const t = await api("/api/admin/tableau");
  $("nom-actif").textContent = t.evenement.nom;
  $("alerte-pin").hidden = !t.pin_par_defaut;

  const impressions = t.limite_impressions ? `${t.impressions} / ${t.limite_impressions}` : `${t.impressions}`;
  const chiffres = [
    [t.photos, "Photos enregistrées"],
    [impressions, t.limite_impressions ? "Impressions (limite)" : "Impressions"],
    [t.impressions_restantes ?? "Illimité", "Impressions restantes"],
    [`${t.disque_libre_go.toLocaleString("fr-FR")} Go`, "Espace libre"],
  ];
  $("chiffres").replaceChildren(...chiffres.map(([valeur, libelle]) =>
    el("div", { class: "chiffre" }, el("div", { class: "valeur" }, String(valeur)), el("div", { class: "libelle" }, libelle))
  ));
  $("materiel").replaceChildren(...[["Appareil photo", t.camera], ["Imprimante", t.imprimante]].map(([titre, m]) =>
    el("div", { class: "carte etat-materiel" },
      el("span", { class: `voyant ${m.ok ? "ok" : ""}` }),
      el("div", {}, el("div", { class: "doux petit" }, titre), el("strong", {}, m.nom), el("div", { class: "doux" }, m.message))
    )
  ));
}

// ---------- Événements ----------

async function chargerEvenements(idAEditer) {
  [catalogue, { cadres: cadresConnus }] = await Promise.all([api("/api/admin/evenements"), api("/api/admin/cadres")]);
  const actif = catalogue.evenements.find((e) => e.id === catalogue.actif);
  $("nom-actif").textContent = actif.nom;
  const cible = idAEditer || evenementEdite?.id || catalogue.actif;
  evenementEdite = catalogue.evenements.find((e) => e.id === cible) || actif;

  $("liste-evenements").replaceChildren(...catalogue.evenements.map((ev) => {
    const estActif = ev.id === catalogue.actif;
    return el("div", { class: `evenement ${ev.id === evenementEdite.id ? "edite" : ""}` },
      el("div", { class: "infos" },
        el("div", { class: "nom" }, ev.nom, estActif ? el("span", { class: "badge" }, "Actif") : null),
        el("div", { class: "doux petit" },
          `Créé le ${new Date(ev.cree_le).toLocaleDateString("fr-FR")} · ${ev.nb_photos} photo(s) · ${ev.nb_impressions} impression(s)`)
      ),
      el("button", { class: "bouton petit", type: "button", onclick: () => chargerEvenements(ev.id) }, "Modifier"),
      estActif ? null : el("button", {
        class: "bouton petit principal", type: "button",
        onclick: () => essayer(async () => {
          await envoyer(`/api/admin/evenements/${encodeURIComponent(ev.id)}/activer`);
          message(`« ${ev.nom} » est maintenant actif sur la borne.`);
          await chargerEvenements(ev.id);
        }),
      }, "Activer"),
      estActif ? null : el("button", {
        class: "bouton petit danger", type: "button",
        onclick: () => essayer(async () => {
          if (!confirmer(`Supprimer « ${ev.nom} » et ses ${ev.nb_photos} photo(s) ? C'est définitif.`)) return;
          await envoyer(`/api/admin/evenements/${encodeURIComponent(ev.id)}`, null, "DELETE");
          evenementEdite = null;
          message("Événement supprimé.");
          await chargerEvenements();
        }),
      }, "Supprimer")
    );
  }));
  remplirFormulaire(evenementEdite);
}

function puce(type, nom, valeur, coche, libelle, image) {
  return el("label", { class: "puce" },
    el("input", { type, name: nom, value: valeur, checked: coche }),
    image ? el("img", { src: image, class: "damier", alt: "" }) : null,
    el("span", {}, libelle)
  );
}

function remplirFormulaire(ev) {
  const p = ev.profil;
  $("titre-edition").textContent = ev.nom;
  $("ev-nom").value = ev.nom;
  $("ev-compte").value = p.compte_a_rebours;
  $("ev-copies").value = p.copies_max_par_photo;
  $("ev-limite").value = p.limite_impressions;
  $("ev-inactivite").value = p.delai_inactivite_resultat;
  $("ev-miroir").checked = p.apercu_miroir;

  $("ev-modes").replaceChildren(...catalogue.modes.map((m) =>
    puce("checkbox", "mode", m.id, p.modes_actifs.includes(m.id), m.nom)
  ));

  $("ev-filtres").replaceChildren(...catalogue.filtres.map((f) => {
    const original = f.id === "aucun";
    const coche = original || p.filtres_actifs === null || p.filtres_actifs.includes(f.id);
    const element = puce("checkbox", "filtre", f.id, coche, original ? `${f.nom} (toujours)` : f.nom);
    if (original) {
      element.classList.add("verrouillee");
      element.querySelector("input").disabled = true;
    }
    return element;
  }));

  $("ev-cadres").replaceChildren(...(cadresConnus.length
    ? cadresConnus.map((c) =>
        puce("checkbox", "cadre", c.id, p.cadres_actifs === null || p.cadres_actifs.includes(c.id),
          c.nom, urlProtegee(`/api/admin/cadres/fichier/${c.id}`)))
    : [el("p", { class: "doux" }, "Aucun cadre. Ajoutez-en dans l'onglet Cadres.")]));

  for (const cle of ["luminosite", "contraste", "saturation"]) $(`ev-${cle}`).value = p.ajustements[cle];
  majSortiesCouleur();
  actualiserApercuCouleur();
  $("etat-enregistrement").textContent = "";
}

function cochees(nom) {
  return [...document.querySelectorAll(`#form-evenement input[name=${nom}]:checked`)].map((i) => i.value);
}

function ajustements() {
  return Object.fromEntries(["luminosite", "contraste", "saturation"].map((c) => [c, parseFloat($(`ev-${c}`).value)]));
}

function majSortiesCouleur() {
  for (const [cle, valeur] of Object.entries(ajustements())) {
    const ecart = Math.round((valeur - 1) * 100);
    $(`sortie-${cle}`).textContent = ecart === 0 ? "normal" : `${ecart > 0 ? "+" : ""}${ecart} %`;
  }
}

let minuteurApercu = null;
function actualiserApercuCouleur() {
  clearTimeout(minuteurApercu);
  minuteurApercu = setTimeout(() => {
    $("apercu-couleur").src = urlProtegee("/api/admin/apercu-couleur.jpg", { ...ajustements(), t: Date.now() });
  }, 150);
}

for (const cle of ["luminosite", "contraste", "saturation"]) {
  $(`ev-${cle}`).addEventListener("input", () => {
    majSortiesCouleur();
    actualiserApercuCouleur();
  });
}
$("btn-couleurs-defaut").onclick = () => {
  for (const cle of ["luminosite", "contraste", "saturation"]) $(`ev-${cle}`).value = 1;
  majSortiesCouleur();
  actualiserApercuCouleur();
};
$("btn-actualiser-apercu").onclick = actualiserApercuCouleur;

$("form-evenement").onsubmit = (e) => {
  e.preventDefault();
  essayer(async () => {
    const filtres = cochees("filtre");
    const cadres = cochees("cadre");
    const profil = {
      compte_a_rebours: parseInt($("ev-compte").value, 10),
      copies_max_par_photo: parseInt($("ev-copies").value, 10),
      limite_impressions: parseInt($("ev-limite").value, 10),
      delai_inactivite_resultat: parseInt($("ev-inactivite").value, 10),
      apercu_miroir: $("ev-miroir").checked,
      modes_actifs: cochees("mode"),
      // Tout coché = « tous » : les filtres et cadres ajoutés plus tard seront aussi proposés.
      filtres_actifs: filtres.length === catalogue.filtres.length - 1 ? null : ["aucun", ...filtres],
      cadres_actifs: cadres.length === cadresConnus.length ? null : cadres,
      ajustements: ajustements(),
    };
    if (!profil.modes_actifs.length) throw new Error("Gardez au moins un mode photo.");
    await envoyer(`/api/admin/evenements/${encodeURIComponent(evenementEdite.id)}`, { nom: $("ev-nom").value, profil }, "PUT");
    message("Réglages enregistrés.");
    await chargerEvenements(evenementEdite.id);
    $("etat-enregistrement").textContent = "Enregistré ✓";
  });
};

$("form-nouvel-evenement").onsubmit = (e) => {
  e.preventDefault();
  essayer(async () => {
    const nouveau = await envoyer("/api/admin/evenements", { nom: $("nom-nouvel-evenement").value });
    $("nom-nouvel-evenement").value = "";
    message(`« ${nouveau.nom} » créé. Pensez à l'activer.`);
    await chargerEvenements(nouveau.id);
  });
};

// ---------- Cadres ----------

async function chargerCadres() {
  const { portees, cadres } = await api("/api/admin/cadres");
  cadresConnus = cadres;
  if (!$("portee-cadre").options.length) {
    $("portee-cadre").replaceChildren(...Object.entries(portees).map(([id, nom]) => el("option", { value: id }, nom)));
  }
  $("grille-cadres").replaceChildren(...(cadres.length
    ? cadres.map((c) =>
        el("div", { class: "cadre" },
          el("img", { class: "damier", src: urlProtegee(`/api/admin/cadres/fichier/${c.id}`, { v: Date.now() }), alt: c.nom }),
          el("div", { class: "pied" },
            el("div", {},
              el("div", { class: "nom" }, c.nom),
              el("div", { class: "doux petit" }, `${portees[c.portee]}${c.actif ? "" : " · masqué pour cet événement"}`)),
            el("button", {
              class: "bouton petit danger",
              onclick: () => essayer(async () => {
                if (!confirmer(`Supprimer le cadre « ${c.nom} » ?`)) return;
                await envoyer(`/api/admin/cadres/${c.id}`, null, "DELETE");
                message("Cadre supprimé.");
                await chargerCadres();
              }),
            }, "Supprimer")
          )
        ))
    : [el("p", { class: "vide" }, "Aucun cadre pour le moment.")]));
}

$("form-cadre").onsubmit = (e) => {
  e.preventDefault();
  essayer(async () => {
    const donnees = new FormData();
    donnees.append("fichier", $("fichier-cadre").files[0]);
    donnees.append("portee", $("portee-cadre").value);
    const cadre = await envoyer("/api/admin/cadres", donnees);
    $("fichier-cadre").value = "";
    const zone = $("avertissements-cadre");
    zone.replaceChildren(...cadre.avertissements.map((a) => el("span", {}, a)));
    zone.hidden = !cadre.avertissements.length;
    message(`Cadre « ${cadre.nom} » ajouté.`);
    await chargerCadres();
  });
};

// ---------- Photos ----------

async function chargerPhotos() {
  if (!catalogue) catalogue = await api("/api/admin/evenements");
  const choix = $("choix-evenement-photos");
  const selection = choix.value || catalogue.actif;
  choix.replaceChildren(...catalogue.evenements.map((ev) =>
    el("option", { value: ev.id, selected: ev.id === selection }, ev.nom)
  ));
  await afficherPhotos(choix.value);
}

async function afficherPhotos(evenement) {
  const photos = await api(`/api/admin/evenements/${encodeURIComponent(evenement)}/photos`);
  $("nb-photos").textContent = `${photos.length} photo(s)`;
  $("lien-archive").href = urlProtegee(`/api/admin/evenements/${encodeURIComponent(evenement)}/archive.zip`);
  $("lien-archive").hidden = !photos.length || KIOSQUE;
  $("grille-photos").replaceChildren(...(photos.length
    ? photos.map((p) =>
        el("button", { onclick: () => ouvrirPhoto(p, evenement) },
          el("img", { src: urlProtegee(`/api/admin/photos/${p.id}/miniature.jpg`), loading: "lazy", alt: "" }),
          el("span", { class: "info-photo" },
            new Date(p.cree_le).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" }),
            p.impressions ? ` · ${p.impressions} impr.` : "")
        ))
    : [el("p", { class: "vide" }, "Aucune photo pour cet événement.")]));
}

$("choix-evenement-photos").onchange = (e) => essayer(() => afficherPhotos(e.target.value));

function ouvrirPhoto(photo, evenement) {
  $("visionneuse-image").src = urlProtegee(`/api/admin/photos/${photo.id}.jpg`);
  $("visionneuse-telecharger").href = urlProtegee(`/api/admin/photos/${photo.id}.jpg`, { telecharger: 1 });
  $("visionneuse-telecharger").hidden = KIOSQUE;
  $("visionneuse-supprimer").onclick = () => essayer(async () => {
    if (!confirmer("Supprimer définitivement cette photo ?")) return;
    await envoyer(`/api/admin/photos/${photo.id}`, null, "DELETE");
    $("visionneuse").hidden = true;
    message("Photo supprimée.");
    await afficherPhotos(evenement);
  });
  $("visionneuse").hidden = false;
}
$("visionneuse-fermer").onclick = () => ($("visionneuse").hidden = true);

// ---------- Réglages ----------

async function chargerReglages() {
  const camera = await api("/api/admin/camera");
  for (const [champ, cle] of [["iso-apercu", "iso_apercu"], ["iso-prise", "iso_prise"]]) {
    $(champ).replaceChildren(...camera.valeurs_iso.map((v) =>
      el("option", { value: v, selected: v === camera[cle] }, v === "auto" ? "Automatique" : v)
    ));
  }
}

$("form-camera").onsubmit = (e) => {
  e.preventDefault();
  essayer(async () => {
    await envoyer("/api/admin/camera", { iso_apercu: $("iso-apercu").value, iso_prise: $("iso-prise").value }, "PUT");
    message("Réglages de l'appareil enregistrés.");
  });
};

$("form-pin").onsubmit = (e) => {
  e.preventDefault();
  essayer(async () => {
    await envoyer("/api/admin/pin", { ancien: $("pin-ancien").value, nouveau: $("pin-nouveau").value });
    $("pin-ancien").value = $("pin-nouveau").value = "";
    message("Code d'accès changé.");
  });
};

// ---------- Démarrage ----------

$("btn-retour-borne").onclick = retourBorne;
$("btn-retour-borne-pin").onclick = retourBorne;
$("btn-deconnexion").onclick = () => deconnecter();

if (KIOSQUE) {
  document.body.classList.add("kiosque");
  // Sur la borne, on ne reste pas bloqué dans l'administration si on l'oublie ouverte.
  let minuteur = setTimeout(retourBorne, RETOUR_AUTO_KIOSQUE);
  document.addEventListener("pointerdown", () => {
    clearTimeout(minuteur);
    minuteur = setTimeout(retourBorne, RETOUR_AUTO_KIOSQUE);
  });
}

construirePave();
if (jeton) {
  api("/api/admin/tableau").then(ouvrir).catch(() => {});
}
