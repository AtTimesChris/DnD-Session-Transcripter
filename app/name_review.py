"""Post-transcription name-correction pass (v2.30). Qt-free and unit-tested.

Finds likely misspelled campaign names in a transcript and returns suggestions for the user
to tick. Nothing is applied here except by `apply_to_text`, which the editor calls only for
suggestions the user ticked. Reviewed segments are never changed.

Two kinds of suggestion:
- known:  an exact match of a dictionary alias or an accepted mishearing ("Sibylna" -> "Zybilna").
- near:   a capitalised word that never appears in lower case in the campaign's transcripts
          (so it is almost certainly a proper noun) and is close to a canonical name or part of
          one ("Agdon" -> "Agden"). Plain fuzzy matching is useless on ordinary words
          ("with" -> "Witch"), which this filter removes.
"""
import difflib
import re

_TOKEN = re.compile(r"[A-Za-z][A-Za-z'’-]{3,}")
_POSSESSIVE = re.compile(r"(['’]s|['’])$")

DEFAULT_THRESHOLD = 0.75
# v2.43: names of these Campaign names categories are matched only as a whole ("Vicious Mockery", never "Mockery"
# on its own, so "Mickey" is no longer offered as Mockery)
WHOLE_NAME_CATEGORIES = ("spell", "item", "feat")


def whole_name_only(category):
    c = str(category or "").casefold()
    return any(c.startswith(k) for k in WHOLE_NAME_CATEGORIES)


_WORD = re.compile(r"(?<![\w'’])[A-Za-z][A-Za-z'’-]+")
_SENTENCE_END = re.compile(r"[.?!…:;\"“”]\s*$")


def lowercase_vocabulary(texts):
    """Ordinary words (casefolded): words written in lower case more often than capitalised
    in the middle of a sentence.

    v2.30r2: a single lower-case use used to be enough, so names that older unpunctuated
    transcripts wrote in lower case ("vaelith opens the door") were never suggested.
    Capitals at the start of a line or after . ? ! are not counted: they say nothing."""
    lower, capital = {}, {}
    for text in texts:
        lo, ca = _case_counts(str(text or ""))
        for k, n in lo.items():
            lower[k] = lower.get(k, 0) + n
        for k, n in ca.items():
            capital[k] = capital.get(k, 0) + n
    return {w for w, n in lower.items() if n > capital.get(w, 0)}


_CASE_CACHE = {}
_SENTENCE_END_CHARS = set('.?!…:;"“”')


def _case_counts(text):
    """(lower-case uses, mid-sentence capitalised uses) of each word in one text. v2.41: the same text (a book
    imported in the reference library) is counted once per session instead of on every Correct names."""
    big = len(text) > 20000
    if big and text in _CASE_CACHE:
        return _CASE_CACHE[text]
    lower, capital = {}, {}
    for line in text.splitlines():
        for m in _WORD.finditer(line):
            word = m.group(0)
            key = word.casefold()
            if word[0].islower():
                lower[key] = lower.get(key, 0) + 1
            else:
                j = m.start() - 1          # the last non-space character before the word (same as the old regex test)
                while j >= 0 and line[j].isspace():
                    j -= 1
                if j >= 0 and line[j] not in _SENTENCE_END_CHARS:
                    capital[key] = capital.get(key, 0) + 1
    if big:
        if len(_CASE_CACHE) > 40:
            _CASE_CACHE.clear()
        _CASE_CACHE[text] = (lower, capital)
    return lower, capital


def _ratio(a, b):
    return difflib.SequenceMatcher(None, a.casefold(), b.casefold()).ratio()


def _is_short_form(alias, canonical):
    """'Pip' for 'Pip Underhill', 'Whiskers' for 'Whiskers the Bat': a legitimate short name."""
    a, c = alias.casefold().split(), canonical.casefold().split()
    if not a or len(a) > len(c):
        return False
    return any(c[i:i + len(a)] == a for i in range(len(c) - len(a) + 1))


