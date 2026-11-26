import json, uuid, copy, re, bisect, time, math, datetime, inspect
from difflib import SequenceMatcher
from pathlib import Path
from PySide6.QtCore import Qt, QUrl, QTimer, QThread, Signal, QProcess
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QPushButton, QDialogButtonBox, QHeaderView, QLineEdit, QFormLayout,
    QGroupBox, QMessageBox, QComboBox, QInputDialog, QCheckBox, QPlainTextEdit, QSplitter, QListWidget,
    QListWidgetItem, QAbstractItemView, QScrollArea, QTabWidget, QGridLayout, QSlider, QWidget)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QMediaDevices
from .run_import import possible_duplicates
from .quality import looks_repetitive
from .compare_logic import split_sentence_candidates, split_context_indexes
from .media import LISTEN_OPTIONS, listening_cache_path, listening_command
from .name_review import NameIndex, lowercase_vocabulary, apply_to_text
from .theme import tone   # v2.46.1: shared colours

# v2.47: speaker colours come from the scheme chosen in Colours… (theme.py), and each speaker also has a symbol,
# so speakers are never told apart by colour alone.
from .theme import speaker_colour, speaker_symbol, contrast_text
from PySide6.QtWidgets import QProgressBar, QApplication
# v2.48: at most this many rows keep their six widgets; past it, rows far off screen give them back (down to KEEP).
# Qt checks every row that has widgets each time the view moves, so without a limit scrolling got slower the longer you
# worked (Witchlight 07: 49 ms a page with 300 built rows, 1.2 s with 2,100).
MAX_BUILT_ROWS = 240
KEEP_BUILT_ROWS = 160
REVIEWED_TINT = "QPlainTextEdit{background-color: rgba(88,166,255,0.13);}"   # v2.47: faint blue, readable on light and dark

def fmt_time(seconds):
    seconds=max(0.0,float(seconds)); whole=int(seconds); ms=int(round((seconds-whole)*1000))
    if ms >= 1000: whole += 1; ms = 0
    h, rem = divmod(whole,3600); m,s=divmod(rem,60)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"

def parse_time(value):
    value=value.strip()
    if not value: raise ValueError("Timestamp is empty.")
    if ":" not in value: result=float(value)
    else:
        parts=value.split(":")
        if len(parts)!=3: raise ValueError("Use HH:MM:SS.mmm or seconds.")
        result=int(parts[0])*3600+int(parts[1])*60+float(parts[2])
    # float() happily accepts "nan" and "inf"; either would corrupt the saved transcript.
    if not math.isfinite(result) or result<0: raise ValueError("Timestamp must be a finite, non-negative time.")
    return result

def similar(a, b, threshold):
    """Cheap-to-expensive SequenceMatcher checks; returns the ratio or 0.0 if below threshold."""
    matcher=SequenceMatcher(None,a,b)
    if matcher.real_quick_ratio()<threshold or matcher.quick_ratio()<threshold: return 0.0
    ratio=matcher.ratio()
    return ratio if ratio>=threshold else 0.0


def _log_if_slow(limit_ms=120):
    """Log (to the log file) any editor step that blocks the window longer than limit_ms, so lag can be traced."""
    def wrap(fn):
        # Qt passes a signal's values (a filter index, a button's "checked" flag) to the slot.
        # Qt drops them for a plain method that takes none, but this wrapper takes *a, so Qt
        # sends them and the wrapped method crashed (v2.29r1 bug). Pass on only what fn accepts.
        params=list(inspect.signature(fn).parameters.values())[1:]
        if any(p.kind==p.VAR_POSITIONAL for p in params): max_args=None
        else: max_args=sum(1 for p in params if p.kind in (p.POSITIONAL_ONLY,p.POSITIONAL_OR_KEYWORD))
        def inner(self,*a,**k):
            if max_args is not None: a=a[:max_args]
            t=time.perf_counter()
            try: return fn(self,*a,**k)
            finally:
                ms=(time.perf_counter()-t)*1000
                lg=getattr(self,"logger",None)
                if ms>limit_ms and lg: lg.warning("Slow editor step | %s | %.0f ms",fn.__name__,ms)
        inner.__name__=fn.__name__
        return inner
    return wrap


