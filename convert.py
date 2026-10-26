"""
Convertisseur d'images - logique de conversion (sans interface)

Tout se passe en mémoire : convert_image() retourne les octets du fichier
converti, ce qui permet de calculer l'aperçu "Après" sans rien écrire sur le
disque. Seul save_conversion() crée un fichier, dans le dossier choisi.
"""

import io
import os
import warnings
from dataclasses import dataclass, field

from PIL import Image, ImageOps, features

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_DISPONIBLE = True
except ImportError:
    HEIF_DISPONIBLE = False


@dataclass(frozen=True)
class FormatSortie:
    libelle: str
    extension: str
    avec_perte: bool  # True = la qualité (slider) a un effet
    metadonnees: bool  # True = le format sait stocker EXIF et profil couleur ICC
    description: str


# Ordre = ordre d'affichage dans l'interface. La clé est le nom du format pour Pillow.
FORMATS = {
    "JPEG": FormatSortie(
        "JPEG", ".jpg", True, True,
        "Le plus compatible. Pas de transparence (remplacée par du blanc).",
    ),
    "PNG": FormatSortie(
        "PNG", ".png", False, True,
        "Sans perte, gère la transparence. Fichiers plus lourds.",
    ),
    "WEBP": FormatSortie(
        "WebP", ".webp", True, True,
        "Léger et moderne, gère la transparence. Idéal pour le web.",
    ),
    "AVIF": FormatSortie(
        "AVIF", ".avif", True, True,
        "Très léger à qualité égale. Encodage plus lent.",
    ),
    "HEIF": FormatSortie(
        "HEIC", ".heic", True, True,
        "Format des photos d'iPhone, très léger. Peu lu en dehors d'Apple.",
    ),
    "BMP": FormatSortie(
        "BMP", ".bmp", False, False,
        "Aucune compression : fichiers très lourds.",
    ),
    "TIFF": FormatSortie(
        "TIFF", ".tiff", False, True,
        "Sans perte, pour l'impression et l'archivage.",
    ),
    "GIF": FormatSortie(
        "GIF", ".gif", False, False,
        "256 couleurs maximum : à réserver aux images simples.",
    ),
    "ICO": FormatSortie(
        "ICO", ".ico", False, False,
        "Icône Windows (256 x 256 px maximum).",
    ),
}

FORMAT_PAR_DEFAUT = "JPEG"
NIVEAU_PAR_DEFAUT = 20  # ~qualité 80

# Formats que Pillow sait "ouvrir" en théorie mais qui demandent un logiciel
# externe (Ghostscript...) ou qui ne sont pas des images : exclus du sélecteur.
_FORMATS_LECTURE_EXCLUS = {"BUFR", "EPS", "FITS", "GRIB", "HDF5", "MPEG", "WMF"}

# Clés de image.info qui ne contiennent rien de personnel et qu'il faut garder
# pour que l'image reste correcte à l'enregistrement.
_INFO_A_GARDER = ("transparency", "dpi")


def is_format_available(format_sortie: str) -> bool:
    """Indique si Pillow (et ses modules optionnels) sait écrire ce format."""
    if format_sortie == "HEIF":
        return HEIF_DISPONIBLE
    if format_sortie == "AVIF":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return bool(features.check("avif"))
    if format_sortie == "WEBP":
        return bool(features.check("webp"))
    return format_sortie in Image.SAVE


def get_readable_extensions() -> list:
    """Extensions de fichiers que l'application sait ouvrir (pour le sélecteur)."""
    Image.init()
    return sorted(
        extension
        for extension, nom_format in Image.registered_extensions().items()
        if nom_format in Image.OPEN and nom_format not in _FORMATS_LECTURE_EXCLUS
    )


@dataclass
class Reglages:
    """Réglages de conversion propres à une image."""

    format_sortie: str = FORMAT_PAR_DEFAUT
    niveau: int = NIVEAU_PAR_DEFAUT  # 1 = compression légère, 100 = très forte
    largeur_max: int = None
    hauteur_max: int = None
    supprimer_exif: bool = True

    @property
    def qualite(self) -> int:
        """Qualité au sens Pillow (100 = meilleure), inverse du niveau de compression."""
        return max(1, min(100, 101 - self.niveau))


@dataclass
class ImageSource:
    """Image ouverte et prête à être convertie (orientation EXIF déjà appliquée)."""

    chemin: str
    image: Image.Image
    format_origine: str  # nom Pillow : "JPEG", "PNG", "HEIF"...
    taille_octets: int
    exif: bytes = None  # EXIF sans le tag d'orientation (déjà appliqué aux pixels)
    icc_profile: bytes = None
    autres_metadonnees: bool = False  # XMP, IPTC, commentaires... (supprimés à la conversion)

    @property
    def dimensions(self) -> tuple:
        return self.image.size

    @property
    def a_des_metadonnees(self) -> bool:
        return bool(self.exif) or self.autres_metadonnees


@dataclass
class Conversion:
    """Résultat d'une conversion, encore en mémoire."""

    donnees: bytes
    format_sortie: str
    extension: str
    dimensions: tuple
    original_conserve: bool = False
    infos: list = field(default_factory=list)


