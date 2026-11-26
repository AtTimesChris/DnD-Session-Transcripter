"""Qt-free logic for the transcript comparison window.

Versions are aligned by TIME (each segment is compared with the reference text spoken at the same
moment), then compared word by word with difflib (the standard-library sequence matcher that word
error rate tools are built on). Capitalisation and punctuation are ignored, so only real word
differences are highlighted. Comparing small time-aligned pieces keeps it fast on four-hour sessions.
"""
import difflib
import json
import re
from datetime import datetime

_TOKEN = re.compile(r"\S+")
_STRIP = re.compile(r"[^\w']+", re.UNICODE)


def _norm(word):
    return _STRIP.sub("", word.casefold().replace("’", "'"))


def load_segments(run):
    try:
        segs = json.loads((run["segments_json"] if "segments_json" in run.keys() else "") or "[]")
    except Exception:
        segs = []
    out = []
    for s in segs:
        text = str(s.get("text", "")).strip()
        if text:
            out.append({"start": float(s.get("start", 0)), "end": float(s.get("end", 0)), "text": text})
    return sorted(out, key=lambda s: (s["start"], s["end"]))


def align_slots(seg_lists, max_span=40.0):
    """Group the segments of all versions into time slots so every version has the same rows.

    A slot is a stretch of time in which segments from any version overlap. Slots are also cut when
    they grow longer than `max_span` seconds, so one long run of speech cannot merge into a single row.
    Returns [{'start','end','per': [segments of version 0, of version 1, ...]}]."""
    items = sorted(((s["start"], s["end"], k, s) for k, segs in enumerate(seg_lists) for s in segs), key=lambda t: (t[0], t[1]))
    slots, cur = [], None
    for start, end, k, seg in items:
        if cur is not None and (start < cur["end"] - 0.05) and (end - cur["start"] <= max_span):
            cur["end"] = max(cur["end"], end)
        else:
            cur = {"start": start, "end": end, "per": [[] for _ in seg_lists]}
            slots.append(cur)
        cur["per"][k].append(seg)
    return slots


def _diff_flags(words, other_words):
    """True for each word in `words` that is not matched in `other_words` (case and punctuation ignored)."""
    a = [_norm(w) for w in words]
    b = [_norm(w) for w in other_words]
    flags = [True] * len(words)
    for block in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(block.a, block.a + block.size):
            flags[k] = False
    for k, w in enumerate(a):  # bare punctuation is never a difference
        if not w:
            flags[k] = False
    return flags


def build_view(seg_lists, threshold=1):
    """Aligned, highlighted rows for every version.

    Returns (rows, differs) where rows[v] is a list with one row per slot:
      {'start', 'words': [(word, flag)], 'kind', 'changed'}
    kind: 'same'    the row matches the reference (or has only small differences)
          'changed' some words differ from the reference (reference column: from another version)
          'only'    this version has speech here and the reference has none (green)
          'missing' reference column: another version has nothing here (red)
          'gap'     this version has nothing here
    differs[i] is True when slot i differs in any version (used by "only lines that differ")."""
    n = len(seg_lists)
    slots = align_slots(seg_lists)
    rows = [[] for _ in range(n)]
    differs = []
    for slot in slots:
        words = [_TOKEN.findall(" ".join(x["text"] for x in slot["per"][k])) for k in range(n)]
        ref = words[0]
        slot_differs = False
        for k in range(n):
            w = words[k]
            if not w:
                kind, flags = "gap", []
                if k > 0 and ref:
                    slot_differs = True
            elif k == 0:
                flags = [False] * len(w)
                kind = "same"
                for j in range(1, n):
                    if not words[j]:
                        flags = [True] * len(w); kind = "missing"; break
                    for idx, f in enumerate(_diff_flags(w, words[j])):
                        flags[idx] = flags[idx] or f
                if kind != "missing" and sum(flags) >= threshold:
                    kind = "changed"
                if kind in ("missing", "changed"):
                    slot_differs = True
            elif not ref:
                flags, kind = [True] * len(w), "only"
                slot_differs = True
            else:
                flags = _diff_flags(w, ref)
                kind = "changed" if sum(flags) >= threshold else "same"
                if kind == "changed":
                    slot_differs = True
            rows[k].append({"start": slot["start"], "words": list(zip(w, flags)), "kind": kind, "changed": sum(flags)})
        differs.append(slot_differs)
    return rows, differs


