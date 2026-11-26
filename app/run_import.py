"""Carry the user's reviewed speaker work from one transcript version to another version
of the SAME recording (e.g. after re-transcribing a quiet file with levelled audio).

Qt-free and unit-tested. Matching is by timestamp overlap only: the audio timeline is
identical, but the new transcript's segment boundaries and words will differ.

Rules
-----
* Only segments the user ticked Reviewed (with a speaker) in the source are used.
* A target segment mostly (>= `min_cover`) inside reviewed source speech, and mostly one
  speaker (>= `min_clean`), receives that speaker. It is also marked Reviewed when the
  cover is high (>= `lock_cover`); partial covers get the speaker as a soft guess that a
  speaker re-check may still correct.
* Target segments the user already reviewed are never touched.
* By default text is not copied (the new transcript may be better); only speaker work moves.
* With copy_text=True every reviewed source clip is carried over whole: its corrected text,
  timestamps, words, speaker and Reviewed tick replace the new transcript's segments that
  sit inside it. New segments that straddle a clip edge are kept. Clips that overlap
  something the user already reviewed in the target are skipped.
* Speaker ids are mapped by player name where possible so ids never collide.
"""
import copy
import re
import uuid

from .alignment import TurnIndex

DEFAULTS = {"min_cover": 0.50, "min_clean": 0.80, "lock_cover": 0.80}


def clip_key(seg):
    return str(seg.get("_edit_id") or f"{float(seg.get('start', 0)):.3f}-{float(seg.get('end', 0)):.3f}")


def _norm(text):
    return re.sub(r"[\W_]+", " ", str(text or "").casefold()).strip()


def edited_clip_keys(source_segments, raw_segments):
    """Keys of reviewed source clips whose text differs from the original Whisper text for that
    stretch (or that have no original counterpart, e.g. added by hand). A clip you only split or
    re-timed may be listed too; that is harmless because its text is the same as the original."""
    raw = sorted((r for r in raw_segments or [] if float(r.get("end", 0)) > float(r.get("start", 0))),
                 key=lambda r: float(r["start"]))
    edited = set()
    for clip in source_segments:
        if not _locked(clip):
            continue
        a, b = float(clip.get("start", 0)), float(clip.get("end", 0))
        inside = [r.get("text", "") for r in raw if a <= (float(r["start"]) + float(r["end"])) / 2 <= b]
        if not inside or _norm(" ".join(inside)) != _norm(clip.get("text", "")):
            edited.add(clip_key(clip))
    return edited


def describe_clips(source_segments, target_segments, edited_keys):
    """Rows for the 'choose clip by clip' review: your reviewed text next to what the new transcript has there."""
    rows = []
    for clip in sorted((c for c in source_segments if _locked(c)), key=lambda c: float(c.get("start", 0))):
        a, b = float(clip.get("start", 0)), float(clip.get("end", 0))
        if b <= a:
            continue
        new_text = " ".join(str(t.get("text", "")).strip() for t in target_segments
                            if not _locked(t) and float(t.get("end", 0)) > float(t.get("start", 0))
                            and min(b, float(t["end"])) - max(a, float(t["start"])) >= 0.5 * (float(t["end"]) - float(t["start"])))
        rows.append({"key": clip_key(clip), "start": a, "end": b, "speaker_id": clip["speaker_id"],
                     "text": str(clip.get("text", "")), "new_text": new_text, "edited": clip_key(clip) in edited_keys})
    return rows


def possible_duplicates(segments, min_overlap_s=0.3, min_overlap_share=0.25):
    """Unreviewed segments that overlap a reviewed one (typically left behind at the edge of an
    imported clip). Returns (flagged, removable) as sets of list indices.

    flagged    overlaps a reviewed segment by >= min_overlap_s or >= min_overlap_share of its own length
    removable  flagged AND every word of it already appears in the overlapping reviewed text
    """
    locked = [(i, s) for i, s in enumerate(segments) if _locked(s) and float(s.get("end", 0)) > float(s.get("start", 0))]
    index = TurnIndex([{"start": float(s["start"]), "end": float(s["end"]), "speaker_id": str(i)} for i, s in locked])
    texts = {str(i): set(_norm(s.get("text", "")).split()) for i, s in locked}
    flagged, removable = set(), set()
    for i, seg in enumerate(segments):
        if _locked(seg):
            continue
        a, b = float(seg.get("start", 0)), float(seg.get("end", 0))
        if b <= a:
            continue
        per = index.overlaps(a, b)
        if not per or not (sum(per.values()) >= min_overlap_s or sum(per.values()) / (b - a) >= min_overlap_share):
            continue
        flagged.add(i)
        words = set(_norm(seg.get("text", "")).split())
        if words and words <= set().union(*(texts[k] for k in per)):
            removable.add(i)
    return flagged, removable


