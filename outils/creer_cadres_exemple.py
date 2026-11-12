"""Génère les cadres d'exemple fournis avec Matbooth (dossier exemples/cadres).

Usage : python outils/creer_cadres_exemple.py
Les vrais cadres se préparent dans n'importe quel logiciel (Canva, Photoshop, GIMP...) :
un PNG de 1800 x 1200 pixels (600 x 1800 pour une bande), transparent là où la photo doit apparaître.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RACINE = Path(__file__).resolve().parent.parent
SORTIE = RACINE / "exemples" / "cadres"
POLICE = RACINE / "web" / "fonts" / "Inter-Bold.ttf"
TAILLE = (1800, 1200)
OR = (201, 162, 90, 255)
BLANC = (255, 255, 255, 255)


def polaroid() -> Image.Image:
    cadre = Image.new("RGBA", TAILLE, BLANC)
    dessin = ImageDraw.Draw(cadre)
    dessin.rectangle((50, 50, TAILLE[0] - 51, TAILLE[1] - 191), fill=(0, 0, 0, 0))
    police = ImageFont.truetype(str(POLICE), 70)
    dessin.text((TAILLE[0] / 2, TAILLE[1] - 95), "Merci d'être là !", font=police, fill=(40, 40, 40, 255), anchor="mm")
    return cadre


def classique() -> Image.Image:
    cadre = Image.new("RGBA", TAILLE, (0, 0, 0, 0))
    dessin = ImageDraw.Draw(cadre)
    dessin.rectangle((0, 0, TAILLE[0] - 1, TAILLE[1] - 1), outline=BLANC, width=36)
    dessin.rectangle((36, 36, TAILLE[0] - 37, TAILLE[1] - 37), outline=OR, width=8)
    dessin.rectangle((58, 58, TAILLE[0] - 59, TAILLE[1] - 59), outline=OR, width=3)
    return cadre


def collage_elegant() -> Image.Image:
    from matbooth.images import zones

    cadre = Image.new("RGBA", TAILLE, (0, 0, 0, 0))
    dessin = ImageDraw.Draw(cadre)
    for x, y, l, h in zones("collage4", TAILLE):
        dessin.rectangle((x + 14, y + 14, x + l - 15, y + h - 15), outline=OR, width=4)
    cx, cy, rayon = TAILLE[0] / 2, TAILLE[1] / 2, 120
    dessin.ellipse((cx - rayon, cy - rayon, cx + rayon, cy + rayon), fill=BLANC, outline=OR, width=8)
    police = ImageFont.truetype(str(POLICE), 54)
    dessin.text((cx, cy), "Merci !", font=police, fill=OR, anchor="mm")
    return cadre


def bande_merci() -> Image.Image:
    from matbooth.images import zones

    taille = (600, 1800)
    cadre = Image.new("RGBA", taille, BLANC)
    dessin = ImageDraw.Draw(cadre)
    emplacements = zones("bandes", taille)
    for x, y, l, h in emplacements:
        dessin.rectangle((x, y, x + l - 1, y + h - 1), fill=(0, 0, 0, 0))
        dessin.rectangle((x - 8, y - 8, x + l + 7, y + h + 7), outline=OR, width=3)
    bas = emplacements[-1][1] + emplacements[-1][3]
    milieu = (bas + taille[1]) / 2
    dessin.text((taille[0] / 2, milieu - 22), "Merci !", font=ImageFont.truetype(str(POLICE), 78), fill=OR, anchor="mm")
    dessin.line((150, milieu + 40, 450, milieu + 40), fill=OR, width=3)
    return cadre


def main() -> None:
    import sys

    sys.path.insert(0, str(RACINE))
    for chemin, fabrique in (
        (SORTIE / "unique" / "polaroid.png", polaroid),
        (SORTIE / "classique.png", classique),
        (SORTIE / "collage4" / "elegant.png", collage_elegant),
        (SORTIE / "bandes" / "merci.png", bande_merci),
    ):
        chemin.parent.mkdir(parents=True, exist_ok=True)
        fabrique().save(chemin, optimize=True)
        print("Créé :", chemin.relative_to(RACINE))


if __name__ == "__main__":
    main()