def _options_for(heard, canonical, whole=False):
    """Suggested replacements: the canonical name, plus its closest single word when that fits better
    (never for a spell, item or feat: whole=True)."""
    options = [canonical]
    if whole:
        return options
    parts = [p for p in canonical.split() if len(p) >= 4]
    if len(canonical.split()) > 1 and len(heard.split()) == 1 and parts:
        best = max(parts, key=lambda p: _ratio(heard, p))
        if _ratio(heard, best) >= _ratio(heard, canonical):
            options.insert(0, best)
        else:
            options.append(best)
    seen, unique = set(), []
    for o in options:
        if o.casefold() not in seen:
            seen.add(o.casefold()); unique.append(o)
    return unique


class NameIndex:
    """What the pass knows about this campaign's names.

    dictionary_rows: iterable of dicts with canonical_name, aliases (comma separated wrong spellings that
                     become the canonical name) and also_known_as (v2.36: other real names; each is a correct
                     name in its own right, never replaced by the canonical, and its misspellings are fixed to it).
    accepted_pairs:  (heard, intended) mishearings the user accepted.
    rejected_pairs:  (heard, intended) the user said never to suggest.
    """

    def __init__(self, dictionary_rows, accepted_pairs=(), rejected_pairs=(), people=(), book_names=(), known_words=()):
        """book_names (v2.46): [(name, book)] ticked in the D&D books window, matched more strictly and only when no
        campaign name fits. known_words: words already known to be right (your notes' names, words you chose in
        Correct names): with "hide words that are already a name" they are never offered."""
        self.canonical = []
        self.whole_only = set()   # v2.43: spells, items and feats: matched only as the whole name
        for row in dictionary_rows:
            for name in [row.get("canonical_name", "")] + str(row.get("also_known_as", "") or "").split(","):
                name = str(name or "").strip()
                if name and whole_name_only(row.get("category")):
                    self.whole_only.add(name.casefold())
                if name and name.casefold() not in {c.casefold() for c in self.canonical}:
                    self.canonical.append(name)
        # v2.30.1: players' names, nicknames and characters from the speaker mapping. They are never
        # "corrected" (Alex was offered as Axe) and misheard ones can be suggested.
        for name in people:
            name = str(name or "").strip()
            if len(name) >= 2 and name.casefold() not in {c.casefold() for c in self.canonical}:
                self.canonical.append(name)
        self.rejected = {(h.casefold().strip(), i.casefold().strip()) for h, i in rejected_pairs}
        canon_folded = {c.casefold() for c in self.canonical}
        self.known = {}  # heard (casefolded) -> (heard, canonical)
        pairs = []
        for row in dictionary_rows:
            canonical = str(row.get("canonical_name", "") or "").strip()
            # v2.36: a wrong spelling becomes whichever of the entry's names it is closest to, so
            # "Creeping Lynn" -> Creeping Lyn (an also-known-as) and "Endelin Moongrave" -> Endelyn Moongrave.
            names = [canonical] + [a.strip() for a in str(row.get("also_known_as", "") or "").split(",") if a.strip()]
            for alias in str(row.get("aliases", "") or "").split(","):
                alias = alias.strip()
                if alias:
                    pairs.append((alias, max(names, key=lambda n: _ratio(alias, n))))
        pairs.extend((str(h or "").strip(), str(i or "").strip()) for h, i in accepted_pairs)
        for heard, canonical in pairs:
            if not heard or not canonical:
                continue
            if heard.casefold() == canonical.casefold() or heard.casefold() in canon_folded:
                continue  # the alias IS a name ("Red" for "Red"), nothing to correct
            if _is_short_form(heard, canonical):
                continue
            self.known.setdefault(heard.casefold(), (heard, canonical))
        self.known_patterns = sorted(self.known.values(), key=lambda hc: -len(hc[0]))  # longest phrase first
        # Words that are already correct: never "correct" them.
        self.correct_words = set(canon_folded)
        for c in self.canonical:
            self.correct_words.update(p.casefold() for p in c.split())
        # v2.46: book names, by first letter and length (a misheard name nearly always keeps its first letter, and
        # this keeps 30,000 book names fast)
        self.book, self.book_source = {}, {}
        self.known_words = {str(w).casefold() for w in known_words if w}
        for name, source in book_names:
            name = str(name or "").strip()
            if len(name) < 4 or name.casefold() in canon_folded or len(name.split()) > 2:
                continue
            key = name.casefold()
            self.known_words.add(key)
            if key in self.book_source:
                continue
            # only names worth offering: 5+ letters ("Eric", "King", "Tegal" gave "Erica", "Kling", "Teal"), and for two
            # words, both capitalised and 3+ letters
            parts = name.split()
            if len(parts) == 1 and len(name) < 5:
                continue
            if len(parts) == 2 and not all(p[:1].isupper() and len(p) >= 3 for p in parts):
                continue
            self.book_source[key] = source
            self.book.setdefault((key[0], len(key)), []).append(name)

    def book_near(self, heard, threshold):
        """The closest book name to a heard word or two words, or None: (score, name, book)."""
        key = heard.casefold()
        if len(key) < 5:
            return None
        cache = self.__dict__.setdefault("_book_cache", {})
        if (key, threshold) in cache:
            return cache[(key, threshold)]
        best = None
        same = {key.rstrip("s"), key[:-2] if key.endswith("es") else key, re.sub(r"['’]s$", "", key)}
        sm = difflib.SequenceMatcher(None, "", key)   # the heard word is the cached side; quick bounds skip most names
        for n in range(len(key) - 2, len(key) + 3):
            for name in self.book.get((key[:1], n), ()):
                folded = name.casefold()
                if folded == key or folded in same:
                    continue   # the name itself, or said as a plural or possessive ("Kenkus")
                sm.set_seq1(folded)
                if sm.real_quick_ratio() < threshold or sm.quick_ratio() < threshold:
                    continue
                r = sm.ratio()
                if r >= threshold and (best is None or r > best[0]):
                    best = (r, name, self.book_source.get(folded, ""))
        cache[(key, threshold)] = best
        return best

    def near_targets(self, lower_vocab):
        """Canonical names and their words (4+ letters) that are not ordinary words. v2.43: a spell, item or feat
        is a target only as its whole name (a multi-word one is matched against two words said together)."""
        targets = []
        for c in self.canonical:
            whole = c.casefold() in self.whole_only
            for t in [c] + ([] if whole else [p for p in c.split() if len(p) >= 4]):
                if t.casefold() in lower_vocab:
                    continue  # "Star", "Red", "Light": ordinary words, too risky to suggest
                if " " in t and not whole:
                    continue  # whole multi-word names are matched through their words
                targets.append((t, c))
        return targets

    def is_rejected(self, heard, intended):
        return (heard.casefold().strip(), intended.casefold().strip()) in self.rejected


