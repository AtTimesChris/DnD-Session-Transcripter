"""Windows CUDA DLL discovery for the application-local CUDA runtime bundle."""
from pathlib import Path
import os
import sys

_DLL_HANDLES = []

def register_nvidia_dll_dirs():
    if os.name != "nt":
        return []
    site_roots = []
    for root in sys.path:
        p = Path(root) if root else None
        if p and p.is_dir() and p.name.lower() == "site-packages":
            site_roots.append(p)
    added = []
    app_cuda = Path(__file__).resolve().parent.parent / "runtime" / "cuda"
    if app_cuda.is_dir():
        for dll_dir in [app_cuda] + [p for p in app_cuda.rglob("*") if p.is_dir()]:
            if any(dll_dir.glob("*.dll")):
                try:
                    _DLL_HANDLES.append(os.add_dll_directory(str(dll_dir)))
                    added.append(str(dll_dir))
                except OSError:
                    pass
    for root in site_roots:
        nvidia = root / "nvidia"
        if not nvidia.is_dir():
            continue
        for dll_dir in nvidia.glob("*/bin"):
            if dll_dir.is_dir():
                try:
                    _DLL_HANDLES.append(os.add_dll_directory(str(dll_dir)))
                    added.append(str(dll_dir))
                except OSError:
                    pass
    return added


def cuda_runtime_status():
    register_nvidia_dll_dirs()
    import ctypes
    found = {}
    for name in ("cublas64_12.dll", "cudnn64_9.dll"):
        try:
            ctypes.WinDLL(name)
            found[name] = True
        except Exception:
            found[name] = False
    return found
