from pathlib import Path
import hashlib
import re
from datetime import datetime

SUBFOLDERS = ("Sessions", "Characters", "NPCs", "Locations", "Items", "Quests", "Events", "Transcripts", "Attachments")


def safe_folder_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "-", name).strip(" .")
    return name or "Untitled Campaign"


def yaml_quote(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def export_campaign_stub(campaign, vault_path: str, root_folder: str, logger):
    """Create a non-destructive campaign landing note; never overwrites an existing note."""
    folder = campaign_folder(vault_path, root_folder, campaign)
    note = folder / "Campaign.md"
    if note.exists():
        logger.info("Obsidian export skipped existing note: %s", note)
        return note, False
    created = campaign["created_at"]
    content = f"""---
type: campaign
campaign_id: {yaml_quote(campaign['id'])}
game_system: {yaml_quote(campaign['game_system'] or '')}
created: {yaml_quote(created)}
tags:
  - dnd/campaign
---

# {campaign['name']}

{campaign['description'] or 'Campaign notes will be generated here.'}

## Sessions

Sessions imported through Local SessionKeeper will appear here as they are processed.

## Campaign Wiki

- [[Characters/Characters Index]]
- [[NPCs/NPC Index]]
- [[Locations/Location Index]]
- [[Items/Item Index]]
- [[Quests/Quest Index]]

---
*Created by Local SessionKeeper. This note is managed conservatively; existing notes are never overwritten.*
"""
    note.write_text(content, encoding="utf-8")
    logger.info("Created Obsidian campaign folder: %s", folder)
    return note, True


def campaign_folder(vault_path, root_folder, campaign) -> Path:
    vault = Path(vault_path).expanduser()
    if not vault.exists() or not vault.is_dir():
        raise ValueError("The selected Obsidian vault folder does not exist.")
    folder = vault / root_folder / safe_folder_name(campaign["name"])
    folder.mkdir(parents=True, exist_ok=True)
    for sub in SUBFOLDERS:
        (folder / sub).mkdir(exist_ok=True)
    return folder


# ----------------------------------------------------------------- transcript notes
def _clock(seconds) -> str:
    s = int(max(0, float(seconds or 0)))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def speaker_display(sid, speaker_map) -> str:
    entry = (speaker_map or {}).get(sid) if sid else None
    if not sid:
        return "Unassigned"
    if isinstance(entry, dict):
        who = (entry.get("player") or "").strip()
        char = (entry.get("character") or "").strip()
        role = (entry.get("role") or "").strip()
        if role == "DM":
            return f"{who} (DM)" if who else "DM"
        if who and char:
            return f"{who} ({char})"
        if who or char:
            return who or char
        if role and role != "Unknown":
            return f"{sid} ({role})"
    return sid


def build_transcript_body(segments, speaker_map) -> str:
    """Readable transcript: one line per segment, with consecutive lines from one speaker kept
    as separate timestamped lines so the user can still jump by time."""
    lines = []
    for seg in sorted(segments, key=lambda s: (float(s.get("start", 0)), float(s.get("end", 0)))):
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        mark = "" if seg.get("speaker_confirmed") else " ?"  # '?' = speaker not ticked Reviewed
        lines.append(f"**{speaker_display(seg.get('speaker_id'), speaker_map)}{mark}** `{_clock(seg.get('start'))}`: {text}")
    return "\n\n".join(lines) + "\n"


def _body_hash(body: str) -> str:
    return hashlib.sha256(body.replace("\r\n", "\n").strip().encode("utf-8")).hexdigest()


def _split_note(text: str):
    """(frontmatter dict of simple key: value lines, body) for a note we wrote."""
    text = text.replace("\r\n", "\n")
    if text.startswith("---\n") and "\n---\n" in text[4:]:
        head, _, body = text[4:].partition("\n---\n")
        meta = {}
        for line in head.split("\n"):
            if ":" in line and not line.startswith((" ", "-")):
                k, _, v = line.partition(":")
                meta[k.strip()] = v.strip().strip('"')
        return meta, body
    return {}, text


def build_transcript_note(campaign, recording_name, run, segments, speaker_map) -> str:
    body_text = build_transcript_body(segments, speaker_map)
    people = sorted({speaker_display(s.get("speaker_id"), speaker_map) for s in segments if s.get("speaker_id")})
    reviewed = sum(1 for s in segments if s.get("speaker_confirmed"))
    title = Path(recording_name).stem
    body = (f"# {title} - transcript\n\n"
            f"Speakers: {', '.join(people) if people else 'not identified yet'}  \n"
            f"Segments: {len(segments)} ({reviewed} speaker-reviewed). A **?** after a name means that speaker was not ticked Reviewed in the editor.\n\n"
            f"{body_text}")
    front = ["---", "type: session-transcript", f"campaign: {yaml_quote(campaign['name'])}", f"campaign_id: {yaml_quote(campaign['id'])}",
             f"recording: {yaml_quote(recording_name)}", f"transcript_run: {yaml_quote(run['id'])}", f"model: {yaml_quote(run['model_name'] or '')}",
             f"segments: {len(segments)}", f"reviewed_segments: {reviewed}", f"exported: {yaml_quote(datetime.now().astimezone().isoformat(timespec='minutes'))}",
             f"sk_body_sha256: {yaml_quote(_body_hash(body))}", "tags:", "  - dnd/transcript", "---", ""]
    return "\n".join(front) + "\n" + body


def write_transcript_note(path: Path, content: str):
    """Write the note unless the user has edited the one already there.

    Returns (path_written, status) where status is 'created', 'updated' (file was still exactly what we
    wrote earlier) or 'kept-edited' (your edits were found; the new export went to '<name> (new export).md')."""
    path = Path(path)
    if not path.exists():
        path.write_text(content, encoding="utf-8")
        return path, "created"
    meta, body = _split_note(path.read_text(encoding="utf-8", errors="replace"))
    if meta.get("sk_body_sha256") and meta["sk_body_sha256"] == _body_hash(body):
        path.write_text(content, encoding="utf-8")
        return path, "updated"
    alt = path.with_name(path.stem + " (new export).md")
    alt.write_text(content, encoding="utf-8")
    return alt, "kept-edited"


def export_session_transcripts(campaign, items, vault_path, root_folder, logger):
    """items: iterable of (recording_name, run_row, segments, speaker_map). Returns a list of
    (recording_name, path, status)."""
    folder = campaign_folder(vault_path, root_folder, campaign) / "Transcripts"
    results = []
    for recording_name, run, segments, speaker_map in items:
        if not segments:
            results.append((recording_name, None, "no-transcript"))
            continue
        name = safe_folder_name(Path(recording_name).stem) + " - transcript.md"
        path, status = write_transcript_note(folder / name, build_transcript_note(campaign, recording_name, run, segments, speaker_map))
        logger.info("Obsidian transcript %s: %s", status, path)
        results.append((recording_name, path, status))
    return results
