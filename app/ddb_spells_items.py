"""Spells, items and feats from D&D Beyond characters (v2.32). Qt-free and unit-tested.

Sources:
- the public character data of a character set to Public (the same service as ddb_import.py);
- a saved character file: the character data as JSON, or a D&D Beyond PDF export (form fields; best effort).

Nothing is added automatically: the names are cleaned up and offered in a tick list. Homebrew names are
common in this campaign ("COPY_OF_Misty Step - Known Spell - 👻🧂🌿 (5.5e)", "Lost: Star Compass",
"WAND OF WHIMSY"), so the clean-up removes copy prefixes, emoji, bracketed notes and "- Known Spell -"
style tails, and turns ALL CAPS into Title Case.
"""
import json
import re
import unicodedata
from pathlib import Path

SPELL, ITEM, FEAT, GEAR = "Spell", "Item", "Feat", "Ordinary gear"

# Feats and items that are bookkeeping rather than names anyone says at the table.
_SKIP = re.compile(r"(ability score (increase|improvement)|^boon\b|^lost next crit$|^banked crit$|attun(e)?ment slot)", re.I)
_SMALL = {"of", "the", "and", "a", "an", "in", "on", "to", "for", "from", "with", "at", "by", "or"}


def _strip_symbols(text):
    """Drop emoji, pictographs and joiners; keep letters, digits, spaces and ordinary punctuation."""
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat.startswith(("L", "N", "Z")) or ch in "'’-,.:&+/()!?":
            out.append(ch)
        else:
            out.append(" ")
    return "".join(out)


def _title(text):
    words = text.lower().split()
    return " ".join(w if (i and w in _SMALL) else w[:1].upper() + w[1:] for i, w in enumerate(words))


def clean_name(raw):
    """A name as people would say it, or "" if nothing usable is left."""
    text = str(raw or "")
    text = re.sub(r"^\s*COPY_OF_", "", text, flags=re.I)
    text = _strip_symbols(text).replace("’", "'")
    text = re.sub(r"\([^)]*\)", " ", text)                            # (10ft Bonus speed), (5.5e), (Dagger, +1)
    text = re.sub(r"^\s*lost\s*:\s*", "", text, flags=re.I)            # Lost: Star Compass
    text = re.sub(r"\s+-\s+.*$", "", text)                             # "Misty Step - Known Spell - …", "Hasted - …"
    text = re.sub(r"\s+", " ", text).strip(" -,.:")
    if not text or len(text) > 60 or not re.search(r"[A-Za-z]{2}", text):
        return ""
    if text.isupper() and len(text) > 3:
        text = _title(text)
    return text


def _definition_name(entry):
    if isinstance(entry, dict):
        definition = entry.get("definition") if isinstance(entry.get("definition"), dict) else {}
        return definition.get("name") or entry.get("name") or ""
    return ""


def parse_character(payload):
    """-> (character name, [{'name','category','raw','default'}]) from character-service JSON.

    'default' is whether the row starts ticked: spells, magic and custom items and feats yes; ordinary gear no."""
    data = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        raise ValueError("This file does not contain D&D Beyond character data.")
    if not any(k in data for k in ("spells", "classSpells", "inventory", "customItems", "feats")):
        raise ValueError("This file has no spells, items or feats (is it a D&D Beyond character data file?).")
    who = str(data.get("name") or "").strip()
    found = []

    own = {who.casefold(), who.split()[0].casefold()} if who else set()

    def add(raw, category, default=True):
        name = clean_name(raw)
        if not name or _SKIP.search(name) or name.casefold() in own:   # "Pip - Known Spell - …" trackers
            return
        tracker = bool(re.search(r"\s-\s", str(raw))) and _strip_symbols(str(raw)) != str(raw)   # "Hasted - 👻🧂🌿 (40ft speed)"
        if (tracker and category != SPELL) or len(name.split()) > 6:
            default = False
        found.append({"name": name, "category": category, "raw": str(raw), "default": default})

    spells = data.get("spells") if isinstance(data.get("spells"), dict) else {}
    for group in spells.values():
        for entry in group or []:
            add(_definition_name(entry), SPELL)
    for cls in data.get("classSpells") or []:
        for entry in (cls or {}).get("spells") or []:
            add(_definition_name(entry), SPELL)
    for entry in data.get("inventory") or []:
        definition = entry.get("definition") if isinstance(entry, dict) and isinstance(entry.get("definition"), dict) else {}
        raw = definition.get("name") or ""
        # homebrew "items" that are really spells ("Misty Step - Known Spell - …")
        category = SPELL if re.search(r"known spell", raw, re.I) else (ITEM if definition.get("magic") else GEAR)
        add(raw, category, default=category != GEAR)
    for entry in data.get("customItems") or []:
        add((entry or {}).get("name"), ITEM)
    for entry in data.get("feats") or []:
        add(_definition_name(entry), FEAT)
    return who, dedupe(found)


