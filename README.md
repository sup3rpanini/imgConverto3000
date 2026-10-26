# Convertisseur d'images (OfflineConverter)

Application de bureau (Python + Tkinter) pour convertir des photos d'un format
à l'autre, par lot et **entièrement hors ligne** : aucune image ne quitte le PC.

Interface et expérience utilisateur reprises du *Compresseur d'images*
(thème sombre inspiré de Shutter Encoder, voir `UI-UX.md` de ce projet-là).

## Formats

| Lecture | Écriture |
|---|---|
| JPEG, PNG, WebP, AVIF, HEIC/HEIF, BMP, TIFF, GIF, ICO, PSD, TGA, JPEG 2000, QOI, DDS, PPM… (tout ce que Pillow sait ouvrir) | JPEG, PNG, WebP, AVIF, HEIC, BMP, TIFF, GIF, ICO |

## Fonctionnalités

- **Aperçu avant / après** calculé en mémoire : poids, format, dimensions et
  variation de poids, avant même d'enregistrer. Clic sur une miniature pour
  l'agrandir. La transparence est affichée sur un damier.
- **Réglages par image** (format, niveau de compression, taille max) avec des
  cases pour les appliquer à tout le lot.
- **Compression** réglable pour les formats avec perte (JPEG, WebP, AVIF, HEIC) ;
  le slider est désactivé pour les formats sans perte.
- **Redimensionnement** optionnel (jamais d'agrandissement, proportions conservées).
- **Vie privée** : métadonnées EXIF (GPS, appareil, date…) et XMP supprimées par
  défaut. L'orientation de la photo est appliquée aux pixels, donc l'image reste
  dans le bon sens.
- **Garde-fou** : si le format ne change pas et que le résultat serait plus lourd,
  l'original est conservé.
- Les fichiers existants ne sont **jamais écrasés** (`photo (1).webp`…).
- Traitement en **tâche de fond** (la fenêtre ne se fige pas), bouton **Annuler**,
  bouton **Ouvrir le dossier** à la fin.
- **Glisser-déposer** de fichiers ou de dossiers dans la fenêtre.
- Raccourcis : `Ctrl+O` ouvrir, `Suppr` retirer l'image, `←` `→` naviguer,
  `Ctrl+S` enregistrer, `Échap` annuler l'enregistrement.

## Installation

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Lancement

```bash
.venv\Scripts\python.exe app.py
```

Des fichiers peuvent aussi être passés en argument : `app.py photo1.heic photo2.png`.

## Tests

```bash
.venv\Scripts\python.exe -m unittest -v test_convert
```

## Créer l'exécutable et l'installateur

Prérequis : [Inno Setup 6](https://jrsoftware.org/isinfo.php) (gratuit) et PyInstaller
dans l'environnement virtuel :

```bash
.venv\Scripts\python.exe -m pip install pyinstaller
```

Puis lancer `build.bat`, qui enchaîne les deux étapes :

1. **PyInstaller** construit `dist\ConvertisseurImages.exe` (un seul fichier, sans
   console, avec les métadonnées de `version.txt`). Les options `--collect-all`
   embarquent les bibliothèques natives de pillow-heif (HEIC) et de tkinterdnd2
   (glisser-déposer), que PyInstaller ne détecte pas seul.
2. **Inno Setup** compile `installer.iss` en `installer_output\ConvertisseurImages_Setup.exe`
   (installation dans Program Files, raccourci menu Démarrer, raccourci Bureau en option).

`installer.iss` doit rester enregistré en **UTF-8 avec BOM**, sinon les accents
s'affichent mal dans l'installateur. Pour une nouvelle version, changer
`MyAppVersion` dans `installer.iss` et les numéros de `version.txt` (garder le même `AppId`).

`dist/`, `build/`, `*.spec` et `installer_output/` ne sont pas versionnés.

## Structure

- `app.py` : interface graphique (fenêtre, aperçu, threads de fond)
- `convert.py` : logique de conversion, sans interface (testable seule)
- `theme.py` : palette du thème sombre, styles ttk, barre de progression
- `test_convert.py` : tests de conversion (tous les formats, EXIF, transparence…)
- `build.bat`, `version.txt`, `installer.iss` : construction de l'exe et de l'installateur

## Limites connues

- Les images animées (GIF, WebP animé) et les TIFF multi-pages sont convertis
  à partir de leur **première image** seulement.
- Pas de prise en charge des photos **RAW** (CR2, NEF, ARW…) ni du **SVG**.
- Les images CMJN converties en RVB perdent leur profil couleur (conversion simple).
- Interface uniquement en français, thème sombre uniquement.