class TranscriptSaveWorker(QThread):
    """Persist a captured editor snapshot using its own SQLite connection."""
    saved = Signal(object)
    failed = Signal(str)

    def __init__(self, db_path, run_id, draft_path, payload):
        super().__init__()
        self.db_path = str(db_path)
        self.run_id = str(run_id)
        self.draft_path = Path(draft_path)
        self.payload = payload
        self.error_message = ""

    def run(self):
        db = None
        try:
            self.draft_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.draft_path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(self.payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(self.draft_path)
            from .database import Database
            # startup=False: a worker connection must never run migrations or
            # crash-recovery (which would re-queue jobs that are really running).
            db = Database(self.db_path, startup=False)
            before_rowid = db.transcript_edit_event_cursor(self.run_id)
            db.update_transcript_run(
                self.run_id, self.payload["text"],
                json.dumps(self.payload["segments"], ensure_ascii=False),
                json.dumps(self.payload["speaker_map"], ensure_ascii=False)
            )
            events = [dict(x) for x in db.transcript_edit_events_since(self.run_id, before_rowid)]
            self.saved.emit(events)
        except Exception as exc:
            self.error_message = str(exc)
            self.failed.emit(str(exc))
        finally:
            if db is not None:
                try: db.close()
                except Exception: pass


class TranscriptEditor(QDialog):
    def __init__(self, run, parent=None, audio_path=None, vocabulary_terms=None, edit_events=None, vocabulary_aliases=None, backup_dir="backups", autosave_callback=None, database_path=None, logger=None, name_db=None, campaign_id=None, book_terms=None):
        build_started=time.perf_counter()
        super().__init__(parent)
        # Real window controls: maximise, minimise and Windows snap (drag to an edge) work like any other window.
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True); self.setWindowFlag(Qt.WindowMinimizeButtonHint, True); self.setSizeGripEnabled(True)
        self.run=run; self.audio_path=audio_path; self.backup_dir=Path(backup_dir); self.draft_path=self.backup_dir / "transcript_drafts" / f"{run['id']}.json"; self.vocabulary_terms=sorted(set(vocabulary_terms or []), key=str.casefold); self.vocabulary_aliases=dict(vocabulary_aliases or {}); self.edit_events=edit_events or []; self.autosave_callback=autosave_callback; self.database_path=database_path; self.logger=logger
        self.name_db=name_db; self.campaign_id=campaign_id
        self._save_worker=None; self._retired_workers=[]; self._pending_save_payload=None; self._save_error=""
        self._final_autosave_flushed=False; self._deferred_history_events=[]
        self._dirty=False; self._discard_unsaved=False
        self._requested_seek_ms=None; self._seek_requested_at=0.0; self._filtered_playback_queue=[]
        self._pending_mapping_sids=set(); self._pending_mapping_role_sids=set()
        self._speaker_combo_widgets={}; self._speaker_ids_cache=None
        self._vocabulary_by_length={}
        for term in self.vocabulary_terms:
            self._vocabulary_by_length.setdefault(len(term), []).append(term)
        # v2.46: book names (ticked in D&D books…) by first letter and length, matched at 80% (campaign names: 70%):
        # 30,000 book names would otherwise make the panel slow and noisy
        self._book_terms={}
        campaign={t.casefold() for t in self.vocabulary_terms}
        for term in set(book_terms or []):
            if len(term)>=4 and term.casefold() not in campaign:
                self._book_terms.setdefault((term[0].casefold(),len(term)),[]).append(term)
        self._aliases_by_length={}
        for alias, canonical in self.vocabulary_aliases.items():
            self._aliases_by_length.setdefault(len(alias), []).append((alias, canonical))
        self._audio_recovery_attempted=False
        self._speaker_merge_undo=None
        self._draft_recovered = False
        if self.draft_path.is_file():
            try:
                draft = json.loads(self.draft_path.read_text(encoding="utf-8"))
                if QMessageBox.question(self, "Recover transcript draft", "An autosaved draft exists for this transcript. Recover the draft instead of the last saved version?", QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) == QMessageBox.Yes:
                    run = dict(run); run["segments_json"] = json.dumps(draft.get("segments", []), ensure_ascii=False); run["speaker_map_json"] = json.dumps(draft.get("speaker_map", {}), ensure_ascii=False); self.run = run; self._draft_recovered = True; self._dirty = True
            except Exception:
                pass
        try: run_meta=json.loads(run["metadata_json"] or "{}")
        except Exception: run_meta={}
        status_label=" · PARTIAL / STOPPED" if run_meta.get("stopped_early") or str(run_meta.get("completion_status","")).startswith("PARTIAL") else ""
        recording_label=str(run.get("recording_name", "")).strip() if hasattr(run, "get") else ""
        if not recording_label:
            recording_label=str(run_meta.get("recording_name", "")).strip()
        title_prefix=f"{recording_label} · " if recording_label else ""
        self.setWindowTitle(f"{title_prefix}Transcript editor{status_label} · {run['model_name']} · {run['device']}")
        self.resize(1450,900)
        self.original_segments=json.loads(run['segments_json'] or '[]')
        self.raw_segments=json.loads(run['raw_segments_json'] or '[]') if 'raw_segments_json' in run.keys() else self.original_segments
        self.speaker_map=json.loads(run['speaker_map_json'] or '{}') if 'speaker_map_json' in run.keys() else {}
        self.segments=[]
        for seg in self.original_segments:
            item=copy.deepcopy(seg); item.setdefault('_edit_id',str(uuid.uuid4())); item.setdefault('confirmed_voice_sample',False); self.segments.append(item)
        self._build()
        self._undo_timer=QTimer(self); self._undo_timer.setSingleShot(True); self._undo_timer.setInterval(700); self._undo_timer.timeout.connect(self._undo_capture)
        self._undo_reset()
        self._draft_timer = QTimer(self); self._draft_timer.setSingleShot(True); self._draft_timer.setInterval(1000); self._draft_timer.timeout.connect(self._write_draft)
        if self._draft_recovered: self.history.addItem("Recovered autosaved draft")
        if self.logger:
            self.logger.info("Transcript editor loaded | recording=%s | run=%s | segments=%s | speakers=%s | build_ms=%.1f",
                recording_label or "unknown", self.run["id"], len(self.segments), len(self._speaker_ids()), (time.perf_counter()-build_started)*1000)
        from .window_state import remember; remember(self)   # v2.46.1: opens at the size and place you left it
        from .notices import attach; attach(self)   # v2.47: yellow note bar at the bottom

    def _build(self):
        root=QVBoxLayout(self)
        root.addWidget(QLabel("Edit transcript words, timestamps and speakers. Original audio and raw Whisper output remain unchanged. F1: keyboard shortcuts."))
        playback=QGridLayout()
        self.play_previous_btn=QPushButton("◀ Previous segment")
        self.play_previous_same_btn=QPushButton("◀ Previous same speaker")
        self.set_end_btn=QPushButton("▶ Play start / set end")
        self.play_segment_btn=QPushButton("▶ Play segment")
        self.play_from_btn=QPushButton("▶ Play from here")
        self.play_filtered_btn=QPushButton("▶ Play filtered segments")
        self.pause_btn=QPushButton("Ⅱ Pause")
        self.rewind_btn=QPushButton("↶ 1 second")
        self.stop_btn=QPushButton("■ Stop")
        self.playback_label=QLabel("Audio ready" if self.audio_path else "Recording path unavailable")
        self.volume_slider=QSlider(Qt.Horizontal); self.volume_slider.setRange(0,100); self.volume_slider.setValue(100); self.volume_slider.setMinimumWidth(120)
        self.volume_label=QLabel("100%"); self.volume_label.setMinimumWidth(36)
        self.boost_combo=QComboBox()
        for label,_filter in LISTEN_OPTIONS: self.boost_combo.addItem(label)
        self.boost_combo.setToolTip("For quiet recordings: listen to a louder copy (above 100%) or the levelled audio Whisper hears. Only changes what you hear; the recording and transcript are untouched. The first time takes a short while to prepare.")
        playback.setHorizontalSpacing(6); playback.setVerticalSpacing(4)
        for col,w in enumerate((self.play_segment_btn,self.play_from_btn,self.play_filtered_btn,self.pause_btn,self.stop_btn)): playback.addWidget(w,0,col)
        for col,w in enumerate((self.play_previous_btn,self.play_previous_same_btn,self.rewind_btn,self.set_end_btn)): playback.addWidget(w,1,col)
        self.seek_slider=QSlider(Qt.Horizontal); self.seek_slider.setRange(0,0); self.seek_slider.setMinimumWidth(160)
        self.seek_slider.setToolTip("Playback position. Drag or click to jump to any point in the recording.")
        self.seek_label=QLabel("00:00:00 / 00:00:00"); self.seek_label.setMinimumWidth(120)
        self._seek_dragging=False
        self.speed_slider=QSlider(Qt.Horizontal); self.speed_slider.setRange(2,10); self.speed_slider.setValue(4); self.speed_slider.setMinimumWidth(120)
        self.speed_slider.setToolTip("Playback speed 0.5x to 2.5x (Shift+< slower, Shift+> faster, as on YouTube; Ctrl+[ and Ctrl+] work too). Always starts at 1x when the editor opens; pitch is kept where your Qt version supports it.")
        self.speed_label=QPushButton("1.00x"); self.speed_label.setFlat(True); self.speed_label.setMinimumWidth(48); self.speed_label.setToolTip("Click to reset to normal speed")
        self.speed_slider.valueChanged.connect(self._speed_changed); self.speed_label.clicked.connect(lambda: self.speed_slider.setValue(4))
        self.set_start_here_btn=QPushButton("⇤ Set start = playhead"); self.set_start_here_btn.setToolTip("Set the selected segment's start time to the current playback position.")
        self.set_end_here_btn=QPushButton("⇥ Set end = playhead"); self.set_end_here_btn.setToolTip("Set the selected segment's end time to the current playback position.")
        self.set_start_here_btn.clicked.connect(lambda: self.set_time_from_playhead(0)); self.set_end_here_btn.clicked.connect(lambda: self.set_time_from_playhead(1))
        playback.addWidget(self.set_start_here_btn,2,0,1,2); playback.addWidget(self.set_end_here_btn,2,2,1,2)
        # Right-hand column, top to bottom: position, speed, volume, listen version
        playback.addWidget(QLabel("Position"),0,5); playback.addWidget(self.seek_slider,0,6); playback.addWidget(self.seek_label,0,7)
        playback.addWidget(QLabel("Speed"),1,5); playback.addWidget(self.speed_slider,1,6); playback.addWidget(self.speed_label,1,7)
        playback.addWidget(QLabel("Volume"),2,5); playback.addWidget(self.volume_slider,2,6); playback.addWidget(self.volume_label,2,7)
        playback.addWidget(QLabel("Listen"),3,5); playback.addWidget(self.boost_combo,3,6,1,2)
        playback.addWidget(self.playback_label,4,0,1,8); root.addLayout(playback)
        self.seek_slider.sliderPressed.connect(lambda: setattr(self,"_seek_dragging",True))
        self.seek_slider.sliderReleased.connect(self._seek_released)
        self.seek_slider.actionTriggered.connect(lambda _a: QTimer.singleShot(0,self._seek_to_slider))
        self.seek_slider.sliderMoved.connect(lambda v: self.seek_label.setText(f"{fmt_time(v/1000)} / {fmt_time(self.player.duration()/1000)}"))
        self.player=QMediaPlayer(self); self.audio_output=QAudioOutput(self); self.player.setAudioOutput(self.audio_output)
        self._play_path=self.audio_path; self._boost_process=None; self._boost_target=None
        try:
            if hasattr(self.player,"setPitchCompensation"): self.player.setPitchCompensation(True)  # keep voices at normal pitch when sped up / slowed
        except Exception: pass
        self.volume_slider.valueChanged.connect(self._volume_changed); self.boost_combo.currentIndexChanged.connect(self._listening_changed)
        if self.audio_path and Path(self.audio_path).is_file(): self.player.setSource(QUrl.fromLocalFile(str(Path(self.audio_path).resolve())))
        else:
            for b in (self.play_segment_btn,self.play_previous_btn,self.play_previous_same_btn,self.play_from_btn,self.play_filtered_btn,self.pause_btn,self.rewind_btn,self.set_end_btn,self.stop_btn,self.volume_slider,self.boost_combo,self.seek_slider,self.set_start_here_btn,self.set_end_here_btn): b.setEnabled(False)
        self.play_segment_btn.clicked.connect(self.play_selected_segment); self.play_previous_btn.clicked.connect(self.play_previous_segment); self.play_previous_same_btn.clicked.connect(self.play_previous_same_speaker); self.play_from_btn.clicked.connect(self.play_from_selected); self.play_filtered_btn.clicked.connect(self.play_filtered_segments); self.set_end_btn.clicked.connect(self.toggle_segment_end_capture)
        self.pause_btn.clicked.connect(self.toggle_pause); self.rewind_btn.clicked.connect(self.rewind_one_second); self.stop_btn.clicked.connect(self.stop_playback)
        self.player.durationChanged.connect(lambda d: self.seek_slider.setRange(0,max(0,int(d))))
        self.player.positionChanged.connect(self._playback_position); self.player.playbackStateChanged.connect(self._playback_state)
        self.player.errorOccurred.connect(self._audio_error)
        self.player.mediaStatusChanged.connect(self._audio_media_status_changed)
        self.media_devices=QMediaDevices(self); self.media_devices.audioOutputsChanged.connect(self._audio_devices_changed)
        self._last_default_output=self._default_output_id()
        self._audio_recovery_pending=False; self._audio_recovery_position=0; self._audio_recovery_resume=False
        self._audio_recovery_scroll=None; self._hold_scroll_until=0.0
        self.segment_stop_ms=None
        self._end_capture_row=None
        self._highlighted_playback_row = -1
        self._playback_starts = []
        self._playback_intervals = []
        filter_row=QHBoxLayout(); filter_row.addWidget(QLabel("Show:"))
        self.speaker_filter=QComboBox()
        self.speaker_filter.addItem("All segments", "all")
        self.speaker_filter.addItem("All Speaker_## only", "speaker_ids")
        self.speaker_filter.addItem("Unassigned", "unassigned")
        self.speaker_filter.addItem("Guests", "role:Guest")
        self.speaker_filter.addItem("Background / non-player", "role:Background")
        self.speaker_filter.addItem("Unknown speakers", "role:Unknown")
        self.speaker_filter.addItem("⚠ Possible duplicates", "dupes")
        self.speaker_filter.addItem("⚠ Repeating text (possible Whisper loop)", "loops")
        self.speaker_filter.addItem("⚠ Possible split sentences (same speaker, with context rows)", "split")
        self.speaker_filter.currentIndexChanged.connect(self.apply_speaker_filter)
        filter_row.addWidget(self.speaker_filter)
        self.undo_btn=QPushButton("↶ Undo"); self.redo_btn=QPushButton("↷ Redo"); self.find_toggle_btn=QPushButton("🔍 Find / replace"); self.next_unreviewed_btn=QPushButton("Next unreviewed ▶")
        self.undo_btn.setToolTip("Undo the last change to the segments (Ctrl+Z when not typing in a text box)"); self.redo_btn.setToolTip("Redo (Ctrl+Y)")
        self.find_toggle_btn.setToolTip("Ctrl+F"); self.find_toggle_btn.setCheckable(True); self.next_unreviewed_btn.setToolTip("Select the next segment you have not ticked Reviewed (Ctrl+J, Ctrl+Shift+J for previous)")
        self.undo_btn.setEnabled(False); self.redo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(self.undo); self.redo_btn.clicked.connect(self.redo); self.find_toggle_btn.toggled.connect(self._toggle_find_bar); self.next_unreviewed_btn.clicked.connect(lambda: self.jump_unreviewed())
        for b in (self.undo_btn,self.redo_btn,self.find_toggle_btn,self.next_unreviewed_btn): filter_row.addWidget(b)
        filter_row.addStretch(); root.addLayout(filter_row)
        self.find_bar=QWidget(); fb=QHBoxLayout(self.find_bar); fb.setContentsMargins(0,0,0,0)
        self.find_edit=QLineEdit(); self.find_edit.setPlaceholderText("Find in transcript text"); self.replace_edit=QLineEdit(); self.replace_edit.setPlaceholderText("Replace with")
        self.find_case=QCheckBox("Match case"); self.find_count=QLabel("")
        self.find_prev_btn=QPushButton("◀"); self.find_next_btn=QPushButton("▶"); self.replace_btn=QPushButton("Replace"); self.replace_all_btn=QPushButton("Replace all…")
        for b in (self.find_prev_btn,self.find_next_btn): b.setMaximumWidth(34)
        for w in (self.find_edit,self.find_prev_btn,self.find_next_btn,self.replace_edit,self.replace_btn,self.replace_all_btn,self.find_case,self.find_count): fb.addWidget(w)
        fb.setStretch(0,2); fb.setStretch(3,2)
        self.find_bar.setToolTip("Searches the segments currently shown (respects the Show filter). Reviewed segments are included, so check before Replace all.")
        self._find_timer=QTimer(self); self._find_timer.setSingleShot(True); self._find_timer.setInterval(300); self._find_timer.timeout.connect(self._update_find_count)
        self.find_edit.textChanged.connect(lambda *_: self._find_timer.start()); self.find_case.toggled.connect(lambda *_: self._find_timer.start())
        self.find_edit.returnPressed.connect(lambda: self.find_step(1))
        self.find_next_btn.clicked.connect(lambda: self.find_step(1)); self.find_prev_btn.clicked.connect(lambda: self.find_step(-1))
        self.replace_btn.clicked.connect(self.replace_current); self.replace_all_btn.clicked.connect(self.replace_all)
        self.find_bar.hide(); root.addWidget(self.find_bar)
        for keys,fn in (("Ctrl+Z",self.undo),("Ctrl+Y",self.redo),("Ctrl+Shift+Z",self.redo),("Ctrl+F",self._focus_find),("F3",lambda: self.find_step(1)),("Shift+F3",lambda: self.find_step(-1)),
                        ("Ctrl+J",lambda: self.jump_unreviewed()),("Ctrl+Shift+J",lambda: self.jump_unreviewed(True)),("Ctrl+]",lambda: self._nudge_speed(1)),("Ctrl+[",lambda: self._nudge_speed(-1)),
                        # v2.46.1: playback and reviewing from the keyboard, also while typing in a line
                        ("Ctrl+Space",self.toggle_play_segment),("Alt+Left",self.play_previous_segment),("Alt+Right",self.play_next_segment),
                        ("Ctrl+Return",self.review_and_next),("Ctrl+Enter",self.review_and_next),("F1",self.show_shortcuts),
                        # v2.47: like YouTube, Shift+> faster and Shift+< slower (in steps of 0.25x)
                        (">",lambda: self._nudge_speed(1)),("<",lambda: self._nudge_speed(-1))):
            QShortcut(QKeySequence(keys),self,activated=fn)
        self.play_segment_btn.setToolTip("Play the selected segment (Ctrl+Space plays or pauses it)")
        self.play_previous_btn.setToolTip("Select and play the segment before (Alt+←; Alt+→ for the one after)")
        self.pause_btn.setToolTip("Pause or resume (Ctrl+Space)")
        self.coverage_label=QLabel(""); self.coverage_label.setWordWrap(True)
        self.coverage_label.setToolTip("Segments ticked 'Reviewed' are locked: Identify / re-check speakers never changes them, and uses them to fix the rest. About a minute per person, in short clips from different parts of the session, is a good start.")
        self.dupe_label=QLabel(""); tone(self.dupe_label,"warn"); self.dupe_label.setWordWrap(True); self.dupe_label.hide()
        # v2.47: how much of the transcript is ticked Reviewed, as a bar
        self.review_bar=QProgressBar(); self.review_bar.setRange(0,1); self.review_bar.setValue(0); self.review_bar.setFormat("Reviewed: %v / %m lines (%p%)")
        self.review_bar.setToolTip("Lines ticked Reviewed, out of all lines in this transcript (whatever the Show filter shows).")
        root.addWidget(self.review_bar)
        root.addWidget(self.coverage_label); root.addWidget(self.dupe_label)
        self._coverage_timer=QTimer(self); self._coverage_timer.setSingleShot(True); self._coverage_timer.setInterval(900)
        self._coverage_timer.timeout.connect(self._update_coverage)
        split=QSplitter(Qt.Horizontal)
        self.table=QTableWidget(0,6); self.table.setHorizontalHeaderLabels(["Start","End","Speaker","Transcript text","Reviewed","Sample"])
        # v2.47: short headers; the details are in each header's tooltip
        for col,tip in ((0,"Start time, HH:MM:SS.mmm"),(1,"End time, HH:MM:SS.mmm"),
                        (4,"Speaker confirmed: you listened and the speaker is right. Locked: Identify / re-check speakers never changes "
                           "the line and uses it to correct the others. Ctrl+Enter ticks it and moves to the next unreviewed line."),
                        (5,"Use this line as a voice sample (clean, single-speaker speech, ideally 10-20 seconds).")):
            self.table.horizontalHeaderItem(col).setToolTip(tip)
        self.table.horizontalHeader().setSectionResizeMode(3,QHeaderView.Stretch)
        self._staged={}  # segments whose row widgets are not built yet (lazy rows)
        self._visible_timer=QTimer(self); self._visible_timer.setSingleShot(True); self._visible_timer.setInterval(30)
        self._visible_timer.timeout.connect(self._materialise_visible)
        self.table.verticalScrollBar().valueChanged.connect(self._schedule_visible_rows)
        self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4,QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5,QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setMinimumSectionSize(70)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)
        # v2.42: on screen the speaker sits with the other controls, after the text (column numbers are unchanged)
        from .column_order import arrange
        arrange(self.table.horizontalHeader(),"editor_segments",[0,1,3,2,4,5])
        self.table.horizontalHeader().setTextElideMode(Qt.ElideNone)
        self.table.horizontalHeader().setMinimumHeight(48)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setColumnWidth(0, 125); self.table.setColumnWidth(1, 125); self.table.setColumnWidth(2, 190); self.table.setColumnWidth(4, 105); self.table.setColumnWidth(5, 100)
        self.table.setAlternatingRowColors(True); self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setWordWrap(True); self.table.verticalHeader().setDefaultSectionSize(48)
        left_split=QSplitter(Qt.Vertical); left_split.setChildrenCollapsible(False); left_split.setHandleWidth(8)
        left_split.addWidget(self.table); split.addWidget(left_split)
        split.setHandleWidth(8); split.setChildrenCollapsible(False)
        suggestion_box=QGroupBox("Vocabulary suggestions")
        suggestion_layout=QVBoxLayout(suggestion_box)
        self.suggestion_hint=QLabel("Campaign names (70% close or more) and names ticked in D&D books… (80%, capitalised words only). Suggestions are never applied automatically."); self.suggestion_hint.setWordWrap(True); suggestion_layout.addWidget(self.suggestion_hint)
        self.suggestion_list=QListWidget(); suggestion_layout.addWidget(self.suggestion_list,1)
        self.accept_suggestion_btn=QPushButton("Apply selected suggestion")
        self.reject_suggestion_btn=QPushButton("Dismiss suggestion")
        suggestion_layout.addWidget(self.accept_suggestion_btn); suggestion_layout.addWidget(self.reject_suggestion_btn)
        self.accept_suggestion_btn.clicked.connect(self.apply_suggestion); self.reject_suggestion_btn.clicked.connect(self._dismiss_suggestion)
        suggestion_box.setMinimumWidth(230)
        split.addWidget(suggestion_box); split.setStretchFactor(0,5); split.setStretchFactor(1,1); split.setSizes([900,300])
        self.suggestion_box = suggestion_box
        self._suggestion_timer=QTimer(self); self._suggestion_timer.setSingleShot(True); self._suggestion_timer.setInterval(250)
        self._suggestion_timer.timeout.connect(self.update_suggestions)
        self._text_resize_timer=QTimer(self); self._text_resize_timer.setSingleShot(True); self._text_resize_timer.setInterval(70)
        self._pending_resize_editor=None
        self._text_resize_timer.timeout.connect(self._apply_pending_text_resize)
        self.suggestion_toggle = QPushButton("Hide suggestions")
        self.suggestion_toggle.setCheckable(True)
        self.suggestion_toggle.toggled.connect(self.toggle_suggestions)
        root.addWidget(self.suggestion_toggle)
        root.addWidget(split,1)
        self.history_tabs=QTabWidget()
        self.history=QListWidget(); self.history.setWordWrap(True); self.history.setAlternatingRowColors(True)
        self.history_tabs.addTab(self.history, "Audit history")
        # The history lives in its own resizable window so many lines are readable at once.
        self.history_window=QDialog(self); self.history_window.setWindowTitle("Audit history"); self.history_window.resize(760,520)
        self.history_window.setWindowFlag(Qt.WindowMaximizeButtonHint,True)
        hw=QVBoxLayout(self.history_window); hw.addWidget(self.history_tabs)
        from .window_state import remember; remember(self.history_window,"AuditHistory")   # v2.46.1
        self.history_window.finished.connect(lambda *_: self.history_toggle.setChecked(False))
        self.history_toggle=QPushButton("Show audit history")
        self.history_toggle.setCheckable(True)
        self.history_toggle.toggled.connect(self._toggle_history)
        self.table.currentCellChanged.connect(self._editor_selection_changed)
        TOOLTIPS={"＋ Add below":"Add an empty segment below the selected one (fills any gap before the next segment)","Remove":"Remove the selected segment","Merge previous":"Merge the selected segment into the one before it",
                  "Remove duplicates…":"Remove unreviewed segments that overlap a reviewed segment and only repeat its words","Add speaker":"Add a new speaker ID","Merge IDs…":"Merge two speaker IDs into one","Undo merge":"Undo the last speaker merge",
                  "Reset…":"Reset speaker assignments","Restore raw":"Restore the original Whisper transcript",
                  "Correct names…":"Find possible misspelled campaign names and fix the ones you tick (nothing changes until you apply; Reviewed segments are never changed)",
                  "Re-transcribe range…":"Run Whisper again on the selected rows' time range (or a range you type), then compare row by row and pick what to use. Reviewed segments are always kept.",
                  "Split at | …":"Split lines where people talk over each other. Type | where a new speaker starts and {name} for who said it, e.g. {Sam} Okay! | {Alex} Yay! | {Kit} Let's go. The name can be a player, nickname or character. Shows what will change first; lines given a name are marked Reviewed. Ctrl+Z undoes it.",
                  "Alternatives…":"Re-transcribe the selected rows' stretch (up to 2 minutes) three ways: a more careful search (beam size 10), with your campaign names as a prompt, and looser (a little randomness, quieter speech kept). Shows the versions side by side; the one you pick opens in the compare window. Reviewed segments are always kept.",
                  "Roll finder…":"Find the checks asked for in this session and what people said they rolled, next to the bonus on their character's D&D Beyond snapshot. Read-only.",
                  "Suggested ranges…":"List places where Whisper may have missed or garbled speech (gaps with sound, repeated text, low confidence, very long segments), listen to them and re-transcribe the ones you choose. Nothing changes from the list itself."}
        def action_group(title,items):
            box=QGroupBox(title); grid=QGridLayout(box); grid.setContentsMargins(8,6,8,8); grid.setHorizontalSpacing(6); grid.setVerticalSpacing(4)
            for index,(label,fn) in enumerate(items):
                b=QPushButton(label); b.clicked.connect(fn); b.setToolTip(TOOLTIPS.get(label,label))
                b.setStyleSheet("QPushButton{padding:3px 6px;}"); b.setMinimumWidth(0); grid.addWidget(b,index//2,index%2)
            return box
        actions=QGridLayout(); actions.setSpacing(8); actions.setContentsMargins(0,0,0,0)
        actions.addWidget(action_group("Segments",[("＋ Add below",self.add_below),("Remove",self.remove_selected),
            ("Split at cursor",self.split_at_cursor),("Split at | …",self.split_markup),("Merge previous",self.merge_previous),
            ("Remove duplicates…",self.remove_duplicates)]),0,0)
        actions.addWidget(action_group("Speakers",[("Add speaker",self.add_speaker),("Merge IDs…",self.merge_speakers),
            ("Undo merge",self.undo_speaker_merge),("Reset…",self.reset_speaker_assignments)]),0,1)
        actions.addWidget(action_group("Transcript",[("Restore raw",self.restore_raw_transcript),("Correct names…",self.correct_names),("Re-transcribe range…",self.retranscribe_range),
            ("Alternatives…",self.alternatives),("Suggested ranges…",self.suggest_ranges),("Roll finder…",self.roll_finder)]),1,0,1,2)
        actions.setRowStretch(2,1)
        actions_panel=QWidget(); actions_panel.setLayout(actions)
        mapping_box=QGroupBox("Speaker-to-player mapping")
        ml=QVBoxLayout(mapping_box); ml.addWidget(QLabel("Map the physical speaker to a player or DM. Character is optional; the DM remains one speaker regardless of NPC voices."))
        self.mapping_table=QTableWidget(0,6); self.mapping_table.setHorizontalHeaderLabels(["Speaker ID","Role","Player / person","Player nicknames","Character (optional)","Include in notes"])
        self.mapping_table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeToContents); self.mapping_table.horizontalHeader().setSectionResizeMode(3,QHeaderView.Interactive); self.mapping_table.horizontalHeader().setSectionResizeMode(4,QHeaderView.Interactive); self.mapping_table.setColumnWidth(3,150); self.mapping_table.setColumnWidth(4,150)
        ml.addWidget(self.mapping_table)
        # bottom of the left column: speaker-to-player mapping, then the segment/speaker/transcript buttons.
        # The vocabulary suggestions column on the right runs the full height beside all of it.
        bottom_panel=QWidget(); bp=QVBoxLayout(bottom_panel); bp.setContentsMargins(0,0,0,0)
        bp.addWidget(self.history_toggle)
        bottom=QHBoxLayout(); bottom.addWidget(mapping_box,3); bottom.addWidget(actions_panel,2); bp.addLayout(bottom,1)
        left_split.addWidget(bottom_panel); left_split.setStretchFactor(0,3); left_split.setStretchFactor(1,2); left_split.setSizes([520,280])
        self._populate_speakers()
        self._refresh_filter_speakers()
        self._mapping_refresh_timer=QTimer(self); self._mapping_refresh_timer.setSingleShot(True); self._mapping_refresh_timer.setInterval(350)
        self._mapping_refresh_timer.timeout.connect(self._flush_mapping_refresh)
        self.mapping_table.itemChanged.connect(self._mapping_item_changed)
        self.mapping_table.itemChanged.connect(self._schedule_draft_save)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Close); buttons.button(QDialogButtonBox.Save).setText("Save & close"); buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); root.addWidget(buttons)
        self._render_segments()
        self._load_history()
        self._update_coverage()

    # ---- undo / redo: snapshots of the segment list, taken after each pause in editing ----
    @staticmethod
    def _state_key(segs):
        return [(s['_edit_id'],round(float(s['start']),3),round(float(s['end']),3),s['speaker_id'],s['text'],bool(s['speaker_confirmed']),bool(s['confirmed_voice_sample'])) for s in segs]

    def _undo_reset(self):
        self._undo_stack=[]; self._redo_stack=[]
        try: self._undo_state=self._read_rows(); self._undo_key=self._state_key(self._undo_state)
        except Exception: self._undo_state=None; self._undo_key=None
        self._update_undo_buttons()

    @_log_if_slow()
    def _undo_capture(self):
        timer=getattr(self,"_undo_timer",None)
        if timer is not None: timer.stop()
        if getattr(self,"_undo_key",None) is None: self._undo_reset(); return
        try: cur=self._read_rows()
        except Exception: return  # half-typed timestamp: capture on the next pause
        key=self._state_key(cur)
        if key==self._undo_key: return
        self._undo_stack.append(self._undo_state); del self._undo_stack[:-100]
        self._redo_stack.clear(); self._undo_state=cur; self._undo_key=key
        self._update_undo_buttons()

    def _update_undo_buttons(self):
        if hasattr(self,"undo_btn"):
            self.undo_btn.setEnabled(bool(getattr(self,"_undo_stack",None))); self.redo_btn.setEnabled(bool(getattr(self,"_redo_stack",None)))

    def _restore_state(self,state,label):
        row=self.table.currentRow()
        self.segments=[dict(s) for s in state]; self._speaker_ids_cache=None
        self._render_segments(); self._highlighted_playback_row=-1
        if self.table.rowCount(): self.table.setCurrentCell(min(max(row,0),self.table.rowCount()-1),3)
        self._undo_state=[dict(s) for s in state]; self._undo_key=self._state_key(state)
        self._update_undo_buttons(); self.history.addItem(label); self._schedule_draft_save()

    def undo(self):
        self._undo_capture()
        if not self._undo_stack: return
        self._redo_stack.append(self._undo_state); state=self._undo_stack.pop()
        self._restore_state(state,"Undo")

    def redo(self):
        self._undo_capture()
        if not self._redo_stack: return
        self._undo_stack.append(self._undo_state); state=self._redo_stack.pop()
        self._restore_state(state,"Redo")

    # ---- jump to next unreviewed ----
    def _row_reviewed(self,row):
        staged=self._staged_segment(row)
        if staged is not None: return bool(staged.get('speaker_confirmed',False))
        w=self.table.cellWidget(row,4)
        return bool(w and w.isChecked())

    def _go_to_row(self,row):
        self.table.setCurrentCell(row,3)
        self.table.scrollTo(self.table.model().index(row,3),QAbstractItemView.PositionAtCenter)
        self._materialise_visible(margin=5)

    def jump_unreviewed(self,backwards=False):
        n=self.table.rowCount(); cur=self.table.currentRow()
        order=list(range(cur-1,-1,-1))+list(range(n-1,cur,-1)) if backwards else list(range(cur+1,n))+list(range(0,max(cur,0)+1 if cur>=0 else 0))
        for r in order:
            if r<0 or r>=n or self.table.isRowHidden(r) or self._row_reviewed(r): continue
            self._go_to_row(r); return
        self._notify("Every segment currently shown is ticked Reviewed.")

    # ---- find / replace ----
    def _toggle_find_bar(self,visible):
        self.find_bar.setVisible(visible)
        if visible: self.find_edit.setFocus(); self.find_edit.selectAll(); self._update_find_count()

    def _focus_find(self):
        if not self.find_toggle_btn.isChecked(): self.find_toggle_btn.setChecked(True)
        else: self.find_edit.setFocus(); self.find_edit.selectAll()

    def _row_text(self,row):
        staged=self._staged_segment(row)
        if staged is not None: return str(staged.get('text',''))
        w=self.table.cellWidget(row,3)
        return w.toPlainText() if w is not None else ""

    def _find_rx(self):
        needle=self.find_edit.text()
        return re.compile(re.escape(needle),0 if self.find_case.isChecked() else re.IGNORECASE) if needle else None

    def _find_rows(self):
        rx=self._find_rx()
        if rx is None: return [],None
        return [r for r in range(self.table.rowCount()) if not self.table.isRowHidden(r) and rx.search(self._row_text(r))],rx

    def _update_find_count(self):
        rows,rx=self._find_rows()
        self.find_count.setText("" if rx is None else (f"{len(rows)} segment(s)" if rows else "no matches"))

    def find_step(self,direction=1):
        rows,rx=self._find_rows()
        if not rows: self.find_count.setText("no matches" if rx is not None else ""); return
        cur=self.table.currentRow()
        later=[r for r in rows if (r>cur if direction>0 else r<cur)]
        row=(later[0] if direction>0 else later[-1]) if later else (rows[0] if direction>0 else rows[-1])
        self._go_to_row(row)
        ed=self._cw(row,3); m=rx.search(ed.toPlainText())
        if m:
            c=ed.textCursor(); c.setPosition(m.start()); c.setPosition(m.end(),QTextCursor.KeepAnchor); ed.setTextCursor(c)
        self.find_count.setText(f"{rows.index(row)+1} of {len(rows)} segment(s)")

    def _set_row_text(self,row,text):
        staged=self._staged_segment(row)
        if staged is not None:
            staged['text']=text; item=self.table.item(row,3)
            if item is not None: item.setText(text)
        else: self.table.cellWidget(row,3).setPlainText(text)

    def replace_current(self):
        rx=self._find_rx(); row=self.table.currentRow()
        if rx is None or row<0 or self.table.isRowHidden(row): return
        text=self._row_text(row)
        if not rx.search(text): self.find_step(1); return
        repl=self.replace_edit.text()
        self._set_row_text(row,rx.sub(lambda m: repl,text,count=1))
        self.history.addItem(f"Replaced '{self.find_edit.text()}' with '{repl}' in one segment")
        self._schedule_draft_save(); self.find_step(1)

    def replace_all(self):
        rows,rx=self._find_rows()
        if not rows: return
        repl=self.replace_edit.text(); total=sum(len(rx.findall(self._row_text(r))) for r in rows)
        if QMessageBox.question(self,"Replace all",f"Replace {total} occurrence(s) of \u201c{self.find_edit.text()}\u201d with \u201c{repl}\u201d in {len(rows)} segment(s) currently shown?\n\nReviewed segments are included. You can undo this.",QMessageBox.Yes|QMessageBox.No)!=QMessageBox.Yes: return
        self._undo_capture()
        for r in rows: self._set_row_text(r,rx.sub(lambda m: repl,self._row_text(r)))
        self.history.addItem(f"Replaced '{self.find_edit.text()}' with '{repl}' ({total} in {len(rows)} segments)")
        self._schedule_draft_save(); self._undo_capture(); self._update_find_count()

    def _duplicate_sets(self):
        """Edit ids of unreviewed segments overlapping a reviewed one (flagged) and the subset whose
        words are already contained in that reviewed text (removable)."""
        segs=[]
        for r in range(self.table.rowCount()):
            try: segs.append(self._row_segment(r))
            except Exception: segs.append({"start":0,"end":0,"text":"","_edit_id":None})
        flagged,removable=possible_duplicates(segs)
        return {segs[i]['_edit_id'] for i in flagged},{segs[i]['_edit_id'] for i in removable}

    def _split_sets(self):
        segs=[]
        for r in range(self.table.rowCount()):
            try: s=dict(self._row_segment(r))
            except Exception: s={"start":0,"end":0,"text":"","_edit_id":None}
            segs.append(s)
        flagged=split_sentence_candidates(segs)
        shown=flagged|split_context_indexes(flagged,len(segs))
        return {segs[i]['_edit_id'] for i in shown if segs[i].get('_edit_id') is not None}

    def remove_duplicates(self):
        flagged,removable=self._duplicate_sets()
        if not flagged:
            self._notify("No unreviewed segments overlap your reviewed segments."); return
        if not removable:
            QMessageBox.information(self,"Possible duplicates",f"{len(flagged)} unreviewed segment(s) overlap reviewed ones, but each has words that are not in the reviewed text, so none can be removed automatically. Use Show → Possible duplicates to check them."); return
        if QMessageBox.question(self,"Remove duplicates",f"{len(removable)} unreviewed segment(s) overlap a reviewed segment and only repeat words already in its text. Nothing is removed unless you press Yes, and you can undo it. Remove them? ({len(flagged)-len(removable)} other overlapping segment(s) stay for you to check.)",QMessageBox.Yes|QMessageBox.No)!=QMessageBox.Yes: return
        try: current=self._read_rows()
        except Exception as e: QMessageBox.warning(self,"Check timestamps",str(e)); return
        self.segments=[s for s in current if s['_edit_id'] not in removable]
        self._render_segments(); self._highlighted_playback_row=-1
        self.history.addItem(f"Removed {len(removable)} duplicate segment(s) that repeated reviewed text")
        self._schedule_draft_save(); self._coverage_timer.start()

    def _dismiss_suggestion(self):
        if self.suggestion_list.currentRow()>=0: self.suggestion_list.takeItem(self.suggestion_list.currentRow())

    def _editor_selection_changed(self, *_):
        # Selecting a row is not an edit: it must not mark the transcript dirty
        # or trigger a database write. Real edits call _schedule_draft_save.
        if self.suggestion_box.isVisible():
            self._suggestion_timer.start()

    def _schedule_draft_save(self, *_):
        self._dirty=True
        self._edit_version=getattr(self,"_edit_version",0)+1   # v2.45: the roll finder beside the editor looks again when this changes
        self._final_autosave_flushed=False
        timer = getattr(self, "_draft_timer", None)
        if timer is not None: timer.start()
        coverage = getattr(self, "_coverage_timer", None)
        if coverage is not None: coverage.start()
        undo_timer = getattr(self, "_undo_timer", None)
        if undo_timer is not None: undo_timer.start()

    def _update_coverage(self):
        """Reviewed (locked) speech per speaker: shows how much evidence a re-check has to work with."""
        totals={}; reviewed_lines=0
        for row in range(self.table.rowCount()):
            try:
                seg=self._row_segment(row)
            except Exception: continue
            reviewed_lines+=bool(seg["speaker_confirmed"])
            sid=seg["speaker_id"]
            if not seg["speaker_confirmed"] or not sid: continue
            a,b=seg["start"],seg["end"]
            if b>a: totals[sid]=totals.get(sid,0.0)+(b-a)
        self.review_bar.setRange(0,max(1,self.table.rowCount())); self.review_bar.setValue(reviewed_lines)
        ids=self._speaker_ids()
        if not ids:
            self.coverage_label.setText("Reviewed speech: no speakers identified yet. Run Identify / re-check speakers first.")
            return
        def mmss(x): x=int(round(x)); return f"{x//60}:{x%60:02d}"
        parts=[f"{self._speaker_label(sid)} {mmss(totals.get(sid,0.0))}{' ✓' if totals.get(sid,0.0)>=60 else ''}" for sid in ids]
        self.coverage_label.setText("Reviewed speech (aim ~1:00 each, then close the editor and run Identify / re-check speakers): "+"  ·  ".join(parts))

    def _snapshot_payload(self):
        text_content, segments, speaker_map = self._values_raw()
        return {"run_id": self.run["id"], "saved_at": datetime.datetime.now().astimezone().isoformat(),
                "text": text_content, "segments": segments, "speaker_map": speaker_map}

    @_log_if_slow()
    def _write_draft(self):
        try:
            snapshot_started=time.perf_counter()
            payload = self._snapshot_payload()
            if self.logger:
                self.logger.debug("Transcript autosave snapshot captured | run=%s | segments=%s | capture_ms=%.1f",
                    self.run["id"], len(payload["segments"]), (time.perf_counter()-snapshot_started)*1000)
            if self.database_path:
                if self._save_worker is not None and self._save_worker.isRunning():
                    self._pending_save_payload = payload
                    return
                self._start_background_save(payload)
            else:
                # Compatibility fallback for older integrations without a DB path.
                self.draft_path.parent.mkdir(parents=True, exist_ok=True)
                temp=self.draft_path.with_suffix(".json.tmp")
                temp.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
                temp.replace(self.draft_path)
                if self.autosave_callback:
                    events=self.autosave_callback(payload["text"],json.dumps(payload["segments"],ensure_ascii=False),json.dumps(payload["speaker_map"],ensure_ascii=False)) or []
                    self._show_save_events(events)
            if self.history.count()==0 or "Draft autosaved" not in self.history.item(self.history.count()-1).text():
                self.history.addItem("Autosave queued · persistence runs in the background")
        except ValueError as exc:
            # A half-typed timestamp is normal while editing; keep the last good save.
            self._save_error=str(exc)
            self.playback_label.setToolTip("Autosave paused until this is fixed: "+str(exc))
            if self.logger: self.logger.warning("Transcript autosave skipped: %s", exc)
        except Exception as exc:
            self._save_error=str(exc)
            if self.logger: self.logger.exception("Could not capture transcript autosave snapshot")

    def _start_background_save(self, payload):
        worker=TranscriptSaveWorker(self.database_path,self.run["id"],self.draft_path,payload)
        worker.saved.connect(self._show_save_events)
        worker.failed.connect(self._save_failed)
        # Bound slot (not a lambda): Qt drops the connection if this dialog is destroyed first.
        worker.finished.connect(self._background_save_finished)
        self._save_worker=worker
        worker.start()

    def _show_save_events(self, events):
        events=list(events or [])
        if self.history_tabs.isVisible():
            for event in events:
                self._append_history_event(event)
        elif events:
            self._deferred_history_events.extend(events)
        if self.logger and events:
            # The full audit is in SQLite. Avoid one synchronous log-file write per edit.
            self.logger.debug("Transcript audit events persisted | run=%s | count=%s", self.run["id"], len(events))
        if not events and self.history.count() and "Draft autosaved" not in self.history.item(self.history.count()-1).text():
            self.history.addItem("Draft autosaved · no new audit events")

    def _append_history_event(self, event):
        before=event.get("before_value") or "∅"; after=event.get("after_value") or "∅"
        line=f'{event.get("edited_at", "")} · {event.get("event_type", "Edit")} · {before} → {after}'
        self.history.addItem(line)

    def _save_failed(self, message):
        self._save_error=message
        if self.logger: self.logger.error("Transcript background autosave failed for %s: %s",self.run["id"],message)
        self.playback_label.setToolTip("Transcript autosave error: "+message)

    def _background_save_finished(self):
        worker=self.sender()
        if worker is None: worker=self._save_worker
        # Keep the Python reference until the thread has fully stopped: dropping it here (the
        # `finished` signal fires from inside the thread) destroyed a still-running QThread and
        # crashed the app ("QThread: Destroyed while thread is still running").
        if self._save_worker is worker: self._save_worker=None
        if worker is not None: self._retired_workers.append(worker)
        self._retired_workers=[w for w in self._retired_workers if not w.isFinished()]
        if self._pending_save_payload is not None:
            payload=self._pending_save_payload; self._pending_save_payload=None
            self._start_background_save(payload)

    def _toggle_history(self, visible):
        if visible and self._deferred_history_events:
            for event in self._deferred_history_events:
                self._append_history_event(event)
            self._deferred_history_events.clear()
        if visible: self.history_window.show(); self.history_window.raise_(); self.history.scrollToBottom()
        else: self.history_window.hide()
        self.history_toggle.setText("Hide audit history window" if visible else "Show audit history")

    def flush_autosave(self):
        """Write the latest edits now. Raises if they cannot be saved (invalid timestamps, DB error)."""
        if self._final_autosave_flushed or self._discard_unsaved:
            return
        timer=getattr(self,"_draft_timer",None)
        if timer is not None: timer.stop()
        for worker in [self._save_worker]+list(self._retired_workers):
            if worker is not None and worker.isRunning(): worker.wait()
        self._save_worker=None; self._retired_workers=[]
        self._pending_save_payload=None
        if not self._dirty:
            self._final_autosave_flushed=True  # nothing was edited: do not touch the database
            return
        latest=self._snapshot_payload()  # raises ValueError for an invalid row: the caller must not close silently
        if self.database_path:
            final_worker=TranscriptSaveWorker(self.database_path,self.run["id"],self.draft_path,latest)
            final_worker.start(); final_worker.wait()
            if final_worker.error_message:
                raise RuntimeError("Final transcript autosave failed: "+final_worker.error_message)
            if self.logger: self.logger.info("Transcript final autosave flushed for run %s",self.run["id"])
        else:
            self.draft_path.parent.mkdir(parents=True,exist_ok=True)
            self.draft_path.write_text(json.dumps(latest,ensure_ascii=False,indent=2),encoding="utf-8")
            if self.autosave_callback:
                self.autosave_callback(latest["text"],json.dumps(latest["segments"],ensure_ascii=False),json.dumps(latest["speaker_map"],ensure_ascii=False))
        self._final_autosave_flushed=True

    def mark_saved(self):
        if self._discard_unsaved:
            return  # keep the draft file: it is the only copy of the unsaved edits
        try:
            if self.draft_path.exists(): self.draft_path.unlink()
        except Exception:
            pass

    def done(self, result):
        # Every way of closing (Save & close, Close, the window's X, Esc) ends here.
        # If the edits cannot be saved, keep the editor open instead of losing them.
        for attr in ("_roll_finder","_name_review","_alternatives"):
            w=getattr(self,attr,None)
            if w is not None:
                try: w.close()   # v2.45/v2.46: the windows beside the editor close with it (the roll finder saves its log)
                except Exception: pass
        if getattr(self,"_range_thread",None) is not None:
            # A QThread destroyed while running crashes the app (v2.29 lesson): wait for the range run.
            QMessageBox.information(self,"Re-transcribe range","A range is still being re-transcribed. Press Stop in its progress window, "
                                    "wait for it to finish, then close the editor.")
            return
        try:
            self.flush_autosave()
        except Exception as exc:
            if self.logger: self.logger.exception("Final transcript save failed")
            answer=QMessageBox.question(self,"Transcript could not be saved",
                f"Your latest edits could not be saved:\n\n{exc}\n\nKeep the editor open so you can fix this?\n(Choosing No closes the editor; the last autosaved draft file is kept.)",
                QMessageBox.Yes|QMessageBox.No,QMessageBox.Yes)
            if answer==QMessageBox.Yes:
                return
            self._discard_unsaved=True
        try:
            if self._boost_process is not None: self._boost_process.kill()
        except Exception: pass
        try: self.player.stop()
        except Exception: pass
        super().done(result)

    def _speaker_ids(self):
        if self._speaker_ids_cache is None:
            ids={str(s.get('speaker_id') or '').strip() for s in self.segments if str(s.get('speaker_id') or '').strip()}
            ids.update(k for k in self.speaker_map.keys() if not str(k).startswith("_"))
            self._speaker_ids_cache=sorted(ids)
        return self._speaker_ids_cache

    def _refresh_filter_speakers(self):
        current=self.speaker_filter.currentData() if hasattr(self,"speaker_filter") else "all"
        self.speaker_filter.blockSignals(True)
        while self.speaker_filter.count()>9:self.speaker_filter.removeItem(9)
        for sid in self._speaker_ids(): self.speaker_filter.addItem(self._speaker_label(sid),sid)
        idx=self.speaker_filter.findData(current); self.speaker_filter.setCurrentIndex(max(0,idx))
        self.speaker_filter.blockSignals(False)

    def _populate_speakers(self):
        self._speaker_ids_cache=None
        ids=self._speaker_ids()
        for sid in ids:
            self.speaker_map.setdefault(sid,{"role":"Unknown","player":"","character":"","include_in_notes":False})
        # Rebuilding the table must not fire itemChanged: that would mark the
        # transcript dirty and queue an autosave for something the user never edited.
        self.mapping_table.blockSignals(True)
        try:
            self.mapping_table.setRowCount(0)
            for sid in ids:
                m=self.speaker_map.get(sid,{}) ; r=self.mapping_table.rowCount(); self.mapping_table.insertRow(r)
                it=QTableWidgetItem(sid); it.setFlags(it.flags() & ~Qt.ItemIsEditable); colour=speaker_colour(sid, ids); it.setBackground(QColor(colour)); it.setForeground(QColor(contrast_text(colour))); self.mapping_table.setItem(r,0,it)
                role=QComboBox(); role.addItems(["Unknown","Guest","Background","Player","DM"]); role.setCurrentText(m.get('role','Unknown'))
                role.currentTextChanged.connect(lambda _value, speaker_id=sid: self._schedule_mapping_refresh(speaker_id, role_changed=True))
                role.currentTextChanged.connect(self._schedule_draft_save); self.mapping_table.setCellWidget(r,1,role)
                self.mapping_table.setItem(r,2,QTableWidgetItem(m.get('player',''))); self.mapping_table.setItem(r,3,QTableWidgetItem(m.get('player_nicknames',''))); self.mapping_table.setItem(r,4,QTableWidgetItem(m.get('character','')))
                cb=QCheckBox(); cb.setChecked(bool(m.get('include_in_notes',m.get('role') in {'Player','DM'}))); cb.toggled.connect(self._schedule_draft_save); self.mapping_table.setCellWidget(r,5,cb)
        finally:
            self.mapping_table.blockSignals(False)
        if hasattr(self,"speaker_filter"): self._refresh_filter_speakers()

    def _make_row_widgets(self, row, seg):
        """Create and wire the six cell widgets for one segment (used by every code path that adds a row)."""
        ids=self._speaker_ids(); edit_id=seg['_edit_id']
        start=QLineEdit(fmt_time(seg.get('start',0))); end=QLineEdit(fmt_time(seg.get('end',0)))
        start.setPlaceholderText("HH:MM:SS.mmm"); end.setPlaceholderText("HH:MM:SS.mmm")
        speaker=QComboBox(); sid=seg.get('speaker_id') or ""
        speaker.addItem("Unassigned","")
        for option in ids: speaker.addItem(self._speaker_label(option),option)
        if sid and sid not in ids: speaker.addItem(sid,sid)
        speaker.setCurrentIndex(max(0,speaker.findData(sid)))
        self._style_speaker_combo(speaker,sid)
        self._register_speaker_combo(speaker)
        text=QPlainTextEdit(); text.setPlainText(seg.get('text','')); text.setMinimumHeight(44); text.setMaximumHeight(170)
        text.setToolTip("The text area grows with its contents. You can also resize the row.")
        text.setProperty("table_row",row)
        text.installEventFilter(self)   # v2.44: Enter on a line with | offers to split it
        confirmed=QCheckBox(); confirmed.setChecked(bool(seg.get('speaker_confirmed',False))); confirmed.setToolTip("You listened and confirmed the speaker is correct. Locked: Identify / re-check speakers never changes this segment, and uses it to correct the others.")
        sample=QCheckBox(); sample.setChecked(bool(seg.get('confirmed_voice_sample',False))); sample.setToolTip(self._voice_sample_tooltip(seg.get("start",0),seg.get("end",0)))
        speaker.setProperty("initial_speaker",sid)
        widgets=(start,end,speaker,text,confirmed,sample)
        for col,widget in enumerate(widgets):
            widget.setProperty("edit_id",edit_id); self.table.setCellWidget(row,col,widget)
        # Connect only after the initial values are set so building a row never counts as an edit.
        start.editingFinished.connect(self._rebuild_playback_index); end.editingFinished.connect(self._rebuild_playback_index)
        start.editingFinished.connect(self._schedule_draft_save); end.editingFinished.connect(self._schedule_draft_save)
        speaker.currentIndexChanged.connect(lambda _=None,w=speaker,c=confirmed: self._speaker_changed(w,c))
        speaker.currentIndexChanged.connect(self._schedule_draft_save)
        text.textChanged.connect(lambda w=text: self._schedule_text_resize(w))
        text.textChanged.connect(self._schedule_draft_save)
        confirmed.toggled.connect(self._schedule_draft_save); sample.toggled.connect(self._schedule_draft_save)
        # v2.47: a Reviewed line gets a faint tint, so what is left to check stands out (set once per row, only when it changes)
        confirmed.toggled.connect(lambda on,w=text: w.setStyleSheet(REVIEWED_TINT if on else ""))
        if confirmed.isChecked(): text.setStyleSheet(REVIEWED_TINT)
        return widgets

    def _render_segments(self):
        # Suppress repeated layout/paint work while rebuilding the widget-heavy table.
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setSortingEnabled(False)
            ordered_segments = sorted(self.segments,key=lambda x:(float(x.get('start',0)),float(x.get('end',0))))
            self.table.setRowCount(0)  # drops every old widget/item before restaging
            self.table.setRowCount(len(ordered_segments))
            self._speaker_combo_widgets={}; self._staged={}; self._built_since_trim=0
            for r, seg in enumerate(ordered_segments):
                self._stage_row(r, seg)
            for r in range(self.table.rowCount()):
                self.table.setVerticalHeaderItem(r, None)  # drop stale playhead markers
            self._highlighted_playback_row=-1
            self._reindex_text_editors()
            self.apply_speaker_filter()
            self._rebuild_playback_index()
        finally:
            self.table.setUpdatesEnabled(True)
        self._materialise_visible(margin=5)
        self._schedule_visible_rows()
        self.table.viewport().update()

    def _rebuild_playback_index(self):
        intervals=[]
        for row in range(self.table.rowCount()):
            try:
                seg=self._row_segment(row); intervals.append((seg["start"],seg["end"],row))
            except Exception: continue
        intervals.sort(key=lambda x:(x[0],x[1]))
        self._playback_intervals=intervals
        self._playback_starts=[x[0] for x in intervals]

    def _speaker_label(self, sid):
        mapping=self.speaker_map.get(sid,{})
        player=(mapping.get("player") or "").strip()
        role=(mapping.get("role") or "").strip()
        suffix=player or ("DM" if role=="DM" else "Unmapped")
        nicknames=(mapping.get("player_nicknames") or "").strip()
        if nicknames: suffix += f" ({nicknames})"
        symbol=speaker_symbol(sid,self._speaker_ids())
        return f"{symbol+' ' if symbol else ''}{sid.replace('SPEAKER_', 'Speaker ')} — {suffix}"

    def _style_speaker_combo(self, widget, sid):
        ids=self._speaker_ids(); colour=speaker_colour(sid,ids); foreground=contrast_text(colour)
        widget.setStyleSheet(f"QComboBox {{ background-color: {colour}; color: {foreground}; padding: 4px; border: 1px solid #555; }} QComboBox QAbstractItemView {{ background: #FFFFFF; color: #111111; selection-background-color: #D0D7DE; selection-color: #111111; }}")

    def _mapping_item_changed(self, item):
        if item is None or item.column() not in (2, 3):
            return
        sid_item=self.mapping_table.item(item.row(), 0)
        if sid_item:
            self._schedule_mapping_refresh(sid_item.text())

    def _schedule_mapping_refresh(self, sid, role_changed=False):
        if not sid:
            return
        self._pending_mapping_sids.add(str(sid))
        if role_changed:
            self._pending_mapping_role_sids.add(str(sid))
        timer=getattr(self, "_mapping_refresh_timer", None)
        if timer is not None:
            timer.start()

    def _flush_mapping_refresh(self):
        pending=set(self._pending_mapping_sids)
        role_changed=set(self._pending_mapping_role_sids)
        self._pending_mapping_sids.clear(); self._pending_mapping_role_sids.clear()
        for sid in pending:
            row=next((r for r in range(self.mapping_table.rowCount())
                      if self.mapping_table.item(r, 0) and self.mapping_table.item(r, 0).text()==sid), -1)
            if row < 0:
                continue
            role=self.mapping_table.cellWidget(row, 1)
            include=self.mapping_table.cellWidget(row, 5)
            self.speaker_map[sid]={
                "role": role.currentText() if role else "Unknown",
                "player": self.mapping_table.item(row, 2).text().strip() if self.mapping_table.item(row, 2) else "",
                "player_nicknames": self.mapping_table.item(row, 3).text().strip() if self.mapping_table.item(row, 3) else "",
                "character": self.mapping_table.item(row, 4).text().strip() if self.mapping_table.item(row, 4) else "",
                "include_in_notes": bool(include.isChecked()) if include else False,
            }
        # Only relabel the dropdowns that contain the speaker being edited.
        # Do not rebuild every speaker dropdown or reapply the filter for a name edit.
        updated_combos=self._refresh_speaker_combo_labels(pending)
        for sid in pending:
            index=self.speaker_filter.findData(sid)
            if index >= 0:
                self.speaker_filter.setItemText(index, self._speaker_label(sid))
        if role_changed:
            self.apply_speaker_filter()
        if self.logger and pending:
            self.logger.info("Speaker mapping refresh | speakers=%s | affected_dropdowns=%s | role_filter_refresh=%s",
                len(pending), updated_combos, bool(role_changed))

    def _refresh_speaker_combo_labels(self, speaker_ids=None):
        ids=set(speaker_ids or self._speaker_ids())
        updated=0
        for sid in ids:
            live=[]
            for combo in self._speaker_combo_widgets.get(sid, []):
                try:
                    selected=combo.currentData()
                    combo.blockSignals(True)
                    try:
                        index=combo.findData(sid)
                        if index >= 0:
                            combo.setItemText(index, self._speaker_label(sid))
                        combo.setCurrentIndex(max(0, combo.findData(selected)))
                    finally:
                        combo.blockSignals(False)
                    live.append(combo)
                    updated += 1
                except RuntimeError:
                    # Qt has already deleted a widget after a row was removed.
                    continue
            self._speaker_combo_widgets[sid]=live
        return updated

    def _register_speaker_combo(self, combo):
        for index in range(combo.count()):
            sid=combo.itemData(index)
            if sid:
                self._speaker_combo_widgets.setdefault(str(sid), []).append(combo)

    def _unregister_speaker_combo(self, combo):
        if combo is None:
            return
        for sid, widgets in list(self._speaker_combo_widgets.items()):
            self._speaker_combo_widgets[sid]=[widget for widget in widgets if widget is not combo]

    def _reindex_text_editors(self, start_row=0):
        # Row positions are refreshed only after structural table edits, never per keystroke.
        for row in range(max(0, start_row), self.table.rowCount()):
            editor=self.table.cellWidget(row, 3)
            if editor is not None:
                editor.setProperty("table_row", row)

    def _find_widget_row(self, widget):
        try:
            row=int(widget.property("table_row"))
            if 0 <= row < self.table.rowCount() and self.table.cellWidget(row,3) is widget:
                return row
        except (TypeError, ValueError, RuntimeError):
            pass
        return -1

    def _schedule_text_resize(self, editor):
        self._pending_resize_editor=editor
        self._text_resize_timer.start()

    def _apply_pending_text_resize(self):
        editor=self._pending_resize_editor
        self._pending_resize_editor=None
        if editor is None:
            return
        self._resize_text_editor(editor, self._find_widget_row(editor))

    def _resize_text_editor(self, editor, row):
        try:
            if row < 0: return
            doc_height=editor.document().size().height()
            height=max(44,min(170,int(doc_height+18)))
            if editor.height()!=height: editor.setFixedHeight(height)
            if row < self.table.rowCount() and self.table.rowHeight(row)!=height+6:
                self.table.setRowHeight(row,height+6)
        except (RuntimeError, AttributeError):
            pass

    def toggle_suggestions(self, hidden):
        self.suggestion_box.setVisible(not hidden)
        self.suggestion_toggle.setText("Show vocabulary suggestions" if hidden else "Hide suggestions")
        if not hidden:
            self._suggestion_timer.start()
        else:
            self._suggestion_timer.stop()

    def toggle_pause(self):
        if self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    @staticmethod
    def _voice_sample_tooltip(start,end):
        duration=max(0.0,float(end)-float(start))
        warning=" Not recommended: shorter than 5 seconds." if duration<5 else ""
        return f"Segment duration: {duration:.2f}s. Choose clean, single-speaker speech. Aim for at least 5 seconds; 10–20 seconds is better.{warning} This does not train a model yet."

    # ---- lazy rows (v2.20.1): a row only gets its six widgets when it is on screen or touched.
    # Until then the segment waits in self._staged and a cheap placeholder item shows its time/text.
    def _cw(self,row,col):
        self._ensure_row(row)
        return self.table.cellWidget(row,col)

    def _staged_segment(self,row):
        if row<0 or row>=self.table.rowCount() or self.table.cellWidget(row,0) is not None: return None
        item=self.table.item(row,0)
        return self._staged.get(item.data(Qt.UserRole)) if item is not None else None

    def _ensure_row(self,row):
        seg=self._staged_segment(row)
        if seg is None: return
        self._staged.pop(seg['_edit_id'],None)
        self.table.takeItem(row,0); self.table.takeItem(row,3)
        self._make_row_widgets(row,seg)
        self._built_since_trim=getattr(self,"_built_since_trim",0)+1

    def _unbuild_row(self,row):
        """v2.48: give a built row's six widgets back and keep what it shows as a staged segment, exactly as when the
        editor opened. Nothing is edited: the values are read from the widgets and put back unchanged, the row keeps its
        height, and no draft save or undo step happens. Rows that cannot be read (a half-typed time), the selected row,
        the row being typed in and the row whose end is being set are never recycled. Returns True when recycled."""
        if row<0 or row>=self.table.rowCount() or self.table.cellWidget(row,0) is None: return False
        if row==self.table.currentRow() or row==self._end_capture_row: return False
        widgets=[self.table.cellWidget(row,c) for c in range(6)]
        focus=QApplication.focusWidget()
        if focus is not None and any(w is not None and (focus is w or w.isAncestorOf(focus)) for w in widgets): return False
        if self._pending_resize_editor is widgets[3]: return False
        try: seg=self._row_segment(row)
        except Exception: return False
        seg['text']=widgets[3].toPlainText()   # as typed (reading strips it; staging keeps it whole, like the widget)
        height=self.table.rowHeight(row)
        self._unregister_speaker_combo(widgets[2])
        for col in range(6): self.table.removeCellWidget(row,col)
        self._stage_row(row,seg)
        self.table.setRowHeight(row,height)
        return True

    def _trim_built_rows(self,top,bottom,margin):
        """v2.48: when more than MAX_BUILT_ROWS rows have widgets, recycle the ones farthest from the screen."""
        if getattr(self,"_built_since_trim",0)<MAX_BUILT_ROWS-KEEP_BUILT_ROWS: return 0
        built=[r for r in range(self.table.rowCount()) if self.table.cellWidget(r,0) is not None]
        self._built_since_trim=max(0,len(built)-KEEP_BUILT_ROWS)
        if len(built)<=MAX_BUILT_ROWS: return 0
        centre=(top+bottom)/2.0
        far=sorted((r for r in built if r<top-margin or r>bottom+margin),key=lambda r:-abs(r-centre))
        recycled=0
        for r in far:
            if len(built)-recycled<=KEEP_BUILT_ROWS: break
            recycled+=self._unbuild_row(r)
        self._built_since_trim=max(0,len(built)-recycled-KEEP_BUILT_ROWS)
        return recycled

    def _stage_row(self,row,seg):
        self._staged[seg['_edit_id']]=seg
        marker=QTableWidgetItem(fmt_time(seg.get('start',0))); marker.setData(Qt.UserRole,seg['_edit_id'])
        marker.setFlags(Qt.ItemIsEnabled|Qt.ItemIsSelectable)
        preview=QTableWidgetItem(str(seg.get('text',''))); preview.setFlags(Qt.ItemIsEnabled|Qt.ItemIsSelectable)
        self.table.setItem(row,0,marker); self.table.setItem(row,3,preview)

    def _schedule_visible_rows(self,*_):
        timer=getattr(self,"_visible_timer",None)
        if timer is not None and self._staged: timer.start()

    def _materialise_visible(self,margin=15):
        n=self.table.rowCount()
        if not n or not self._staged: return
        top=max(0,self.table.rowAt(0))
        bottom=self.table.rowAt(max(0,self.table.viewport().height()-1))
        if bottom<0: bottom=min(n-1,top+40)
        self.table.setUpdatesEnabled(False)
        try:
            for r in range(max(0,top-margin),min(n-1,bottom+margin)+1):
                if not self.table.isRowHidden(r): self._ensure_row(r)
            self._trim_built_rows(top,bottom,margin)   # v2.48: keep the number of rows with widgets small
        finally:
            self.table.setUpdatesEnabled(True)

    def showEvent(self,event):
        super().showEvent(event); self._schedule_visible_rows()

    def resizeEvent(self,event):
        super().resizeEvent(event); self._schedule_visible_rows()

    def _row_speaker(self,row):
        staged=self._staged_segment(row)
        if staged is not None: return staged.get('speaker_id') or ""
        combo=self.table.cellWidget(row,2)
        return (combo.currentData() or "") if combo else ""

    def _row_segment(self,row):
        staged=self._staged_segment(row)
        if staged is not None:
            return {"_edit_id":staged['_edit_id'],"start":float(staged.get('start',0)),"end":float(staged.get('end',0)),
                    "speaker_id":staged.get('speaker_id') or "","text":str(staged.get('text','')).strip(),
                    "speaker_confirmed":bool(staged.get('speaker_confirmed',False)),"confirmed_voice_sample":bool(staged.get('confirmed_voice_sample',False))}
        get=lambda c:self.table.cellWidget(row,c)
        return {"_edit_id":get(0).property('edit_id'),"start":parse_time(get(0).text()),"end":parse_time(get(1).text()),
                "speaker_id":get(2).currentData() or "","text":get(3).toPlainText().strip(),
                "speaker_confirmed":get(4).isChecked(),"confirmed_voice_sample":get(5).isChecked()}

    def _row_edit_id(self,row):
        staged=self._staged_segment(row)
        if staged is not None: return staged['_edit_id']
        widget=self.table.cellWidget(row,0)
        return widget.property('edit_id') if widget is not None else None

    def _read_rows(self):
        result=[]
        previous_by_id={s.get('_edit_id'):s for s in self.segments if s.get('_edit_id')}
        for r in range(self.table.rowCount()):
            try: seg=self._row_segment(r)
            except Exception as e: raise ValueError(f"Segment row {r+1}: {e}")
            if seg['start']<0 or seg['end']<seg['start']: raise ValueError(f"Segment row {r+1} has invalid start/end times.")
            old=previous_by_id.get(seg['_edit_id'], {})
            # Word timings are immutable editor metadata; reuse the existing list
            # instead of deep-copying every word on each autosave snapshot.
            seg['words']=old.get('words',[]) if old.get('text','').strip()==seg['text'] else []
            result.append(seg)
        return sorted(result,key=lambda x:(x['start'],x['end']))

    def _selected_row(self): return self.table.currentRow()
    def add_below(self):
        try: current=self._read_rows()
        except Exception as e: QMessageBox.warning(self,"Check timestamps",str(e)); return
        row=max(0,self._selected_row())
        # _read_rows() is sorted by time, which may differ from table order, so find
        # the anchor by its edit id rather than by row number.
        anchor_id=self._row_edit_id(row) if row<self.table.rowCount() else None
        anchor=next((s for s in current if s['_edit_id']==anchor_id),None) or (current[-1] if current else {"start":0,"end":0,"speaker_id":""})
        start=float(anchor.get('end',anchor.get('start',0)))
        # If there is a gap before the next segment, the new segment fills exactly that gap.
        position=next((i for i,s in enumerate(current) if s['_edit_id']==anchor.get('_edit_id')),-1)
        following=current[position+1] if 0<=position<len(current)-1 else None
        end=start
        if following is not None and float(following.get('start',start))>start: end=float(following['start'])
        seg={"_edit_id":str(uuid.uuid4()),"start":start,"end":end,"speaker_id":anchor.get('speaker_id',''),"text":"","words":[],"speaker_confirmed":False,"confirmed_voice_sample":False}
        insert_at=min(row+1,self.table.rowCount())
        # Insert only one row. Rebuilding every widget in a multi-hour transcript
        # made this action unnecessarily expensive and could freeze the GUI.
        self.table.insertRow(insert_at)
        self._make_row_widgets(insert_at,seg)
        self._reindex_text_editors(insert_at)
        self.segments=current+[seg]
        self._rebuild_playback_index()
        self.table.selectRow(insert_at)
        self._cw(insert_at,3).setFocus()
        self.history.addItem("Added empty segment below selected segment"+(f" filling the gap {fmt_time(start)} to {fmt_time(end)}" if end>start else ""))
        self._schedule_draft_save()

    def split_at_cursor(self):
        row=self._selected_row()
        if row<0: return
        try: selected=self._row_segment(row)
        except Exception as e: QMessageBox.warning(self,"Check timestamps",str(e)); return
        editor=self._cw(row,3); cursor=editor.textCursor(); pos=cursor.position(); text=selected['text']
        if pos<=0 or pos>=len(text):
            self._notify("Place the text cursor between two words in the transcript, then choose Split at text cursor."); return
        left=text[:pos].rstrip(); right=text[pos:].lstrip()
        if not left or not right: return
        # Use the selected row only. Rebuilding thousands of transcript rows here
        # was the main cause of the apparent GUI freeze on long sessions.
        old=next((x for x in self.segments if x.get('_edit_id')==selected['_edit_id']),{})
        ratio=len(left)/(len(left)+len(right))
        boundary=selected['start']+(selected['end']-selected['start'])*ratio
        playhead=self.player.position()/1000.0 if hasattr(self, "player") else -1
        use_playhead = float(selected["start"]) < playhead < float(selected["end"])
        words=old.get('words',[])
        if words and not use_playhead:
            target_chars=len(left)
            cumulative=0; best=None; best_distance=None
            for index,word in enumerate(words):
                token=str(word.get('word',''))
                cumulative += len(token)
                distance=abs(cumulative-target_chars)
                if best_distance is None or distance<best_distance:
                    best=(index,word); best_distance=distance
            if best:
                index,word=best
                left_end=float(word.get('end',boundary))
                right_start=float(words[index+1].get('start',left_end)) if index+1<len(words) else left_end
                boundary=(left_end+right_start)/2.0
        if use_playhead:
            boundary=playhead
        boundary=max(selected['start'],min(boundary,selected['end']))
        left_id=str(uuid.uuid4()); right_id=str(uuid.uuid4())
        left_seg={**selected,'text':left,'end':boundary,'_edit_id':left_id,'words':[]}
        right_seg={**selected,'text':right,'start':boundary,'_edit_id':right_id,'words':[],'speaker_confirmed':False,'confirmed_voice_sample':False}
        # Update the existing row in place.
        self._cw(row,0).setText(fmt_time(left_seg['start']))
        self._cw(row,1).setText(fmt_time(left_seg['end']))
        self._cw(row,3).setPlainText(left)
        for col in range(self.table.columnCount()):
            widget=self._cw(row,col)
            if widget is not None: widget.setProperty('edit_id',left_id)
        # Insert only the new row; do not rebuild the complete table.
        self.table.insertRow(row+1)
        self._make_row_widgets(row+1,right_seg)
        self._reindex_text_editors(row+1)
        self.segments=[x for x in self.segments if x.get('_edit_id')!=selected['_edit_id']]+[left_seg,right_seg]
        self._rebuild_playback_index()
        self.table.selectRow(row+1); self._cw(row+1,3).setFocus()
        self.history.addItem("Split segment · boundary " + fmt_time(boundary))
        self._schedule_draft_save()

    # ---- v2.41: split / speaker markup ----
    def _speaker_lookup(self):
        """{name casefolded: speaker id} for {name} markup: player, nicknames, character (and its first word),
        the speaker label and id, and "DM" when there is one DM. A name shared by several speaker ids goes to the
        one with the most lines."""
        pc=self.name_db.player_characters(self.campaign_id) if (self.name_db is not None and self.campaign_id and hasattr(self.name_db,"player_characters")) else {}
        counts={}
        for x in self.segments: counts[x.get('speaker_id') or ""]=counts.get(x.get('speaker_id') or "",0)+1
        cands={}
        dms=[]
        for sid,m in self.speaker_map.items():
            if str(sid).startswith("_") or not isinstance(m,dict): continue
            player=(m.get("player") or "").strip()
            names=[sid,sid.replace("SPEAKER_","Speaker "),re.sub(r"^SPEAKER_0*(\d)",r"Speaker \1",sid),player]
            names+=[x.strip() for x in str(m.get("player_nicknames") or "").split(",")]
            for c in {(m.get("character") or "").strip(),pc.get(player,"") if player else ""}:
                if c: names+=[c,c.split()[0]]
            if (m.get("role") or "")=="DM": dms.append(sid)
            for n in names:
                n=str(n or "").strip()
                if n: cands.setdefault(n.casefold(),set()).add(sid)
        if len(dms)==1: cands.setdefault("dm",set()).add(dms[0])
        return {k:max(v,key=lambda sid:(counts.get(sid,0),sid)) for k,v in cands.items()}

    def _markup_plan(self,items):
        """items: [(edit_id, text)] -> ([(edit_id, parts, sids)], [unknown names]) for the lines that have markup."""
        from .markup_split import parse_markup, resolve
        lookup=self._speaker_lookup(); plan=[]; unknown=[]
        for edit_id,text in items:
            parts=parse_markup(text)
            if not parts: continue
            sids,bad=resolve(parts,lookup); unknown+=bad
            plan.append((edit_id,parts,sids))
        return plan,sorted(set(unknown),key=str.casefold)

    def _apply_markup_rows(self,plan):
        """Replace each planned line by its parts (the first keeps the line's id). Returns the number of lines added."""
        from .markup_split import split_by_parts
        by_row=sorted(((self._row_for_edit_id(e),e,parts,sids) for e,parts,sids in plan),reverse=True)
        old_by_id={x.get('_edit_id'):x for x in self.segments}
        added=0; first_row=self.table.rowCount()
        for row,edit_id,parts,sids in by_row:
            if row<0: continue
            self._ensure_row(row)
            seg=self._row_segment(row)
            seg['words']=list((old_by_id.get(edit_id) or {}).get('words') or [])
            # v2.42: an edited line has lost its word timings, so use Whisper's original words for the same stretch
            words=seg['words'] or [w for raw in self.raw_segments for w in (raw.get('words') or [])
                                   if seg['start']-0.3<=float(w.get('start',-1))<=seg['end']+0.3]
            pieces=split_by_parts(seg,[t for _n,t in parts],words=words)
            new=[]
            for i,(piece,sid) in enumerate(zip(pieces,sids)):
                piece={**piece,'_edit_id':edit_id if i==0 else str(uuid.uuid4())}
                if sid is not None:
                    piece['speaker_id']=sid; piece['speaker_confirmed']=True   # you said who it was
                elif i>0:
                    piece['speaker_confirmed']=False
                if i>0: piece['confirmed_voice_sample']=False
                new.append(piece)
            self._unregister_speaker_combo(self._cw(row,2)); self.table.removeRow(row)
            for i,piece in enumerate(new):
                self.table.insertRow(row+i); self._make_row_widgets(row+i,piece)
            self.segments=[x for x in self.segments if x.get('_edit_id')!=edit_id]+new
            added+=len(new)-1; first_row=min(first_row,row)
        self._reindex_text_editors(first_row)
        self._rebuild_playback_index()
        return added

    def keyPressEvent(self,event):
        """v2.46: Enter in a box (Find, a time) never closes the editor. Qt passes an Enter the box does not use on to the
        window, which pressed its default button: searching for "gonna slash" and pressing Enter closed the editor."""
        if event.key() in (Qt.Key_Return,Qt.Key_Enter):
            event.accept(); return
        super().keyPressEvent(event)

    def eventFilter(self,obj,event):
        """v2.44: Enter (without Shift) in a line that has | or {name} offers to split that line. Elsewhere Enter
        works as before (a new line in the text)."""
        try:
            from PySide6.QtCore import QEvent
            # v2.47: > and < in a line change the speed (as on YouTube) instead of typing; speech never needs them
            if event.type()==QEvent.KeyPress and isinstance(obj,QPlainTextEdit) and event.text() in (">","<"):
                self._nudge_speed(1 if event.text()==">" else -1); return True
            if (event.type()==QEvent.KeyPress and isinstance(obj,QPlainTextEdit) and event.key() in (Qt.Key_Return,Qt.Key_Enter)
                    and not (event.modifiers() & Qt.ShiftModifier) and ('|' in obj.toPlainText() or re.search(r'\{[^{}]+\}',obj.toPlainText()))):
                edit_id=obj.property("edit_id")
                if edit_id is not None:
                    self.split_markup(only=[edit_id]); return True
        except Exception:
            if self.logger: self.logger.exception("Enter to split failed")
        return super().eventFilter(obj,event)

    def split_markup(self,only=None):
        """v2.41: apply | and {name} typed into the lines (all lines that have them), after showing what will change.
        v2.44: only = [edit_id]: just that line (Enter in a line with |)."""
        if not isinstance(only,(list,tuple,set)): only=None   # a button's clicked(bool)
        rows=range(self.table.rowCount()) if only is None else [r for r in (self._row_for_edit_id(e) for e in only) if r>=0]
        items=[(self._row_edit_id(r),self._row_text(r)) for r in rows]
        plan,unknown=self._markup_plan(items)
        if not plan:
            QMessageBox.information(self,"Split at |","No line has | or {name} in it.\n\nType | where a new speaker starts and {name} for who said it, "
                                    "for example:\n{Sam} Okay, oh my God! | {Alex} Yay! | {Kit} Let's go."); return
        if unknown:
            QMessageBox.warning(self,"Split at |","These names are not a player, nickname, character or speaker in this transcript:\n\n"
                                +", ".join(unknown)+"\n\nCorrect them (or add the nickname in the Speaker-to-player mapping), then try again. Nothing was changed."); return
        parts=sum(len(p) for _e,p,_s in plan); named=sum(1 for _e,_p,ss in plan for x in ss if x)
        if QMessageBox.question(self,"Split at |",f"{len(plan)} line(s) will become {parts} line(s). {named} of them get the speaker you named "
                                "and are marked Reviewed. Split times come from Whisper's word timings where the line still has them, "
                                "otherwise from the length of each part: listen to the new lines.\n\nGo ahead? (Ctrl+Z undoes it.)",
                                QMessageBox.Yes|QMessageBox.No)!=QMessageBox.Yes: return
        self._undo_capture()
        added=self._apply_markup_rows(plan)
        self.apply_speaker_filter()
        self.history.addItem(f"Split at | · {len(plan)} line(s) into {len(plan)+added}")
        self._schedule_draft_save(); self._undo_capture()

    # ---- v2.30: name-correction pass ----
    def _row_for_edit_id(self,edit_id):
        for r in range(self.table.rowCount()):
            if self._row_edit_id(r)==edit_id: return r
        return -1

    def _play_edit_id(self,edit_id):
        """Play exactly that segment (also when the Show filter hides its row), from the Correct names window."""
        row=self._row_for_edit_id(edit_id)
        if row<0: return
        try: seg=self._row_segment(row)
        except Exception as e: QMessageBox.warning(self,"Invalid timestamp",str(e)); return
        self._filtered_playback_queue=[]; self._end_capture_row=None
        if not self.table.isRowHidden(row): self._go_to_row(row)
        # Start a quarter of a second early so the first word is not clipped.
        self.segment_stop_ms=int(seg['end']*1000)+250
        self.player.setPosition(max(0,int(seg['start']*1000)-250)); self.player.play()

    def _pause_or_resume_clip(self):
        """v2.31.2, for the Pause / resume buttons in the review windows: pause a playing clip, or carry on with
        a paused one. Returns False when no clip is playing or paused part-way (the window then plays its row)."""
        state=self.player.playbackState()
        if state==QMediaPlayer.PlaybackState.PlayingState: self.player.pause(); return True
        if state==QMediaPlayer.PlaybackState.PausedState and self.segment_stop_ms is not None: self.player.play(); return True
        return False

    def correct_names(self,only_ids=None):
        # only_ids (v2.31.2): review just these segments (the lines a range merge added).
        if not isinstance(only_ids,(set,list,tuple)): only_ids=None   # a button's clicked(bool)
        # Not timed as a whole: the review window stays open while the user ticks boxes, so a
        # 2-minute review was logged as a 163 s "slow step" (v2.30r1). Only the work is timed.
        if self.name_db is None or not self.campaign_id:
            QMessageBox.information(self,"Correct names","Open this transcript from the main window to correct names (the campaign's names list is needed)."); return
        open_nr=getattr(self,"_name_review",None)
        if open_nr is not None and open_nr.isVisible() and only_ids is None:
            open_nr.showNormal(); open_nr.raise_(); open_nr.activateWindow(); return
        from .name_review_dialog import NameReviewDialog
        rows,accepted,rejected,texts=self.name_db.name_review_data(self.campaign_id)
        if not rows and not accepted:
            QMessageBox.information(self,"Correct names","This campaign has no names yet. Add them in Campaign names and terminology first."); return
        run_skips=self.name_db.name_review_run_skips(self.run['id']) if hasattr(self.name_db,'name_review_run_skips') else []
        segs,lower,index=self._prepare_name_review(rows,accepted,list(rejected)+run_skips,texts)
        if only_ids is not None:
            wanted=set(only_ids); segs=[x for x in segs if x.get('_edit_id') in wanted]
        record=lambda heard,intended,accept: self.name_db.record_name_review_decision(self.campaign_id,heard,intended,accept)
        skip=(lambda pairs: self.name_db.add_name_review_run_skips(self.run['id'],pairs)) if run_skips is not None and hasattr(self.name_db,'add_name_review_run_skips') else None
        import time as _time
        scan_start=_time.perf_counter()
        def reload():   # v2.46: Search again reads the lines as they are now
            out=[]
            for r in range(self.table.rowCount()):
                try: s=self._row_segment(r)
                except Exception: continue
                s['text']=self._row_text(r); out.append(s)
            return [x for x in out if x.get('_edit_id') in set(only_ids)] if only_ids is not None else out
        add_name=(lambda name,cat: self.name_db.add_campaign_name(self.campaign_id,name,cat)) if hasattr(self.name_db,'add_campaign_name') else None
        keep=(lambda words: self.name_db.add_name_review_kept(self.campaign_id,words)) if hasattr(self.name_db,'add_name_review_kept') else None
        dlg=NameReviewDialog(segs,index,lower,self._speaker_label,self._play_edit_id,record,self,skip=skip,pause=self._pause_or_resume_clip,
                             reload=reload,add_name=add_name,keep=keep)
        if self.logger: self.logger.info("Correct names | run=%s | %s suggestion(s) | scan %d ms",self.run["id"],len(dlg.suggestions),(_time.perf_counter()-scan_start)*1000)
        if only_ids is not None:
            dlg.setWindowTitle(dlg.windowTitle()+" (re-transcribed lines)")
            if not dlg.suggestions:
                self._notify("No possible name misspellings in the new lines."); return
        # v2.46: not modal, so you can play and edit other lines in the editor while it is open
        def applied():
            if dlg.chosen: QMessageBox.information(self,"Correct names",self._apply_name_corrections(dlg.chosen))
        dlg.accepted.connect(applied)
        self._name_review=dlg
        dlg.finished.connect(lambda *_: setattr(self,"_name_review",None))
        dlg.setModal(False); dlg.show()

    @_log_if_slow()
    def _prepare_name_review(self,rows,accepted,rejected,texts):
        segs=[]
        for r in range(self.table.rowCount()):
            try: s=self._row_segment(r)
            except Exception: continue
            s['text']=self._row_text(r)  # exact text, so positions match when applying
            segs.append(s)
        lower=lowercase_vocabulary(texts+[s['text'] for s in segs])
        # Players' names (saved mappings in the campaign plus this editor's current, maybe unsaved, mapping).
        people=set(self.name_db.campaign_people(self.campaign_id)) if hasattr(self.name_db,'campaign_people') else set()
        for sid,m in self.speaker_map.items():
            if str(sid).startswith('_') or not isinstance(m,dict): continue
            for v in [m.get('player')]+str(m.get('player_nicknames') or '').split(','):
                v=str(v or '').strip()
                if v and v.casefold() not in {'dm','unknown','unmapped'} and '/' not in v: people.add(v)
        db=self.name_db
        books=db.book_names(self.campaign_id) if hasattr(db,'book_names') else []
        known=set()
        for fn in ('known_name_words','name_review_kept'):
            if hasattr(db,fn):
                try: known|=set(getattr(db,fn)(self.campaign_id))
                except Exception: pass
        return segs,lower,NameIndex(rows,accepted,rejected,sorted(people,key=str.casefold),book_names=books,known_words=known)

    @_log_if_slow()
    def _apply_name_corrections(self,chosen):
        by_id={}
        for c in chosen: by_id.setdefault(c['edit_id'],[]).append(c)
        self._undo_capture()
        applied=0; changed=0; skipped=0
        for edit_id,items in by_id.items():
            row=self._row_for_edit_id(edit_id)
            if row<0 or self._row_reviewed(row):
                skipped+=len(items); continue  # reviewed segments are always protected
            text=self._row_text(row)
            new_text,n=apply_to_text(text,[(c['offset'],c['length'],c['heard'],c['replacement']) for c in items])
            skipped+=len(items)-n
            if n:
                self._set_row_text(row,new_text); applied+=n; changed+=1
        if applied:
            pairs={}
            for c in chosen:
                key=('line edited','by you') if c.get('kind')=='line' else (c['heard'],c['replacement'])
                pairs[key]=pairs.get(key,0)+1
            summary=", ".join((f"{n} line(s) edited" if (h,r)==("line edited","by you") else f"{h} → {r} ×{n}") for (h,r),n in sorted(pairs.items(),key=lambda x:-x[1])[:8])
            self.history.addItem(f"Name corrected · {applied} item(s) in {changed} segment(s) · {summary}")
            if self.logger: self.logger.info("Name corrections applied | run=%s | words=%s | segments=%s",self.run["id"],applied,changed)
            self._schedule_draft_save(); self._undo_capture()
        lines=sum(1 for c in chosen if c.get('kind')=='line')
        msg=f"Changed {applied} item(s) in {changed} segment(s)"+(f", including {lines} edited line(s)." if lines else ".")
        if skipped: msg+=f" {skipped} not changed because the text had changed or the segment is Reviewed."
        return msg+" Ctrl+Z undoes this."

    # ---- re-transcribe a time range (v2.31) ----
    def _processed_wav(self):
        from .config import load_config
        return Path(load_config().get("recordings_dir","recordings"))/"processed_audio"/f"{self.run['recording_id']}.wav"

    def suggest_ranges(self):
        """v2.31.1: list likely problem spots; picking one opens Re-transcribe range on it, and the list
        comes back (recomputed from the current transcript) when that is finished or cancelled."""
        from .range_dialogs import SuggestedRangesDialog
        from .range_suggest import suggest_ranges
        self._suggest_return=False
        try: current=self._read_rows()
        except Exception as e: QMessageBox.warning(self,"Check timestamps",str(e)); return
        wav=self._processed_wav(); checked=wav.is_file()
        duration=self.player.duration()/1000.0 if self.player.duration()>0 else None
        def compute(loose):
            try: return suggest_ranges(current,str(wav) if checked else None,duration,loose)
            except Exception as exc:
                if self.logger: self.logger.exception("Suggested ranges failed")
                QMessageBox.warning(self,"Suggested ranges",f"Could not check the transcript: {exc}"); return []
        tried=getattr(self,"_ranges_tried",set())
        dlg=SuggestedRangesDialog(compute,fmt_time,self._play_range,self._pause_or_resume_clip,tried,checked,getattr(self,"_suggest_loose",False),self)
        result=dlg.exec(); self._suggest_loose=dlg.loose.isChecked()
        if result!=QDialog.Accepted or not dlg.chosen: return
        self._suggest_return=True
        self.retranscribe_range(preset=dlg.chosen)

    def _back_to_suggestions(self):
        if getattr(self,"_suggest_return",False):
            self._suggest_return=False
            QTimer.singleShot(0,self.suggest_ranges)

    def retranscribe_range(self,preset=None):
        # preset: one (start, end) or, from Suggested ranges, a list of them (v2.31.3).
        # A button's clicked(bool) is not a range.
        if isinstance(preset,tuple): preset=[preset]
        elif not (isinstance(preset,list) and preset): preset=None
        if not self.audio_path or not Path(self.audio_path).is_file():
            QMessageBox.information(self,"Re-transcribe range","The recording file is not available, so Whisper cannot run on it."); return
        if getattr(self,"_range_thread",None) is not None:
            QMessageBox.information(self,"Re-transcribe range","A range is already being re-transcribed."); return
        owner=self.parent()
        if owner is not None and hasattr(owner,"_audio_worker_running") and owner._audio_worker_running():
            QMessageBox.information(self,"Re-transcribe range","A transcription or speaker identification is running in the main window. "
                                    "Wait for it to finish so they do not compete for the graphics card."); return
        from .config import load_config
        from .range_dialogs import RangeSetupDialog
        from .range_merge import selection_range
        cfg=load_config()
        rows=sorted({i.row() for i in self.table.selectionModel().selectedRows()}) or ([self.table.currentRow()] if self.table.currentRow()>=0 else [])
        segs=[]
        for r in rows:
            try: segs.append(self._row_segment(r))
            except Exception: pass
        start,end=(preset[0][0],preset[-1][1]) if preset else selection_range(segs)
        if end<=start: start,end=self.player.position()/1000.0,self.player.position()/1000.0+30.0
        if preset and len(preset)==1: start,end=preset[0]
        settings={"model":cfg.get("whisper_model","large-v3"),"device":cfg.get("whisper_device","cpu"),"compute":cfg.get("whisper_compute_type","int8"),
                  "vad_threshold":cfg.get("whisper_vad_threshold",0.4),"style_hint":cfg.get("whisper_style_hint",True)}
        setup=RangeSetupDialog(start,end,settings,fmt_time,parse_time,self,multi=preset if preset and len(preset)>1 else None)
        if setup.exec()!=QDialog.Accepted or not setup.values(): self._back_to_suggestions(); return
        v=setup.values(); self._range_request=v
        if preset: self._ranges_tried=getattr(self,"_ranges_tried",set())|set(preset)
        clips=[(max(0.0,a-v["padding"]),b+v["padding"]) for a,b in v["ranges"]]
        from PySide6.QtWidgets import QProgressDialog
        from .range_transcribe import RangeTranscriptionWorker
        worker=RangeTranscriptionWorker(self.run['recording_id'],self.audio_path,cfg.get("recordings_dir","recordings"),
                                        max(0.0,v["start"]-v["padding"]),v["end"]+v["padding"],settings["model"],settings["device"],
                                        settings["compute"],v["vad_threshold"],v["beam_size"],v["style_hint"],clips=clips)
        thread=QThread(self); worker.moveToThread(thread)
        progress=QProgressDialog("Preparing…","Stop",0,0,self); progress.setWindowTitle("Re-transcribe range"); progress.setMinimumDuration(0)
        progress.canceled.connect(worker.stop)
        worker.progress.connect(progress.setLabelText)
        # Bound methods of this window (not lambdas) so the results are handled on the GUI thread.
        worker.finished.connect(self._range_finished)
        worker.failed.connect(self._range_failed)
        self._range_progress=progress
        thread.started.connect(worker.run)
        thread.finished.connect(self._range_thread_done)
        self._range_thread,self._range_worker=thread,worker
        if self.logger: self.logger.info("Re-transcribing range | run=%s | %s | vad=%s beam=%s style=%s",self.run["id"],
                                         ", ".join(f"{fmt_time(a)}-{fmt_time(b)}" for a,b in v["ranges"]),v["vad_threshold"],v["beam_size"],v["style_hint"])
        thread.start(); progress.show()

    def alternatives(self,preset=None):
        """v2.45: the selected stretch re-transcribed three ways (range_transcribe.ALTERNATIVES), shown side by side."""
        if not self.audio_path or not Path(self.audio_path).is_file():
            QMessageBox.information(self,"Alternatives","The recording file is not available, so Whisper cannot run on it."); return
        if getattr(self,"_range_thread",None) is not None:
            QMessageBox.information(self,"Alternatives","A range is already being re-transcribed."); return
        owner=self.parent()
        if owner is not None and hasattr(owner,"_audio_worker_running") and owner._audio_worker_running():
            QMessageBox.information(self,"Alternatives","A transcription or speaker identification is running in the main window. "
                                    "Wait for it to finish so they do not compete for the graphics card."); return
        from .config import load_config
        from .range_merge import selection_range
        from .range_transcribe import RangeTranscriptionWorker,ALTERNATIVES
        cfg=load_config()
        if isinstance(preset,tuple): start,end=preset
        else:
            rows=sorted({i.row() for i in self.table.selectionModel().selectedRows()}) or ([self.table.currentRow()] if self.table.currentRow()>=0 else [])
            segs=[]
            for r in rows:
                try: segs.append(self._row_segment(r))
                except Exception: pass
            start,end=selection_range(segs)
        if end-start<0.5:
            self._notify("Select the line or lines to try again first."); return
        if end-start>120:
            QMessageBox.information(self,"Alternatives",f"That is {end-start:.0f} seconds. Choose 2 minutes or less: it is transcribed four "
                                    "ways. (Re-transcribe range… takes longer stretches.)"); return
        names=""
        if self.name_db is not None and self.campaign_id and hasattr(self.name_db,"campaign_prompt_context"):
            try: names=self.name_db.campaign_prompt_context(self.campaign_id)
            except Exception: names=""
        pad=2.0
        v={"start":start,"end":end,"ranges":[(start,end)],"padding":pad,"vad_threshold":cfg.get("whisper_vad_threshold",0.4),
           "beam_size":5,"style_hint":cfg.get("whisper_style_hint",True),"alternatives":True}
        self._range_request=v
        from PySide6.QtWidgets import QProgressDialog
        worker=RangeTranscriptionWorker(self.run['recording_id'],self.audio_path,cfg.get("recordings_dir","recordings"),
                                        max(0.0,start-pad),end+pad,cfg.get("whisper_model","large-v3"),cfg.get("whisper_device","cpu"),
                                        cfg.get("whisper_compute_type","int8"),v["vad_threshold"],5,v["style_hint"],
                                        clips=[(max(0.0,start-pad),end+pad)],variants=ALTERNATIVES,names_prompt=names)
        thread=QThread(self); worker.moveToThread(thread)
        progress=QProgressDialog("Preparing…","Stop",0,0,self); progress.setWindowTitle("Alternatives"); progress.setMinimumDuration(0)
        progress.canceled.connect(worker.stop)
        worker.progress.connect(progress.setLabelText)
        worker.finished.connect(self._alternatives_finished)
        worker.failed.connect(self._range_failed)
        self._range_progress=progress
        thread.started.connect(worker.run)
        thread.finished.connect(self._range_thread_done)
        self._range_thread,self._range_worker=thread,worker
        if self.logger: self.logger.info("Alternatives | run=%s | %s-%s",self.run["id"],fmt_time(start),fmt_time(end))
        thread.start(); progress.show()

    def _alternatives_finished(self,payload):
        self._range_stop_ui()
        from .alternatives_dialog import AlternativesDialog
        data=json.loads(payload); v=self._range_request; s=data.get("settings",{})
        try: current=[x for x in self._read_rows() if v["start"]-0.5<=float(x["start"])<=v["end"]+0.5]
        except Exception as e: QMessageBox.warning(self,"Check timestamps",str(e)); return
        versions=data.get("versions",[])
        if self.logger: self.logger.info("Alternatives done | %s s | %s",s.get("seconds"),
                                         [(x["label"],len(x["segments"]),x.get("same_as","")) for x in versions])
        old=getattr(self,"_alternatives",None)
        if old is not None:
            try: old.close()
            except Exception: pass
        dlg=AlternativesDialog(current,versions,v["start"],v["end"],fmt_time,self._play_range,self._pause_or_resume_clip,
                               f"Whisper {s.get('model_name')} · {s.get('seconds')} s for all {len(versions)}.",self,padding=v.get("padding",2.0))
        # v2.46: not modal, so the editor (its playback speed, lines and buttons) can be used while choosing
        dlg.accepted.connect(lambda d=dlg,vv=v,ss=s: self._alternative_chosen(d,vv,ss))
        self._alternatives=dlg
        dlg.finished.connect(lambda *_: setattr(self,"_alternatives",None))
        dlg.setModal(False); dlg.show()

    def _alternative_chosen(self,dlg,v,s):
        pick=dlg.chosen()
        if pick is None: return
        self._range_request=v   # the stretch this window was for (another may have run since)
        ps=pick.get("settings",{})
        merged={"segments":pick["segments"],"clips":[{"clip_start":s.get("clip_start",max(0.0,v["start"]-v["padding"])),"segments":pick["segments"]}],
                "settings":{"model_name":f"{s.get('model_name')} ({pick['label']})","vad_threshold":ps.get("vad_threshold"),
                            "beam_size":ps.get("beam_size"),"style_hint_used":ps.get("style_hint_used"),"seconds":s.get("seconds")}}
        self._range_result(json.dumps(merged,ensure_ascii=False))

    def _range_thread_done(self):
        self._range_thread=None; self._range_worker=None

    def _range_stop_ui(self):
        p=getattr(self,"_range_progress",None)
        if p is not None: p.close(); self._range_progress=None
        if getattr(self,"_range_thread",None) is not None: self._range_thread.quit()

    def _range_finished(self,payload):
        self._range_stop_ui()
        try: self._range_result(payload)
        finally: self._back_to_suggestions()

    def _range_failed(self,message):
        self._range_stop_ui()
        QMessageBox.warning(self,"Re-transcribe range",f"Whisper could not re-transcribe the range: {message}")
        self._back_to_suggestions()

    def _play_range(self,start,end):
        self.segment_stop_ms=int(end*1000)+250
        self.player.setPosition(max(0,int(start*1000)-250)); self.player.play()

    def _range_result(self,payload):
        from .range_dialogs import RangeMergeDialog
        from .range_merge import apply_choices
        data=json.loads(payload); v=self._range_request; s=data.get("settings",{})
        try: current=self._read_rows()
        except Exception as e: QMessageBox.warning(self,"Check timestamps",str(e)); return
        text=(f"Whisper {s.get('model_name')} · speech threshold {s.get('vad_threshold')} · beam {s.get('beam_size')} · "
              f"punctuation hint {'on' if s.get('style_hint_used') else 'off'} · {s.get('seconds')} s")
        turns=[]
        if self.name_db is not None and hasattr(self.name_db,"diarization_turns"):
            try: turns=self.name_db.diarization_turns(self.run['id'])
            except Exception: turns=[]
        # Each range gets the text of its own clip, so padding shared by neighbouring ranges is not offered twice.
        clips=data.get("clips") or [{"clip_start":max(0.0,v["start"]-v.get("padding",0)),"segments":data.get("segments",[])}]
        ranges=[]
        for a,b in v.get("ranges",[(v["start"],v["end"])]):
            clip=next((c for c in clips if abs(float(c["clip_start"])-max(0.0,a-v.get("padding",0)))<0.01),None)
            if clip is not None: ranges.append((a,b,clip["segments"]))
        playing=lambda: self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState
        dlg=RangeMergeDialog(current,data.get("segments",[]),v["start"],v["end"],self._speaker_label,fmt_time,self._play_range,text,self,
                             speaker_ids=self._speaker_ids(),turns=turns,pause=self._pause_or_resume_clip,ranges=ranges,is_playing=playing)
        if not dlg.slots:
            self._notify("Neither the transcript nor the new run has any speech in that range."); return
        if dlg.exec()!=QDialog.Accepted: return
        new_ids,picked_ids=self._apply_range_merge(current,dlg.slots,dlg.choices(),turns,text,v,dlg.speakers())
        if new_ids and dlg.check_names() and self.name_db is not None and self.campaign_id:
            self.correct_names(only_ids=new_ids)
        # v2.32.1: a speaker the user picked counts as reviewed. Done after the names check, because
        # Correct names never changes Reviewed segments.
        if picked_ids and dlg.review_picked():
            marked=0
            for edit_id in picked_ids:
                row=self._row_for_edit_id(edit_id)
                box=self._cw(row,4) if row>=0 else None
                if box is not None and not box.isChecked(): box.setChecked(True); marked+=1
            if marked:
                self.history.addItem(f"Marked {marked} re-transcribed line(s) Reviewed (speaker picked in the compare window)")
                self._schedule_draft_save(); self._undo_capture()
            if new_ids:
                row=self._row_for_edit_id(new_ids[0])
                if row>=0: self._go_to_row(row)

    @_log_if_slow()
    def _apply_range_merge(self,current,slots,choices,turns,settings_text,v,speakers=None):
        """Returns the edit ids of the added segments."""
        from .range_merge import apply_choices
        self._undo_capture()
        merged,summary=apply_choices(current,slots,choices,turns,speakers=speakers)
        if not summary["added"] and not summary["removed"]: return [],[]
        row=self.table.currentRow()
        self.segments=merged; self._speaker_ids_cache=None
        self._populate_speakers()
        if not self._replace_rows_in_place(current,merged): self._render_segments()
        self._highlighted_playback_row=-1
        # v2.31.3: rebuilding the list put it back at the top, so the new lines were hard to find.
        # Go to the first new line (or, if none was added, back to where the user was).
        target=self._row_for_edit_id(summary["new_ids"][0]) if summary["new_ids"] else -1
        if target>=0: self._hold_scroll_until=0; self._go_to_row(target)
        elif self.table.rowCount(): self._go_to_row(min(max(row,0),self.table.rowCount()-1))
        self._undo_capture()
        where=(f"{fmt_time(v['start'])[:8]}–{fmt_time(v['end'])[:8]}" if len(v.get("ranges",[]))<=1
               else f"{len(v['ranges'])} ranges from {fmt_time(v['start'])[:8]} to {fmt_time(v['end'])[:8]}")
        self.history.addItem(f"Re-transcribed {where} · {summary['rows_used']} row(s): "
                             f"{summary['removed']} segment(s) replaced by {summary['added']} ({summary['words_before']} → {summary['words_after']} words) · "
                             f"speakers from {summary['speakers_from']} · {settings_text}")
        if self.logger: self.logger.info("Range merged | run=%s | %s",self.run["id"],{k:x for k,x in summary.items() if k not in ("new_ids","picked_ids")})
        self._schedule_draft_save()
        return summary["new_ids"],summary["picked_ids"]

    def _replace_rows_in_place(self,current,merged):
        """v2.41: after a merge, remove and insert only the rows that changed instead of rebuilding the whole table
        (about 200 ms on a 4,600-line session). Returns False, changing nothing, when the result would not be exactly
        what a full rebuild shows (a kept line changed, or the order differs); the caller then rebuilds as before."""
        key=lambda s:(float(s.get('start',0)),float(s.get('end',0)))
        desired=sorted(merged,key=key)
        merged_ids={s.get('_edit_id') for s in merged}
        table_ids=[self._row_edit_id(r) for r in range(self.table.rowCount())]
        if None in table_ids or len(set(table_ids))!=len(table_ids): return False
        old_by_id={s.get('_edit_id'):s for s in current}
        fields=('start','end','speaker_id','text','speaker_confirmed')
        for s in merged:
            old=old_by_id.get(s.get('_edit_id'))
            if old is not None and any(old.get(f)!=s.get(f) for f in fields): return False
        kept_table=[i for i in table_ids if i in merged_ids]
        kept_desired=[s.get('_edit_id') for s in desired if s.get('_edit_id') in old_by_id]
        if kept_table!=kept_desired or len(kept_table)!=len([s for s in merged if s.get('_edit_id') in old_by_id]): return False
        self.table.setUpdatesEnabled(False)
        try:
            first=self.table.rowCount()
            for r in range(len(table_ids)-1,-1,-1):
                if table_ids[r] not in merged_ids:
                    self._unregister_speaker_combo(self._cw(r,2)); self._staged.pop(table_ids[r],None)
                    self.table.removeRow(r); first=min(first,r)
            for i,s in enumerate(desired):
                if s.get('_edit_id') not in old_by_id:
                    self.table.insertRow(i); self._make_row_widgets(i,s); first=min(first,i)
            for r in range(self.table.rowCount()): self.table.setVerticalHeaderItem(r,None)
            self._reindex_text_editors(first)
            self.apply_speaker_filter()
            self._rebuild_playback_index()
        finally:
            self.table.setUpdatesEnabled(True)
        self.table.viewport().update()
        return True

    # ---- roll finder (v2.37) ----
    def _who(self,sid):
        m=self.speaker_map.get(sid,{}) or {}
        player=(m.get("player") or "").strip()
        return player or ("DM" if (m.get("role") or "")=="DM" else (sid.replace("SPEAKER_","Speaker ") if sid else "no speaker"))

    def _edit_player_characters(self):
        from .roll_finder_dialog import PlayerCharactersDialog
        players=sorted({(m.get("player") or "").strip() for k,m in self.speaker_map.items()
                        if not str(k).startswith("_") and isinstance(m,dict) and (m.get("player") or "").strip()
                        and (m.get("role") or "")!="DM"},key=str.casefold)
        current=self.name_db.player_characters(self.campaign_id)
        # v2.46: only the players in this session's speaker map (two players were listed in one session because they
        # were saved for a later one); the others' characters are kept as they are
        if not players:
            QMessageBox.information(self,"Players and characters","Map the speakers to players in this editor first (Speaker-to-player mapping)."); return False
        entries=self.name_db.campaign_dictionary_entries(self.campaign_id)
        characters=sorted({e["canonical_name"] for e in entries if "dndbeyond.com" in (e["source_url"] or "")},key=str.casefold)
        characters+=sorted({e["canonical_name"] for e in entries if (e["category"] or "").startswith("Character")} - set(characters),key=str.casefold)
        dlg=PlayerCharactersDialog(players,characters,current,self)
        if dlg.exec()!=QDialog.Accepted: return False
        merged=dict(current); merged.update(dlg.mapping())
        self.name_db.save_player_characters(self.campaign_id,merged)
        return True

    def _edit_line_from_roll_finder(self,edit_id):
        """v2.46: double-click "What was said" in the roll finder: rewrite that line here (one Ctrl+Z undoes it)."""
        row=self._row_for_edit_id(edit_id)
        if row<0: return False
        old=self._row_text(row)
        text,ok=QInputDialog.getMultiLineText(self,"Edit line",f"{fmt_time(float(self._row_segment(row)['start']))[:8]}  "
                                              f"{self._speaker_label(self._row_speaker(row))}\n\nCorrect the line as it was said "
                                              "({Sam} Okay! | {Alex} Yay! splits it):",old)
        if not ok or text.strip()==old.strip(): return False
        self._undo_capture(); self._set_row_text(row,text.strip())
        if ('|' in text or re.search(r'\{[^{}]+\}',text)) and self._row_edit_id(row) is not None:
            self.split_markup(only=[self._row_edit_id(row)])
        self.history.addItem("Roll finder · line edited"); self._schedule_draft_save(); self._undo_capture()
        return True

    def roll_finder(self):
        # v2.45: the roll finder stays open beside the editor; pressing the button again brings it to the front
        open_rf=getattr(self,"_roll_finder",None)
        if open_rf is not None and open_rf.isVisible():
            open_rf.showNormal(); open_rf.raise_(); open_rf.activateWindow(); return
        """v2.37 / v2.39: rolls, pass / fail, damage; lines can be corrected from the window; results kept per version."""
        if self.name_db is None or not self.campaign_id:
            QMessageBox.information(self,"Roll finder","Open this transcript from the main window to use the roll finder."); return
        from .roll_finder import find_rolls, snapshot_for, find_damage_taken
        from .spell_book import spells_from_books
        from .roll_finder_dialog import RollFinderDialog, RollContext
        from .ddb_import import character_id_from_url
        import time as _time
        opened=_time.perf_counter()
        try: self._read_rows()
        except Exception as e: QMessageBox.warning(self,"Check timestamps",str(e)); return
        rec=self.name_db.get_recording(self.run['recording_id']) if hasattr(self.name_db,"get_recording") else None
        when=str(rec["file_modified"] or rec["imported_at"] or "") if rec else ""
        entries=self.name_db.campaign_dictionary_entries(self.campaign_id)
        def ddb_id_for(character):
            key=character.casefold()
            for e in entries:
                names=[e["canonical_name"]]+[a.strip() for a in str(e["also_known_as"] or "").split(",") if a.strip()]
                if key in {n.casefold() for n in names} or key in {n.split()[0].casefold() for n in names if n}:
                    try: return character_id_from_url(e["source_url"])
                    except Exception: return None
            return None
        def sheet_for(character):
            did=ddb_id_for(character)
            if not did: return (None,"no D&D Beyond link for this character in Campaign names")
            snaps=self.name_db.character_snapshots(self.campaign_id,did)
            # prefer versions that recorded the most (weapons from v2.38, skills from v2.35)
            snap,note=snapshot_for([x for x in snaps if "weapons" in x["summary"]] or [x for x in snaps if "skills" in x["summary"]] or snaps,when)
            if snap and "skills" not in snap["summary"]:
                note="this snapshot was saved before skills and saves were recorded: refresh the character in Campaign names"
            elif snap and "weapons" not in snap["summary"]:
                note+="; weapons and damage not recorded yet: refresh the character in Campaign names"
            return (snap["summary"] if snap else None,note)
        def role(sid):
            return ((self.speaker_map.get(sid,{}) or {}).get("role") or "").strip()
        def character_of(sid):
            m=self.speaker_map.get(sid,{}) or {}
            player=(m.get("player") or "").strip()
            return self.name_db.player_characters(self.campaign_id).get(player) or (m.get("character") or "").strip()
        def lookup(sid):
            if role(sid)=="DM": return ("",None,"DM")
            character=character_of(sid)
            if not character: return ("",None,"")
            summary,note=sheet_for(character)
            return (character,summary,note)
        def people():
            # v2.43: only the people in THIS session's speaker map (any role but DM: a guest or someone in the
            # background can roll for someone too), shown as their character when one is set. It used to list
            # everyone ever saved under Players and characters, so every saved player appeared in every session.
            pc=self.name_db.player_characters(self.campaign_id)
            names=[]
            for k,m in self.speaker_map.items():
                if not str(k).startswith("_") and isinstance(m,dict) and (m.get("player") or "").strip() and (m.get("role") or "")!="DM":
                    p=m["player"].strip()
                    names.append(pc.get(p) or (m.get("character") or "").strip() or p)
            return sorted(dict.fromkeys(names),key=str.casefold)
        def spoken_names():
            # v2.40: names said at the table -> the person a roll is credited to (character if set, else player)
            pc=self.name_db.player_characters(self.campaign_id); out={}
            for k,m in self.speaker_map.items():
                if str(k).startswith("_") or not isinstance(m,dict) or (m.get("role") or "")=="DM": continue
                p=(m.get("player") or "").strip()
                if not p: continue
                person=pc.get(p) or (m.get("character") or "").strip() or p
                for n in [p]+[x.strip() for x in str(m.get("player_nicknames") or "").split(",")]:
                    if n: out[n]=person
            for p,c in pc.items():
                if p: out.setdefault(p,c or p)
                if c:
                    out[c]=c; out.setdefault(c.split()[0],c)
            return out
        dm_ids={k for k,m in self.speaker_map.items() if not str(k).startswith("_") and isinstance(m,dict) and (m.get("role") or "")=="DM"}
        first_data=[]   # v2.41: the rolls found for the "none found?" check are reused when the window opens
        def data():
            if first_data: return first_data.pop()
            rows=self._read_rows()
            events=find_rolls(rows,people=spoken_names(),dm=dm_ids)
            pc=self.name_db.player_characters(self.campaign_id)
            names=[c for c in pc.values() if c]+list(pc)
            taken=find_damage_taken(rows,names)
            for t in taken:
                guess=pc.get(t["target"]) or t["target"]   # a player's name said -> their character
                if not guess:   # "you take…": whoever rolled last (not the DM) in the minute before
                    before=[r for e in events for r in e["results"] if r["start"]<=t["start"] and t["start"]-r["start"]<=90 and role(r["speaker"])!="DM"]
                    if before: guess=character_of(before[-1]["speaker"]) or self._who(before[-1]["speaker"])
                t["target_guess"]=guess
            return {"events":events,"segments":rows,"taken":taken}
        def apply_texts(changes,speakers=None):
            plan,unknown=self._markup_plan(list(changes.items()))
            if unknown:
                raise ValueError("These names are not a player, nickname, character or speaker in this transcript: "+", ".join(unknown)
                                 +". Correct them (or add the nickname in the Speaker-to-player mapping). Nothing was changed.")
            marked={e for e,_p,_s in plan}
            self._undo_capture(); n=0; ns=0
            for edit_id,text in changes.items():
                if edit_id in marked: continue   # split below
                row=self._row_for_edit_id(edit_id)
                if row>=0 and self._row_text(row).strip()!=text.strip():
                    self._set_row_text(row,text); n+=1
            for edit_id,sid in (speakers or {}).items():
                row=self._row_for_edit_id(edit_id)
                if row<0: continue
                self._ensure_row(row); combo=self._cw(row,2)
                if (combo.currentData() or "")==(sid or ""): continue
                if combo.findData(sid)<0: combo.addItem(self._speaker_label(sid) if sid else "Unassigned",sid)
                combo.setCurrentIndex(combo.findData(sid)); ns+=1   # marks the line Reviewed, as a change in the editor does
            if ns: n+=ns
            added=0
            if plan:
                added=self._apply_markup_rows(plan); self.apply_speaker_filter(); n+=len(plan)
            if n:
                nt=n-ns-len(plan)
                parts=([f"corrected {nt} line(s)"] if nt else [])+([f"changed {ns} speaker(s)"] if ns else [])
                parts+=([f"split {len(plan)} line(s) into {len(plan)+added}"] if plan else [])
                self.history.addItem("Roll finder · "+", ".join(parts))
                self._schedule_draft_save(); self._undo_capture()
            return n
        def go_to(edit_id):
            row=self._row_for_edit_id(edit_id)
            if row>=0: self._go_to_row(row)
        run_id=self.run["id"]
        ctx=RollContext(data=data,who=self._who,role=role,lookup=lookup,sheet_for=sheet_for,people=people,
                        speakers=lambda: [(sid,self._who(sid)) for sid in self._speaker_ids()],
                        spells=lambda: spells_from_books(self.name_db.reference_texts(self.campaign_id)) if hasattr(self.name_db,"reference_texts") else {},
                        overrides=lambda: self.name_db.roll_overrides(run_id),
                        set_override=lambda key,field,value: self.name_db.set_roll_override(run_id,key,field,value),
                        override_times=lambda: self.name_db.roll_override_times(run_id) if hasattr(self.name_db,"roll_override_times") else {},
                        edit_line=self._edit_line_from_roll_finder,
                        save_log=lambda rows: self.name_db.save_roll_log(run_id,rows),
                        campaign_logs=lambda: self.name_db.campaign_roll_logs(self.campaign_id),
                        apply_texts=apply_texts,play=self._play_range,pause=self._pause_or_resume_clip,go_to=go_to,
                        row_map=lambda: {self._row_edit_id(r):r for r in range(self.table.rowCount())},
                        version=lambda: getattr(self,"_edit_version",0),
                        edit_players=self._edit_player_characters)
        d=data()
        if not d["events"]:
            self._notify("No checks or rolls were found in this transcript."); return
        first_data.append(d)
        dlg=RollFinderDialog(ctx,fmt_time,self)
        if self.logger: self.logger.info("Roll finder | run=%s | rolls=%s | opened in %d ms",run_id,len(d["events"]),(_time.perf_counter()-opened)*1000)
        # v2.45: not modal, so the editor can be used at the same time (it was blocked while the roll finder was open)
        self._roll_finder=dlg
        dlg.finished.connect(lambda *_: setattr(self,"_roll_finder",None))
        dlg.setModal(False); dlg.show()

    def _select_edit_id(self,edit_id):
        for r in range(self.table.rowCount()):
            if self._row_edit_id(r)==edit_id:
                self.table.selectRow(r); self._cw(r,3).setFocus(); return

    def merge_previous(self):
        row=self._selected_row()
        if row<=0: return
        try:
            a=self._row_segment(row-1); b=self._row_segment(row)
            if a['start']<0 or a['end']<a['start'] or b['start']<0 or b['end']<b['start']:
                raise ValueError("One of the selected segments has an invalid timestamp.")
        except Exception as e: QMessageBox.warning(self,"Check timestamps",str(e)); return
        if a['speaker_id']!=b['speaker_id'] and QMessageBox.question(self,"Different speakers","These segments have different speakers. Merge and keep the previous segment's speaker?",QMessageBox.Yes|QMessageBox.No)!=QMessageBox.Yes: return
        merged_text=(a['text']+" "+b['text']).strip(); merged_end=max(a['end'],b['end'])
        self._cw(row-1,1).setText(fmt_time(merged_end))
        self._cw(row-1,3).setPlainText(merged_text)
        self._unregister_speaker_combo(self._cw(row, 2))
        self.table.removeRow(row)
        self._reindex_text_editors(row)
        old_ids={a['_edit_id'],b['_edit_id']}
        merged={**a,'text':merged_text,'end':merged_end,'words':[]}
        self.segments=[x for x in self.segments if x.get('_edit_id') not in old_ids]+[merged]
        self._rebuild_playback_index()
        self.table.selectRow(row-1); self._cw(row-1,3).setFocus()
        self.history.addItem("Merged segment with previous")
        self._schedule_draft_save()

    def remove_selected(self):
        row=self._selected_row()
        if row<0:return
        if QMessageBox.question(self,"Remove segment","Remove this segment from the edited transcript? The original Whisper transcript remains available.",QMessageBox.Yes|QMessageBox.No)!=QMessageBox.Yes:return
        # Identify the segment by the edit id stored on its row. _read_rows() is sorted by
        # time, so indexing it with the table row could delete a different segment.
        target_id=self._row_edit_id(row)
        try: current=self._read_rows()
        except Exception as e: QMessageBox.warning(self,"Check timestamps",str(e)); return
        self.segments=[s for s in current if s['_edit_id']!=target_id]
        self._unregister_speaker_combo(self._cw(row, 2))
        self.table.removeRow(row)
        self._reindex_text_editors(row)
        self._highlighted_playback_row=-1
        self._rebuild_playback_index()
        if self.table.rowCount(): self.table.selectRow(min(row,self.table.rowCount()-1))
        self.history.addItem("Removed segment")
        self._schedule_draft_save()

    def merge_speakers(self):
        ids=self._speaker_ids()
        if len(ids)<2:
            self._notify("At least two identified speakers are required."); return
        labels=[self._speaker_label(sid) for sid in ids]
        source_label,ok=QInputDialog.getItem(self,"Merge speakers","Speaker to merge FROM (all its segments will be reassigned):",labels,1,False)
        if not ok:return
        source=ids[labels.index(source_label)]
        target_labels=[self._speaker_label(sid) for sid in ids if sid!=source]
        target_label,ok=QInputDialog.getItem(self,"Merge speakers","Speaker to KEEP:",target_labels,0,False)
        if not ok:return
        target=ids[[self._speaker_label(sid) for sid in ids if sid!=source].index(target_label)]
        try: current=self._read_rows()
        except Exception as exc: QMessageBox.warning(self,"Cannot merge speakers",str(exc)); return
        affected=sum(1 for seg in current if seg.get("speaker_id")==source)
        if not affected:
            self._notify(f"No transcript segments are currently assigned to {source}."); return
        source_map=copy.deepcopy(self.speaker_map.get(source,{})); target_map=copy.deepcopy(self.speaker_map.get(target,{}))
        if source_map and target_map and source_map!=target_map:
            choice=QMessageBox.question(self,"Speaker mappings differ",f"{source} and {target} have different player mappings. Keep the mapping for {target}?\n\nThe source mapping will be retained in the merge backup.",QMessageBox.Yes|QMessageBox.No|QMessageBox.Cancel,QMessageBox.Yes)
            if choice==QMessageBox.Cancel:return
            if choice==QMessageBox.No: target_map=source_map
        elif source_map and not target_map: target_map=source_map
        answer=QMessageBox.question(self,"Confirm speaker merge",f"Reassign {affected} segment(s) from {source} to {target}?\n\nText, timestamps, Reviewed flags and voice-sample flags will be preserved. A backup will be kept for undo.",QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if answer!=QMessageBox.Yes:return
        self._speaker_merge_undo={"segments":copy.deepcopy(current),"speaker_map":copy.deepcopy(self.speaker_map),"source":source,"target":target}
        try:
            self.backup_dir.mkdir(parents=True,exist_ok=True)
            backup_path=self.backup_dir/f"speaker_merge_{self.run['id']}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json"
            backup_path.write_text(json.dumps({"run_id":self.run["id"],"source":source,"target":target,"segments":current,"speaker_map":self.speaker_map},ensure_ascii=False,indent=2),encoding="utf-8")
        except Exception as exc:
            QMessageBox.warning(self,"Merge backup unavailable",f"Could not write the merge backup. The in-editor undo will still be available.\n\n{exc}")
        for seg in current:
            if seg.get("speaker_id")==source: seg["speaker_id"]=target
        self.segments=current
        self.speaker_map.pop(source,None)
        if target_map:self.speaker_map[target]=target_map
        self._populate_speakers(); self._render_segments(); self._schedule_draft_save()
        self.history.addItem(f"Merged {source} into {target} · {affected} segment(s)")
        QMessageBox.information(self,"Speakers merged",f"{affected} segment(s) reassigned to {target}. Use Undo speaker merge to reverse this operation before closing the editor.")

    def undo_speaker_merge(self):
        if not self._speaker_merge_undo:
            self._notify("There is no speaker merge to undo in this editing session."); return
        snapshot=self._speaker_merge_undo
        self.segments=copy.deepcopy(snapshot["segments"]); self.speaker_map=copy.deepcopy(snapshot["speaker_map"])
        self._speaker_merge_undo=None
        self._populate_speakers(); self._render_segments(); self._schedule_draft_save()
        self.history.addItem(f"Undid merge of {snapshot['source']} into {snapshot['target']}")

    def rewind_one_second(self):
        # Position is read before anything else; a stale pending seek (older than
        # one second, e.g. the media backend never reported it) is ignored.
        try: current=int(self.player.position() or 0)
        except (TypeError,ValueError): current=0
        pending=self._requested_seek_ms if (self._requested_seek_ms is not None and time.monotonic()-self._seek_requested_at<1.0) else None
        base=int(pending) if pending is not None else current
        target=max(0,base-1000)
        self._requested_seek_ms=target; self._seek_requested_at=time.monotonic()
        self.player.setPosition(target)
        self.playback_label.setText(f"Seeking to {fmt_time(target/1000)}")

    def add_speaker(self):
        try: self.segments=self._read_rows()
        except Exception as e: QMessageBox.warning(self,"Check timestamps",str(e)); return
        existing=set(self._speaker_ids()); n=1
        while f"SPEAKER_{n:02d}" in existing:n+=1
        sid=f"SPEAKER_{n:02d}"; self.speaker_map[sid]={"role":"Unknown","player":"","character":"","include_in_notes":False}
        self._populate_speakers(); self._render_segments(); self.history.addItem(f"Added {sid}")
        self._schedule_draft_save()

    def reset_speaker_assignments(self):
        if QMessageBox.question(self, "Reset speaker assignments", "Back up the current speaker assignments and mapping, then clear all automatic speaker labels and mappings for this transcript? Transcript text and timestamps will be preserved.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            current_segments = self._read_rows()
            backup = {"run_id": self.run["id"], "created_at": datetime.datetime.now().astimezone().isoformat(), "segments": current_segments, "speaker_map": self.speaker_map}
            path = self.backup_dir / f"speaker_assignments_{self.run['id']}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            path.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
            for segment in current_segments:
                segment["speaker_id"] = ""
                segment["speaker_confirmed"] = False
                segment["confirmed_voice_sample"] = False
            self.segments = current_segments
            self.speaker_map = {}
            self._populate_speakers(); self._render_segments()
            self.history.addItem(f"Speaker assignments reset · backup: {path.name}")
            self._schedule_draft_save()
            QMessageBox.information(self, "Speaker assignments reset", f"Assignments cleared. Backup saved to:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Could not reset speakers", str(exc))

    def restore_raw_transcript(self):
        if QMessageBox.question(self,"Restore original transcription","Replace the current transcript with original Whisper output? This will be recorded in edit history when saved.",QMessageBox.Yes|QMessageBox.No)!=QMessageBox.Yes:return
        self.segments=[]
        for s in self.raw_segments:
            item=copy.deepcopy(s); item['_edit_id']=str(uuid.uuid4()); item['confirmed_voice_sample']=False; self.segments.append(item)
        self._speaker_ids_cache=None
        self._populate_speakers(); self._render_segments(); self.history.addItem("Restored raw Whisper transcript")
        self._schedule_draft_save()

    def play_selected_segment(self):
        self._filtered_playback_queue=[]
        self._end_capture_row=None; self.set_end_btn.setText("▶ Play start / set end")
        row=self._selected_row()
        if row<0:return
        try: seg=self._row_segment(row)
        except Exception as e: QMessageBox.warning(self,"Invalid timestamp",str(e)); return
        self.segment_stop_ms=int(seg['end']*1000); self.player.setPosition(int(seg['start']*1000)); self.player.play()

    def play_previous_segment(self):
        row=self._selected_row()
        if row<=0: return
        previous=row-1
        while previous>=0 and self.table.isRowHidden(previous): previous-=1
        if previous<0:return
        self.table.setCurrentCell(previous,0)
        self.play_selected_segment()

    notice_name="Editor"

    def _notify(self,text):
        """v2.47: a short note in the yellow bar at the bottom of every open window (12 seconds, or until clicked)."""
        from .notices import notify
        notify(text,self)

    def play_next_segment(self):
        """v2.46.1 (Alt+→): select the next shown segment and play it."""
        row=self._selected_row(); n=self.table.rowCount()
        following=row+1
        while following<n and self.table.isRowHidden(following): following+=1
        if following>=n: return
        self._go_to_row(following)
        self.play_selected_segment()

    def toggle_play_segment(self):
        """v2.46.1 (Ctrl+Space): pause while playing; resume a segment paused part-way; otherwise play the selected segment."""
        state=self.player.playbackState()
        if state==QMediaPlayer.PlaybackState.PlayingState: self.player.pause(); return
        if state==QMediaPlayer.PlaybackState.PausedState and self.segment_stop_ms is not None: self.player.play(); return
        self.play_selected_segment()

    def review_and_next(self):
        """v2.46.1 (Ctrl+Enter): tick the selected segment Reviewed and go to the next unreviewed one (one Ctrl+Z undoes the tick)."""
        row=self._selected_row()
        if row<0: return
        box=self._cw(row,4)
        if box is not None and not box.isChecked():
            box.setChecked(True); self._undo_capture()   # its own undo step, even when pressed quickly in a row
        self.jump_unreviewed()

    def show_shortcuts(self):
        """v2.46.1 (F1): every keyboard shortcut of the editor in one small window."""
        rows=[("Ctrl+Space","Play the selected segment, or pause / resume"),("Alt+← / Alt+→","Select and play the segment before / after"),
              ("Ctrl+Enter","Tick Reviewed and go to the next unreviewed segment"),("Ctrl+J / Ctrl+Shift+J","Next / previous unreviewed segment"),
              ("Shift+> / Shift+<","Faster / slower playback, 0.25x a step (Ctrl+] / Ctrl+[ too)"),("Ctrl+F","Find / replace"),("F3 / Shift+F3","Next / previous match"),
              ("Ctrl+Z / Ctrl+Y","Undo / redo (when not typing in a box)"),("Enter in a line with |","Offer to split that line"),("F1","This list")]
        html="<table cellspacing='0' cellpadding='4'>"+"".join(f"<tr><td><b>{k}</b></td><td>{v}</td></tr>" for k,v in rows)+"</table>"
        box=QMessageBox(self); box.setWindowTitle("Keyboard shortcuts"); box.setTextFormat(Qt.RichText); box.setText(html)
        box.setWindowModality(Qt.NonModal); box.show()

    def play_previous_same_speaker(self):
        row=self._selected_row()
        if row<=0:return
        try: current=self._row_segment(row); sid=current.get("speaker_id") or ""
        except Exception:return
        for previous in range(row-1,-1,-1):
            if self.table.isRowHidden(previous): continue
            try: candidate=self._row_segment(previous)
            except Exception: continue
            if (candidate.get("speaker_id") or "") == sid and sid:
                self.table.setCurrentCell(previous,0); self.play_selected_segment(); return

    def toggle_segment_end_capture(self):
        if self._end_capture_row is None: self._filtered_playback_queue=[]
        row=self._selected_row()
        if row<0:return
        if self._end_capture_row is None:
            try: start=parse_time(self._cw(row,0).text())
            except Exception as exc: QMessageBox.warning(self,"Invalid timestamp",str(exc)); return
            self._end_capture_row=row
            self.segment_stop_ms=None
            self.player.setPosition(int(start*1000)); self.player.play()
            self.set_end_btn.setText("⚑ Set end here")
            self.playback_label.setText("Playing selected segment from its start…")
            return
        capture_row=self._end_capture_row
        # Read the playhead first, then pause, both before any timestamp work or dialogs.
        position=self.player.position()/1000.0
        self.player.pause()
        try: start=parse_time(self._cw(capture_row,0).text())
        except Exception as exc:
            QMessageBox.warning(self,"Invalid timestamp",f"The segment's start time is not valid: {exc}"); return
        if position<=start:
            self._notify("The playhead must be after the segment start."); return
        self._cw(capture_row,1).setText(fmt_time(position))
        self.history.addItem("Set segment end from audio playhead · "+fmt_time(position))
        next_row=capture_row+1
        while next_row < self.table.rowCount() and self.table.isRowHidden(next_row): next_row+=1  # skip filtered-out rows
        if next_row < self.table.rowCount():
            try: next_start=parse_time(self._cw(next_row,0).text())
            except Exception: next_start=position
            answer=QMessageBox.question(self,"Adjust next segment start?",f"The next segment currently starts at {fmt_time(next_start)}.\n\nSet its start to the end of this segment ({fmt_time(position)})?",QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
            if answer==QMessageBox.Yes:
                self._cw(next_row,0).setText(fmt_time(position))
                self.history.addItem("Moved next segment start to match previous end")
        self._rebuild_playback_index(); self._schedule_draft_save()
        self._end_capture_row=None
        self.set_end_btn.setText("▶ Play start / set end")

    def _speaker_changed(self, combo, reviewed_checkbox):
        new_sid=combo.currentData() or ""
        old_sid=combo.property("initial_speaker") or ""
        self._style_speaker_combo(combo,new_sid)
        if new_sid and new_sid != old_sid:
            reviewed_checkbox.setChecked(True)

    @_log_if_slow()
    def apply_speaker_filter(self):
        mode=self.speaker_filter.currentData()
        flagged=self._duplicate_sets()[0] if mode=="dupes" else set()
        if mode=="split": flagged=self._split_sets()
        for row in range(self.table.rowCount()):
            sid=self._row_speaker(row)
            mapping=self.speaker_map.get(sid,{})
            role=(mapping.get("role") or "Unknown")
            if mode=="all": visible=True
            elif mode=="speaker_ids": visible=bool(re.fullmatch(r"SPEAKER_\d+",sid))
            elif mode=="unassigned": visible=not sid
            elif mode=="role:Guest": visible=role=="Guest"
            elif mode=="role:Background": visible=role in {"Background","Guest"}
            elif mode=="role:Unknown": visible=role=="Unknown"
            elif mode=="dupes": visible=self._row_edit_id(row) in flagged
            elif mode=="loops": visible=looks_repetitive(self._row_text(row))
            elif mode=="split": visible=self._row_edit_id(row) in flagged
            else: visible=(sid==mode)
            self.table.setRowHidden(row,not visible)
        self._schedule_visible_rows()

    def play_filtered_segments(self):
        start_row=self._selected_row()
        if start_row < 0:
            self._notify("Select the segment where playback should begin first."); return
        queue=[]
        for row in range(start_row,self.table.rowCount()):
            if self.table.isRowHidden(row): continue
            try: seg=self._row_segment(row)
            except Exception: continue
            if float(seg["end"])>float(seg["start"]): queue.append((row,int(float(seg["start"])*1000),int(float(seg["end"])*1000)))
        if not queue:
            self._notify("There are no playable visible segments from the selected row downward."); return
        self._filtered_playback_queue=queue
        self._play_next_filtered_segment()

    def _play_next_filtered_segment(self):
        if not self._filtered_playback_queue:
            self.segment_stop_ms=None; self.playback_label.setText("Finished filtered segments"); return
        row,start_ms,end_ms=self._filtered_playback_queue.pop(0)
        self.segment_stop_ms=end_ms
        self.player.setPosition(start_ms); self.player.play()
        self.playback_label.setText(f"Playing filtered segment · row {row+1}")

    def play_from_selected(self):
        self._filtered_playback_queue=[]
        self._end_capture_row=None; self.set_end_btn.setText("▶ Play start / set end")
        row=self._selected_row()
        if row<0:return
        try: seg=self._row_segment(row)
        except Exception as e: QMessageBox.warning(self,"Invalid timestamp",str(e)); return
        self.segment_stop_ms=None; self.player.setPosition(int(seg['start']*1000)); self.player.play()

    def _seek_to_slider(self):
        if self._seek_dragging: return
        target=int(self.seek_slider.sliderPosition())
        self._requested_seek_ms=target; self._seek_requested_at=time.monotonic(); self.player.setPosition(target)

    @_log_if_slow()
    def _seek_released(self):
        self._seek_dragging=False
        target=int(self.seek_slider.value())
        self._requested_seek_ms=target; self._seek_requested_at=time.monotonic()
        self.player.setPosition(target)

    def set_time_from_playhead(self,col):
        row=self._selected_row()
        if row<0:
            self._notify("Select a segment first."); return
        position=self.player.position()/1000.0
        try:
            start=parse_time(self._cw(row,0).text()); end=parse_time(self._cw(row,1).text())
        except Exception as exc:
            QMessageBox.warning(self,"Invalid timestamp",str(exc)); return
        if col==0 and position>=end:
            self._notify("The playhead is after this segment's end. Move the end first."); return
        if col==1 and position<=start:
            self._notify("The playhead must be after this segment's start."); return
        self._cw(row,col).setText(fmt_time(position))
        self.history.addItem(("Set segment start" if col==0 else "Set segment end")+" from audio playhead · "+fmt_time(position))
        self._rebuild_playback_index(); self._schedule_draft_save()

    @_log_if_slow()
    def _playback_position(self,pos):
        if not self._seek_dragging:
            self.seek_slider.blockSignals(True); self.seek_slider.setValue(int(pos)); self.seek_slider.blockSignals(False)
        if self._requested_seek_ms is not None and abs(pos-self._requested_seek_ms) <= 180:
            self._requested_seek_ms=None
        if self.segment_stop_ms is not None and pos>=self.segment_stop_ms:
            self.player.pause(); self.segment_stop_ms=None
            if self._filtered_playback_queue:
                QTimer.singleShot(80,self._play_next_filtered_segment)
        self.seek_label.setText(f"{fmt_time(pos/1000)} / {fmt_time(self.player.duration()/1000)}")
        # While the recording reloads (audio device or Listen change) the player reports 0:00.
        # That is not a real playhead, so do not move the highlight or scroll to the first line.
        if self._audio_recovery_pending: return
        active=-1; seconds=pos/1000.0
        index=bisect.bisect_right(self._playback_starts,seconds)-1
        if index>=0:
            start,end,row=self._playback_intervals[index]
            if start<=seconds<end: active=row
        if active != self._highlighted_playback_row:
            old=self._highlighted_playback_row
            if 0<=old<self.table.rowCount():self._set_playback_row_highlight(old,False)
            self._highlighted_playback_row=active
            if active>=0 and not self.table.isRowHidden(active):
                self._set_playback_row_highlight(active,True)
                if time.monotonic()>=self._hold_scroll_until:  # just after a reload: keep the user's view
                    self.table.scrollTo(self.table.model().index(active,0),QAbstractItemView.ScrollHint.EnsureVisible)

    @_log_if_slow()
    def _set_playback_row_highlight(self,row,enabled):
        # Use the vertical row header as a playhead marker. Do not restyle or
        # select the transcript editor widget: that keeps its cursor/focus intact.
        label = f"{row + 1}  ▶" if enabled else str(row + 1)
        marker=QTableWidgetItem(label)
        marker.setTextAlignment(Qt.AlignCenter)
        if enabled:
            marker.setBackground(QColor("#D9EAF7"))
            marker.setForeground(QColor("#005A9C"))
            marker.setToolTip("Audio playhead is in this segment")
        self.table.setVerticalHeaderItem(row,marker)

    # ---- volume (0-100%) and louder / levelled listening copies (v2.20.2) ----------------------
    def _speed(self): return self.speed_slider.value()*0.25

    def _speed_changed(self,*_):
        rate=self._speed(); self.speed_label.setText(f"{rate:.2f}x")
        try: self.player.setPlaybackRate(rate)
        except Exception: pass

    def _nudge_speed(self,step): self.speed_slider.setValue(self.speed_slider.value()+step)

    def _volume_changed(self,value):
        self.audio_output.setVolume(value/100.0); self.volume_label.setText(f"{value}%")

    def _listening_changed(self,index):
        if self._boost_process is not None:
            self._boost_process.kill(); self._boost_process=None
        audio_filter=LISTEN_OPTIONS[index][1]
        if audio_filter is None:
            self._switch_listening_source(self.audio_path); return
        try:
            target=listening_cache_path(self.audio_path,audio_filter)
            command=listening_command(self.audio_path,target,audio_filter)
        except Exception as exc:
            self.playback_label.setText(f"Could not prepare louder audio: {exc}"); self.boost_combo.setCurrentIndex(0); return
        if command is None:
            self.playback_label.setText("FFmpeg is unavailable, so louder audio cannot be prepared."); self.boost_combo.setCurrentIndex(0); return
        if target.is_file() and target.stat().st_size>44:
            self._switch_listening_source(str(target)); return
        target.parent.mkdir(parents=True,exist_ok=True)
        part=target.with_suffix(".part.wav"); command[-1]=str(part)
        self.playback_label.setText("Preparing louder audio… playback continues meanwhile")
        process=QProcess(self); self._boost_process=process; self._boost_target=(target,part)
        process.finished.connect(lambda code,status,pr=process: self._listening_ready(pr,code))
        process.start(command[0],command[1:])

    def _listening_ready(self,process,code):
        if process is not self._boost_process: return  # superseded by a newer choice
        self._boost_process=None
        target,part=self._boost_target
        if code!=0 or not part.is_file():
            self.playback_label.setText("Could not prepare louder audio (FFmpeg failed).")
            self.boost_combo.blockSignals(True); self.boost_combo.setCurrentIndex(0); self.boost_combo.blockSignals(False); return
        part.replace(target)
        self._switch_listening_source(str(target))

    def _switch_listening_source(self,path):
        """Swap the audio file without losing the play position or whether it was playing."""
        if not path or not Path(path).is_file() or path==self._play_path: return
        self._begin_audio_reload()
        self._play_path=path
        self.player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))
        self.playback_label.setText("Listening: "+self.boost_combo.currentText())
        self._finish_if_already_loaded()

    def _begin_audio_reload(self):
        """Remember where playback and the list were, then stop. Used before every source reload."""
        self._audio_recovery_resume=self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState
        self._audio_recovery_position=int(self.player.position() or 0)
        self._audio_recovery_scroll=self.table.verticalScrollBar().value()
        self._audio_recovery_pending=True
        self.player.stop()

    def _finish_if_already_loaded(self):
        # Qt may skip the "loaded" signal when the file was already loaded; restore anyway.
        if self._audio_recovery_pending and self.player.mediaStatus()==QMediaPlayer.MediaStatus.LoadedMedia:
            QTimer.singleShot(0,lambda: self._audio_media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia))

    def _audio_error(self, error, error_string):
        self.playback_label.setText("Audio output unavailable · attempting recovery")
        self.playback_label.setToolTip(str(error_string or error))
        if self.logger: self.logger.warning("Transcript audio playback error: %s · %s",error,error_string)
        if not self._audio_recovery_attempted:
            self._audio_recovery_attempted=True
            QTimer.singleShot(350,self._recover_audio_output)

    @staticmethod
    def _default_output_id():
        try: return bytes(QMediaDevices.defaultAudioOutput().id())
        except Exception: return b""

    def _audio_devices_changed(self):
        # audioOutputsChanged also fires for unrelated devices (a USB headset being
        # plugged in elsewhere). Reload the recording only if the default output changed.
        current=self._default_output_id()
        if current==self._last_default_output: return
        self._last_default_output=current
        QTimer.singleShot(250,self._recover_audio_output)

    def _recover_audio_output(self):
        if not self._play_path or not Path(self._play_path).is_file(): return
        try:
            self._begin_audio_reload()
            self.audio_output.setDevice(QMediaDevices.defaultAudioOutput())
            self.player.setSource(QUrl())  # same file again: clear first so Qt really reloads it
            self.player.setSource(QUrl.fromLocalFile(str(Path(self._play_path).resolve())))
            self.playback_label.setText("Audio output reset · reloading recording")
            self._finish_if_already_loaded()
        except Exception as exc:
            self._audio_recovery_pending=False
            self.playback_label.setText("Audio unavailable · check Windows sound output")
            if self.logger: self.logger.exception("Could not recover transcript audio output")

    def _audio_media_status_changed(self,status):
        if not self._audio_recovery_pending or status!=QMediaPlayer.MediaStatus.LoadedMedia:
            return
        self._audio_recovery_pending=False
        self._audio_recovery_attempted=False
        try:
            # Keep the list where the user left it, even if the playhead line is elsewhere.
            self._hold_scroll_until=time.monotonic()+1.5
            if self._audio_recovery_scroll is not None:
                self.table.verticalScrollBar().setValue(self._audio_recovery_scroll); self._audio_recovery_scroll=None
            self.player.setPosition(self._audio_recovery_position)
            self.playback_label.setText(("Listening: "+self.boost_combo.currentText()+" · ready") if self._play_path!=self.audio_path else "Audio output reset · ready to play")
            if self._audio_recovery_resume: self.player.play()
        except Exception:
            if self.logger: self.logger.exception("Could not restore playback position after audio reset")

    def _playback_state(self,state):
        if state==QMediaPlayer.PlaybackState.PlayingState:
            self.pause_btn.setText("Ⅱ Pause")
            if abs(self.player.playbackRate()-self._speed())>0.001: self.player.setPlaybackRate(self._speed())  # a source switch can reset it
        elif state==QMediaPlayer.PlaybackState.PausedState:
            self.pause_btn.setText("▶ Play")
        else:
            self.pause_btn.setText("▶ Play")
            self.segment_stop_ms=None

    def stop_playback(self): self.segment_stop_ms=None; self._filtered_playback_queue=[]; self._end_capture_row=None; self.set_end_btn.setText("▶ Play start / set end"); self.player.stop(); self.pause_btn.setText("▶ Play")

    def _load_history(self):
        if self.edit_events:
            for event in self.edit_events[-100:]:
                before=event["before_value"] or "∅"; after=event["after_value"] or "∅"
                self.history.addItem(f"{event['edited_at']} · {event['event_type']} · {before} → {after}")
        else:
            self.history.addItem("No previous detailed edit events. Changes made here will be audited when saved.")

    def update_suggestions(self):
        self.suggestion_list.clear()
        row=self._selected_row()
        if row<0 or not (self.vocabulary_terms or self._book_terms):return
        text_widget=self._cw(row,3)
        if text_widget is None:return
        text=text_widget.toPlainText()
        words=list(set(re.findall(r"[A-Za-z][A-Za-z'-]{3,}",text)))[:300]
        candidates=[]
        # Phrase-level corrections are especially useful for misheard clauses;
        # they remain suggestions and are never applied automatically.
        for heard,canonical in self.vocabulary_aliases.items():
            if len(heard.split())>1 and re.search(r"(?<!\w)"+re.escape(heard)+r"(?!\w)",text,re.I):
                candidates.append((1.0,heard,canonical))
        for word in words:
            folded=word.casefold()
            for length in range(max(1,len(word)-3),len(word)+4):
                for alias,canonical in self._aliases_by_length.get(length, ()):
                    if folded==alias.casefold():
                        candidates.append((1.0,word,canonical)); break
                    ratio=similar(folded,alias.casefold(),0.82)
                    if ratio:
                        candidates.append((ratio,word,canonical))
            for length in range(max(1,len(word)-4),len(word)+5):
                for term in self._vocabulary_by_length.get(length, ()):
                    if term.casefold()==folded: continue
                    ratio=similar(folded,term.casefold(),0.70)
                    if ratio:
                        candidates.append((ratio,word,term))
            if word[0].isupper():   # v2.46: book names only for capitalised words, same first letter
                for length in range(max(1,len(word)-2),len(word)+3):
                    for term in self._book_terms.get((folded[0],length), ()):
                        if term.casefold()==folded: continue
                        ratio=similar(folded,term.casefold(),0.80)
                        if ratio:
                            candidates.append((ratio,word,term))
        seen=set()
        for ratio,word,term in sorted(candidates,reverse=True):
            if (word.casefold(),term) in seen: continue
            seen.add((word.casefold(),term))
            if len(seen)>60: break
            item=QListWidgetItem(f"{word}  →  {term}  ({ratio:.0%} match)")
            item.setData(Qt.UserRole,(word,term)); self.suggestion_list.addItem(item)

    def apply_suggestion(self):
        row=self._selected_row(); item=self.suggestion_list.currentItem()
        if row<0 or not item:return
        word,term=item.data(Qt.UserRole); editor=self._cw(row,3)
        pattern=r"(?<!\w)"+re.escape(word)+r"(?!\w)"
        # A function replacement keeps backslashes and group references in names literal.
        new_text,n=re.subn(pattern,lambda _m: term,editor.toPlainText(),count=1,flags=re.IGNORECASE)
        if n:
            editor.setPlainText(new_text); self.history.addItem(f"Suggested vocabulary correction · {word} → {term}")
            self.update_suggestions()

    def values(self):
        text,segments,mapping=self._values_raw()
        return text,json.dumps(segments,ensure_ascii=False),json.dumps(mapping,ensure_ascii=False)

    def _values_raw(self):
        """(text, segments, speaker map) as Python objects. The autosave uses these directly: turning them
        into JSON and straight back on the window's thread was wasted work on every autosave (v2.31.3);
        the background save does the JSON once."""
        segments=self._read_rows(); mapping={}
        for r in range(self.mapping_table.rowCount()):
            sid=self.mapping_table.item(r,0).text(); role=self.mapping_table.cellWidget(r,1); include=self.mapping_table.cellWidget(r,5)
            mapping[sid]={"role":role.currentText() if role else "Unknown","player":self.mapping_table.item(r,2).text().strip() if self.mapping_table.item(r,2) else "",
                          "player_nicknames":self.mapping_table.item(r,3).text().strip() if self.mapping_table.item(r,3) else "","character":self.mapping_table.item(r,4).text().strip() if self.mapping_table.item(r,4) else "","include_in_notes":bool(include.isChecked()) if include else False}
        # Keep profile samples separate from ordinary diarization labels.
        mapping["_confirmed_voice_sample_segment_ids"]=[s['_edit_id'] for s in segments if s.get('confirmed_voice_sample') and s.get('speaker_id')]
        text='\n'.join(s['text'] for s in segments)
        return text,segments,mapping