def open_image(chemin: str) -> ImageSource:
    """Ouvre une image, applique son orientation EXIF et isole ses métadonnées.

    Seule la première image est lue pour les fichiers animés ou multi-pages
    (GIF animé, TIFF multi-pages...).
    """
    with Image.open(chemin) as fichier:
        format_origine = fichier.format or ""
        if format_origine == "MPO":  # JPEG "multi-images" de certains appareils photo
            format_origine = "JPEG"
        # exif_transpose retourne une copie chargée en mémoire : le fichier
        # peut être fermé, et l'image est remise dans le bon sens.
        image = ImageOps.exif_transpose(fichier)

    exif = image.getexif()
    exif.pop(0x0112, None)  # orientation, déjà appliquée aux pixels
    infos = image.info
    autres_metadonnees = any(
        cle in infos for cle in ("xmp", "XML:com.adobe.xmp", "iptc", "photoshop", "comment")
    )
    source = ImageSource(
        chemin=chemin,
        image=image,
        format_origine=format_origine,
        taille_octets=os.path.getsize(chemin),
        exif=exif.tobytes() if len(exif) else None,
        icc_profile=infos.get("icc_profile"),
        autres_metadonnees=autres_metadonnees,
    )
    # Certains encodeurs (HEIC notamment) recopient d'eux-mêmes tout ce qui
    # traîne dans image.info : on ne garde que le strict nécessaire, les
    # métadonnées voulues sont ensuite passées explicitement à l'enregistrement.
    image.info = {cle: infos[cle] for cle in _INFO_A_GARDER if cle in infos}
    return source


def compute_target_size(largeur: int, hauteur: int, largeur_max: int = None, hauteur_max: int = None):
    """Dimensions finales en gardant les proportions. Une limite absente ne
    contraint pas cet axe, et l'image n'est jamais agrandie."""
    ratio = min((largeur_max or largeur) / largeur, (hauteur_max or hauteur) / hauteur, 1)
    return max(1, round(largeur * ratio)), max(1, round(hauteur * ratio))


def _to_8_bits(image: Image.Image) -> Image.Image:
    """Ramène une image 16/32 bits en niveaux de gris 8 bits (sinon tout serait blanc)."""
    if image.mode not in ("I", "F"):
        image = image.convert("I")
    return image.point(lambda valeur: valeur * (1 / 256)).convert("L")


def _has_visible_transparency(image: Image.Image) -> bool:
    """True si au moins un pixel n'est pas totalement opaque (un PNG en RGBA
    est souvent entièrement opaque : sa couche alpha ne compte alors pas)."""
    if image.mode in ("RGBA", "LA", "PA"):
        return image.getchannel("A").getextrema()[0] < 255
    return image.has_transparency_data


def _flatten_on_white(image: Image.Image) -> Image.Image:
    """Remplace la transparence par un fond blanc."""
    rgba = image.convert("RGBA")
    fond = Image.new("RGB", rgba.size, (255, 255, 255))
    fond.paste(rgba, mask=rgba.getchannel("A"))
    return fond


def _prepare_mode(image: Image.Image, format_sortie: str) -> Image.Image:
    """Convertit le mode de couleur de l'image vers un mode accepté par le format."""
    if image.mode.startswith("I") or image.mode == "F":
        if format_sortie not in ("PNG", "TIFF"):
            image = _to_8_bits(image)

    transparence = image.has_transparency_data

    if format_sortie in ("JPEG", "BMP"):
        if transparence:
            return _flatten_on_white(image)
        modes_acceptes = ("RGB", "L", "CMYK") if format_sortie == "JPEG" else ("RGB", "L")
        return image if image.mode in modes_acceptes else image.convert("RGB")

    if format_sortie in ("PNG", "TIFF"):
        modes_acceptes = ("1", "L", "LA", "P", "RGB", "RGBA", "I", "I;16")
        if format_sortie == "TIFF":
            modes_acceptes += ("CMYK", "F")
        if image.mode in modes_acceptes:
            return image
        return image.convert("RGBA" if transparence else "RGB")

    # WebP, AVIF, HEIC, GIF, ICO : RGB, ou RGBA s'il y a de la transparence
    mode = "RGBA" if transparence else "RGB"
    return image if image.mode == mode else image.convert(mode)


def _color_space(mode: str) -> str:
    if mode in ("RGB", "RGBA", "P", "PA"):
        return "RGB"
    if mode in ("1", "L", "LA") or mode.startswith("I") or mode == "F":
        return "L"
    return mode


