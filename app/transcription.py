import inspect
import json
import time
import wave
import threading
import os
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import QObject, Signal
from .media import normalise_audio
from .runtime import register_nvidia_dll_dirs


# A neutral, punctuated sentence with no campaign names. faster-whisper 1.2.1 puts its
# `hotwords` text in front of EVERY 30-second window (get_prompt, as "previous text").
# Without it, condition_on_previous_text=False lets Whisper pick a style per window, and in
# unpunctuated windows it cuts after almost every phrase (v2.29.1 tests: mid-sentence
# one-word segments 285 -> 0 on a full session). Re-check if faster-whisper is upgraded.
STYLE_HINT = "Okay, so what do you want to do? Yeah, I'll roll for it."


def choose_window_prompt(name_hotwords="", style_hint_on=True):
    """What goes in the per-window prompt slot: (text, kind) with kind 'names', 'style' or ''.
    Name hotwords win when the user has deliberately switched them on; otherwise the style hint."""
    names = (name_hotwords or "").strip()
    if names:
        return names[:600], "names"
    if style_hint_on:
        return STYLE_HINT, "style"
    return "", ""


def _levelling_on():
    try:
        from .media import _level_filter_wanted
        return bool(_level_filter_wanted())
    except Exception:
        return False


class TranscriptionWorker(QObject):
    progress = Signal(str)
    progress_detail = Signal(str, float)
    finished = Signal(str, str, str, str, str, str)
    failed = Signal(str, str)

    def __init__(self, recording_id, audio_path, model_size="small", device="cpu",
                 compute_type="int8", context_prompt="", output_root="", hotwords="", vad_threshold=0.0,
                 style_hint=True):
        super().__init__()
        self.recording_id = recording_id
        self.audio_path = audio_path
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.context_prompt = context_prompt
        self.hotwords = hotwords
        self.style_hint = bool(style_hint)
        self.vad_threshold = float(vad_threshold or 0.0)
        self.output_root = output_root
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._stop_event = threading.Event()
        self.checkpoint_path = None
        self._last_checkpoint = time.monotonic()

    def _write_checkpoint(self, recording_id, output, plain, metadata, state="RUNNING", error=""):
        if not self.checkpoint_path: return
        payload={"recording_id":recording_id,"state":state,"updated_at":datetime.now().astimezone().isoformat(timespec="seconds"),
                 "segments":output,"text":"\n".join(plain),"metadata":metadata,"error":error}
        temp=self.checkpoint_path.with_suffix(self.checkpoint_path.suffix+".tmp")
        temp.write_text(json.dumps(payload,ensure_ascii=False),encoding="utf-8")
        os.replace(temp,self.checkpoint_path)

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    def stop(self):
        self._stop_event.set()
        self._pause_event.set()

    @staticmethod
    def _duration_seconds(path):
        try:
            with wave.open(str(path), "rb") as wf:
                return wf.getnframes() / float(wf.getframerate())
        except Exception:
            return 0.0

    @staticmethod
    def _clock(seconds):
        seconds = max(0, int(seconds))
        hours, rem = divmod(seconds, 3600)
        minutes, secs = divmod(rem, 60)
        if hours:
            return f"{hours}h {minutes:02d}m"
        if minutes:
            return f"{minutes}m {secs:02d}s"
        return f"{secs}s"

    @staticmethod
    def _supports_hotwords(model):
        try:
            return "hotwords" in inspect.signature(model.transcribe).parameters
        except (TypeError, ValueError):
            return False

    def run(self):
        started = time.monotonic()
        try:
            register_nvidia_dll_dirs()
            from faster_whisper import WhisperModel
            wav_path = str(Path(self.output_root) / "processed_audio" / f"{self.recording_id}.wav")
            audio_path = normalise_audio(self.audio_path, wav_path, self.progress.emit)
            audio_prepared_at = time.monotonic()
            audio_prepare_seconds = audio_prepared_at - started
            total_seconds = self._duration_seconds(audio_path)
            self.progress_detail.emit(f"Loading Whisper {self.model_size} ({self.device}, {self.compute_type})…", 0.0)
            model_load_started = time.monotonic()
            model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
            model_loaded_at = time.monotonic()
            model_load_seconds = model_loaded_at - model_load_started
            self.progress_detail.emit("Transcribing audio · calculating estimate…", 0.0)
            options = {
                "beam_size": 5,
                "vad_filter": True,
                "word_timestamps": True,
                "condition_on_previous_text": False,
                # Skip stretches of silence when the model starts looping ("Gleam, Gleam, ...").
                "hallucination_silence_threshold": 2.0,
            }
            if self.context_prompt.strip():
                # Whisper only keeps the last ~223 prompt tokens, and with
                # condition_on_previous_text=False the initial prompt only
                # influences the first 30-second window.
                options["initial_prompt"] = self.context_prompt.strip()[-1500:]
            if self.vad_threshold > 0:
                # Lower than faster-whisper's default (0.5) keeps quiet or distant speech
                # (e.g. the table chatting before the session formally starts) instead of
                # classing it as silence.
                options["vad_parameters"] = {"threshold": self.vad_threshold}
            hotwords_used = False
            style_hint_used = False
            if self._supports_hotwords(model):
                # The per-window prompt slot holds either the user's name hotwords
                # (not recommended: they made Whisper loop and lose windows) or the
                # neutral style hint that keeps Whisper punctuating.
                window_prompt, kind = choose_window_prompt(self.hotwords, self.style_hint)
                if window_prompt:
                    options["hotwords"] = window_prompt
                    hotwords_used = kind == "names"
                    style_hint_used = kind == "style"
            segments, info = model.transcribe(audio_path, **options)
            transcribing_started = time.monotonic()
            output, plain = [], []
            checkpoint_dir=Path(self.output_root)/"Transcripts"/".checkpoints"
            checkpoint_dir.mkdir(parents=True,exist_ok=True)
            checkpoint_stamp=datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            self.checkpoint_path=checkpoint_dir/f"{self.recording_id}_{self.model_size}_{checkpoint_stamp}.checkpoint.json"
            checkpoint_base={"model_name":self.model_size,"language":info.language or "","device":self.device,"compute_type":self.compute_type,
                             "audio_duration_seconds":total_seconds,"started_at":datetime.now().astimezone().isoformat(timespec="seconds")}
            self._write_checkpoint(self.recording_id,output,plain,checkpoint_base,"RUNNING")
            last_emit = 0.0
            stopped = False
            for seg in segments:
                # Whisper yields segment-by-segment. Pause/stop takes effect at the
                # next segment boundary, preserving all segments already received.
                self._pause_event.wait()
                text = (seg.text or "").strip()
                plain.append(text)
                words = []
                if seg.words:
                    words = [{"start": w.start, "end": w.end, "word": w.word,
                              "probability": w.probability} for w in seg.words]
                output.append({"start": seg.start, "end": seg.end, "text": text,
                               "words": words, "speaker_id": ""})
                if len(output)%10==0 or time.monotonic()-self._last_checkpoint>=30:
                    self._write_checkpoint(self.recording_id,output,plain,checkpoint_base,"RUNNING")
                    self._last_checkpoint=time.monotonic()
                if self._stop_event.is_set():
                    stopped = True
                    break
                # ETA should measure inference only, not audio conversion or model loading.
                elapsed = time.monotonic() - transcribing_started
                done = max(0.0, float(seg.end))
                pct = min(99.0, (done / total_seconds * 100.0)) if total_seconds else 0.0
                remaining = (elapsed * (total_seconds - done) / done) if done > 1 and total_seconds > done else 0
                now = time.monotonic()
                if now - last_emit >= 0.15:
                    if total_seconds and done > 1:
                        message = (f"Transcribing · {self._clock(done)} / {self._clock(total_seconds)}"
                                   f" · about {self._clock(remaining)} remaining")
                    else:
                        message = f"Transcribing · {self._clock(done)} processed"
                    self.progress_detail.emit(message, pct)
                    last_emit = now
            payload = json.dumps({"text": "\n".join(plain), "segments": output}, ensure_ascii=False)
            metadata_obj = {"model_name": self.model_size, "language": info.language or "", "device": self.device, "compute_type": self.compute_type,
                                   "condition_on_previous_text": False, "beam_size": 5,
                                   "vad_filter": True, "vad_threshold": self.vad_threshold or 0.5, "word_timestamps": True,
                                   "hallucination_silence_threshold": 2.0, "audio_levelling": _levelling_on(),
                                   "initial_prompt_used": "initial_prompt" in options,
                                   "hotwords_used": hotwords_used,
                                   "style_hint_used": style_hint_used,
                                   "initial_prompt_chars": len(options.get("initial_prompt", "")),
                                   "hotwords_chars": len(options.get("hotwords", "")) if hotwords_used else 0,
                                   "audio_duration_seconds": total_seconds,
                                   "audio_prepare_seconds": audio_prepare_seconds,
                                   "model_load_seconds": model_load_seconds,
                                   "transcription_seconds": time.monotonic() - model_loaded_at,
                                   "elapsed_seconds": time.monotonic() - started,
                                   "stopped_early": stopped,
                                   "transcribed_until_seconds": (output[-1]["end"] if output else 0.0),
                                   "completion_status": "PARTIAL_STOPPED" if stopped else "COMPLETE",
                                   "checkpoint_file": str(self.checkpoint_path) if self.checkpoint_path else ""}
            self._write_checkpoint(self.recording_id,output,plain,metadata_obj,"STOPPED" if stopped else "COMPLETE")
            metadata = json.dumps(metadata_obj, ensure_ascii=False)
            self.progress_detail.emit("Saving partial transcript…" if stopped else "Saving transcript…", 99.0)
            self.finished.emit(self.recording_id, info.language or "", self.model_size,
                               self.device, self.compute_type, payload + "\n__METADATA__\n" + metadata)
        except Exception as exc:
            try:
                self._write_checkpoint(self.recording_id,locals().get("output",[]),locals().get("plain",[]),
                    {"model_name":self.model_size,"device":self.device,"compute_type":self.compute_type},"INTERRUPTED",f"{type(exc).__name__}: {exc}")
            except Exception: pass
            self.failed.emit(self.recording_id, f"{type(exc).__name__}: {exc}")
        finally:
            # Faster-Whisper/CTranslate2 can retain CUDA allocations until the
            # model object is released. Drop references before the worker exits.
            try:
                try: del segments
                except Exception: pass
                try: del model
                except Exception: pass
                import gc; gc.collect()
                import torch
                if torch.cuda.is_available():
                    try: torch.cuda.synchronize()
                    except Exception: pass
                    torch.cuda.empty_cache()
                    try: torch.cuda.ipc_collect()
                    except Exception: pass
            except Exception:
                pass
