"""Traitement des images : réglages couleur, filtres, montages et cadres."""

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

FILTRES = {
    "aucun": "Original",
    "nb": "Noir & blanc",
    "sepia": "Sépia",
    "vintage": "Vintage",
    "chaud": "Chaud",
    "froid": "Froid",
    "eclatant": "Éclatant",
    "doux": "Doux",
}

MODES = {
    "unique": {"nom": "Photo unique", "prises": 1},
    "collage4": {"nom": "Collage 4 photos", "prises": 4},
    # Bande 5x15 en portrait ; deux bandes identiques sont imprimées sur une feuille 10x15 puis découpées.
    "bandes": {"nom": "Bandes photo", "prises": 4, "bande": True},
}

# Marges du collage, en proportion de la largeur du format final.
_MARGE_COLLAGE = 0.022
_ESPACE_COLLAGE = 0.017


def taille_mode(mode: str, format_impression: tuple[int, int]) -> tuple[int, int]:
    """Taille de la composition finale : la feuille 10x15, ou une bande 5x15 (demi-feuille, en portrait)."""
    largeur, hauteur = format_impression
    if MODES[mode].get("bande"):
        return (min(largeur, hauteur) // 2, max(largeur, hauteur))
    return (largeur, hauteur)


def taille_rendu(taille_finale: tuple[int, int], largeur: int) -> tuple[int, int]:
    """Taille d'un aperçu : tient dans un cadre de « largeur » de large (carré pour une bande)."""
    l, h = taille_finale
    largeur = max(80, min(largeur, l if l >= h else h))
    if l >= h:
        return (largeur, round(largeur * h / l))
    return (max(1, round(largeur * l / h)), largeur)


def zones(mode: str, taille: tuple[int, int]) -> list[tuple[int, int, int, int]]:
    """Emplacements (x, y, largeur, hauteur) des photos dans le format final."""
    largeur, hauteur = taille
    if mode == "unique":
        return [(0, 0, largeur, hauteur)]
    if mode == "collage4":
        marge = round(largeur * _MARGE_COLLAGE)
        espace = round(largeur * _ESPACE_COLLAGE)
        l = (largeur - 2 * marge - espace) // 2
        h = (hauteur - 2 * marge - espace) // 2
        return [
            (marge + colonne * (l + espace), marge + ligne * (h + espace), l, h)
            for ligne in range(2)
            for colonne in range(2)
        ]
    if mode == "bandes":
        # Quatre photos 3:2 empilées ; l'espace restant en bas accueille le logo ou le texte du cadre.
        marge = round(largeur * 0.06)
        espace = round(largeur * 0.04)
        l = largeur - 2 * marge
        h = round(l * 2 / 3)
        return [(marge, marge + i * (h + espace), l, h) for i in range(4)]
    raise ValueError(f"Mode inconnu : {mode}")


def ajuster(image: Image.Image, ajustements: dict) -> Image.Image:
    """Réglages couleur simples choisis par le loueur (1.0 = inchangé)."""
    for cle, outil in (
        ("luminosite", ImageEnhance.Brightness),
        ("contraste", ImageEnhance.Contrast),
        ("saturation", ImageEnhance.Color),
    ):
        facteur = float(ajustements.get(cle, 1.0))
        if abs(facteur - 1.0) > 0.001:
            image = outil(image).enhance(facteur)
    return image


def _teinte(image: Image.Image, rouge: float, vert: float, bleu: float) -> Image.Image:
    matrice = (rouge, 0, 0, 0, 0, vert, 0, 0, 0, 0, bleu, 0)
    return image.convert("RGB", matrice)


def filtrer(image: Image.Image, filtre: str) -> Image.Image:
    image = image.convert("RGB")
    if filtre == "aucun":
        return image
    if filtre == "nb":
        gris = ImageOps.grayscale(image)
        return ImageEnhance.Contrast(gris).enhance(1.15).convert("RGB")
    if filtre == "sepia":
        gris = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=1)
        return ImageOps.colorize(gris, black="#2b1d0e", white="#fff6e6", mid="#a8805a")
    if filtre == "vintage":
        image = ImageEnhance.Color(image).enhance(0.65)
        image = ImageEnhance.Contrast(image).enhance(0.88)
        image = _teinte(image, 1.06, 1.0, 0.86)
        return Image.blend(image, Image.new("RGB", image.size, "#f2dfc2"), 0.14)
    if filtre == "chaud":
        return _teinte(ImageEnhance.Color(image).enhance(1.08), 1.08, 1.0, 0.88)
    if filtre == "froid":
        return _teinte(image, 0.9, 1.0, 1.1)
    if filtre == "eclatant":
        image = ImageEnhance.Color(image).enhance(1.4)
        return ImageEnhance.Contrast(image).enhance(1.12)
    if filtre == "doux":
        rayon = max(1, image.width // 400)
        image = Image.blend(image, image.filter(ImageFilter.GaussianBlur(rayon)), 0.35)
        image = ImageEnhance.Contrast(image).enhance(0.9)
        return ImageEnhance.Brightness(image).enhance(1.05)
    raise ValueError(f"Filtre inconnu : {filtre}")


def composer(
    prises: list[Image.Image],
    mode: str,
    filtre: str,
    cadre: Path | None,
    ajustements: dict,
    taille: tuple[int, int],
) -> Image.Image:
    """Assemble les prises, applique réglages et filtre, puis pose le cadre PNG par-dessus."""
    emplacements = zones(mode, taille)
    if len(prises) != len(emplacements):
        raise ValueError(f"{len(emplacements)} photo(s) attendue(s), {len(prises)} reçue(s)")

    toile = Image.new("RGB", taille, "white")
    for prise, (x, y, l, h) in zip(prises, emplacements):
        photo = ImageOps.fit(prise.convert("RGB"), (l, h), Image.Resampling.LANCZOS)
        photo = filtrer(ajuster(photo, ajustements), filtre)
        toile.paste(photo, (x, y))

    if cadre is not None:
        calque = _calque(str(cadre), cadre.stat().st_mtime, taille)
        toile = Image.alpha_composite(toile.convert("RGBA"), calque).convert("RGB")
    return toile


@lru_cache(maxsize=48)
def _calque(chemin: str, _modifie_le: float, taille: tuple[int, int]) -> Image.Image:
    """Cadre redimensionné, gardé en mémoire (la date de modification invalide le cache)."""
    calque = Image.open(chemin).convert("RGBA")
    if calque.size != taille:
        calque = calque.resize(taille, Image.Resampling.LANCZOS)
    return calque