BOOK_THRESHOLD = 0.85


def find_suggestions(segments, index, lower_vocab, threshold=DEFAULT_THRESHOLD, skip_known=False, use_books=False,
                     book_threshold=BOOK_THRESHOLD):
    """segments: dicts with _edit_id, start, speaker_id, text, speaker_confirmed.
    v2.46: skip_known: a word spelled exactly like a known name (index.known_words) is not offered. use_books: when no
    campaign name is close, a book name at least book_threshold close is offered (kind 'book', with 'book').

    Returns (suggestions, skipped_reviewed). Each suggestion is a dict:
    edit_id, start, speaker_id, heard, offset, length, options, kind ('known'|'near'), score, text.
    Reviewed segments are not searched for suggestions; matches there are only counted.
    """
    out, skipped = [], 0
    targets = index.near_targets(lower_vocab)
    # v2.41: one matcher per name, kept for the whole scan (difflib caches the second sequence); the quick upper
    # bounds skip names that cannot reach the threshold. ratio() itself is unchanged, so the results are the same.
    matchers = [(difflib.SequenceMatcher(None, "", target.casefold()), target, canonical) for target, canonical in targets]
    near_cache = {}

    def near(stem):
        key = stem.casefold()
        if key not in near_cache:
            best = None
            for sm, target, canonical in matchers:
                if abs(len(target) - len(stem)) > 4:
                    continue
                sm.set_seq1(key)
                if sm.real_quick_ratio() < threshold or sm.quick_ratio() < threshold:
                    continue
                r = sm.ratio()
                if r >= threshold and (best is None or r > best[0]):
                    best = (r, target, canonical)
            near_cache[key] = best
        return near_cache[key]

    patterns = [(heard, canonical, re.compile(r"(?<![\w'’])" + re.escape(heard) + r"(?![\w])", re.IGNORECASE),
                 heard.casefold() if heard.isascii() else None) for heard, canonical in index.known_patterns]

    for seg in segments:
        text = str(seg.get("text", "") or "")
        if not text.strip():
            continue
        found, taken = [], []
        folded_text = text.casefold()
        for heard, canonical, rx, quick in patterns:
            if quick is not None and quick not in folded_text:
                continue   # cannot match: skip the regex
            for m in rx.finditer(text):
                span = (m.start(), m.end())
                if any(a < span[1] and span[0] < b for a, b in taken):
                    continue
                options = [o for o in _options_for(m.group(0), canonical, canonical.casefold() in index.whole_only) if not index.is_rejected(m.group(0), o)]
                if options:
                    taken.append(span)
                    found.append({"heard": m.group(0), "offset": m.start(), "length": m.end() - m.start(),
                                  "options": options, "kind": "known", "score": 1.0})
        _split_names(text, index, lower_vocab, near, found, taken, skip_known)  # before single words: the pair is more specific
        if use_books:
            _book_pairs(text, index, lower_vocab, found, taken, max(threshold, book_threshold), skip_known)
        for m in _TOKEN.finditer(text):
            word = m.group(0)
            stem = _POSSESSIVE.sub("", word)
            if len(stem) < 4 or not stem[0].isupper():
                continue
            folded = stem.casefold()
            if folded in lower_vocab or folded in index.correct_words or folded in index.known:
                continue
            if skip_known and folded in index.known_words:
                continue   # v2.46: already spelled like a known name ("Garry" once you chose it)
            span = (m.start(), m.start() + len(stem))
            if any(a < span[1] and span[0] < b for a, b in taken):
                continue
            best = near(stem)
            if not best and use_books:
                b = index.book_near(stem, max(threshold, book_threshold))
                if b and not index.is_rejected(stem, b[1]):
                    taken.append(span)
                    found.append({"heard": stem, "offset": span[0], "length": len(stem), "options": [b[1]],
                                  "kind": "book", "score": round(b[0], 2), "book": b[2]})
                continue
            if not best:
                continue
            score, target, canonical = best
            options = [o for o in _options_for(stem, canonical, canonical.casefold() in index.whole_only) if not index.is_rejected(stem, o)]
            if target in options:
                options.remove(target); options.insert(0, target)
            if options:
                taken.append(span)
                found.append({"heard": stem, "offset": span[0], "length": len(stem),
                              "options": options, "kind": "near", "score": round(score, 2)})
        if not found:
            continue
        if seg.get("speaker_confirmed"):
            skipped += len(found)
            continue
        for f in sorted(found, key=lambda f: f["offset"]):
            f.update({"edit_id": seg.get("_edit_id"), "start": float(seg.get("start", 0) or 0),
                      "speaker_id": seg.get("speaker_id") or "", "text": text})
            out.append(f)
    return out, skipped


