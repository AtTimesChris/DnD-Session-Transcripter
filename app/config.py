from pathlib import Path
import json
import os
import shutil

APP_NAME = "Local SessionKeeper"
APP_VERSION = "2.48"

def app_root() -> Path:
    # Keep portable data alongside the application when running from source.
    return Path(__file__).resolve().parent.parent

def default_config() -> dict:
    return {
        "data_dir": str(app_root() / "data"),
        "recordings_dir": str(app_root() / "recordings"),
        "logs_dir": str(app_root() / "logs"),
        "backups_dir": str(app_root() / "backups"),
        "obsidian_vault": "",
        "obsidian_export_vault": "",
        "obsidian_folder": "SessionKeeper",
        "allowed_extensions": [".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".mp4", ".mkv", ".webm"],
        "auto_create_campaign_folder": True,
        "whisper_model": "large-v3",
        "whisper_device": "cpu",
        "whisper_compute_type": "int8",
        # Off: hotwords are re-sent before every 30-second window and made Whisper loop
        # and drop whole windows of speech in a full-session test (v2.29r2).
        "whisper_use_hotwords": False,
        # Neutral punctuated sentence (no names) before every window, so Whisper keeps
        # punctuating instead of cutting after every phrase (v2.29.1).
        "whisper_style_hint": True,
        # Lower = keeps quieter speech (faster-whisper default is 0.5). 0 = library default.
        "whisper_vad_threshold": 0.4,
        "audio_normalise_loudness": True,
        "diarization_device": "cuda",
        # 0 = let pyannote decide. Set diarization_num_speakers to the exact
        # table size (players + DM) for the most reliable speaker separation.
        "diarization_num_speakers": 0,
        "diarization_min_speakers": 5,
        "diarization_max_speakers": 0,
        "diarization_match_threshold": 0.60,
        "use_campaign_context": False,
        # v2.47: View → Colours…
        "app_theme": "system",          # system / light / dark
        "speaker_colours": "okabe_ito", # see theme.SPEAKER_SCHEMES
        "speaker_symbols": True,        # a symbol beside each speaker, so colour is never the only clue
    }

def load_config() -> dict:
    path = app_root() / "config.json"
    config = default_config()
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(saved, dict):
                raise ValueError("config.json must contain a JSON object")
            config.update(saved)
        except Exception:
            # Do not silently discard a hand-edited file: keep a copy, then use defaults.
            try:
                shutil.copy2(path, path.with_suffix(".json.bad"))
            except Exception:
                pass
    return config

def save_config(config: dict) -> None:
    path = app_root() / "config.json"
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)  # atomic: a crash never leaves a half-written config

def ensure_directories(config: dict) -> None:
    for key in ("data_dir", "recordings_dir", "logs_dir", "backups_dir"):
        Path(config[key]).expanduser().mkdir(parents=True, exist_ok=True)
