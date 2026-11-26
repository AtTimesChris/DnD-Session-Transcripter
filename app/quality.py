"""Cheap, Qt-free checks that flag likely Whisper hallucination loops ("Gleam, Gleam, Gleam, ...").
They only flag; nothing is changed or removed."""
import re
import zlib

_WORD = re.compile(r"[\w'’-]+", re.UNICODE)


def looks_repetitive(text, min_run=5, ratio=2.4, min_len=40):
    """True when a segment is dominated by the same word / short phrase repeated."""
    text = (text or "").strip()
    words = [w.casefold() for w in _WORD.findall(text)]
    if len(words) >= min_run:
        run = best = 1
        for a, b in zip(words, words[1:]):
            run = run + 1 if a == b else 1
            best = max(best, run)
        if best >= min_run:
            return True
        # repeated two-word phrase ("thank you thank you thank you ...")
        for size in (2, 3):
            grams = [tuple(words[i:i + size]) for i in range(0, len(words) - size + 1)]
            run = best = 1
            for a, b in zip(grams, grams[size:]):
                run = run + 1 if a == b else 1
                best = max(best, run)
            if best >= 3 and len(words) >= size * 4:
                return True
    raw = text.encode("utf-8")
    return len(raw) >= min_len and len(raw) / max(1, len(zlib.compress(raw))) > ratio