def _encode(image: Image.Image, format_sortie: str, reglages: Reglages, exif, icc_profile) -> bytes:
    options = {}
    if FORMATS[format_sortie].metadonnees:
        if icc_profile:
            options["icc_profile"] = icc_profile
        if exif:
            options["exif"] = exif

    qualite = reglages.qualite
    if format_sortie == "JPEG":
        options.update(quality=qualite, optimize=True)
    elif format_sortie == "WEBP":
        options.update(quality=qualite, method=4)
    elif format_sortie == "AVIF":
        options.update(quality=qualite, speed=6)
    elif format_sortie == "HEIF":
        options.update(quality=qualite)
        # Sans valeur explicite, pillow-heif va lui-même chercher l'EXIF/XMP
        # d'origine (via image.getexif()) : None = ne rien écrire.
        options.setdefault("exif", None)
        options["xmp"] = None
    elif format_sortie == "PNG":
        options.update(optimize=True)
    elif format_sortie == "TIFF":
        options.update(compression="tiff_lzw")
    elif format_sortie == "GIF":
        options.update(optimize=True)

    tampon = io.BytesIO()
    image.save(tampon, format_sortie, **options)
    return tampon.getvalue()


def convert_image(source: ImageSource, reglages: Reglages) -> Conversion:
    """Convertit l'image en mémoire selon les réglages.

    Garde-fou : si le format de sortie est celui d'origine, sans
    redimensionnement, et que le résultat serait plus lourd que l'original,
    l'original est conservé tel quel (sauf s'il contient des métadonnées à
    supprimer : la vie privée passe avant le poids).
    """
    format_sortie = reglages.format_sortie
    if format_sortie not in FORMATS:
        raise ValueError(f"Format de sortie inconnu : {format_sortie}")
    if not is_format_available(format_sortie):
        raise ValueError(f"Le format {FORMATS[format_sortie].libelle} n'est pas disponible")

    image = source.image
    taille_cible = compute_target_size(*image.size, reglages.largeur_max, reglages.hauteur_max)
    redimensionnee = taille_cible != image.size
    if redimensionnee:
        if image.mode in ("1", "P", "PA"):
            # Le mode palette ne se redimensionne qu'au "plus proche voisin" : on
            # repasse en couleurs complètes pour un résultat lisse.
            image = image.convert("RGBA" if image.has_transparency_data else "RGB")
        image = image.resize(taille_cible, Image.Resampling.LANCZOS, reducing_gap=3.0)

    mode_origine = image.mode
    transparence_visible = _has_visible_transparency(image)
    image = _prepare_mode(image, format_sortie)

    infos = []
    if transparence_visible and not image.has_transparency_data:
        infos.append("transparence remplacée par du blanc")

    # Un profil couleur ne reste valable que si l'espace de couleur n'a pas changé
    # (ex. un profil CMJN n'a plus de sens sur une image convertie en RVB).
    icc_profile = source.icc_profile if _color_space(mode_origine) == _color_space(image.mode) else None
    exif = None if reglages.supprimer_exif else source.exif

    donnees = _encode(image, format_sortie, reglages, exif, icc_profile)
    conversion = Conversion(
        donnees=donnees,
        format_sortie=format_sortie,
        extension=FORMATS[format_sortie].extension,
        dimensions=image.size,
        infos=infos,
    )

    meme_format = source.format_origine == format_sortie
    metadonnees_a_supprimer = reglages.supprimer_exif and source.a_des_metadonnees
    if (
        meme_format
        and not redimensionnee
        and not metadonnees_a_supprimer
        and len(donnees) >= source.taille_octets
    ):
        with open(source.chemin, "rb") as fichier:
            conversion.donnees = fichier.read()
        conversion.extension = os.path.splitext(source.chemin)[1]
        conversion.dimensions = source.dimensions
        conversion.original_conserve = True
        conversion.infos = []

    return conversion


def prepare_for_display(image: Image.Image) -> Image.Image:
    """Version 8 bits RVB (ou RVBA s'il y a de la transparence), affichable par Tkinter."""
    if image.mode.startswith("I") or image.mode == "F":
        image = _to_8_bits(image)
    mode = "RGBA" if _has_visible_transparency(image) else "RGB"
    return image if image.mode == mode else image.convert(mode)


def get_available_path(chemin: str) -> str:
    """Retourne un chemin qui n'existe pas encore, en ajoutant (1), (2)... si besoin."""
    if not os.path.exists(chemin):
        return chemin

    base, extension = os.path.splitext(chemin)
    i = 1
    while True:
        candidat = f"{base} ({i}){extension}"
        if not os.path.exists(candidat):
            return candidat
        i += 1


def save_conversion(chemin_source: str, conversion: Conversion, dossier_destination: str) -> str:
    """Écrit le fichier converti dans le dossier choisi, sans jamais écraser un
    fichier existant. Retourne le chemin du fichier créé."""
    nom_sans_extension = os.path.splitext(os.path.basename(chemin_source))[0]
    chemin_sortie = get_available_path(
        os.path.join(dossier_destination, nom_sans_extension + conversion.extension)
    )
    with open(chemin_sortie, "xb") as fichier:
        fichier.write(conversion.donnees)
    return chemin_sortie


def format_size(octets: float) -> str:
    """Poids lisible : Ko sous 1 Mo, Mo au-delà."""
    ko = octets / 1024
    if ko < 1024:
        return f"{ko:.1f} Ko"
    return f"{ko / 1024:.2f} Mo"
