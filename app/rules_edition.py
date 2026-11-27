"""Rules editions and book levels (v2.47). Qt-free and unit-tested.

A campaign's Game system says which D&D rules it uses (2024 / 5.5e, 2014 / 5e, or both). Each book in the campaign's
reference library has a level for Correct names and the Suggestions panel:

    Campaign book  the adventure you are running: matched at 80%, wins over every other book
    Rules          the core books the campaign uses: 85%
    Other          supplements and the other edition's core books: 92%
    Off            never used for names (and not read for spell dice)

A book's level starts from a default worked out here (so it follows the Game system when you change it) until you set
one yourself in D&D books…. The adventure's own edition does not matter: The Wild Beyond the Witchlight is a 2014 book
played with 2024 rules, and it is the Campaign book either way; only the core rulebooks follow the edition.
"""
import difflib
import re

LEVELS = [("campaign", "Campaign book", 0.80), ("rules", "Rules", 0.85), ("other", "Other", 0.92), ("off", "Off", None)]
LEVEL_LABEL = {k: label for k, label, _t in LEVELS}
LEVEL_THRESHOLD = {k: t for k, _l, t in LEVELS}
LEVEL_RANK = {k: i for i, (k, _l, _t) in enumerate(LEVELS)}

# the choices in the campaign's Game system list (you can still type anything)
GAME_SYSTEMS = ["D&D 5.5e (2024 rules)", "D&D 5e (2014 rules)", "D&D 5e + 5.5e mixed (2024 main)",
                "D&D 5e + 5.5e mixed (2014 main)", "Pathfinder 2e", "Other"]


def campaign_edition(game_system):
    """'2024', '2014', 'mixed-2024', 'mixed-2014' or '' from the Game system text. 5.5e, 5.5th Edition, One D&D and
    D&D 2024 are the 2024 rules; 5e and 2014 the older ones."""
    s = str(game_system or "").casefold().replace("’", "'")
    new = bool(re.search(r"5\.5|one\s*d\s*&?\s*d|onednd|\b2024\b|\b2025\b", s))
    old = bool(re.search(r"\b5e\b|\b5th\b|fifth|\b2014\b", re.sub(r"5\.5\s*(?:e|th)?", " ", s)))
    if "mix" in s or (new and old):
        return "mixed-2014" if re.search(r"2014 main|5e main|\(2014", s) and "2024 main" not in s else "mixed-2024"
    return "2024" if new else ("2014" if old else "")


def main_edition(edition):
    return {"2024": "2024", "mixed-2024": "2024", "2014": "2014", "mixed-2014": "2014"}.get(edition, "")


def book_edition(source_name, rules=""):
    """'2024', '2014' or '' for one book, from its D&D Beyond rules ("2024 rules (5.5e)") or a year in its name."""
    r = str(rules or "").casefold()
    if "2024" in r or "5.5e" in r:
        return "2024"
    if "2014" in r or "legacy" in r:
        return "2014"
    m = re.search(r"\b(20\d\d)\b", source_name or "")
    if m:
        return "2024" if int(m.group(1)) >= 2024 else "2014"
    return ""


_CORE = re.compile(r"player'?s handbook|dungeon master'?s guide|monster manual", re.I)


def is_core(source_name, category=""):
    return bool(_CORE.search((source_name or "").replace("’", "'")))


def _norm(s):
    s = re.sub(r"\.(md|markdown|txt|pdf)$", "", str(s or ""), flags=re.I).casefold().replace("’", "'")
    return " ".join(w for w in re.findall(r"[a-z0-9']+", s) if w not in ("the", "a", "of", "and"))


def is_campaign_book(source_name, campaign_name, title=""):
    """The book the campaign is named after ("Wild Beyond the Witchlight" -> The Wild Beyond the Witchlight.md)."""
    c = _norm(campaign_name)
    if len(c) < 4:
        return False
    for b in (_norm(source_name), _norm(title)):
        if b and (b == c or (len(c) >= 8 and (c in b or b in c)) or difflib.SequenceMatcher(None, b, c).ratio() >= 0.85):
            return True
    return False


def default_level(source_name, category, rules, campaign_name, edition, title=""):
    """The level a book starts at (see the module notes)."""
    if is_campaign_book(source_name, campaign_name, title):
        return "campaign"
    if is_core(source_name, category):
        ed = book_edition(source_name, rules)
        if edition.startswith("mixed") or not edition or not ed:
            return "rules"
        return "rules" if ed == edition else "other"
    if str(category or "").casefold().startswith("adventure"):
        return "off"   # another adventure: its names would be wrong guesses here
    return "other"


def level_of(source, campaign_name, edition):
    """(level, set_by_you) for a reference_sources row (dict with source_name, source_category, use_level,
    metadata_json)."""
    import json
    if source.get("use_level") in LEVEL_LABEL:
        return source["use_level"], True
    try:
        meta = json.loads(source.get("metadata_json") or "{}")
    except Exception:
        meta = {}
    return default_level(source.get("source_name", ""), source.get("source_category", ""), meta.get("rules", ""),
                         campaign_name, edition, meta.get("title", "")), False


def spell_order(sources, campaign_name, edition):
    """Books in the order their spell dice are trusted: the campaign's main-edition Rules books, other Rules books,
    the Campaign book, then Other. Off books are left out. sources: rows as for level_of."""
    main = main_edition(edition)

    def key(s):
        level, _set = level_of(s, campaign_name, edition)
        import json
        try:
            rules = json.loads(s.get("metadata_json") or "{}").get("rules", "")
        except Exception:
            rules = ""
        same = book_edition(s.get("source_name", ""), rules) == main if main else False
        return (LEVEL_RANK["rules"] if level == "rules" else 1.5 if level == "campaign" else LEVEL_RANK.get(level, 9) + 1,
                0 if same else 1)
    return [s for s in sorted(sources, key=key) if level_of(s, campaign_name, edition)[0] != "off"]
