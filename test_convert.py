"""
Tests de la logique de conversion (convert.py).

Lancer avec :  python -m unittest -v test_convert
"""

import io
import os
import shutil
import tempfile
import unittest

from PIL import Image

from convert import (
    FORMATS,
    Reglages,
    compute_target_size,
    convert_image,
    is_format_available,
    open_image,
    save_conversion,
)

FORMATS_DISPONIBLES = [nom for nom in FORMATS if is_format_available(nom)]
TAG_MARQUE = 0x010F  # "Make" (fabricant de l'appareil)
TAG_ORIENTATION = 0x0112
TAG_GPS = 0x8825


def make_photo(taille=(120, 80)) -> Image.Image:
    """Image en dégradé (compressible de façon réaliste, contrairement à un aplat)."""
    image = Image.new("RGB", taille)
    image.putdata([(x * 2 % 256, y * 3 % 256, (x + y) % 256) for y in range(taille[1]) for x in range(taille[0])])
    return image


def make_transparent_image() -> Image.Image:
    image = Image.new("RGBA", (64, 48), (0, 0, 0, 0))
    image.paste((30, 120, 250, 255), (10, 10, 40, 40))
    return image


def make_exif(orientation: int = 1) -> bytes:
    exif = Image.Exif()
    exif[TAG_MARQUE] = "AppareilTest"
    exif[TAG_ORIENTATION] = orientation
    exif.get_ifd(TAG_GPS)[2] = (48.0, 51.0, 24.0)  # latitude GPS
    return exif.tobytes()


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.mkdtemp(prefix="test_convertisseur_")

    def tearDown(self):
        shutil.rmtree(self.dossier, ignore_errors=True)

    def write(self, image: Image.Image, nom: str, **options) -> str:
        chemin = os.path.join(self.dossier, nom)
        image.save(chemin, **options)
        return chemin

    def sources(self):
        """Une image de test par format d'entrée courant (et quelques cas piégeux)."""
        photo = make_photo()
        transparente = make_transparent_image()
        palette = transparente.convert("P", palette=Image.Palette.ADAPTIVE)
        palette.info["transparency"] = palette.getpixel((0, 0))
        seize_bits = Image.new("I;16", (40, 30))
        seize_bits.putdata([i * 50 for i in range(40 * 30)])

        sources = {
            "jpeg": self.write(photo, "photo.jpg", quality=90),
            "png_rgba": self.write(transparente, "transparente.png"),
            "png_palette": self.write(palette, "palette.png"),
            "png_16_bits": self.write(seize_bits, "seize_bits.png"),
            "jpeg_cmjn": self.write(photo.convert("CMYK"), "cmjn.jpg"),
            "webp": self.write(transparente, "image.webp"),
            "bmp": self.write(photo, "image.bmp"),
            "tiff": self.write(photo, "image.tiff"),
            "gif": self.write(palette, "image.gif"),
            "ico": self.write(transparente, "image.ico"),
        }
        if is_format_available("AVIF"):
            sources["avif"] = self.write(photo, "image.avif")
        if is_format_available("HEIF"):
            sources["heic"] = self.write(photo, "image.heic", format="HEIF")
        return sources

    def test_every_input_converts_to_every_output(self):
        for nom_source, chemin in self.sources().items():
            source = open_image(chemin)
            for format_sortie in FORMATS_DISPONIBLES:
                with self.subTest(source=nom_source, sortie=format_sortie):
                    conversion = convert_image(source, Reglages(format_sortie=format_sortie))
                    with Image.open(io.BytesIO(conversion.donnees)) as resultat:
                        resultat.load()
                        if not conversion.original_conserve:
                            self.assertEqual(resultat.format, format_sortie)
                            self.assertEqual(resultat.size, conversion.dimensions)

    def test_exif_removed_by_default_and_orientation_applied(self):
        chemin = self.write(make_photo((120, 80)), "pivotee.jpg", exif=make_exif(orientation=6))
        source = open_image(chemin)
        # Orientation 6 = rotation de 90° : l'image doit être remise debout
        self.assertEqual(source.dimensions, (80, 120))

        for format_sortie in FORMATS_DISPONIBLES:
            with self.subTest(sortie=format_sortie):
                conversion = convert_image(source, Reglages(format_sortie=format_sortie))
                self.assertNotIn(b"AppareilTest", conversion.donnees)
                with Image.open(io.BytesIO(conversion.donnees)) as resultat:
                    exif = resultat.getexif()
                    self.assertNotIn(TAG_MARQUE, exif)
                    self.assertNotIn(TAG_GPS, exif)

    def test_exif_kept_on_request_without_orientation(self):
        chemin = self.write(make_photo(), "pivotee.jpg", exif=make_exif(orientation=6))
        source = open_image(chemin)
        for format_sortie in FORMATS_DISPONIBLES:
            if not FORMATS[format_sortie].metadonnees:
                continue
            with self.subTest(sortie=format_sortie):
                reglages = Reglages(format_sortie=format_sortie, supprimer_exif=False)
                conversion = convert_image(source, reglages)
                with Image.open(io.BytesIO(conversion.donnees)) as resultat:
                    exif = resultat.getexif()
                    self.assertEqual(exif.get(TAG_MARQUE), "AppareilTest")
                    # Les pixels sont déjà tournés : l'orientation ne doit plus être appliquée
                    self.assertIn(exif.get(TAG_ORIENTATION), (None, 1))

    def test_xmp_never_leaks(self):
        photo = make_photo()
        chemin = self.write(photo, "xmp.jpg", xmp=b"<x:xmpmeta>secret-gps</x:xmpmeta>")
        source = open_image(chemin)
        self.assertTrue(source.a_des_metadonnees)
        for format_sortie in FORMATS_DISPONIBLES:
            with self.subTest(sortie=format_sortie):
                conversion = convert_image(source, Reglages(format_sortie=format_sortie))
                self.assertNotIn(b"secret-gps", conversion.donnees)

    def test_transparency_kept_or_replaced_by_white(self):
        source = open_image(self.write(make_transparent_image(), "transparente.png"))

        jpeg = convert_image(source, Reglages(format_sortie="JPEG"))
        with Image.open(io.BytesIO(jpeg.donnees)) as resultat:
            # Pixel loin du carré bleu (le JPEG "bave" légèrement autour des bords)
            self.assertEqual(resultat.convert("RGB").getpixel((60, 45)), (255, 255, 255))
        self.assertIn("transparence remplacée par du blanc", jpeg.infos)

        for format_sortie in ("PNG", "WEBP", "GIF"):
            with self.subTest(sortie=format_sortie):
                conversion = convert_image(source, Reglages(format_sortie=format_sortie))
                self.assertEqual(conversion.infos, [])
                with Image.open(io.BytesIO(conversion.donnees)) as resultat:
                    self.assertEqual(resultat.convert("RGBA").getpixel((0, 0))[3], 0)

    def test_opaque_rgba_does_not_report_lost_transparency(self):
        opaque = make_photo().convert("RGBA")
        source = open_image(self.write(opaque, "opaque.png"))
        self.assertEqual(convert_image(source, Reglages(format_sortie="JPEG")).infos, [])

    def test_resize_keeps_ratio_and_never_enlarges(self):
        self.assertEqual(compute_target_size(4000, 3000, 1000, None), (1000, 750))
        self.assertEqual(compute_target_size(4000, 3000, None, 300), (400, 300))
        self.assertEqual(compute_target_size(4000, 3000, 1000, 1000), (1000, 750))
        self.assertEqual(compute_target_size(800, 600, 5000, 5000), (800, 600))

        source = open_image(self.write(make_photo((120, 80)), "photo.png"))
        conversion = convert_image(source, Reglages(format_sortie="WEBP", largeur_max=60))
        self.assertEqual(conversion.dimensions, (60, 40))

    def test_quality_follows_compression_level(self):
        source = open_image(self.write(make_photo((300, 200)), "photo.png"))
        leger = convert_image(source, Reglages(format_sortie="JPEG", niveau=5))
        fort = convert_image(source, Reglages(format_sortie="JPEG", niveau=90))
        self.assertLess(len(fort.donnees), len(leger.donnees))

    def test_original_kept_when_conversion_is_heavier(self):
        # JPEG très compressé, reconverti en JPEG quasi sans compression : plus lourd
        chemin = self.write(make_photo((300, 200)), "deja_compresse.jpg", quality=20)
        source = open_image(chemin)
        conversion = convert_image(source, Reglages(format_sortie="JPEG", niveau=1))
        self.assertTrue(conversion.original_conserve)
        with open(chemin, "rb") as fichier:
            self.assertEqual(conversion.donnees, fichier.read())

    def test_original_not_kept_when_metadata_must_be_removed(self):
        chemin = self.write(make_photo((300, 200)), "avec_exif.jpg", quality=20, exif=make_exif())
        conversion = convert_image(open_image(chemin), Reglages(format_sortie="JPEG", niveau=1))
        self.assertFalse(conversion.original_conserve)
        self.assertNotIn(b"AppareilTest", conversion.donnees)

    def test_save_never_overwrites(self):
        chemin = self.write(make_photo(), "photo.png")
        conversion = convert_image(open_image(chemin), Reglages(format_sortie="WEBP"))
        destination = os.path.join(self.dossier, "sortie")
        os.mkdir(destination)
        premier = save_conversion(chemin, conversion, destination)
        second = save_conversion(chemin, conversion, destination)
        self.assertEqual(os.path.basename(premier), "photo.webp")
        self.assertEqual(os.path.basename(second), "photo (1).webp")


if __name__ == "__main__":
    unittest.main()
