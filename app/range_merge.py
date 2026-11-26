"""Re-transcribe a time range: line up the new Whisper text with the current transcript and merge
only what the user picks (v2.31). Qt-free and unit-tested.

Rules:
- Nothing is replaced unless the user picks "Use new" for that row.
- A row that contains a Reviewed segment is locked: it is always kept, and new text that overlaps
  it is not inserted.
- New segments are unreviewed. Their speaker comes from the saved speaker-identification turns if
  there are any, otherwise from the current segment they replace (a guess for the user to check).
"""
import uuid

from .alignment import TurnIndex, assign_speakers
from .compare_logic import align_slots

KEEP, NEW = "keep", "new"


def _overlaps(seg, a, b):
    return float(seg["end"]) > a and float(seg["start"]) < b


def build_slots(current, new, range_start, range_end, max_span=30.0):
    """Rows for the merge window.

    current: the editor's segments (with _edit_id, speaker_confirmed). Only those overlapping the range are used.
    new:     segments from the re-run, already in recording time. Only those whose middle falls inside
             the range are used (the clip is padded, so text from the padding is ignored).
    Returns [{'start','end','current':[...],'new':[...],'locked':bool}]."""
    a, b = float(range_start), float(range_end)
    cur = [s for s in current if _overlaps(s, a, b)]
    nw = [s for s in new if a <= (float(s["start"]) + float(s["end"])) / 2 <= b and str(s.get("text", "")).strip()]
    slots = []
    for slot in align_slots([cur, nw], max_span=max_span):
        old, fresh = slot["per"]
        slots.append({"start": slot["start"], "end": slot["end"], "current": old, "new": fresh,
                      "locked": any(bool(s.get("speaker_confirmed")) for s in old)})
    return slots


def can_use_new(slot):
    return bool(slot["new"]) and not slot["locked"]


def _guess_speaker(seg, replaced, all_segments):
    """Speaker of the replaced segment it overlaps most, else of the nearest current segment."""
    best, best_overlap = "", 0.0
    for old in replaced:
        overlap = min(seg["end"], float(old["end"])) - max(seg["start"], float(old["start"]))
        if overlap > best_overlap and old.get("speaker_id"):
            best, best_overlap = old["speaker_id"], overlap
    if best:
        return best
    mid = (seg["start"] + seg["end"]) / 2
    nearest = min((s for s in all_segments if s.get("speaker_id")), default=None,
                  key=lambda s: abs((float(s["start"]) + float(s["end"])) / 2 - mid))
    return nearest["speaker_id"] if nearest else ""


def _new_segment(n, new_id):
    return {"_edit_id": new_id(), "start": float(n["start"]), "end": float(n["end"]),
            "text": str(n.get("text", "")).strip(), "words": list(n.get("words") or []),
            "speaker_id": "", "speaker_confirmed": False, "confirmed_voice_sample": False}


def _auto_speakers(added, replaced, all_segments, turns):
    """Fill speaker_id on new segments: saved turns first, else the replaced/nearest segment."""
    if not added:
        return
    if turns:
        assign_speakers(added, turns)
    index = TurnIndex(turns) if turns else None
    for seg in added:
        if not seg["speaker_id"] or (index is not None and not index.overlaps(seg["start"], seg["end"])):
            seg["speaker_id"] = _guess_speaker(seg, replaced, all_segments)


def preview_speakers(slot, all_segments, turns=None):
    """The speaker each new segment of this row would get automatically (for the merge window), in order."""
    added = [_new_segment(n, lambda: "") for n in slot["new"]]
    replaced = [s for s in slot["current"] if not s.get("speaker_confirmed")]
    _auto_speakers(added, replaced, all_segments, turns)
    return [s["speaker_id"] for s in added]