def _locked(seg):
    return bool(seg.get("speaker_confirmed") and seg.get("speaker_id"))


def _meaningful(entry):
    return isinstance(entry, dict) and bool((entry.get("role") or "Unknown") != "Unknown"
                                            or entry.get("player") or entry.get("character"))


def _real_ids(mapping):
    return {str(k) for k in mapping if not str(k).startswith("_")}


def import_reviewed(source_segments, source_map, target_segments, target_map, settings=None, copy_text=False, text_clips=None):
    """Return (new_target_segments, new_target_map, report). Inputs are not mutated.

    Which reviewed clips are carried over whole (text, timestamps, words): all of them when
    copy_text is True, otherwise only those whose clip_key is in text_clips. Every other
    reviewed clip still gives its speaker to the new transcript's segments.
    """
    cfg = {**DEFAULTS, **(settings or {})}
    source_map = source_map or {}
    target_map = copy.deepcopy(target_map or {})
    target = copy.deepcopy(target_segments)

    src = [s for s in source_segments if _locked(s) and float(s.get("end", 0)) > float(s.get("start", 0))]
    turns = [{"start": float(s["start"]), "end": float(s["end"]), "speaker_id": s["speaker_id"]} for s in src]
    samples = [{"start": float(s["start"]), "end": float(s["end"]), "speaker_id": s["speaker_id"]}
               for s in src if s.get("confirmed_voice_sample")]
    index, sample_index = TurnIndex(turns), TurnIndex(samples)

    # --- map source speaker ids onto ids that are safe in the target
    used_in_target = {s.get("speaker_id") for s in target if s.get("speaker_id")}
    taken = used_in_target | {k for k in _real_ids(target_map) if _meaningful(target_map[k])}
    by_player = {}
    for sid in _real_ids(target_map):
        entry = target_map[sid]
        if isinstance(entry, dict) and (entry.get("player") or "").strip():
            by_player.setdefault(entry["player"].strip().casefold(), sid)

    def free_id():
        n = 0
        while f"SPEAKER_{n:02d}" in taken:
            n += 1
        sid = f"SPEAKER_{n:02d}"
        taken.add(sid)
        return sid

    id_map = {}

    def target_id(src_id):
        if src_id in id_map:
            return id_map[src_id]
        entry = source_map.get(src_id) if isinstance(source_map.get(src_id), dict) else {}
        player = (entry.get("player") or "").strip().casefold()
        if player and player in by_player:
            new = by_player[player]
        elif src_id not in taken:
            new = src_id
            taken.add(new)
        else:
            new = free_id()
        if player:
            by_player.setdefault(player, new)
        id_map[src_id] = new
        if new not in target_map and entry:
            target_map[new] = dict(entry)
        return new

    # --- optionally carry the reviewed clips over whole (text + timestamps + words)
    copied = removed = skipped_clips = 0
    original_locked = sum(1 for s in target if _locked(s))
    chosen = {clip_key(c) for c in src} if copy_text else set(text_clips or ())
    text_mode = bool(chosen)
    if text_mode:
        locked_target = TurnIndex([{"start": float(s.get("start", 0)), "end": float(s.get("end", 0)), "speaker_id": "x"}
                                   for s in target if _locked(s)])
        doomed, clips = set(), []
        for clip in sorted(src, key=lambda s: (float(s["start"]), float(s["end"]))):
            if clip_key(clip) not in chosen:
                continue
            a, b = float(clip["start"]), float(clip["end"])
            if locked_target.overlaps(a, b):
                skipped_clips += 1  # the user already reviewed this stretch in the target: theirs wins
                continue
            for i, seg in enumerate(target):
                sa, sb = float(seg.get("start", 0)), float(seg.get("end", 0))
                if sb > sa and not _locked(seg) and min(b, sb) - max(a, sa) >= 0.5 * (sb - sa):
                    doomed.add(i)
            new = copy.deepcopy(clip)
            new["_edit_id"] = str(uuid.uuid4())
            new["speaker_id"] = target_id(clip["speaker_id"])
            new["speaker_confirmed"] = True
            for w in new.get("words", []):
                w["speaker_id"] = new["speaker_id"]
            clips.append(new)
        removed = len(doomed)
        target = [s for i, s in enumerate(target) if i not in doomed]
        target.extend(clips)
        target.sort(key=lambda s: (float(s.get("start", 0)), float(s.get("end", 0))))
        copied = len(clips)

    # Nothing is ever removed automatically: overlapping leftovers are only counted so the user can
    # review them in the editor (Show -> Possible duplicates) and decide.
    duplicates_flagged = len(possible_duplicates(target)[0]) if text_mode else 0

    # --- label target segments
    assigned = locked_new = soft = conflicts = 0
    already_reviewed = original_locked
    for seg in target:
        if _locked(seg):
            continue
        a, b = float(seg.get("start", 0)), float(seg.get("end", 0))
        dur = b - a
        if dur <= 0:
            continue
        per = index.overlaps(a, b)
        covered = sum(per.values())
        if covered / dur < cfg["min_cover"]:
            continue
        best = max(per, key=per.get)
        if per[best] / covered < cfg["min_clean"]:
            conflicts += 1  # crosstalk across two reviewed speakers: leave for a human
            continue
        new_id = target_id(best)
        seg["speaker_id"] = new_id
        for w in seg.get("words", []):
            w["speaker_id"] = new_id
        assigned += 1
        if per[best] / dur >= cfg["lock_cover"]:
            seg["speaker_confirmed"] = True
            locked_new += 1
            if sum(sample_index.overlaps(a, b).values()) / dur >= cfg["lock_cover"]:
                seg["confirmed_voice_sample"] = True
        else:
            soft += 1

    # --- source reviewed clips the new transcript has nothing for (transcription gaps)
    t_index = TurnIndex([{"start": float(s.get("start", 0)), "end": float(s.get("end", 0)), "speaker_id": "x"} for s in target])
    gaps = [s for s in src if not t_index.overlaps(float(s["start"]), float(s["end"]))]

    report = {"source_reviewed": len(src), "assigned": assigned, "locked": locked_new, "soft": soft,
              "conflicts": conflicts, "target_already_reviewed": already_reviewed,
              "text_copied": copied, "text_replaced_segments": removed, "clips_skipped_already_reviewed": skipped_clips,
              "copy_text": text_mode, "duplicates_flagged": duplicates_flagged, "text_kept_from_new": len(src) - copied - skipped_clips if text_mode else len(src),
              "source_clips_without_new_speech": len(gaps),
              "gap_seconds": sum(float(s["end"]) - float(s["start"]) for s in gaps),
              "speaker_ids": dict(id_map)}
    return target, target_map, report


