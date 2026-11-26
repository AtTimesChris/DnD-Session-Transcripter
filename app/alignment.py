"""Pure (Qt-free) speaker/transcript alignment so it can be unit-tested."""
from bisect import bisect_left, bisect_right


class TurnIndex:
    """Sorted diarization turns with a bisect lookup.

    shortcut: the search window is widened by the longest turn, so one very long
    turn degrades lookups towards a linear scan; fine for session-length audio.
    """

    def __init__(self, turns):
        self.turns = sorted(turns, key=lambda t: (t["start"], t["end"]))
        self.starts = [t["start"] for t in self.turns]
        self.longest = max((t["end"] - t["start"] for t in self.turns), default=0.0)

    def nearby(self, a, b):
        lo = bisect_left(self.starts, a - self.longest)
        hi = bisect_right(self.starts, b)
        return self.turns[lo:hi]

    def overlaps(self, a, b):
        """Seconds of overlap with [a, b], summed per speaker label."""
        totals = {}
        for turn in self.nearby(a, b):
            overlap = max(0.0, min(b, turn["end"]) - max(a, turn["start"]))
            if overlap:
                totals[turn["speaker_id"]] = totals.get(turn["speaker_id"], 0.0) + overlap
        return totals


def assign_speakers(segments, turns):
    """Set segment['speaker_id'] and word['speaker_id'] from diarization turns.

    A segment gets the speaker with the largest total overlap; a word gets the
    speaker whose turn contains the word midpoint (falling back to the segment's
    speaker).
    """
    index = TurnIndex(turns)
    for seg in segments:
        a = float(seg.get("start", 0))
        b = float(seg.get("end", a))
        totals = index.overlaps(a, b)
        seg["speaker_id"] = max(totals, key=totals.get) if totals else ""
        for word in seg.get("words", []):
            mid = (float(word.get("start", a)) + float(word.get("end", b))) / 2
            word["speaker_id"] = next(
                (t["speaker_id"] for t in index.nearby(mid, mid) if t["start"] <= mid <= t["end"]),
                seg["speaker_id"],
            )
    return segments
