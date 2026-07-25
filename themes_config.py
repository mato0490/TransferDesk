"""Configuration centralisee des themes de l'application UltraPro.

Ce module ne depend d'aucune bibliotheque externe. Les palettes sont utilisees
par ``legacy_file_manager.py`` et les constantes historiques restent disponibles pour
les anciennes versions de l'application.
"""

THEMES_SOMBRES = [
    "liquid_glass",
    "superhero",
    "darkly",
    "cyborg",
    "solar",
    "vapor",
]

THEMES_CLAIRS = [
    "flatly",
    "cosmo",
    "litera",
    "journal",
    "yeti",
    "minty",
]

TOUS_LES_THEMES = THEMES_SOMBRES + THEMES_CLAIRS
THEME_PAR_DEFAUT = "liquid_glass"


# Chaque palette expose les memes roles afin que l'interface puisse changer de
# theme instantanement, y compris pour les widgets Tkinter classiques.
PALETTES = {
    "liquid_glass": {
        "bg": "#070b16", "surface": "#121a2a", "surface_alt": "#0c1322",
        "fg": "#f5f7ff", "muted": "#9eacc2", "primary": "#78a9ff",
        "success": "#38d9a9", "danger": "#ff668a", "warning": "#ffbf69",
        "entry_bg": "#182337", "entry_fg": "#f7f9ff", "border": "#344766",
        "log_bg": "#080d18", "log_fg": "#a9c7ff",
    },
    "superhero": {
        "bg": "#2b3e50", "surface": "#32465a", "surface_alt": "#253748",
        "fg": "#ffffff", "muted": "#c4ced7", "primary": "#4c9be8",
        "success": "#5cb85c", "danger": "#d9534f", "warning": "#f0ad4e",
        "entry_bg": "#f5f7fa", "entry_fg": "#1d2731", "border": "#60758a",
        "log_bg": "#15202b", "log_fg": "#72e06a",
    },
    "darkly": {
        "bg": "#222222", "surface": "#2f2f2f", "surface_alt": "#191919",
        "fg": "#f1f1f1", "muted": "#b8b8b8", "primary": "#375a7f",
        "success": "#00bc8c", "danger": "#e74c3c", "warning": "#f39c12",
        "entry_bg": "#f4f4f4", "entry_fg": "#202020", "border": "#555555",
        "log_bg": "#101010", "log_fg": "#5fe6b7",
    },
    "cyborg": {
        "bg": "#060606", "surface": "#151515", "surface_alt": "#000000",
        "fg": "#eeeeee", "muted": "#aaaaaa", "primary": "#2a9fd6",
        "success": "#77b300", "danger": "#cc0000", "warning": "#ff8800",
        "entry_bg": "#282828", "entry_fg": "#f2f2f2", "border": "#555555",
        "log_bg": "#000000", "log_fg": "#69ff69",
    },
    "solar": {
        "bg": "#002b36", "surface": "#073642", "surface_alt": "#00252e",
        "fg": "#eee8d5", "muted": "#93a1a1", "primary": "#268bd2",
        "success": "#859900", "danger": "#dc322f", "warning": "#b58900",
        "entry_bg": "#fdf6e3", "entry_fg": "#073642", "border": "#586e75",
        "log_bg": "#001f27", "log_fg": "#b4cf60",
    },
    "vapor": {
        "bg": "#190831", "surface": "#2a1247", "surface_alt": "#11051f",
        "fg": "#f4e8ff", "muted": "#c7a7dd", "primary": "#9b5de5",
        "success": "#00d6b2", "danger": "#ff4d8d", "warning": "#ffb84d",
        "entry_bg": "#f9f2ff", "entry_fg": "#2a1247", "border": "#744c96",
        "log_bg": "#0d021b", "log_fg": "#33f0d0",
    },
    "flatly": {
        "bg": "#ecf0f1", "surface": "#ffffff", "surface_alt": "#dfe6e9",
        "fg": "#2c3e50", "muted": "#6c7a89", "primary": "#2c3e50",
        "success": "#18bc9c", "danger": "#e74c3c", "warning": "#f39c12",
        "entry_bg": "#ffffff", "entry_fg": "#263238", "border": "#b9c3ca",
        "log_bg": "#1f2d36", "log_fg": "#66e0b8",
    },
    "cosmo": {
        "bg": "#f2f5f8", "surface": "#ffffff", "surface_alt": "#e6ebf0",
        "fg": "#373a3c", "muted": "#6b747c", "primary": "#2780e3",
        "success": "#3fb618", "danger": "#ff0039", "warning": "#ff7518",
        "entry_bg": "#ffffff", "entry_fg": "#2d3338", "border": "#c8d0d8",
        "log_bg": "#17212b", "log_fg": "#71e29a",
    },
    "litera": {
        "bg": "#f7f7f7", "surface": "#ffffff", "surface_alt": "#ececec",
        "fg": "#343a40", "muted": "#6c757d", "primary": "#4582ec",
        "success": "#02b875", "danger": "#d9534f", "warning": "#f0ad4e",
        "entry_bg": "#ffffff", "entry_fg": "#333333", "border": "#d2d2d2",
        "log_bg": "#202124", "log_fg": "#80e3a2",
    },
    "journal": {
        "bg": "#f4f0e6", "surface": "#fffdf7", "surface_alt": "#e8e0cf",
        "fg": "#222222", "muted": "#6f685c", "primary": "#8b1a1a",
        "success": "#2f7d32", "danger": "#c62828", "warning": "#b26a00",
        "entry_bg": "#fffefa", "entry_fg": "#25221e", "border": "#b8ad98",
        "log_bg": "#24211d", "log_fg": "#b8e986",
    },
    "yeti": {
        "bg": "#eef4f7", "surface": "#ffffff", "surface_alt": "#dce9ef",
        "fg": "#212529", "muted": "#697981", "primary": "#008cba",
        "success": "#43ac6a", "danger": "#f04124", "warning": "#e99002",
        "entry_bg": "#ffffff", "entry_fg": "#27323a", "border": "#bdd0d9",
        "log_bg": "#16313d", "log_fg": "#78e0a3",
    },
    "minty": {
        "bg": "#eef8f5", "surface": "#ffffff", "surface_alt": "#dcefe9",
        "fg": "#3d4d4a", "muted": "#71847f", "primary": "#78c2ad",
        "success": "#56cc9d", "danger": "#ff7851", "warning": "#ffce67",
        "entry_bg": "#ffffff", "entry_fg": "#344743", "border": "#b9d8cf",
        "log_bg": "#17342c", "log_fg": "#8ce8c3",
    },
}


def get_palette(nom_theme):
    """Retourne une copie de la palette demandee ou celle par defaut."""
    return PALETTES.get(nom_theme, PALETTES[THEME_PAR_DEFAUT]).copy()


def changer_theme(app_style, nouveau_theme):
    """Compatibilite avec une instance ttkbootstrap.Style existante."""
    if nouveau_theme not in TOUS_LES_THEMES:
        nouveau_theme = THEME_PAR_DEFAUT
    app_style.theme_use(nouveau_theme)
