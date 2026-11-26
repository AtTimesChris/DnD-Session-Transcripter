"""Exit 0 if the local CUDA/cuDNN runtime is already installed (used by INSTALL.bat to skip the download)."""
from pathlib import Path

REQUIRED = ("cublas64_12.dll", "cudnn64_9.dll")


def present():
    root = Path(__file__).resolve().parent.parent / "runtime" / "cuda"
    return root.is_dir() and all(next(root.rglob(name), None) is not None for name in REQUIRED)


if __name__ == "__main__":
    raise SystemExit(0 if present() else 1)
