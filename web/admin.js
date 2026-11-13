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
  const materiel = [["Appareil photo", t.camera], ["Imprimante", t.imprimante], ["Wi-Fi", t.wifi], ["E-mails", t.emails], ["Clé USB", t.usb]];
  $("materiel").replaceChildren(...materiel.filter(([, m]) => m).map(([titre, m]) => {
    const alertes = m.alertes || [];
    const details = [];
    if (m.papier && m.papier.restant !== null) {
      const part = m.papier.capacite ? Math.max(0, Math.min(1, m.papier.restant / m.papier.capacite)) : 0;
      details.push(
        el("div", { class: "doux petit" }, `Papier : ${m.papier.restant} / ${m.papier.capacite ?? "?"} tirages${m.papier.type ? ` (${m.papier.type})` : ""}`),
        el("div", { class: "jauge" }, el("span", { style: `width: ${Math.round(part * 100)}%` }))
      );
    }
    if (m.compteur !== null && m.compteur !== undefined) {
      details.push(el("div", { class: "doux petit" }, `Compteur total de l'imprimante : ${m.compteur.toLocaleString("fr-FR")} tirages`));
    }
    for (const a of alertes) details.push(el("div", { class: `alerte-materiel ${a.niveau}` }, a.message));
    const attention = m.ok && alertes.length > 0;
    return el("div", { class: "carte etat-materiel" },
      el("span", { class: `voyant ${m.ok ? (attention ? "attention" : "ok") : ""}` }),
      el("div", { class: "etat-texte" }, el("div", { class: "doux petit" }, titre), el("strong", {}, m.nom), el("div", { class: "doux" }, m.message), ...details)
    );
  }));
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
  for (const cle of ["qr", "email", "usb"]) $(`ev-partage-${cle}`).checked = p.partage[cle];

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
      partage: Object.fromEntries(["qr", "email", "usb"].map((c) => [c, $(`ev-partage-${c}`).checked])),
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
  await chargerEnvoi();
  chargerReseauxWifi().catch((e) => ($("wifi-statut").textContent = e.message));
  const camera = await api("/api/admin/camera");
  for (const [champ, cle] of [["iso-apercu", "iso_apercu"], ["iso-prise", "iso_prise"]]) {
    const valeurs = [...camera.valeurs_iso];
    // Une valeur enregistrée que l'appareil actuel ne propose pas reste visible.
    if (camera[cle] && !valeurs.includes(camera[cle])) valeurs.push(camera[cle]);
    $(champ).replaceChildren(
      el("option", { value: "", selected: camera[cle] === null }, "Réglage de l'appareil"),
      ...valeurs.map((v) => el("option", { value: v, selected: v === camera[cle] }, v === "Auto" ? "Automatique" : v))
    );
  }
  $("etat-camera").textContent = camera.appareil_branche
    ? ""
    : "Appareil non détecté : liste des valeurs habituelles.";
}

$("form-camera").onsubmit = (e) => {
  e.preventDefault();
  essayer(async () => {
    await envoyer("/api/admin/camera", {
      iso_apercu: $("iso-apercu").value || null,
      iso_prise: $("iso-prise").value || null,
    }, "PUT");
    message("Réglages de l'appareil enregistrés.");
  });
};

async function chargerEnvoi() {
  const r = await api("/api/admin/envoi");
  const w = r.wifi_borne;
  $("form-wifi-borne").hidden = !w.disponible;
  $("wifi-borne-actif").checked = w.actif;
  $("wifi-borne-nom").value = w.nom;
  $("wifi-borne-mdp").value = w.mot_de_passe;
  $("etat-wifi-borne").textContent = w.actif ? `Actif · adresse de la borne : ${w.adresse}` : "Désactivé";
  $("reseau-wifi-nom").value = r.reseau.wifi_nom;
  $("reseau-wifi-mdp").value = r.reseau.wifi_mot_de_passe;
  $("reseau-adresse").value = r.reseau.adresse;
  $("reseau-adresse").placeholder = `Détectée : ${r.adresse_detectee}`;
  const s = r.smtp;
  $("smtp-hote").value = s.hote;
  $("smtp-port").value = s.port;
  $("smtp-securite").value = s.securite;
  $("smtp-utilisateur").value = s.utilisateur;
  $("smtp-mdp").value = "";
  $("smtp-mdp").placeholder = s.mot_de_passe_enregistre ? "Enregistré (laisser vide pour le garder)" : "";
  $("smtp-expediteur").value = s.expediteur;
  $("smtp-nom").value = s.nom_expediteur;
  $("smtp-sujet").value = s.sujet;
  $("smtp-message").value = s.message;
}

