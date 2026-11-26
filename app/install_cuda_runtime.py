"""Install the Windows CUDA 12 + cuDNN 9 runtime for Faster-Whisper.

Automatic extraction is attempted first. If the installed Python extractor cannot
handle BCJ2, the user can select a folder extracted with 7-Zip/WinZip.
"""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import urllib.request

RELEASE_API = "https://api.github.com/repos/Purfview/whisper-standalone-win/releases/tags/libs"
ASSET_NAME = "cuBLAS.and.cuDNN_CUDA12_win_v3.7z"
REQUIRED = ("cublas64_12.dll", "cudnn64_9.dll")


def _find_required(root: Path):
    found = {}
    for name in REQUIRED:
        matches = list(root.rglob(name))
        if matches:
            found[name] = matches[0]
    return found


def _select_extracted_folder():
    try:
        from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.information(
            None, "Manual CUDA extraction",
            "The archive downloaded successfully, but automatic extraction was not supported.\n\n"
            "Please extract the downloaded .7z archive with 7-Zip or WinZip, then select the "
            "folder containing the extracted files. You can select the top-level extracted folder; "
            "the installer will search inside it."
        )
        folder = QFileDialog.getExistingDirectory(
            None, "Select the folder containing the extracted CUDA runtime",
            str(Path.home() / "Downloads")
        )
        return Path(folder) if folder else None
    except Exception as exc:
        print(f"Could not open folder picker: {exc}")
        print("Extract the archive with 7-Zip/WinZip, then rerun INSTALL.bat.")
        return None


def _extract_with_7zip(archive: Path, destination: Path):
    candidates = [shutil.which("7z"), shutil.which("7za"), shutil.which("7zz")]
    candidates += [
        str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "7-Zip" / "7z.exe"),
        str(Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "7-Zip" / "7z.exe"),
    ]
    executable = next((p for p in candidates if p and Path(p).is_file()), None)
    if not executable:
        return False, "7-Zip command-line extractor not found"
    result = subprocess.run([executable, "x", "-y", f"-o{destination}", str(archive)],
                            capture_output=True, text=True)
    if result.returncode:
        return False, (result.stderr or result.stdout or "7-Zip extraction failed")[-3000:]
    return True, "7-Zip extraction succeeded"


def _extract(archive: Path, destination: Path):
    ok, message = _extract_with_7zip(archive, destination)
    if ok:
        return
    print(f"Built-in 7-Zip extraction unavailable: {message}")
    try:
        import py7zr
        with py7zr.SevenZipFile(archive, mode="r") as z:
            z.extractall(path=destination)
        return
    except Exception as exc:
        print(f"Automatic extraction failed: {type(exc).__name__}: {exc}")
    selected = _select_extracted_folder()
    if not selected:
        raise RuntimeError(
            "Automatic extraction failed and no extracted folder was selected. "
            "Extract the downloaded archive with 7-Zip or WinZip and rerun INSTALL.bat."
        )
    if not selected.is_dir():
        raise RuntimeError("The selected extracted folder does not exist.")
    files = _find_required(selected)
    missing = [name for name in REQUIRED if name not in files]
    if missing:
        raise RuntimeError(
            "The selected folder does not contain all required runtime DLLs: " + ", ".join(missing) +
            ". Select the top-level folder created by extracting the full archive."
        )
    # Copy the complete extracted tree, not just the two primary DLLs: cuBLAS/cuDNN
    # depend on companion DLLs which must remain alongside the application.
    for item in selected.iterdir():
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def main():
    root = Path(__file__).resolve().parent.parent
    destination = root / "runtime" / "cuda"
    destination.mkdir(parents=True, exist_ok=True)
    present = _find_required(destination)
    if all(name in present for name in REQUIRED):
        print("CUDA runtime files already present; validating DLL load...")
        return 0

    print("The Windows CUDA 12.8 + cuDNN 9 runtime archive is approximately 850 MB.")
    print("It is installed locally in this application folder; no system PATH or admin change is made.")
    request = urllib.request.Request(RELEASE_API, headers={"User-Agent": "LocalSessionKeeper-Installer"})
    with urllib.request.urlopen(request, timeout=60) as response:
        release = json.loads(response.read().decode("utf-8"))
    asset = next((a for a in release.get("assets", []) if a.get("name") == ASSET_NAME), None)
    if not asset:
        raise RuntimeError(f"Could not find expected release asset: {ASSET_NAME}")
    archive = Path.home() / "Downloads" / ASSET_NAME
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists() or (asset.get("size") and archive.stat().st_size != int(asset["size"])):
        url = asset["browser_download_url"]
        print(f"Downloading {ASSET_NAME} ({asset.get('size', 0) / (1024**3):.2f} GiB) to {archive}")
        req = urllib.request.Request(url, headers={"User-Agent": "LocalSessionKeeper-Installer"})
        with urllib.request.urlopen(req, timeout=120) as response, archive.open("wb") as out:
            total = int(response.headers.get("Content-Length", asset.get("size", 0)) or 0)
            done = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk); done += len(chunk)
                if total:
                    print(f"\rDownloaded {done / total * 100:5.1f}%", end="", flush=True)
        expected = int(asset.get("size", 0) or 0)
        if expected and archive.stat().st_size != expected:
            raise RuntimeError(f"Incomplete download: expected {expected} bytes, received {archive.stat().st_size}.")
        print("\nDownload complete.")
    else:
        print(f"Using previously downloaded archive: {archive}")

    print("Extracting CUDA runtime. This may take several minutes...")
    _extract(archive, destination)
    found = _find_required(destination)
    missing = [name for name in REQUIRED if name not in found]
    if missing:
        raise RuntimeError("Runtime extraction finished, but required DLLs are missing: " + ", ".join(missing))
    print(f"cuBLAS: {found['cublas64_12.dll']}")
    print(f"cuDNN:  {found['cudnn64_9.dll']}")
    print("CUDA runtime files installed. The application will validate DLL loading when started.")
    try:
        archive.unlink(missing_ok=True)
        print("Removed the downloaded archive to free disk space.")
    except OSError:
        print(f"The archive could not be removed automatically: {archive}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"CUDA runtime installation failed: {type(exc).__name__}: {exc}")
        raise SystemExit(1)