# ------------------------------------------------------------------ readable properties
def _fmt_date(value):
    try:
        return datetime.fromisoformat(str(value)).astimezone().strftime("%d %b %Y %H:%M")
    except Exception:
        return str(value)[:16].replace("T", " ")


def _minutes(seconds):
    try:
        m = int(round(float(seconds) / 60))
        return f"{m // 60} h {m % 60} min" if m >= 60 else f"{m} min"
    except Exception:
        return "?"


def describe_run(run, segments=None):
    """Short, readable property lines for one transcript version (older versions say 'not recorded')."""
    keys = run.keys()
    meta = {}
    try:
        meta = json.loads((run["metadata_json"] if "metadata_json" in keys else "") or "{}")
    except Exception:
        pass
    get = lambda k: meta.get(k, None)
    yesno = lambda v: "not recorded" if v is None else ("on" if v else "off")
    total = len(segments) if segments is not None else run["segment_count"] if "segment_count" in keys else 0
    reviewed = sum(1 for s in (segments or []) if s.get("speaker_confirmed"))
    lines = [f"{run['model_name']} · {run['device']} · {run['compute_type']} · {_fmt_date(run['created_at'])}"]
    lines.append(f"Segments: {total:,}" + (f" ({reviewed:,} reviewed)" if reviewed else ""))
    vad = get("vad_threshold")
    lines.append(f"Audio levelling: {yesno(get('audio_levelling'))} · Speech threshold: {vad if vad is not None else 'not recorded'}")
    lines.append(f"Name prompt: {yesno(get('initial_prompt_used'))} · Hotwords: {yesno(get('hotwords_used'))}")
    style = get("style_hint_used")
    if style is None and get("hotwords_used") is not None:
        style = False  # recorded before v2.29.1, when there was no style hint
    lines.append(f"Punctuation style hint: {yesno(style)}")
    status = get("completion_status")
    took = get("transcription_seconds")
    lines.append(f"Status: {'complete' if status == 'COMPLETE' else 'partial (stopped early)' if status == 'PARTIAL_STOPPED' else (status or 'not recorded')}"
                 + (f" · took {_minutes(took)}" if took else ""))
    if get("hallucination_silence_threshold"):
        lines.append(f"Loop guard: on ({get('hallucination_silence_threshold')} s)")
    return lines


def split_sentence_candidates(segments, max_gap=1.0, short_seconds=1.5, tiny_gap=0.3):
    """Indexes of segments that probably continue into the next one. Only flags; merging is the user's decision.
    A segment is flagged when the next segment has the same speaker, starts within `max_gap` seconds and
    either this one has no sentence-ending punctuation, or it is very short (under `short_seconds`)
    and the next one follows almost immediately (within `tiny_gap`)."""
    flagged = set()
    for i in range(len(segments) - 1):
        cur, nxt = segments[i], segments[i + 1]
        text = str(cur.get("text", "")).rstrip()
        if not text:
            continue
        if str(cur.get("speaker_id") or "") != str(nxt.get("speaker_id") or ""):
            continue
        gap = float(nxt.get("start", 0)) - float(cur.get("end", 0))
        if gap > max_gap:
            continue
        ends_sentence = text[-1] in ".?!…\"”)"
        duration = float(cur.get("end", 0)) - float(cur.get("start", 0))
        if (not ends_sentence) or (duration <= short_seconds and gap <= tiny_gap):
            flagged.add(i)
    return flagged


def split_context_indexes(flagged, count):
    """For each flagged run, the segment just before it and the segment it probably continues into."""
    extra = set()
    for i in flagged:
        if i - 1 >= 0 and (i - 1) not in flagged:
            extra.add(i - 1)
        if i + 1 < count and (i + 1) not in flagged:
            extra.add(i + 1)
    return extra


def _words(text):
    return [w for w in (_norm(t) for t in _TOKEN.findall(str(text))) if w]


