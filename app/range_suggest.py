"""Suggested ranges to re-transcribe (v2.31.1). Qt-free and unit-tested.

Looks for places where the first Whisper run probably went wrong, so the user can listen and,
if they agree, open Re-transcribe range on them. It only reads: nothing in the transcript changes.

Reasons it flags:
- gap with sound: a gap between segments where the audio is about as loud as speech
  (checked on the prepared 16 kHz WAV; if that file is missing, long gaps are flagged unchecked)
- repeated text: the same words looping inside one segment, or the same line several times in a row
- low confidence: Whisper's own word probabilities are low across the segment
- long or sparse segment: a very long segment, or very few words for its length (speech likely lost)

Reviewed segments are not flagged (the user has already checked them); gaps are still flagged.
"""
import math
import re
import wave

SAMPLE_RATE = 16000
FRAME = 0.1  # seconds per loudness frame

GAP, REPEAT, LOW_CONF, LONG = "Gap with sound", "Repeated text", "Low confidence", "Long or sparse segment"
GAP_UNCHECKED = "Long gap (sound not checked)"
REASONS = (GAP, GAP_UNCHECKED, REPEAT, LOW_CONF, LONG)

MIN_GAP = 4.0            # gaps shorter than this are normal pauses
UNCHECKED_GAP = 10.0     # without audio, only flag gaps this long
SOUND_DB_BELOW_SPEECH = 12.0
# A gap is flagged when at least MIN_SOUND_SECONDS of it is speech-level and that is MIN_SOUND_SHARE of the
# gap, or when SURE_SOUND_SECONDS of it is speech-level whatever its length. "More suggestions" uses the
# LOOSE values. Tuned on Session 7: the strict values still find the known 13 s hole at 1:08:34.
MIN_SOUND_SECONDS, MIN_SOUND_SHARE, SURE_SOUND_SECONDS = 2.5, 0.4, 5.0
LOOSE_SOUND_SECONDS, LOOSE_SOUND_SHARE = 1.5, 0.2
LOW_CONF_MEAN = 0.45
LOW_CONF_MIN_WORDS = 3
LONG_SECONDS = 25.0
SPARSE_SECONDS, SPARSE_WPS = 8.0, 0.6
MERGE_WITHIN, MAX_SPAN = 2.0, 90.0

_WORD = re.compile(r"[a-z0-9']+")


def _words(text):
    return _WORD.findall(str(text or "").lower())


def _norm(text):
    return " ".join(_words(text))


# ---------- loudness -------------------------------------------------------------------------

def _frames_db(samples):
    """dBFS of each FRAME of int16 samples (numpy array)."""
    import numpy as np
    n = int(SAMPLE_RATE * FRAME)
    usable = len(samples) // n * n
    if usable == 0:
        return np.zeros(0)
    x = samples[:usable].astype(np.float32).reshape(-1, n) / 32768.0
    rms = np.sqrt(np.mean(x * x, axis=1)) + 1e-9
    return 20 * np.log10(rms)


def _read(wf, start, end):
    import numpy as np
    total = wf.getnframes()
    first = max(0, min(total, int(start * SAMPLE_RATE)))
    last = max(first, min(total, int(end * SAMPLE_RATE)))
    wf.setpos(first)
    return np.frombuffer(wf.readframes(last - first), dtype="<i2")


def _open(wav_path):
    wf = wave.open(str(wav_path), "rb")
    if wf.getsampwidth() != 2 or wf.getframerate() != SAMPLE_RATE or wf.getnchannels() != 1:
        wf.close()
        raise RuntimeError("The prepared audio is not mono 16-bit 16 kHz.")
    return wf