def summarise_import(report, target_map=None):
    target_map = target_map or {}

    def label(sid):
        e = target_map.get(sid)
        return f"{sid} ({e['player']})" if isinstance(e, dict) and e.get("player") else sid

    lines = [f"Reviewed clips found in the source version: {report['source_reviewed']}"]
    if report.get("copy_text"):
        lines.append(f"Carried over with your corrected text and timestamps: {report['text_copied']} clip(s), "
                     f"replacing {report['text_replaced_segments']} new segment(s).")
        lines.append(f"Reviewed clips that keep the new transcript's text (speaker only): {report['text_kept_from_new']}")
        if report.get("duplicates_flagged"):
            lines.append(f"{report['duplicates_flagged']} unreviewed segment(s) overlap text you reviewed. Nothing was removed: open the editor and choose Show \u2192 Possible duplicates to check them yourself.")
        if report.get("clips_skipped_already_reviewed"):
            lines.append(f"{report['clips_skipped_already_reviewed']} clip(s) skipped because you already reviewed that stretch in this version.")
    lines += [
             f"Segments labelled in this version: {report['assigned']} "
              f"({report['locked']} marked Reviewed, {report['soft']} left unreviewed as a guess)"]
    if report["conflicts"]:
        lines.append(f"{report['conflicts']} segment(s) overlap two reviewed speakers and were left alone.")
    if report["target_already_reviewed"]:
        lines.append(f"{report['target_already_reviewed']} segment(s) you had already reviewed here were not touched.")
    if report["source_clips_without_new_speech"]:
        m, s = divmod(int(round(report["gap_seconds"])), 60)
        lines.append(f"{report['source_clips_without_new_speech']} reviewed clip(s) ({m}:{s:02d}) have no speech in this version.")
    if report["speaker_ids"]:
        lines.append("")
        lines.append("Speaker mapping:")
        for src_id, new_id in report["speaker_ids"].items():
            lines.append(f"  {src_id} -> {label(new_id)}")
    lines.append("")
    lines.append("Run Identify / re-check speakers next to label the rest." if report.get("copy_text")
                 else "Text was not copied. Run Identify / re-check speakers next to label the rest.")
    return "\n".join(lines)