def apply_choices(all_segments, slots, choices, turns=None, new_id=None, speakers=None):
    """Return (merged_segments, summary). choices[i] is KEEP or NEW for slots[i].
    speakers[i] (optional, v2.31.2): a speaker id the user picked for every new segment of row i, or (v2.31.3)
    a list with one entry per new segment of that row; empty or None means automatic. Reviewed segments are never removed, whatever the choice."""
    new_id = new_id or (lambda: str(uuid.uuid4()))
    speakers = list(speakers or [])
    remove, replaced, auto, added, picked = set(), [], [], [], 0
    picked_ids = []
    for i, (slot, choice) in enumerate(zip(slots, choices)):
        if choice != NEW or not can_use_new(slot):
            continue
        for old in slot["current"]:
            if not old.get("speaker_confirmed"):
                remove.add(old["_edit_id"]); replaced.append(old)
        forced = speakers[i] if i < len(speakers) else None
        for k, n in enumerate(slot["new"]):
            seg = _new_segment(n, new_id)
            pick = forced[k] if isinstance(forced, (list, tuple)) and k < len(forced) else (None if isinstance(forced, (list, tuple)) else forced)
            if pick:
                seg["speaker_id"] = pick; picked += 1; picked_ids.append(seg["_edit_id"])
            else:
                auto.append(seg)
            added.append(seg)
    _auto_speakers(auto, replaced, all_segments, turns)
    kept = [s for s in all_segments if s.get("_edit_id") not in remove]
    merged = sorted(kept + added, key=lambda s: (float(s["start"]), float(s["end"])))
    source = "speaker turns" if turns else "replaced segments (please check)"
    if picked:
        source = "your choice" if picked == len(added) else f"your choice ({picked}) and {source}"
    summary = {"rows_used": sum(1 for s, c in zip(slots, choices) if c == NEW and can_use_new(s)),
               "removed": len(remove), "added": len(added),
               "words_before": sum(len(str(s.get("text", "")).split()) for s in replaced),
               "words_after": sum(len(s["text"].split()) for s in added),
               "speakers_from": source,
               "new_ids": [s["_edit_id"] for s in added],
               "picked_ids": picked_ids}
    return merged, summary


def build_slots_multi(current, ranges, max_span=30.0):
    """Rows for several re-transcribed ranges at once (v2.31.3): ranges = [(start, end, new_segments)]."""
    slots = []
    for a, b, new in sorted(ranges, key=lambda r: r[0]):
        for slot in build_slots(current, new, a, b, max_span):
            slot["range"] = (a, b)
            slots.append(slot)
    return slots


def split_segment(seg, char_index):
    """Split one new segment's text at char_index into two segments (v2.31.3), for a line where two people
    speak. The split time comes from the word timings when there are any (the first word that starts at or
    after the split), otherwise from the share of characters. Returns (first, second) or None if either
    side would be empty."""
    text = str(seg.get("text", ""))
    left, right = text[:char_index].strip(), text[char_index:].strip()
    if not left or not right:
        return None
    a, b = float(seg["start"]), float(seg["end"])
    words = list(seg.get("words") or [])
    cut, first_words, second_words = None, words, []
    joined = "".join(str(w.get("word", "")) for w in words)
    if words and joined.strip() == text.strip():   # word timings still match the text
        lead = len(joined) - len(joined.lstrip()) - (len(text) - len(text.lstrip()))
        target = len(text[:char_index].rstrip())
        pos = 0
        for i, w in enumerate(words):
            token = str(w.get("word", ""))
            start_char = pos + (len(token) - len(token.lstrip())) - lead
            pos += len(token)
            if i > 0 and start_char >= target:
                cut = float(w["start"]); first_words, second_words = words[:i], words[i:]
                break
    if cut is None or not (a < cut < b):
        cut = a + (b - a) * (len(text[:char_index]) / max(1, len(text)))
        first_words = [w for w in words if float(w["start"]) < cut]
        second_words = [w for w in words if float(w["start"]) >= cut]
    cut = round(cut, 3)
    first = {**seg, "end": cut, "text": left, "words": first_words}
    second = {**seg, "start": cut, "text": right, "words": second_words}
    return first, second


def selection_range(segments, padding=0.0):
    """Start/end covering the given segments, with optional padding."""
    if not segments:
        return 0.0, 0.0
    start = min(float(s["start"]) for s in segments)
    end = max(float(s["end"]) for s in segments)
    return max(0.0, start - padding), end + padding
