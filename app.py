"""
Convertisseur d'images - interface graphique (Tkinter)

Convertit des photos d'un format à l'autre (JPEG, PNG, WebP, AVIF, HEIC...),
par lot et entièrement hors ligne. La logique de conversion est dans
convert.py, le thème sombre dans theme.py.

Les calculs lourds (aperçu, enregistrement) tournent dans des threads en tâche
de fond pour que la fenêtre ne se fige jamais. Tkinter n'étant pas
thread-safe, ces threads ne touchent jamais aux widgets : ils déposent leurs
résultats dans une file de messages, lue régulièrement par le thread principal.
"""

import io
import os
import queue
import sys
import threading
import tkinter as tk
from dataclasses import dataclass, field, replace
from tkinter import ttk, filedialog, messagebox

from PIL import Image, ImageTk

from convert import (
    FORMATS,
    FORMAT_PAR_DEFAUT,
    NIVEAU_PAR_DEFAUT,
    Reglages,
    compute_target_size,
    convert_image,
    format_size,
    get_readable_extensions,
    is_format_available,
    open_image,
    prepare_for_display,
    save_conversion,
)
from theme import (
    ACCENT,
    ACCENT_BTN,
    BG,
    BORDER,
    ERROR,
    FONT,
    MUTED,
    PANEL_BG_LIGHT,
    SUCCESS,
    TEXT,
    ProgressBar,
    configure_dark_style,
)

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    GLISSER_DEPOSER_DISPONIBLE = True
except ImportError:
    GLISSER_DEPOSER_DISPONIBLE = False

TAILLE_APERCU = (200, 170)
DELAI_SAISIE_MS = 400  # anti-rafale de frappe dans les champs de taille
INTERVALLE_MESSAGES_MS = 40  # fréquence de lecture des messages des threads
EXTENSIONS_LISIBLES = get_readable_extensions()


def make_thumbnail(image: Image.Image, taille_max) -> Image.Image:
    """Miniature affichable, sur damier si l'image a de la transparence (pour
    voir d'un coup d'œil si elle a été conservée ou remplacée par du blanc)."""
    image = prepare_for_display(image)
    taille = compute_target_size(*image.size, *taille_max)
    if taille != image.size:
        image = image.resize(taille, Image.Resampling.LANCZOS, reducing_gap=2.0)
    if image.mode != "RGBA":
        return image

    case = 8
    motif = Image.new("RGB", (case * 2, case * 2), "#3a3b40")
    motif.paste("#2c2d31", (0, 0, case, case))
    motif.paste("#2c2d31", (case, case, case * 2, case * 2))
    damier = Image.new("RGB", image.size)
    for y in range(0, image.height, case * 2):
        for x in range(0, image.width, case * 2):
            damier.paste(motif, (x, y))
    damier.paste(image, mask=image.getchannel("A"))
    return damier


def format_label(nom_format: str) -> str:
    """Nom lisible d'un format Pillow ("HEIF" -> "HEIC", formats inconnus tels quels)."""
    return FORMATS[nom_format].libelle if nom_format in FORMATS else (nom_format or "?")


@dataclass
class Apercu:
    """Résultat d'un calcul d'aperçu, envoyé par le thread d'aperçu."""

    jeton: int
    chemin: str
    miniature_avant: Image.Image = None
    format_avant: str = ""
    taille_avant: int = 0
    dimensions_avant: tuple = None
    miniature_apres: Image.Image = None
    donnees_apres: bytes = None
    format_apres: str = ""
    dimensions_apres: tuple = None
    original_conserve: bool = False
    infos: list = field(default_factory=list)
    erreur: str = None


@dataclass
class Rapport:
    """Bilan d'un enregistrement, envoyé par le thread d'enregistrement."""

    total: int
    dossier: str
    reussites: int = 0
    originaux_conserves: int = 0
    octets_avant: int = 0
    octets_apres: int = 0
    echecs: list = field(default_factory=list)
    annule: bool = False


class PreviewWorker:
    """Thread unique qui calcule les aperçus.

    Il ne traite que la DERNIÈRE demande reçue : si l'utilisateur enchaîne
    les réglages ou les images, les demandes intermédiaires sont ignorées au
    lieu de s'empiler.
    """

    def __init__(self, file_messages: queue.Queue):
        self._file_messages = file_messages
        self._demande = None
        self._verrou = threading.Lock()
        self._signal = threading.Event()
        # Dernière image ouverte : évite de relire le fichier à chaque réglage
        self._cache = None  # ((chemin, date de modif), ImageSource, miniature)
        threading.Thread(target=self._run, daemon=True).start()

    def request(self, jeton: int, chemin: str, reglages: Reglages):
        with self._verrou:
            self._demande = (jeton, chemin, reglages)
        self._signal.set()

    def _run(self):
        while True:
            self._signal.wait()
            with self._verrou:
                demande, self._demande = self._demande, None
                self._signal.clear()
            if demande is not None:
                self._file_messages.put(("apercu", self._compute(*demande)))

    def _get_source(self, chemin: str):
        cle = (chemin, os.path.getmtime(chemin))
        if self._cache is None or self._cache[0] != cle:
            source = open_image(chemin)
            self._cache = (cle, source, make_thumbnail(source.image, TAILLE_APERCU))
        return self._cache[1], self._cache[2]

    def _compute(self, jeton: int, chemin: str, reglages: Reglages) -> Apercu:
        apercu = Apercu(jeton=jeton, chemin=chemin)
        try:
            source, apercu.miniature_avant = self._get_source(chemin)
        except Exception as erreur:
            apercu.erreur = f"Impossible d'ouvrir l'image : {erreur}"
            return apercu

        apercu.format_avant = format_label(source.format_origine)
        apercu.taille_avant = source.taille_octets
        apercu.dimensions_avant = source.dimensions

        try:
            conversion = convert_image(source, reglages)
            apercu.donnees_apres = conversion.donnees
            apercu.format_apres = format_label(
                source.format_origine if conversion.original_conserve else conversion.format_sortie
            )
            apercu.dimensions_apres = conversion.dimensions
            apercu.original_conserve = conversion.original_conserve
            apercu.infos = conversion.infos
            with Image.open(io.BytesIO(conversion.donnees)) as resultat:
                apercu.miniature_apres = make_thumbnail(resultat, TAILLE_APERCU)
        except Exception as erreur:
            apercu.erreur = str(erreur)
        return apercu