const PREREGLAGES_SMTP = {
  gmail: ["smtp.gmail.com", 587, "starttls"],
  outlook: ["smtp-mail.outlook.com", 587, "starttls"],
  orange: ["smtp.orange.fr", 465, "ssl"],
  ovh: ["ssl0.ovh.net", 465, "ssl"],
};
document.querySelectorAll("[data-prereglage]").forEach((b) => {
  b.onclick = () => {
    const [hote, port, securite] = PREREGLAGES_SMTP[b.dataset.prereglage];
    $("smtp-hote").value = hote;
    $("smtp-port").value = port;
    $("smtp-securite").value = securite;
  };
});
$("smtp-securite").onchange = () => {
  const port = { starttls: 587, ssl: 465 }[$("smtp-securite").value];
  if (port) $("smtp-port").value = port;
};

// ---------- Wi-Fi : rejoindre le réseau d'une salle ----------

async function chargerReseauxWifi() {
  $("wifi-statut").textContent = "Recherche des réseaux…";
  $("liste-wifi").replaceChildren();
  const r = await api("/api/admin/wifi/reseaux");
  if (r.borne) {
    $("wifi-statut").textContent = "Le Wi-Fi de la borne est actif : désactivez-le (plus bas) pour rejoindre un autre réseau.";
    return;
  }
  $("wifi-statut").textContent = r.actuel ? `Connectée à « ${r.actuel} ».` : "Pas connectée à un Wi-Fi.";
  $("liste-wifi").replaceChildren(...r.reseaux.map((reseau) => {
    const barres = el("span", { class: "barres-wifi", title: `${reseau.signal} %` },
      ...[25, 50, 75].map((seuil) => el("i", { class: reseau.signal >= seuil ? "plein" : "" })));
    const formulaire = el("form", { class: "connexion-wifi", hidden: true },
      reseau.protege ? el("input", { type: "text", placeholder: "Mot de passe du Wi-Fi", autocomplete: "off", autocapitalize: "off", spellcheck: "false" }) : null,
      el("button", { class: "bouton principal" }, "Se connecter"));
    formulaire.onsubmit = (e) => {
      e.preventDefault();
      essayer(async () => {
        const champ = formulaire.querySelector("input");
        const bouton = formulaire.querySelector("button");
        bouton.disabled = true;
        bouton.textContent = "Connexion…";
        try {
          await envoyer("/api/admin/wifi/connecter", { nom: reseau.nom, mot_de_passe: champ ? champ.value : "" });
          message(`Connectée à « ${reseau.nom} ».`);
          await chargerEnvoi();
          await chargerReseauxWifi();
        } finally {
          bouton.disabled = false;
          bouton.textContent = "Se connecter";
        }
      });
    };
    const ligne = el("button", { type: "button", class: `reseau-wifi${reseau.nom === r.actuel ? " actuel" : ""}` },
      barres, el("span", { class: "nom-reseau" }, reseau.nom),
      el("span", { class: "doux petit" }, reseau.nom === r.actuel ? "Connectée" : reseau.protege ? "Protégé" : "Ouvert"));
    ligne.onclick = () => {
      document.querySelectorAll(".connexion-wifi").forEach((f) => { if (f !== formulaire) f.hidden = true; });
      formulaire.hidden = !formulaire.hidden;
      formulaire.querySelector("input")?.focus();
    };
    return el("div", { class: "element-wifi" }, ligne, formulaire);
  }));
}
$("btn-rechercher-wifi").onclick = () => essayer(chargerReseauxWifi);

