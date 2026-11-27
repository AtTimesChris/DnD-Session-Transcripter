"""Names from the D&D books (v2.46). Qt-free and unit-tested.

A book word is offered as a *name* only when the book writes it with a capital letter in the middle of a sentence
more often than in lower case ("Aboleth", "Waterdeep", "Chromatic Orb"). Words that are only capitalised because
they start a sentence, a heading or a bold paragraph label ("About", "Has", "Talk.") are ordinary words: they are
still listed in the review, but not ticked, and never used for corrections.

The old term finder (before v2.46) stopped after the first 1,500 terms of a book (so most of a long book was never
read) and joined a heading to the first word of the next line ("Prying Eyes Everyone"). This one reads every line
of the whole book and never joins two lines.

Used by Correct names (book names, stricter match) and the editor's Suggestions panel. Whisper never gets them:
its prompt holds about 700 characters and the campaign's own names fill it.
"""
import re

_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")
_CONNECT = {"of", "the", "and", "de", "von", "van", "du", "la", "le", "da", "del"}
_LEAD_STRIP = {"the", "a", "an", "of", "and", "this", "that", "these", "those", "its", "his", "her", "their", "your", "our", "my"}
_SENTENCE_END = set('.?!…:;"“”')
_MARKUP = set("*_>#|-•([{“\"'`~+=0123456789 \t")
MAX_TERMS = 6000


def _sentence_start(line, pos):
    """True when the word at pos starts a sentence, a heading, a list item or a bold label: its capital says nothing."""
    j = pos - 1
    while j >= 0 and line[j] in _MARKUP:
        j -= 1
    return j < 0 or line[j] in _SENTENCE_END


def _is_heading(line):
    s = line.lstrip()
    return s.startswith("#") or s.startswith("|")


def case_counts(text):
    """(lower-case uses, capitalised uses in the middle of a sentence) of each word (casefolded). Headings and table
    rows are not counted (their words are capitalised by style)."""
    lower, capital = {}, {}
    for line in text.splitlines():
        if _is_heading(line):
            continue
        for m in _WORD.finditer(line):
            w = m.group(0)
            k = w.casefold()
            if w[0].islower():
                lower[k] = lower.get(k, 0) + 1
            elif not _sentence_start(line, m.start()):
                capital[k] = capital.get(k, 0) + 1
    return lower, capital


def _runs(line):
    """Capitalised word runs in one line ("Tomb of Annihilation", "Beledros Witherbloom"): [(phrase, start)]."""
    out, run = [], []
    words = list(_WORD.finditer(line))
    for i, m in enumerate(words):
        w = m.group(0)
        gap_ok = not run or re.fullmatch(r"[ ]+", line[run[-1].end():m.start()]) is not None
        if w[0].isupper() and len(w) > 1 and gap_ok:
            run.append(m)
        elif run and w.casefold() in _CONNECT and gap_ok and i + 1 < len(words) and words[i + 1].group(0)[0].isupper():
            run.append(m)
        else:
            if run:
                out.append(run)
            run = [m] if (w[0].isupper() and len(w) > 1) else []
    if run:
        out.append(run)
    phrases = []
    for r in out:
        while r and r[0].group(0).casefold() in _LEAD_STRIP:
            r = r[1:]
        while r and r[-1].group(0).casefold() in _CONNECT:
            r = r[:-1]
        if len(r) > 4:
            r = r[:4]
            while r and r[-1].group(0).casefold() in _CONNECT:   # v2.49: "Sleight of Hand and" (cut at four words)
                r = r[:-1]
        if r:
            phrase = line[r[0].start():r[-1].end()].strip(" -'’")
            phrase = re.sub(r"(['’]s|['’])$", "", phrase)   # "Strahd's" -> Strahd
            if 3 <= len(phrase) <= 64:
                phrases.append((phrase, r[0].start()))
    return phrases


_CREDITS = re.compile(r"^#+\s*(?:credits|acknowledg(?:e)?ments|special thanks|playtesters|about the authors?)\b", re.I)


def _without_credits(text):
    """The book without its Credits sections (the people who made it are not names to correct a session to)."""
    out, skip, level = [], False, 0
    for line in (text or "").splitlines():
        m = re.match(r"^(#+)\s", line)
        if m:
            if _CREDITS.match(line):
                skip, level = True, len(m.group(1)); continue
            if skip and len(m.group(1)) <= level:
                skip = False
        if not skip:
            out.append(line)
    return "\n".join(out)


def book_terms(text, limit=MAX_TERMS):
    """Every name-or-term candidate in a book: [{'term', 'name_like', 'count'}], the most used first (at most limit).
    name_like: the book writes it as a name (see the module notes). Credits sections are left out."""
    text = _without_credits(text)
    lower, capital = case_counts(text or "")
    found, mid = {}, {}

    def very_ordinary(w):
        k = w.casefold()
        return lower.get(k, 0) >= 5 * max(1, capital.get(k, 0))
    for line in (text or "").splitlines():
        heading = _is_heading(line)
        for phrase, start in _runs(line):
            # "Perhaps the NPC", "While Marquis Atrith": a sentence-start word glued on (only at a sentence start)
            parts = phrase.split(" ")
            while len(parts) > 1 and _sentence_start(line, start) and very_ordinary(parts[0]):
                start += len(parts[0]) + 1
                parts = parts[1:]
                while len(parts) > 1 and parts[0].casefold() in _LEAD_STRIP:
                    start += len(parts[0]) + 1
                    parts = parts[1:]
            phrase = " ".join(parts)
            if len(phrase) < 3:
                continue
            found[phrase] = found.get(phrase, 0) + 1
            if not heading and not _sentence_start(line, start):
                mid[phrase] = mid.get(phrase, 0) + 1
    out = []
    for phrase, count in found.items():
        words = [w for w in _WORD.findall(phrase) if w.casefold() not in _CONNECT]
        if not words:
            continue
        if len(words) == 1 and words[0].isupper() and len(words[0]) <= 2:
            continue   # "DM", "AC", "HP"
        ordinary = [lower.get(w.casefold(), 0) >= max(1, capital.get(w.casefold(), 0)) for w in words]
        if len(words) == 1:
            name_like = not ordinary[0] and (capital.get(words[0].casefold(), 0) > 0 or words[0].isupper())
        else:
            name_like = not all(ordinary) or mid.get(phrase, 0) >= 2
        out.append({"term": phrase, "name_like": bool(name_like), "count": count})
    # v2.49: every spell described in the book is a name, however the book writes it in sentences (the 2014 Player's
    # Handbook writes spells in lower case, so only 581 of its words were names)
    try:
        from .spell_book import parse_spells
        spells = {v["name"] for v in parse_spells(text).values()}
    except Exception:
        spells = set()
    have = {t["term"]: t for t in out}
    for name in spells:
        if name in have:
            have[name]["name_like"] = True
        elif 3 <= len(name) <= 64:
            out.append({"term": name, "name_like": True, "count": 1})
    out.sort(key=lambda t: (-t["name_like"], -t["count"], t["term"].casefold()))
    out = out[:limit]
    out.sort(key=lambda t: t["term"].casefold())
    return out


def name_like_terms(text):
    """Only the names (the terms ticked by default)."""
    return [t["term"] for t in book_terms(text) if t["name_like"]]
