"""
Thème sombre de l'application (inspiré de Shutter Encoder) : palette, styles
ttk et barre de progression personnalisée.
"""

import tkinter as tk
from tkinter import ttk

# --- Palette du thème sombre ---
BG = "#1e1f22"              # fond principal de la fenêtre
PANEL_BG = "#26272b"        # fond des blocs ("cartes")
PANEL_BG_LIGHT = "#323338"  # champs, listbox, boutons secondaires
BORDER = "#3d3e44"          # bordures discrètes
TEXT = "#e8e8ea"            # texte principal
MUTED = "#9a9ba3"           # texte secondaire / placeholder
DISABLED = "#5c5d64"        # texte des éléments désactivés
ACCENT = "#5aa9ff"          # texte/état mis en avant (bleu clair)
ACCENT_BTN = "#2f6fed"      # bouton d'action principal
ACCENT_BTN_HOVER = "#4a82f2"
ERROR = "#e5534b"
SUCCESS = "#57c785"

FONT = ("Segoe UI", 9)
FONT_BOLD = ("Segoe UI", 9, "bold")
FONT_SECTION = ("Segoe UI", 10, "bold")
FONT_ACCENT_BTN = ("Segoe UI", 10, "bold")


def configure_dark_style(root: tk.Tk) -> ttk.Style:
    """Configure un thème sombre pour tous les widgets ttk de l'application."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(bg=BG)

    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=PANEL_BG)

    style.configure("TLabel", background=BG, foreground=TEXT, font=FONT)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=FONT)
    style.configure("Card.TLabel", background=PANEL_BG, foreground=TEXT, font=FONT)
    style.configure("CardMuted.TLabel", background=PANEL_BG, foreground=MUTED, font=FONT)
    style.configure("CardSection.TLabel", background=PANEL_BG, foreground=TEXT, font=FONT_SECTION)
    style.configure("CardAccent.TLabel", background=PANEL_BG, foreground=ACCENT, font=FONT_BOLD)

    style.configure(
        "TButton",
        background=PANEL_BG_LIGHT,
        foreground=TEXT,
        borderwidth=0,
        focusthickness=0,
        focuscolor=PANEL_BG_LIGHT,
        padding=8,
        font=FONT,
    )
    style.map(
        "TButton",
        background=[("disabled", PANEL_BG), ("active", BORDER)],
        foreground=[("disabled", DISABLED)],
    )

    style.configure(
        "Accent.TButton",
        background=ACCENT_BTN,
        foreground="#ffffff",
        borderwidth=0,
        focusthickness=0,
        focuscolor=ACCENT_BTN,
        padding=10,
        font=FONT_ACCENT_BTN,
    )
    style.map(
        "Accent.TButton",
        background=[("disabled", PANEL_BG_LIGHT), ("active", ACCENT_BTN_HOVER)],
        foreground=[("disabled", MUTED)],
    )

    style.configure(
        "Small.TButton",
        background=PANEL_BG_LIGHT,
        foreground=TEXT,
        borderwidth=0,
        focusthickness=0,
        focuscolor=PANEL_BG_LIGHT,
        padding=(10, 4),
        font=FONT,
    )
    style.map(
        "Small.TButton",
        background=[("disabled", PANEL_BG), ("active", BORDER)],
        foreground=[("disabled", DISABLED)],
    )

    # Le thème "clam" dessine la case/le rond avec indicatorbackground (fond),
    # indicatorforeground (coche/point) et upper/lowerbordercolor (contour).
    for nom_style in ("Card.TCheckbutton", "Card.TRadiobutton"):
        style.configure(
            nom_style,
            background=PANEL_BG,
            foreground=TEXT,
            font=FONT,
            indicatorbackground=PANEL_BG_LIGHT,
            indicatorforeground="#ffffff",
            upperbordercolor=BORDER,
            lowerbordercolor=BORDER,
        )
        style.map(
            nom_style,
            background=[("active", PANEL_BG)],
            foreground=[("disabled", DISABLED)],
            indicatorbackground=[
                ("disabled", PANEL_BG),
                ("selected", ACCENT_BTN),
                ("active", BORDER),
            ],
            indicatorforeground=[("disabled", DISABLED)],
            upperbordercolor=[("selected", ACCENT_BTN), ("active", MUTED)],
            lowerbordercolor=[("selected", ACCENT_BTN), ("active", MUTED)],
        )

    style.configure(
        "TEntry",
        fieldbackground=PANEL_BG_LIGHT,
        foreground=TEXT,
        insertcolor=TEXT,
        borderwidth=1,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        padding=4,
    )
    style.map("TEntry", fieldbackground=[("disabled", PANEL_BG)])

    style.configure(
        "Horizontal.TScale",
        background=ACCENT_BTN,
        troughcolor=PANEL_BG_LIGHT,
        bordercolor=PANEL_BG,
        lightcolor=ACCENT_BTN,
        darkcolor=ACCENT_BTN,
    )
    style.map(
        "Horizontal.TScale",
        background=[("disabled", BORDER), ("active", ACCENT_BTN_HOVER)],
        lightcolor=[("disabled", BORDER)],
        darkcolor=[("disabled", BORDER)],
    )

    style.configure(
        "Vertical.TScrollbar",
        background=PANEL_BG_LIGHT,
        troughcolor=PANEL_BG,
        arrowcolor=TEXT,
        bordercolor=PANEL_BG,
        lightcolor=PANEL_BG_LIGHT,
        darkcolor=PANEL_BG_LIGHT,
        borderwidth=0,
    )
    style.map("Vertical.TScrollbar", background=[("active", BORDER)])

    return style


class ProgressBar(tk.Canvas):
    """Barre de progression personnalisée : coins arrondis, pourcentage centré,
    dans l'esprit de l'interface de Shutter Encoder (ttk.Progressbar ne permet
    pas nativement d'afficher du texte par-dessus la barre)."""

    def __init__(self, parent, height: int = 22, **kwargs):
        super().__init__(parent, height=height, bg=BG, highlightthickness=0, **kwargs)
        self._value = 0
        self._maximum = 100
        self._hauteur = height
        self._largeur = 1
        self.bind("<Configure>", self._on_resize)

    def _on_resize(self, event):
        self._largeur = event.width
        self._hauteur = event.height
        self._redraw()

    def set_progress(self, value: float = None, maximum: float = None):
        """Met à jour la valeur et/ou le maximum, puis redessine la barre."""
        if maximum is not None:
            self._maximum = max(1, maximum)
        if value is not None:
            self._value = value
        self._redraw()

    def _round_rect(self, x1, y1, x2, y2, radius, **kwargs):
        radius = max(0, min(radius, (y2 - y1) / 2, (x2 - x1) / 2 if x2 > x1 else 0))
        points = [
            x1 + radius, y1,
            x2 - radius, y1,
            x2, y1,
            x2, y1 + radius,
            x2, y2 - radius,
            x2, y2,
            x2 - radius, y2,
            x1 + radius, y2,
            x1, y2,
            x1, y2 - radius,
            x1, y1 + radius,
            x1, y1,
        ]
        return self.create_polygon(points, smooth=True, **kwargs)

    def _redraw(self):
        self.delete("all")
        w, h = self._largeur, self._hauteur
        if w <= 2 or h <= 2:
            return

        radius = h / 2
        self._round_rect(1, 1, w - 1, h - 1, radius, fill=PANEL_BG_LIGHT, outline="")

        ratio = 0 if self._maximum <= 0 else max(0, min(1, self._value / self._maximum))
        largeur_remplie = w * ratio
        if largeur_remplie >= 2:
            self._round_rect(
                1, 1, max(largeur_remplie, h - 1), h - 1, radius, fill=ACCENT_BTN, outline=""
            )

        pourcentage = int(round(ratio * 100))
        self.create_text(w / 2, h / 2, text=f"{pourcentage} %", fill=TEXT, font=FONT_BOLD)