$("form-wifi-borne").onsubmit = (e) => {
  e.preventDefault();
  essayer(async () => {
    const actif = $("wifi-borne-actif").checked;
    const mdp = $("wifi-borne-mdp").value;
    if (mdp && mdp.length < 8) throw new Error("Mot de passe : 8 caractères minimum (ou vide pour un réseau ouvert).");
    if (actif && !confirmer("Activer le Wi-Fi de la borne ? Elle n'aura plus internet par Wi-Fi pendant ce temps.")) return;
    await envoyer("/api/admin/wifi-borne", { actif, nom: $("wifi-borne-nom").value, mot_de_passe: mdp }, "PUT");
    message(actif ? "Le Wi-Fi de la borne démarre (quelques secondes)…" : "Retour au Wi-Fi habituel (quelques secondes)…");
    $("etat-wifi-borne").textContent = actif ? "Démarrage…" : "Arrêt…";
  });
};

$("form-reseau").onsubmit = (e) => {
  e.preventDefault();
  essayer(async () => {
    await envoyer("/api/admin/reseau", {
      adresse: $("reseau-adresse").value,
      wifi_nom: $("reseau-wifi-nom").value,
      wifi_mot_de_passe: $("reseau-wifi-mdp").value,
    }, "PUT");
    message("Réglages réseau enregistrés.");
  });
};

function valeursSmtp() {
  return {
    hote: $("smtp-hote").value,
    port: parseInt($("smtp-port").value, 10) || 587,
    securite: $("smtp-securite").value,
    utilisateur: $("smtp-utilisateur").value,
    mot_de_passe: $("smtp-mdp").value || null,
    expediteur: $("smtp-expediteur").value,
    nom_expediteur: $("smtp-nom").value,
    sujet: $("smtp-sujet").value,
    message: $("smtp-message").value,
  };
}

$("form-smtp").onsubmit = (e) => {
  e.preventDefault();
  essayer(async () => {
    await envoyer("/api/admin/smtp", valeursSmtp(), "PUT");
    message("Réglages e-mail enregistrés.");
    await chargerEnvoi();
  });
};

$("btn-essai-smtp").onclick = () => essayer(async () => {
  const adresse = $("smtp-essai-adresse").value.trim();
  if (!adresse) throw new Error("Indiquez une adresse pour l'essai.");
  // On enregistre d'abord, pour tester exactement ce qui est affiché.
  await envoyer("/api/admin/smtp", valeursSmtp(), "PUT");
  $("etat-smtp").textContent = "Envoi de l'essai…";
  try {
    await envoyer("/api/admin/smtp/essai", { adresse });
    $("etat-smtp").textContent = `E-mail de test envoyé à ${adresse} ✓`;
  } catch (erreur) {
    $("etat-smtp").textContent = "";
    throw erreur;
  }
  await chargerEnvoi();
});

const QUESTIONS_SYSTEME = {
  bureau: ["Quitter le plein écran ? La borne affichera le bureau jusqu'au prochain démarrage.", "Retour au bureau…"],
  ecran: [null, "L'écran de la borne se relance…"],
  redemarrer: ["Redémarrer la borne ? Elle sera indisponible environ une minute.", "La borne redémarre…"],
  eteindre: ["Éteindre la borne ? Attendez que l'écran soit noir avant de couper le courant.", "La borne s'éteint…"],
};
document.querySelectorAll("[data-systeme]").forEach((b) => {
  b.onclick = () => essayer(async () => {
    const [question, annonce] = QUESTIONS_SYSTEME[b.dataset.systeme];
    if (question && !confirmer(question)) return;
    await envoyer(`/api/admin/systeme/${b.dataset.systeme}`);
    message(annonce);
  });
});

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
  // Signe de vie pour la surveillance de l'écran (un onglet planté se tait et l'écran est relancé).
  const battement = () => fetch("/api/ecran/battement", { method: "POST" }).catch(() => {});
  battement();
  setInterval(battement, 10000);
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
