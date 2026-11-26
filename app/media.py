from pathlib import Path
import subprocess
import shutil

# Gentle, speech-oriented levelling: remove rumble, then lift quiet passages (max +20 dB)
# without crushing loud ones. Applied before BOTH transcription and diarization.
LEVEL_FILTER = "highpass=f=80,dynaudnorm=f=200:g=11:p=0.9:m=10"


def ffmpeg_executable():
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _level_filter_wanted() -> str:
    try:
        from .config import load_config
        return LEVEL_FILTER if load_config().get("audio_normalise_loudness", True) else ""
    except Exception:
        return LEVEL_FILTER


def normalise_audio(source: str, destination: str, progress=None) -> str:
    """Convert a source audio/video file to mono 16 kHz PCM WAV (optionally level-matched)
    without touching the source."""
    exe = ffmpeg_executable()
    if not exe:
        raise RuntimeError("The bundled FFmpeg executable is unavailable. Re-run INSTALL.bat to repair the application dependencies.")
    src, dst = Path(source), Path(destination)
    dst.parent.mkdir(parents=True, exist_ok=True)
    audio_filter = _level_filter_wanted()
    marker = dst.with_suffix(".filter")  # records how the cached WAV was made
    try:
        used = marker.read_text(encoding="utf-8") if marker.exists() else ""
    except OSError:
        used = ""
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime and dst.stat().st_size > 44 and used == audio_filter:
        return str(dst)
    if progress:
        progress("Preparing audio with FFmpeg (original file is unchanged)…")
    cmd = [exe, "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000"]
    if audio_filter:
        cmd += ["-af", audio_filter]
    cmd += ["-c:a", "pcm_s16le", str(dst)]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode != 0 or not dst.exists() or dst.stat().st_size <= 44:
        detail = (result.stderr or result.stdout or "Unknown FFmpeg error")[-2500:]
        raise RuntimeError(f"FFmpeg conversion failed:\n{detail}")
    marker.write_text(audio_filter, encoding="utf-8")
    return str(dst)


# ---- listening copies for the transcript editor (never used for transcription) ----------
_LIMIT = "alimiter=limit=0.97"
LISTEN_OPTIONS = [
    ("Original volume", None),
    ("Louder ×2 (+6 dB)", f"volume=6dB,{_LIMIT}"),
    ("Louder ×4 (+12 dB)", f"volume=12dB,{_LIMIT}"),
    ("Louder ×8 (+18 dB)", f"volume=18dB,{_LIMIT}"),
    ("Levelled, as transcribed", LEVEL_FILTER),
    ("Levelled + ×2", f"{LEVEL_FILTER},volume=6dB,{_LIMIT}"),
]


def listening_cache_path(source: str, audio_filter: str) -> Path:
    """Stable per-file, per-filter cache location for a louder copy of the recording."""
    import hashlib
    import tempfile
    src = Path(source)
    stamp = f"{src.resolve()}|{src.stat().st_mtime_ns}|{audio_filter}"
    name = hashlib.sha1(stamp.encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / "LocalSessionKeeper" / f"listen_{name}.wav"


def listening_command(source: str, destination: str, audio_filter: str):
    exe = ffmpeg_executable()
    if not exe:
        return None
    return [exe, "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "22050", "-af", audio_filter, "-c:a", "pcm_s16le", str(destination)]
