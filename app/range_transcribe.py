"""Re-transcribe one time range of a recording with Whisper (v2.31).

Uses the same prepared 16 kHz WAV and the same main options as a full transcription, but only
on the chosen stretch (plus padding), and returns segments in recording time. Nothing is saved
here: the editor shows the result in the merge window and the user picks what to use.
"""
import json
import threading
import time
import wave
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .media import normalise_audio
from .runtime import register_nvidia_dll_dirs
from .transcription import choose_window_prompt

SAMPLE_RATE = 16000


def read_clip(wav_path, start, end):
    """Float32 samples of [start, end] seconds from a mono 16-bit 16 kHz WAV."""
    import numpy as np
    with wave.open(str(wav_path), "rb") as wf:
        if wf.getsampwidth() != 2 or wf.getframerate() != SAMPLE_RATE or wf.getnchannels() != 1:
            raise RuntimeError("The prepared audio is not mono 16-bit 16 kHz.")
        total = wf.getnframes()
        first = max(0, min(total, int(start * SAMPLE_RATE)))
        last = max(first, min(total, int(end * SAMPLE_RATE)))
        wf.setpos(first)
        raw = wf.readframes(last - first)
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0, first / SAMPLE_RATE


def whisper_options(vad_threshold=0.4, beam_size=5, style_hint=True, supports_hotwords=True):
    """The options a full transcription uses (v2.29.1), with the retry's own speech threshold and beam size."""
    options = {"beam_size": int(beam_size), "vad_filter": True, "word_timestamps": True,
               "condition_on_previous_text": False, "hallucination_silence_threshold": 2.0}
    if vad_threshold and float(vad_threshold) > 0:
        options["vad_parameters"] = {"threshold": float(vad_threshold)}
    if supports_hotwords:
        prompt, _kind = choose_window_prompt("", style_hint)
        if prompt:
            options["hotwords"] = prompt
    return options


# v2.45: Alternatives: the same stretch run three ways, to see whether other settings hear it better
ALTERNATIVES = [
    {"label": "A · careful search", "about": "beam size 10 (Whisper weighs more possible wordings before choosing)",
     "beam_size": 10, "options": {"temperature": 0.0}},
    {"label": "B · with your names", "about": "beam size 5, given the campaign's names as a prompt (no punctuation hint)",
     "beam_size": 5, "options": {"temperature": 0.0}, "names_prompt": True, "style_hint": False},
    {"label": "C · looser", "about": "beam size 5, a little randomness (temperature 0.4) and a lower speech threshold (0.25), "
     "for quiet or overlapping speech", "beam_size": 5, "options": {"temperature": 0.4}, "vad_threshold": 0.25},
    # v2.46: people talking over each other: Whisper often writes only the loudest voice of a long stretch, but hears
    # the quieter one in a short piece (seen in testing)
    {"label": "D · short pieces", "about": "3-second pieces, each starting 1.5 s after the last, no speech filter: catches "
     "quiet words and people talking over each other. The pieces overlap, so copy the words you want into a line.",
     "beam_size": 5, "options": {"temperature": 0.0}, "pieces": (3.0, 1.5), "copy_only": True},
]


def piece_windows(length, size=3.0, hop=1.5):
    """(start, end) seconds of the overlapping pieces of a clip `length` seconds long."""
    out, a = [], 0.0
    while a < length - 0.3:
        out.append((a, min(length, a + size)))
        if a + size >= length:
            break
        a += hop
    return out


def join_pieces(pieces):
    """Drop a piece whose words are the same as the piece before (overlaps often hear the same words)."""
    out, last = [], None
    for p in pieces:
        key = normalised(p.get("text", ""))
        if key and key != last:
            out.append(p)
        last = key or last
    return out


def normalised(text):
    """Text for telling versions apart: lower case, letters and digits only."""
    import re
    return " ".join(re.findall(r"[a-z0-9']+", (text or "").casefold()))


def distinct_versions(versions):
    """versions: [{'label', 'segments', ...}] -> the same list where a version whose words match an earlier one gets
    'same_as' = that version's label (shown once, not three times)."""
    seen = {}
    for v in versions:
        if v.get("copy_only"):
            continue   # v2.46: short pieces overlap: never "the same words as" a full version
        key = normalised(" ".join(s.get("text", "") for s in v.get("segments") or []))
        if key in seen:
            v["same_as"] = seen[key]
        else:
            seen[key] = v["label"]
    return versions


