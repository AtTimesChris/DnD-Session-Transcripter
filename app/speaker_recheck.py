"""Speaker re-check: re-label a transcript from a fresh diarization run while keeping
every segment the user has reviewed exactly as they set it.  Qt-free and unit-tested.

How it learns from manual work
------------------------------
A diarization run produces anonymous clusters (e.g. SPEAKER_03).  Each segment the
user marked "Reviewed" is evidence: whichever cluster it overlaps most "votes" for the
speaker the user assigned.  A cluster with enough consistent votes is renamed to that
speaker, so one person split across several clusters is merged automatically, and every
*unreviewed* segment in those clusters follows.  Clusters with no evidence can still be
matched by voice similarity (cluster embeddings) to a resolved cluster in the same
recording or to voiceprints saved from earlier sessions.  Reviewed segments are never
modified, so the process can be repeated: the more you review, the less is guessed.
"""
import copy
import math
import re

from .alignment import TurnIndex, assign_speakers

DEFAULTS = {
    "min_evidence_seconds": 3.0,   # reviewed speech needed before a cluster is trusted
    "min_purity": 0.75,            # share of that evidence that must agree on one speaker
    "segment_share": 0.60,         # a reviewed clip must be mostly one cluster to count
    "match_threshold": 0.60,       # cosine similarity needed to match by voice
    "match_margin": 0.10,          # best match must beat the runner-up by this much
    "target_seconds": 60.0,        # suggested reviewed speech per person
    "voiceprint_min_seconds": 10.0,
}


def cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _meaningful(entry):
    """A speaker_map entry the user actually filled in (not an empty auto placeholder)."""
    if not isinstance(entry, dict):
        return False
    return bool((entry.get("role") or "Unknown") != "Unknown" or entry.get("player") or entry.get("character"))


def _next_free_id(reserved):
    n = 0
    while f"SPEAKER_{n:02d}" in reserved:
        n += 1
    return f"SPEAKER_{n:02d}"


