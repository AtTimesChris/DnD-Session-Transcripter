import os
from pathlib import Path
from PySide6.QtCore import Qt, QThread, QTimer, QProcess, QItemSelectionModel
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QListWidget,
    QListWidgetItem, QPushButton, QLabel, QTableWidget, QTableWidgetItem,
    QFileDialog, QMessageBox, QLineEdit, QFormLayout, QGroupBox, QStatusBar,
    QHeaderView, QAbstractItemView, QPlainTextEdit, QDialog, QDialogButtonBox, QComboBox, QToolButton, QWidget, QInputDialog, QProgressBar, QProgressDialog, QScrollArea, QGridLayout
)
from .config import APP_VERSION
from .dialogs import CampaignDialog
from .obsidian import export_campaign_stub, export_session_transcripts
from .vault_import import scan_vault
from .transcription import TranscriptionWorker
from .audio_settings import AudioSettingsDialog
from .transcript_editor import TranscriptEditor
from .diarization import DiarizationWorker
from .speaker_recheck import prune_speaker_map, summarise_report
from .run_import import import_reviewed, summarise_import, edited_clip_keys, describe_clips
from .corrections_dialog import LearnedCorrectionsDialog, TextReportDialog, ImportClipsDialog
from .transcript_compare import TranscriptComparisonDialog
from .reference_library import (read_reference, candidate_terms, source_record, DND_BOOK_CATEGORIES, guess_book_title, lookup_book_metadata, suggest_dnd_category)
from .reference_terms_dialog import ReferenceTermReviewDialog
from .campaign_dictionary import CampaignDictionaryDialog
from .background import run_blocking_in_thread
import json, re
from .theme import tone   # v2.46.1: shared colours
from datetime import datetime

RECORDING_ID_ROLE = Qt.UserRole
PROGRESS_COL = 8   # v2.47: shown second (moved in the header only)

