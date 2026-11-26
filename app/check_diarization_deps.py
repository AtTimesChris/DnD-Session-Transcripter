"""Verify the exact optional diarization dependency stack before app startup."""
import importlib.metadata as md
import os
import sys
import warnings

EXPECTED = {
    "torch": "2.8.0+cu128",
    "torchaudio": "2.8.0+cu128",
    "pyannote.audio": "4.0.7",
    "huggingface-hub": "0.36.2",
}


def main():
    problems = []
    for package, expected in EXPECTED.items():
        try:
            actual = md.version(package)
        except md.PackageNotFoundError:
            actual = "NOT INSTALLED"
        print(f"{package}: {actual} (required: {expected})")
        if actual != expected:
            problems.append(f"{package}: expected {expected}, found {actual}")

    # TorchCodec is deliberately removed by INSTALL_DIARIZATION.bat.
    # The application decodes audio itself and passes an in-memory waveform.
    try:
        codec_version = md.version("torchcodec")
    except md.PackageNotFoundError:
        codec_version = None
    if codec_version is not None:
        problems.append(f"torchcodec: must be absent (found {codec_version}); rerun INSTALL_DIARIZATION.bat")
    else:
        print("torchcodec: not installed (expected; in-memory waveform is used)")

    try:
        # Match the application's privacy default during dependency checks.
        os.environ["PYANNOTE_METRICS_ENABLED"] = "0"
        warnings.filterwarnings("ignore", message=r"[\s\S]*torchcodec is not installed correctly.*")
        import torch
        import numpy
        import wave
        from pyannote.audio import Pipeline
        from huggingface_hub import hf_hub_download
        if not callable(hf_hub_download):
            problems.append("huggingface_hub hf_hub_download is unavailable")
        if not callable(Pipeline.from_pretrained):
            problems.append("pyannote Pipeline.from_pretrained is unavailable")
        print("pyannote Pipeline import: OK")
        print(f"NumPy waveform decoder: {'OK' if callable(numpy.frombuffer) else 'MISSING'}")
        print(f"Standard WAV decoder: {'OK' if callable(wave.open) else 'MISSING'}")
        print(f"CUDA available: {torch.cuda.is_available()}")
        if torch.cuda.is_available():
            print(f"GPU: {torch.cuda.get_device_name(0)}")
    except Exception as exc:
        problems.append(f"Import/compatibility test failed: {type(exc).__name__}: {exc}")

    if problems:
        print("\nDIARIZATION CHECK FAILED:")
        for problem in problems:
            print(" - " + problem)
        return 1
    print("\nDiarization dependency check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