def recheck_segments(segments, turns, speaker_map=None, centroids=None, stored_prints=None, settings=None):
    """Return (new_segments, speaker_map_updates, report).

    segments      transcript segments; those with speaker_confirmed and a speaker_id are locked
    turns         diarization turns [{start, end, speaker_id(cluster)}]
    speaker_map   the run's existing speaker mapping (role/player/...)
    centroids     {cluster: embedding vector} if the pipeline supplied them
    stored_prints [{name, role, vector}] saved from earlier sessions of this campaign
    """
    cfg = {**DEFAULTS, **(settings or {})}
    speaker_map = speaker_map or {}
    centroids = centroids or {}
    stored_prints = stored_prints or []
    segments = copy.deepcopy(segments)
    index = TurnIndex(turns)

    cluster_seconds = {}
    for t in turns:
        cluster_seconds[t["speaker_id"]] = cluster_seconds.get(t["speaker_id"], 0.0) + max(0.0, t["end"] - t["start"])

    def locked(seg):
        return bool(seg.get("speaker_confirmed") and seg.get("speaker_id"))

    # 1. Reviewed segments vote for the cluster they sit in.
    votes, confirmed_seconds, ambiguous = {}, {}, 0
    for seg in segments:
        if not locked(seg):
            continue
        a, b = float(seg.get("start", 0)), float(seg.get("end", 0))
        if b <= a:
            continue
        sid = seg["speaker_id"]
        confirmed_seconds[sid] = confirmed_seconds.get(sid, 0.0) + (b - a)
        per = index.overlaps(a, b)
        covered = sum(per.values())
        if covered <= 0:
            continue
        best = max(per, key=per.get)
        if per[best] / covered < cfg["segment_share"]:
            ambiguous += 1  # crosstalk / boundary clip: not trustworthy evidence
            continue
        votes.setdefault(best, {})
        votes[best][sid] = votes[best].get(sid, 0.0) + per[best]

    # 2. Clusters with enough consistent votes take the user's speaker.
    assignment, mixed = {}, []
    for cluster, tally in votes.items():
        total = sum(tally.values())
        winner = max(tally, key=tally.get)
        purity = tally[winner] / total
        if total >= cfg["min_evidence_seconds"] and purity >= cfg["min_purity"]:
            assignment[cluster] = {"speaker_id": winner, "method": "confirmed", "evidence": tally[winner], "purity": purity}
        elif total >= cfg["min_evidence_seconds"]:
            mixed.append(cluster)  # pyannote put two people in one cluster: cannot be fixed by relabelling

    # 3. Remaining clusters: try to match by voice.
    player_to_id = {}
    for sid, entry in speaker_map.items():
        if not str(sid).startswith("_") and isinstance(entry, dict) and (entry.get("player") or "").strip():
            player_to_id.setdefault(entry["player"].strip().casefold(), sid)
    references = []  # (key, vector)
    for cluster, info in assignment.items():
        if centroids.get(cluster):
            references.append((("id", info["speaker_id"]), centroids[cluster]))
    for p in stored_prints:
        if p.get("vector") and p.get("name"):
            references.append((("print", p["name"], p.get("role", "Player")), p["vector"]))

    updates = {}
    reserved = set(assignment_info["speaker_id"] for assignment_info in assignment.values())
    reserved.update(seg["speaker_id"] for seg in segments if locked(seg))
    reserved.update(str(k) for k, v in speaker_map.items() if not str(k).startswith("_") and _meaningful(v))

    unresolved = sorted((c for c in cluster_seconds if c not in assignment), key=lambda c: -cluster_seconds[c])
    for cluster in unresolved:
        if cluster in mixed or not centroids.get(cluster) or not references:
            continue
        scores = {}
        for key, vec in references:
            scores[key] = max(scores.get(key, -1.0), cosine(centroids[cluster], vec))
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        best_key, best_score = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else -1.0
        if best_score < cfg["match_threshold"] or best_score - runner_up < cfg["match_margin"]:
            continue
        if best_key[0] == "id":
            assignment[cluster] = {"speaker_id": best_key[1], "method": "similar", "evidence": 0.0, "purity": best_score}
        else:
            name, role = best_key[1], best_key[2]
            sid = player_to_id.get(name.casefold())
            if sid is None:
                sid = _next_free_id(reserved)
                reserved.add(sid)
                player_to_id[name.casefold()] = sid
                updates[sid] = {"role": role, "player": name, "player_nicknames": "", "character": "", "include_in_notes": role in {"Player", "DM"}}
            assignment[cluster] = {"speaker_id": sid, "method": "voiceprint", "evidence": 0.0, "purity": best_score}

    # 4. Whatever is left becomes a new, unmapped speaker (keeping pyannote's label when free).
    for cluster in unresolved:
        if cluster in assignment:
            continue
        sid = cluster if cluster not in reserved else _next_free_id(reserved)
        reserved.add(sid)
        assignment[cluster] = {"speaker_id": sid, "method": "mixed" if cluster in mixed else "new", "evidence": 0.0, "purity": 0.0}

    # 5. Re-label unreviewed segments only.
    relabelled = [{**t, "speaker_id": assignment[t["speaker_id"]]["speaker_id"]} for t in turns]
    todo = [s for s in segments if not locked(s)]
    before = {id(s): s.get("speaker_id", "") for s in todo}
    assign_speakers(todo, relabelled)
    changed = sum(1 for s in todo if s.get("speaker_id", "") != before[id(s)])

    report = {
        "clusters": [{"cluster": c, "speaker_id": a["speaker_id"], "method": a["method"], "seconds": cluster_seconds.get(c, 0.0),
                      "evidence_seconds": a["evidence"], "score": a["purity"]}
                     for c, a in sorted(assignment.items(), key=lambda kv: -cluster_seconds.get(kv[0], 0.0))],
        "confirmed_seconds": confirmed_seconds,
        "ambiguous_confirmed": ambiguous,
        "changed": changed,
        "unreviewed_segments": len(todo),
        "reviewed_segments": len(segments) - len(todo),
        # v2.31: pyannote cluster -> final speaker id, so the turns can be saved in the transcript's own labels.
        "turn_labels": {c: a["speaker_id"] for c, a in assignment.items()},
    }
    return segments, updates, report


