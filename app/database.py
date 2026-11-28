import sqlite3
import json
import re
from pathlib import Path
from datetime import datetime, timezone
import uuid

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    game_system TEXT DEFAULT '',
    description TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    obsidian_relative_path TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS recordings (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    original_path TEXT NOT NULL,
    stored_path TEXT DEFAULT '',
    filename TEXT NOT NULL,
    file_size INTEGER NOT NULL DEFAULT 0,
    file_modified TEXT DEFAULT '',
    duration_seconds REAL,
    imported_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Imported',
    error TEXT DEFAULT '',
    UNIQUE(campaign_id, original_path)
);

CREATE TABLE IF NOT EXISTS processing_jobs (
    id TEXT PRIMARY KEY,
    recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
    stage TEXT NOT NULL DEFAULT 'Queued',
    progress REAL NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_error TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS processing_tasks (
    id TEXT PRIMARY KEY,
    recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
    task_type TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT 'Queued',
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_error TEXT NOT NULL DEFAULT ''
);


CREATE TABLE IF NOT EXISTS transcripts (
    id TEXT PRIMARY KEY,
    recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
    language TEXT DEFAULT '',
    model_name TEXT DEFAULT '',
    text_content TEXT NOT NULL DEFAULT '',
    segments_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    UNIQUE(recording_id)
);

CREATE TABLE IF NOT EXISTS vault_notes (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    relative_path TEXT NOT NULL,
    title TEXT NOT NULL,
    note_type TEXT DEFAULT 'Other',
    content_hash TEXT NOT NULL,
    note_content TEXT NOT NULL DEFAULT '',
    indexed_at TEXT NOT NULL,
    UNIQUE(campaign_id, relative_path)
);

CREATE TABLE IF NOT EXISTS transcript_runs (
    id TEXT PRIMARY KEY,
    recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
    language TEXT DEFAULT '',
    model_name TEXT DEFAULT '',
    device TEXT DEFAULT 'cpu',
    compute_type TEXT DEFAULT 'int8',
    text_content TEXT NOT NULL DEFAULT '',
    segments_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    speaker_map_json TEXT NOT NULL DEFAULT '{}',
    raw_text_content TEXT NOT NULL DEFAULT '',
    raw_segments_json TEXT NOT NULL DEFAULT '[]',
    segment_count INTEGER NOT NULL DEFAULT -1,
    reviewed_count INTEGER NOT NULL DEFAULT 0,
    sample_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transcript_edit_events (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES transcript_runs(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    segment_id TEXT DEFAULT '',
    before_value TEXT DEFAULT '',
    after_value TEXT DEFAULT '',
    edited_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transcript_edit_history (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES transcript_runs(id) ON DELETE CASCADE,
    text_content TEXT NOT NULL DEFAULT '',
    segments_json TEXT NOT NULL DEFAULT '[]',
    speaker_map_json TEXT NOT NULL DEFAULT '{}',
    edited_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_candidates (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    entity_name TEXT NOT NULL,
    entity_type TEXT NOT NULL DEFAULT 'Unknown',
    source_note TEXT DEFAULT '',
    confidence TEXT DEFAULT 'Needs review',
    canonical_name TEXT DEFAULT '',
    UNIQUE(campaign_id, entity_name, entity_type, source_note)
);

CREATE TABLE IF NOT EXISTS reference_sources (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    source_path TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_category TEXT NOT NULL DEFAULT 'General reference',
    content_text TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    indexed_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(campaign_id, source_path)
);

CREATE TABLE IF NOT EXISTS reference_terms (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES reference_sources(id) ON DELETE CASCADE,
    term TEXT NOT NULL,
    approved_for_whisper INTEGER NOT NULL DEFAULT 0,
    source_category TEXT NOT NULL DEFAULT 'General reference',
    UNIQUE(campaign_id, source_id, term)
);

CREATE TABLE IF NOT EXISTS campaign_facts (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    entity_name TEXT NOT NULL DEFAULT '',
    fact_text TEXT NOT NULL,
    discovery_status TEXT NOT NULL DEFAULT 'Unknown',
    approved_for_export INTEGER NOT NULL DEFAULT 0,
    source_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS campaign_dictionary (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    canonical_name TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '',
    also_known_as TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'Character / campaign name',
    source TEXT NOT NULL DEFAULT 'Manual',
    approved_for_whisper INTEGER NOT NULL DEFAULT 1,
    source_url TEXT NOT NULL DEFAULT '',
    last_refreshed TEXT NOT NULL DEFAULT '',
    import_status TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(campaign_id, canonical_name)
);

CREATE TABLE IF NOT EXISTS campaign_mishearings (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    heard_text TEXT NOT NULL,
    intended_text TEXT NOT NULL,
    source_session TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'Candidate',
    created_at TEXT NOT NULL,
    UNIQUE(campaign_id, heard_text, intended_text, source_session)
);

CREATE TABLE IF NOT EXISTS speaker_voiceprints (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL DEFAULT '',
    person TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'Player',
    vector_json TEXT NOT NULL,
    seconds REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- v2.46: words you chose in Correct names (and names added from it): never offered as a misspelling again when
-- "Hide words that are already a name" is ticked
CREATE TABLE IF NOT EXISTS name_review_kept (
    campaign_id TEXT NOT NULL,
    word TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id, word)
);

-- v2.30.2: "Skip in this transcript" decisions of the name-correction pass (this transcript only).
CREATE TABLE IF NOT EXISTS name_review_run_skips (
    run_id TEXT NOT NULL,
    heard_text TEXT NOT NULL,
    intended_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(run_id, heard_text, intended_text)
);

-- v2.31: pyannote speaker turns of the latest speaker identification, so later fixes
-- (re-transcribed ranges) can be labelled from the same turns.
CREATE TABLE IF NOT EXISTS diarization_turns (
    run_id TEXT PRIMARY KEY,
    turns_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- v2.34: a dated summary of a D&D Beyond character per refresh (level, HP, AC estimate, spells, items),
-- so session notes can use the sheet as it was and changes between refreshes can be shown.
CREATE TABLE IF NOT EXISTS character_snapshots (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    ddb_id TEXT NOT NULL,
    character_name TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL,
    last_checked TEXT NOT NULL DEFAULT '',
    summary_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_character_snapshots ON character_snapshots(campaign_id, ddb_id, fetched_at);

-- v2.37: which character each player plays in a campaign (for the roll finder).
CREATE TABLE IF NOT EXISTS player_characters (
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    player TEXT NOT NULL,
    character_name TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(campaign_id, player)
);

-- v2.39: the roll finder's results per transcript version (for session and campaign player stats), and the
-- corrections the user made in it (pass / fail, who took damage).
CREATE TABLE IF NOT EXISTS roll_log (
    run_id TEXT PRIMARY KEY,
    saved_at TEXT NOT NULL,
    rows_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS roll_overrides (
    run_id TEXT NOT NULL,
    row_key TEXT NOT NULL,
    field TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY(run_id, row_key, field)
);

-- v2.46: when each roll finder choice was made (for "Your changes…": spot a list changed by accident)
CREATE TABLE IF NOT EXISTS roll_override_times (
    run_id TEXT NOT NULL,
    row_key TEXT NOT NULL,
    field TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    PRIMARY KEY(run_id, row_key, field)
);

CREATE TABLE IF NOT EXISTS app_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_recordings_campaign ON recordings(campaign_id);
CREATE INDEX IF NOT EXISTS idx_jobs_stage ON processing_jobs(stage);
"""

# Campaign-specific review candidates come from real sessions, so they live in a local file
# (data/ is never uploaded). Without the file, nothing is seeded.
CANDIDATES_FILE = Path(__file__).resolve().parent.parent / "data" / "witchlight_candidates.json"


def _local_witchlight_candidates():
    try:
        rows = json.loads(CANDIDATES_FILE.read_text(encoding="utf-8"))
        return [(str(heard), str(intended), str(session)) for heard, intended, session in rows]
    except (OSError, ValueError, TypeError):
        return []

# Snapshots of the full transcript are only taken when the last one is older than this.
HISTORY_SNAPSHOT_INTERVAL_SECONDS = 120


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _unique_casefold(values):
    seen, result = set(), []
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


class Database:
    def __init__(self, db_path: str, startup: bool = True):
        """Open the database.

        startup=True (the application's main connection) creates/migrates the
        schema and runs crash recovery. Worker threads must pass startup=False:
        otherwise opening a second connection re-queues jobs that are genuinely
        running and repeats every migration.
        """
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=15)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 15000")
        if startup:
            try:
                # WAL lets the editor's background autosave write while the UI reads.
                self.conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.DatabaseError:
                pass
            self._create_and_migrate()
            self._recover_interrupted_work()

    # ------------------------------------------------------------------ startup
    def _create_and_migrate(self):
        self.conn.executescript(SCHEMA)
        # Additive migrations for databases created by earlier builds.
        rec_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(recordings)")}
        if "position" not in rec_cols:
            self.conn.execute("ALTER TABLE recordings ADD COLUMN position INTEGER")
            # Existing recordings keep a stable manual order, starting from the order they were imported.
            for (campaign_id,) in self.conn.execute("SELECT DISTINCT campaign_id FROM recordings").fetchall():
                ids = [r[0] for r in self.conn.execute("SELECT id FROM recordings WHERE campaign_id=? ORDER BY imported_at ASC, rowid ASC", (campaign_id,)).fetchall()]
                for n, rid in enumerate(ids, 1):
                    self.conn.execute("UPDATE recordings SET position=? WHERE id=?", (n, rid))
            self.conn.commit()
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(vault_notes)")}
        if "note_content" not in cols:
            self.conn.execute("ALTER TABLE vault_notes ADD COLUMN note_content TEXT NOT NULL DEFAULT ''")
        run_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(transcript_runs)")}
        for col, definition in (("raw_text_content", "TEXT NOT NULL DEFAULT ''"),
                                ("raw_segments_json", "TEXT NOT NULL DEFAULT '[]'"),
                                ("segment_count", "INTEGER NOT NULL DEFAULT -1"),
                                ("reviewed_count", "INTEGER NOT NULL DEFAULT 0"),
                                ("sample_count", "INTEGER NOT NULL DEFAULT 0")):
            if col not in run_cols:
                self.conn.execute(f"ALTER TABLE transcript_runs ADD COLUMN {col} {definition}")
        ref_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(reference_sources)")}
        if "metadata_json" not in ref_cols:
            self.conn.execute("ALTER TABLE reference_sources ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'")
        if "use_level" not in ref_cols:   # v2.47: Campaign book / Rules / Other / Off ('' = the default)
            self.conn.execute("ALTER TABLE reference_sources ADD COLUMN use_level TEXT NOT NULL DEFAULT ''")
        dict_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(campaign_dictionary)")}
        # v2.36: also_known_as = other real names of the same entry (Creeping Lyn for Endelyn Moongrave). The old
        # aliases column keeps its data and meaning: wrong spellings that should become the canonical name.
        for col, definition in (("source_url", "TEXT NOT NULL DEFAULT ''"), ("last_refreshed", "TEXT NOT NULL DEFAULT ''"), ("import_status", "TEXT NOT NULL DEFAULT ''"),
                                ("also_known_as", "TEXT NOT NULL DEFAULT ''")):
            if col not in dict_cols:
                self.conn.execute(f"ALTER TABLE campaign_dictionary ADD COLUMN {col} {definition}")
        self.conn.commit()
        # Preserve the original Phase 2 transcript as the first comparable run.
        old = self.conn.execute("SELECT t.* FROM transcripts t LEFT JOIN transcript_runs v ON v.recording_id=t.recording_id WHERE v.id IS NULL").fetchall()
        with self.conn:
            for row in old:
                self.conn.execute(
                    "INSERT INTO transcript_runs(id,recording_id,language,model_name,device,compute_type,text_content,segments_json,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), row['recording_id'], row['language'], row['model_name'], 'cpu', 'int8', row['text_content'], row['segments_json'], '{"legacy_phase2":true}', row['created_at'])
                )
            self.conn.execute("UPDATE transcript_runs SET raw_text_content=text_content WHERE raw_text_content=''")
            self.conn.execute("UPDATE transcript_runs SET raw_segments_json=segments_json WHERE raw_segments_json='[]'")

    def _recover_interrupted_work(self):
        stamp = now_iso()
        with self.conn:
            self.conn.execute("UPDATE processing_jobs SET stage='Queued', updated_at=? WHERE stage='Running'", (stamp,))
            self.conn.execute(
                "UPDATE processing_tasks SET stage='Queued', updated_at=?, last_error='Re-queued after application restart' WHERE stage='Running'",
                (stamp,))
            self.conn.execute("UPDATE recordings SET status='Imported' WHERE status='Processing'")
            # Review candidates are retained for the user to approve, never applied automatically.
            for campaign in self.conn.execute("SELECT id FROM campaigns WHERE lower(name) LIKE '%witchlight%'").fetchall():
                self._seed_witchlight(campaign["id"], stamp)

    def _seed_witchlight(self, campaign_id, stamp):
        for heard, intended, source_session in _local_witchlight_candidates():
            self.conn.execute(
                "INSERT OR IGNORE INTO campaign_mishearings(id,campaign_id,heard_text,intended_text,source_session,status,created_at) VALUES(?,?,?,?,?,'Candidate',?)",
                (str(uuid.uuid4()), campaign_id, heard, intended, source_session, stamp))

    def close(self):
        self.conn.close()

    # ---------------------------------------------------------------- campaigns
    def campaigns(self):
        return self.conn.execute(
            "SELECT c.*, COUNT(DISTINCT r.id) AS recording_count "
            "FROM campaigns c LEFT JOIN recordings r ON r.campaign_id=c.id "
            "GROUP BY c.id ORDER BY c.name COLLATE NOCASE"
        ).fetchall()

    def create_campaign(self, name, game_system="", description=""):
        stamp = now_iso()
        cid = str(uuid.uuid4())
        with self.conn:
            self.conn.execute(
                "INSERT INTO campaigns(id,name,game_system,description,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (cid, name.strip(), game_system.strip(), description.strip(), stamp, stamp)
            )
            if "witchlight" in name.casefold():
                self._seed_witchlight(cid, stamp)
        return cid

    def update_campaign(self, cid, name, game_system, description):
        with self.conn:
            self.conn.execute(
                "UPDATE campaigns SET name=?, game_system=?, description=?, updated_at=? WHERE id=?",
                (name.strip(), game_system.strip(), description.strip(), now_iso(), cid)
            )

    def delete_campaign(self, cid):
        with self.conn:
            self.conn.execute("DELETE FROM campaigns WHERE id=?", (cid,))

    def get_campaign(self, cid):
        return self.conn.execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone()

    def get_recording(self, recording_id):
        return self.conn.execute("SELECT * FROM recordings WHERE id=?", (recording_id,)).fetchone()

    def recordings(self, campaign_id):
        return self.conn.execute(
            "SELECT r.*, j.stage AS job_stage, j.progress AS job_progress, j.last_error "
            "FROM recordings r LEFT JOIN processing_jobs j ON j.recording_id=r.id "
            "WHERE r.campaign_id=? ORDER BY COALESCE(r.position, 1000000000), r.rowid",
            (campaign_id,)
        ).fetchall()

    def add_recording(self, campaign_id, path):
        p = Path(path)
        stat = p.stat()
        rid = str(uuid.uuid4())
        stamp = now_iso()
        modified = datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds")
        try:
            with self.conn:  # both inserts commit together or neither does
                position = (self.conn.execute("SELECT COALESCE(MAX(position),0) FROM recordings WHERE campaign_id=?", (campaign_id,)).fetchone()[0] or 0) + 1
                self.conn.execute(
                    "INSERT INTO recordings(id,campaign_id,original_path,filename,file_size,file_modified,imported_at,status,position) "
                    "VALUES(?,?,?,?,?,?,?,'Imported',?)",
                    (rid, campaign_id, str(p.resolve()), p.name, stat.st_size, modified, stamp, position)
                )
                self.conn.execute(
                    "INSERT INTO processing_jobs(id,recording_id,stage,progress,created_at,updated_at) "
                    "VALUES(?,?,'Queued',0,?,?)",
                    (str(uuid.uuid4()), rid, stamp, stamp)
                )
            return True, rid
        except sqlite3.IntegrityError:
            return False, None

    def set_recording_order(self, campaign_id, ordered_ids):
        with self.conn:
            for n, rid in enumerate(ordered_ids, 1):
                self.conn.execute("UPDATE recordings SET position=? WHERE id=? AND campaign_id=?", (n, rid, campaign_id))

    def move_recordings(self, campaign_id, ids, delta):
        """Move the given recordings up (delta<0) or down (delta>0) one place, keeping their relative order."""
        order = [r["id"] for r in self.recordings(campaign_id)]
        chosen = set(ids)
        seq = range(len(order)) if delta < 0 else range(len(order) - 1, -1, -1)
        for i in seq:
            j = i + (-1 if delta < 0 else 1)
            if order[i] in chosen and 0 <= j < len(order) and order[j] not in chosen:
                order[i], order[j] = order[j], order[i]
        self.set_recording_order(campaign_id, order)

    @staticmethod
    def _natural_key(name):
        return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", name or "")]

    def sort_recordings_by_name(self, campaign_id):
        rows = sorted(self.recordings(campaign_id), key=lambda r: self._natural_key(r["filename"]))
        self.set_recording_order(campaign_id, [r["id"] for r in rows])

    def queue_counts(self):
        return self.conn.execute(
            "SELECT stage, COUNT(*) AS count FROM processing_jobs GROUP BY stage"
        ).fetchall()

    def all_jobs(self):
        return self.conn.execute(
            "SELECT j.*, r.filename, c.name AS campaign_name FROM processing_jobs j "
            "JOIN recordings r ON r.id=j.recording_id "
            "JOIN campaigns c ON c.id=r.campaign_id ORDER BY j.created_at"
        ).fetchall()


    def add_processing_task(self, recording_id, task_type):
        """Queue a task. v2.50: returns None (and adds nothing) when the same task for the same recording is already
        queued or running (pressing Transcribe twice queued a second transcription)."""
        if task_type not in {"transcribe", "diarize"}:
            raise ValueError("Unsupported processing task type.")
        if self.conn.execute("SELECT 1 FROM processing_tasks WHERE recording_id=? AND task_type=? AND stage IN ('Queued','Running')",
                             (recording_id, task_type)).fetchone():
            return None
        stamp=now_iso(); task_id=str(uuid.uuid4())
        with self.conn:
            position=self.conn.execute("SELECT COALESCE(MAX(position),0)+1 FROM processing_tasks WHERE stage='Queued'").fetchone()[0]
            self.conn.execute("INSERT INTO processing_tasks(id,recording_id,task_type,stage,position,created_at,updated_at) VALUES(?,?,?,'Queued',?,?,?)",(task_id,recording_id,task_type,position,stamp,stamp))
        return task_id

    def queued_processing_tasks(self, include_history=True):
        where="" if include_history else "WHERE t.stage IN ('Queued','Running','Failed')"
        return self.conn.execute("SELECT t.*,r.filename,c.name AS campaign_name FROM processing_tasks t JOIN recordings r ON r.id=t.recording_id JOIN campaigns c ON c.id=r.campaign_id "+where+" ORDER BY CASE t.stage WHEN 'Running' THEN 0 WHEN 'Queued' THEN 1 WHEN 'Failed' THEN 2 ELSE 3 END,t.position,t.created_at").fetchall()

    def next_processing_task(self):
        return self.conn.execute("SELECT * FROM processing_tasks WHERE stage='Queued' ORDER BY position,created_at LIMIT 1").fetchone()

    def update_processing_task(self, task_id, stage, error=''):
        with self.conn:
            self.conn.execute("UPDATE processing_tasks SET stage=?,last_error=?,updated_at=? WHERE id=?",(stage,error,now_iso(),task_id))

    def move_processing_task(self, task_id, direction):
        if direction not in {-1, 1}: raise ValueError("Direction must be -1 or 1.")
        row=self.conn.execute("SELECT id,position FROM processing_tasks WHERE id=? AND stage='Queued'",(task_id,)).fetchone()
        if not row:return False
        peers=self.conn.execute("SELECT id,position FROM processing_tasks WHERE stage='Queued' ORDER BY position,created_at").fetchall()
        index=next((i for i,item in enumerate(peers) if item["id"]==task_id),-1)
        other_index=index+direction
        if index<0 or other_index<0 or other_index>=len(peers):return False
        a,b=peers[index],peers[other_index]
        with self.conn:
            self.conn.execute("UPDATE processing_tasks SET position=? WHERE id=?",(b["position"],a["id"]))
            self.conn.execute("UPDATE processing_tasks SET position=? WHERE id=?",(a["position"],b["id"]))
        return True

    def remove_processing_task(self, task_id):
        """v2.50: take one queued or failed task out of the queue (a running one is never removed)."""
        with self.conn:
            cur=self.conn.execute("DELETE FROM processing_tasks WHERE id=? AND stage IN ('Queued','Failed')",(task_id,))
        return cur.rowcount>0

    def clear_pending_processing_tasks(self):
        with self.conn:
            cur=self.conn.execute("DELETE FROM processing_tasks WHERE stage IN ('Queued','Failed')")
        return cur.rowcount

    def clear_processing_task_history(self):
        with self.conn:
            cur=self.conn.execute("DELETE FROM processing_tasks WHERE stage IN ('Completed','Partial')")
        return cur.rowcount

    def clear_processing_jobs(self, stages=("Completed", "Partial")):
        stages = tuple(stages)
        if not stages: return 0
        placeholders = ",".join("?" for _ in stages)
        with self.conn:
            cur = self.conn.execute(f"DELETE FROM processing_jobs WHERE stage IN ({placeholders})", stages)
        return cur.rowcount

    def set_job_stage(self, job_id, stage, error=""):
        with self.conn:
            self.conn.execute(
                "UPDATE processing_jobs SET stage=?, last_error=?, updated_at=? WHERE id=?",
                (stage, error, now_iso(), job_id)
            )

    def set_job_progress(self, job_id, progress):
        with self.conn:
            self.conn.execute(
                "UPDATE processing_jobs SET progress=?, updated_at=? WHERE id=?",
                (max(0.0, min(100.0, float(progress))), now_iso(), job_id)
            )

    # -------------------------------------------------------------- transcripts
    def save_transcript(self, recording_id, language, model_name, text_content, segments_json,
                        device="cpu", compute_type="int8", metadata_json="{}"):
        stamp = now_iso()
        run_id = str(uuid.uuid4())
        try:
            metadata = json.loads(metadata_json or "{}")
        except Exception:
            metadata = {}
        partial = str(metadata.get("completion_status", "")).startswith("PARTIAL") or bool(metadata.get("stopped_early"))
        job_stage = "Partial" if partial else "Completed"
        duration = float(metadata.get("audio_duration_seconds", 0) or 0)
        done = float(metadata.get("transcribed_until_seconds", 0) or 0)
        progress = min(99.0, max(0.0, done / duration * 100.0)) if partial and duration > 0 else (100.0 if not partial else 0.0)
        with self.conn:
            self.conn.execute(
                "INSERT INTO transcript_runs(id,recording_id,language,model_name,device,compute_type,text_content,segments_json,metadata_json,raw_text_content,raw_segments_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, recording_id, language, model_name, device, compute_type,
                 text_content, segments_json, metadata_json, text_content, segments_json, stamp)
            )
            self.conn.execute(
                "INSERT INTO transcripts(id,recording_id,language,model_name,text_content,segments_json,created_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(recording_id) DO UPDATE SET "
                "language=excluded.language, model_name=excluded.model_name, text_content=excluded.text_content, "
                "segments_json=excluded.segments_json, created_at=excluded.created_at",
                (str(uuid.uuid4()), recording_id, language, model_name, text_content, segments_json, stamp)
            )
            self.conn.execute("UPDATE recordings SET status=? WHERE id=?", ("Partially transcribed" if partial else "Transcribed", recording_id))
            self.conn.execute("UPDATE processing_jobs SET stage=?, progress=?, updated_at=? WHERE recording_id=?",
                              (job_stage, progress, stamp, recording_id))
        return run_id

    def get_transcript(self, recording_id):
        row = self.conn.execute("SELECT * FROM transcript_runs WHERE recording_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (recording_id,)).fetchone()
        if row:
            return row
        return self.conn.execute("SELECT * FROM transcripts WHERE recording_id=?", (recording_id,)).fetchone()

    def review_stats(self, recording_id, prefer_reviewed=False):
        """(segments, reviewed, voice_samples) for the newest run (or, with prefer_reviewed, the run with most reviewed segments), without loading its JSON.

        Counts are stored when a transcript is saved; runs created before r1 are
        counted once here and cached.
        """
        row = self.conn.execute(
            "SELECT id, segment_count, reviewed_count, sample_count FROM transcript_runs "
            "WHERE recording_id=? ORDER BY " + ("reviewed_count DESC, " if prefer_reviewed else "") + "created_at DESC, rowid DESC LIMIT 1", (recording_id,)).fetchone()
        if not row:
            return None
        if row["segment_count"] < 0:
            raw = self.conn.execute("SELECT segments_json FROM transcript_runs WHERE id=?", (row["id"],)).fetchone()[0]
            try:
                segments = json.loads(raw or "[]")
            except Exception:
                segments = []
            counts = self._segment_counts(segments)
            with self.conn:
                self.conn.execute("UPDATE transcript_runs SET segment_count=?,reviewed_count=?,sample_count=? WHERE id=?", (*counts, row["id"]))
            return counts
        return (row["segment_count"], row["reviewed_count"], row["sample_count"])

    def run_stats_list(self, recording_id):
        """Every transcription of a recording, newest first, as dicts with its review counts
        (segments, reviewed, samples). Counts stored before r1 are computed once and cached."""
        out = []
        for row in self.conn.execute(
                "SELECT id, model_name, created_at, segment_count, reviewed_count, sample_count, "
                "length(coalesce(speaker_map_json, '{}')) > 2 AS has_speakers FROM transcript_runs "   # v2.47: Progress column
                "WHERE recording_id=? ORDER BY created_at DESC, rowid DESC", (recording_id,)).fetchall():
            counts = (row["segment_count"], row["reviewed_count"], row["sample_count"])
            if row["segment_count"] < 0:
                raw = self.conn.execute("SELECT segments_json FROM transcript_runs WHERE id=?", (row["id"],)).fetchone()[0]
                try:
                    segments = json.loads(raw or "[]")
                except Exception:
                    segments = []
                counts = self._segment_counts(segments)
                with self.conn:
                    self.conn.execute("UPDATE transcript_runs SET segment_count=?,reviewed_count=?,sample_count=? WHERE id=?", (*counts, row["id"]))
            out.append({"id": row["id"], "model": row["model_name"] or "", "created_at": row["created_at"] or "",
                        "segments": counts[0], "reviewed": counts[1], "samples": counts[2], "has_speakers": bool(row["has_speakers"])})
        return out

    @staticmethod
    def _segment_counts(segments):
        return (len(segments),
                sum(1 for s in segments if s.get("speaker_confirmed")),
                sum(1 for s in segments if s.get("confirmed_voice_sample")))

    def transcript_runs(self, recording_id):
        return self.conn.execute("SELECT * FROM transcript_runs WHERE recording_id=? ORDER BY created_at DESC, rowid DESC", (recording_id,)).fetchall()

    def export_recording(self, recording_id):
        """Everything stored for one recording (v2.31.3), for the backup written before it is deleted."""
        rec = self.get_recording(recording_id)
        if not rec:
            return None
        runs = []
        for r in self.conn.execute("SELECT * FROM transcript_runs WHERE recording_id=? ORDER BY created_at", (recording_id,)).fetchall():
            run = dict(r)
            run["edit_events"] = [dict(x) for x in self.conn.execute("SELECT * FROM transcript_edit_events WHERE run_id=?", (r["id"],))]
            run["edit_history"] = [dict(x) for x in self.conn.execute("SELECT * FROM transcript_edit_history WHERE run_id=?", (r["id"],))]
            run["diarization_turns"] = self.diarization_turns(r["id"])
            run["name_review_run_skips"] = self.name_review_run_skips(r["id"])
            runs.append(run)
        return {"recording": dict(rec), "transcript_runs": runs,
                "transcripts": [dict(x) for x in self.conn.execute("SELECT * FROM transcripts WHERE recording_id=?", (recording_id,))]}

    def delete_recording(self, recording_id):
        """Remove one recording and everything stored for it: transcript versions, edit history, jobs, tasks,
        saved speaker turns and per-transcript skips. The audio file and learned voices are not touched."""
        run_ids = [r[0] for r in self.conn.execute("SELECT id FROM transcript_runs WHERE recording_id=?", (recording_id,)).fetchall()]
        with self.conn:
            for rid in run_ids:
                self.conn.execute("DELETE FROM roll_log WHERE run_id=?", (rid,))
                self.conn.execute("DELETE FROM roll_overrides WHERE run_id=?", (rid,))
                self.conn.execute("DELETE FROM name_review_run_skips WHERE run_id=?", (rid,))
                self.conn.execute("DELETE FROM diarization_turns WHERE run_id=?", (rid,))
                self.conn.execute("DELETE FROM transcript_edit_events WHERE run_id=?", (rid,))
                self.conn.execute("DELETE FROM transcript_edit_history WHERE run_id=?", (rid,))
            for table in ("transcript_runs", "transcripts", "processing_tasks", "processing_jobs"):
                self.conn.execute(f"DELETE FROM {table} WHERE recording_id=?", (recording_id,))
            self.conn.execute("DELETE FROM recordings WHERE id=?", (recording_id,))
        return len(run_ids)

    def delete_transcript_run(self, run_id):
        row = self.conn.execute("SELECT recording_id FROM transcript_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return False
        recording_id = row["recording_id"]
        count = self.conn.execute("SELECT COUNT(*) FROM transcript_runs WHERE recording_id=?", (recording_id,)).fetchone()[0]
        if count <= 1:
            raise ValueError("The last transcript version cannot be deleted.")
        with self.conn:
            self.conn.execute("DELETE FROM transcript_runs WHERE id=?", (run_id,))
            self.conn.execute("DELETE FROM name_review_run_skips WHERE run_id=?", (run_id,))
            self.conn.execute("DELETE FROM diarization_turns WHERE run_id=?", (run_id,))
            self.conn.execute("DELETE FROM roll_log WHERE run_id=?", (run_id,))
            self.conn.execute("DELETE FROM roll_overrides WHERE run_id=?", (run_id,))
            newest = self.conn.execute("SELECT * FROM transcript_runs WHERE recording_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (recording_id,)).fetchone()
            if newest:
                self.conn.execute("UPDATE transcripts SET language=?,model_name=?,text_content=?,segments_json=?,created_at=? WHERE recording_id=?",
                    (newest["language"], newest["model_name"], newest["text_content"], newest["segments_json"], newest["created_at"], recording_id))
        return True

    def _history_snapshot_due(self, run_id):
        last = self.conn.execute("SELECT edited_at FROM transcript_edit_history WHERE run_id=? ORDER BY rowid DESC LIMIT 1", (run_id,)).fetchone()
        if not last:
            return True
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(last["edited_at"]).astimezone(timezone.utc)).total_seconds()
        except (ValueError, TypeError):
            return True
        return age >= HISTORY_SNAPSHOT_INTERVAL_SECONDS

    def update_transcript_run(self, run_id, text_content, segments_json, speaker_map_json="{}", force_snapshot=False):
        row = self.conn.execute("SELECT * FROM transcript_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise ValueError("Transcript run not found.")
        stamp = now_iso()
        old_segments = json.loads(row["segments_json"] or "[]")
        new_segments = json.loads(segments_json or "[]")
        old_map = json.loads(row["speaker_map_json"] or "{}")
        new_map = json.loads(speaker_map_json or "{}")
        with self.conn:
            if (row["text_content"] != text_content or row["segments_json"] != segments_json or
                    (row["speaker_map_json"] or "{}") != (speaker_map_json or "{}")):
                # The per-field audit events below are always recorded. The full-copy
                # snapshot is throttled: autosaving every second would otherwise store
                # a complete copy of a multi-hour transcript each time.
                if force_snapshot or self._history_snapshot_due(run_id):
                    self.conn.execute(
                        "INSERT INTO transcript_edit_history(id,run_id,text_content,segments_json,speaker_map_json,edited_at) VALUES(?,?,?,?,?,?)",
                        (str(uuid.uuid4()),run_id,row["text_content"],row["segments_json"],row["speaker_map_json"] or "{}",stamp)
                    )
                def log_event(kind, sid="", before="", after=""):
                    self.conn.execute("INSERT INTO transcript_edit_events(id,run_id,event_type,segment_id,before_value,after_value,edited_at) VALUES(?,?,?,?,?,?,?)",
                        (str(uuid.uuid4()),run_id,kind,str(sid or ""),str(before or ""),str(after or ""),stamp))
                flag_fields = {"speaker_confirmed", "confirmed_voice_sample"}
                labels = (("text","Text corrected"),("start","Start time changed"),("end","End time changed"),("speaker_id","Speaker reassigned"),("speaker_confirmed","Speaker assignment confirmed"),("confirmed_voice_sample","Voice sample confirmation changed"))
                def changed(before, after, field):
                    default = False if field in flag_fields else None
                    return before.get(field, default) != after.get(field, default)
                old_by_id = {str(x.get("_edit_id")): x for x in old_segments if x.get("_edit_id")}
                new_by_id = {str(x.get("_edit_id")): x for x in new_segments if x.get("_edit_id")}
                # Older runs have no edit IDs; compare their rows by position for the first editor save.
                if not old_by_id:
                    for i, (before, after) in enumerate(zip(old_segments, new_segments)):
                        sid = after.get("_edit_id", i)
                        for field, label in labels:
                            if changed(before, after, field):
                                log_event(label, sid, before.get(field), after.get(field))
                    if len(new_segments) > len(old_segments): log_event("Segment(s) added", "", len(old_segments), len(new_segments))
                    if len(new_segments) < len(old_segments): log_event("Segment(s) removed", "", len(old_segments), len(new_segments))
                else:
                    for sid in old_by_id.keys() - new_by_id.keys(): log_event("Segment removed", sid, old_by_id[sid].get("text", ""), "")
                    for sid in new_by_id.keys() - old_by_id.keys(): log_event("Segment added", sid, "", new_by_id[sid].get("text", ""))
                    for sid in old_by_id.keys() & new_by_id.keys():
                        before, after = old_by_id[sid], new_by_id[sid]
                        for field, label in labels:
                            if changed(before, after, field): log_event(label, sid, before.get(field), after.get(field))
                for sid in set(old_map) | set(new_map):
                    if old_map.get(sid) != new_map.get(sid): log_event("Speaker mapping changed", sid, json.dumps(old_map.get(sid,{}),ensure_ascii=False), json.dumps(new_map.get(sid,{}),ensure_ascii=False))
            total, reviewed, samples = self._segment_counts(new_segments)
            self.conn.execute("UPDATE transcript_runs SET text_content=?,segments_json=?,speaker_map_json=?,segment_count=?,reviewed_count=?,sample_count=? WHERE id=?",
                              (text_content, segments_json, speaker_map_json, total, reviewed, samples, run_id))
            self.conn.execute("UPDATE transcripts SET text_content=?,segments_json=?,language=?,model_name=?,created_at=? WHERE recording_id=?",
                              (text_content, segments_json, row['language'], row['model_name'], stamp, row['recording_id']))

    def transcript_edit_events(self, run_id):
        return self.conn.execute("SELECT * FROM transcript_edit_events WHERE run_id=? ORDER BY edited_at, rowid", (run_id,)).fetchall()

    def transcript_edit_event_cursor(self, run_id):
        row = self.conn.execute("SELECT COALESCE(MAX(rowid), 0) AS last_rowid FROM transcript_edit_events WHERE run_id=?", (run_id,)).fetchone()
        return int(row["last_rowid"] or 0)

    def transcript_edit_events_since(self, run_id, after_rowid=0):
        return self.conn.execute("SELECT * FROM transcript_edit_events WHERE run_id=? AND rowid>? ORDER BY rowid", (run_id, int(after_rowid))).fetchall()

    def transcript_edit_history(self, run_id):
        return self.conn.execute("SELECT * FROM transcript_edit_history WHERE run_id=? ORDER BY edited_at DESC, rowid DESC", (run_id,)).fetchall()

    # ------------------------------------------------------------- voiceprints
    # A voiceprint is the average voice embedding of a cluster the user's own review
    # identified as one person. Stored per campaign so later sessions can be matched.
    MAX_VOICEPRINTS_PER_PERSON = 8

    def save_voiceprints(self, campaign_id, run_id, prints):
        stamp = now_iso()
        with self.conn:
            # Re-checking the same run replaces its earlier prints instead of piling up.
            self.conn.execute("DELETE FROM speaker_voiceprints WHERE campaign_id=? AND run_id=?", (campaign_id, run_id))
            for item in prints:
                self.conn.execute(
                    "INSERT INTO speaker_voiceprints(id,campaign_id,run_id,person,role,vector_json,seconds,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), campaign_id, run_id, item["name"], item.get("role", "Player"),
                     json.dumps(item["vector"]), float(item.get("seconds", 0)), stamp))
            self.conn.execute(
                "DELETE FROM speaker_voiceprints WHERE campaign_id=? AND rowid NOT IN ("
                "SELECT rowid FROM (SELECT rowid, ROW_NUMBER() OVER (PARTITION BY person ORDER BY created_at DESC, rowid DESC) AS n "
                "FROM speaker_voiceprints WHERE campaign_id=?) WHERE n<=?)",
                (campaign_id, campaign_id, self.MAX_VOICEPRINTS_PER_PERSON))

    def voiceprints(self, campaign_id, exclude_run_id="", share_across=False):
        rows = self.conn.execute("SELECT person,role,vector_json,seconds FROM speaker_voiceprints WHERE campaign_id=? AND run_id<>? ORDER BY created_at DESC",
                                 (campaign_id, exclude_run_id)).fetchall()
        if share_across:
            # Reuse voices learned in other campaigns, but only for people who belong to this one
            # (same player name in this campaign's prints or speaker mappings).
            names = {str(r["person"]).strip().casefold() for r in rows}
            for run in self.conn.execute("SELECT t.speaker_map_json FROM transcript_runs t JOIN recordings r ON r.id=t.recording_id WHERE r.campaign_id=?", (campaign_id,)).fetchall():
                try:
                    for key, entry in json.loads(run[0] or "{}").items():
                        if not str(key).startswith("_") and isinstance(entry, dict) and (entry.get("player") or "").strip():
                            names.add(entry["player"].strip().casefold())
                except Exception:
                    continue
            others = self.conn.execute("SELECT person,role,vector_json,seconds FROM speaker_voiceprints WHERE campaign_id<>? ORDER BY created_at DESC", (campaign_id,)).fetchall()
            # v2.50: a new campaign has no names yet (its first session is identified before you name anyone), so then every
            # voice from your other campaigns is offered; matches are still listed in the report for you to check
            rows = list(rows) + [r for r in others if not names or str(r["person"]).strip().casefold() in names]
            kept, count = [], {}
            for r in rows:  # newest first within each group; at most MAX per person overall
                key = str(r["person"]).strip().casefold()
                count[key] = count.get(key, 0) + 1
                if count[key] <= self.MAX_VOICEPRINTS_PER_PERSON:
                    kept.append(r)
            rows = kept
        result = []
        for r in rows:
            try:
                result.append({"name": r["person"], "role": r["role"], "vector": json.loads(r["vector_json"]), "seconds": r["seconds"]})
            except Exception:
                continue
        return result

    def clear_voiceprints(self, campaign_id):
        with self.conn:
            self.conn.execute("DELETE FROM speaker_voiceprints WHERE campaign_id=?", (campaign_id,))

    def update_transcript_metadata(self, run_id, metadata_json):
        with self.conn:
            self.conn.execute("UPDATE transcript_runs SET metadata_json=? WHERE id=?", (metadata_json, run_id))

    # ----------------------------------------------------------- campaign terms
    def campaign_mishearings(self, campaign_id):
        return self.conn.execute("SELECT * FROM campaign_mishearings WHERE campaign_id=? AND status='Candidate' ORDER BY source_session,heard_text COLLATE NOCASE",(campaign_id,)).fetchall()

    # ------------------------------------------------- learning from the user's edits
    def campaign_text_correction_events(self, campaign_id):
        return self.conn.execute(
            "SELECT e.run_id,e.segment_id,e.event_type,e.before_value,e.after_value FROM transcript_edit_events e "
            "JOIN transcript_runs r ON r.id=e.run_id JOIN recordings rec ON rec.id=r.recording_id "
            "WHERE rec.campaign_id=? AND e.event_type='Text corrected' ORDER BY e.rowid", (campaign_id,)).fetchall()

    def known_correction_pairs(self, campaign_id):
        """Every heard->intended pair already decided on (accepted or rejected), so it is not offered again."""
        return [(r["heard_text"], r["intended_text"]) for r in self.conn.execute(
            "SELECT heard_text,intended_text FROM campaign_mishearings WHERE campaign_id=?", (campaign_id,)).fetchall()]

    def record_learned_correction(self, campaign_id, heard, intended, accept):
        """accept: the editor suggests it from now on and the intended spelling joins the Whisper vocabulary.
        reject: remembered so it is never offered again."""
        stamp = now_iso()
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO campaign_mishearings(id,campaign_id,heard_text,intended_text,source_session,status,created_at) VALUES(?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), campaign_id, heard, intended, "learned from edits", "Candidate" if accept else "Rejected", stamp))
            if accept:
                have = {r[0].casefold() for r in self.conn.execute("SELECT canonical_name FROM campaign_dictionary WHERE campaign_id=?", (campaign_id,))}
                if intended.casefold() not in have:
                    self.conn.execute(
                        "INSERT INTO campaign_dictionary(id,campaign_id,canonical_name,aliases,category,source,approved_for_whisper,created_at) VALUES(?,?,?,?,?,?,?,?)",
                        (str(uuid.uuid4()), campaign_id, intended, "", "Character / campaign name", "Learned from edits", 1, stamp))

    # ------------------------------------------------- v2.30: name-correction pass
    def name_review_data(self, campaign_id):
        """Everything the name-correction pass reads: dictionary rows, accepted and rejected
        heard->intended pairs, and the text of every transcript version in the campaign (used only
        to learn which words are ordinary lower-case words). Read-only.
        campaign_mishearings.status: 'Candidate' = accepted (used for suggestions), 'Rejected' = never offer."""
        rows = [{"canonical_name": r["canonical_name"], "aliases": r["aliases"] or "", "also_known_as": r["also_known_as"] or "",
                 "category": r["category"] or ""}   # v2.43: spells / items / feats are matched as whole names
                for r in self.conn.execute("SELECT canonical_name, aliases, also_known_as, category FROM campaign_dictionary WHERE campaign_id=?", (campaign_id,))]
        accepted, rejected = [], []
        for r in self.conn.execute("SELECT heard_text, intended_text, status FROM campaign_mishearings WHERE campaign_id=?", (campaign_id,)):
            (rejected if r["status"] == "Rejected" else accepted).append((r["heard_text"], r["intended_text"]))
        texts = [r[0] for r in self.conn.execute(
            "SELECT r.text_content FROM transcript_runs r JOIN recordings rec ON rec.id=r.recording_id WHERE rec.campaign_id=?",
            (campaign_id,))]
        return rows, accepted, rejected, texts

    def campaign_people(self, campaign_id):
        """Players' names and nicknames from every speaker mapping in the campaign (v2.30.1).
        Read-only. Used so the name pass never 'corrects' a player's name and can suggest misheard ones."""
        names = set()
        for (raw,) in self.conn.execute(
                "SELECT r.speaker_map_json FROM transcript_runs r JOIN recordings rec ON rec.id=r.recording_id WHERE rec.campaign_id=?",
                (campaign_id,)):
            try:
                mapping = json.loads(raw or "{}")
            except Exception:
                continue
            for sid, entry in mapping.items() if isinstance(mapping, dict) else ():
                if str(sid).startswith("_") or not isinstance(entry, dict):
                    continue
                # Players' own names only: characters belong in the campaign names list, and the
                # character fields hold hand-typed variants ("Vaelth", "Liorra") that must not
                # become "correct" spellings.
                for value in (entry.get("player"), *str(entry.get("player_nicknames") or "").split(",")):
                    value = str(value or "").strip()
                    if value and value.casefold() not in {"dm", "unknown", "unmapped"} and not re.search(r"[/\\0-9]", value):
                        names.add(value)
        return sorted(names, key=str.casefold)

    def name_review_run_skips(self, run_id):
        """(heard, intended) pairs the user skipped for this transcript only (v2.30.2)."""
        return [(r[0], r[1]) for r in self.conn.execute(
            "SELECT heard_text, intended_text FROM name_review_run_skips WHERE run_id=?", (run_id,))]

    def add_name_review_run_skips(self, run_id, pairs):
        stamp = now_iso()
        with self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO name_review_run_skips(run_id,heard_text,intended_text,created_at) VALUES(?,?,?,?)",
                [(run_id, h, i, stamp) for h, i in pairs])

    # ---- v2.46: names Correct names already knows are right ----
    def name_review_kept(self, campaign_id):
        """Words you chose or kept in Correct names (casefolded)."""
        return {r[0].casefold() for r in self.conn.execute("SELECT word FROM name_review_kept WHERE campaign_id=?", (campaign_id,))}

    def add_name_review_kept(self, campaign_id, words):
        stamp = now_iso()
        with self.conn:
            self.conn.executemany("INSERT OR IGNORE INTO name_review_kept(campaign_id,word,created_at) VALUES(?,?,?)",
                                  [(campaign_id, str(w).strip(), stamp) for w in words if str(w).strip()])

    def known_name_words(self, campaign_id):
        """Names from your Obsidian notes (casefolded), so a word spelled exactly like one is not offered as a misspelling."""
        out = set()
        for (n,) in self.conn.execute("SELECT DISTINCT COALESCE(NULLIF(canonical_name,''),entity_name) FROM entity_candidates WHERE campaign_id=?",
                                      (campaign_id,)):
            if n:
                out.add(str(n).casefold())
                out.update(p.casefold() for p in str(n).split() if len(p) >= 4)
        return out

    def campaign_rules(self, campaign_id):
        """(campaign name, rules edition from its Game system) (v2.47)."""
        from .rules_edition import campaign_edition
        c = self.get_campaign(campaign_id)
        return (c["name"] if c else ""), campaign_edition(c["game_system"] if c else "")

    def book_levels(self, campaign_id):
        """{source id: (level, set_by_you)} for every book of the campaign (v2.47)."""
        from .rules_edition import level_of
        name, edition = self.campaign_rules(campaign_id)
        return {s["id"]: level_of(s, name, edition) for s in self.reference_source_list(campaign_id)}

    def set_reference_level(self, source_id, level):
        """v2.47: '' puts the book back on its default level."""
        with self.conn:
            self.conn.execute("UPDATE reference_sources SET use_level=? WHERE id=?", (level or "", source_id))

    def book_names(self, campaign_id):
        """The ticked names of the books that are not Off: [(name, book file name, level)] (v2.46; level v2.47)."""
        levels = self.book_levels(campaign_id)
        return [(r[0], r[1], levels.get(r[2], ("other", False))[0]) for r in self.conn.execute(
            "SELECT rt.term, rs.source_name, rs.id FROM reference_terms rt JOIN reference_sources rs ON rs.id=rt.source_id "
            "WHERE rt.campaign_id=? AND rt.approved_for_whisper=1 ORDER BY rt.term COLLATE NOCASE", (campaign_id,))
            if levels.get(r[2], ("other", False))[0] != "off"]

    def add_campaign_name(self, campaign_id, name, category="NPC", also_known_as=""):
        """v2.46: add one name to Campaign names (from Correct names). An existing name is left as it is (False)."""
        name = str(name or "").strip()
        if not name:
            return False
        if self.conn.execute("SELECT 1 FROM campaign_dictionary WHERE campaign_id=? AND canonical_name=? COLLATE NOCASE",
                             (campaign_id, name)).fetchone():
            return False
        with self.conn:
            self.conn.execute("INSERT INTO campaign_dictionary(id,campaign_id,canonical_name,aliases,also_known_as,category,source,"
                              "approved_for_whisper,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                              (str(uuid.uuid4()), campaign_id, name, "", also_known_as, category, "Correct names", 1, now_iso()))
        return True

    def term_approvals(self, source_id):
        """{term: ticked} of one book (v2.46: kept when its names are found again)."""
        return {r[0]: bool(r[1]) for r in self.conn.execute("SELECT term, approved_for_whisper FROM reference_terms WHERE source_id=?", (source_id,))}

    def save_diarization_turns(self, run_id, turns):
        """Keep the speaker turns of the latest speaker identification for this transcript (v2.31)."""
        clean = [{"start": round(float(t["start"]), 3), "end": round(float(t["end"]), 3), "speaker_id": str(t["speaker_id"])}
                 for t in turns if float(t.get("end", 0)) > float(t.get("start", 0))]
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO diarization_turns(run_id,turns_json,created_at) VALUES(?,?,?)",
                              (run_id, json.dumps(clean), now_iso()))

    def diarization_turns(self, run_id):
        row = self.conn.execute("SELECT turns_json FROM diarization_turns WHERE run_id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else []

    def record_name_review_decision(self, campaign_id, heard, intended, accept):
        """accept: remembered as a known mishearing (exact match next time; the editor suggests it too).
        reject: never suggested again. The campaign dictionary itself is not changed."""
        stamp = now_iso()
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO campaign_mishearings(id,campaign_id,heard_text,intended_text,source_session,status,created_at) VALUES(?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), campaign_id, heard, intended, "name review", "Candidate" if accept else "Rejected", stamp))

    def save_character_snapshot(self, campaign_id, ddb_id, url, summary):
        """Store one refresh. An identical summary to the latest one is not stored twice (returns False)."""
        latest = self.character_snapshots(campaign_id, ddb_id, limit=1)
        body = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        if latest and json.dumps(latest[0]["summary"], ensure_ascii=False, sort_keys=True) == body:
            with self.conn:   # same sheet: keep when it was first seen, note when it was last checked
                self.conn.execute("UPDATE character_snapshots SET last_checked=? WHERE id=?", (now_iso(), latest[0]["id"]))
            return False
        with self.conn:
            stamp = now_iso()
            self.conn.execute("INSERT INTO character_snapshots(id,campaign_id,ddb_id,character_name,url,fetched_at,last_checked,summary_json) VALUES(?,?,?,?,?,?,?,?)",
                              (str(uuid.uuid4()), campaign_id, str(ddb_id), summary.get("name", ""), url or "", stamp, stamp, body))
        return True

    def character_snapshots(self, campaign_id, ddb_id=None, limit=None):
        """Newest first: [{'id','ddb_id','character_name','url','fetched_at','summary'}]."""
        sql = "SELECT * FROM character_snapshots WHERE campaign_id=?" + (" AND ddb_id=?" if ddb_id else "") + " ORDER BY fetched_at DESC, rowid DESC"
        args = (campaign_id, str(ddb_id)) if ddb_id else (campaign_id,)
        if limit: sql += f" LIMIT {int(limit)}"
        out = []
        for r in self.conn.execute(sql, args).fetchall():
            try: summary = json.loads(r["summary_json"])
            except Exception: summary = {}
            out.append({"id": r["id"], "ddb_id": r["ddb_id"], "character_name": r["character_name"], "url": r["url"],
                        "fetched_at": r["fetched_at"], "last_checked": r["last_checked"] or r["fetched_at"], "summary": summary})
        return out

    def roll_overrides(self, run_id):
        """{(row_key, field): value} the user set in the roll finder (v2.39)."""
        return {(r["row_key"], r["field"]): r["value"] for r in
                self.conn.execute("SELECT row_key, field, value FROM roll_overrides WHERE run_id=?", (run_id,))}

    def set_roll_override(self, run_id, row_key, field, value):
        with self.conn:
            if value in (None, ""):
                self.conn.execute("DELETE FROM roll_overrides WHERE run_id=? AND row_key=? AND field=?", (run_id, row_key, field))
                self.conn.execute("DELETE FROM roll_override_times WHERE run_id=? AND row_key=? AND field=?", (run_id, row_key, field))
            else:
                self.conn.execute("INSERT OR REPLACE INTO roll_overrides(run_id,row_key,field,value) VALUES(?,?,?,?)",
                                  (run_id, row_key, field, str(value)))
                self.conn.execute("INSERT OR REPLACE INTO roll_override_times(run_id,row_key,field,changed_at) VALUES(?,?,?,?)",
                                  (run_id, row_key, field, now_iso()))

    def roll_override_times(self, run_id):
        """{(row_key, field): when it was set} (v2.46; choices made before v2.46 have no time)."""
        return {(r[0], r[1]): r[2] for r in self.conn.execute(
            "SELECT row_key, field, changed_at FROM roll_override_times WHERE run_id=?", (run_id,))}

    def save_roll_log(self, run_id, rows):
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO roll_log(run_id,saved_at,rows_json) VALUES(?,?,?)",
                              (run_id, now_iso(), json.dumps(rows, ensure_ascii=False)))

    def roll_log(self, run_id):
        r = self.conn.execute("SELECT rows_json FROM roll_log WHERE run_id=?", (run_id,)).fetchone()
        return json.loads(r["rows_json"]) if r else []

    def campaign_roll_logs(self, campaign_id):
        """[(run_id, recording filename, rows)] for every transcript version in the campaign that has a roll log."""
        out = []
        for r in self.conn.execute("SELECT l.run_id, rec.filename, l.rows_json FROM roll_log l JOIN transcript_runs t ON t.id=l.run_id "
                                   "JOIN recordings rec ON rec.id=t.recording_id WHERE rec.campaign_id=? ORDER BY rec.position", (campaign_id,)):
            out.append((r["run_id"], r["filename"], json.loads(r["rows_json"])))
        return out

    def player_characters(self, campaign_id):
        """{player: character name} for a campaign (v2.37)."""
        return {r["player"]: r["character_name"] for r in
                self.conn.execute("SELECT player, character_name FROM player_characters WHERE campaign_id=?", (campaign_id,))}

    def save_player_characters(self, campaign_id, mapping):
        with self.conn:
            self.conn.execute("DELETE FROM player_characters WHERE campaign_id=?", (campaign_id,))
            for player, character in mapping.items():
                if str(player).strip():
                    self.conn.execute("INSERT INTO player_characters(campaign_id, player, character_name) VALUES(?,?,?)",
                                      (campaign_id, str(player).strip(), str(character or "").strip()))

    def campaign_dictionary_entries(self, campaign_id):
        return self.conn.execute("SELECT * FROM campaign_dictionary WHERE campaign_id=? ORDER BY canonical_name COLLATE NOCASE", (campaign_id,)).fetchall()

    def save_campaign_dictionary(self, campaign_id, entries):
        stamp=now_iso()
        # One transaction: if any insert fails, the old dictionary is kept intact.
        with self.conn:
            self.conn.execute("DELETE FROM campaign_dictionary WHERE campaign_id=?", (campaign_id,))
            seen=set()
            for entry in entries:
                name=str(entry.get("canonical_name", "")).strip()
                key=name.casefold()
                if not name or key in seen: continue
                seen.add(key)
                self.conn.execute("INSERT INTO campaign_dictionary(id,campaign_id,canonical_name,aliases,also_known_as,category,source,approved_for_whisper,source_url,last_refreshed,import_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()),campaign_id,name,str(entry.get("aliases", "")).strip(),str(entry.get("also_known_as", "")).strip(),str(entry.get("category", "Character / campaign name")),str(entry.get("source", "Manual")),1 if entry.get("approved_for_whisper", True) else 0,str(entry.get("source_url", "")),str(entry.get("last_refreshed", "")),str(entry.get("import_status", "")),stamp))

    def _vocabulary_groups(self, campaign_id):
        """Return (dictionary names, note names, reference terms), each a de-duplicated list."""
        names = self.conn.execute(
            "SELECT DISTINCT COALESCE(NULLIF(canonical_name,''),entity_name) AS n FROM entity_candidates WHERE campaign_id=? ORDER BY n COLLATE NOCASE",
            (campaign_id,)
        ).fetchall()
        # Adventure terminology is intentionally included: the active adventure
        # is often the most relevant spelling reference for this campaign. It is
        # still only a list of terms, never plot passages or identity revelations.
        approved_terms = self.conn.execute(
            "SELECT rt.term, MIN(CASE WHEN lower(rs.source_category) LIKE 'adventure%' THEN 0 "
            "WHEN lower(rs.source_category) LIKE 'campaign setting%' THEN 1 ELSE 2 END) AS priority "
            "FROM reference_terms rt JOIN reference_sources rs ON rs.id=rt.source_id "
            "WHERE rt.campaign_id=? AND rt.approved_for_whisper=1 "
            "GROUP BY rt.term ORDER BY priority, rt.term COLLATE NOCASE",
            (campaign_id,)
        ).fetchall()
        # Only the correct spelling goes to Whisper. Aliases are often mishearings or
        # ordinary words ("Glean", "Zoo", "Red"); sending them teaches Whisper to write
        # them. Aliases stay in the dictionary for the editor's correction suggestions.
        dictionary_rows = self.conn.execute("SELECT canonical_name, also_known_as, category FROM campaign_dictionary WHERE campaign_id=? AND approved_for_whisper=1 ORDER BY canonical_name COLLATE NOCASE", (campaign_id,)).fetchall()
        # v2.36: "also known as" names are real names people say (Creeping Lyn), so they are hinted too;
        # mistranscriptions (the aliases column) never are.
        dictionary_names, characters = [], set()
        for row in dictionary_rows[:500]:
            row_names = [row["canonical_name"]] + [a.strip() for a in str(row["also_known_as"] or "").split(",") if a.strip()]
            dictionary_names.extend(row_names)
            if str(row["category"] or "").casefold().startswith("character"):
                characters.update(n.casefold() for n in row_names)
        characters.update(str(c).casefold() for c in self.player_characters(campaign_id).values() if c)
        return (self._rank_names(campaign_id, _unique_casefold(dictionary_names), characters),
                _unique_casefold(r["n"] for r in names[:350]),
                _unique_casefold(r["term"] for r in approved_terms[:500]))

    def _rank_names(self, campaign_id, names, characters=()):
        """v2.43: most valuable first: the player characters, then the names said most in the campaign's three
        latest sessions, then the rest alphabetically. Only ~700 characters of names reach Whisper, and the list
        used to be alphabetical with the start cut off, so only names from P to Z were ever sent."""
        texts, seen = [], set()
        for r in self.conn.execute(
                "SELECT r.recording_id, r.text_content FROM transcript_runs r JOIN recordings rec ON rec.id=r.recording_id "
                "WHERE rec.campaign_id=? ORDER BY r.created_at DESC", (campaign_id,)):
            if r["recording_id"] not in seen:
                seen.add(r["recording_id"]); texts.append(str(r["text_content"] or "").casefold())
            if len(texts) >= 3:
                break
        recent = "\n".join(texts)

        counts = {n: 0 for n in names}
        if recent and names:   # one pass over the text for every name (longest first, so "Agdon Longscarf" wins over "Agdon")
            by_key = {n.casefold(): n for n in names}
            rx = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(k) for k in sorted(by_key, key=len, reverse=True)) + r")(?!\w)")
            for m in rx.finditer(recent):
                counts[by_key[m.group(0)]] += 1
        return sorted(names, key=lambda n: (n.casefold() not in characters, -counts[n], n.casefold()))

    @staticmethod
    def _keep_tail(text, max_chars):
        """Keep the END of text (Whisper keeps the last ~223 prompt tokens), starting on a term boundary."""
        if len(text) <= max_chars:
            return text
        text = text[-max_chars:]
        cut = text.find(", ")
        return text[cut + 2:] if 0 <= cut < 200 else text

    def campaign_prompt_context(self, campaign_id, max_chars=700):
        # Recognition context is vocabulary-only. Full reference passages are kept
        # locally for later audio-grounded review and are never sent to Whisper.
        # Whisper discards the START of an over-long prompt, so the most valuable
        # terms (the campaign's own names) go last and survive truncation.
        dictionary_names, note_names, reference_terms = self._vocabulary_groups(campaign_id)
        parts = []
        if reference_terms:
            parts.append("Reference spellings: " + ", ".join(reference_terms))
        if note_names:
            parts.append("Campaign notes: " + ", ".join(note_names))
        if dictionary_names:   # most valuable last: Whisper keeps the end of the prompt (v2.43)
            parts.append("Campaign names: " + ", ".join(reversed(dictionary_names)))
        if not parts:
            return ""
        return self._keep_tail("D&D vocabulary (spell names this way only when spoken).\n" + "\n".join(parts), max_chars)

    def campaign_hotwords(self, campaign_id, max_chars=500):
        """Short, highest-value term list for faster-whisper's `hotwords` (applied to every window)."""
        dictionary_names, note_names, _ = self._vocabulary_groups(campaign_id)
        words, used = [], 0
        for term in _unique_casefold(dictionary_names + note_names):
            if used + len(term) + 2 > max_chars:
                break
            words.append(term)
            used += len(term) + 2
        return ", ".join(words)

    def save_reference_source(self, campaign_id, source, terms, approved_terms):
        stamp = now_iso()
        source_id = str(uuid.uuid4())
        metadata_json = json.dumps(source.get("metadata", {}), ensure_ascii=False)
        with self.conn:
            self.conn.execute(
                "INSERT INTO reference_sources(id,campaign_id,source_path,source_name,source_category,content_text,content_hash,indexed_at,metadata_json) "
                "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(campaign_id,source_path) DO UPDATE SET "
                "source_name=excluded.source_name,source_category=excluded.source_category,content_text=excluded.content_text,content_hash=excluded.content_hash,indexed_at=excluded.indexed_at,metadata_json=excluded.metadata_json",
                (source_id,campaign_id,source["path"],source["name"],source["category"],source["content"],source["hash"],stamp,metadata_json)
            )
            row = self.conn.execute("SELECT id FROM reference_sources WHERE campaign_id=? AND source_path=?", (campaign_id, source["path"])).fetchone()
            source_id = row["id"]
            self.conn.execute("DELETE FROM reference_terms WHERE source_id=?", (source_id,))
            approved = set(approved_terms)
            for term in terms:
                self.conn.execute(
                    "INSERT OR IGNORE INTO reference_terms(id,campaign_id,source_id,term,approved_for_whisper,source_category) VALUES(?,?,?,?,?,?)",
                    (str(uuid.uuid4()),campaign_id,source_id,term,1 if term in approved else 0,source["category"])
                )
        return source_id

    def reference_texts(self, campaign_id):
        """The text of the campaign's books for spell dice (v2.42), v2.47: in the order they are trusted (the Rules books of
        the campaign's edition first) and without the books set to Off. Local only."""
        from .rules_edition import spell_order
        name, edition = self.campaign_rules(campaign_id)
        order = spell_order(self.reference_source_list(campaign_id), name, edition)
        return [self.reference_content(s["id"]) for s in order]

    # ---- v2.43: D&D books window ----
    def reference_source_list(self, campaign_id):
        """Every book in the campaign's reference library, oldest import first, with its size and word counts."""
        return [dict(r) for r in self.conn.execute(
            "SELECT rs.id, rs.source_path, rs.source_name, rs.source_category, rs.content_hash, rs.indexed_at, rs.metadata_json, rs.use_level, "
            "length(rs.content_text) AS size, "
            "(SELECT COUNT(*) FROM reference_terms rt WHERE rt.source_id=rs.id) AS terms, "
            "(SELECT COUNT(*) FROM reference_terms rt WHERE rt.source_id=rs.id AND rt.approved_for_whisper=1) AS approved "
            "FROM reference_sources rs WHERE rs.campaign_id=? ORDER BY rs.indexed_at, rs.source_name COLLATE NOCASE", (campaign_id,))]

    def reference_content(self, source_id):
        row = self.conn.execute("SELECT content_text FROM reference_sources WHERE id=?", (source_id,)).fetchone()
        return (row[0] or "") if row else ""

    def relink_reference_source(self, source_id, path, name, content, content_hash):
        """Point a book at another file (or the same one, re-read) and store that file's text. Its category and
        Whisper word choices are kept."""
        with self.conn:
            self.conn.execute("UPDATE reference_sources SET source_path=?, source_name=?, content_text=?, content_hash=?, indexed_at=? "
                              "WHERE id=?", (path, name, content, content_hash, now_iso(), source_id))

    def set_reference_metadata(self, source_id, metadata, category=None):
        """v2.45: the book's details (from D&D Beyond) and, when given, its category."""
        with self.conn:
            self.conn.execute("UPDATE reference_sources SET metadata_json=? WHERE id=?", (json.dumps(metadata, ensure_ascii=False), source_id))
        if category:
            self.set_reference_category(source_id, category)

    def set_reference_category(self, source_id, category):
        with self.conn:
            self.conn.execute("UPDATE reference_sources SET source_category=? WHERE id=?", (category, source_id))
            self.conn.execute("UPDATE reference_terms SET source_category=? WHERE source_id=?", (category, source_id))

    def replace_reference_terms(self, source_id, terms, approved_terms):
        row = self.conn.execute("SELECT campaign_id, source_category FROM reference_sources WHERE id=?", (source_id,)).fetchone()
        if not row:
            return
        approved = set(approved_terms)
        with self.conn:
            self.conn.execute("DELETE FROM reference_terms WHERE source_id=?", (source_id,))
            for term in terms:
                self.conn.execute(
                    "INSERT OR IGNORE INTO reference_terms(id,campaign_id,source_id,term,approved_for_whisper,source_category) VALUES(?,?,?,?,?,?)",
                    (str(uuid.uuid4()), row["campaign_id"], source_id, term, 1 if term in approved else 0, row["source_category"]))

    def delete_reference_source(self, source_id):
        """Remove one book (its stored text and Whisper words) from the library. The file on disk is not touched."""
        with self.conn:
            self.conn.execute("DELETE FROM reference_terms WHERE source_id=?", (source_id,))
            self.conn.execute("DELETE FROM reference_sources WHERE id=?", (source_id,))

    def reference_source_count(self, campaign_id):
        return self.conn.execute("SELECT COUNT(*) FROM reference_sources WHERE campaign_id=?", (campaign_id,)).fetchone()[0]

    def save_campaign_fact(self, campaign_id, entity_name, fact_text, discovery_status, approved_for_export=False, source_note=""):
        stamp = now_iso()
        with self.conn:
            self.conn.execute(
                "INSERT INTO campaign_facts(id,campaign_id,entity_name,fact_text,discovery_status,approved_for_export,source_note,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()),campaign_id,entity_name,fact_text,discovery_status,1 if approved_for_export else 0,source_note,stamp,stamp)
            )

    def campaign_facts(self, campaign_id):
        return self.conn.execute("SELECT * FROM campaign_facts WHERE campaign_id=? ORDER BY created_at", (campaign_id,)).fetchall()

    def approved_campaign_facts(self, campaign_id):
        return self.conn.execute(
            "SELECT * FROM campaign_facts WHERE campaign_id=? AND approved_for_export=1 AND discovery_status IN ('Revealed','DM override') ORDER BY entity_name,created_at",
            (campaign_id,)
        ).fetchall()

    def update_campaign_fact(self, fact_id, entity_name, fact_text, discovery_status, approved_for_export, source_note=""):
        with self.conn:
            self.conn.execute(
                "UPDATE campaign_facts SET entity_name=?,fact_text=?,discovery_status=?,approved_for_export=?,source_note=?,updated_at=? WHERE id=?",
                (entity_name,fact_text,discovery_status,1 if approved_for_export else 0,source_note,now_iso(),fact_id)
            )

    def delete_campaign_fact(self, fact_id):
        with self.conn:
            self.conn.execute("DELETE FROM campaign_facts WHERE id=?", (fact_id,))

    def save_vault_index(self, campaign_id, rows, candidates):
        stamp = now_iso()
        with self.conn:
            for row in rows:
                self.conn.execute(
                    "INSERT INTO vault_notes(id,campaign_id,relative_path,title,note_type,content_hash,note_content,indexed_at) "
                    "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(campaign_id,relative_path) DO UPDATE SET "
                    "title=excluded.title,note_type=excluded.note_type,content_hash=excluded.content_hash,note_content=excluded.note_content,indexed_at=excluded.indexed_at",
                    (str(uuid.uuid4()), campaign_id, row["path"], row["title"], row["type"], row["hash"], row.get("content", ""), stamp)
                )
            for item in candidates:
                self.conn.execute(
                    "INSERT OR IGNORE INTO entity_candidates(id,campaign_id,entity_name,entity_type,source_note,confidence) "
                    "VALUES(?,?,?,?,?,'Needs review')",
                    (str(uuid.uuid4()), campaign_id, item["name"], item["type"], item["source"])
                )

    def vault_note_count(self, campaign_id):
        return self.conn.execute("SELECT COUNT(*) FROM vault_notes WHERE campaign_id=?", (campaign_id,)).fetchone()[0]

    def entity_candidates(self, campaign_id):
        return self.conn.execute(
            "SELECT * FROM entity_candidates WHERE campaign_id=? ORDER BY entity_type,entity_name",
            (campaign_id,)
        ).fetchall()

    def update_entity_candidate(self, candidate_id, canonical_name, confidence):
        with self.conn:
            self.conn.execute("UPDATE entity_candidates SET canonical_name=?, confidence=? WHERE id=?",
                              (canonical_name.strip(), confidence, candidate_id))

    def checkpoint(self):
        self.conn.execute("PRAGMA wal_checkpoint(FULL)")
        self.conn.commit()
