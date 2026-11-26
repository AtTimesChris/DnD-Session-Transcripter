"""v2.46.1: one set of colours, so a colour means the same thing in every window.
v2.47: light / dark / Windows theme, a choice of speaker colour schemes and a symbol beside each speaker.

Each tone is set on that one widget only: there is no stylesheet for the whole program, so the normal Windows look
of buttons, lists and tables is not touched and nothing gets slower. The tone colours read on light and dark themes.

Speakers are never told apart by colour alone: with "symbols" on (the default) every speaker also has its own
symbol (● ■ ▲ ◆ …) in front of its name, in the same place in every list. The schemes below are published palettes
designed to stay distinct for common kinds of colour blindness; which one works best differs from person to person,
so Colours… shows them side by side to pick from by eye.
"""

DIM = "#8b949e"        # side notes and explanations
INFO = "#58a6ff"       # neutral information
WARN = "#d29922"       # check this / something may be wrong
OK = "#238636"         # all good
NOTICE_BG = "#5a4a00"  # notice bar (needs your attention)
NOTICE_FG = "#fff3c4"

_TONES = {
    "dim": f"color:{DIM};",
    "info": f"color:{INFO};",
    "warn": f"color:{WARN};",
    "ok": f"color:{OK}; font-weight:700;",
    "notice": f"QLabel{{background:{NOTICE_BG};color:{NOTICE_FG};padding:6px;border-radius:4px;font-weight:600;}}",
}


def tone(widget, name):
    """Colour a label (or any widget) as one of: dim, info, warn, ok, notice. Returns the widget."""
    widget.setStyleSheet(_TONES[name])
    return widget


def dim_span(text):
    """Grey side text inside rich-text labels (times, dashes)."""
    return f"<span style='color:{DIM}'>{text}</span>"


# ---- speaker colours (v2.47) ----
# key: (name shown in Colours…, description, colours in the order speakers get them)
SPEAKER_SCHEMES = {
    "okabe_ito": ("Colour-blind safe (Okabe-Ito)",
                  "The scheme used so far. Eight colours chosen to stay apart for red-green and blue-yellow colour blindness.",
                  ["#56B4E9", "#E69F00", "#009E73", "#CC79A7", "#D55E00", "#0072B2", "#F0E442", "#666666"]),
    "blue_yellow": ("Blue / yellow and light / dark (no reds, greens or browns)",
                    "For red-green colour blindness: speakers differ in blue against yellow and in how light or dark "
                    "they are, which those eyes still tell apart. No orange, brown, red or green.",
                    ["#004488", "#DDAA33", "#88CCEE", "#222222", "#EEDD88", "#AA4499", "#F5F5F5", "#777777"]),
    "tol_bright": ("Paul Tol bright",
                   "Another published colour-blind-safe set, with stronger, more saturated colours.",
                   ["#4477AA", "#EE6677", "#228833", "#CCBB44", "#66CCEE", "#AA3377", "#BBBBBB", "#000000"]),
}
SPEAKER_SYMBOLS = ["●", "■", "▲", "◆", "★", "✚", "▼", "⬟", "◐", "✱", "⬢", "◇"]
UNASSIGNED_COLOUR = "#D9D9D9"

_state = {"scheme": "okabe_ito", "symbols": True}


def configure(config):
    """Read the colour settings from config (at start-up and after Colours… is applied)."""
    scheme = config.get("speaker_colours", "okabe_ito")
    _state["scheme"] = scheme if scheme in SPEAKER_SCHEMES else "okabe_ito"
    _state["symbols"] = bool(config.get("speaker_symbols", True))


def speaker_palette(scheme=None):
    return SPEAKER_SCHEMES[scheme or _state["scheme"]][2]


def speaker_colour(sid, ids, scheme=None):
    palette = speaker_palette(scheme)
    return palette[ids.index(sid) % len(palette)] if sid in ids else UNASSIGNED_COLOUR


def speaker_symbol(sid, ids):
    """The speaker's symbol ('' when symbols are off or the speaker is not in the list)."""
    if not _state["symbols"] or sid not in ids: return ""
    return SPEAKER_SYMBOLS[ids.index(sid) % len(SPEAKER_SYMBOLS)]


def contrast_text(hex_colour):
    value = hex_colour.lstrip('#')
    r, g, b = (int(value[i:i+2], 16) for i in (0, 2, 4))
    return "#111111" if (0.2126*r + 0.7152*g + 0.0722*b) > 145 else "#FFFFFF"


# ---- light / dark (v2.47) ----
APP_THEMES = {"system": "Same as Windows", "light": "Light", "dark": "Dark"}


def apply_app_theme(app, name):
    """Light, dark or follow Windows. Qt redraws its own colours; nothing else changes."""
    from PySide6.QtCore import Qt
    scheme = {"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}.get(name, Qt.ColorScheme.Unknown)
    try:
        app.styleHints().setColorScheme(scheme)
    except Exception:
        pass   # an older Qt without the setting: the Windows theme is used