_PAIR = re.compile(r"(?<![\w'’])([A-Za-z]{2,})[ -]([A-Za-z]{2,})(?![\w'’])")
SPLIT_STRICT_THRESHOLD = 0.85


def _book_pairs(text, index, lower_vocab, found, taken, threshold, skip_known):
    """v2.46: two words close to a two-word book name ("Chromatic Orbs", "Vicious Mockry"). At least one of the two
    words must be capitalised and not an ordinary word."""
    pos = 0
    while True:
        m = _PAIR.search(text, pos)
        if not m:
            return
        pos = m.start(2)
        first, second = m.group(1), m.group(2)
        span = (m.start(), m.end())
        if any(a < span[1] and span[0] < b for a, b in taken):
            continue
        pair = first + " " + second
        if not all(w[0].isupper() and len(w) >= 3 for w in (first, second)):
            continue   # "and Morgan", "Alex is": two capitalised words only
        if pair.casefold() in index.correct_words or (skip_known and pair.casefold() in index.known_words):
            continue
        b = index.book_near(pair, threshold)
        if not b or " " not in b[1] or index.is_rejected(pair, b[1]):
            continue
        if _POSSESSIVE.sub("", pair).casefold().rstrip("s") == b[1].casefold().rstrip("s"):
            continue   # the right name, said as a plural or possessive
        taken.append(span)
        found.append({"heard": pair, "offset": span[0], "length": span[1] - span[0], "options": [b[1]],
                      "kind": "book", "score": round(b[0], 2), "book": b[2]})


