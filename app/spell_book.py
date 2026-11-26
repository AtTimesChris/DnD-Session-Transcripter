"""Spell dice from the books imported in the reference library (v2.42). Qt-free and unit-tested.

Reads spell blocks written the way the 2024 books are exported to Markdown:

    #### Backlash
    *Level 4 Abjuration*            (or *Evocation Cantrip*)
    ...
    The creature takes 4d6 Force damage on a failed save ...
    ***Using a Higher-Level Spell Slot.*** The ... damage ... increase by 1d6 for every spell slot level above 4.

Only reads; the book text never leaves the computer. A book without the spell descriptions simply gives nothing.
"""
import hashlib
import re

_HEAD = re.compile(r"(?m)^#{2,6}\s+(?P<name>[^\n#]{2,60}?)\s*\n+\s*\*(?P<kind>Level\s+(?P<lvl>[1-9])\s+[A-Za-z]+|[A-Za-z]+\s+Cantrip)[^*\n]*\*")
_DAMAGE = re.compile(r"\b(?P<c>\d{1,2})d(?P<s>4|6|8|10|12|20)\b(?:\s*\+\s*\d+)?\s+(?:(?P<type>[A-Z][a-z]+)\s+)?damage\b")
_HIGHER = re.compile(r"increases?\s+by\s+(?P<c>\d{1,2})d(?P<s>4|6|8|10|12|20)\s+for\s+(?:each|every)\s+(?:spell\s+)?slot\s+level\s+above\s+(?P<lvl>\d)", re.I)
_CANTRIP_STEPS = re.compile(r"levels?\s+5\s*\((?P<a>\d)d\d+\),\s*11\s*\((?P<b>\d)d\d+\),\s*and\s*17\s*\((?P<c>\d)d\d+\)", re.I)
_CACHE = {}


def parse_spells(text):
    """{name casefolded: {'name','level','dice':(count, sides) or None,'type','higher':(count, sides) or None,
    'cantrip_steps':(n5, n11, n17) or None}} for every spell block in the text."""
    text = text or ""
    key = hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()
    if key in _CACHE:
        return _CACHE[key]
    out = {}
    heads = list(_HEAD.finditer(text))
    for i, m in enumerate(heads):
        body_end = heads[i + 1].start() if i + 1 < len(heads) else min(len(text), m.end() + 6000)
        body = text[m.end():body_end]
        stop = re.search(r"(?m)^\s*\*\*Classes:\*\*|^#{1,6}\s", body)
        if stop:
            body = body[:stop.start()]
        name = " ".join(m.group("name").split()).strip("*_ ")
        level = int(m.group("lvl")) if m.group("lvl") else 0
        d = _DAMAGE.search(body)
        h = _HIGHER.search(body)
        steps = _CANTRIP_STEPS.search(body) if level == 0 else None
        out[name.casefold()] = {"name": name, "level": level,
                                "dice": (int(d.group("c")), int(d.group("s"))) if d else None,
                                "type": (d.group("type") or "").capitalize() if d else "",
                                "higher": (int(h.group("c")), int(h.group("s"))) if h and int(h.group("lvl")) == level else None,
                                "cantrip_steps": tuple(int(steps.group(k)) for k in "abc") if steps else None}
    if len(_CACHE) > 60:
        _CACHE.clear()
    _CACHE[key] = out
    return out


def spells_from_books(texts):
    """All spells found in the given book texts (the first book that has a spell wins)."""
    out = {}
    for t in texts or []:
        for k, v in parse_spells(t).items():
            out.setdefault(k, v)
    return out


def expected_dice(spell, slot_level=None, character_level=None, critical=False):
    """The damage dice for this casting: [(count, sides)] or None. Upcasting adds the higher-level dice per slot
    level above the spell's; a cantrip uses the character level steps (5, 11, 17); a critical doubles the dice."""
    if not spell or not spell.get("dice"):
        return None
    count, sides = spell["dice"]
    if spell["level"] == 0 and spell.get("cantrip_steps") and character_level:
        n5, n11, n17 = spell["cantrip_steps"]
        count = n17 if character_level >= 17 else n11 if character_level >= 11 else n5 if character_level >= 5 else count
    elif slot_level and spell.get("higher") and slot_level > spell["level"]:
        hc, hs = spell["higher"]
        if hs == sides:
            count += hc * (slot_level - spell["level"])
        else:
            return [(count * (2 if critical else 1), sides), (hc * (slot_level - spell["level"]) * (2 if critical else 1), hs)]
    return [(count * (2 if critical else 1), sides)]


def dice_text(dice):
    return " + ".join(f"{c}d{s}" for c, s in dice or [])