def shift_segments(segments, offset, words=True):
    """Move clip-relative Whisper segments to recording time."""
    out = []
    for seg in segments:
        item = {"start": round(seg["start"] + offset, 3), "end": round(seg["end"] + offset, 3),
                "text": (seg.get("text") or "").strip(), "words": []}
        if words:
            item["words"] = [{**w, "start": round(w["start"] + offset, 3), "end": round(w["end"] + offset, 3)}
                             for w in seg.get("words") or []]
        out.append(item)
    return out


class RangeTranscriptionWorker(QObject):
    progress = Signal(str)
    finished = Signal(str)   # JSON: {"segments": [...], "settings": {...}}
    failed = Signal(str)

    def __init__(self, recording_id, audio_path, output_root, start, end, model_size="large-v3",
                 device="cuda", compute_type="int8_float16", vad_threshold=0.4, beam_size=5, style_hint=True, clips=None,
                 variants=None, names_prompt=""):
        """clips (v2.31.3): several (start, end) stretches to run with one model load; otherwise start/end.
        variants (v2.45): run the first clip once per variant (ALTERNATIVES) instead; names_prompt is the
        campaign's names for a variant that asks for them."""
        self.variants, self.names_prompt = variants, names_prompt or ""
        super().__init__()
        self.recording_id, self.audio_path, self.output_root = recording_id, audio_path, output_root
        self.start, self.end = float(start), float(end)
        self.clips = [(float(a), float(b)) for a, b in (clips or [(start, end)])]
        self.model_size, self.device, self.compute_type = model_size, device, compute_type
        self.vad_threshold, self.beam_size, self.style_hint = float(vad_threshold or 0), int(beam_size), bool(style_hint)
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def _run_variants(self, model, clip, supports, started):
        samples, offset, a, b = clip
        versions = []
        for n, var in enumerate(self.variants, 1):
            if self._stop.is_set():
                raise RuntimeError("Stopped.")   # v2.50: between versions too (it ran on to the end before)
            label = f"Version {n} of {len(self.variants)} ({var['label']})"
            self.progress.emit(label + "…")
            if var.get("pieces"):
                versions.append(self._run_pieces(model, samples, offset, var, label))
                continue
            options = whisper_options(var.get("vad_threshold", self.vad_threshold), var.get("beam_size", self.beam_size),
                                      var.get("style_hint", self.style_hint), supports)
            options.update(var.get("options") or {})
            if var.get("names_prompt") and self.names_prompt:
                options["initial_prompt"] = self.names_prompt[-1500:]
            segments, _info = model.transcribe(samples, **options)
            raw = []
            for seg in segments:
                if self._stop.is_set():
                    raise RuntimeError("Stopped.")
                raw.append({"start": seg.start, "end": seg.end, "text": seg.text,
                            "words": [{"start": w.start, "end": w.end, "word": w.word, "probability": w.probability}
                                      for w in (seg.words or [])]})
                self.progress.emit(f"{label}… {self._clock(offset + seg.end)}")
            versions.append({"label": var["label"], "about": var.get("about", ""), "segments": shift_segments(raw, offset),
                             "settings": {"model_name": self.model_size, "beam_size": options["beam_size"],
                                          "temperature": options.get("temperature", 0.0),
                                          "vad_threshold": (options.get("vad_parameters") or {}).get("threshold", 0.5),
                                          "names_prompt": "initial_prompt" in options, "style_hint_used": "hotwords" in options}})
        settings = {"model_name": self.model_size, "device": self.device, "compute_type": self.compute_type,
                    "range_start": self.start, "range_end": self.end, "clip_start": a, "clip_end": b,
                    "seconds": round(time.monotonic() - started, 1)}
        self.finished.emit(json.dumps({"versions": distinct_versions(versions), "settings": settings}, ensure_ascii=False))

    def _run_pieces(self, model, samples, offset, var, label):
        """v2.46: the clip in short overlapping pieces, speech filter off (see ALTERNATIVES D)."""
        size, hop = var["pieces"]
        pieces = []
        windows = piece_windows(len(samples) / SAMPLE_RATE, size, hop)
        for k, (a, b) in enumerate(windows, 1):
            if self._stop.is_set():
                raise RuntimeError("Stopped.")
            self.progress.emit(f"{label}… piece {k} of {len(windows)}")
            chunk = samples[int(a * SAMPLE_RATE):int(b * SAMPLE_RATE)]
            segs, _info = model.transcribe(chunk, beam_size=var.get("beam_size", 5), vad_filter=False, word_timestamps=False,
                                           condition_on_previous_text=False, **(var.get("options") or {}))
            text = " ".join(s.text.strip() for s in segs).strip()
            if text:
                pieces.append({"start": round(offset + a, 3), "end": round(offset + b, 3), "text": text, "words": []})
        return {"label": var["label"], "about": var.get("about", ""), "segments": join_pieces(pieces), "copy_only": True,
                "settings": {"model_name": self.model_size, "beam_size": var.get("beam_size", 5), "temperature": 0.0,
                             "vad_threshold": 0, "names_prompt": False, "style_hint_used": False}}

    def run(self):
        started = time.monotonic()
        model = None
        try:
            register_nvidia_dll_dirs()
            if self.device == "cuda":
                # Load the CUDA libraries the same way a full transcription does (cublas/cudnn).
                from .runtime import cuda_runtime_status
                missing = [name for name, ok in cuda_runtime_status().items() if not ok]
                if missing:
                    raise RuntimeError("Missing or unloadable CUDA libraries: " + ", ".join(missing)
                                       + ". Run INSTALL.bat, or choose CPU in Audio settings.")
            from faster_whisper import WhisperModel
            import inspect
            wav = str(Path(self.output_root) / "processed_audio" / f"{self.recording_id}.wav")
            wav = normalise_audio(self.audio_path, wav, self.progress.emit)
            clips = []
            for a, b in self.clips:
                samples, offset = read_clip(wav, a, b)
                if len(samples) >= SAMPLE_RATE // 2:
                    clips.append((samples, offset, a, b))
            if not clips:
                raise RuntimeError("The chosen range is shorter than half a second.")
            self.progress.emit(f"Loading Whisper {self.model_size} ({self.device}, {self.compute_type})…")
            model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
            if self._stop.is_set():
                raise RuntimeError("Stopped.")
            supports = "hotwords" in inspect.signature(model.transcribe).parameters
            if self.variants:
                self._run_variants(model, clips[0], supports, started)
                return
            options = whisper_options(self.vad_threshold, self.beam_size, self.style_hint, supports)
            all_segments, per_clip = [], []
            for n, (samples, offset, a, b) in enumerate(clips, 1):
                label = "Transcribing the range" + (f" {n} of {len(clips)}" if len(clips) > 1 else "")
                self.progress.emit(label + "…")
                segments, info = model.transcribe(samples, **options)
                raw = []
                for seg in segments:
                    if self._stop.is_set():
                        raise RuntimeError("Stopped.")
                    raw.append({"start": seg.start, "end": seg.end, "text": seg.text,
                                "words": [{"start": w.start, "end": w.end, "word": w.word, "probability": w.probability}
                                          for w in (seg.words or [])]})
                    self.progress.emit(f"{label}… {self._clock(offset + seg.end)}")
                shifted = shift_segments(raw, offset)
                per_clip.append({"clip_start": a, "clip_end": b, "segments": shifted})
                all_segments.extend(shifted)
            settings = {"model_name": self.model_size, "device": self.device, "compute_type": self.compute_type,
                        "vad_threshold": self.vad_threshold or 0.5, "beam_size": self.beam_size,
                        "style_hint_used": "hotwords" in options, "range_start": self.start, "range_end": self.end,
                        "seconds": round(time.monotonic() - started, 1)}
            self.finished.emit(json.dumps({"segments": all_segments, "clips": per_clip, "settings": settings}, ensure_ascii=False))
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            try:
                del model
                import gc; gc.collect()
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass

    @staticmethod
    def _clock(seconds):
        s = max(0, int(seconds))
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
