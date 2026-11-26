"""Split and speaker markup typed into a transcript line (v2.41). Qt-free and unit-tested.

    {Sam} Okay, Oh my God! | {Alex} Yay! | {Kit} Let's go.

"|" splits the line; "{name}" at the start of a part says who said it (a player, a nickname, a character, or a
speaker label, in any case). Nothing happens until the user applies it. The split times come from Whisper's word
timings when the line still has them, otherwise from each part's share of the characters.
"""
import re

from .range_merge import split_segment

_LEAD = re.compile(r"^\s*\{([^{}]*)\}\s*")


def has_markup(text):
    t = str(text or "")
    return "|" in t or bool(_LEAD.match(t))


def parse_markup(text):
    """[(name or None, part text)] for a line with markup, else None. Empty parts are dropped."""
    if not has_markup(text):
        return None
    parts = []
    for raw in str(text).split("|"):
        m = _LEAD.match(raw)
        name = m.group(1).strip() if m else None
        body = raw[m.end():] if m else raw
        body = " ".join(body.split())
        if body:
            parts.append((name or None, body))
    return parts


def resolve(parts, lookup):
    """lookup: {name casefolded: speaker id}. -> (speaker ids or None per part, [unknown names])."""
    sids, unknown = [], []
    for name, _text in parts:
        if not name:
            sids.append(None)
            continue
        sid = lookup.get(name.casefold().strip())
        if sid is None:
            unknown.append(name)
        sids.append(sid)
    return sids, unknown


def _tokens(text):
    return re.findall(r"[a-z0-9']+", str(text or "").casefold())


def aligned_cuts(texts, words, start, end):
    """v2.42: where each part after the first starts, from Whisper's word timings, even when the text was edited
    (88 -> 8d8, a name corrected, punctuation added). The parts' words are matched to Whisper's words loosely
    (difflib on lower-case words); a part starts at the Whisper word matched to its first matched word.
    -> [time or None] per boundary (None: nothing matched there; the caller shares the time out by length)."""
    spoken = []
    for w in words or []:
        for tok in _tokens(w.get("word", "")):
            spoken.append((tok, float(w["start"])))
    if not spoken:
        return [None] * (len(texts) - 1)
    typed, first_index = [], []
    for t in texts:
        first_index.append(len(typed))
        typed += _tokens(t)
    import difflib
    sm = difflib.SequenceMatcher(None, typed, [t for t, _s in spoken], autojunk=False)
    match = {}
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            match[a + k] = b + k
    cuts, last = [], float(start)
    for i in range(1, len(texts)):
        lo, hi = first_index[i], (first_index[i + 1] if i + 1 < len(texts) else len(typed))
        hit = next((match[k] for k in range(lo, hi) if k in match), None)
        t = None
        if hit is not None:
            # back up over the part's unmatched leading words (an edited first word): use the word before the match
            back = next(k for k in range(lo, hi) if k in match) - lo
            t = spoken[max(0, hit - back)][1]
        if t is not None and not (last < t < float(end)):
            t = None
        if t is not None:
            last = t
        cuts.append(t)
    return cuts


def split_by_parts(seg, texts, words=None):
    """The segment cut into len(texts) segments with these texts, in order. The first part keeps the segment's id.
    Times: from Whisper's word timings (words, else the segment's own), matched loosely so edited text still lines
    up (aligned_cuts); a boundary nothing matches gets the share of characters (split_segment)."""
    texts = [t for t in texts if t]
    if len(texts) <= 1:
        return [{**seg, "text": texts[0] if texts else ""}]
    whole = " ".join(texts)
    cuts = aligned_cuts(texts, words if words is not None else seg.get("words"), seg["start"], seg["end"])
    rest = {**seg, "text": whole, "words": []}
    out = []
    for t, cut in zip(texts[:-1], cuts):
        if cut is not None and float(rest["start"]) < cut < float(rest["end"]):
            first = {**rest, "end": round(cut, 3), "text": t}
            rest = {**rest, "start": round(cut, 3), "text": rest["text"][len(t):].strip()}
        else:
            pieces = split_segment(rest, len(t))
            if not pieces:
                continue
            first, rest = pieces
            first["text"] = t
        out.append(first)
    out.append(rest)
    return out
