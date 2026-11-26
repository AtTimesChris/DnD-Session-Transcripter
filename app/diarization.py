import json
import os
import wave
import warnings
from pathlib import Path
from PySide6.QtCore import QObject, Signal
from .speaker_recheck import recheck_segments, voiceprints_to_save
from .media import normalise_audio
from .runtime import register_nvidia_dll_dirs


class DiarizationWorker(QObject):
    progress = Signal(str)
    finished = Signal(str, str, str)
    failed = Signal(str, str)

    def __init__(self, run_id, recording_id, audio_path, segments_json, token, device="cpu",
                 output_root="", min_speakers=0, num_speakers=0, max_speakers=0,
                 speaker_map_json="{}", stored_prints_json="[]", match_threshold=0.60):
        super().__init__()
        self.run_id = run_id
        self.recording_id = recording_id
        self.audio_path = audio_path
        self.segments_json = segments_json
        self.token = token
        self.device = device
        self.output_root = output_root
        # 0 means "not set". An exact num_speakers overrides min/max.
        self.num_speakers = max(0, int(num_speakers or 0))
        self.min_speakers = max(0, int(min_speakers or 0))
        self.max_speakers = max(0, int(max_speakers or 0))
        self.speaker_map_json = speaker_map_json or "{}"
        self.stored_prints_json = stored_prints_json or "[]"
        self.settings = {"match_threshold": float(match_threshold or 0.60)}
        if self.max_speakers and self.min_speakers > self.max_speakers:
            self.min_speakers = self.max_speakers

    def _speaker_options(self):
        if self.num_speakers:
            return {"num_speakers": self.num_speakers}, f"exactly {self.num_speakers} speakers"
        options = {}
        if self.min_speakers:
            options["min_speakers"] = self.min_speakers
        if self.max_speakers:
            options["max_speakers"] = self.max_speakers
        if not options:
            return options, "automatic speaker count"
        return options, f"minimum {self.min_speakers or 'auto'} · maximum {self.max_speakers or 'auto'}"

    @staticmethod
    def _annotation_turns(annotation):
        turns = []
        if annotation is None:
            return turns
        for turn, _, speaker in annotation.itertracks(yield_label=True):
            turns.append({
                "start": float(turn.start),
                "end": float(turn.end),
                "speaker_id": str(speaker),
            })
        return turns

    def run(self):
        try:
            self.progress.emit("Loading PyTorch and checking the GPU runtime…")
            register_nvidia_dll_dirs()
            import numpy as np
            import torch
            # pyannote.audio 4.x has optional usage telemetry. This app is
            # offline-first and does not send telemetry, so disable it before
            # importing pyannote or constructing its pipeline.
            os.environ["PYANNOTE_METRICS_ENABLED"] = "0"
            self.progress.emit("Loading pyannote speaker-identification components…")
            # TorchCodec is deliberately absent: this worker supplies an in-memory
            # waveform, so pyannote's file decoder is not used. Suppress only its
            # expected optional-decoder warning; other warnings remain visible.
            warnings.filterwarnings("ignore", message=r"[\s\S]*torchcodec is not installed correctly.*")
            from pyannote.audio import Pipeline

            self.progress.emit("Preparing audio for speaker diarization…")
            # Resolve paths before handing them to FFmpeg or opening the WAV.
            # The app stores paths relative to its install folder; background
            # worker threads must not depend on the process working directory.
            app_root = Path(__file__).resolve().parent.parent
            source_candidate = Path(self.audio_path).expanduser()
            if not source_candidate.is_absolute():
                source_candidate = app_root / source_candidate
            output_candidate = Path(self.output_root).expanduser()
            if not output_candidate.is_absolute():
                output_candidate = app_root / output_candidate
            source_path = str(source_candidate.resolve())
            wav_path = str((output_candidate / "processed_audio" / f"{self.recording_id}.wav").resolve())
            wav = normalise_audio(source_path, wav_path, self.progress.emit)
            wav = str(Path(wav).expanduser().resolve())

            # Read the PCM WAV using Python's standard library rather than
            # torchaudio.load(). On some Windows installations the TorchAudio
            # dispatcher cannot select a backend for a valid WAV. FFmpeg above
            # always creates mono, 16 kHz, signed 16-bit PCM, so no resampling
            # backend or TorchCodec DLL is needed here.
            self.progress.emit("Loading prepared audio into memory…")
            with wave.open(wav, "rb") as audio_file:
                channels = audio_file.getnchannels()
                sample_width = audio_file.getsampwidth()
                sample_rate = audio_file.getframerate()
                frame_count = audio_file.getnframes()
                if sample_width != 2:
                    raise RuntimeError(f"Prepared WAV must be 16-bit PCM; found {sample_width * 8}-bit audio.")
                if sample_rate != 16000:
                    raise RuntimeError(f"Prepared WAV must be 16000 Hz; found {sample_rate} Hz.")
                if channels < 1 or frame_count < 1:
                    raise RuntimeError("Prepared WAV contains no audio frames.")
                raw_audio = audio_file.readframes(frame_count)
            samples = np.frombuffer(raw_audio, dtype="<i2").astype(np.float32) / 32768.0
            if channels > 1:
                samples = samples.reshape(-1, channels).mean(axis=1)
            waveform = torch.from_numpy(np.ascontiguousarray(samples)).unsqueeze(0)

            self.progress.emit("Loading Community-1 model (first load may take a few minutes)…")
            pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-community-1",
                token=self.token,
            )
            use_cuda = self.device == "cuda" and torch.cuda.is_available()
            self.progress.emit("Moving speaker model to GPU…" if use_cuda else "Preparing speaker model on CPU…")
            pipeline.to(torch.device("cuda" if use_cuda else "cpu"))

            speaker_options, speaker_note = self._speaker_options()
            self.progress.emit(f"Analysing recording and identifying speakers ({speaker_note})…")
            output = pipeline({"waveform": waveform, "sample_rate": sample_rate}, **speaker_options)

            # Community-1 exposes regular diarization and an exclusive version.
            # Use exclusive turns to align transcript words; retain regular turns
            # for the full speaker inventory and overlapping-speech information.
            regular_annotation = getattr(output, "speaker_diarization", None)
            exclusive_annotation = getattr(output, "exclusive_speaker_diarization", None)
            if regular_annotation is None:
                # Defensive compatibility with Annotation-like pipeline outputs.
                regular_annotation = output
            regular_turns = self._annotation_turns(regular_annotation)
            alignment_turns = self._annotation_turns(exclusive_annotation) if exclusive_annotation is not None else regular_turns

            # Cluster voice embeddings (pyannote 4.x exposes one per speaker). They are
            # optional: without them re-checking still works from reviewed segments alone.
            centroids = {}
            try:
                embeddings = getattr(output, "speaker_embeddings", None)
                labels = list(regular_annotation.labels()) if hasattr(regular_annotation, "labels") else []
                if embeddings is not None and len(embeddings) == len(labels):
                    for label, vector in zip(labels, embeddings):
                        values = [float(x) for x in vector]
                        if values and all(x == x and abs(x) != float("inf") for x in values):
                            centroids[str(label)] = values
            except Exception:
                centroids = {}
            self.progress.emit("Matching speaker turns to transcript segments (keeping your reviewed segments)…")
            speaker_map = json.loads(self.speaker_map_json)
            segments, map_updates, report = recheck_segments(
                json.loads(self.segments_json or "[]"), alignment_turns, speaker_map, centroids,
                json.loads(self.stored_prints_json), self.settings)
            speaker_ids = sorted({s.get("speaker_id") for s in segments if s.get("speaker_id")})
            merged_map = {**map_updates, **speaker_map}
            payload = json.dumps({
                "segments": segments,
                "speaker_ids": speaker_ids,
                "turns": regular_turns,
                # v2.31: the alignment turns in the transcript's final speaker ids (saved for later fixes).
                "labelled_turns": [{**t, "speaker_id": report.get("turn_labels", {}).get(t["speaker_id"], t["speaker_id"])}
                                   for t in alignment_turns],
                "report": report,
                "speaker_map_updates": map_updates,
                "voiceprints": voiceprints_to_save(report, centroids, merged_map),
                "has_embeddings": bool(centroids),
            }, ensure_ascii=False)
            self.progress.emit("Saving speaker assignments…")
            self.finished.emit(self.run_id, payload, "cuda" if use_cuda else "cpu")
        except Exception as exc:
            self.failed.emit(self.run_id, f"{type(exc).__name__}: {exc}")
        finally:
            # Release the pyannote pipeline, waveform and CUDA allocator cache
            # when identification finishes; transcript editing does not need them.
            try:
                try: del pipeline
                except Exception: pass
                try: del waveform
                except Exception: pass
                try: del samples
                except Exception: pass
                try: del raw_audio
                except Exception: pass
                try: del output
                except Exception: pass
                try: del regular_annotation
                except Exception: pass
                try: del exclusive_annotation
                except Exception: pass
                try: del regular_turns
                except Exception: pass
                try: del alignment_turns
                except Exception: pass
                import gc; gc.collect()
                if "torch" in locals() and torch.cuda.is_available():
                    try: torch.cuda.synchronize()
                    except Exception: pass
                    torch.cuda.empty_cache()
                    try: torch.cuda.ipc_collect()
                    except Exception: pass
            except Exception:
                pass