def dedupe(rows):
    """One row per name (case-insensitive); a spell or item wins over ordinary gear."""
    rank = {SPELL: 0, ITEM: 1, FEAT: 2, GEAR: 3}
    best = {}
    for row in rows:
        key = row["name"].casefold()
        if key not in best or rank.get(row["category"], 9) < rank.get(best[key]["category"], 9):
            best[key] = row
    return sorted(best.values(), key=lambda r: (rank.get(r["category"], 9), r["name"].casefold()))


def combine(rows):
    """Rows from several characters: one row per name, 'who' listing every character that has it."""
    merged = {}
    for row in rows:
        key = row["name"].casefold()
        if key in merged:
            have = merged[key]
            names = [w for w in have.get("who", "").split(", ") if w]
            if row.get("who") and row["who"] not in names:
                have["who"] = ", ".join(names + [row["who"]])
            have["default"] = have["default"] or row["default"]
        else:
            merged[key] = dict(row)
    return dedupe(list(merged.values()))


# ---------- saved files ----------------------------------------------------------------------

_PDF_SPELL_FIELD = re.compile(r"spell.?name|^spells?\s*\d|cantrip", re.I)
_PDF_ITEM_FIELD = re.compile(r"^eq\s*name|equipment|inventory|magic\s*item|attuned", re.I)


def parse_pdf_fields(fields):
    """Best effort for a D&D Beyond PDF export: {field name: value} from the PDF's form.
    Spell-name fields become spells; equipment fields become items (one name per line)."""
    who, found = "", []
    for key, value in fields.items():
        text = str(value or "").strip()
        if not text:
            continue
        if re.fullmatch(r"character\s*name\d*", key.strip(), re.I) and not who:
            who = text
        elif _PDF_SPELL_FIELD.search(key):
            for line in text.splitlines():
                name = clean_name(line)
                if name and not _SKIP.search(name):
                    found.append({"name": name, "category": SPELL, "raw": line, "default": True})
        elif _PDF_ITEM_FIELD.search(key):
            for line in re.split(r"[\n;]", text):
                name = clean_name(re.sub(r"^\s*\d+\s*[x×]?\s*", "", line))
                if name and not _SKIP.search(name):
                    found.append({"name": name, "category": ITEM, "raw": line, "default": False})
    return who, dedupe(found)


def load_character_file(path):
    """-> (character name, rows, how) from a saved JSON or PDF file."""
    p = Path(path)
    if p.suffix.lower() == ".json":
        return (*parse_character(json.loads(p.read_text(encoding="utf-8-sig"))), "character data file")
    if p.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(p))
        fields = {k: (v.get("/V") if isinstance(v, dict) else v) for k, v in (reader.get_fields() or {}).items()}
        fields = {k: v for k, v in fields.items() if isinstance(v, str)}
        if not fields:
            raise ValueError("This PDF has no filled form fields, so spells and items cannot be read from it. "
                             "Use Bulk import names to paste them instead.")
        who, rows = parse_pdf_fields(fields)
        if not rows:
            raise ValueError("No spell or equipment fields were recognised in this PDF. Use Bulk import names to paste them instead.")
        return who, rows, "PDF form fields (best effort)"
    raise ValueError("Choose a .json or .pdf file.")


def whisper_default(name):
    """Tick "Use in Whisper" only for names of two or more words: single-word spells and items are often
    ordinary words (Shield, Haste, Catapult), and hinting them can make Whisper insert them."""
    return len(name.split()) >= 2
