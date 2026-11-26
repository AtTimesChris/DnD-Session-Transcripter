from pathlib import Path
import hashlib, re

def classify_note(rel, text):
    parts = [part.lower() for part in Path(rel).parts]
    low = rel.lower()
    if "session notes" in parts or re.search(r"\\bsession\\s+\\d+", Path(rel).stem, re.I):
        return "Session"
    if "party" in parts and Path(rel).stem.lower() != "party":
        return "Character"
    if "npcs" in parts: return "NPC"
    if "enemies" in parts: return "Enemy"
    if "locations" in parts: return "Location"
    if "items" in parts: return "Item"
    if "quest" in low: return "Quest"
    if "#partymember" in text.lower(): return "Character"
    return "Other"

def scan_vault(vault_path):
    root = Path(vault_path)
    if not root.is_dir():
        raise ValueError("Selected vault folder does not exist.")
    rows, candidates = [], []
    for file in root.rglob("*.md"):
        rel = file.relative_to(root).as_posix()
        parts = [part.lower() for part in Path(rel).parts]
        if ".obsidian" in parts or ".trash" in parts or ".git" in parts:
            continue
        try:
            raw = file.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            raw = file.read_text(encoding="utf-8", errors="replace")
        typ = classify_note(rel, raw)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        rows.append({"path": rel, "title": file.stem, "type": typ, "hash": digest, "content": raw[:20000]})
        if typ in {"Character", "NPC", "Enemy", "Location", "Item", "Quest"}:
            candidates.append({"name": file.stem, "type": typ, "source": rel})
    return rows, candidates