class MainWindow(QMainWindow):
    def __init__(self, db, config, logger):
        super().__init__()
        self.db, self.config, self.logger = db, config, logger
        from PySide6.QtWidgets import QApplication
        from .tooltips import WrappedToolTips
        self._tooltip_filter = WrappedToolTips(self)
        QApplication.instance().installEventFilter(self._tooltip_filter)
        self.current_campaign_id = None
        self.transcription_thread = None
        self.transcription_worker = None
        self.active_recording_id = None
        self._last_saved_progress = -1
        self._last_logged_progress = -5
        self.diarization_thread = None
        self.diarization_worker = None
        self._diarizing_run_id = None
        self._diarization_last_error = False
        self._diarization_status_text = "Ready · Offline-first"
        self._queue_active_task_id = None
        self._queue_active_task_failed = False
        self._queue_running = False
        self._queue_paused = False
        self._open_editors = []
        self.setWindowTitle(f"Local SessionKeeper – v{APP_VERSION}")
        self.resize(1180, 760)
        self.setMinimumSize(720, 560)
        self._build_ui()
        self._gpu_proc = QProcess(self)
        self._gpu_proc.finished.connect(self._gpu_probe_finished)
        self._gpu_proc.errorOccurred.connect(self._gpu_probe_failed)
        self.refresh_campaigns()
        self.refresh_jobs()
        self.refresh_gpu_memory()
        self._gpu_refresh_timer=QTimer(self); self._gpu_refresh_timer.setInterval(10000); self._gpu_refresh_timer.timeout.connect(self.refresh_gpu_memory); self._gpu_refresh_timer.start()
        self.statusBar().showMessage("Ready · Offline-first")
        from .window_state import remember; remember(self)   # v2.46.1: opens at the size and place you left it
        from .notices import attach; attach(self)   # v2.47: yellow note bar at the bottom

    notice_name = "Main window"

    def _notify(self, text):
        """v2.47: a short note in the yellow bar at the bottom of every open window (12 seconds, or until clicked).
        Not the status bar: transcription progress rewrites that every few seconds."""
        from .notices import notify
        notify(text, self)

    # ---- v2.47: menus, right-click, one processing path, drag and drop, progress column ----
    def _build_menus(self):
        """Every command is in a menu, so the window only needs buttons for everyday work. A menu item for a command that
        also has a (possibly hidden) button is greyed out exactly when that button is."""
        bar = self.menuBar(); self._action_buttons = {}

        def add(menu, text, handler, button=None, tip=None, shortcut=None):
            act = menu.addAction(text); act.triggered.connect(lambda _=False: handler())
            if tip or (button is not None and button.toolTip()): act.setToolTip(tip or button.toolTip())
            if shortcut: act.setShortcut(shortcut)
            if button is not None: self._action_buttons[act] = button
            return act

        m = bar.addMenu("&File")
        add(m, "Import recordings…", self.import_recordings, self.import_btn, shortcut="Ctrl+O")
        add(m, "Audio settings…", self.audio_settings, self.audio_settings_btn)
        m.addSeparator(); add(m, "Exit", self.close, shortcut="Ctrl+Q")

        m = bar.addMenu("&Campaign")
        add(m, "New campaign…", self.add_campaign, self.add_campaign_btn)
        add(m, "Edit campaign…", self.edit_campaign, self.edit_campaign_btn)
        add(m, "Delete campaign…", self.delete_campaign, self.delete_campaign_btn)
        m.addSeparator()
        add(m, "Campaign names && character sheets…", self.manage_campaign_dictionary, self.dictionary_btn)
        add(m, "Learn from my edits…", self.learn_from_edits, self.learn_edits_btn)
        m.addSeparator()
        add(m, "Import D&&D books…", self.import_reference_library, self.reference_btn)
        add(m, "D&&D books…", self.manage_books, self.books_btn)
        m.addSeparator()
        add(m, "Index existing Obsidian vault…", self.index_existing_vault, self.scan_vault_btn)
        add(m, "Export campaign to Obsidian…", self.export_campaign, self.export_btn)

        self._recording_menu = m = bar.addMenu("&Recording")
        self._fill_recording_menu(m, add)
        m = bar.addMenu("&Transcript")
        self._fill_transcript_menu(m, add)

        m = bar.addMenu("&Queue")
        add(m, "Start queue", self.start_processing_queue, self.start_queue_btn)
        add(m, "Pause / resume queue", self.pause_processing_queue, self.pause_queue_btn)
        add(m, "Stop after current task", self.stop_processing_queue, self.stop_queue_btn)
        add(m, "■ Stop now", self.stop_everything_now, self.stop_now_btn)
        m.addSeparator()
        add(m, "Clear pending tasks…", self.clear_pending_tasks, self.clear_pending_tasks_btn)
        add(m, "Clear completed…", self.clear_completed_jobs, self.clear_completed_jobs_btn)
        add(m, "Clear history…", self.clear_job_queue, self.clear_queue_btn)
        m.addSeparator()
        add(m, "Refresh GPU memory", self.refresh_gpu_memory, self.refresh_gpu_btn)
        add(m, "Release GPU memory", self.release_gpu_memory, self.release_gpu_btn)

        m = bar.addMenu("&View")
        add(m, "Colours…", self.choose_colours, tip="Light or dark theme, speaker colour scheme and speaker symbols.")
        add(m, "Reset window sizes", self.reset_window_sizes, tip="Every window opens at its default size again.")

        m = bar.addMenu("&Help")
        add(m, "Keyboard shortcuts", self.show_keyboard_shortcuts, shortcut="F1")
        add(m, "About", lambda: QMessageBox.about(self, "About", f"Local SessionKeeper v{APP_VERSION}\nOffline-first campaign manager."))

        for menu in bar.findChildren(type(m)):
            menu.setToolTipsVisible(True)
            menu.aboutToShow.connect(self._sync_action_states)

    def _fill_recording_menu(self, m, add):
        add(m, "🎙 Transcribe", lambda: self.process_selected("transcribe"), self.queue_transcribe_btn)
        add(m, "🗣 Identify speakers (newest version)", lambda: self.process_selected("diarize"), self.queue_diarize_btn)
        add(m, "🎙 + 🗣 Both", lambda: self.process_selected("both"), self.queue_both_btn)
        m.addSeparator()
        add(m, "Identify speakers in a chosen version…", self.diarize_selected, self.diarize_btn)
        add(m, "Recover interrupted transcription…", self.recover_transcription_checkpoint, self.recover_checkpoint_btn)
        m.addSeparator()
        add(m, "▲ Move up", lambda: self.move_selected_recordings(-1), self.move_up_btn)
        add(m, "▼ Move down", lambda: self.move_selected_recordings(1), self.move_down_btn)
        add(m, "Sort by name", self.sort_recordings_by_name, self.sort_name_btn)
        m.addSeparator()
        add(m, "Delete recording…", self.delete_selected_recording, self.delete_recording_btn)

    def _fill_transcript_menu(self, m, add):
        add(m, "Edit transcript", self.view_selected_transcript, self.view_transcript_btn)
        add(m, "Compare versions", self.compare_selected_versions, self.compare_versions_btn)
        add(m, "Delete version…", self.delete_selected_version, self.delete_version_btn)
        m.addSeparator()
        add(m, "Import reviewed work…", self.import_reviewed_work, self.import_review_btn)
        add(m, "Last re-check report", self.show_last_recheck_report, self.last_recheck_btn)

    def _sync_action_states(self):
        self._update_recording_queue_controls()
        for act, button in getattr(self, "_action_buttons", {}).items():
            act.setEnabled(button.isEnabled())

    def _recording_context_menu(self, pos):
        """Right-click a recording: everything that works on it, in one menu."""
        from PySide6.QtWidgets import QMenu
        row = self.recording_table.rowAt(pos.y())
        if row >= 0 and not self.recording_table.selectionModel().isRowSelected(row, self.recording_table.rootIndex()):
            self.recording_table.selectRow(row)
        menu = QMenu(self); menu.setToolTipsVisible(True)
        made = {}

        def add(m, text, handler, button=None, tip=None, shortcut=None):
            act = m.addAction(text); act.triggered.connect(lambda _=False: handler())
            if button is not None:
                made[act] = button
                if button.toolTip(): act.setToolTip(button.toolTip())
            return act

        self._fill_transcript_menu(menu, add); menu.addSeparator(); self._fill_recording_menu(menu, add)
        self._update_recording_queue_controls()
        for act, button in made.items(): act.setEnabled(button.isEnabled())
        menu.exec(self.recording_table.viewport().mapToGlobal(pos))

    def process_selected(self, mode):
        """Transcribe / Identify speakers / Both: put the selected recordings in the queue and start it when nothing runs."""
        ids = self._selected_recording_ids()
        if not ids:
            self._notify("Select one or more recordings first."); return
        before = len(self.db.queued_processing_tasks(True))
        self.queue_selected(mode)
        if len(self.db.queued_processing_tasks(True)) == before: return   # queueing failed (already told)
        if self._queue_running:
            self._notify("Added to the processing queue. It runs after the tasks before it."); return
        if self._audio_worker_running():
            self._start_queue_when_idle = True
            self._notify("Added to the processing queue. It starts by itself when the current task finishes."); return
        self.start_processing_queue()

    def _start_queue_if_waiting(self):
        if getattr(self, "_start_queue_when_idle", False) and not self._queue_running and not self._audio_worker_running():
            self._start_queue_when_idle = False
            QTimer.singleShot(500, self.start_processing_queue)

    def choose_colours(self):
        from .colours_dialog import ColoursDialog
        if ColoursDialog(self.config, self).exec():
            self._notify("Colours saved. Transcript editors that are open show the new speaker colours when opened again.")

    def reset_window_sizes(self):
        from . import window_state
        window_state.reset()
        self._notify("Window sizes reset. Every window opens at its default size the next time it is opened.")

    def show_keyboard_shortcuts(self):
        rows = [("Main window", ""), ("Ctrl+O", "Import recordings"), ("Double-click a recording", "Edit its transcript"),
                ("Right-click a recording", "Everything that works on it"), ("Drop audio files on the window", "Import them"),
                ("Ctrl+Q", "Exit"), ("Transcript editor", ""), ("Ctrl+Space", "Play the selected line, or pause / resume"),
                ("Alt+← / Alt+→", "Select and play the line before / after"), ("Ctrl+Enter", "Tick Reviewed, go to the next unreviewed line"),
                ("Shift+> / Shift+<", "Faster / slower playback (0.25x a step)"), ("Ctrl+J / Ctrl+Shift+J", "Next / previous unreviewed"),
                ("Ctrl+F, F3", "Find, next match"), ("Ctrl+Z / Ctrl+Y", "Undo / redo"), ("F1", "Shortcut list")]
        html = "<table cellspacing='0' cellpadding='3'>" + "".join(
            f"<tr><td colspan='2'><br><b><u>{k}</u></b></td></tr>" if not v else f"<tr><td><b>{k}</b></td><td>{v}</td></tr>" for k, v in rows) + "</table>"
        box = QMessageBox(self); box.setWindowTitle("Keyboard shortcuts"); box.setTextFormat(Qt.RichText); box.setText(html)
        box.setWindowModality(Qt.NonModal); box.show()

    def _media_paths(self, urls):
        allowed = {x.lower() for x in self.config["allowed_extensions"]}
        return [u.toLocalFile() for u in urls if u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in allowed]

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and self._media_paths(event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = self._media_paths(event.mimeData().urls())
        if not paths: return
        event.acceptProposedAction()
        if not self.current_campaign_id:
            self._notify("Create or select a campaign first, then drop the files again."); return
        self._import_paths(paths)

    def _update_empty_hint(self):
        if not self.current_campaign_id:
            text = ("Start here: 1. Press ＋ New on the left to create a campaign. 2. Drop audio files on this window (or ＋ Import "
                    "recordings). 3. Select a recording and press 🎙 Transcribe.")
        elif self.recording_table.rowCount() == 0:
            text = ("No recordings yet. Drop audio or video files on this window, or press ＋ Import recordings. Then select one "
                    "and press 🎙 Transcribe (or 🎙 + 🗣 Both).")
        else:
            text = ""
        self.empty_hint.setText(text); self.empty_hint.setVisible(bool(text))

    @staticmethod
    def _progress_text(run):
        """One line for the Progress column: what is done and the next step."""
        if not run: return "Next: 🎙 Transcribe", "Not transcribed yet."
        total, reviewed = run["segments"], run["reviewed"]
        if not run.get("has_speakers"):
            return "✓ Transcribed → Next: 🗣 Identify speakers", "Transcribed; speakers not identified yet."
        if total and reviewed >= total:
            return "✓ Transcribed → ✓ Speakers → ✓ All reviewed", "Every line is ticked Reviewed."
        pct = f"{reviewed/total*100:.0f}%" if total else "0%"
        return f"✓ Transcribed → ✓ Speakers → {pct} reviewed", "Next: review the speakers in the editor (Ctrl+Enter ticks a line and moves on)."

    def _audio_worker_running(self):
        return bool((self.transcription_thread and self.transcription_thread.isRunning()) or
                    (self.diarization_thread and self.diarization_thread.isRunning()))

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        header = QHBoxLayout()
        title = QLabel("LOCAL SESSIONKEEPER")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        subtitle = QLabel("Offline-first campaign manager")
        tone(subtitle, "dim")
        header.addWidget(title)
        header.addSpacing(18)
        header.addWidget(subtitle)
        header.addStretch()
        self.offline_label = QLabel("● LOCAL MODE")
        tone(self.offline_label, "ok")
        header.addWidget(self.offline_label)
        root.addLayout(header)

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)

        left = QSplitter(Qt.Vertical)
        campaigns_panel = QWidget()
        left_layout = QVBoxLayout(campaigns_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("CAMPAIGNS"))
        self.campaign_list = QListWidget()
        self.campaign_list.currentItemChanged.connect(self.on_campaign_selected)
        left_layout.addWidget(self.campaign_list, 1)
        row = QHBoxLayout()
        self.add_campaign_btn = QPushButton("＋ New")
        self.edit_campaign_btn = QPushButton("Edit")
        self.delete_campaign_btn = QPushButton("Delete")
        self.add_campaign_btn.setToolTip("Create a new campaign.")
        self.edit_campaign_btn.setToolTip("Change the selected campaign's name, game system and description.")
        self.delete_campaign_btn.setToolTip("Remove the selected campaign and its recordings and transcripts from the program (asks first). Audio files are not deleted.")
        self.add_campaign_btn.clicked.connect(self.add_campaign)
        self.edit_campaign_btn.clicked.connect(self.edit_campaign)
        self.delete_campaign_btn.clicked.connect(self.delete_campaign)
        row.addWidget(self.add_campaign_btn)
        row.addWidget(self.edit_campaign_btn)
        row.addWidget(self.delete_campaign_btn)
        left_layout.addLayout(row)
        self.dictionary_btn = QPushButton("Campaign names && character sheets")
        self.dictionary_btn.setToolTip("Campaign names, aliases and categories (used for Whisper hints and Correct names), character sheet "
                                       "imports, and D&D Beyond spells and items.")
        self.dictionary_btn.clicked.connect(self.manage_campaign_dictionary)
        left_layout.addWidget(self.dictionary_btn)
        left.addWidget(campaigns_panel)
        splitter.addWidget(left)

        right = QSplitter(Qt.Vertical)
        right_top = QWidget()
        right_layout = QVBoxLayout(right_top)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.campaign_heading = QLabel("Select or create a campaign")
        self.campaign_heading.setStyleSheet("font-size: 18px; font-weight: 600;")
        right_layout.addWidget(self.campaign_heading)

        def make_btn(text, handler, tip=None, enabled=True):
            btn = QPushButton(text)
            if tip: btn.setToolTip(tip)
            btn.setEnabled(enabled)
            btn.clicked.connect(handler)
            return btn

        self.import_btn = make_btn("＋ Import recordings", self.import_recordings,
            "Add audio or video files to the selected campaign. The files stay where they are; only their location is stored.")
        self.audio_settings_btn = make_btn("Audio settings", self.audio_settings,
            "Whisper model, device and accuracy options, audio levelling, and speaker identification settings.")
        self.transcribe_btn = make_btn("Transcribe selected", self.transcribe_selected,
            "Transcribe the selected recording now with Whisper. Each run is saved as a new transcript version; earlier versions are kept.")
        self.pause_transcription_btn = make_btn("Pause", self.toggle_transcription_pause,
            "Pause or resume the transcription started with Transcribe selected.", enabled=False)
        self.stop_transcription_btn = make_btn("Stop", self.stop_transcription,
            "Stop the running transcription. What was transcribed so far is saved as a partial version.", enabled=False)
        self.recover_checkpoint_btn = make_btn("Recover checkpoint", self.recover_transcription_checkpoint,
            "Save the finished part of a transcription that was interrupted (crash or closed program) as a partial version.")
        self.view_transcript_btn = make_btn("Edit transcript", self.view_selected_transcript,
            "Open the transcript version the ◀ ▶ arrows show for the selected recording in the editor. Hold Shift while clicking to choose from a list.")
        self.compare_versions_btn = make_btn("Compare versions", self.compare_selected_versions,
            "Show two to four transcript versions of the selected recording side by side. Greyed out when it has only one version.")
        self.delete_version_btn = make_btn("Delete version…", self.delete_selected_version,
            "Permanently delete one transcript version (asks to confirm; the last remaining version is protected; the recording itself is never deleted).")
        self.diarize_btn = make_btn("Identify / re-check speakers", self.diarize_selected,
            "Run speaker identification on a transcript version of the selected recording. Reviewed clips are kept and used to recognise voices.")
        self.import_review_btn = make_btn("Import reviewed work…", self.import_reviewed_work,
            "Copy the clips you reviewed (speakers, and optionally your corrected text and timestamps) from one transcript version onto another version of the same recording.")
        self.last_recheck_btn = make_btn("Last re-check report", self.show_last_recheck_report,
            "Show the summary of the last speaker identification / re-check of the selected recording: what it changed and how sure it was.")
        self.learn_edits_btn = make_btn("Learn from my edits…", self.learn_from_edits,
            "Find words you keep correcting in the editor and add them to the campaign vocabulary.")
        # v2.46: "&&" shows one "&" (a single & on a button marks the Alt shortcut letter, so it showed "DD")
        self.reference_btn = make_btn("Import D&&D books…", self.import_reference_library,
            "Read D&D books or notes (PDF, Markdown, text) in one window: title, D&D Beyond match, category and the names each book uses.")
        self.books_btn = make_btn("D&&D books…", self.manage_books,
            "List the books in the reference library: re-link one to another file, re-read it, change its category, review its "
            "Whisper words or remove it. Flags missing files, changed files, newer copies and books imported twice.")
        self.scan_vault_btn = make_btn("Index existing Obsidian vault", self.index_existing_vault,
            "Read your Obsidian vault (read-only) to find campaign names and notes. Nothing in the vault is changed.")
        self.export_btn = make_btn("Export campaign to Obsidian", self.export_campaign,
            "Write the campaign and a transcript note per recording into an Obsidian folder. Notes you have edited are never overwritten.")

        def group(title, *rows):
            box = QGroupBox(title); lay = QVBoxLayout(box); lay.setContentsMargins(8, 6, 8, 8); lay.setSpacing(4)
            for row in rows:
                if isinstance(row, (tuple, list)):
                    h = QHBoxLayout(); h.setSpacing(4)
                    for w in row: h.addWidget(w)
                    lay.addLayout(h)
                else:
                    lay.addWidget(row)
            lay.addStretch()
            return box

        toolbar = QHBoxLayout(); toolbar.setSpacing(8)
        self.move_up_btn = make_btn("▲ Move up", lambda: self.move_selected_recordings(-1), "Move the selected recording(s) up the list")
        self.move_down_btn = make_btn("▼ Move down", lambda: self.move_selected_recordings(1), "Move the selected recording(s) down the list")
        self.sort_name_btn = make_btn("Sort by name", self.sort_recordings_by_name, "Put the recordings in name order (Session 2 before Session 10)")
        self.delete_recording_btn = make_btn("Delete recording…", self.delete_selected_recording,
            "Remove the selected recording from the program with all its transcript versions and edit history (asks first, and saves a backup "
            "of the transcripts in backups\\deleted_recordings). The audio file itself is never deleted.")
        # v2.47: one way to process a recording (the queue: it starts by itself when idle), and only the everyday buttons on
        # screen. Everything else is in the menus at the top and on the right-click menu of a recording.
        self.queue_transcribe_btn=QPushButton("🎙 Transcribe")
        self.queue_transcribe_btn.setToolTip("Transcribe the selected recording(s) with Whisper. They go into the processing queue, which starts "
                                             "at once when nothing else is running. Each run is saved as a new transcript version; earlier versions are kept.")
        self.queue_transcribe_btn.clicked.connect(lambda: self.process_selected("transcribe"))
        self.queue_diarize_btn=QPushButton("🗣 Identify speakers")
        self.queue_diarize_btn.setToolTip("Identify speakers in the newest transcript version of the selected recording(s), through the queue. "
                                          "Reviewed lines are kept and used to recognise voices. To choose another version: Recording menu → "
                                          "Identify speakers in a chosen version….")
        self.queue_diarize_btn.clicked.connect(lambda: self.process_selected("diarize"))
        self.queue_both_btn=QPushButton("🎙 + 🗣 Both")
        self.queue_both_btn.setToolTip("Transcribe, then identify speakers in that new transcript, through the queue.")
        self.queue_both_btn.clicked.connect(lambda: self.process_selected("both"))
        self.diarize_btn.setText("Identify speakers in a chosen version…")
        self._toolbar = toolbar; self._group = group
        toolbar.addWidget(group("Recordings", self.import_btn, self.audio_settings_btn))
        # the "Process selected" group is added once the queue buttons exist (below)
        toolbar.addWidget(group("Transcript", self.view_transcript_btn, self.compare_versions_btn))
        toolbar.addWidget(group("Campaign knowledge", self.books_btn, self.export_btn))
        toolbar.addStretch()
        right_layout.addLayout(toolbar)
        # Buttons that moved to the menus stay as (hidden) objects: their greyed-out state is still worked out the same
        # way and copied to the menu items.
        self._hidden_buttons = QWidget(self); self._hidden_buttons.hide()
        for b in (self.transcribe_btn, self.pause_transcription_btn, self.stop_transcription_btn, self.recover_checkpoint_btn,
                  self.delete_version_btn, self.diarize_btn, self.import_review_btn, self.last_recheck_btn, self.learn_edits_btn,
                  self.reference_btn, self.scan_vault_btn, self.move_up_btn, self.move_down_btn, self.sort_name_btn, self.delete_recording_btn):
            b.setParent(self._hidden_buttons)

        self.recording_table = QTableWidget(0, 9)
        self.recording_table.setHorizontalHeaderLabels(["Recording", "Size", "Imported", "Status", "Transcription", "Speaker review", "Voice samples", "Error", "Progress"])
        self._setup_resizable_columns(self.recording_table, "recordings")
        # v2.47: Progress is the last column in the data but shown second, so nothing that reads the other columns moves
        self.recording_table.horizontalHeader().moveSection(PROGRESS_COL, 1)
        self.recording_table.horizontalHeaderItem(PROGRESS_COL).setToolTip(
            "Where each recording is: transcribed, speakers identified, how much you have reviewed, and the next step. "
            "For the transcript version the ◀ ▶ arrows show.")
        self.recording_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.recording_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.recording_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.recording_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.recording_table.customContextMenuRequested.connect(self._recording_context_menu)
        self.recording_table.doubleClicked.connect(lambda *_: self.view_selected_transcript() if self.view_transcript_btn.isEnabled() else None)
        right_layout.addWidget(QLabel("RECORDINGS"))
        # v2.47: what to do first, while there is nothing in the list
        self.empty_hint = tone(QLabel(""), "info"); self.empty_hint.setWordWrap(True)
        right_layout.addWidget(self.empty_hint)
        right_layout.addWidget(self.recording_table, 1)
        self.setAcceptDrops(True)   # v2.47: drop audio files on the window to import them

        self.queue_summary = QLabel("Processing queue: 0 queued · 0 running · 0 failed")
        self.queue_summary.setStyleSheet("font-weight: 600;")
        self.progress_label = QLabel("Ready · no active processing")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.jobs_table = QTableWidget(0, 5)
        self.jobs_table.setHorizontalHeaderLabels(["Campaign", "Recording", "Stage", "Last error", "Order"])
        self._setup_resizable_columns(self.jobs_table, "jobs")
        self.jobs_table.horizontalHeader().setStretchLastSection(False)
        self.jobs_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.jobs_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.clear_completed_jobs_btn = QPushButton("Clear completed")
        self.clear_completed_jobs_btn.setToolTip("Remove finished and partial entries from the queue list (asks first). Recordings and transcripts are not touched.")
        self.clear_completed_jobs_btn.clicked.connect(self.clear_completed_jobs)
        self.clear_queue_btn = QPushButton("Clear history")
        self.clear_queue_btn.setToolTip("Remove every queued, finished and failed entry from the queue list (asks first). A running task is kept; recordings and transcripts are not touched.")
        self.clear_queue_btn.clicked.connect(self.clear_job_queue)
        queue_header = QHBoxLayout()
        queue_header.addWidget(QLabel("PROCESSING QUEUE"))
        queue_header.addWidget(self.queue_summary, 1)
        queue_header.addWidget(self.clear_completed_jobs_btn)
        queue_header.addWidget(self.clear_queue_btn)

        # queue controls and GPU live under the campaigns, so the recordings keep the room
        self.start_queue_btn=QPushButton("Start")
        self.start_queue_btn.setToolTip("Start working through the processing queue, one task at a time.")
        self.start_queue_btn.clicked.connect(self.start_processing_queue)
        self.pause_queue_btn=QPushButton("Pause")
        self.pause_queue_btn.setToolTip("Pause the queue after the running task (press again to resume). With no queue running, pauses a Transcribe selected run.")
        self.pause_queue_btn.clicked.connect(self.pause_processing_queue)
        self.stop_queue_btn=QPushButton("Stop after current")
        self.stop_queue_btn.setToolTip("Stop the queue after the running task finishes. Pending tasks are kept.")
        self.stop_queue_btn.clicked.connect(self.stop_processing_queue)
        self.stop_now_btn=QPushButton("■ Stop now")
        self.stop_now_btn.setToolTip("Stop the queue AND the running transcription now. What was transcribed so far is saved as a partial version.")
        self.stop_now_btn.clicked.connect(self.stop_everything_now)
        self.clear_pending_tasks_btn=QPushButton("Clear pending")
        self.clear_pending_tasks_btn.setToolTip("Remove queued and failed tasks (asks first). A running task and all recordings and transcripts are kept.")
        self.clear_pending_tasks_btn.clicked.connect(self.clear_pending_tasks)
        self.gpu_memory_label=QLabel("GPU memory: not checked")
        self.gpu_memory_label.setWordWrap(True)
        self.refresh_gpu_btn=QPushButton("Refresh GPU")
        self.refresh_gpu_btn.setToolTip("Check the graphics card's memory use now (it is also checked every 10 seconds).")
        self.refresh_gpu_btn.clicked.connect(self.refresh_gpu_memory)
        self.release_gpu_btn=QPushButton("Release GPU")
        self.release_gpu_btn.setToolTip("Free graphics card memory this program is still holding after a transcription or speaker identification. Only when nothing is running.")
        self.release_gpu_btn.clicked.connect(self.release_gpu_memory)
        self.start_queue_btn.setText("Start queue")
        self.pause_queue_btn.setToolTip("Pause the queue after the running task (press again to resume).")
        # v2.47: Pause and Stop sit with Transcribe, so there is one set of controls for everything that runs
        self._toolbar.insertWidget(1, self._group("Process selected", (self.queue_transcribe_btn, self.queue_diarize_btn, self.queue_both_btn),
                                                  (self.pause_queue_btn, self.stop_queue_btn, self.stop_now_btn)))
        queue_box = QGroupBox("Queue && GPU")
        queue_grid = QGridLayout(queue_box)
        queue_grid.setSpacing(4)
        queue_grid.addWidget(self.start_queue_btn, 0, 0); queue_grid.addWidget(self.clear_pending_tasks_btn, 0, 1)
        queue_grid.addWidget(self.gpu_memory_label, 1, 0, 1, 2)
        queue_grid.addWidget(self.refresh_gpu_btn, 2, 0)
        queue_grid.addWidget(self.release_gpu_btn, 2, 1)
        queue_grid.setRowStretch(3, 1)
        left.addWidget(queue_box)
        left.setStretchFactor(0, 1); left.setStretchFactor(1, 0)
        left.setSizes([480, 240])

        self.jobs_table.itemSelectionChanged.connect(self._update_queue_selection_controls)
        self.recording_table.itemSelectionChanged.connect(self._update_recording_queue_controls)
        self._update_recording_queue_controls()

        right_bottom = QWidget()
        bottom_layout = QVBoxLayout(right_bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.addLayout(queue_header)
        bottom_layout.addWidget(self.progress_label)
        bottom_layout.addWidget(self.progress_bar)
        bottom_layout.addWidget(self.jobs_table, 1)
        right.addWidget(right_top)
        right.addWidget(right_bottom)
        right.setStretchFactor(0, 3); right.setStretchFactor(1, 1)
        right.setSizes([560, 220])
        right.setChildrenCollapsible(False)
        right.setHandleWidth(10)
        right.setStyleSheet("QSplitter::handle:vertical { background: palette(mid); margin: 3px 80px; border-radius: 2px; }")
        # The hint belongs on the bar only: set on the whole panel, every button without its own
        # tooltip showed it (v2.31.3 fix).
        right.handle(1).setToolTip("Drag this bar to give Recordings or the processing queue more room.")
        right_top.setMinimumHeight(180); right_bottom.setMinimumHeight(130)
        splitter.addWidget(right)
        splitter.setSizes([300, 900])

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready")
        self._build_menus()

    def refresh_campaigns(self, select_id=None):
        self.campaign_list.blockSignals(True)
        self.campaign_list.clear()
        rows = self.db.campaigns()
        target = select_id or self.current_campaign_id
        selected = None
        for row in rows:
            item = QListWidgetItem(f"{row['name']}  ({row['recording_count']})")
            item.setData(Qt.UserRole, row["id"])
            self.campaign_list.addItem(item)
            if row["id"] == target:
                selected = item
        self.campaign_list.blockSignals(False)
        if selected:
            self.campaign_list.setCurrentItem(selected)
        elif self.campaign_list.count():
            self.campaign_list.setCurrentRow(0)
        else:
            self.current_campaign_id = None
            self.refresh_recordings()

    def on_campaign_selected(self, current, previous):
        if not current:
            return
        self.current_campaign_id = current.data(Qt.UserRole)
        campaign = self.db.get_campaign(self.current_campaign_id)
        self.campaign_heading.setText(f"{campaign['name']}  ·  {campaign['game_system'] or 'Game system not set'}")
        self.refresh_recordings()

    def add_campaign(self):
        dialog = CampaignDialog(self)
        if dialog.exec():
            name, system, description = dialog.values()
            cid = self.db.create_campaign(name, system, description)
            self.logger.info("Created campaign: %s", name)
            self.refresh_campaigns(cid)

    def edit_campaign(self):
        if not self.current_campaign_id:
            return
        campaign = self.db.get_campaign(self.current_campaign_id)
        dialog = CampaignDialog(self, campaign)
        if dialog.exec():
            self.db.update_campaign(self.current_campaign_id, *dialog.values())
            self.logger.info("Updated campaign: %s", dialog.values()[0])
            self.refresh_campaigns(self.current_campaign_id)

    def delete_campaign(self):
        if not self.current_campaign_id:
            return
        campaign = self.db.get_campaign(self.current_campaign_id)
        answer = QMessageBox.question(
            self, "Delete campaign",
            f"Delete '{campaign['name']}' and its database recording/job entries?\n\nOriginal audio files will NOT be deleted.",
            QMessageBox.Yes | QMessageBox.No
        )
        if answer == QMessageBox.Yes:
            cid = self.current_campaign_id
            self.db.delete_campaign(cid)
            self.current_campaign_id = None
            self.logger.warning("Deleted campaign record: %s", campaign["name"])
            self.refresh_campaigns()

    def import_recordings(self):
        if not self.current_campaign_id:
            self._notify("Create or select a campaign first.")
            return
        extensions = " ".join("*" + x for x in self.config["allowed_extensions"])
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import session recordings", "", f"Supported media ({extensions});;All files (*.*)"
        )
        if paths: self._import_paths(paths)

    def _import_paths(self, paths):
        added, duplicates, errors = 0, 0, []
        for path in paths:
            try:
                ok, _ = self.db.add_recording(self.current_campaign_id, path)
                if ok:
                    added += 1
                    self.logger.info("Imported recording: %s", path)
                else:
                    duplicates += 1
            except Exception as exc:
                errors.append(f"{Path(path).name}: {exc}")
                self.logger.exception("Failed to import recording: %s", path)
        self.refresh_recordings()
        self.refresh_campaigns(self.current_campaign_id)
        self.refresh_jobs()
        if errors:
            QMessageBox.warning(self, "Import", f"Imported: {added}\nAlready imported: {duplicates}\n\nErrors:\n" + "\n".join(errors[:10]))
        else:
            self._notify(f"Imported {added} recording(s)." + (f" {duplicates} already imported." if duplicates else ""))

    # ---- columns start fitted to their text; dragging a header edge keeps that width from then on
    def _setup_resizable_columns(self, table, key):
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        header.setMinimumSectionSize(50)
        self._manual_columns = getattr(self, "_manual_columns", {})
        self._fitting = False
        self._manual_columns[key] = {}
        header.sectionResized.connect(lambda col, old, new, k=key: None if self._fitting else self._manual_columns[k].__setitem__(col, new))

    def _autofit_columns(self, table, key, max_width=420):
        manual = self._manual_columns.get(key, {})
        self._fitting = True
        try:
            table.resizeColumnsToContents()
            for col in range(table.columnCount()):
                if col in manual:
                    table.setColumnWidth(col, manual[col])
                elif table.columnWidth(col) > max_width:
                    table.setColumnWidth(col, max_width)
        finally:
            self._fitting = False

    def _transcription_cell(self, rec_id, runs):
        """Gallery control for a recording's transcriptions: ◀ 2/3 ▶ steps the stats columns through them."""
        box = QWidget(); lay = QHBoxLayout(box); lay.setContentsMargins(2, 0, 2, 0); lay.setSpacing(2)
        prev_b, next_b = QToolButton(), QToolButton()
        prev_b.setText("◀"); next_b.setText("▶")
        label = QLabel("—"); label.setAlignment(Qt.AlignCenter)
        for b in (prev_b, next_b): b.setAutoRaise(True)
        prev_b.clicked.connect(lambda _=False, r=rec_id: self._step_stats(r, -1))
        next_b.clicked.connect(lambda _=False, r=rec_id: self._step_stats(r, 1))
        lay.addWidget(prev_b); lay.addWidget(label, 1); lay.addWidget(next_b)
        box.label, box.prev_b, box.next_b = label, prev_b, next_b
        prev_b.setEnabled(len(runs) > 1); next_b.setEnabled(len(runs) > 1)
        return box

    def _apply_stats_row(self, r, rec_id):
        runs = self._run_stats.get(rec_id, [])
        pos = min(self._stats_pos.get(rec_id, 0), max(len(runs) - 1, 0))
        self._stats_pos[rec_id] = pos
        review_label = sample_label = "—"; cell = "—"; tip = "No transcription yet"
        if runs:
            run = runs[pos]; total, reviewed, samples = run["segments"], run["reviewed"], run["samples"]
            if total:
                review_label = f"{reviewed/total*100:.0f}% ({reviewed}/{total})"; sample_label = f"{samples/total*100:.0f}% ({samples}/{total})"
            cell = f"{pos+1}/{len(runs)}" + (" newest" if pos == 0 else "")
            tip = f"Transcription {pos+1} of {len(runs)} · {run['model']} · {run['created_at'][:16].replace('T', ' ')}\nUse the arrows to show the stats of another transcription."
        widget = self.recording_table.cellWidget(r, 4)
        if widget is not None:
            widget.label.setText(cell); widget.setToolTip(tip)
        for col, text in ((5, review_label), (6, sample_label)):
            item = self.recording_table.item(r, col)
            if item is not None: item.setText(text); item.setToolTip(tip)
        item = self.recording_table.item(r, PROGRESS_COL)   # v2.47
        if item is not None:
            text, why = self._progress_text(runs[pos] if runs else None)
            item.setText(text); item.setToolTip(why)

    def _step_stats(self, rec_id, direction):
        runs = self._run_stats.get(rec_id, [])
        if len(runs) < 2: return
        self._stats_pos[rec_id] = (self._stats_pos.get(rec_id, 0) + direction) % len(runs)
        for r in range(self.recording_table.rowCount()):
            item = self.recording_table.item(r, 0)
            if item is not None and item.data(RECORDING_ID_ROLE) == rec_id:
                self._apply_stats_row(r, rec_id); break

    def refresh_recordings(self):
        # v2.31.2: the list is rebuilt from scratch, so remember which recordings were selected (by id,
        # not row number) and where the list was scrolled, and put both back afterwards.
        keep_ids = self._selected_recording_ids() if self.recording_table.rowCount() else []
        current_id = self.selected_recording_id() if self.recording_table.rowCount() else None
        scroll = self.recording_table.verticalScrollBar().value()
        self._rebuild_recordings()
        if keep_ids:
            self._select_recordings(keep_ids)
            for r in range(self.recording_table.rowCount()):
                item = self.recording_table.item(r, 0)
                if item is not None and item.data(RECORDING_ID_ROLE) == current_id:
                    model = self.recording_table.selectionModel()
                    model.setCurrentIndex(self.recording_table.model().index(r, 0), QItemSelectionModel.NoUpdate)
                    break
        self.recording_table.verticalScrollBar().setValue(scroll)
        self._update_recording_queue_controls()

    def _rebuild_recordings(self):
        self._run_stats = {}; self._stats_pos = getattr(self, "_stats_pos", {})
        self.recording_table.setRowCount(0)
        if not self.current_campaign_id:
            self._update_empty_hint()
            return
        for row in self.db.recordings(self.current_campaign_id):
            r = self.recording_table.rowCount()
            self.recording_table.insertRow(r)
            size_mb = row["file_size"] / (1024 * 1024)
            self._run_stats[row["id"]] = self.db.run_stats_list(row["id"])
            values = [row["filename"], f"{size_mb:.1f} MB", row["imported_at"], row["job_stage"] or row["status"], "", "—", "—", row["last_error"] or "", ""]
            for c, value in enumerate(values):
                self.recording_table.setItem(r, c, QTableWidgetItem(str(value)))
            self.recording_table.setCellWidget(r, 4, self._transcription_cell(row["id"], self._run_stats[row["id"]]))
            # The row's recording id travels with the row, so sorting or a refresh can never
            # make a selected row point at a different recording.
            self.recording_table.item(r, 0).setData(RECORDING_ID_ROLE, row["id"])
            self._apply_stats_row(r, row["id"])
        self._autofit_columns(self.recording_table, "recordings")
        self.recording_table.setColumnWidth(4, max(self.recording_table.columnWidth(4), 130))
        self._update_empty_hint()

    def _update_queue_selection_controls(self):
        # Reordering is available directly on each queued task row.
        return

    def _update_recording_queue_controls(self):
        if not hasattr(self,"queue_transcribe_btn"): return
        selected=bool(self.recording_table.selectionModel().selectedRows()) or self.recording_table.currentRow()>=0
        for button in (self.queue_transcribe_btn,self.queue_diarize_btn,self.queue_both_btn): button.setEnabled(selected)
        if hasattr(self,"compare_versions_btn"):
            rid=self.selected_recording_id() if selected else None
            versions=len(getattr(self,"_run_stats",{}).get(rid,[])) if rid else 0
            for button in (self.compare_versions_btn,self.delete_version_btn,self.import_review_btn): button.setEnabled(versions>1)
            # v2.46: every button that works on the selected recording is greyed out until one is selected (and, for the
            # ones that need a transcript, until it has one)
            for button in (self.transcribe_btn,self.delete_recording_btn,self.move_up_btn,self.move_down_btn): button.setEnabled(selected)
            for button in (self.view_transcript_btn,self.diarize_btn,self.last_recheck_btn): button.setEnabled(versions>=1)
            self.view_transcript_btn.setToolTip(("Open the transcript version the ◀ ▶ arrows show for the selected recording in the "
                                                 "editor. Hold Shift while clicking to choose from a list.") if versions else
                                                ("Select a recording first." if not rid else "This recording has no transcript yet: Transcribe it first."))

    def refresh_jobs(self):
        jobs = self.db.all_jobs()
        counts = {}
        for job in jobs:
            counts[job["stage"]] = counts.get(job["stage"], 0) + 1
        self.queue_summary.setText(
            f"Processing queue: {counts.get('Queued',0)} queued · "
            f"{counts.get('Running',0)} running · {counts.get('Failed',0)} failed"
        )
        self.jobs_table.setRowCount(0)
        for job in jobs:
            r = self.jobs_table.rowCount()
            self.jobs_table.insertRow(r)
            for c, value in enumerate([
                job["campaign_name"], job["filename"],
                (f"{job['stage']} · {job['progress']:.0f}%" if job['stage'] == 'Running' else job["stage"]),
                job["last_error"] or ""
            ]):
                self.jobs_table.setItem(r, c, QTableWidgetItem(str(value)))
        tasks=self.db.queued_processing_tasks(True)
        pending=sum(1 for task in tasks if task["stage"]=="Queued")
        running=sum(1 for task in tasks if task["stage"]=="Running")
        if hasattr(self,"queue_summary"):
            self.queue_summary.setText(self.queue_summary.text()+f" · Tasks: {pending} queued / {running} running")
        for task in tasks:
            r=self.jobs_table.rowCount(); self.jobs_table.insertRow(r)
            label={"transcribe":"Whisper transcription","diarize":"Speaker identification"}.get(task["task_type"],task["task_type"])
            values=[task["campaign_name"],task["filename"],f"Task · {label} · {task['stage']}",task["last_error"] or ""]
            for c,value in enumerate(values): self.jobs_table.setItem(r,c,QTableWidgetItem(str(value)))
            self.jobs_table.item(r,0).setData(Qt.UserRole,task["id"])
            order_widget=QWidget(); order_layout=QHBoxLayout(order_widget); order_layout.setContentsMargins(2,0,2,0); order_layout.setSpacing(2)
            up_btn=QPushButton("↑"); up_btn.setFixedWidth(28); up_btn.setToolTip("Move this queued task earlier")
            down_btn=QPushButton("↓"); down_btn.setFixedWidth(28); down_btn.setToolTip("Move this queued task later")
            movable=task["stage"]=="Queued"
            up_btn.setEnabled(movable); down_btn.setEnabled(movable)
            up_btn.clicked.connect(lambda checked=False,tid=task["id"]: self._move_task_id(tid,-1))
            down_btn.clicked.connect(lambda checked=False,tid=task["id"]: self._move_task_id(tid,1))
            order_layout.addWidget(up_btn); order_layout.addWidget(down_btn)
            self.jobs_table.setCellWidget(r,4,order_widget)
        self._autofit_columns(self.jobs_table, "jobs")
        self._update_queue_selection_controls()

    def _move_task_id(self,task_id,direction):
        if self.db.move_processing_task(task_id,direction): self.refresh_jobs()

    def move_selected_task(self,direction):
        row=self.jobs_table.currentRow()
        item=self.jobs_table.item(row,0) if row>=0 else None
        task_id=item.data(Qt.UserRole) if item else None
        if not task_id:
            self._notify("Select a scheduled task row first."); return
        if self.db.move_processing_task(task_id,direction): self.refresh_jobs()

    def pause_processing_queue(self):
        if not self._queue_running:
            if self.transcription_worker is not None:  # a transcription started with "Transcribe selected"
                self.toggle_transcription_pause(); return
            self._notify("Nothing is running to pause."); return
        self._queue_paused=not self._queue_paused
        self.pause_queue_btn.setText("Resume queue" if self._queue_paused else "Pause queue")
        self.statusBar().showMessage("Queue paused after current task" if self._queue_paused else "Queue resumed",8000)
        if not self._queue_paused and not self._queue_active_task_id: QTimer.singleShot(0,self._start_next_processing_task)

    def stop_everything_now(self):
        """Stop the queue and, if a transcription is running (queued or started by hand), stop it and save what is done."""
        if not self._queue_running and self.transcription_worker is None:
            if self.diarization_thread is not None and self.diarization_thread.isRunning():
                QMessageBox.information(self,"Speaker identification","Speaker identification cannot be interrupted safely; it will finish on its own. Nothing else is running.")
            else:
                self._notify("Nothing is running.")
            return
        self.stop_processing_queue()
        if self.transcription_worker is not None:
            self.stop_transcription()

    def stop_processing_queue(self):
        self._queue_running=False; self._queue_paused=False
        self.pause_queue_btn.setText("Pause queue"); self.start_queue_btn.setEnabled(True)
        self.statusBar().showMessage("Queue stopped · current task, if any, will finish; pending tasks are retained",10000)

    def _selected_recording_ids(self):
        rows=sorted({index.row() for index in self.recording_table.selectionModel().selectedRows()})
        if not rows and self.recording_table.currentRow()>=0: rows=[self.recording_table.currentRow()]
        ids=[]
        for row in rows:
            item=self.recording_table.item(row,0)
            if item is not None and item.data(RECORDING_ID_ROLE): ids.append(item.data(RECORDING_ID_ROLE))
        return ids

    def queue_selected(self, mode):
        ids=self._selected_recording_ids()
        if not ids:
            self._notify("Select one or more recordings first."); return
        try:
            added=0
            for rid in ids:
                if mode in {"transcribe","both"}: self.db.add_processing_task(rid,"transcribe"); added+=1
                if mode in {"diarize","both"}: self.db.add_processing_task(rid,"diarize"); added+=1
            self.logger.info("Queued %s processing task(s) for %s recording(s)", added, len(ids))
            self.refresh_jobs()
        except Exception as exc: QMessageBox.critical(self,"Could not queue task",str(exc))

    def clear_pending_tasks(self):
        if QMessageBox.question(self,"Clear pending tasks","Remove queued and failed tasks? Running tasks and recordings/transcripts are not affected.",QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        removed=self.db.clear_pending_processing_tasks(); self.logger.info("Cleared %s pending processing tasks",removed); self.refresh_jobs()

    def start_processing_queue(self):
        if self._queue_running:
            self._notify("The processing queue is already running."); return
        if self._audio_worker_running():
            QMessageBox.information(self,"Busy","Wait for the current manual processing task to finish before starting the queue."); return
        self._queue_running=True; self._queue_paused=False; self.pause_queue_btn.setText("Pause queue"); self.start_queue_btn.setEnabled(False); self._start_next_processing_task()

    def _select_recording_row(self, rid):
        for r in range(self.recording_table.rowCount()):
            item=self.recording_table.item(r,0)
            if item is not None and item.data(RECORDING_ID_ROLE)==rid:
                self.recording_table.selectRow(r); return True
        return False

    def _start_next_processing_task(self):
        if not self._queue_running or self._queue_paused:return
        task=self.db.next_processing_task()
        if not task:
            self._queue_running=False; self._queue_paused=False; self._queue_active_task_id=None; self.start_queue_btn.setEnabled(True); self.pause_queue_btn.setText("Pause queue"); self.statusBar().showMessage("Processing queue finished",10000); self.refresh_jobs(); return
        task_id=task["id"]; rid=task["recording_id"]
        rec=self.db.get_recording(rid)
        if not rec or not Path(rec["original_path"]).is_file():
            self.db.update_processing_task(task_id,"Failed","Recording file is missing")
            QTimer.singleShot(0,self._start_next_processing_task); return
        campaign_id=rec["campaign_id"]
        if campaign_id!=self.current_campaign_id:
            self.refresh_campaigns(campaign_id)
        if not self._select_recording_row(rid):
            self.db.update_processing_task(task_id,"Failed","Recording is not available in the selected campaign")
            QTimer.singleShot(0,self._start_next_processing_task); return
        self._queue_active_task_id=task_id; self._queue_active_task_failed=False
        self.db.update_processing_task(task_id,"Running")
        self.refresh_jobs()
        self.logger.info("Starting queued task %s for %s",task["task_type"],rec["filename"])
        if task["task_type"]=="transcribe": self.transcribe_selected()
        else: self.diarize_selected(queued=True)
        # If worker did not start (e.g. cancelled credentials), advance queue safely.
        if not self._audio_worker_running():
            if self._queue_active_task_id:
                self.db.update_processing_task(self._queue_active_task_id,"Failed","Task did not start; check prerequisites or credentials")
                self._queue_active_task_id=None; QTimer.singleShot(0,self._start_next_processing_task)

    def _finish_queued_task(self, failed=False, error=""):
        task_id=self._queue_active_task_id
        if task_id:
            self.db.update_processing_task(task_id,"Failed" if failed else "Completed",error)
            self._queue_active_task_id=None
            self.refresh_jobs()
            if self._queue_running and not self._queue_paused: QTimer.singleShot(350,self._start_next_processing_task)

    def refresh_gpu_memory(self):
        # nvidia-smi can take seconds on a busy GPU or a sleeping driver. Run it as an
        # asynchronous process so the window never waits for it.
        if self._gpu_proc.state()!=QProcess.NotRunning: return
        self._gpu_proc.start("nvidia-smi",["--query-gpu=memory.used,memory.total","--format=csv,noheader,nounits"])
        QTimer.singleShot(5000,self._gpu_probe_timeout)

    def _gpu_probe_timeout(self):
        if self._gpu_proc.state()!=QProcess.NotRunning: self._gpu_proc.kill()

    def _gpu_probe_finished(self, exit_code=0, exit_status=None):
        try:
            output=bytes(self._gpu_proc.readAllStandardOutput()).decode("utf-8",errors="replace")
            used,total=[int(x.strip()) for x in output.strip().splitlines()[0].split(",")[:2]]
            active=" · SessionKeeper processing active" if self._audio_worker_running() else " · no SessionKeeper audio worker active"
            self.gpu_memory_label.setText(f"GPU memory: {used/1024:.1f}/{total/1024:.1f} GB ({used/total*100:.0f}%)"+active)
        except Exception:
            self._gpu_probe_failed()

    def _gpu_probe_failed(self, *_):
        self.gpu_memory_label.setText("GPU memory: unavailable (nvidia-smi not found or GPU unsupported)")

    def release_gpu_memory(self):
        if self._audio_worker_running():
            QMessageBox.information(self,"GPU memory in use","Wait until transcription and speaker identification have finished before releasing cached GPU memory."); return
        try:
            import gc; gc.collect()
            import torch
            if torch.cuda.is_available():
                try: torch.cuda.synchronize()
                except Exception: pass
                torch.cuda.empty_cache()
                try: torch.cuda.ipc_collect()
                except Exception: pass
            self.logger.info("Requested release of cached PyTorch GPU memory")
            self.refresh_gpu_memory()
            QMessageBox.information(self,"GPU memory release requested","Python garbage collection and the PyTorch CUDA cache cleanup have been requested. CTranslate2 or other applications may still retain GPU allocations; this action cannot reclaim memory owned by another process.")
        except Exception as exc:
            QMessageBox.warning(self,"GPU memory release unavailable",f"Could not run the PyTorch CUDA cache cleanup.\n\n{exc}")

    def clear_completed_jobs(self):
        answer = QMessageBox.question(self, "Clear completed jobs", "Remove completed and partial jobs from the persistent queue? Recordings and transcripts will not be deleted.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes: return
        removed = self.db.clear_processing_jobs(stages=("Completed", "Partial"))
        task_removed=self.db.clear_processing_task_history()
        self.logger.info("Cleared %s completed/partial processing jobs and %s task-history rows", removed, task_removed)
        self.refresh_jobs(); self.refresh_recordings()

    def clear_job_queue(self):
        answer = QMessageBox.question(self, "Clear processing queue", "Remove all queued, completed, partial and failed job entries? A currently running job is protected. Recordings and transcripts will not be deleted.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes: return
        removed = self.db.clear_processing_jobs(stages=("Queued", "Completed", "Partial", "Failed"))
        task_removed=self.db.clear_pending_processing_tasks()
        history_removed=self.db.clear_processing_task_history()
        self.logger.info("Cleared %s legacy job rows, %s pending tasks and %s task-history rows", removed, task_removed, history_removed)
        self.refresh_jobs(); self.refresh_recordings()

    def selected_recording_ids(self):
        ids = []
        for index in self.recording_table.selectionModel().selectedRows():
            item = self.recording_table.item(index.row(), 0)
            if item is not None:
                ids.append(item.data(RECORDING_ID_ROLE))
        return ids

    def _select_recordings(self, ids):
        wanted = set(ids)
        self.recording_table.clearSelection()
        mode = self.recording_table.selectionMode()
        self.recording_table.setSelectionMode(QAbstractItemView.MultiSelection)
        for r in range(self.recording_table.rowCount()):
            item = self.recording_table.item(r, 0)
            if item is not None and item.data(RECORDING_ID_ROLE) in wanted:
                self.recording_table.selectRow(r)
        self.recording_table.setSelectionMode(mode)

    def move_selected_recordings(self, delta):
        ids = self.selected_recording_ids()
        if not ids or not self.current_campaign_id:
            self._notify("Select one or more recordings to move."); return
        self.db.move_recordings(self.current_campaign_id, ids, delta)
        self.refresh_recordings(); self._select_recordings(ids)

    def sort_recordings_by_name(self):
        if not self.current_campaign_id: return
        ids = self.selected_recording_ids()
        self.db.sort_recordings_by_name(self.current_campaign_id)
        self.refresh_recordings(); self._select_recordings(ids)

    def selected_recording_id(self):
        row = self.recording_table.currentRow()
        if row < 0:
            return None
        item = self.recording_table.item(row, 0)
        return item.data(RECORDING_ID_ROLE) if item is not None else None

    def index_existing_vault(self):
        if not self.current_campaign_id:
            self._notify("Select a campaign first.")
            return
        vault = QFileDialog.getExistingDirectory(self, "Select your existing Obsidian vault")
        if not vault:
            return
        try:
            rows, candidates = scan_vault(vault)
            self.db.save_vault_index(self.current_campaign_id, rows, candidates)
            self.config["obsidian_vault"] = vault
            from .config import save_config
            save_config(self.config)
            self.logger.info("Indexed %s markdown notes from vault %s", len(rows), vault)
            QMessageBox.information(
                self, "Vault indexed",
                f"Read-only index complete.\n\nMarkdown notes indexed: {len(rows)}"
                f"\nEntity candidates: {len(candidates)}\n\n"
                "No vault notes were changed. Character/player relationships are not assumed; "
                "they will be reviewed and mapped in a later phase."
            )
        except Exception as exc:
            self.logger.exception("Vault indexing failed")
            QMessageBox.critical(self, "Vault indexing failed", str(exc))

    def import_reference_library(self):
        """v2.46: one Import books window for all the chosen files (it was about six pop-ups per book)."""
        if not self.current_campaign_id:
            self._notify("Select a campaign first.")
            return
        start_dir = self.config.get("books_folder", "") if os.path.isdir(self.config.get("books_folder", "") or "") else ""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import D&D books", start_dir,
            "Reference files (*.pdf *.md *.markdown *.txt);;All files (*.*)"
        )
        if not paths:
            return
        from .book_import import BookImportDialog
        existing = [s["source_path"] for s in self.db.reference_source_list(self.current_campaign_id)]
        dlg = BookImportDialog(paths, DND_BOOK_CATEGORIES, self, existing=existing)
        if dlg.exec() != QDialog.Accepted:
            return
        imported, skipped = 0, []
        for book in dlg.chosen():
            try:
                source = source_record(book["path"], book["category"], book["text"])
                source["metadata"] = book["metadata"]
                self.db.save_reference_source(self.current_campaign_id, source, book["terms"], book["approved"])
                imported += 1
                self.logger.info("Imported reference source %s (%s terms; %s ticked; category=%s)", book["path"], len(book["terms"]),
                                 len(book["approved"]), book["category"])
            except Exception as exc:
                skipped.append(f"{Path(book['path']).name}: {exc}")
                self.logger.exception("Reference import failed: %s", book["path"])
        message = f"Books imported: {imported}\nBooks in this campaign's library: {self.db.reference_source_count(self.current_campaign_id)}"
        message += "\n\nUse D&D books… to see them all, re-link, combine parts of one book or remove duplicates."
        if skipped:
            message += "\n\nSkipped / issues:\n" + "\n".join(skipped[:12])
        message += "\n\nThe books are used as a names and rules reference only. Session summaries and campaign continuity come from your recorded sessions, not unrevealed book content."
        QMessageBox.information(self, "Import books", message)

    def manage_books(self):
        """v2.43: the D&D books window."""
        if not self.current_campaign_id:
            self._notify("Select a campaign first."); return
        from .book_manager import BookManagerDialog
        BookManagerDialog(self.db, self.current_campaign_id, self, self.logger, config=self.config).exec()

    def audio_settings(self):
        dlg = AudioSettingsDialog(self.config, self)
        if dlg.exec():
            self.config.update(dlg.values())
            from .config import save_config
            save_config(self.config)
            self.logger.info("Audio settings updated: %s / %s / %s", self.config['whisper_model'], self.config['whisper_device'], self.config['whisper_compute_type'])
            self.statusBar().showMessage(f"Whisper: {self.config['whisper_model']} · {self.config['whisper_device']} · {self.config['whisper_compute_type']}", 8000)

    def transcribe_selected(self):
        rid = self.selected_recording_id()
        if not rid:
            self._notify("Select a recording first.")
            return
        rec = self.db.get_recording(rid)
        if not rec:
            return
        if self._audio_worker_running():
            QMessageBox.information(self, "Busy", "Another audio-processing stage is already running. Whisper and speaker identification run separately to avoid competing for GPU memory.")
            return
        if not Path(rec["original_path"]).is_file():
            QMessageBox.warning(self, "File missing", "The original recording could not be found.")
            return
        model_size = self.config.get("whisper_model", "small")
        device = self.config.get("whisper_device", "cpu")
        compute = self.config.get("whisper_compute_type", "int8" if device == "cpu" else "float16")
        if device == "cuda":
            try:
                from .runtime import cuda_runtime_status
                runtime = cuda_runtime_status()
                missing = [name for name, available in runtime.items() if not available]
                if missing:
                    raise RuntimeError("Missing or unloadable CUDA libraries: " + ", ".join(missing))
                import ctranslate2
                ctranslate2.get_supported_compute_types("cuda")
            except Exception as exc:
                QMessageBox.critical(self, "CUDA runtime unavailable",
                    f"GPU transcription cannot start.\n\n{exc}\n\nRun INSTALL.bat and install the CUDA runtime, or select CPU / int8 in Audio settings.")
                return
        use_context = self.config.get("use_campaign_context", False)
        context = self.db.campaign_prompt_context(rec["campaign_id"]) if use_context else ""
        hotwords = self.db.campaign_hotwords(rec["campaign_id"]) if (use_context and self.config.get("whisper_use_hotwords", False)) else ""
        jobs = [j for j in self.db.all_jobs() if j["recording_id"] == rid]
        if jobs:
            self.db.set_job_stage(jobs[0]["id"], "Running")
        self.active_recording_id = rid
        self._last_saved_progress = -1
        self._last_logged_progress = -5
        self.progress_bar.setValue(0)
        self.progress_label.setText("Preparing transcription…")
        self.pause_transcription_btn.setEnabled(True)
        self.pause_transcription_btn.setText("Pause")
        self.stop_transcription_btn.setEnabled(True)
        self.transcription_thread = QThread(self)
        self.transcription_worker = TranscriptionWorker(
            rid, rec["original_path"], model_size, device, compute, context,
            self.config.get("recordings_dir", "recordings"), hotwords,
            float(self.config.get("whisper_vad_threshold", 0.4) or 0.0),
            bool(self.config.get("whisper_style_hint", True))
        )
        self.transcription_worker.moveToThread(self.transcription_thread)
        self.transcription_thread.started.connect(self.transcription_worker.run)
        self.transcription_worker.progress.connect(self.statusBar().showMessage)
        self.transcription_worker.progress_detail.connect(self.update_transcription_progress)
        self.transcription_worker.finished.connect(self.on_transcription_finished)
        self.transcription_worker.failed.connect(self.on_transcription_failed)
        self.transcription_worker.finished.connect(self.transcription_thread.quit)
        self.transcription_worker.failed.connect(self.transcription_thread.quit)
        self.transcription_worker.finished.connect(self.transcription_worker.deleteLater)
        self.transcription_worker.failed.connect(self.transcription_worker.deleteLater)
        self.transcription_thread.finished.connect(self.refresh_jobs)
        self.transcription_thread.finished.connect(self.refresh_recordings)
        self.transcription_thread.finished.connect(self.on_transcription_thread_finished)
        self.transcription_thread.finished.connect(self.transcription_thread.deleteLater)
        self.transcription_thread.start()
        context_label = (" · campaign context + hotwords enabled" if hotwords else " · campaign context enabled") if context else ""
        self.statusBar().showMessage(f"Starting {model_size} transcription on {device}…{context_label}")

    def toggle_transcription_pause(self):
        worker = self.transcription_worker
        if worker is None:
            return
        if self.pause_transcription_btn.text() == "Pause":
            worker.pause()
            self.pause_transcription_btn.setText("Resume")
            self.progress_label.setText("Paused · waiting at the current segment boundary")
            self.statusBar().showMessage("Transcription paused")
        else:
            worker.resume()
            self.pause_transcription_btn.setText("Pause")
            self.statusBar().showMessage("Transcription resumed")

    def stop_transcription(self):
        if self.transcription_worker is None:
            return
        answer = QMessageBox.question(
            self, "Stop transcription",
            "Stop after the current Whisper segment and save the transcript completed so far?\n\n"
            "The original recording and all previously saved transcript versions will be kept.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if answer == QMessageBox.Yes:
            self.transcription_worker.stop()
            self.stop_transcription_btn.setEnabled(False)
            self.pause_transcription_btn.setEnabled(False)
            self.progress_label.setText("Stopping · saving completed segments…")
            self.statusBar().showMessage("Stopping transcription and saving partial result…")

    def on_transcription_finished(self, rid, language, model_name, device, compute_type, payload):
        try:
            data_part, _, meta_part = payload.partition("\n__METADATA__\n")
            data = json.loads(data_part)
            metadata = json.loads(meta_part or "{}")
            # save_transcript also marks the recording and its job Completed / Partial.
            run_id = self.db.save_transcript(
                rid, language, model_name, data["text"], json.dumps(data["segments"], ensure_ascii=False),
                device, compute_type, json.dumps(metadata, ensure_ascii=False)
            )
            rec = self.db.get_recording(rid)
            campaign = self.db.get_campaign(rec['campaign_id']) if rec else None
            base = Path(self.config.get("recordings_dir", "recordings")) / "Transcripts" / self._safe_name(campaign['name'] if campaign else 'Unknown Campaign') / self._safe_name(rec['filename'] if rec else rid)
            base.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            stem = f"{model_name}_{device}_{compute_type}_{stamp}_{run_id[:8]}_{metadata.get('completion_status', 'COMPLETE')}"
            txt_path = base / f"{stem}.txt"
            json_path = base / f"{stem}.json"
            txt_path.write_text(data['text'], encoding='utf-8')
            export_data = {"recording_id": rid, "run_id": run_id, "language": language,
                           "model": model_name, "device": device, "compute_type": compute_type,
                           "metadata": metadata, "segments": data['segments']}
            json_path.write_text(json.dumps(export_data, ensure_ascii=False, indent=2), encoding='utf-8')
            metadata['text_export'] = str(txt_path)
            metadata['json_export'] = str(json_path)
            self.db.update_transcript_metadata(run_id, json.dumps(metadata, ensure_ascii=False))
            self._write_run_exports(run_id)
            partial = bool(metadata.get("stopped_early"))
            if partial:
                self.logger.info("Transcription stopped by user; partial transcript saved: %s (%s) %s; exports: %s", rid, language, model_name, base)
            else:
                self.logger.info("Transcription completed: %s (%s) %s; exports: %s", rid, language, model_name, base)
            self.progress_bar.setValue(100 if not partial else self.progress_bar.value())
            self.progress_label.setText("Partial transcript saved · stopped by user" if partial else "Transcription complete · 100%")
            self.active_recording_id = None
            self.statusBar().showMessage(("Partial transcript saved" if partial else f"{model_name} transcription complete") + f" · exports saved to {base}", 15000)
            first_start = min((float(seg.get("start", 0)) for seg in data.get("segments", [])), default=0.0)
            duration = float(metadata.get("audio_duration_seconds", 0) or 0)
            elapsed = float(metadata.get("elapsed_seconds", 0) or 0)
            timing_note = (f"\n\nNote: first detected speech starts at {self._format_duration(first_start)}. Check the opening audio if you expected speech earlier."
                           if first_start >= 60 else "")
            if not self._queue_active_task_id:
                QMessageBox.information(self, "Partial transcript saved" if partial else "Transcription complete",
                    ("Saved the completed portion as a new, separate transcript run. Existing runs and the original recording were kept.\n\n" if partial else "Saved a new, separate transcript run. Existing runs were kept.\n\n") +
                    f"Model: {model_name}\nDevice: {device}\nCompute: {compute_type}\n"
                    f"Audio length: {self._format_duration(duration)}\nProcessing time: {self._format_duration(elapsed)}\n\n"
                    f"Text and timestamped JSON exports:\n{base}{timing_note}")
        except Exception as exc:
            if self._queue_active_task_id: self._queue_active_task_failed=True
            self.logger.exception("Could not save transcript output")
            jobs = [j for j in self.db.all_jobs() if j["recording_id"] == rid]
            if jobs:
                self.db.set_job_stage(jobs[0]["id"], "Failed", str(exc))
            self.progress_label.setText("Transcript save failed")
            self.progress_bar.setValue(0)
            self.active_recording_id = None
            self.refresh_jobs(); self.refresh_recordings()
            QMessageBox.critical(self, "Transcript save failed", str(exc))

    def on_transcription_thread_finished(self):
        self.transcription_thread = None
        self.transcription_worker = None
        if getattr(self, "_close_after_stop", False):
            QTimer.singleShot(300, self.close)
        self.pause_transcription_btn.setEnabled(False)
        self.pause_transcription_btn.setText("Pause")
        self.stop_transcription_btn.setEnabled(False)
        self.refresh_jobs()
        self.refresh_recordings()
        self.refresh_gpu_memory()
        if self._queue_active_task_id:
            self._finish_queued_task(self._queue_active_task_failed, "Transcription failed" if self._queue_active_task_failed else "")
        self._start_queue_if_waiting()   # v2.47

    @staticmethod
    def _format_duration(seconds):
        seconds = max(0, int(seconds))
        hours, rem = divmod(seconds, 3600)
        minutes, secs = divmod(rem, 60)
        if hours:
            return f"{hours}h {minutes:02d}m"
        if minutes:
            return f"{minutes}m {secs:02d}s"
        return f"{secs}s"

    def update_transcription_progress(self, message, percent):
        value = max(0, min(100, int(percent)))
        self.progress_label.setText(message)
        self.progress_bar.setValue(value)
        self.statusBar().showMessage(message)
        if value >= self._last_logged_progress + 5:
            self.logger.info("Transcription progress %s: %s%% · %s", self.active_recording_id, value, message)
            self._last_logged_progress = value
        if self.active_recording_id and value != self._last_saved_progress:
            jobs = [j for j in self.db.all_jobs() if j["recording_id"] == self.active_recording_id and j["stage"] == "Running"]
            if jobs:
                self.db.set_job_progress(jobs[0]["id"], value)
                self._last_saved_progress = value

    def _write_run_exports(self, run_id):
        row = self.db.conn.execute("SELECT * FROM transcript_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return
        metadata = json.loads(row['metadata_json'] or "{}")
        segments = json.loads(row['segments_json'] or "[]")
        text_content = row['text_content'] or ""
        txt_path = metadata.get('text_export')
        json_path = metadata.get('json_export')
        if txt_path:
            Path(txt_path).parent.mkdir(parents=True, exist_ok=True)
            Path(txt_path).write_text(text_content, encoding='utf-8')
        if json_path:
            Path(json_path).parent.mkdir(parents=True, exist_ok=True)
            payload = {"run_id":run_id,"recording_id":row['recording_id'],"language":row['language'],
                       "model":row['model_name'],"device":row['device'],"compute_type":row['compute_type'],
                       "metadata":metadata,"speaker_map":json.loads(row['speaker_map_json'] or "{}"),"segments":segments,
                       "raw_text":row['raw_text_content'] if 'raw_text_content' in row.keys() else text_content,
                       "raw_segments":json.loads(row['raw_segments_json'] or "[]") if 'raw_segments_json' in row.keys() else segments,
                       "edit_history_count":len(self.db.transcript_edit_history(run_id))}
            Path(json_path).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')

    @staticmethod
    def _safe_name(value):
        return re.sub(r'[<>:"/\\|?*]', "_", str(value)).strip(" .") or "Untitled"

    def on_transcription_failed(self, rid, error):
        if self._queue_active_task_id: self._queue_active_task_failed=True
        jobs = [j for j in self.db.all_jobs() if j["recording_id"] == rid and j["stage"] == "Running"]
        if jobs:
            self.db.set_job_stage(jobs[0]["id"], "Failed", error)
        self.logger.error("Transcription failed for %s: %s", rid, error)
        self.progress_label.setText("Transcription failed · see job error")
        self.progress_bar.setValue(0)
        self.active_recording_id = None
        self.refresh_jobs(); self.refresh_recordings()
        if not self._queue_active_task_id: QMessageBox.critical(self, "Transcription failed", error)

    def recover_transcription_checkpoint(self):
        rid=self.selected_recording_id()
        if not rid:
            self._notify("Select the recording whose interrupted transcription you want to recover."); return
        folder=Path(self.config.get("recordings_dir","recordings"))/"Transcripts"/".checkpoints"
        candidates=sorted(folder.glob(f"{rid}_*.checkpoint.json"),key=lambda x:x.stat().st_mtime,reverse=True) if folder.exists() else []
        # Stopping a transcription already saves a partial transcript version. A checkpoint is only
        # offered when no saved version came from it (crash, closed app, failed save).
        saved=set()
        for existing in self.db.transcript_runs(rid):
            try: meta=json.loads(existing["metadata_json"] or "{}")
            except Exception: meta={}
            for key in ("checkpoint_file","recovered_from_checkpoint"):
                if meta.get(key): saved.add(Path(str(meta[key])).name)
        incomplete=[]
        for candidate in candidates:
            try:
                state=json.loads(candidate.read_text(encoding="utf-8")).get("state")
                if state in {"RUNNING","INTERRUPTED","STOPPED"} and candidate.name not in saved: incomplete.append(candidate)
            except Exception: continue
        candidates=incomplete
        if not candidates:
            partial=[r for r in self.db.transcript_runs(rid) if "PARTIAL" in (r["metadata_json"] or "") or '"stopped_early": true' in (r["metadata_json"] or "")]
            QMessageBox.information(self,"Nothing to recover",
                "There is no unsaved transcription for this recording.\n\nWhen you press Stop, the part transcribed so far is saved automatically as a transcript version marked PARTIAL / STOPPED"
                +(f" (this recording has {len(partial)}): open it with Edit transcript." if partial else ".")
                +"\nRecover checkpoint is only for runs that were cut off by a crash, a power loss or closing the app.")
            return
        path,ok=QInputDialog.getItem(self,"Recover transcription checkpoint","Choose a checkpoint:",[p.name for p in candidates],0,False)
        if not ok:return
        selected=next(p for p in candidates if p.name==path)
        try:
            for existing_run in self.db.transcript_runs(rid):
                try:
                    existing_meta=json.loads(existing_run["metadata_json"] or "{}")
                    if existing_meta.get("recovered_from_checkpoint")==str(selected):
                        QMessageBox.information(self,"Already recovered","This checkpoint has already been recovered as a transcript version.")
                        return
                except Exception: pass
            payload=json.loads(selected.read_text(encoding="utf-8"))
            if not payload.get("segments"):
                QMessageBox.information(self,"Empty checkpoint","This checkpoint does not contain any completed transcript segments."); return
            state=payload.get("state","RUNNING")
            metadata=payload.get("metadata",{})
            metadata.update({"completion_status":"PARTIAL_INTERRUPTED" if state in {"RUNNING","INTERRUPTED"} else "PARTIAL_STOPPED",
                             "recovered_from_checkpoint":str(selected),"checkpoint_state":state,"stopped_early":True})
            data_segments=payload["segments"]; text_content=payload.get("text","\n".join(x.get("text","") for x in data_segments))
            rec=self.db.get_recording(rid); campaign=self.db.get_campaign(rec['campaign_id']) if rec else None
            base=Path(self.config.get("recordings_dir","recordings"))/"Transcripts"/self._safe_name(campaign['name'] if campaign else 'Unknown Campaign')/self._safe_name(rec['filename'] if rec else rid)
            base.mkdir(parents=True,exist_ok=True); stamp=datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            stem=f"{metadata.get('model_name','unknown')}_{metadata.get('device','cpu')}_{metadata.get('compute_type','int8')}_{stamp}_PARTIAL_INTERRUPTED"
            txt_path=base/f"{stem}.txt"; json_path=base/f"{stem}.json"
            metadata.update({"text_export":str(txt_path),"json_export":str(json_path),"transcribed_until_seconds":data_segments[-1].get("end",0)})
            run_id=self.db.save_transcript(rid,metadata.get("language",""),metadata.get("model_name","unknown"),text_content,json.dumps(data_segments,ensure_ascii=False),
                metadata.get("device","cpu"),metadata.get("compute_type","int8"),json.dumps(metadata,ensure_ascii=False))
            txt_path.write_text(text_content,encoding="utf-8")
            self._write_run_exports(run_id)
            self.logger.info("Recovered transcription checkpoint %s as run %s (state=%s)",selected,run_id,state)
            QMessageBox.information(self,"Checkpoint recovered",f"Recovered {len(data_segments)} completed segments as a new partial transcript version.\n\nExports saved to:\n{base}\n\nOriginal checkpoint retained:\n{selected}")
            self.refresh_recordings()
        except Exception as exc:
            self.logger.exception("Checkpoint recovery failed")
            QMessageBox.critical(self,"Could not recover checkpoint",str(exc))

    def manage_campaign_dictionary(self):
        if not self.current_campaign_id:
            self._notify("Select a campaign first."); return
        dlg=CampaignDictionaryDialog(self.db,self.current_campaign_id,self)
        if dlg.exec():
            self.logger.info("Campaign dictionary updated for campaign %s",self.current_campaign_id)
            QMessageBox.information(self,"Campaign dictionary saved","Canonical names and aliases are saved locally. Approved canonical names and their legitimate aliases will be used as transcription context.")

    # ---- v2.31.2: Edit transcript, Compare versions and Delete version are separate buttons
    @staticmethod
    def _version_label(r):
        try: meta=json.loads(r["metadata_json"] or "{}")
        except Exception: meta={}
        status="PARTIAL / STOPPED" if meta.get("partial") or meta.get("stopped_early") else "Completed"
        return f"[{status}] {r['model_name']} · {r['device']} · {r['compute_type']} · {r['created_at']} · {r['id'][:8]}"

    def _runs_for_selected(self):
        """(recording id, its transcript versions newest first), with a message when there are none."""
        rid = self.selected_recording_id()
        if not rid:
            self._notify("Select a recording first."); return None, []
        runs = self.db.transcript_runs(rid)
        if not runs:
            transcript = self.db.get_transcript(rid)
            if not transcript:
                self._notify("This recording has not been transcribed yet."); return rid, []
            runs = [transcript]
        return rid, runs

    def compare_selected_versions(self):
        rid, runs = self._runs_for_selected()
        if not runs: return
        if len(runs) < 2:
            self._notify("This recording has only one transcript version, so there is nothing to compare it with."); return
        TranscriptComparisonDialog(runs, self, self.db).exec()

    def delete_selected_version(self):
        rid, runs = self._runs_for_selected()
        if not runs: return
        if len(runs) < 2:
            self._notify("This is the only transcript version of this recording, so it is protected."); return
        labels=[self._version_label(r) for r in runs]
        choice,ok=QInputDialog.getItem(self,"Delete transcript version","Choose the version to delete (the last remaining version is protected):",labels,0,False)
        if not ok:return
        selected=runs[labels.index(choice)]
        if any(editor.run["id"] == selected["id"] for editor in self._open_editors):
            QMessageBox.information(self, "Delete version", "That version is open in an editor window. Close it first."); return
        if self._diarizing_run_id == selected["id"]:
            QMessageBox.information(self, "Delete version", "Speaker identification is writing to that version. Wait for it to finish."); return
        if QMessageBox.question(self,"Confirm transcript deletion",f"Permanently delete this transcript version?\n\n{choice}\n\nThis does not delete the original recording.",QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        try:
            self.db.delete_transcript_run(selected["id"])
            self.logger.info("Deleted transcript run %s",selected["id"])
            self.refresh_recordings()
        except Exception as exc: QMessageBox.critical(self,"Could not delete transcript",str(exc))

    def delete_selected_recording(self):
        """v2.31.3: remove one recording and all its transcript versions from the program, after a backup.
        The audio file is never deleted. Reviewed work needs a second, typed confirmation."""
        rid = self.selected_recording_id()
        if not rid:
            self._notify("Select a recording first."); return
        rec = self.db.get_recording(rid)
        if not rec: return
        runs = self.db.transcript_runs(rid)
        if any(editor.run["recording_id"] == rid for editor in self._open_editors):
            QMessageBox.information(self, "Delete recording", "A transcript of this recording is open in an editor window. Close it first."); return
        if self._audio_worker_running() and (self.active_recording_id == rid or self._diarizing_run_id in {r["id"] for r in runs}):
            QMessageBox.information(self, "Delete recording", "This recording is being transcribed or identified. Wait for it to finish."); return
        busy = self.db.conn.execute("SELECT COUNT(*) FROM processing_tasks WHERE recording_id=? AND stage='Running'", (rid,)).fetchone()[0]
        if busy:
            QMessageBox.information(self, "Delete recording", "The processing queue is working on this recording. Wait for it to finish."); return
        reviewed = sum(int(r["reviewed_count"] or 0) for r in runs)
        backup_dir = Path(self.config.get("backups_dir", "backups")) / "deleted_recordings"
        text = (f"Remove “{rec['filename']}” from Local SessionKeeper?\n\n"
                f"This deletes its {len(runs)} transcript version(s), their edit history, saved speaker turns and queue entries"
                + (f", including {reviewed} reviewed segment(s)" if reviewed else "") + ".\n\n"
                f"A backup of all of it is saved first to:\n{backup_dir}\n\n"
                "The audio file itself is NOT deleted, and learned voices are kept. Obsidian notes already exported are not touched.")
        if QMessageBox.question(self, "Delete recording", text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        if reviewed:
            typed, ok = QInputDialog.getText(self, "Delete reviewed work",
                f"This recording has {reviewed} reviewed segment(s). Type DELETE to remove it (a backup is still saved).")
            if not ok or typed.strip() != "DELETE":
                self._notify("Nothing was deleted."); return
        try:
            data = self.db.export_recording(rid)
            backup_dir.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r'[^A-Za-z0-9 ._-]+', '_', Path(rec["filename"]).stem)[:60]
            target = backup_dir / f"{safe}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            target.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        except Exception as exc:
            self.logger.exception("Backup before deleting recording %s failed", rid)
            QMessageBox.critical(self, "Delete recording", f"The backup could not be written, so nothing was deleted:\n{exc}"); return
        try:
            count = self.db.delete_recording(rid)
        except Exception as exc:
            self.logger.exception("Deleting recording %s failed", rid)
            QMessageBox.critical(self, "Delete recording", f"Could not delete the recording:\n{exc}"); return
        self.logger.warning("Deleted recording %s (%s) with %s transcript version(s); backup %s", rec["filename"], rid, count, target)
        self.refresh_recordings(); self.refresh_campaigns(self.current_campaign_id); self.refresh_jobs()
        self.statusBar().showMessage(f"Removed {rec['filename']} · backup saved to {target}", 15000)

    def view_selected_transcript(self):
        """Edit transcript: open a version in the editor (asks which one when there are several)."""
        rid, runs = self._runs_for_selected()
        if not runs: return
        run = runs[0]
        # v2.43: open the version the ◀ ▶ arrows in the recordings table show; hold Shift to choose from the list
        from PySide6.QtWidgets import QApplication
        stats = getattr(self, "_run_stats", {}).get(rid) or []
        pos = getattr(self, "_stats_pos", {}).get(rid, 0)
        shown = stats[pos]["id"] if 0 <= pos < len(stats) else None
        ask = bool(QApplication.keyboardModifiers() & Qt.ShiftModifier)
        if len(runs) > 1 and shown and not ask and any(r["id"] == shown for r in runs):
            run = next(r for r in runs if r["id"] == shown)
        elif len(runs) > 1:
            labels = [self._version_label(r) for r in runs]
            choice, ok = QInputDialog.getItem(self, "Edit transcript", "Choose a version to edit:", labels, 0, False)
            if not ok:
                return
            run = runs[labels.index(choice)]
        if self._diarizing_run_id == run["id"]:
            QMessageBox.information(self, "Speaker identification running", "Speaker identification is still writing to this transcript version. Open the editor after it finishes so your edits are not overwritten.")
            return
        if any(editor.run["id"] == run["id"] for editor in self._open_editors):
            QMessageBox.information(self, "Already open", "This transcript version is already open in an editor window.")
            return
        rec = self.db.get_recording(rid)
        run = dict(run)
        run["recording_name"] = rec["filename"] if rec else ""
        self.logger.info("Transcript editor opened | recording=%s | run=%s", run["recording_name"], run["id"])
        vocabulary = []
        alias_map = {}
        book_terms = []
        if rec:
            # v2.46: book names are passed apart (matched more strictly); they were mixed in with the campaign names
            # v2.47: with each book's level (books set to Off are left out)
            book_terms = [(t, level) for t, _book, level in self.db.book_names(rec["campaign_id"])]
            dictionary_rows=self.db.campaign_dictionary_entries(rec["campaign_id"])
            vocabulary.extend(x["canonical_name"] for x in dictionary_rows if x["approved_for_whisper"])
            vocabulary.extend(a.strip() for x in dictionary_rows for a in str(x["also_known_as"] or "").split(",") if a.strip())
            alias_map={x["heard_text"]:x["intended_text"] for x in self.db.campaign_mishearings(rec["campaign_id"])}
        edit_events = self.db.transcript_edit_events(run['id']) if hasattr(self.db, "transcript_edit_events") else []
        dlg = TranscriptEditor(run, self, rec["original_path"] if rec else None, vocabulary, edit_events,
            alias_map, self.config.get("backups_dir", "backups"), None,
            str(self.db.path), self.logger,
            name_db=self.db, campaign_id=rec["campaign_id"] if rec else None, book_terms=book_terms)
        dlg.setModal(False)
        dlg.setAttribute(Qt.WA_DeleteOnClose, True)
        self._open_editors.append(dlg)
        dlg.finished.connect(lambda result, editor=dlg, run_id=run["id"]: self._transcript_editor_finished(editor, run_id, result))
        dlg.show()

    # ------------------------------------------------ v2.20: import / report / learning
    @staticmethod
    def _run_choice_label(r):
        return f"{str(r['created_at'])[:16]} · {r['model_name']} · {r['reviewed_count']} reviewed of {r['segment_count']} segments · {r['id'][:8]}"

    def import_reviewed_work(self):
        rid = self.selected_recording_id()
        if not rid:
            self._notify("Select a recording first."); return
        runs = self.db.transcript_runs(rid)
        if len(runs) < 2:
            QMessageBox.information(self, "Need two versions", "This recording has only one transcript version. Transcribe it again (for example with the levelled audio), then import your reviewed work onto the new version."); return
        labels = [self._run_choice_label(r) for r in runs]
        best = max(range(len(runs)), key=lambda i: runs[i]["reviewed_count"])
        src_label, ok = QInputDialog.getItem(self, "Import reviewed work", "Copy the speaker work FROM this version:", labels, best, False)
        if not ok: return
        others = [l for l in labels if l != src_label]
        tgt_label, ok = QInputDialog.getItem(self, "Import reviewed work", "…onto this version (anything you already reviewed there is never changed):", others, 0, False)
        if not ok: return
        source, target = runs[labels.index(src_label)], runs[labels.index(tgt_label)]
        modes = ["Speakers + text and timestamps only where I edited the text (recommended)",
                 "Speakers + text and timestamps of ALL reviewed clips",
                 "Let me choose clip by clip…",
                 "Speakers only (keep the new transcript's text)"]
        mode, ok = QInputDialog.getItem(self, "Import reviewed work", "What should be carried over from the reviewed clips?", modes, 0, False)
        if not ok: return
        busy = {self._diarizing_run_id} | {e.run["id"] for e in self._open_editors}
        if source["id"] in busy or target["id"] in busy:
            QMessageBox.information(self, "Version in use", "Close the transcript editor (and wait for any speaker identification) for both versions first, so nothing is overwritten."); return
        try:
            tgt_map = json.loads(target["speaker_map_json"] or "{}")
            src_segs = json.loads(source["segments_json"] or "[]")
            tgt_segs = json.loads(target["segments_json"] or "[]")
            edited = edited_clip_keys(src_segs, json.loads(source["raw_segments_json"] or "[]"))
            text_clips = set()
            if mode == modes[0]:
                text_clips = edited
            elif mode == modes[1]:
                text_clips = {row["key"] for row in describe_clips(src_segs, tgt_segs, edited)}
            elif mode == modes[2]:
                dialog = ImportClipsDialog(describe_clips(src_segs, tgt_segs, edited), self)
                if not dialog.exec(): return
                text_clips = dialog.chosen_keys()
            new_segs, new_map, report = import_reviewed(src_segs, json.loads(source["speaker_map_json"] or "{}"), tgt_segs, tgt_map, text_clips=text_clips)
            if not report["source_reviewed"]:
                QMessageBox.information(self, "Nothing to import", "The source version has no segments ticked Reviewed with a speaker."); return
            summary = summarise_import(report, new_map)
            if QMessageBox.question(self, "Import reviewed work", summary + "\n\nApply this to the selected version? A full copy of it is kept in its edit history.",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) != QMessageBox.Yes:
                return
            text = "\n".join(sg.get("text", "") for sg in new_segs)
            self.db.update_transcript_run(target["id"], text, json.dumps(new_segs, ensure_ascii=False), json.dumps(new_map, ensure_ascii=False), force_snapshot=True)
            self._write_run_exports(target["id"])
            self.logger.info("Imported reviewed work %s -> %s | %s", source["id"], target["id"], {k: v for k, v in report.items() if k != "speaker_ids"})
            self.refresh_recordings()
            self.statusBar().showMessage("Reviewed work imported. Run Identify / re-check speakers to label the rest.", 15000)
        except Exception as exc:
            self.logger.exception("Import of reviewed work failed")
            QMessageBox.critical(self, "Import failed", str(exc))

    def show_last_recheck_report(self):
        rid = self.selected_recording_id()
        if not rid:
            self._notify("Select a recording first."); return
        for r in self.db.transcript_runs(rid):
            try: info = (json.loads(r["metadata_json"] or "{}")).get("last_recheck")
            except Exception: info = None
            if info:
                text = f"Run {r['id'][:8]} · {info.get('at', '')}\n\n{info.get('summary', '')}"
                if not info.get("has_embeddings"):
                    text += "\n\nNote: this check had no voice embeddings, so clusters were matched only from your reviewed segments."
                TextReportDialog("Last speaker re-check report", text, self).exec()
                return
        self._notify("No speaker re-check has been saved for this recording yet.")

    def learn_from_edits(self):
        if not self.current_campaign_id:
            self._notify("Select a campaign first."); return
        dlg = LearnedCorrectionsDialog(self.db, self.current_campaign_id, self)
        if dlg.exec() and getattr(dlg, "added", 0):
            self.logger.info("Learned %s correction(s) from edits for campaign %s", dlg.added, self.current_campaign_id)
            QMessageBox.information(self, "Vocabulary updated", f"{dlg.added} correction(s) added. They are used the next time you transcribe, and the editor will suggest them.")

    def _transcript_editor_finished(self, editor, run_id, result):
        try:
            editor.flush_autosave()
            editor.mark_saved()
            self._write_run_exports(run_id)
            self.logger.info("Transcript editor closed for run %s (result=%s)",run_id,result)
        except Exception as exc:
            self.logger.exception("Could not finish transcript autosave/export for %s",run_id)
            QMessageBox.critical(self,"Transcript save issue",f"The editor closed, but the final save/export reported an error:\n{exc}")
        try: self._open_editors.remove(editor)
        except ValueError: pass
        self.refresh_recordings()

    def diarize_selected(self, queued=False):
        rid = self.selected_recording_id()
        if not rid:
            self._notify("Select a recording first.")
            return
        runs = self.db.transcript_runs(rid)
        if not runs:
            if queued and self._queue_active_task_id:
                self._queue_active_task_failed=True
                self.logger.error("Queued speaker identification skipped: no transcript exists for recording %s",rid)
            else:
                QMessageBox.information(self, "Transcribe first", "Run a Whisper transcription before identifying speakers.")
            return
        if queued:
            run=runs[0]
        else:
            labels = [f"{r['model_name']} · {r['device']} · {r['compute_type']} · {r['created_at']} · {r['id'][:8]}" for r in runs]
            choice, ok = QInputDialog.getItem(self, "Choose transcript", "Identify speakers in which transcript version?", labels, 0, False)
            if not ok: return
            run = runs[labels.index(choice)]
        if self._audio_worker_running():
            QMessageBox.information(self, "Busy", "Another audio-processing stage is already running. Wait for transcription to finish before identifying speakers.")
            return
        # Speaker identification rewrites the run's segments. An open editor would later
        # autosave its older copy over the result (or vice versa), so require it to be closed.
        if any(editor.run["id"] == run["id"] for editor in self._open_editors):
            message = "Close the transcript editor for this version before identifying speakers. Both would write to the same transcript and one would overwrite the other."
            if queued and self._queue_active_task_id:
                self._queue_active_task_failed=True
                self.logger.error("Queued speaker identification skipped: transcript editor is open for run %s", run["id"])
            else:
                QMessageBox.information(self, "Transcript editor open", message)
            return
        token = ""
        try:
            from .credentials import get_huggingface_token
            token = get_huggingface_token()
        except Exception as exc:
            self.logger.warning("Windows Credential Manager unavailable: %s", exc)
        if not token:
            token, ok = QInputDialog.getText(self, "Hugging Face access token",
                "pyannote requires accepted model terms and a Hugging Face read token. Save it securely in Windows Credential Manager?", QLineEdit.Password)
            if not ok or not token.strip():
                return
            remember = QMessageBox.question(self, "Save token securely",
                "Store this token in Windows Credential Manager so you do not need to enter it again?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if remember == QMessageBox.Yes:
                try:
                    from .credentials import set_huggingface_token
                    set_huggingface_token(token.strip())
                except Exception as exc:
                    QMessageBox.warning(self, "Secure storage unavailable", f"Could not save token securely; it will only be used for this run.\n\n{exc}")
        rec = self.db.get_recording(rid)
        if not rec or not Path(rec['original_path']).is_file():
            QMessageBox.warning(self, "Recording unavailable", "The original recording could not be found.")
            return
        self._diarization_last_error = False
        self._diarization_status_text = "Speaker identification started…"
        self._diarizing_run_id = run['id']
        self.diarization_thread = QThread(self)
        # Diarization is a separate stage; the user-selected device is used only after transcription has finished.
        device = self.config.get('diarization_device', 'cpu')
        self.diarization_worker = DiarizationWorker(
            run['id'], rid, rec['original_path'], run['segments_json'], token.strip(), device,
            self.config.get('recordings_dir','recordings'),
            min_speakers=int(self.config.get('diarization_min_speakers', 0) or 0),
            num_speakers=int(self.config.get('diarization_num_speakers', 0) or 0),
            max_speakers=int(self.config.get('diarization_max_speakers', 0) or 0),
            speaker_map_json=run['speaker_map_json'] or "{}",
            stored_prints_json=json.dumps(self.db.voiceprints(rec['campaign_id'], exclude_run_id=run['id'], share_across=bool(self.config.get('voiceprints_share_across_campaigns', True)))),
            match_threshold=float(self.config.get('diarization_match_threshold', 0.60) or 0.60))
        self.diarization_worker.moveToThread(self.diarization_thread)
        self.diarization_thread.started.connect(self.diarization_worker.run)
        self.diarization_progress = QProgressDialog("Starting speaker identification…", "", 0, 0, self)
        self.diarization_progress.setWindowTitle("Identifying speakers")
        self.diarization_progress.setWindowModality(Qt.WindowModal)
        self.diarization_progress.setCancelButton(None)
        self.diarization_progress.setMinimumDuration(0)
        self.diarization_progress.show()
        self.diarization_worker.progress.connect(self.on_diarization_progress)
        self.diarization_worker.finished.connect(self.on_diarization_finished)
        self.diarization_worker.failed.connect(self.on_diarization_failed)
        self.diarization_worker.finished.connect(self.diarization_thread.quit)
        self.diarization_worker.failed.connect(self.diarization_thread.quit)
        self.diarization_worker.finished.connect(self.diarization_worker.deleteLater)
        self.diarization_worker.failed.connect(self.diarization_worker.deleteLater)
        self.diarization_thread.finished.connect(self.on_diarization_thread_finished)
        self.diarization_thread.finished.connect(self.diarization_thread.deleteLater)
        self.diarization_thread.start()
        self.statusBar().showMessage("Speaker identification started…")

    def on_diarization_progress(self, message):
        self.statusBar().showMessage(message)
        if getattr(self, "diarization_progress", None):
            self.diarization_progress.setLabelText(message)
        self.logger.info("Speaker identification: %s", message)

    def on_diarization_thread_finished(self):
        if getattr(self, "diarization_progress", None):
            self.diarization_progress.close()
            self.diarization_progress = None
        self.diarization_thread = None
        self.diarization_worker = None
        self._diarizing_run_id = None
        self.refresh_gpu_memory()
        if self._queue_active_task_id:
            self._finish_queued_task(self._diarization_last_error, "Speaker identification failed" if self._diarization_last_error else "")
        self._start_queue_if_waiting()   # v2.47
        self.statusBar().showMessage(
            getattr(self, "_diarization_status_text", "Ready"),
            15000 if self._diarization_last_error else 10000
        )

    def on_diarization_finished(self, run_id, payload, device):
        if getattr(self, "diarization_progress", None):
            self.diarization_progress.close()
            self.diarization_progress = None
        try:
            result = json.loads(payload)
            row = self.db.conn.execute("SELECT * FROM transcript_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise RuntimeError("The transcript run no longer exists.")
            text_content = "\n".join(s.get('text','') for s in result['segments'])
            # Merge in speakers created by the re-check without overwriting anything the user entered,
            # then drop empty auto-placeholders that no segment uses any more.
            speaker_map = json.loads(row['speaker_map_json'] or "{}")
            for sid, entry in (result.get('speaker_map_updates') or {}).items():
                speaker_map.setdefault(sid, entry)
            used_ids = {s.get('speaker_id') for s in result['segments'] if s.get('speaker_id')}
            speaker_map = prune_speaker_map(speaker_map, used_ids)
            # force_snapshot: keep a full copy of the transcript as it was before this re-check.
            self.db.update_transcript_run(run_id, text_content, json.dumps(result['segments'], ensure_ascii=False),
                                          json.dumps(speaker_map, ensure_ascii=False), force_snapshot=True)
            if result.get('labelled_turns'):
                try: self.db.save_diarization_turns(run_id, result['labelled_turns'])
                except Exception: self.logger.exception("Could not save speaker turns for run %s", run_id)
            rec = self.db.get_recording(row['recording_id'])
            if rec and result.get('voiceprints'):
                self.db.save_voiceprints(rec['campaign_id'], run_id, result['voiceprints'])
            self._write_run_exports(run_id)
            report = result.get('report') or {}
            summary = summarise_report(report, speaker_map) if report else ""
            if report:
                # Keep the report with the run so it can be re-read later (button: Last re-check report).
                try: meta = json.loads(row['metadata_json'] or "{}")
                except Exception: meta = {}
                meta["last_recheck"] = {"at": datetime.now().isoformat(timespec="seconds"), "summary": summary,
                                        "has_embeddings": bool(result.get('has_embeddings'))}
                self.db.update_transcript_metadata(run_id, json.dumps(meta, ensure_ascii=False))
            self.logger.info("Speaker identification completed for run %s using %s; %s speakers; reviewed kept=%s; relabelled=%s; voiceprints saved=%s; embeddings=%s",
                             run_id, device, len(result['speaker_ids']), report.get('reviewed_segments'), report.get('changed'),
                             len(result.get('voiceprints') or []), result.get('has_embeddings'))
            if not self._queue_active_task_id and report:
                if not result.get('has_embeddings'):
                    summary += "\n\nNote: this pyannote version supplied no voice embeddings, so clusters were matched only from your reviewed segments."
                QMessageBox.information(self, "Speaker identification complete",
                    f"Detected {len(result['speaker_ids'])} speakers.\n\n{summary}\n\n"
                    "Open Edit transcript to map speakers to players and tick Reviewed on more clips, then run Identify / re-check speakers again. "
                    "Your reviewed segments are never changed.")
            self._diarization_last_error = False
            self._diarization_status_text = "Speaker identification complete · map speakers in transcript editor"
            self.statusBar().showMessage(self._diarization_status_text, 12000)
            self.refresh_recordings()
        except Exception as exc:
            self._diarization_last_error = True
            self._diarization_status_text = "Speaker identification result could not be saved"
            self.logger.exception("Could not save diarization result")
            if not self._queue_active_task_id: QMessageBox.critical(self, "Diarization save failed", str(exc))

    def on_diarization_failed(self, run_id, error):
        if getattr(self, "diarization_progress", None):
            self.diarization_progress.close()
            self.diarization_progress = None
        self._diarization_last_error = True
        self._diarization_status_text = "Speaker identification failed · see error details"
        self.logger.error("Speaker diarization failed for %s: %s", run_id, error)
        if "hf_hub_download" in error and "use_auth_token" in error:
            guidance = (
                "This is a Hugging Face package-version mismatch, not a token or model-terms problem. "
                "Close SessionKeeper and run INSTALL_DIARIZATION.bat to install the compatible pinned version."
            )
        elif "401" in error or "403" in error or "gated" in error.casefold():
            guidance = (
                "Check that you entered a Hugging Face read token and accepted the access conditions for "
                "pyannote/speaker-diarization-community-1."
            )
        else:
            guidance = (
                "Check the error above. If this concerns missing or incompatible packages, close "
                "SessionKeeper and run INSTALL_DIARIZATION.bat. First use also requires a Hugging Face "
                "read token and acceptance of the Community-1 model access conditions."
            )
        self.statusBar().showMessage("Speaker identification failed · worker stopped", 15000)
        if not self._queue_active_task_id: QMessageBox.critical(self, "Speaker identification failed", error + "\n\n" + guidance)

    def export_campaign(self):
        if not self.current_campaign_id:
            return
        start = self.config.get("obsidian_export_vault") or str(Path.home())
        vault = QFileDialog.getExistingDirectory(self, "Choose Obsidian export destination", start)
        if not vault:
            return
        source_vault = self.config.get("obsidian_vault")
        if source_vault:
            try:
                dest = Path(vault).resolve()
                source = Path(source_vault).resolve()
                if dest == source or source in dest.parents:
                    QMessageBox.warning(self, "Separate vault required",
                        "Choose an export destination outside the indexed source vault. The source vault is kept read-only and separate from generated exports.")
                    return
            except Exception:
                pass
        campaign = self.db.get_campaign(self.current_campaign_id)
        try:
            note, created = export_campaign_stub(
                campaign, vault, self.config.get("obsidian_folder", "SessionKeeper"), self.logger
            )
            self.config["obsidian_export_vault"] = vault
            from .config import save_config
            save_config(self.config)
            message = f"Created campaign note:\n{note}" if created else f"Campaign note already exists; left unchanged:\n{note}"
            items = []
            for rec in self.db.recordings(self.current_campaign_id):
                runs = self.db.transcript_runs(rec["id"])
                if not runs:
                    continue
                stats = {x["id"]: x["reviewed"] for x in self.db.run_stats_list(rec["id"])}
                best = max(runs, key=lambda r: (stats.get(r["id"], 0), r["created_at"] or ""))  # most speaker-reviewed, then newest
                try:
                    segments = json.loads(best["segments_json"] or "[]"); smap = json.loads(best["speaker_map_json"] or "{}")
                except Exception:
                    continue
                items.append((rec["filename"], best, segments, smap))
            results = export_session_transcripts(campaign, items, vault, self.config.get("obsidian_folder", "SessionKeeper"), self.logger)
            counts = {}
            for _name, _path, status in results:
                counts[status] = counts.get(status, 0) + 1
            if results:
                message += (f"\n\nSession transcripts: {counts.get('created', 0)} new, {counts.get('updated', 0)} refreshed, "
                            f"{counts.get('kept-edited', 0)} already edited by you (new version saved beside it as '(new export)').\n"
                            "Each recording uses its most speaker-reviewed transcription.")
            QMessageBox.information(self, "Obsidian export", message)
        except Exception as exc:
            self.logger.exception("Obsidian export failed")
            QMessageBox.critical(self, "Export failed", str(exc))


    def closeEvent(self, event):
        if self.transcription_worker is not None and getattr(self, "_close_after_stop", False) is False:
            answer = QMessageBox.question(self, "Transcription is running",
                "A transcription is still running.\n\nStop it now, save what is done so far as a partial version, and close the program?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer == QMessageBox.Yes:
                self._close_after_stop = True
                self._queue_running = False
                self.transcription_worker.stop()
                self.progress_label.setText("Stopping · saving completed segments, then closing…")
                self.stop_transcription_btn.setEnabled(False); self.pause_transcription_btn.setEnabled(False)
            event.ignore()
            return
        if self._audio_worker_running():
            QMessageBox.warning(self, "Processing still running",
                "A transcription or speaker-identification task is still running. Wait for it to finish before closing Local SessionKeeper so the database and transcript are not interrupted.")
            event.ignore()
            return
        for editor in list(getattr(self,"_open_editors",[])):
            try: editor.close()  # Editor close/finished handlers perform the final autosave and export.
            except Exception: self.logger.exception("Could not close open transcript editor during application shutdown")
        # An editor that could not save stays open; keep the application open with it.
        if any(editor.isVisible() for editor in getattr(self,"_open_editors",[])):
            event.ignore()
            return
        event.accept()