def edit_report(run, max_items=40):
    """Markdown report of what the user changed in the segments they marked reviewed, compared with the
    original Whisper output of the same run. Shows what Whisper gets wrong so settings can be tuned."""
    try:
        raw = json.loads((run["raw_segments_json"] if "raw_segments_json" in run.keys() else "") or "[]")
        cur = json.loads((run["segments_json"] if "segments_json" in run.keys() else "") or "[]")
    except Exception:
        raw, cur = [], []
    raw = sorted((s for s in raw if str(s.get("text", "")).strip()), key=lambda s: float(s.get("start", 0)))
    reviewed = [s for s in cur if s.get("speaker_confirmed") and str(s.get("text", "")).strip()]
    reviewed.sort(key=lambda s: float(s.get("start", 0)))
    stats = {"reviewed": len(reviewed), "unchanged": 0, "reworded": 0, "no_raw_speech": 0, "merged": 0, "split": 0,
             "raw_words": 0, "final_words": 0, "words_added": 0, "words_removed": 0, "words_replaced": 0, "speaker_changed": 0}
    subs, added, removed = {}, {}, {}
    raw_hits = {}
    import bisect
    starts = [float(s.get("start", 0)) for s in raw]
    ends_max = [float(s.get("end", 0)) for s in raw]
    long_gaps = []
    for seg in reviewed:
        a, b = float(seg.get("start", 0)), float(seg.get("end", 0))
        i = max(0, bisect.bisect_left(starts, a) - 30)
        over = []
        while i < len(raw) and starts[i] < b:
            if ends_max[i] > a:
                over.append(raw[i])
            i += 1
        for r in over:
            raw_hits[id(r)] = raw_hits.get(id(r), 0) + 1
        final = _words(seg["text"])
        stats["final_words"] += len(final)
        if not over:
            stats["no_raw_speech"] += 1
            stats["words_added"] += len(final)
            if b - a >= 8:
                long_gaps.append((a, b, seg["text"]))
            continue
        if len(over) >= 2:
            stats["merged"] += 1
        original = [w for r in over for w in _words(r["text"])]
        stats["raw_words"] += len(original)
        if str(over[0].get("speaker_id") or "") != str(seg.get("speaker_id") or "") and over[0].get("speaker_id"):
            stats["speaker_changed"] += 1
        if original == final:
            stats["unchanged"] += 1
            continue
        stats["reworded"] += 1
        sm = difflib.SequenceMatcher(None, original, final, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "replace":
                stats["words_replaced"] += max(i2 - i1, j2 - j1)
                if i2 - i1 <= 3 and j2 - j1 <= 3:
                    k = (" ".join(original[i1:i2]), " ".join(final[j1:j2]))
                    subs[k] = subs.get(k, 0) + 1
            elif tag == "insert":
                stats["words_added"] += j2 - j1
                k = " ".join(final[j1:j2])
                if j2 - j1 <= 4:
                    added[k] = added.get(k, 0) + 1
            elif tag == "delete":
                stats["words_removed"] += i2 - i1
                k = " ".join(original[i1:i2])
                if i2 - i1 <= 4:
                    removed[k] = removed.get(k, 0) + 1
    stats["split"] = sum(1 for n in raw_hits.values() if n >= 2)
    def top(d):
        return sorted(d.items(), key=lambda kv: (-kv[1], str(kv[0])))[:max_items]
    out = ["# Edit report", "",
           "Compares the segments you marked reviewed with the original Whisper output of this same transcript. "
           "Nothing here changes your transcript.", ""]
    n = max(1, stats["reviewed"])
    out += ["## Summary", "",
            f"- Reviewed segments: {stats['reviewed']:,}",
            f"- Unchanged text: {stats['unchanged']:,} ({stats['unchanged'] * 100 // n}%)",
            f"- Reworded: {stats['reworded']:,}",
            f"- Where Whisper had heard nothing at all: {stats['no_raw_speech']:,} segments (text you typed in)",
            f"- Made by joining several Whisper segments (merges): {stats['merged']:,}",
            f"- Whisper segments cut into several of yours (splits): {stats['split']:,}",
            f"- Speaker changed from the original label: {stats['speaker_changed']:,}",
            f"- Words: Whisper {stats['raw_words']:,} vs yours {stats['final_words']:,}; replaced {stats['words_replaced']:,}, added {stats['words_added']:,}, removed {stats['words_removed']:,}",
            ""]
    out += ["## Most common word replacements (Whisper heard → you wrote)", ""]
    out += [f"- {c}× `{h}` → `{w}`" for (h, w), c in top(subs)] or ["- none"]
    out += ["", "## Words you most often added (Whisper missed them)", ""]
    out += [f"- {c}× `{w}`" for w, c in top(added)] or ["- none"]
    out += ["", "## Words you most often removed (Whisper invented or repeated them)", ""]
    out += [f"- {c}× `{w}`" for w, c in top(removed)] or ["- none"]
    out += ["", "## Stretches of 8+ seconds where Whisper heard nothing", ""]
    out += [f"- {_hms(a)}–{_hms(b)}: {t[:120]}" for a, b, t in long_gaps[:max_items]] or ["- none"]
    return "\n".join(out) + "\n", stats


def _hms(seconds):
    s = int(max(0, seconds))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"