def save_all(taches, dossier: str, annulation: threading.Event, file_messages: queue.Queue):
    """Convertit et enregistre chaque image (exécuté dans un thread de fond)."""
    rapport = Rapport(total=len(taches), dossier=dossier)
    for i, (chemin, reglages) in enumerate(taches, start=1):
        if annulation.is_set():
            rapport.annule = True
            break
        try:
            source = open_image(chemin)
            conversion = convert_image(source, reglages)
            save_conversion(chemin, conversion, dossier)
            rapport.reussites += 1
            rapport.octets_avant += source.taille_octets
            rapport.octets_apres += len(conversion.donnees)
            if conversion.original_conserve:
                rapport.originaux_conserves += 1
        except Exception as erreur:
            rapport.echecs.append(f"{os.path.basename(chemin)} : {erreur}")
        file_messages.put(("progression", (i, len(taches))))
    file_messages.put(("fin", rapport))


def set_enabled(widget, actif: bool):
    """Active/désactive un widget ttk ; la main n'apparaît que s'il est cliquable."""
    widget.state(["!disabled"] if actif else ["disabled"])
    widget.configure(cursor="hand2" if actif else "")


class ConvertisseurApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Convertisseur d'images")
        self.root.geometry("560x800")
        self.root.minsize(500, 480)

        configure_dark_style(self.root)

        self.chemins_images = []
        self.reglages_par_image = {}  # chemin source -> Reglages propres à cette image
        self._miniature_avant_tk = None  # garde une référence pour éviter le garbage collector
        self._miniature_apres_tk = None
        self._chemin_affiche = None  # image dont l'aperçu est actuellement affiché
        self._donnees_apres_affichees = None  # octets de l'aperçu "Après" (pour l'agrandissement)
        self._tache_apercu_programmee = None  # id du .after() en attente (anti-rafale de frappe)
        self._jeton_apercu = 0  # permet d'ignorer les aperçus devenus obsolètes
        self._annulation = None  # threading.Event de l'enregistrement en cours, sinon None
        self._dernier_dossier = None  # dossier proposé par défaut au prochain enregistrement
        self._dossier_resultat = None  # dossier ouvert par le bouton "Ouvrir le dossier"

        self._file_messages = queue.Queue()
        self._apercus = PreviewWorker(self._file_messages)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_scrollable_page()
        self._build_files_card()
        self._build_preview_card()
        self._build_format_card()
        self._build_compression_card()
        self._build_size_card()
        self._build_privacy_card()
        self._build_save_area()
        self._bind_shortcuts()
        if GLISSER_DEPOSER_DISPONIBLE:
            self._enable_drag_and_drop()

        self.update_format_controls()
        self.update_buttons()
        self.root.after(INTERVALLE_MESSAGES_MS, self.process_messages)

    # ------------------------------------------------------------------
    # Construction de l'interface
    # ------------------------------------------------------------------

    def _build_scrollable_page(self):
        """Conteneur défilable : tout le contenu peut dépasser la hauteur de la
        fenêtre (ex. quand l'aperçu s'affiche) sans jamais rien masquer."""
        conteneur = tk.Frame(self.root, bg=BG)
        conteneur.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(conteneur, bg=BG, highlightthickness=0, yscrollincrement=20)
        barre_defilement = ttk.Scrollbar(conteneur, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=barre_defilement.set)
        barre_defilement.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        contenu = ttk.Frame(self.canvas)
        id_fenetre = self.canvas.create_window((0, 0), window=contenu, anchor="nw")
        contenu.bind(
            "<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        # Le contenu occupe toujours toute la largeur visible du canvas
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(id_fenetre, width=e.width))
        self.root.bind_all("<MouseWheel>", self.on_mousewheel)

        # "zone" est le parent réel de toutes les sections, avec la marge intérieure
        self.zone = ttk.Frame(contenu)
        self.zone.pack(fill="both", expand=True, padx=16, pady=16)

    def _create_card(self, titre: str, sous_titre: str = None):
        """Crée un bloc ("carte") avec son titre en majuscules. Retourne
        (carte, intérieur) ; la carte n'est pas encore placée dans la page."""
        carte = ttk.Frame(self.zone, style="Card.TFrame")
        interieur = ttk.Frame(carte, style="Card.TFrame")
        interieur.pack(fill="x", padx=14, pady=12)
        ttk.Label(interieur, text=titre, style="CardSection.TLabel").pack(
            anchor="w", pady=(0, 2 if sous_titre else 10)
        )
        if sous_titre:
            ttk.Label(interieur, text=sous_titre, style="CardMuted.TLabel").pack(
                anchor="w", pady=(0, 8)
            )
        return carte, interieur

    def _build_files_card(self):
        carte, interieur = self._create_card("CHOIX DES FICHIERS")
        carte.pack(fill="x", pady=(0, 12))

        boutons = ttk.Frame(interieur, style="Card.TFrame")
        boutons.pack(fill="x", pady=(0, 8))
        ttk.Button(
            boutons, text="Choisir des images...", command=self.select_files, cursor="hand2"
        ).pack(side="left")
        self.bouton_retirer = ttk.Button(
            boutons, text="Retirer", style="Small.TButton", command=self.remove_selected
        )
        self.bouton_retirer.pack(side="left", padx=(8, 0))
        self.bouton_vider = ttk.Button(
            boutons, text="Vider", style="Small.TButton", command=self.clear_files
        )
        self.bouton_vider.pack(side="left", padx=(8, 0))

        self.label_selection = ttk.Label(interieur, style="CardMuted.TLabel")
        self.label_selection.pack(fill="x", pady=(0, 8))
        self.update_selection_label()

        self.cadre_liste = tk.Frame(
            interieur, bg=PANEL_BG_LIGHT, highlightthickness=1, highlightbackground=BORDER
        )
        self.cadre_liste.pack(fill="x")
        barre_liste = ttk.Scrollbar(self.cadre_liste, orient="vertical")
        self.liste_fichiers = tk.Listbox(
            self.cadre_liste,
            height=7,
            yscrollcommand=barre_liste.set,
            exportselection=False,
            activestyle="none",
            bg=PANEL_BG_LIGHT,
            fg=TEXT,
            selectbackground=ACCENT_BTN,
            selectforeground="#ffffff",
            highlightthickness=0,
            relief="flat",
            borderwidth=0,
            font=FONT,
        )
        barre_liste.config(command=self.liste_fichiers.yview)
        self.liste_fichiers.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        barre_liste.pack(side="right", fill="y")
        self.liste_fichiers.bind("<<ListboxSelect>>", self.on_list_selection)

    def _build_preview_card(self):
        # Masquée tant qu'aucune image n'est chargée (voir add_files)
        self.carte_apercu, interieur = self._create_card(
            "APERÇU", "cliquer sur une miniature pour l'agrandir"
        )

        navigation = ttk.Frame(interieur, style="Card.TFrame")
        navigation.pack(fill="x", pady=(0, 8))
        ttk.Button(
            navigation, text="◀", width=3, style="Small.TButton",
            command=lambda: self.navigate(-1), cursor="hand2",
        ).pack(side="left")
        self.label_position = ttk.Label(navigation, anchor="center", style="Card.TLabel")
        self.label_position.pack(side="left", expand=True, fill="x")
        ttk.Button(
            navigation, text="▶", width=3, style="Small.TButton",
            command=lambda: self.navigate(1), cursor="hand2",
        ).pack(side="right")

        colonnes = ttk.Frame(interieur, style="Card.TFrame")
        colonnes.pack(fill="x")
        colonnes.columnconfigure((0, 1), weight=1, uniform="apercu")
        self.apercu_avant = self._build_preview_column(colonnes, 0, "Avant", "avant")
        self.apercu_apres = self._build_preview_column(colonnes, 1, "Après", "apres")

    def _build_preview_column(self, parent, colonne: int, titre: str, cote: str) -> dict:
        cadre = ttk.Frame(parent, style="Card.TFrame")
        cadre.grid(row=0, column=colonne, sticky="n")
        ttk.Label(cadre, text=titre, style="CardMuted.TLabel").pack()

        # Taille fixe : la mise en page ne "saute" pas pendant le calcul
        boite = tk.Frame(
            cadre,
            width=TAILLE_APERCU[0] + 8,
            height=TAILLE_APERCU[1] + 8,
            bg=PANEL_BG_LIGHT,
            highlightthickness=1,
            highlightbackground=BORDER,
        )
        boite.pack_propagate(False)
        boite.pack(pady=5)
        miniature = tk.Label(boite, bg=PANEL_BG_LIGHT, fg=MUTED, font=FONT)
        miniature.pack(fill="both", expand=True)
        miniature.bind("<Button-1>", lambda _e: self.open_fullsize_preview(cote))

        poids = ttk.Label(cadre, style="Card.TLabel")
        poids.pack()
        dimensions = ttk.Label(cadre, style="CardMuted.TLabel")
        dimensions.pack()
        info = ttk.Label(
            cadre, style="CardMuted.TLabel", foreground=ACCENT,
            wraplength=TAILLE_APERCU[0] + 8, justify="center",
        )
        info.pack()
        return {"miniature": miniature, "poids": poids, "dimensions": dimensions, "info": info}

    def _build_format_card(self):
        self.carte_format, interieur = self._create_card("FORMAT DE SORTIE")
        self.carte_format.pack(fill="x", pady=(0, 12))

        grille = ttk.Frame(interieur, style="Card.TFrame")
        grille.pack(fill="x", pady=(0, 6))
        self.format_var = tk.StringVar(value=FORMAT_PAR_DEFAUT)
        colonnes = 5
        for i, (nom_format, format_sortie) in enumerate(FORMATS.items()):
            disponible = is_format_available(nom_format)
            bouton = ttk.Radiobutton(
                grille,
                text=format_sortie.libelle,
                value=nom_format,
                variable=self.format_var,
                command=self.on_format_change,
                style="Card.TRadiobutton",
            )
            set_enabled(bouton, disponible)
            bouton.grid(row=i // colonnes, column=i % colonnes, sticky="w", pady=2)
        grille.columnconfigure(tuple(range(colonnes)), weight=1, uniform="format")

        self.label_description_format = ttk.Label(
            interieur, style="CardMuted.TLabel", justify="left"
        )
        self.label_description_format.pack(fill="x", pady=(0, 8))
        interieur.bind(
            "<Configure>",
            lambda e: self.label_description_format.configure(wraplength=e.width - 4),
            add="+",
        )

        self.appliquer_format_toutes_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            interieur,
            text="Convertir toutes les images dans ce format",
            variable=self.appliquer_format_toutes_var,
            command=self.on_toggle_apply_format_to_all,
            style="Card.TCheckbutton",
            cursor="hand2",
        ).pack(anchor="w")

    def _build_compression_card(self):
        carte, interieur = self._create_card("COMPRESSION")
        carte.pack(fill="x", pady=(0, 12))

        # Plus on va vers la droite, plus l'image est compressée (donc plus la
        # qualité résultante est basse) : voir Reglages.qualite dans convert.py.
        titre = ttk.Frame(interieur, style="Card.TFrame")
        titre.pack(fill="x")
        ttk.Label(titre, text="Niveau (image sélectionnée)", style="Card.TLabel").pack(side="left")
        self.label_niveau = ttk.Label(titre, style="CardAccent.TLabel")
        self.label_niveau.pack(side="right")

        self.niveau_var = tk.IntVar(value=NIVEAU_PAR_DEFAUT)
        self.slider_niveau = ttk.Scale(
            interieur,
            from_=1,
            to=100,
            orient="horizontal",
            variable=self.niveau_var,
            command=lambda _v: self.update_level_label(),
        )
        self.slider_niveau.pack(fill="x", pady=(4, 4))
        # Aperçu recalculé au relâchement seulement (pas à chaque pixel de mouvement)
        self.slider_niveau.bind("<ButtonRelease-1>", self.on_slider_release)
        self.slider_niveau.bind("<KeyRelease>", self.on_slider_release)  # réglage aux flèches

        self.label_info_compression = ttk.Label(interieur, style="CardMuted.TLabel")
        self.label_info_compression.pack(anchor="w", pady=(0, 4))

        self.appliquer_compression_toutes_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            interieur,
            text="Compresser toutes les images en même temps (même niveau pour toutes)",
            variable=self.appliquer_compression_toutes_var,
            command=self.on_toggle_apply_compression_to_all,
            style="Card.TCheckbutton",
            cursor="hand2",
        ).pack(anchor="w")

    def _build_size_card(self):
        carte, interieur = self._create_card("TAILLE")
        carte.pack(fill="x", pady=(0, 12))

        ligne = ttk.Frame(interieur, style="Card.TFrame")
        ligne.pack(fill="x", pady=(0, 8))
        ttk.Label(ligne, text="Taille max (optionnel)", style="Card.TLabel").pack(side="left")

        self.largeur_max_var = tk.StringVar()
        self.hauteur_max_var = tk.StringVar()
        champ_largeur = ttk.Entry(ligne, textvariable=self.largeur_max_var, width=6)
        champ_largeur.pack(side="left", padx=(12, 2))
        ttk.Label(ligne, text="x", style="Card.TLabel").pack(side="left")
        champ_hauteur = ttk.Entry(ligne, textvariable=self.hauteur_max_var, width=6)
        champ_hauteur.pack(side="left", padx=(2, 5))
        ttk.Label(ligne, text="px", style="CardMuted.TLabel").pack(side="left")
        champ_largeur.bind("<KeyRelease>", lambda _e: self.on_dimension_input("largeur"))
        champ_hauteur.bind("<KeyRelease>", lambda _e: self.on_dimension_input("hauteur"))

        self.lier_dimensions_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            interieur,
            text="Lier largeur et hauteur (même valeur pour les deux)",
            variable=self.lier_dimensions_var,
            command=self.on_toggle_link_dimensions,
            style="Card.TCheckbutton",
            cursor="hand2",
        ).pack(anchor="w", pady=(0, 4))

        self.appliquer_taille_toutes_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            interieur,
            text="Redimensionner toutes les images en même temps (même taille max pour toutes)",
            variable=self.appliquer_taille_toutes_var,
            command=self.on_toggle_apply_resize_to_all,
            style="Card.TCheckbutton",
            cursor="hand2",
        ).pack(anchor="w")

    def _build_privacy_card(self):
        carte, interieur = self._create_card("CONFIDENTIALITÉ")
        carte.pack(fill="x", pady=(0, 16))

        self.supprimer_exif_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            interieur,
            text="Supprimer les métadonnées EXIF (position GPS, appareil, date...)",
            variable=self.supprimer_exif_var,
            command=self.update_preview,
            style="Card.TCheckbutton",
            cursor="hand2",
        ).pack(anchor="w")

    def _build_save_area(self):
        self.bouton_enregistrer = ttk.Button(
            self.zone, text="Enregistrer sur le PC", command=self.save_images, style="Accent.TButton"
        )
        self.bouton_enregistrer.pack(fill="x", pady=(0, 12))

        # Barre de progression + Annuler : visibles seulement pendant l'enregistrement
        self.cadre_progression = ttk.Frame(self.zone)
        self.progress = ProgressBar(self.cadre_progression, height=22)
        self.progress.pack(side="left", fill="x", expand=True)
        self.bouton_annuler = ttk.Button(
            self.cadre_progression, text="Annuler", style="Small.TButton", command=self.cancel_save
        )
        self.bouton_annuler.pack(side="right", padx=(8, 0))

        self.label_resultat = ttk.Label(self.zone, justify="left")
        self.label_resultat.pack(fill="x")
        self.zone.bind(
            "<Configure>", lambda e: self.label_resultat.configure(wraplength=e.width - 4), add="+"
        )

        self.bouton_ouvrir_dossier = ttk.Button(
            self.zone, text="Ouvrir le dossier", style="Small.TButton",
            command=self.open_destination_folder, cursor="hand2",
        )

        raccourcis = "Ctrl+O ouvrir  ·  Suppr retirer  ·  ← → naviguer  ·  Ctrl+S enregistrer"
        self.label_raccourcis = ttk.Label(self.zone, text=raccourcis, style="Muted.TLabel")
        self.label_raccourcis.pack(anchor="w", pady=(16, 0))

    def _bind_shortcuts(self):
        def hors_champ_texte(action):
            # Les flèches et Suppr gardent leur rôle normal dans un champ de saisie / le slider
            def gestionnaire(event):
                if event.widget.winfo_class() in ("TEntry", "Entry", "TScale"):
                    return None
                action()
                return "break"

            return gestionnaire

        self.root.bind("<Control-o>", lambda _e: self.select_files())
        self.root.bind("<Control-s>", lambda _e: self.save_images())
        self.root.bind("<Control-Return>", lambda _e: self.save_images())
        self.root.bind("<Delete>", hors_champ_texte(self.remove_selected))
        self.root.bind("<Left>", hors_champ_texte(lambda: self.navigate(-1)))
        self.root.bind("<Right>", hors_champ_texte(lambda: self.navigate(1)))
        self.root.bind("<Escape>", lambda _e: self.cancel_save())

    def _enable_drag_and_drop(self):
        """Glisser-déposer de fichiers ou de dossiers n'importe où dans la fenêtre."""
        def surbrillance(active: bool):
            self.cadre_liste.configure(highlightbackground=ACCENT if active else BORDER)

        def entree(event):
            surbrillance(True)
            return event.action

        def sortie(event):
            surbrillance(False)
            return event.action

        def depot(event):
            surbrillance(False)
            self.add_files(self.root.tk.splitlist(event.data))
            return event.action

        self.root.drop_target_register(DND_FILES)
        self.root.dnd_bind("<<DropEnter>>", entree)
        self.root.dnd_bind("<<DropLeave>>", sortie)
        self.root.dnd_bind("<<Drop>>", depot)

    # ------------------------------------------------------------------
    # Fichiers
    # ------------------------------------------------------------------

    def select_files(self):
        motifs = " ".join(f"*{extension}" for extension in EXTENSIONS_LISIBLES)
        chemins = filedialog.askopenfilenames(
            title="Choisir une ou plusieurs images à convertir",
            filetypes=[("Images", motifs), ("Tous les fichiers", "*.*")],
        )
        if chemins:
            self.add_files(chemins)

    def add_files(self, chemins):
        """Ajoute des fichiers à la liste (les dossiers déposés sont parcourus,
        sans leurs sous-dossiers). Les doublons sont ignorés."""
        nouveaux = []
        for chemin in chemins:
            chemin = os.path.normpath(chemin)
            if os.path.isdir(chemin):
                contenu = sorted(os.path.join(chemin, nom) for nom in os.listdir(chemin))
                candidats = [
                    c for c in contenu
                    if os.path.isfile(c) and os.path.splitext(c)[1].lower() in EXTENSIONS_LISIBLES
                ]
            else:
                candidats = [chemin] if os.path.isfile(chemin) else []
            for candidat in candidats:
                if candidat not in self.reglages_par_image and candidat not in nouveaux:
                    nouveaux.append(candidat)
        if not nouveaux:
            return

        modele = self._settings_for_new_image()
        for chemin in nouveaux:
            self.reglages_par_image[chemin] = replace(modele)
        premier_nouvel_index = len(self.chemins_images)
        self.chemins_images.extend(nouveaux)

        self.refresh_file_list()
        self.update_selection_label()
        self.hide_result()
        # La section aperçu ne s'affiche qu'une fois des images chargées
        self.carte_apercu.pack(fill="x", pady=(0, 12), before=self.carte_format)
        # Sélectionne automatiquement la première image ajoutée : l'aperçu
        # s'affiche tout de suite, sans clic.
        self.select_index(premier_nouvel_index)

    def _settings_for_new_image(self) -> Reglages:
        """Une image ajoutée hérite des réglages appliqués "à toutes les images"."""
        reglages = Reglages()
        if self.appliquer_format_toutes_var.get():
            reglages.format_sortie = self.format_var.get()
        if self.appliquer_compression_toutes_var.get():
            reglages.niveau = self.niveau_var.get()
        if self.appliquer_taille_toutes_var.get():
            reglages.largeur_max, reglages.hauteur_max = self.get_max_dimensions()
        return reglages

    def remove_selected(self):
        index = self.selected_index()
        if index is None:
            return
        chemin = self.chemins_images.pop(index)
        del self.reglages_par_image[chemin]
        if not self.chemins_images:
            self.clear_files()
            return
        self.refresh_file_list()
        self.update_selection_label()
        self.select_index(min(index, len(self.chemins_images) - 1))

    def clear_files(self):
        """Vide la liste et réinitialise l'interface (bouton "Vider")."""
        self.chemins_images = []
        self.reglages_par_image = {}
        self._jeton_apercu += 1  # tout aperçu encore en calcul sera ignoré
        self._chemin_affiche = None
        self._donnees_apres_affichees = None
        self.liste_fichiers.delete(0, tk.END)
        self.update_selection_label()
        self.hide_result()
        self.carte_apercu.pack_forget()
        self.update_buttons()

    def refresh_file_list(self):
        """Redessine la liste en conservant la sélection ; chaque ligne montre
        le format de sortie et le niveau propres à l'image."""
        index = self.selected_index()
        self.liste_fichiers.delete(0, tk.END)
        for chemin in self.chemins_images:
            self.liste_fichiers.insert(tk.END, self.describe_file(chemin))
        if index is not None and index < len(self.chemins_images):
            self.liste_fichiers.selection_set(index)
            self.liste_fichiers.see(index)

    def describe_file(self, chemin: str) -> str:
        reglages = self.reglages_par_image[chemin]
        description = FORMATS[reglages.format_sortie]
        try:
            taille = format_size(os.path.getsize(chemin))
        except OSError:
            taille = "introuvable"
        texte = f"{os.path.basename(chemin)} ({taille})  →  {description.libelle}"
        if description.avec_perte:
            texte += f", niveau {reglages.niveau}"
        return texte

    def update_selection_label(self):
        if not self.chemins_images:
            texte = "Aucune image sélectionnée"
            if GLISSER_DEPOSER_DISPONIBLE:
                texte += " — vous pouvez aussi glisser-déposer des fichiers ici"
            self.label_selection.config(text=texte, foreground=MUTED)
            return
        total = 0
        for chemin in self.chemins_images:
            try:
                total += os.path.getsize(chemin)
            except OSError:
                pass
        nb = len(self.chemins_images)
        pluriel = "s" if nb > 1 else ""
        self.label_selection.config(
            text=f"{nb} image{pluriel} sélectionnée{pluriel} — {format_size(total)} au total",
            foreground=TEXT,
        )

    def update_buttons(self):
        a_des_images = bool(self.chemins_images)
        set_enabled(self.bouton_vider, a_des_images)
        set_enabled(self.bouton_retirer, self.selected_index() is not None)
        set_enabled(self.bouton_enregistrer, a_des_images and self._annulation is None)

    # ------------------------------------------------------------------
    # Sélection et navigation
    # ------------------------------------------------------------------

    def selected_index(self):
        selection = self.liste_fichiers.curselection()
        return selection[0] if selection else None

    def selected_path(self):
        index = self.selected_index()
        return self.chemins_images[index] if index is not None else None

    def select_index(self, index: int):
        self.liste_fichiers.selection_clear(0, tk.END)
        self.liste_fichiers.selection_set(index)
        self.liste_fichiers.activate(index)
        self.liste_fichiers.see(index)
        self.on_list_selection()

    def navigate(self, delta: int):
        """Passe à l'image précédente (-1) ou suivante (+1) dans la liste."""
        if not self.chemins_images:
            return
        index = self.selected_index() or 0
        nouvel_index = max(0, min(len(self.chemins_images) - 1, index + delta))
        if nouvel_index != index or self.selected_index() is None:
            self.select_index(nouvel_index)

    def on_list_selection(self, _event=None):
        """Les réglages affichés reflètent toujours l'image sélectionnée."""
        chemin = self.selected_path()
        self.update_buttons()
        if chemin is None:
            return
        reglages = self.reglages_par_image[chemin]
        self.format_var.set(reglages.format_sortie)
        self.niveau_var.set(reglages.niveau)
        self.largeur_max_var.set(str(reglages.largeur_max) if reglages.largeur_max else "")
        self.hauteur_max_var.set(str(reglages.hauteur_max) if reglages.hauteur_max else "")
        self.update_format_controls()
        self.update_preview()

    # ------------------------------------------------------------------
    # Réglages
    # ------------------------------------------------------------------

    def _apply_setting(self, appliquer_a_toutes: bool, **valeurs):
        """Applique un réglage à l'image sélectionnée, ou à toutes les images."""
        chemins = self.chemins_images if appliquer_a_toutes else [self.selected_path()]
        for chemin in chemins:
            if chemin is not None:
                for nom, valeur in valeurs.items():
                    setattr(self.reglages_par_image[chemin], nom, valeur)

    def on_format_change(self):
        self._apply_setting(self.appliquer_format_toutes_var.get(), format_sortie=self.format_var.get())
        self.refresh_file_list()
        self.update_format_controls()
        self.update_preview()

    def on_toggle_apply_format_to_all(self):
        if self.appliquer_format_toutes_var.get() and self.chemins_images:
            self._apply_setting(True, format_sortie=self.format_var.get())
            self.refresh_file_list()
            self.update_preview()

    def update_format_controls(self):
        """Description du format choisi ; le slider n'est actif que pour les
        formats avec perte (pour les autres, la qualité ne se règle pas)."""
        description = FORMATS[self.format_var.get()]
        self.label_description_format.config(text=description.description)

        set_enabled(self.slider_niveau, description.avec_perte)
        if description.avec_perte:
            self.label_info_compression.config(
                text="À droite : fichier plus léger, mais qualité plus basse."
            )
        else:
            self.label_info_compression.config(
                text=f"{description.libelle} est un format sans perte : la qualité n'est pas réglable."
            )
        self.update_level_label()

    def update_level_label(self):
        if FORMATS[self.format_var.get()].avec_perte:
            self.label_niveau.config(text=f"{self.niveau_var.get()} %")
        else:
            self.label_niveau.config(text="—")

    def on_slider_release(self, _event=None):
        """Mémorise le niveau pour l'image sélectionnée (ou pour toutes si la
        case est cochée), puis recalcule l'aperçu."""
        if not FORMATS[self.format_var.get()].avec_perte or not self.chemins_images:
            return
        self._apply_setting(self.appliquer_compression_toutes_var.get(), niveau=self.niveau_var.get())
        self.refresh_file_list()
        self.update_preview()

    def on_toggle_apply_compression_to_all(self):
        if self.appliquer_compression_toutes_var.get() and self.chemins_images:
            self._apply_setting(True, niveau=self.niveau_var.get())
            self.refresh_file_list()
            self.update_preview()

    def get_max_dimensions(self):
        """Lit les champs largeur/hauteur max : entier positif, ou None si vide/invalide."""

        def lire(texte: str):
            try:
                nombre = int(texte.strip())
            except ValueError:
                return None
            return nombre if nombre > 0 else None

        return lire(self.largeur_max_var.get()), lire(self.hauteur_max_var.get())

    def on_dimension_input(self, source: str):
        """Appelée à chaque frappe dans les champs de taille. Si "Lier" est
        coché, recopie la valeur dans l'autre champ ; l'aperçu est recalculé
        après un court délai d'inactivité."""
        if self.lier_dimensions_var.get():
            if source == "largeur":
                self.hauteur_max_var.set(self.largeur_max_var.get())
            else:
                self.largeur_max_var.set(self.hauteur_max_var.get())
        largeur, hauteur = self.get_max_dimensions()
        self._apply_setting(self.appliquer_taille_toutes_var.get(), largeur_max=largeur, hauteur_max=hauteur)
        self.schedule_preview_update()

    def on_toggle_link_dimensions(self):
        """Quand on coche la case, aligne tout de suite la hauteur sur la largeur."""
        if self.lier_dimensions_var.get():
            self.on_dimension_input("largeur")

    def on_toggle_apply_resize_to_all(self):
        if self.appliquer_taille_toutes_var.get() and self.chemins_images:
            largeur, hauteur = self.get_max_dimensions()
            self._apply_setting(True, largeur_max=largeur, hauteur_max=hauteur)
            self.schedule_preview_update()

    # ------------------------------------------------------------------
    # Aperçu
    # ------------------------------------------------------------------

    def schedule_preview_update(self):
        """Reporte l'actualisation de l'aperçu, en annulant toute actualisation
        déjà programmée : évite de reconvertir à chaque frappe."""
        if self._tache_apercu_programmee is not None:
            self.root.after_cancel(self._tache_apercu_programmee)
        self._tache_apercu_programmee = self.root.after(DELAI_SAISIE_MS, self.update_preview)

    def update_preview(self):
        """Demande au thread d'aperçu de convertir l'image sélectionnée, en
        mémoire uniquement : aucun fichier n'est créé avant "Enregistrer"."""
        self._tache_apercu_programmee = None
        chemin = self.selected_path()
        if chemin is None:
            return

        index = self.selected_index()
        self.label_position.config(text=f"Image {index + 1} / {len(self.chemins_images)}")

        if chemin != self._chemin_affiche:
            # Nouvelle image : on vide les deux colonnes pendant le chargement
            self._chemin_affiche = chemin
            self._donnees_apres_affichees = None
            for colonne in (self.apercu_avant, self.apercu_apres):
                self._show_placeholder(colonne, "chargement...")
        else:
            # Même image, autres réglages : on garde l'ancienne miniature affichée
            self.apercu_apres["poids"].config(text="calcul en cours...", foreground=MUTED)

        self._jeton_apercu += 1
        reglages = replace(self.reglages_par_image[chemin], supprimer_exif=self.supprimer_exif_var.get())
        self._apercus.request(self._jeton_apercu, chemin, reglages)

    def _show_placeholder(self, colonne: dict, texte: str):
        colonne["miniature"].config(image="", text=texte, cursor="")
        colonne["poids"].config(text="")
        colonne["dimensions"].config(text="")
        colonne["info"].config(text="")

    def show_preview(self, apercu: Apercu):
        if apercu.jeton != self._jeton_apercu:
            return  # réglages ou image changés entre-temps : résultat obsolète

        if apercu.miniature_avant is None:
            self._show_placeholder(self.apercu_avant, "aperçu\nindisponible")
            self._show_placeholder(self.apercu_apres, "aperçu\nindisponible")
            self.apercu_avant["info"].config(text=apercu.erreur or "", foreground=ERROR)
            return

        self._miniature_avant_tk = ImageTk.PhotoImage(apercu.miniature_avant)
        self.apercu_avant["miniature"].config(image=self._miniature_avant_tk, text="", cursor="hand2")
        self.apercu_avant["poids"].config(
            text=f"{format_size(apercu.taille_avant)} · {apercu.format_avant}", foreground=TEXT
        )
        self.apercu_avant["dimensions"].config(text="{} x {} px".format(*apercu.dimensions_avant))

        if apercu.miniature_apres is None:
            self._miniature_apres_tk = None
            self._donnees_apres_affichees = None
            self._show_placeholder(self.apercu_apres, "aperçu\nindisponible")
            self.apercu_apres["info"].config(text=apercu.erreur or "", foreground=ERROR)
            return

        self._miniature_apres_tk = ImageTk.PhotoImage(apercu.miniature_apres)
        self._donnees_apres_affichees = apercu.donnees_apres
        self.apercu_apres["miniature"].config(image=self._miniature_apres_tk, text="", cursor="hand2")

        taille_apres = len(apercu.donnees_apres)
        if apercu.original_conserve:
            # La conversion aurait produit un fichier plus lourd : l'original est conservé tel quel
            self.apercu_apres["poids"].config(
                text=f"{format_size(taille_apres)} (original conservé)", foreground=ACCENT
            )
        else:
            variation = (taille_apres / apercu.taille_avant - 1) * 100 if apercu.taille_avant else 0
            self.apercu_apres["poids"].config(
                text=f"{format_size(taille_apres)} · {apercu.format_apres} ({variation:+.0f} %)",
                foreground=TEXT,
            )
        # Dimensions réellement obtenues (redimensionnée ou non)
        self.apercu_apres["dimensions"].config(text="{} x {} px".format(*apercu.dimensions_apres))
        self.apercu_apres["info"].config(text=", ".join(apercu.infos), foreground=ACCENT)

    def open_fullsize_preview(self, cote: str):
        """Ouvre l'image Avant ou Après en grand (85 % de l'écran au maximum)."""
        chemin = self._chemin_affiche
        if not chemin:
            return
        try:
            if cote == "avant":
                image = open_image(chemin).image
            elif self._donnees_apres_affichees:
                image = Image.open(io.BytesIO(self._donnees_apres_affichees))
            else:
                return
            taille_max = (
                int(self.root.winfo_screenwidth() * 0.85),
                int(self.root.winfo_screenheight() * 0.85),
            )
            image_tk = ImageTk.PhotoImage(make_thumbnail(image, taille_max))
        except Exception:
            return

        fenetre = tk.Toplevel(self.root)
        fenetre.title(f"{os.path.basename(chemin)} — {'avant' if cote == 'avant' else 'après'}")
        fenetre.configure(bg=BG)
        fenetre.transient(self.root)
        label = tk.Label(fenetre, image=image_tk, bg=BG, cursor="hand2")
        label.image = image_tk  # garde une référence pour éviter le garbage collector
        label.pack()
        # Clic ou Échap pour refermer
        label.bind("<Button-1>", lambda _e: fenetre.destroy())
        fenetre.bind("<Escape>", lambda _e: fenetre.destroy())
        fenetre.focus_set()

    # ------------------------------------------------------------------
    # Enregistrement
    # ------------------------------------------------------------------

    def save_images(self):
        if not self.chemins_images or self._annulation is not None:
            return
        dossier = filedialog.askdirectory(
            title="Choisir le dossier de destination", initialdir=self._dernier_dossier
        )
        if not dossier:
            return
        self._dernier_dossier = dossier

        # Copie figée des réglages : on peut continuer à modifier la liste
        # pendant l'enregistrement sans perturber le thread.
        supprimer_exif = self.supprimer_exif_var.get()
        taches = [
            (chemin, replace(self.reglages_par_image[chemin], supprimer_exif=supprimer_exif))
            for chemin in self.chemins_images
        ]

        self._annulation = threading.Event()
        self.hide_result()
        self.progress.set_progress(0, len(taches))
        set_enabled(self.bouton_annuler, True)
        self.cadre_progression.pack(fill="x", pady=(0, 12), before=self.label_resultat)
        self.update_buttons()
        threading.Thread(
            target=save_all,
            args=(taches, dossier, self._annulation, self._file_messages),
            daemon=True,
        ).start()

    def cancel_save(self):
        if self._annulation is None:
            return
        self._annulation.set()
        set_enabled(self.bouton_annuler, False)
        self.label_resultat.config(text="Annulation après l'image en cours...", foreground=MUTED)

    def on_save_finished(self, rapport: Rapport):
        self._annulation = None
        self.cadre_progression.pack_forget()
        self.update_buttons()

        lignes = []
        if rapport.annule:
            lignes.append(
                f"Enregistrement annulé : {rapport.reussites}/{rapport.total} image(s) enregistrée(s)"
            )
        elif rapport.reussites:
            lignes.append(f"{rapport.reussites}/{rapport.total} image(s) convertie(s) avec succès")
        else:
            lignes.append("Aucune image n'a pu être convertie.")

        if rapport.reussites:
            avant, apres = rapport.octets_avant, rapport.octets_apres
            lignes.append(f"Avant : {format_size(avant)}  →  Après : {format_size(apres)}")
            if avant and apres <= avant:
                lignes.append(f"Gain de place : {(1 - apres / avant) * 100:.1f} %")
            elif avant:
                lignes.append(f"Poids augmenté de {(apres / avant - 1) * 100:.1f} %")
            nb = rapport.originaux_conserves
            if nb:
                debut = "1 image gardée telle quelle" if nb == 1 else f"{nb} images gardées telles quelles"
                lignes.append(f"{debut} (la conversion aurait été plus lourde)")

        if rapport.echecs:
            lignes.append("\nÉchecs :\n" + "\n".join(rapport.echecs))
            couleur = ERROR
        elif rapport.annule:
            couleur = ACCENT
        else:
            couleur = SUCCESS
        self.label_resultat.config(text="\n".join(lignes), foreground=couleur)

        if rapport.reussites:
            self.bouton_ouvrir_dossier.pack(anchor="w", pady=(8, 0), before=self.label_raccourcis)
            self._dossier_resultat = rapport.dossier

        if rapport.echecs:
            nb = len(rapport.echecs)
            debut = "1 image n'a pas pu être convertie" if nb == 1 else f"{nb} images n'ont pas pu être converties"
            messagebox.showwarning(
                "Conversion terminée avec des erreurs", f"{debut}. Voir le détail dans la fenêtre."
            )

    def hide_result(self):
        self.label_resultat.config(text="")
        self.bouton_ouvrir_dossier.pack_forget()

    def open_destination_folder(self):
        if self._dossier_resultat and os.path.isdir(self._dossier_resultat):
            os.startfile(self._dossier_resultat)

    # ------------------------------------------------------------------
    # Boucle de messages, molette, fermeture
    # ------------------------------------------------------------------

    def process_messages(self):
        """Lit les messages des threads de fond (seul endroit où leurs résultats
        touchent l'interface), puis se reprogramme."""
        try:
            while True:
                type_message, contenu = self._file_messages.get_nowait()
                if type_message == "apercu":
                    self.show_preview(contenu)
                elif type_message == "progression":
                    self.progress.set_progress(*contenu)
                elif type_message == "fin":
                    self.on_save_finished(contenu)
        except queue.Empty:
            pass
        self.root.after(INTERVALLE_MESSAGES_MS, self.process_messages)

    def on_mousewheel(self, event):
        """Molette : au-dessus de la liste de fichiers, seule la liste défile
        (la page reste immobile) ; ailleurs, c'est toute la page qui défile."""
        sous_la_souris = self.root.winfo_containing(event.x_root, event.y_root)
        if sous_la_souris is None or sous_la_souris.winfo_toplevel() is not self.root:
            return None
        crans = int(-event.delta / 120)
        if sous_la_souris is self.liste_fichiers:
            if event.widget is not self.liste_fichiers:
                self.liste_fichiers.yview_scroll(crans, "units")
            return None
        # Ne défile que si le contenu dépasse la fenêtre
        if self.canvas.yview() != (0.0, 1.0):
            self.canvas.yview_scroll(crans * 3, "units")
        return None

    def on_close(self):
        if self._annulation is not None and not messagebox.askyesno(
            "Enregistrement en cours",
            "Des images sont en cours d'enregistrement. Quitter quand même ?",
        ):
            return
        if self._tache_apercu_programmee is not None:
            self.root.after_cancel(self._tache_apercu_programmee)
        self.root.destroy()


def main():
    root = TkinterDnD.Tk() if GLISSER_DEPOSER_DISPONIBLE else tk.Tk()
    app = ConvertisseurApp(root)
    # Fichiers passés en argument (ex. "Ouvrir avec" ou glissés sur l'exécutable)
    if len(sys.argv) > 1:
        root.after(100, lambda: app.add_files(sys.argv[1:]))
    root.mainloop()


if __name__ == "__main__":
    main()
