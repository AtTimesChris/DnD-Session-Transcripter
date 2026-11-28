from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QComboBox, QCheckBox,
                               QDialogButtonBox, QLabel, QSpinBox, QDoubleSpinBox)

class AudioSettingsDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Audio processing settings")
        self.setMinimumWidth(430); self.setMaximumWidth(760)
        self.config = dict(config)
        layout = QVBoxLayout(self)
        info = QLabel("Choose a Whisper model and processing device. GPU mode requires a compatible CUDA/CTranslate2 setup.")
        info.setWordWrap(True)
        layout.addWidget(info)
        form = QFormLayout()
        self.model = QComboBox(); self.model.addItems(["small", "medium", "large-v3"])
        self.model.setCurrentText(config.get("whisper_model", "large-v3"))
        self.device = QComboBox(); self.device.addItems(["cpu", "cuda"])
        self.device.setCurrentText(config.get("whisper_device", "cpu"))
        self.compute = QComboBox()
        self.device.currentTextChanged.connect(self._device_changed)
        self._device_changed(self.device.currentText())
        preferred = config.get("whisper_compute_type", "int8" if self.device.currentText()=="cpu" else "float16")
        if self.compute.findText(preferred) >= 0: self.compute.setCurrentText(preferred)
        self.diarization_device = QComboBox(); self.diarization_device.addItems(["cpu", "cuda"])
        self.diarization_device.setCurrentText(config.get("diarization_device", "cuda"))
        self.min_speakers = QSpinBox(); self.min_speakers.setRange(1, 20); self.min_speakers.setValue(int(config.get("diarization_min_speakers", 5)))
        self.use_context = QCheckBox("Use campaign notes and approved names as vocabulary")
        self.use_context.setToolTip("Off by default. Gives Whisper the canonical spellings of your ticked campaign names as a starting prompt. "
                                    "With the settings this app uses, the prompt only affects the first 30 seconds of a recording, so it helps little with names; "
                                    "in a full-session test it did not lose speech. Fix names afterwards with Learn from my edits / vocabulary suggestions.")
        self.use_context.setChecked(config.get("use_campaign_context", False))
        self.level_audio = QCheckBox("Level quiet audio (recommended)")
        self.level_audio.setChecked(bool(config.get("audio_normalise_loudness", True)))
        self.level_audio.setToolTip("Lifts quiet passages (up to +20 dB) and removes low rumble, on a temporary copy. Your recording is never changed. "
                                    "Turn off to compare, or if a recording is already evenly loud, or if levelling makes table noise in quiet gaps turn into invented text. "
                                    "Changing this re-prepares the audio the next time you transcribe; existing transcripts are untouched.")
        self.hotwords = QCheckBox("Also send names as hotwords on every window")
        self.hotwords.setChecked(bool(config.get("whisper_use_hotwords", False)))
        self.hotwords.setToolTip("Not recommended. Hotwords are repeated to Whisper before every 30-second window (only when Campaign context is on). "
                                 "In a full-session test they made Whisper loop on names (\"Gleam, Gleam, Gleam...\") and lose about 11 whole "
                                 "30-second stretches of speech, including crosstalk; with hotwords off, none were lost. Off by default.")
        self.style_hint = QCheckBox("Punctuation style hint (recommended)")
        self.style_hint.setChecked(bool(config.get("whisper_style_hint", True)))
        self.style_hint.setToolTip("Gives Whisper one short, ordinary punctuated sentence (no names) before every 30-second window, "
                                   "so it keeps writing full, punctuated sentences instead of cutting after almost every phrase. "
                                   "In a full-session test it removed all mid-sentence one-word segments without losing speech. "
                                   "The sentence itself never appears in the transcript. If Hotwords are on (with Campaign context), "
                                   "they take this slot instead. Affects new transcriptions only.")
        self.share_voices = QCheckBox("Reuse learned voices from my other campaigns")
        self.share_voices.setChecked(bool(config.get("voiceprints_share_across_campaigns", True)))
        self.share_voices.setToolTip("Voices you confirmed in one campaign help identify the same players in another. Only players whose name appears in this campaign are used; in a new campaign where nobody is named yet, everyone's learned voice is tried. Different microphones or rooms make matches less reliable, and matches are listed for you to spot-check.")
        self.vad = QDoubleSpinBox(); self.vad.setRange(0.0, 0.9); self.vad.setSingleStep(0.05); self.vad.setDecimals(2)
        self.vad.setValue(float(config.get("whisper_vad_threshold", 0.4)))
        self.vad.setToolTip("Speech-detection threshold. Lower keeps quieter speech (0.4 default here, library default 0.5). Raise it if you see invented text in silent stretches. 0 = library default.")
        form.addRow("Whisper model", self.model)
        form.addRow("Device", self.device)
        form.addRow("Compute type", self.compute)
        form.addRow("Speaker identification device", self.diarization_device)
        form.addRow("Minimum speakers", self.min_speakers)
        form.addRow("Campaign context", self.use_context)
        form.addRow("Audio levelling", self.level_audio)
        form.addRow("Punctuation", self.style_hint)
        form.addRow("Hotwords", self.hotwords)
        form.addRow("Learned voices", self.share_voices)
        form.addRow("Speech detection threshold", self.vad)
        layout.addLayout(form)
        note = QLabel("Best accuracy: large-v3 + CUDA + float16. If VRAM is tight, try int8_float16. CPU + int8 works without CUDA. "
                      "Levelling lifts quiet speech before transcription and speaker identification (your recording is never changed). "
                      "Learned voices are matched by player name. Speaker identification runs as a separate stage; do not run it at the same time as transcription.")
        note.setWordWrap(True); layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _device_changed(self, device):
        current = self.compute.currentText() if hasattr(self, "compute") else ""
        self.compute.blockSignals(True)
        self.compute.clear()
        self.compute.addItems(["int8", "float32"] if device == "cpu" else ["float16", "int8_float16", "int8"])
        if self.compute.findText(current) >= 0: self.compute.setCurrentText(current)
        self.compute.blockSignals(False)

    def values(self):
        return {"whisper_model": self.model.currentText(),
                "whisper_device": self.device.currentText(),
                "whisper_compute_type": self.compute.currentText(),
                "diarization_device": self.diarization_device.currentText(),
                "diarization_min_speakers": self.min_speakers.value(),
                "use_campaign_context": self.use_context.isChecked(),
                "audio_normalise_loudness": self.level_audio.isChecked(),
                "voiceprints_share_across_campaigns": self.share_voices.isChecked(),
                "whisper_use_hotwords": self.hotwords.isChecked(),
                "whisper_style_hint": self.style_hint.isChecked(),
                "whisper_vad_threshold": self.vad.value()}
