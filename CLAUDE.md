# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Local SessionKeeper: an offline Windows desktop app (Python 3.11, PySide6, SQLite) that turns D&D session recordings into
reviewed transcripts and campaign notes. Pipeline: recording → FFmpeg (16 kHz mono WAV in `recordings/processed_audio/`)
→ faster-whisper (large-v3, CUDA, int8_float16) → pyannote speaker identification → transcript editor (review, names,
rolls) → Obsidian Markdown export. Everything runs locally; the only network use is D&D Beyond (public catalogue,
characters) and the Hugging Face model download.

The folder name `v2.29` is historical; the version is `APP_VERSION` in `app/config.py` (also echoed in the three .bat files).

## Commands

All run from the project root with the project's venv.

```powershell
.\INSTALL.bat                     # creates .venv, installs requirements (+ CUDA runtime), checks every main package imports
.\RUN.bat                         # starts the app (python -m app.main, output to logs\LocalSessionKeeper.log)
.\RUN_TESTS.bat                   # whole test suite, offscreen Qt
```

Tests directly (Qt must be offscreen; GUI tests construct real widgets):

```powershell
$env:QT_QPA_PLATFORM = "offscreen"; .venv\Scripts\python.exe -m pytest -q tests
$env:QT_QPA_PLATFORM = "offscreen"; .venv\Scripts\python.exe -m pytest -q tests\test_v250_fixes.py
$env:QT_QPA_PLATFORM = "offscreen"; .venv\Scripts\python.exe -m pytest -q tests -k "book_levels"
```

From the Bash tool: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest -q tests`. If pytest is missing from
the venv (INSTALL.bat not re-run since v2.47), install it to a scratch folder and add that folder to `PYTHONPATH`
rather than changing the venv. There is no linter or build step; `python -m py_compile app/*.py` is the quick syntax check.

Tests are named by the version that added them (`tests/test_v246_*.py`); a new release adds `tests/test_vNNN_*.py`.

## Architecture

- `app/main.py` creates the QApplication, installs `wheel_guard` (the mouse wheel never changes a closed dropdown) and
  opens `main_window.MainWindow`.
- `app/database.py` is the single data layer: schema as `CREATE TABLE IF NOT EXISTS` plus additive migrations in
  `Database.__init__` (`PRAGMA table_info` then `ALTER TABLE ADD COLUMN`). There are no migration files; add new columns
  the same way. SQLite connections belong to the thread that created them: read what you need on the GUI thread
  before `background.run_blocking_in_thread`.
- Transcript versions are rows in `transcript_runs` (`segments_json`, `speaker_map_json`, `metadata_json`). A new
  transcription or re-check creates a new version; the main window's ◀ ▶ arrows pick one. Segments carry
  `speaker_confirmed` ("Reviewed"): **reviewed segments are never changed by any automatic pass.**
- Long work runs on QThreads or in-process workers: `transcription.TranscriptionWorker`, `diarization.DiarizationWorker`
  (pyannote, then `speaker_recheck.recheck_segments` which matches clusters to reviewed speech and saved voiceprints),
  `range_transcribe.RangeTranscriptionWorker` (re-transcribe a stretch, also the four "Alternatives" variants). The main
  window's processing queue (`processing_tasks` table) starts tasks one at a time.
- `transcript_editor.TranscriptEditor` is the hub for review work. Rows are built lazily and recycled (v2.48): a row's
  widgets exist only near the viewport, otherwise its values live in `_staged`. Always read and write through
  `_row_text`, `_set_row_text`, `_row_segment`, `_row_speaker`, `_ensure_row`, never `cellWidget` directly. Edits go
  through `_schedule_draft_save` (autosave, undo capture, and `_edit_version`, which tells windows beside the editor
  to look again).
- Windows opened from the editor are non-modal and read the editor through callbacks, not the database:
  `roll_finder_dialog.RollFinderDialog` (via a `RollContext` of callables), `name_review_dialog.NameReviewDialog`
  (Correct names), `alternatives_dialog.AlternativesDialog`. They close with the editor (`TranscriptEditor.done`).
- Qt-free logic is kept apart from dialogs so it can be unit-tested: `roll_finder.py` (detection, pass/fail, damage,
  stats), `name_review.py` (name suggestions), `book_names.py`, `rules_edition.py` (Game system → edition, book
  levels), `spell_book.py`, `range_merge.py`, `compare_logic.py`, `speaker_recheck.py`, `ddb_character.py`
  (D&D Beyond sheet summary, AC estimate). Put new logic in these modules and test it there.
- Roll finder choices (Pass / fail, Who, Asked for, Used, added rolls, AC) are stored in `roll_overrides` keyed by
  `roll_finder.roll_key(event, i)`, which depends on the detected event's start time and kind. Changing detection can
  orphan saved choices; `YourChangesDialog` lists them and can re-attach them.
- Book reference library (`reference_sources`, `reference_terms`) is per campaign. Book text never goes to Whisper:
  the Whisper prompt (`Database.campaign_prompt_context`, about 700 characters, Whisper keeps the end) is campaign names
  only. Book names are used after transcription by Correct names and the editor's Suggestions panel, weighted by each
  book's level (Campaign book 80%, Rules 85%, Other 92%, Off) from `rules_edition`.
- Whisper decisions with evidence are in `DESIGN_NOTES.md` (hotwords off by default, a neutral punctuation sentence in
  the hotwords slot, `condition_on_previous_text=False`, faster-whisper pinned at 1.2.1). Re-test before changing them.
- UI conventions: short notes go to the yellow bar via `notices.notify` / `self._notify` (not pop-ups); colours come
  from `theme.tone`; working windows call `window_state.remember(self)` and `notices.attach(self)` at the end of
  `__init__`. On a button, check box or group title a literal `&` must be written `&&` (Qt mnemonic).

## Releases and documents

- A release is a zip overlay in `backups\Projects\LocalSessionKeeper_vX.YY_overlay.zip`: app/*.py, tests, the .bat
  files, README, CHANGELOG, history and checklist documents, requirements, config.example.json. **Never config.json,
  data/, recordings/, logs/ or book folders.**
- Every release: bump `APP_VERSION` and the three .bat files (keep them CRLF; no parentheses in `echo` inside `if (...)`
  blocks), add an entry at the top of `CHANGELOG.md`, a `## vX.YY (current)` section in `README.md`, a line in
  `PROJECT_HISTORY.md`, and a section at the top of `TESTING_CHECKLIST.md` listing what the user should test.
- Version naming: v2.x = major feature(s), v2.x.Y = single refinement, rN = hotfix.
- Git: `.gitignore` is an allow-list (only `app/*.py`, the .bat files, README, CLAUDE.md, requirements and
  config.example.json are tracked; a new file type must be added to it to be uploaded). The GitHub repository is private. App code, tooltips and the README must not contain real player or
  character names or quoted session lines: use made-up examples.

## Working with the user

- Agree the cause of each problem before writing code; for a reported bug, show the cause first.
- Never change or remove the user's data automatically: flag and filter only, ask before anything destructive, and
  leave Reviewed segments alone.
- When code and the history documents disagree, the code wins (correct the docs); stated requirements win over code.
  Say which source you trusted and why.
- When proposing a code change, show the current snippet, the replacement, then what it does and why.
- Say clearly which GUI behaviour was not tried on the user's screen (off-screen runs on a database copy are fine for
  checking, but they are not the user's test).
- Do not suggest running local LLMs on this PC (hardware over-current warnings), and do not claim one AI model beats
  another without several good sources.
