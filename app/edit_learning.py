"""Learn recurring word corrections from the user's own transcript edits ("ice cream" ->
"Ice Knife"). Qt-free and unit-tested.

Net change per segment (earliest 'before' -> latest 'after') is diffed word by word; short
replaced phrases become (heard, intended) candidates, counted across segments and runs.
The user approves candidates; nothing is applied automatically.
"""
import difflib
import re

_WORD = re.compile(r"[\w'’-]+", re.UNICODE)


def net_changes(events):
    """events: rows/dicts with run_id, segment_id, event_type, before_value, after_value in
    chronological order. Returns [(before_text, after_text)] one per (run, segment)."""
    first, last = {}, {}
    for e in events:
        if e["event_type"] != "Text corrected" or not e["segment_id"]:
            continue
        key = (e["run_id"], e["segment_id"])
        first.setdefault(key, e["before_value"] or "")
        last[key] = e["after_value"] or ""
    return [(first[k], last[k]) for k in first if first[k].strip() and last[k].strip()]


def _tokens(text):
    return _WORD.findall(text)


def diff_pairs(before, after, max_words=3):
    """Replaced word runs between two texts as (heard, intended) pairs."""
    a, b = _tokens(before), _tokens(after)
    out = []
    matcher = difflib.SequenceMatcher(None, [w.casefold() for w in a], [w.casefold() for w in b], autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op != "replace":
            continue
        # A neighbouring word that only changed case belongs to the same name ("ice cream" -> "Ice Knife").
        while i1 > 0 and j1 > 0 and a[i1 - 1] != b[j1 - 1]:
            i1, j1 = i1 - 1, j1 - 1
        while i2 < len(a) and j2 < len(b) and a[i2] != b[j2] and a[i2].casefold() == b[j2].casefold():
            i2, j2 = i2 + 1, j2 + 1
        if not (1 <= i2 - i1 <= max_words and 1 <= j2 - j1 <= max_words):
            continue
        heard, intended = " ".join(a[i1:i2]), " ".join(b[j1:j2])
        if heard.casefold() == intended.casefold() or len(heard) > 40 or len(intended) > 40:
            continue
        out.append((heard, intended))
    return out


def mine_corrections(events, known=(), min_count=1):
    """Return [{'heard','intended','count'}] most frequent first, skipping `known` pairs
    (case-insensitive tuples of (heard, intended))."""
    known = {(h.casefold(), i.casefold()) for h, i in known}
    counts = {}
    for before, after in net_changes(events):
        for heard, intended in diff_pairs(before, after):
            key = (heard.casefold(), intended)
            if (heard.casefold(), intended.casefold()) in known:
                continue
            entry = counts.setdefault(key, {"heard": heard, "intended": intended, "count": 0})
            entry["count"] += 1
    result = [v for v in counts.values() if v["count"] >= min_count]
    return sorted(result, key=lambda v: (-v["count"], v["heard"].casefold()))