def voiceprints_to_save(report, centroids, speaker_map, settings=None):
    """Only clusters resolved from the user's own review are trusted enough to remember."""
    cfg = {**DEFAULTS, **(settings or {})}
    result = []
    for c in report.get("clusters", []):
        entry = speaker_map.get(c["speaker_id"], {})
        name = (entry.get("player") or "").strip() if isinstance(entry, dict) else ""
        if (c["method"] == "confirmed" and name and c["evidence_seconds"] >= cfg["voiceprint_min_seconds"]
                and centroids.get(c["cluster"])):
            result.append({"name": name, "role": entry.get("role", "Player") or "Player",
                           "vector": centroids[c["cluster"]], "seconds": c["evidence_seconds"]})
    return result


def _mmss(seconds):
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def summarise_report(report, speaker_map=None, settings=None):
    """Plain-language result for the user, including what to review next."""
    cfg = {**DEFAULTS, **(settings or {})}
    speaker_map = speaker_map or {}

    def label(sid):
        entry = speaker_map.get(sid, {}) if isinstance(speaker_map.get(sid, {}), dict) else {}
        return f"{sid} ({entry['player']})" if entry.get("player") else sid

    lines = [f"Reviewed segments kept exactly as you set them: {report['reviewed_segments']}",
             f"Unreviewed segments re-labelled: {report['unreviewed_segments']} (speaker changed on {report['changed']})"]
    if report["ambiguous_confirmed"]:
        lines.append(f"{report['ambiguous_confirmed']} reviewed clip(s) were ignored as evidence because they overlap several voices "
                     "(they are still kept as you set them).")
    lines.append("")
    lines.append("Reviewed speech per speaker (aim for about 1 minute each, in short clips from different parts of the session):")
    speakers = sorted(set(report["confirmed_seconds"]) | {c["speaker_id"] for c in report["clusters"] if c["method"] in {"confirmed", "similar", "voiceprint"}})
    for sid in speakers:
        secs = report["confirmed_seconds"].get(sid, 0.0)
        mark = "enough" if secs >= cfg["target_seconds"] else "needs more"
        lines.append(f"  {label(sid)}: {_mmss(secs)} · {mark}")
    needs = [c for c in report["clusters"] if c["method"] in {"new", "mixed"} and c["seconds"] >= 20]
    merged = [c for c in report["clusters"] if c["method"] in {"similar", "voiceprint"}]
    if merged:
        lines.append("")
        lines.append("Matched by voice similarity (check a few clips):")
        for c in merged:
            how = "an earlier session's voiceprint" if c["method"] == "voiceprint" else "a speaker you reviewed"
            lines.append(f"  {c['cluster']} ({_mmss(c['seconds'])}) -> {label(c['speaker_id'])} · similar to {how} ({c['score']:.2f})")
    if needs:
        lines.append("")
        lines.append("Still unidentified - review 2-3 clips from each, then re-check:")
        for c in needs:
            why = "contains two people" if c["method"] == "mixed" else "no reviewed clips yet"
            lines.append(f"  {c['speaker_id']} · {_mmss(c['seconds'])} of speech · {why}")
    return "\n".join(lines)


def prune_speaker_map(speaker_map, used_ids):
    """Drop empty auto-created speakers that no segment uses any more."""
    return {k: v for k, v in speaker_map.items()
            if str(k).startswith("_") or k in used_ids or _meaningful(v)}


if __name__ == "__main__":  # tiny self-check, see tests/
    assert re.match(r"SPEAKER_\d\d", _next_free_id(set()))
