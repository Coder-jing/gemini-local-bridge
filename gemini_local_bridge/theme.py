"""Modern (2026-era) theming for the desktop console.

Uses `sv_ttk` (Sun Valley) as the base for an authentic Windows 11 dark/light
look, then layers on:
  * a custom accent palette (teal/green brand colour) via ttk style maps,
  * card-style ``TLabelframe`` with soft borders and generous padding,
  * modern typography (Segoe UI Variable / Segoe UI on Windows),
  * a flat "header" style used for the top status bar.

Public API:
  * ``apply_theme(root, dark=True)`` — install the theme + custom styles.
  * ``toggle_theme(root)`` — switch dark/light at runtime.
  * ``COLORS`` — the accent palette used elsewhere in the UI.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# Brand accent palette (teal/green, matches the tray icon).
COLORS = {
    "accent": "#10a37f",
    "accent_hover": "#0d8a6b",
    "accent_active": "#0b7459",
    "ok": "#3fb950",
    "warn": "#d29922",
    "danger": "#f85149",
    "fg": "#e6edf3",
    "fg_dim": "#8b949e",
    "card_bg": "#161b22",
    "border": "#30363d",
}


def apply_theme(root: tk.Tk, dark: bool = True) -> None:
    """Install sv_ttk + custom accent/card styles on *root*."""
    try:
        import sv_ttk

        sv_ttk.set_theme("dark" if dark else "light")
    except Exception:  # noqa: BLE001 — fall back to a clean native theme
        style = ttk.Style(root)
        try:
            style.theme_use("vista" if root.tk.call("tk", "windowingsystem") == "win32" else "clam")
        except tk.TclError:
            pass

    style = ttk.Style(root)
    _apply_typography(style)
    _apply_accent_colors(style)
    _apply_cards(style)
    _apply_treeview(style, dark)
    _apply_classic_widgets(root, dark)


def style_classic(widget: tk.Widget, dark: bool = True) -> None:
    """Apply dark colors to a classic (non-ttk) widget like Listbox or Text.

    Tolerant of widgets that don't support every option (e.g. Listbox has no
    ``insertbackground``) — each option is applied individually and skipped if
    the widget rejects it.
    """
    bg = COLORS["card_bg"] if dark else "#ffffff"
    fg = COLORS["fg"] if dark else "#1a1a1a"
    insert = COLORS["fg"] if dark else "#000000"
    select_bg = COLORS["accent"]
    select_fg = "#ffffff"
    for key, value in (
        ("bg", bg),
        ("fg", fg),
        ("insertbackground", insert),
        ("selectbackground", select_bg),
        ("selectforeground", select_fg),
        ("relief", "flat"),
        ("highlightthickness", 1),
        ("highlightbackground", COLORS["border"]),
        ("highlightcolor", COLORS["accent"]),
    ):
        try:
            widget.configure({key: value})
        except tk.TclError:
            continue


def _font_family() -> tuple[str, int]:
    """Pick a modern UI font available on the platform."""
    import tkinter.font as tkfont

    root = tk._default_root  # noqa: SLF001 — the app root is the default root
    families = set(tkfont.families(root))
    for candidate in ("Segoe UI Variable Display", "Segoe UI Variable", "Segoe UI", "Inter", "Helvetica Neue"):
        if candidate in families:
            return candidate, 10
    return "TkDefaultFont", 10


def _apply_typography(style: ttk.Style) -> None:
    family, size = _font_family()
    normal = (family, size)
    bold = (family, size, "bold")
    small = (family, size - 1)

    style.configure(".", font=normal)
    style.configure("TLabel", font=normal)
    style.configure("Title.TLabel", font=bold, foreground=COLORS["fg"])
    style.configure("Dim.TLabel", font=small, foreground=COLORS["fg_dim"])
    style.configure("Header.TLabel", font=(family, 15, "bold"), foreground=COLORS["fg"])
    style.configure("Status.TLabel", font=bold)
    style.configure("TLabelframe.Label", font=bold, foreground=COLORS["fg"])
    style.configure("TButton", font=normal)
    style.configure("TCheckbutton", font=normal)
    style.configure("TRadiobutton", font=normal)
    style.configure("TNotebook.Tab", font=normal, padding=(14, 7))


def _force_plain_button_layout(style: ttk.Style, name: str) -> None:
    """Replace an sv_ttk button layout (which uses images and ignores bg) with a
    plain layout whose background colour actually renders."""
    style.layout(
        name,
        [
            (
                "Button.border",
                {
                    "children": [
                        (
                            "Button.focus",
                            {
                                "children": [
                                    (
                                        "Button.padding",
                                        {
                                            "children": [
                                                ("Button.label", {"sticky": "nswe"})
                                            ],
                                            "sticky": "nswe",
                                        },
                                    )
                                ],
                                "sticky": "nswe",
                            },
                        )
                    ],
                    "sticky": "nswe",
                },
            )
        ],
    )


def _apply_accent_colors(style: ttk.Style) -> None:
    """Make the primary button use the brand accent colour (flat, rounded look).

    Uses a custom ``Brand.TButton`` name so sv_ttk's own ``Accent.TButton``
    doesn't override our teal with the Windows default blue. sv_ttk buttons are
    drawn with image elements that ignore ``-background``, so we reset each
    button's layout to the plain (non-image) layout so the colour shows.
    """
    _force_plain_button_layout(style, "Brand.TButton")
    _force_plain_button_layout(style, "Danger.TButton")

    accent = COLORS["accent"]
    style.configure(
        "Brand.TButton",
        foreground="#ffffff",
        background=accent,
        borderwidth=0,
        focuscolor=accent,
        padding=(18, 9),
    )
    style.map(
        "Brand.TButton",
        background=[
            ("disabled", COLORS["border"]),
            ("pressed", COLORS["accent_active"]),
            ("active", COLORS["accent_hover"]),
        ],
        foreground=[("disabled", COLORS["fg_dim"])],
    )

    # Secondary/danger variants for stop / destructive actions.
    style.configure(
        "Danger.TButton",
        foreground="#ffffff",
        background=COLORS["danger"],
        borderwidth=0,
        padding=(16, 8),
    )
    style.map(
        "Danger.TButton",
        background=[("pressed", "#c93c36"), ("active", "#e0483f")],
    )

    # Colour the status label via tags set at call sites.
    style.configure("Ok.TLabel", foreground=COLORS["ok"], font=("Segoe UI", 10, "bold"))
    style.configure("Bad.TLabel", foreground=COLORS["danger"], font=("Segoe UI", 10, "bold"))


def _apply_cards(style: ttk.Style) -> None:
    """Card-style labelframes: subtle background + soft border, roomy padding."""
    style.configure(
        "Card.TLabelframe",
        background=COLORS["card_bg"],
        bordercolor=COLORS["border"],
        borderwidth=1,
        relief="solid",
    )
    style.configure(
        "Card.TLabelframe.Label",
        background=COLORS["card_bg"],
        foreground=COLORS["fg"],
        padding=(4, 2),
    )
    # Give framed content a bit of breathing room by default.
    style.configure("TFrame", padding=2)


def _apply_treeview(style: ttk.Style, dark: bool) -> None:
    bg = COLORS["card_bg"] if dark else "#ffffff"
    style.configure(
        "Treeview",
        background=bg,
        fieldbackground=bg,
        foreground=COLORS["fg"],
        bordercolor=COLORS["border"],
        rowheight=26,
        font=("Segoe UI", 10),
    )
    style.configure(
        "Treeview.Heading",
        background=COLORS["border"],
        foreground=COLORS["fg"],
        font=("Segoe UI", 10, "bold"),
        borderwidth=0,
    )
    style.map("Treeview", background=[("selected", COLORS["accent"])])
    style.map("TNotebook.Tab", background=[("selected", COLORS["accent"])])


def _apply_classic_widgets(root: tk.Tk, dark: bool) -> None:
    """Give the root window a solid themed background so nothing bleeds through."""
    bg = "#1e2229" if dark else "#f3f3f3"
    try:
        root.configure(background=bg)
    except tk.TclError:
        pass


def toggle_theme(root: tk.Tk) -> None:
    """Flip between dark and light."""
    try:
        import sv_ttk

        sv_ttk.toggle_theme()
    except Exception:  # noqa: BLE001
        pass