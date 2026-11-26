from .runtime import register_nvidia_dll_dirs

# v2.47: every package the program (and its tests) needs from requirements.txt, checked after installing, so a fresh
# folder either works completely or INSTALL.bat says what is missing. Speaker identification is checked separately.
REQUIRED_MODULES = ["PySide6.QtWidgets", "PySide6.QtMultimedia", "faster_whisper", "ctranslate2", "huggingface_hub",
                    "imageio_ffmpeg", "keyring", "pypdf", "py7zr", "numpy", "pytest"]


def missing_modules(names=REQUIRED_MODULES):
    import importlib
    missing = []
    for name in names:
        try:
            importlib.import_module(name)
        except Exception as exc:
            missing.append(f"{name} ({exc})")
    return missing


def main():
    missing = missing_modules()
    if missing:
        print("MISSING after install: " + "; ".join(missing))
        return 1
    print("All main packages import: " + ", ".join(REQUIRED_MODULES))
    print("Checking Faster-Whisper CUDA runtime DLLs...")
    register_nvidia_dll_dirs()
    if __import__("os").name == "nt":
        try:
            import ctypes
            ctypes.WinDLL("cublas64_12.dll")
            print("cuBLAS: available")
        except Exception as exc:
            print(f"cuBLAS: unavailable ({exc})")
        try:
            import ctypes
            ctypes.WinDLL("cudnn64_9.dll")
            print("cuDNN 9: available")
        except Exception as exc:
            print(f"cuDNN 9: unavailable ({exc})")
    else:
        print("Windows DLL check skipped on this platform.")
    try:
        import ctranslate2
        print("CTranslate2 CUDA compute types:", ", ".join(ctranslate2.get_supported_compute_types("cuda")))
    except Exception as exc:
        print("CTranslate2 CUDA check:", exc)

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