def speech_level(wf, segments, samples=150):
    """Median loudness (dBFS) of the louder half of frames inside a spread of segments: a typical speech level."""
    import numpy as np
    usable = [s for s in segments if float(s["end"]) - float(s["start"]) >= 1.0 and str(s.get("text", "")).strip()]
    if not usable:
        return None
    step = max(1, len(usable) // samples)
    levels = []
    for s in usable[::step]:
        db = _frames_db(_read(wf, float(s["start"]), float(s["end"])))
        if len(db):
            levels.append(float(np.median(db[db >= np.median(db)])))
    return float(np.median(levels)) if levels else None


def sound_seconds(wf, start, end, threshold_db):
    db = _frames_db(_read(wf, start, end))
    return float((db >= threshold_db).sum()) * FRAME


# ---------- detectors ------------------------------------------------------------------------

def find_gaps(segments, min_gap=MIN_GAP, duration=None):
    """(start, end) of silences in the transcript longer than min_gap, including before the first and after the last segment."""
    ordered = sorted(segments, key=lambda s: (float(s["start"]), float(s["end"])))
    gaps, last_end = [], 0.0
    for s in ordered:
        if float(s["start"]) - last_end >= min_gap:
            gaps.append((last_end, float(s["start"])))
        last_end = max(last_end, float(s["end"]))
    if duration and duration - last_end >= min_gap:
        gaps.append((last_end, float(duration)))
    return gaps


def is_repetitive(text):
    """True when a segment's words loop: one word five or more times in a row, a 2-4 word phrase four or
    more times in a row, or very few distinct words. (People do say "yeah, yeah, yeah", so three is not enough.)"""
    w = _words(text)
    if len(w) < 6:
        return False
    for n in range(1, 5):
        times = 5 if n == 1 else 4
        for i in range(0, len(w) - times * n + 1):
            phrase = w[i:i + n]
            if all(w[i + k * n:i + (k + 1) * n] == phrase for k in range(1, times)):
                return True
    return len(w) >= 10 and len(set(w)) / len(w) < 0.3


def mean_probability(seg):
    probs = [float(w["probability"]) for w in seg.get("words") or [] if w.get("probability") is not None]
    return (sum(probs) / len(probs), len(probs)) if probs else (None, 0)


def segment_flags(segments):
    """[(start, end, reason, detail)] from the text and word timings alone."""
    ordered = sorted(segments, key=lambda s: (float(s["start"]), float(s["end"])))
    out = []
    for s in ordered:
        if s.get("speaker_confirmed"):
            continue
        a, b, text = float(s["start"]), float(s["end"]), str(s.get("text", ""))
        n = len(_words(text))
        if is_repetitive(text):
            out.append((a, b, REPEAT, "words repeat inside the line"))
        mean, count = mean_probability(s)
        if mean is not None and count >= LOW_CONF_MIN_WORDS and mean < LOW_CONF_MEAN:
            out.append((a, b, LOW_CONF, f"average word confidence {mean:.0%}"))
        dur = b - a
        if dur >= LONG_SECONDS:
            out.append((a, b, LONG, f"{dur:.0f} s long"))
        elif dur >= SPARSE_SECONDS and n / dur < SPARSE_WPS:
            out.append((a, b, LONG, f"{n} word(s) in {dur:.0f} s"))
    # the same line three or more times in a row (a Whisper loop)
    i = 0
    while i < len(ordered):
        key = _norm(ordered[i].get("text"))
        j = i
        while j + 1 < len(ordered) and key and _norm(ordered[j + 1].get("text")) == key:
            j += 1
        if key and j - i >= 2 and not all(x.get("speaker_confirmed") for x in ordered[i:j + 1]):
            out.append((float(ordered[i]["start"]), float(ordered[j]["end"]), REPEAT, f"same line {j - i + 1} times in a row"))
        i = j + 1
    return out


def gap_flags(segments, wav_path=None, duration=None, loose=False):
    """[(start, end, reason, detail)] for gaps. With the WAV, only gaps that contain speech-level sound."""
    gaps = find_gaps(segments, duration=duration)
    if not gaps:
        return []
    wf = None
    if wav_path:
        try:
            wf = _open(wav_path)
        except Exception:
            wf = None
    if wf is None:
        return [(a, b, GAP_UNCHECKED, f"{b - a:.0f} s with no text") for a, b in gaps if b - a >= UNCHECKED_GAP]
    try:
        level = speech_level(wf, segments)
        if level is None:
            return [(a, b, GAP_UNCHECKED, f"{b - a:.0f} s with no text") for a, b in gaps if b - a >= UNCHECKED_GAP]
        need, share = (LOOSE_SOUND_SECONDS, LOOSE_SOUND_SHARE) if loose else (MIN_SOUND_SECONDS, MIN_SOUND_SHARE)
        out = []
        for a, b in gaps:
            loud = sound_seconds(wf, a, b, level - SOUND_DB_BELOW_SPEECH)
            if loud >= need and (loud >= share * (b - a) or (not loose and loud >= SURE_SOUND_SECONDS)):
                out.append((a, b, GAP, f"{b - a:.0f} s with no text, {loud:.0f} s of it about as loud as speech"))
        return out
    finally:
        wf.close()


# ---------- combine --------------------------------------------------------------------------

def merge_flags(flags, within=MERGE_WITHIN, max_span=MAX_SPAN):
    """Join flags that touch or sit within a couple of seconds into ranges.
    -> [{'start','end','reasons':[...],'details':[...]}] sorted by time."""
    ranges = []
    for a, b, reason, detail in sorted(flags):
        cur = ranges[-1] if ranges else None
        if cur and a - cur["end"] <= within and max(b, cur["end"]) - cur["start"] <= max_span:
            cur["end"] = max(cur["end"], b)
            if reason not in cur["reasons"]:
                cur["reasons"].append(reason)
            cur["details"].append(detail)
        else:
            ranges.append({"start": a, "end": b, "reasons": [reason], "details": [detail]})
    return ranges


def context_text(segments, start, end, limit=220):
    """The transcript text inside a range (or a note that there is none)."""
    parts = [str(s.get("text", "")).strip() for s in sorted(segments, key=lambda s: float(s["start"]))
             if float(s["end"]) > start and float(s["start"]) < end and str(s.get("text", "")).strip()]
    text = " / ".join(parts) or "— no text in the transcript here —"
    return text if len(text) <= limit else text[:limit - 1] + "…"


def suggest_ranges(segments, wav_path=None, duration=None, loose=False):
    """Everything above, merged into ranges with their reasons and the current text."""
    flags = segment_flags(segments) + gap_flags(segments, wav_path, duration, loose)
    ranges = merge_flags(flags)
    for r in ranges:
        r["text"] = context_text(segments, r["start"], r["end"])
        r["start"], r["end"] = round(r["start"], 2), round(r["end"], 2)
    return ranges