def _split_names(text, index, lower_vocab, near, found, taken, skip_known=False):
    """v2.30.1: a name Whisper split into two words ("Vay lith", "Vale Ith" for Vaelith).
    The two words are joined and compared like a single word. Rules (checked on Witchlight):
    - Skipped if either word alone is already close to a name ("Alec we", "Aelith is"):
      the single-word check handles that word.
    - Skipped if one word is a short ordinary word next to a longer or ordinary one ("Talivar do", "Orb is");
      "Ag Don" is kept because "ag" is not an ordinary word.
    - If both words are ordinary words, or both are lower case, the joined word must be very close
      (0.85), and two lower-case ordinary words are never offered."""
    pos = 0
    while True:
        m = _PAIR.search(text, pos)
        if not m:
            return
        pos = m.start(2)  # overlapping pairs: "a b c" tries "a b" then "b c"
        first, second = m.group(1), m.group(2)
        joined = first + second
        if len(joined) < 5:
            continue
        span = (m.start(), m.end())
        if any(a < span[1] and span[0] < b for a, b in taken):
            continue
        f1, f2 = first.casefold(), second.casefold()
        pair = first + " " + second
        # v2.43: two words said together that are close to a whole spell / item name ("Vishus Mockery")
        spaced = near(pair) if pair.casefold() not in index.correct_words else None
        if spaced and (spaced[2].casefold() not in index.whole_only
                       or _POSSESSIVE.sub("", pair).casefold().rstrip("s") == spaced[2].casefold().rstrip("s")):
            spaced = None   # not a spell / item, or the right name said as a plural or possessive ("chromatic orbs")
        if not spaced and (f1 in index.correct_words or f2 in index.correct_words or joined.casefold() in index.correct_words):
            continue
        if skip_known and (pair.casefold() in index.known_words or joined.casefold() in index.known_words):
            continue
        if any(w.casefold() in index.known for w in (first, second) if len(w) >= 4):
            continue
        joined_best = near(joined)
        pair_best = max((b for b in (joined_best, spaced) if b), key=lambda b: b[0], default=None)
        # v2.43: a word alone close to a name only wins when it fits at least as well as the two words joined:
        # "Thunder Wave" is Thunderwave (0.95), not "Thunder" -> Thunderwave (0.78) leaving "Thunderwave Wave"
        singles = [near(w) for w in (first, second) if len(w) >= 4]
        if any(s and (pair_best is None or s[0] >= pair_best[0]) for s in singles):
            continue
        ord1, ord2 = f1 in lower_vocab, f2 in lower_vocab
        if (ord1 and len(f1) <= 3 and (len(f2) >= 4 or ord2)) or (ord2 and len(f2) <= 3 and (len(f1) >= 4 or ord1)):
            continue  # a short ordinary word ("is", "do", "of") glued to another word: "Talivar do", "Orb is"
        both_lower = first[0].islower()
        if both_lower and ord1 and ord2:
            continue
        best = pair_best
        if not best:
            continue
        score, target, canonical = best
        if (both_lower or (ord1 and ord2)) and score < SPLIT_STRICT_THRESHOLD:
            continue
        heard = m.group(0)
        options = [o for o in _options_for(joined, canonical, canonical.casefold() in index.whole_only) if not index.is_rejected(heard, o)]
        if target in options:
            options.remove(target); options.insert(0, target)
        if options:
            taken.append(span)
            found.append({"heard": heard, "offset": span[0], "length": span[1] - span[0],
                          "options": options, "kind": "split", "score": round(score, 2)})


def apply_to_text(text, corrections):
    """corrections: [(offset, length, heard, replacement)]. Applies right to left and only where the
    text still says `heard` at that place. Returns (new_text, applied_count)."""
    applied = 0
    for offset, length, heard, replacement in sorted(corrections, key=lambda c: -c[0]):
        if text[offset:offset + length] != heard:
            continue
        text = text[:offset] + replacement + text[offset + length:]
        applied += 1
    return text, applied


def context_snippet(text, offset, length, width=60):
    """'…words before ‹heard› words after…' for the review table."""
    start, end = max(0, offset - width), min(len(text), offset + length + width)
    before = ("…" if start > 0 else "") + text[start:offset]
    after = text[offset + length:end] + ("…" if end < len(text) else "")
    return f"{before}‹{text[offset:offset + length]}›{after}"
