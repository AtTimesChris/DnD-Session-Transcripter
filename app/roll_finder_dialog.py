"""Roll finder window (v2.37; context, editing, pass / fail, damage taken and the session summary in v2.39;
rolls nobody asked for, the DM's attacks, credit from names, "No roll" and speaker changes in v2.40).

Checks, attacks, damage and save DCs in a session, what was said straight after, the bonus on that character's
D&D Beyond snapshot, and whether it passed or failed. Lines can be listened to and corrected here; the
results (with your corrections) are kept per transcript version for session and campaign stats."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QTableWidget, QTableWidgetItem,
                               QHeaderView, QAbstractItemView, QPushButton, QDialogButtonBox, QGridLayout, QSplitter,
                               QWidget, QGroupBox, QMessageBox, QInputDialog, QCheckBox)
from PySide6.QtGui import QColor

from .range_dialogs import _RowPlayback
from .column_order import arrange
from .roll_finder import (assess, check_label, sheet, detect_outcome, roll_key, damage_total, session_stats, ac_outcome,
                          detect_advantage, link_damage, repeat_flags, check_choices, check_from_label, stat_rows,
                          critical_said, notable_moments)
from .theme import tone   # v2.46.1: shared colours

# v2.44: Used (the weapon or spell, which you can set) and Line (the line's number in the editor) added at the end
COLS = ["Time", "Asked for", "Asked by", "Who", "What was said", "Read as", "Sheet", "Note", "Adv / Dis", "Pass / fail", "Listen",
        "Used", "Line"]
C_TIME, C_CHECK, C_ASKER, C_WHO, C_SAID, C_READ, C_SHEET, C_NOTE, C_ADV, C_OUT, C_PLAY, C_USED, C_LINE = range(len(COLS))
ADV = [("Normal", ""), ("Advantage", "adv"), ("Disadvantage", "dis"), ("Both (straight)", "both")]   # v2.44: "Normal" when nothing was said
DAMAGE_OUTCOMES = [("Count", ""), ("Ignore this number", "ignore")]
FLAG = "⚑ "   # v2.44: something for you to check; never acted on by itself
INFO_NOTES = ("Natural", "includes", "spell damage", "critical hit")
# (a spell note that does not fit starts "said …" or "… is outside …" and is shown as a possible mix-up)   # shown in yellow: information, not a mix-up
OUTCOMES = [("?", ""), ("Pass", "pass"), ("Fail", "fail"), ("No roll", "none"), ("Ignore this number", "ignore")]
DM_OUTCOMES = [("?", ""), ("Hit", "pass"), ("Miss", "fail"), ("No roll", "none"), ("Ignore this number", "ignore")]
_READER_KINDS = ("skill", "check", "save", "death")   # attacks: whoever says the number is usually the one attacking


def _set_widget(table, r, c, w):
    """setCellWidget that hides the widget it replaces at once. Qt only deletes a replaced widget when control
    returns to the event loop it was replaced in; the first fill happens before the window's own loop starts,
    so the old lists stayed drawn (at the top-left) until the window closed: the "ghost box"."""
    old = table.cellWidget(r, c)
    if old is not None and old is not w:
        old.hide()
        table.removeCellWidget(r, c)
    if w is not None:
        table.setCellWidget(r, c, w)


def _signed(n):
    return "" if n is None else (f"+{n}" if n >= 0 else str(n))


def read_as(r):
    if r.get("kind") == "damage":
        if r.get("dice"):
            dice = " + ".join(f"{c}d{s}" for c, s in r["dice"])
            return f"damage {dice}" + (f" {_signed(r['bonus'])}" if r.get("bonus") is not None else "")
        return f"damage {r['total']}"
    if r.get("kind") == "dc":
        return f"save DC {r['total']}"
    if r.get("nat"):
        return f"natural {r['nat']}"
    if r.get("die") is not None and r.get("bonus") is not None:
        return f"d20 {r['die']} {_signed(r['bonus'])} = {r['total']}"
    if r.get("total") is not None:
        return f"{r['total']} (die or total?)"
    return ""


class RollContext:
    """What the window needs from the editor (all callables).
    data() -> {'events', 'segments', 'taken'}; who(sid); role(sid); lookup(sid) -> (character, summary, note);
    sheet_for(character) -> (summary, note); people() -> [names for the Who lists];
    overrides() -> {(key, field): value}; set_override(key, field, value); save_log(rows);
    apply_texts({edit_id: text}, speakers={edit_id: sid}) -> int; speakers() -> [(sid, label)];
    play(start, end); pause() -> bool; go_to(edit_id); edit_players() -> bool."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class RollFinderDialog(QDialog, _RowPlayback):
    def __init__(self, ctx, fmt_time, parent=None):
        super().__init__(parent)
        from PySide6.QtGui import QKeySequence, QShortcut
        self.ctx, self.fmt_time = ctx, fmt_time
        self.play = ctx.play
        self._init_playback(ctx.pause)
        self.setWindowTitle("Roll finder")
        self.resize(1400, 820)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        layout = QVBoxLayout(self)
        intro = QLabel("Checks, attacks, spell attacks, damage and save DCs in this session, what was said straight after, the "
                       "character's D&D Beyond numbers, and whether it passed or failed (from what was said next: “that hits”, "
                       "“I failed”, “bounces off”…). Select a row to see and hear the lines around it, and correct any that were "
                       "misheard. Set Pass / fail or Who yourself where it guessed wrong: your choices are kept. The results are "
                       "saved with this transcript for session and campaign stats.")
        intro.setWordWrap(True); layout.addWidget(intro)
        top = QHBoxLayout()
        self.show_filter = QComboBox()   # v2.45: was self.show, which hid QDialog.show() (needed now the window is not modal)
        for label, key in (("All rows", "all"), ("Only rows with a number", "numbers"), ("Possible mix-ups", "odd"),
                           ("Natural 20s and 1s", "nat"), ("Damage rolls", "damage"), ("Damage taken", "taken"),
                           ("Pass / fail not known", "unknown"), ("Advantage / disadvantage", "adv"),
                           ("Flags to check ⚑", "flags"), ("Rolls with nothing said before them", "nolead"),
                           ("Ignored numbers", "ignored")):
            self.show_filter.addItem(label, key)
        self.show_filter.currentIndexChanged.connect(self._fill)
        # v2.44: an attack with its hit / miss, damage and damage taken under it (All rows, Any kind only)
        self.group = QCheckBox("Group under attacks"); self.group.setChecked(True); self.group.toggled.connect(self._fill)
        self.group.setToolTip("Show each attack as one row with ▸: click the time to open it and see its damage and the damage "
                              "taken under it. Damage said as a roll of its own is put under the same character's attack "
                              "just before it (within 90 s). Only with All rows and Any kind.")
        self._expanded = set()
        self.kind = QComboBox(); self.kind.currentIndexChanged.connect(self._fill)
        players = QPushButton("Players and characters…"); players.clicked.connect(self._players)
        players.setToolTip("Say which character each player plays in this campaign, so their rolls can be compared with the sheet.")
        summary = QPushButton("Session summary…"); summary.clicked.connect(self._summary)
        summary.setToolTip("Per character: rolls passed and failed, natural 20s and 1s, attacks hit and missed, damage dealt and taken.")
        campaign = QPushButton("Campaign summary…"); campaign.clicked.connect(self._campaign_summary)
        campaign.setToolTip("The same counts over every session of this campaign whose roll finder has been opened (each one's "
                            "results are saved when the roll finder is used).")
        campaign.setEnabled(hasattr(ctx, "campaign_logs"))
        open_all = QPushButton("Open all"); open_all.clicked.connect(lambda: self._expand_all(True))
        close_all = QPushButton("Close all"); close_all.clicked.connect(lambda: self._expand_all(False))
        open_all.setToolTip("Open every attack to show its damage and damage taken."); close_all.setToolTip("Close every attack.")
        changes = QPushButton("Your changes…"); changes.clicked.connect(self._your_changes)
        changes.setToolTip("Every choice you made in this window (Pass / fail, Who, Asked for, Used, Adv / Dis, rolls you added), "
                           "newest first, with Undo: to find one changed by accident.")
        again = QPushButton("↻ Look again"); again.clicked.connect(self._reload)
        again.setToolTip("Find the rolls again from the transcript as it is now in the editor (done by itself when you come "
                         "back to this window after editing, unless you have unapplied changes in the lines panel).")
        for w in (QLabel("Show"), self.show_filter, self.kind, self.group, open_all, close_all, again, changes, players, summary, campaign):
            top.addWidget(w)
        top.addStretch(); layout.addLayout(top)
        # v2.46: said where you will see it (it was only in the status line at the bottom)
        self.banner = QLabel(); self.banner.setWordWrap(True); self.banner.hide()
        tone(self.banner, "notice")
        layout.addWidget(self.banner)

        split = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, len(COLS)); self.table.setHorizontalHeaderLabels(COLS)
        h = self.table.horizontalHeader()
        for c in (C_TIME, C_ASKER):
            h.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        # v2.43: the other columns have set widths (Asked for / Read as / Sheet fitted and capped in _fit_columns), so a
        # long sheet note or a wide list no longer takes the width from "What was said" (which made rows very tall).
        # Drag a header edge to change one.
        for c in (C_CHECK, C_READ, C_SHEET, C_NOTE, C_WHO, C_ADV, C_OUT, C_PLAY, C_USED):
            h.setSectionResizeMode(c, QHeaderView.Interactive)
        h.setSectionResizeMode(C_LINE, QHeaderView.ResizeToContents)
        for c, w in ((C_NOTE, 260), (C_WHO, 160), (C_ADV, 120), (C_OUT, 90), (C_PLAY, 50), (C_USED, 150), (C_CHECK, 150)):
            self.table.setColumnWidth(c, w)
        h.setSectionResizeMode(C_SAID, QHeaderView.Stretch)
        # v2.42: the controls together on the right (only the on-screen order; drag headers to change it)
        arrange(h, "roll_finder_v244", [C_TIME, C_LINE, C_CHECK, C_ASKER, C_SAID, C_READ, C_USED, C_SHEET, C_NOTE, C_WHO, C_ADV,
                                        C_OUT, C_PLAY])
        self.table.setWordWrap(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.cellDoubleClicked.connect(self._double_clicked)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)   # v2.50: right-click in the main table too
        self.table.customContextMenuRequested.connect(self._table_menu)
        self.table.cellClicked.connect(self._cell_clicked)
        self.table.currentCellChanged.connect(lambda r, *_: self._show_context(r))
        split.addWidget(self.table)

        box = QGroupBox("Lines around the selected row (correct the text or the speaker, then Apply). To split a line where "
                        "people talk over each other: {Sam} Okay! | {Alex} Yay! | {Kit} Let's go.")
        bl = QVBoxLayout(box)
        self.ctx_table = QTableWidget(0, 4); self.ctx_table.setHorizontalHeaderLabels(["Time", "Who", "Text", "Listen"])
        ch = self.ctx_table.horizontalHeader()
        ch.setSectionResizeMode(0, QHeaderView.ResizeToContents); ch.setSectionResizeMode(1, QHeaderView.Interactive)
        ch.setSectionResizeMode(2, QHeaderView.Stretch); ch.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.ctx_table.setColumnWidth(1, 160)   # v2.43: room for the speaker list (it was sized to the name under it)
        self.ctx_table.setWordWrap(True)
        arrange(ch, "roll_finder_lines", [0, 2, 1, 3])   # Time · Text · Who · ▶
        self.ctx_table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed)
        self.ctx_table.itemChanged.connect(self._ctx_edited)
        self.ctx_table.setContextMenuPolicy(Qt.CustomContextMenu)   # v2.46: right-click a line: Add roll here…
        self.ctx_table.customContextMenuRequested.connect(self._ctx_menu)
        self.ctx_table.setToolTip("Right-click a line to add a roll the finder missed (Add roll here…).")
        bl.addWidget(self.ctx_table, 1)
        crow = QHBoxLayout()
        earlier = QPushButton("◀ Earlier 30 s"); earlier.clicked.connect(lambda: self._extend_context(-30))
        earlier.setToolTip("Show 30 more seconds before the roll (press again for more): to find the spell cast or what led to it.")
        later = QPushButton("Later 30 s ▶"); later.clicked.connect(lambda: self._extend_context(30))
        later.setToolTip("Show 30 more seconds after the roll (press again for more): to find the result or the response.")
        self.play_on = QPushButton("▶ Play from here (30 s)"); self.play_on.clicked.connect(self._play_on)
        self.play_on.setToolTip("Plays from the start of the selected row for 30 seconds, to hear what was said next.")
        self.apply_btn = QPushButton("Apply changes"); self.apply_btn.clicked.connect(self._apply_texts); self.apply_btn.setEnabled(False)
        self.apply_btn.setToolTip("Writes your corrected lines and speakers into the transcript (one Ctrl+Z in the editor undoes "
                                  "them) and looks for rolls again. A line whose speaker you change is marked Reviewed, as in the editor.")
        go = QPushButton("Go to line in editor"); go.clicked.connect(lambda: self._go(self.table.currentRow()))
        pause_btn = QPushButton("⏯ Pause / resume"); pause_btn.clicked.connect(self._toggle_current)
        for w in (earlier, later, self.play_on, pause_btn, self.apply_btn, go):
            crow.addWidget(w)
        crow.addStretch(); bl.addLayout(crow)
        split.addWidget(box)
        split.setSizes([480, 260])
        layout.addWidget(split, 1)
        QShortcut(QKeySequence(Qt.Key_Space), self.table, activated=self._toggle_current)
        self.status = QLabel(); layout.addWidget(self.status)
        self.sheets = QLabel(); self.sheets.setWordWrap(True); tone(self.sheets, "dim"); layout.addWidget(self.sheets)
        buttons = QDialogButtonBox(QDialogButtonBox.Close); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self._edits = {}; self._speaker_edits = {}
        self._reload()
        from .window_state import remember; remember(self)   # v2.46.1: opens at the size and place you left it
        from .notices import attach; attach(self)   # v2.47: yellow note bar at the bottom

    # ---- data ----
    def _reload(self, *_):
        self._seen_version = self.ctx.version() if hasattr(self.ctx, "version") else 0
        if hasattr(self, "banner"):
            self.banner.hide()
        d = self.ctx.data()
        self.overrides = self.ctx.overrides()
        self.events, self.segments, self.taken = d["events"], d["segments"], d["taken"]
        self.events = sorted(list(self.events) + self._added_events(), key=lambda e: e["start"])
        current = self.kind.currentData()
        self.kind.blockSignals(True); self.kind.clear(); self.kind.addItem("Any kind", "")
        for label in sorted({check_label(e["check"]) for e in self.events} | ({"Damage taken"} if self.taken else set())):
            self.kind.addItem(label, label)
        if current and self.kind.findData(current) >= 0:
            self.kind.setCurrentIndex(self.kind.findData(current))
        self.kind.blockSignals(False)
        self.all_rows = self._build_rows()
        self._fill()
        self._save_log()

    def _added_events(self):
        """v2.46: rolls you added with Add roll here… (kept as choices under the key "added|n")."""
        import json
        out = []
        for (key, field), value in self.overrides.items():
            if field != "added":
                continue
            try:
                a = json.loads(value)
            except Exception:
                continue
            check = ("dm_attack", None) if a.get("check") == "DM attack" else (check_from_label(a.get("check", "")) or ("unknown", None))
            st, en = float(a.get("start", 0)) + 0.3, float(a.get("end", 0))
            text = a.get("text", "")
            res = []
            if a.get("total") is not None or a.get("nat"):
                kind = "damage" if check[0] == "damage" else "d20"
                r = {"kind": kind, "die": a.get("nat"), "bonus": None, "total": a.get("total"), "nat": a.get("nat"), "nat_dice": 1,
                     "said": "added by you", "speaker": "", "edit_id": a.get("edit_id"), "start": st, "end": max(en, st),
                     "text": text, "named": "" if check[0] == "dm_attack" else a.get("who", ""), "about_other": False}
                if kind == "damage":
                    r.update(dice=[], bonus=None)
                res.append(r)
            out.append({"start": st, "end": max(en, st), "edit_id": a.get("edit_id"), "asked_by": "", "check": check,
                        "ask_text": text, "context": text, "wide_context": text, "adv_text": "", "results": res,
                        "target": a.get("who", "") if check[0] == "dm_attack" else "", "target_sid": "", "ac_said": None,
                        "unasked": True, "added_key": key, "added_note": a.get("note", "")})
        return out

    def _dm_names(self):
        """v2.50: the DM's names in this session (their player name and "DM"), so a roll credited by the name said counts for the DM."""
        names = {"DM"}
        try:
            for sid, label in (self.ctx.speakers() if hasattr(self.ctx, "speakers") else []):
                if self.ctx.role(sid) == "DM":
                    names.update({label, self.ctx.who(sid)})
        except Exception:
            pass
        return names

    def _ac_set(self, character):
        """v2.50: the AC you set for this character in this session (right-click a row → Set AC…), or None."""
        try:
            v = self.overrides.get((f"ac|{character}", "ac")) if character else None
            return int(v) if v else None
        except (TypeError, ValueError):
            return None

    def _bladesong(self, e, summary):
        """v2.50: (INT modifier, when said) when Bladesong / bladesinging was said in the 10 minutes before this attack."""
        import re as _re
        intmod = ((summary or {}).get("modifiers") or {}).get("INT")
        if intmod is None:
            return None
        said = [float(s["start"]) for s in self.segments if e["start"] - 600 <= float(s["start"]) <= e["end"] + 5
                and _re.search(r"\bblade\s?(?:song|sing(?:ing|er)?)", str(s.get("text") or ""), _re.I)]
        return (int(intmod), said[-1]) if said else None

    def _person(self, sid):
        return (self.ctx.lookup(sid)[0] or self.ctx.who(sid)) if sid else ""

    def _credit(self, e, r):
        """Whose row this is -> (sid, name, note). A name said ("Mia got 15", "Robin, give me…") wins; then a
        number the DM reads out after a player's check, or another player reads out for the one who made the check,
        counts for that player; otherwise whoever said it. name, when given, is used instead of sid's character."""
        role, who = self.ctx.role, self.ctx.who
        kind = e["check"][0]
        asker_is_player = bool(e["asked_by"]) and role(e["asked_by"]) != "DM"
        if kind == "dm_attack":   # the row belongs to the character attacked
            if e.get("target"):
                return "", e["target"], ""
            sid = e.get("target_sid") or ""
            return sid, "", ("" if sid else "who was attacked is not known: set Who")
        if not r:
            if e.get("target"):
                return "", e["target"], ""
            return (e["asked_by"] if asker_is_player else ""), "", ""
        reader = r.get("speaker", "")
        said_by = r.get("said_by")
        note = f"total read out by {who(said_by)}" if said_by and said_by != reader else ""
        reader_is_player = bool(reader) and role(reader) != "DM"
        if r.get("about_other") and reader_is_player and not r.get("named"):
            return "", "", f"{who(reader)} said this about someone else's damage: set Who"
        if r.get("named"):
            if reader_is_player and self._person(reader) != r["named"]:
                note = note or f"said by {who(reader)}"
            return "", r["named"], note
        if e.get("target") and kind != "initiative" and self._person(reader) != e["target"]:
            if reader_is_player:
                note = note or f"said by {who(reader)}; asked of {e['target']}"
            return "", e["target"], note
        if not reader_is_player and asker_is_player and r.get("kind") != "damage":
            return e["asked_by"], "", note   # the DM reading out the player's roll
        if (reader_is_player and asker_is_player and reader != e["asked_by"] and kind in _READER_KINDS
                and r.get("kind") in ("d20", "dc")):
            return e["asked_by"], "", note or f"said by {who(reader)}; {who(e['asked_by'])} made the check"
        return reader, "", note

    def _build_rows(self):
        if not hasattr(self, "spells"):
            try:
                self.spells = self.ctx.spells() if hasattr(self.ctx, "spells") else {}
            except Exception:
                self.spells = {}
        out = []
        for e0 in self.events:
            # v2.44: "Asked for" you set (kept under the roll's first key), used for the sheet and the stats
            set_check = self.overrides.get((roll_key(e0, 0), "check"))
            new_check = check_from_label(set_check) if set_check and e0["check"][0] != "dm_attack" else None
            e = dict(e0, check=new_check, check_set=True) if new_check else e0
            used = self.overrides.get((roll_key(e0, 0), "used"), "")
            auto, evidence, _eid = detect_outcome(e, self.segments)
            adv_auto, adv_evidence = detect_advantage(e)
            dm_attack = e["check"][0] == "dm_attack"
            results = list(e["results"])
            if dm_attack and not any(r.get("kind") == "d20" for r in results):
                results = [None] + results   # the attack itself, even when no number was said
            results = results or [None]
            for i, r in enumerate(results):
                key = roll_key(e0, i)
                sid, name, credit_note = self._credit(e, r)
                who_override = self.overrides.get((key, "who"))
                if who_override or name:
                    character = who_override or name; summary, note = self.ctx.sheet_for(character); player = ""
                else:
                    character, summary, note = self.ctx.lookup(sid) if sid else ("", None, "")
                    player = self.ctx.who(sid) if sid else ""
                row_auto, row_evidence = auto, evidence
                if dm_attack and not (r and r.get("kind") == "damage"):
                    ac_mine = self._ac_set(character)
                    ac = ac_mine or e.get("ac_said") or (summary or {}).get("ac_estimate")
                    total = (r or {}).get("total")
                    shield = e.get("shield")
                    blade = None if (ac_mine or e.get("ac_said")) else self._bladesong(e, summary)
                    if blade and ac:
                        ac += blade[0]   # v2.50: Bladesong said before the attack: + INT modifier
                    ac_now = (ac + 5) if (ac and shield) else ac   # v2.46: Shield adds 5 to AC (until the start of their next turn)
                    label = (f"AC {ac}" + (" (set by you)" if ac_mine else " (said)" if e.get("ac_said") else
                                           f" (sheet + Bladesong {blade[0]})" if blade else "")) if ac else ""
                    if shield:
                        label = (label + f" + 5 Shield = {ac_now}") if ac else "Shield: AC + 5"
                    info = {"options": {}, "label": label}
                    odd = f"{total} against AC {ac_now}" if (total is not None and ac_now) else ""
                    if shield:
                        on_sheet = any("shield" == str(s).casefold() for s in (summary or {}).get("spells") or [])
                        odd = "; ".join(p for p in (odd, f"{self.ctx.who(shield['speaker'])} cast Shield (“{shield['text']}”): +5 AC"
                                                    + (", on their sheet" if on_sheet else "")) if p)
                    if blade:
                        odd = "; ".join(p for p in (odd, f"Bladesong said at {self.fmt_time(blade[1])[:8]}: + INT {blade[0]} to AC") if p)
                    if r and r.get("dm_answer"):
                        odd = "; ".join(p for p in (odd, f"“{r['text']}” was the answer to “What did it roll?”, so it is taken as the "
                                                    f"DM's number (the line is labelled {self.ctx.who(r.get('speaker', ''))}: check "
                                                    "the speaker)") if p)
                    if not row_auto and ac_outcome(total, ac_now):
                        row_auto, row_evidence = ac_outcome(total, ac_now), odd
                    elif row_auto and ac_outcome(total, ac_now) and ac_outcome(total, ac_now) != row_auto:
                        # v2.46: what was said wins; say why the numbers disagree
                        odd += ("; the table said it missed, so the AC was higher than this (Bladesong, a spell or cover?)"
                                if row_auto == "fail" else "; the table said it hit, so the AC was lower than this")
                    kind = "roll"
                else:
                    info = (self._sheet_info(summary, e, r, used) if (r and not dm_attack) else {"options": {}, "label": ""})
                    odd = assess(r, info) if (r and not dm_attack) else ""
                    kind = "damage" if r and r.get("kind") == "damage" else ("dc" if r and r.get("kind") == "dc" else "roll")
                if credit_note:
                    odd = f"{odd}; {credit_note}" if odd else credit_note
                if e.get("added_key"):
                    odd = "; ".join(p for p in ("added by you" + (f": {e['added_note']}" if e.get("added_note") else ""), odd) if p)
                outcome = self.overrides.get((key, "outcome"), row_auto if kind == "roll" else "")
                out.append({"type": "roll", "key": key, "key0": roll_key(e0, 0), "event": e, "result": r, "summary": summary,
                            "used": used, "flags": [], "sid": sid if not (who_override or name) else "",
                            "player": player, "character": character, "info": info, "odd": odd, "sheet_note": note, "kind": kind,
                            "dm_attack": dm_attack, "outcome": outcome or "", "auto": row_auto or "", "evidence": row_evidence,
                            "reason": self.overrides.get((key, "reason"), ""),
                            "adv": self.overrides.get((key, "adv"), adv_auto if kind == "roll" and not dm_attack else "").strip("-"),
                            "adv_auto": adv_auto, "adv_evidence": adv_evidence, "adv_manual": (key, "adv") in self.overrides,
                            "manual": (key, "outcome") in self.overrides, "start": r["start"] if r else e["start"],
                            "end": r["end"] if r else e["end"], "edit_id": (r or {}).get("edit_id") or e["edit_id"]})
        for t in self.taken:
            key = f"taken|{t['start']:.1f}"
            target = self.overrides.get((key, "who"), t.get("target_guess") or t.get("target") or "")
            out.append({"type": "taken", "key": key, "taken": t, "target": target, "start": t["start"], "end": t["end"],
                        "edit_id": t["edit_id"], "kind": "taken", "character": target, "outcome": "", "odd": "",
                        "manual": (key, "who") in self.overrides})
        out.sort(key=lambda x: x["start"])
        self._link_and_flag(out)
        return out

    def _sheet_info(self, summary, e, r, used="", crit=None, wide_extra=""):
        """What the sheet says for one result. used: the weapon or spell you chose (then only that name is looked for);
        crit / wide_extra: from the attack a damage roll belongs to (v2.44)."""
        context, wide = e.get("context", ""), (wide_extra + " " + e.get("wide_context", "")).strip()
        if crit is None:
            crit = critical_said(context, r)
        if used:
            context = wide = used
        return sheet(summary, e["check"], context, r, wide=wide, spells=self.spells, crit=crit)

    def _link_and_flag(self, out):
        """v2.44: damage said as its own roll takes the spell and critical hit of the same character's attack just
        before it; and the flags for you to check (never acted on by themselves)."""
        self.links = link_damage(out)
        for x in out:
            if x["type"] != "roll":
                continue
            e = x["event"]
            atk = self.links.get(e["start"])
            if atk is not None and x["kind"] == "damage" and x["result"]:
                crit = any(r.get("nat") == 20 for r in atk["results"]) or critical_said(atk.get("context", ""))
                x["info"] = self._sheet_info(x["summary"], e, x["result"], x["used"], crit=crit or None,
                                             wide_extra=atk.get("wide_context", ""))
                odd = assess(x["result"], x["info"])
                x["odd"] = "; ".join(p for p in (odd, f"part of the attack at {self.fmt_time(atk['start'])[:8]}") if p)
            if e.get("no_lead") and x["key"] == x["key0"]:
                x["flags"].append("nothing about a roll was said in the 30 s before: a gap in the transcript? "
                                  "(Alternatives… in the editor can re-transcribe that stretch)")
            if e.get("ac_answer") and x["key"] == x["key0"]:
                a = e["ac_answer"]
                x["flags"].append(f"{self.ctx.who(a['speaker'])} said “{a['text']}” after the AC question: their AC? "
                                  "(not used: set Pass / fail yourself)")
        for key, (earlier, total) in repeat_flags(out).items():
            x = next(y for y in out if y["key"] == key)
            x["flags"].append(f"this roll also says {total}, already counted at {self.fmt_time(earlier)[:8]}; this row adds "
                              f"{damage_total(x['result'])}: if it is the same damage, choose Ignore this number")
        # v2.46: "A double nat 20, imagine that!" 15 minutes after the double nat 20 is talk about that one
        nats = [x for x in out if x["type"] == "roll" and x["result"] and x["result"].get("nat")]
        for x in nats:
            if not x["event"].get("unasked"):
                continue
            n, two = x["result"]["nat"], (x["result"].get("nat_dice") or 1) > 1
            before = [y for y in nats if y is not x and y["start"] < x["start"] and x["start"] - y["start"] <= 1200
                      and y["result"]["nat"] == n and ((y["result"].get("nat_dice") or 1) > 1) == two]
            if before:
                x["flags"].append(f"said again {self.fmt_time(x['start'] - before[-1]['start'])[3:8]} after the "
                                  f"{'double ' if two else ''}natural {n} at {self.fmt_time(before[-1]['start'])[:8]}: talk about "
                                  "that one? If so, choose Ignore this number")

    def _cancelled(self):
        """{event start: reason} for rolls you marked "No roll" (every row of that roll is left out of the stats)."""
        return {x["event"]["start"]: x.get("reason", "") for x in self.all_rows if x["type"] == "roll" and x["outcome"] == "none"}

    def log_rows(self):
        """The rows as saved in the roll log (plain values only). Damage totals repeated while a roll is talked over
        ("23… 21… 21 damage?") count once: only the last total said for that roll and character is added up."""
        last_damage = {}
        for x in self.all_rows:
            if (x["type"] == "roll" and x["kind"] == "damage" and damage_total(x["result"]) is not None
                    and x["outcome"] != "ignore"):
                last_damage[(x["event"]["start"], x["character"] or x["player"])] = x["key"]
        cancelled = self._cancelled()
        dm_row = {}   # one counted row per DM attack: the one you set, else the last number said
        for x in self.all_rows:
            if x["type"] == "roll" and x["dm_attack"] and x["kind"] == "roll":
                st, best = x["event"]["start"], dm_row.get(x["event"]["start"])
                score = lambda y: (y["manual"], y["result"] is not None)
                if best is None or score(x) >= score(best):
                    dm_row[st] = x
        dm_row = {st: x["key"] for st, x in dm_row.items()}
        rows = []
        for x in self.all_rows:
            if x["type"] == "taken":
                rows.append({"kind": "taken", "start": x["start"], "amount": x["taken"]["amount"], "target": x["target"],
                             "text": x["taken"]["text"]})
                continue
            e, r = x["event"], x["result"]
            if not r and not x["character"] and not x["player"] and not x["dm_attack"] and x["outcome"] != "none":
                continue
            rows.append({"kind": x["kind"], "start": x["start"], "check": check_label(e["check"]), "check_kind": e["check"][0],
                         "player": x["player"], "character": x["character"],
                         "role": (self.ctx.role(x["sid"]) if x["sid"] else "") or ("DM" if (x["character"] or x["player"]) in self._dm_names() else ""),
                         "outcome": x["outcome"] if x["kind"] == "roll" else "", "outcome_source": "you" if x["manual"] else "guessed",
                         "nat": (r or {}).get("nat"), "nat_dice": (r or {}).get("nat_dice") or 1, "total": (r or {}).get("total"), "adv": x.get("adv", ""),
                         "damage": damage_total(r) if r else None, "note": x["odd"], "text": (r or {}).get("text") or e["ask_text"],
                         "shield": bool(e.get("shield")), "context": (e.get("context") or "")[:300]})
            if x["kind"] == "damage" and rows[-1]["damage"] is not None and last_damage.get((e["start"], x["character"] or x["player"])) != x["key"]:
                rows[-1]["kind"] = "damage_repeat"   # an earlier figure for the same roll: shown, not added up
            if x["dm_attack"]:
                rows[-1]["role"] = ""
                rows[-1]["kind"] = ("dm_attack" if dm_row.get(e["start"]) == x["key"] else "dm_attack_repeat") if x["kind"] == "roll" else "dm_damage"
                rows[-1]["outcome"] = x["outcome"] if x["kind"] == "roll" else ""
            if e["start"] in cancelled:
                rows[-1]["kind"] = "cancelled"
                rows[-1]["reason"] = cancelled[e["start"]]
            if x["outcome"] == "ignore":
                rows[-1]["kind"] = "ignored"   # v2.44: Ignore this number: shown, never counted
        return rows

    def _save_log(self):
        try:
            self.ctx.save_log(self.log_rows())
        except Exception:
            pass

    # ---- table ----
    def _fill(self, *_):
        want, kind = self.show_filter.currentData(), self.kind.currentData()
        rows = []
        for x in self.all_rows:
            label = "Damage taken" if x["type"] == "taken" else check_label(x["event"]["check"])
            if kind and label != kind:
                continue
            r = x.get("result")
            if want == "numbers" and x["type"] == "roll" and not r:
                continue
            if want == "damage" and not (r and r.get("kind") == "damage"):
                continue
            if want == "taken" and x["type"] != "taken":
                continue
            if want == "odd" and not (x["odd"] and not x["odd"].startswith(INFO_NOTES)):
                continue
            if want == "nat" and not (r and r.get("nat")):
                continue
            if want == "unknown" and not (x["type"] == "roll" and x["kind"] == "roll" and not x["outcome"]):
                continue
            if want == "adv" and not (x["type"] == "roll" and x.get("adv")):
                continue
            if want == "flags" and not x.get("flags"):
                continue
            if want == "nolead" and not (x["type"] == "roll" and x["event"].get("no_lead")):
                continue
            if want == "ignored" and x.get("outcome") != "ignore":
                continue
            rows.append(x)
        # v2.44: attacks with their damage and damage taken under them
        self._parent, self._children = {}, {}
        if self.group.isChecked() and want == "all" and not kind:
            rows, self._parent, self._children = self._grouped(rows)
        prev = self.shown[self.table.currentRow()] if 0 <= self.table.currentRow() < len(getattr(self, "shown", [])) else None
        self.shown = rows
        self._playing_row = -1
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        people = self.ctx.people()
        cancelled = self._cancelled()
        choices = [label for label, _c in check_choices()]
        rmap = self.ctx.row_map() if hasattr(self.ctx, "row_map") else {}   # edit id -> editor row
        for i, x in enumerate(rows):
            flags = x.get("flags") or []
            if x["type"] == "taken":
                t = x["taken"]
                values = [self.fmt_time(x["start"])[:8], "Damage taken", self.ctx.who(t["speaker"]), "", t["text"],
                          f"takes {t['amount']}", "", ""]
            else:
                e, r = x["event"], x["result"]
                note = x["odd"]
                if e["start"] in cancelled:
                    note = "No roll" + (f": {cancelled[e['start']]}" if cancelled[e["start"]] else "")
                elif x.get("outcome") == "ignore":
                    note = "Ignored: not counted" + (f" ({note})" if note else "")
                note = "; ".join([note] + [FLAG + f for f in flags]) if flags and note else (FLAG + "; ".join(flags) if flags else note)
                asker = "—" if e.get("unasked") else self.ctx.who(e["asked_by"])   # v2.43: nobody asked for it
                values = [self.fmt_time(x["start"])[:8], check_label(e["check"]) + (" ✎" if e.get("check_set") else ""), asker, "",
                          (r["text"] if r else e["ask_text"]), read_as(r) if r else "no number said",
                          x["info"].get("label") or ("" if not r else "—"), note]
            values[C_TIME] = self._time_text(x, values[C_TIME])
            line = rmap.get(x["edit_id"], -1)
            values += ["", "", "", "", str(line + 1) if line is not None and line >= 0 else ""]   # Adv, Pass/fail, Listen, Used, Line
            for c, v in enumerate(values):
                if c in (C_ADV, C_OUT, C_PLAY, C_USED):
                    self.table.setItem(i, c, QTableWidgetItem(""))
                    continue
                item = QTableWidgetItem(str(v))
                if c == C_NOTE and (x["odd"] or flags):
                    color = Qt.red if (x["odd"] and not x["odd"].startswith(INFO_NOTES)) else (Qt.darkYellow if x["odd"] else QColor("#3d8bfd"))
                    item.setForeground(color)
                    if flags:
                        item.setToolTip("⚑ = something to check by listening. Nothing is changed or left out because of it.")
                if c == C_SHEET and x.get("sheet_note"):
                    item.setToolTip(x["sheet_note"])
                if c == C_LINE:
                    item.setToolTip("This line's number in the editor (it follows added and split lines). Double-click a row to go there.")
                if x["type"] == "roll" and not x["result"] and c in (C_SAID, C_READ):
                    item.setForeground(Qt.gray)
                if x.get("outcome") == "ignore" and c in (C_SAID, C_READ):
                    f = item.font(); f.setStrikeOut(True); item.setFont(f)
                if c == C_TIME and x["key"] in self._children:   # v2.46: an attack with rows under it stands out
                    f = item.font(); f.setBold(True); item.setFont(f)
                    item.setToolTip("Click the time to open or close the rows under this attack (its damage and damage taken).")
                self.table.setItem(i, c, item)
            # Who: a list, so a wrong guess (or a line given to the wrong speaker) can be fixed
            who = QComboBox(); who.setEditable(True)
            current = x["target"] if x["type"] == "taken" else (x["character"] or x["player"])
            who.addItem(current or "")
            for p in people:
                if p != current:
                    who.addItem(p)
            who.setToolTip("Who rolled (or who took the damage). Change it if the guess or the speaker is wrong; it is kept.")
            who.currentTextChanged.connect(lambda text, key=x["key"]: self._set_who(key, text))
            _set_widget(self.table, i, C_WHO, who)
            first = x["type"] == "roll" and x["key"] == x["key0"]
            # v2.44: Asked for can be set, including which ability a save was. v2.50: on every row of the roll (it was only on
            # the first, so a later row had no list); it is kept for the whole roll
            if x["type"] == "roll" and not x["dm_attack"]:
                ask = QComboBox()
                label = check_label(x["event"]["check"])
                if label not in choices:
                    ask.addItem(label)
                ask.addItems(choices)
                ask.setCurrentText(label)
                ask.setToolTip("What was rolled. Set it if the guess is wrong (for example a Charisma saving throw); it is kept, "
                               "and the sheet and the stats use it." + ("\nSet by you." if x["event"].get("check_set") else ""))
                if x["event"].get("check_set"):
                    ask.setStyleSheet("QComboBox{font-weight:bold;}")
                ask.currentTextChanged.connect(lambda text, key=x["key0"]: self._set_field(key, "check", text))
                _set_widget(self.table, i, C_CHECK, ask)
                self.table.item(i, C_CHECK).setText("")   # the list shows it (the text under it showed twice)
            else:
                _set_widget(self.table, i, C_CHECK, None)
            # v2.44: Used: the weapon or spell, instead of the guess from what was said
            summary = x.get("summary") or {}
            if first and not x["dm_attack"] and summary and (x["kind"] == "damage" or x["event"]["check"][0] in ("attack", "spell_attack", "unknown", "damage")):
                used = QComboBox()
                used.addItem("(guess)", "")
                for name in [w["name"] for w in summary.get("weapons") or []] + list(summary.get("spells") or []):
                    if used.findData(name) < 0:
                        used.addItem(name, name)
                if x["used"] and used.findData(x["used"]) < 0:
                    used.addItem(x["used"], x["used"])
                used.setCurrentIndex(max(0, used.findData(x["used"])))
                used.setToolTip("The weapon or spell used. (guess) = worked out from what was said. A choice is kept and "
                                "the Sheet and Note use only that weapon or spell.")
                used.currentIndexChanged.connect(lambda _i, key=x["key0"], cb=used: self._set_field(key, "used", cb.currentData() or ""))
                _set_widget(self.table, i, C_USED, used)
            else:
                _set_widget(self.table, i, C_USED, None)
            if x["type"] == "roll" and x["kind"] in ("roll", "damage"):
                combo = QComboBox()
                for label, value in (DM_OUTCOMES if x["dm_attack"] and x["kind"] == "roll" else
                                     (OUTCOMES if x["kind"] == "roll" else DAMAGE_OUTCOMES)):
                    combo.addItem(label, value)
                combo.setCurrentIndex(max(0, combo.findData(x["outcome"])))
                if x["kind"] == "roll":
                    tip = (f"Guessed from: “{x['evidence']}”" if x["auto"] and not x["manual"] else
                           ("Set by you" if x["manual"] else "Nothing said that shows it: set it yourself if you know"))
                    tip += ("\nHit / Miss: whether the DM's attack hit this character." if x["dm_attack"] else "")
                    tip += "\nNo roll: the roll was asked for or expected but never made (it is left out of the stats; you can give a reason)."
                else:
                    tip = "Count: this damage is added to the stats."
                tip += "\nIgnore this number: shown, but never counted (for example the same total said again)."
                combo.setToolTip(tip)
                if x["auto"] and not x["manual"] if x["kind"] == "roll" else False:
                    combo.setStyleSheet("QComboBox{font-style:italic;}")
                combo.currentIndexChanged.connect(lambda _i, key=x["key"], cb=combo: self._set_outcome(key, cb.currentData()))
                _set_widget(self.table, i, C_OUT, combo)
            else:
                _set_widget(self.table, i, C_OUT, None)
            if x["type"] == "roll" and x["kind"] == "roll" and not x.get("dm_attack"):
                adv = QComboBox()
                for label, value in ADV:
                    adv.addItem(label, value)
                adv.setCurrentIndex(max(0, adv.findData(x.get("adv", ""))))
                guessed = x.get("adv_auto") and not x.get("adv_manual")
                adv.setToolTip((f"Guessed from: “{x['adv_evidence']}”" if guessed else ("Set by you" if x.get("adv_manual") else
                                "Nothing said about advantage, so a normal (straight) roll: set it yourself if you know")) +
                               "\nAdvantage / disadvantage for the stats; kept when you change it.")
                if guessed:
                    adv.setStyleSheet("QComboBox{font-style:italic;}")
                adv.currentIndexChanged.connect(lambda _i, key=x["key"], cb=adv: self._set_adv(key, cb.currentData()))
                _set_widget(self.table, i, C_ADV, adv)
            else:
                _set_widget(self.table, i, C_ADV, None)
            _set_widget(self.table, i, C_PLAY, self._listen_button(i))
        self._fit_columns()
        # v2.43: stay on the row you were working on (or the nearest one in time) when the filter changes,
        # and fill the lines panel once (it was filled twice, once by the selection signal and once here)
        target = 0
        if prev is not None and rows:
            same = next((i for i, x in enumerate(rows) if x["key"] == prev["key"]), None)
            target = same if same is not None else min(range(len(rows)), key=lambda i: abs(rows[i]["start"] - prev["start"]))
            if rows[target]["key"] in self._parent:   # v2.44: open the group the row is in
                self._expanded.add(self._parent[rows[target]["key"]])
        self._apply_groups()
        if rows:
            self.table.setCurrentCell(target, C_TIME)
        self.table.blockSignals(False)
        self.table.resizeRowsToContents()
        if rows:
            self.table.scrollToItem(self.table.item(target, C_TIME), QAbstractItemView.PositionAtCenter)
            self._show_context(target)
        else:
            self._show_context(-1)
        rolls = [x for x in self.all_rows if x["type"] == "roll" and x["kind"] == "roll" and x["outcome"] not in ("none", "ignore")]
        n_odd = sum(1 for x in self.all_rows if x["odd"] and not x["odd"].startswith(INFO_NOTES))
        n_flags = sum(1 for x in self.all_rows if x.get("flags"))
        self.status.setText(f"{len(rows)} shown · {len(self.events)} rolls asked or made · "
                            f"{sum(1 for x in rolls if x['outcome'] == 'pass')} passed · {sum(1 for x in rolls if x['outcome'] == 'fail')} failed · "
                            f"{sum(1 for x in rolls if not x['outcome'])} not known · {n_odd} possible mix-up(s) · "
                            f"{n_flags} flag(s) to check · {len(self.taken)} damage-taken line(s)")
        notes = sorted({f"{x['character']}: {x['sheet_note']}" for x in self.all_rows
                        if x["type"] == "roll" and x["character"] and x.get("sheet_note")})
        missing = sorted({x["player"] for x in self.all_rows if x["type"] == "roll" and x["result"] and not x["character"]
                          and x["player"] and x.get("sheet_note") != "DM"})
        text = " · ".join(notes)
        if missing:
            text += ("\n" if text else "") + "No character set for: " + ", ".join(missing) + " (Players and characters…)."
        self.sheets.setText(text)

    # ---- v2.44: grouping ----
    def _grouped(self, rows):
        """-> (rows with each attack followed by its rows, {child key: parent key}, {parent key: [children]})."""
        first = {}
        for x in rows:
            if x["type"] == "roll":
                first.setdefault(x["event"]["start"], x)
        parent = {}
        dm_firsts = [x for x in first.values() if x["dm_attack"]]
        for x in rows:
            if x["type"] == "roll":
                e = x["event"]
                if e["check"][0] in ("attack", "spell_attack", "unknown", "dm_attack") and first[e["start"]] is not x:
                    parent[x["key"]] = first[e["start"]]["key"]
                elif e["start"] in getattr(self, "links", {}):
                    p = first.get(self.links[e["start"]]["start"])
                    if p is not None:
                        parent[x["key"]] = p["key"]
            else:   # damage taken just after a DM attack on that character
                cands = [d for d in dm_firsts if d["event"]["start"] <= x["start"] <= d["event"]["end"] + 30
                         and (not x["target"] or x["target"] == (d["character"] or d["player"]))]
                if cands:
                    parent[x["key"]] = cands[-1]["key"]
        children = {}
        for x in rows:
            if x["key"] in parent:
                children.setdefault(parent[x["key"]], []).append(x)
        ordered = []
        for x in rows:
            if x["key"] in parent:
                continue
            ordered.append(x)
            ordered.extend(sorted(children.get(x["key"], []), key=lambda y: y["start"]))
        return ordered, parent, children

    def _time_text(self, x, time):
        if x["key"] in getattr(self, "_children", {}):
            n = len(self._children[x["key"]])
            end = max([c["end"] for c in self._children[x["key"]]] + [x.get("end") or x["start"]])
            return (("▾ " if x["key"] in self._expanded else "▸ ") + time + f"\n     {n} more row{'s' if n != 1 else ''}"
                    + f"\n     to {self.fmt_time(end)[:8]}")   # v2.50: when the attack's rows end
        if x["key"] in getattr(self, "_parent", {}):
            return "        ↳ " + time
        return time

    def _apply_groups(self):
        for i, x in enumerate(self.shown):
            p = self._parent.get(x["key"])
            self.table.setRowHidden(i, bool(p) and p not in self._expanded)
            if x["key"] in self._children and self.table.item(i, C_TIME) is not None:
                self.table.item(i, C_TIME).setText(self._time_text(x, self.fmt_time(x["start"])[:8]))

    def _cell_clicked(self, r, c):
        if c != C_TIME or not (0 <= r < len(self.shown)) or self.shown[r]["key"] not in self._children:
            return
        key = self.shown[r]["key"]
        self._expanded.symmetric_difference_update({key})
        self._apply_groups()
        self.table.resizeRowsToContents()

    def _expand_all(self, on):
        self._expanded = set(self._children) if on else set()
        self._apply_groups()

    def _set_field(self, key, field, value):
        """v2.44: Asked for / Used you set: kept, then the rows are worked out again."""
        self.ctx.set_override(key, field, value)
        self._reload()

    def _fit_columns(self):
        """Fit Asked for / Read as / Sheet to their text, at most 150-180 px each (drag a header edge to widen one)."""
        for c, cap in ((C_CHECK, 150), (C_READ, 160), (C_SHEET, 180)):
            self.table.resizeColumnToContents(c)
            if self.table.columnWidth(c) > cap:
                self.table.setColumnWidth(c, cap)

    # v2.43: row heights were worked out before the window had its real size (with "What was said" still narrow,
    # so the text wrapped word by word and rows were very tall); they are redone once shown and after a resize
    def _refit_rows(self):
        from PySide6.QtCore import QTimer
        if not hasattr(self, "_row_timer"):
            self._row_timer = QTimer(self); self._row_timer.setSingleShot(True); self._row_timer.setInterval(120)
            self._row_timer.timeout.connect(lambda: (self.table.resizeRowsToContents(), self.ctx_table.resizeRowsToContents()))
        self._row_timer.start()

    def changeEvent(self, event):
        """v2.45: coming back to this window after editing the transcript finds the rolls again (keeping your place)."""
        super().changeEvent(event)
        from PySide6.QtCore import QEvent
        if event.type() != QEvent.ActivationChange or not hasattr(self.ctx, "version"):
            return
        if not self.isActiveWindow():
            # v2.46: a line still being typed in the lines panel is kept as a change when you click away (it was lost
            # when the rolls were found again, without a word)
            editor = self.focusWidget()
            if self.ctx_table.state() == QAbstractItemView.EditingState and editor is not None and self.ctx_table.isAncestorOf(editor):
                from PySide6.QtWidgets import QAbstractItemDelegate
                self.ctx_table.commitData(editor)
                self.ctx_table.closeEditor(editor, QAbstractItemDelegate.NoHint)
            return
        if self.ctx.version() == getattr(self, "_seen_version", 0):
            return
        if self._edits or self._speaker_edits:
            msg = ("The transcript changed in the editor, and you have changes in the lines panel below that are not applied yet. "
                   "Press Apply changes (or select another row to drop them), then ↻ Look again.")
            self.status.setText(msg); self.banner.setText("⚠ " + msg); self.banner.show()
            return
        try:
            self._reload()
        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        self._refit_rows()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refit_rows()

    def _double_clicked(self, r, c):
        """v2.46: double-click What was said to correct that line (in the editor, one Ctrl+Z undoes it); elsewhere go to it."""
        if c == C_SAID and 0 <= r < len(self.shown) and getattr(self.ctx, "edit_line", None):
            if self._edits or self._speaker_edits:
                QMessageBox.information(self, "Edit line", "Apply or drop your changes in the lines panel first."); return
            if self.ctx.edit_line(self.shown[r]["edit_id"]):
                self._reload()
            return
        self._go(r)

    def _ctx_menu(self, pos):
        from PySide6.QtWidgets import QMenu
        r = self.ctx_table.rowAt(pos.y())
        if not (0 <= r < len(getattr(self, "ctx_lines", []))):
            return
        seg = self.ctx_lines[r]
        menu = QMenu(self)
        add = menu.addAction("Add roll here…")
        play = menu.addAction("▶ Play this line")
        chosen = menu.exec(self.ctx_table.viewport().mapToGlobal(pos))
        if chosen is add:
            self._add_roll(seg)
        elif chosen is play and self.play:
            self.play(float(seg["start"]), float(seg["end"]))

    def _table_menu(self, pos):
        """v2.50: right-click a row: add a roll at its line, set the character's AC for this session, play, go to the line."""
        from PySide6.QtWidgets import QMenu
        r = self.table.rowAt(pos.y())
        if not (0 <= r < len(getattr(self, "shown", []))):
            return
        x = self.shown[r]
        seg = next((s for s in self.segments if s.get("_edit_id") == x["edit_id"]), None)
        who = x.get("character") or x.get("target") or ""
        menu = QMenu(self)
        add = menu.addAction("Add roll here…") if seg else None
        ac = menu.addAction(f"Set {who}'s AC for this session…") if who else None
        menu.addSeparator()
        play = menu.addAction("▶ Play this row")
        go = menu.addAction("Go to line in editor")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is add:
            self._add_roll(seg)
        elif chosen is ac:
            self._set_ac(who)
        elif chosen is play:
            self._play_row_audio(r)
        elif chosen is go:
            self._go(r)

    def _set_ac(self, who):
        current = self._ac_set(who) or 0
        value, ok = QInputDialog.getInt(self, "Set AC", f"{who}'s AC in this session (for the DM's attacks; 0 = use the sheet "
                                        "and what was said). Shield is still added when it is cast.", current, 0, 40)
        if not ok:
            return
        self.ctx.set_override(f"ac|{who}", "ac", str(value) if value else "")
        self._reload()

    def _add_roll(self, seg):
        """v2.46: a roll the finder missed (the DM's 22 at 26:04, a number said off-mic), added at this line."""
        import json
        dlg = AddRollDialog(self.ctx.people(), seg.get("text", ""), self.fmt_time(float(seg["start"]))[:8], self)
        if dlg.exec() != QDialog.Accepted:
            return
        a = dlg.values()
        a.update(start=float(seg["start"]), end=float(seg["end"]), edit_id=seg.get("_edit_id"), text=seg.get("text", ""))
        n = 1 + max([int(k.split("|")[1]) for (k, f) in self.overrides if f == "added" and k.startswith("added|")
                     and k.split("|")[1].isdigit()] or [0])
        self.ctx.set_override(f"added|{n}", "added", json.dumps(a, ensure_ascii=False))
        self._reload()

    def _your_changes(self):
        """v2.46: every choice made in this window, newest first, with Undo."""
        times = self.ctx.override_times() if hasattr(self.ctx, "override_times") else {}
        by_key = {}
        for x in getattr(self, "all_rows", []):
            by_key.setdefault(x["key"], x)
        items = []
        for (key, field), value in self.overrides.items():
            if field == "reason":
                continue
            x = by_key.get(key)
            if field == "added":
                x = next((y for y in self.all_rows if y.get("type") == "roll" and y["event"].get("added_key") == key), None)
            item = {"key": key, "field": field, "value": value, "row": x, "when": times.get((key, field), "")}
            if x is None and field != "added" and not key.startswith("ac|"):
                # v2.50: the roll is found differently now (2:57:23's damage is now under the 2:56:39 attack), so this
                # choice no longer applies to any row: offer the row now at that time
                t = _key_time(key)
                near = sorted((y for y in self.all_rows if y.get("type") == "roll" and abs(y["start"] - t) <= 30),
                              key=lambda y: abs(y["start"] - t))
                if near:
                    item["move_to"] = near[0]["key0"] if field in ("check", "used") else near[0]["key"]
                    item["move_row"] = near[0]
            items.append(item)
        if not items:
            QMessageBox.information(self, "Your changes", "You have not changed anything in this transcript's roll finder."); return
        dlg = YourChangesDialog(items, self.fmt_time, self)
        dlg.exec()
        for it in dlg.moved():   # v2.50: re-attach a choice to the row it belongs to now
            self.ctx.set_override(it["move_to"], it["field"], it["value"])
            self.ctx.set_override(it["key"], it["field"], "")
        undo = dlg.undone()
        if not undo:
            if dlg.moved():
                self._reload()
            return
        for key, field in undo:
            self.ctx.set_override(key, field, "")
            if field == "outcome":
                self.ctx.set_override(key, "reason", "")
        self._reload()

    def _toggle_row(self, r):
        """v2.43: ▶ on a row also selects it, so the lines panel shows the row you are listening to."""
        if 0 <= r < self.table.rowCount() and r != self.table.currentRow():
            self.table.setCurrentCell(r, C_TIME)
        _RowPlayback._toggle_row(self, r)

    def _set_outcome(self, key, value):
        was_ignored = any(x["key"] == key and x.get("outcome") == "ignore" for x in self.all_rows)
        reason = ""
        if value == "none":
            old = next((x.get("reason", "") for x in self.all_rows if x["key"] == key), "")
            reason, ok = QInputDialog.getText(self, "No roll", "Why was there no roll? (optional, e.g. \"Sam didn't call it\")", text=old)
            reason = reason.strip() if ok else old
        self.ctx.set_override(key, "outcome", value)
        self.ctx.set_override(key, "reason", reason if value == "none" else "")
        self.overrides = self.ctx.overrides()
        for x in self.all_rows:
            if x["key"] == key:
                x["outcome"], x["manual"], x["reason"] = value, bool(value), reason if value == "none" else ""
        self._fill()   # v2.44: refreshes the Note of every row of this roll (No roll / Ignored), keeping your place
        self._save_log()

    def _set_adv(self, key, value):
        self.ctx.set_override(key, "adv", value or "-")   # "-" keeps "none" apart from "not set by you"
        self.overrides = self.ctx.overrides()
        for x in self.all_rows:
            if x["key"] == key:
                x["adv"], x["adv_manual"] = value or "", True
        self._save_log()

    def _set_who(self, key, text):
        self.ctx.set_override(key, "who", text.strip())
        self.overrides = self.ctx.overrides()
        for x in self.all_rows:
            if x["key"] == key:
                if x["type"] == "taken":
                    x["target"] = text.strip()
                else:
                    x["character"] = text.strip()
        self._save_log()

    # ---- context panel ----
    def _extend_context(self, seconds):
        if seconds < 0:
            self._ctx_before += -seconds
        else:
            self._ctx_after += seconds
        self._show_context(self.table.currentRow(), keep=True)

    def _show_context(self, i, keep=False):
        if not keep or getattr(self, "_ctx_row", None) != i:
            self._ctx_before, self._ctx_after = 10, 30
        self._ctx_row = i
        self._edits = {}; self.apply_btn.setEnabled(False)
        if not (0 <= i < len(getattr(self, "shown", []))):
            self.ctx_table.setRowCount(0); return
        x = self.shown[i]
        start = x["event"]["start"] if x["type"] == "roll" else x["start"]
        end = max(x["end"], start) + self._ctx_after
        lines = [s for s in self.segments if start - self._ctx_before <= float(s["start"]) <= end][:400]
        self.ctx_lines = lines
        self._speaker_edits = {}
        speakers = self.ctx.speakers() if hasattr(self.ctx, "speakers") else []
        self.ctx_table.blockSignals(True)
        self.ctx_table.setRowCount(len(lines))
        for r, s in enumerate(lines):
            t = QTableWidgetItem(self.fmt_time(float(s["start"]))[:8]); t.setFlags(t.flags() & ~Qt.ItemIsEditable)
            # v2.43: the name is left out of the cell when the speaker list sits on it (both showed: "JJJ")
            w = QTableWidgetItem("" if speakers else self.ctx.who(s.get("speaker_id", ""))); w.setFlags(w.flags() & ~Qt.ItemIsEditable)
            if speakers:
                combo = QComboBox(); sid = s.get("speaker_id", "") or ""
                combo.addItem("Unassigned", "")
                for opt, label in speakers:
                    combo.addItem(label, opt)
                if combo.findData(sid) < 0:
                    combo.addItem(self.ctx.who(sid), sid)
                combo.setCurrentIndex(max(0, combo.findData(sid)))
                combo.setToolTip("Who said this line. Change it if the speaker is wrong, then Apply changes.")
                combo.currentIndexChanged.connect(lambda _i, cb=combo, seg=s: self._ctx_speaker(seg, cb.currentData()))
                _set_widget(self.ctx_table, r, 1, combo)
            text = QTableWidgetItem(s.get("text", ""))
            if s.get("_edit_id") == x["edit_id"]:
                for it in (t, w, text):
                    it.setBackground(Qt.darkCyan)
            if s.get("speaker_confirmed"):
                text.setToolTip("Reviewed segment: the speaker was checked. You can still correct the words.")
            self.ctx_table.setItem(r, 0, t); self.ctx_table.setItem(r, 1, w); self.ctx_table.setItem(r, 2, text)
            btn = QPushButton("▶"); btn.setStyleSheet("QPushButton{padding:1px 8px;}")
            btn.clicked.connect(lambda _c=False, seg=s: self.play and self.play(float(seg["start"]), float(seg["end"])))
            _set_widget(self.ctx_table, r, 3, btn)
        self.ctx_table.blockSignals(False)
        self.ctx_table.resizeRowsToContents()

    def _ctx_speaker(self, seg, sid):
        if (sid or "") != (seg.get("speaker_id", "") or ""):
            self._speaker_edits[seg["_edit_id"]] = sid or ""
        else:
            self._speaker_edits.pop(seg["_edit_id"], None)
        self.apply_btn.setEnabled(bool(self._edits or self._speaker_edits))

    def _ctx_edited(self, item):
        if item.column() != 2 or not (0 <= item.row() < len(self.ctx_lines)):
            return
        seg = self.ctx_lines[item.row()]
        new = item.text().strip()
        if new and new != str(seg.get("text", "")).strip():
            self._edits[seg["_edit_id"]] = new
        else:
            self._edits.pop(seg["_edit_id"], None)
        self.apply_btn.setEnabled(bool(self._edits or self._speaker_edits))

    def _apply_texts(self):
        if not (self._edits or self._speaker_edits):
            return
        reviewed = [s for s in getattr(self, "ctx_lines", []) if s.get("speaker_confirmed") and s["_edit_id"] in self._speaker_edits]
        if reviewed and QMessageBox.question(
                self, "Reviewed lines", f"{len(reviewed)} of these lines were already Reviewed (speaker checked). "
                "Change their speaker anyway?", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            if self._speaker_edits:
                n = self.ctx.apply_texts(dict(self._edits), speakers=dict(self._speaker_edits))
            else:
                n = self.ctx.apply_texts(dict(self._edits))
        except ValueError as exc:
            QMessageBox.warning(self, "Roll finder", str(exc)); return
        self._edits = {}; self._speaker_edits = {}; self.apply_btn.setEnabled(False)
        self._reload()   # stays on the same row (v2.43)
        QMessageBox.information(self, "Roll finder", f"Changed {n} line(s) in the transcript (Ctrl+Z in the editor undoes this). "
                                "Rolls were looked for again.")

    def _play_on(self):
        i = self.table.currentRow()
        if self.play and 0 <= i < len(self.shown):
            a = self.shown[i]["event"]["start"] if self.shown[i]["type"] == "roll" else self.shown[i]["start"]
            self._playing_row = i
            self.play(a, a + 30)

    # ---- playback / navigation ----
    def _play_row_audio(self, i):
        if self.play and 0 <= i < len(self.shown):
            self.play(self.shown[i]["start"], self.shown[i]["end"])

    def _go(self, i):
        if self.ctx.go_to and 0 <= i < len(self.shown):
            self.ctx.go_to(self.shown[i]["edit_id"])

    def _players(self):
        if self.ctx.edit_players and self.ctx.edit_players():
            self._reload()

    def _summary(self):
        SessionSummaryDialog(session_stats(self.log_rows()), self, rows=self.log_rows(), fmt_time=self.fmt_time,
                             go=self._go_time).exec()

    def _go_time(self, start, session=""):
        """v2.50: go to the row (and the editor line) at this time in this session."""
        if session:
            return False
        rows = [x for x in self.all_rows if abs(x["start"] - start) < 0.6]
        if not rows or not self.ctx.go_to:
            return False
        self.ctx.go_to(rows[0]["edit_id"])
        if rows[0] in self.shown:
            self.table.setCurrentCell(self.shown.index(rows[0]), C_TIME)
        return True

    def _campaign_summary(self):
        self._save_log()
        logs = self.ctx.campaign_logs()
        rows = [dict(r, session=name) for _run, name, rr in logs for r in rr]
        names = ", ".join(n for _run, n, _rr in logs)
        SessionSummaryDialog(session_stats(rows), self, title=f"Campaign summary ({len(logs)} session(s): {names})",
                             rows=rows, fmt_time=self.fmt_time).exec()

    def done(self, result):
        self._save_log()
        super().done(result)


SUMMARY_COLS = [("rolls", "Rolls"), ("pass", "Passed"), ("fail", "Failed"), ("unknown", "Not known"), ("nat20", "Nat 20"),
                ("nat1", "Nat 1"), ("attacks", "Attacks"), ("hits", "Hits"), ("misses", "Misses"),
                ("damage_dealt", "Damage dealt (said)"), ("damage_rolls_no_total", "Damage rolls without a total"),
                ("damage_taken", "Damage taken"), ("attacked", "Attacked by DM"), ("hit_by_dm", "Hit by DM"),
                ("missed_by_dm", "DM missed"), ("no_roll", "No roll"), ("advantage", "Advantage"), ("disadvantage", "Disadvantage")]


class SessionSummaryDialog(QDialog):
    def __init__(self, stats, parent=None, title="Session summary", rows=None, fmt_time=None, go=None):
        super().__init__(parent)
        self.rows, self.fmt_time, self.go = rows or [], fmt_time or (lambda s: f"{s:.0f} s"), go
        self.setWindowTitle(title)
        self.resize(1000, 360)
        layout = QVBoxLayout(self)
        tip = QLabel("Counted from the roll finder rows (your Pass / fail and Who corrections included). Damage dealt only adds up "
                     "totals that were said; dice without a total are counted separately. The DM's attacks are counted under the "
                     "character attacked and in the DM row (with the DM's own rolls). Rolls you marked No roll are only counted under No roll. "
                     "Double-click a number to see the rolls behind it.")
        tip.setWordWrap(True); layout.addWidget(tip)
        moments = notable_moments(self.rows)
        table = QTableWidget(len(stats), len(SUMMARY_COLS) + 1)
        table.setHorizontalHeaderLabels(["Character"] + [label for _k, label in SUMMARY_COLS])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.cellDoubleClicked.connect(lambda r, c: self._details(table, r, c))
        self.table = table
        for r, (who, s) in enumerate(sorted(stats.items(), key=lambda kv: kv[0].casefold())):
            table.setItem(r, 0, QTableWidgetItem(who))
            for c, (k, _label) in enumerate(SUMMARY_COLS, 1):
                table.setItem(r, c, QTableWidgetItem(str(s.get(k, 0))))
        layout.addWidget(table, 1)
        if moments:   # v2.46: the rare things worth remembering
            box = QGroupBox(f"Notable moments ({len(moments)})")
            bl = QVBoxLayout(box)
            from PySide6.QtWidgets import QListWidget
            lst = QListWidget()
            for m in moments:
                lst.addItem((f"{m['session']} · " if m.get("session") else "") + f"{self.fmt_time(float(m['start']))[:8]}  "
                            f"{m['who']}: {m['what']}")
            lst.setMaximumHeight(140)
            if go:   # v2.50: double-click a moment to go to it in the editor (this session's only)
                lst.setToolTip("Double-click to go to this moment in the editor (moments from this session).")
                lst.itemDoubleClicked.connect(lambda it, ms=moments, l=lst: self.go(float(ms[l.row(it)]["start"]),
                                                                                   ms[l.row(it)].get("session", "")))
            bl.addWidget(lst); layout.addWidget(box)
        buttons = QDialogButtonBox(QDialogButtonBox.Close); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def _details(self, table, r, c):
        """v2.44: the rolls behind one number."""
        if c < 1 or not self.rows:
            return
        who, (key, label) = table.item(r, 0).text(), SUMMARY_COLS[c - 1]
        found = stat_rows(self.rows, who, key)
        dlg = QDialog(self); dlg.setWindowTitle(f"{who}: {label} ({len(found)})"); dlg.resize(1000, 420)
        lay = QVBoxLayout(dlg)
        many = any(x.get("session") for x in found)
        heads = (["Session"] if many else []) + ["Time", "Roll", "Number", "Pass / fail", "What was said", "Note"]
        t2 = QTableWidget(len(found), len(heads)); t2.setHorizontalHeaderLabels(heads)
        t2.setEditTriggers(QAbstractItemView.NoEditTriggers); t2.setWordWrap(True)
        h = t2.horizontalHeader()
        for i in range(len(heads)):
            h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(len(heads) - 2, QHeaderView.Stretch)
        for i, x in enumerate(found):
            number = x.get("damage") if x.get("damage") is not None else (x.get("amount") if x.get("kind") == "taken" else x.get("total"))
            if x.get("nat"):
                number = f"natural {x['nat']}" + (" ×2" if (x.get("nat_dice") or 1) > 1 else "")
            vals = ([x.get("session", "")] if many else []) + [
                self.fmt_time(float(x.get("start") or 0))[:8], x.get("check") or ("Damage taken" if x.get("kind") == "taken" else ""),
                "" if number is None else str(number), {"pass": "Pass / hit", "fail": "Fail / miss"}.get(x.get("outcome"), ""),
                x.get("text", ""), x.get("note") or x.get("reason") or ""]
            for j, v in enumerate(vals):
                t2.setItem(i, j, QTableWidgetItem(str(v)))
        t2.resizeRowsToContents()
        lay.addWidget(QLabel("Find a row in the roll finder by its time to listen to it or change it."))
        lay.addWidget(t2, 1)
        b = QDialogButtonBox(QDialogButtonBox.Close); b.rejected.connect(dlg.reject); lay.addWidget(b)
        dlg.exec()


class AddRollDialog(QDialog):
    """v2.46: a roll the finder missed, added at one line."""

    def __init__(self, people, line_text, time_text, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QFormLayout, QSpinBox, QLineEdit
        self.setWindowTitle("Add roll here")
        lay = QVBoxLayout(self)
        about = QLabel(f"At {time_text}: “{line_text[:200]}”\n\nThe roll is added to the list and the stats, marked “added by you”. "
                       "Your changes… removes it again.")
        about.setWordWrap(True); lay.addWidget(about)
        form = QFormLayout()
        self.kind = QComboBox(); self.kind.addItems(["DM attack"] + [label for label, _c in check_choices()])
        self.kind.setToolTip("DM attack: the DM attacked the character in Who (the number is the DM's total).")
        self.number = QSpinBox(); self.number.setRange(0, 99); self.number.setSpecialValueText("not said")
        self.nat = QComboBox(); self.nat.addItem("—", None); self.nat.addItem("natural 20", 20); self.nat.addItem("natural 1", 1)
        self.who = QComboBox(); self.who.setEditable(True); self.who.addItem(""); self.who.addItems(list(people))
        self.who.setToolTip("Who rolled, or for a DM attack, who was attacked.")
        self.note = QLineEdit(); self.note.setPlaceholderText("optional, e.g. \"cast Shield: AC 21\"")
        for label, w in (("Roll", self.kind), ("Number (total)", self.number), ("Natural", self.nat), ("Who", self.who), ("Note", self.note)):
            form.addRow(label, w)
        lay.addLayout(form)
        b = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        b.accepted.connect(self.accept); b.rejected.connect(self.reject); lay.addWidget(b)

    def values(self):
        return {"check": self.kind.currentText(), "total": self.number.value() or None, "nat": self.nat.currentData(),
                "who": self.who.currentText().strip(), "note": self.note.text().strip()}


FIELD_NAMES = {"outcome": "Pass / fail", "who": "Who", "adv": "Adv / Dis", "check": "Asked for", "used": "Used", "added": "Added roll",
               "ac": "AC"}


class YourChangesDialog(QDialog):
    """v2.46: the choices you made in the roll finder, newest first; tick and Undo the ones made by accident."""

    def __init__(self, items, fmt_time, parent=None):
        super().__init__(parent)
        import json
        self.items = sorted(items, key=lambda i: (i["when"] or "", -_key_time(i["key"])), reverse=True)
        self.setWindowTitle(f"Your changes ({len(items)})")
        self.resize(1100, 520)
        lay = QVBoxLayout(self)
        tip = QLabel("Every choice you made in this transcript's roll finder, newest first. Tick any made by accident (a list "
                     "changed while scrolling, for example) and press Undo ticked: the row goes back to the finder's own guess. "
                     "Choices made before v2.46 have no time and are listed last, in the order of the session.")
        tip.setWordWrap(True); lay.addWidget(tip)
        heads = ["Tick", "Changed", "Roll at", "What", "Set to", "What was said"]
        self.table = QTableWidget(len(self.items), len(heads)); self.table.setHorizontalHeaderLabels(heads)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.table.setWordWrap(True)
        h = self.table.horizontalHeader()
        for c in range(len(heads)):
            h.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(5, QHeaderView.Stretch)
        labels = {v: l for l, v in OUTCOMES + DM_OUTCOMES + DAMAGE_OUTCOMES if v}
        labels.update({v: l for l, v in ADV if v})
        self.boxes = []
        for r, it in enumerate(self.items):
            box = QCheckBox(); self.table.setCellWidget(r, 0, box); self.boxes.append(box)
            x = it["row"]
            value = it["value"]
            if it["field"] == "outcome":
                value = labels.get(value, value)
            elif it["field"] == "adv":
                value = labels.get(value.strip("-"), "Normal")
            elif it["field"] == "added":
                try:
                    a = json.loads(value); value = f"{a.get('check')} {a.get('total') or ''} {a.get('who') or ''}".strip()
                except Exception:
                    pass
            when = str(it["when"] or "")[:19].replace("T", " ")
            said = (x["result"] or {}).get("text") if x and x.get("type") == "roll" and x.get("result") else (
                (x["event"]["ask_text"] if x and x.get("type") == "roll" else (x or {}).get("taken", {}).get("text", "")) if x else "")
            start = x["start"] if x else _key_time(it["key"])
            if x is None and it.get("move_row"):
                y = it["move_row"]
                said = (f"⚠ no row has this choice now: the roll is found differently. Re-attach puts it on the row at "
                        f"{fmt_time(y['start'])[:8]}: “{((y.get('result') or {}).get('text') or y['event'].get('ask_text', ''))[:80]}”")
            elif x is None and it["field"] != "added" and not it["key"].startswith("ac|"):
                said = "⚠ no row has this choice now, and none is near that time"
            if it["key"].startswith("ac|"):
                said = f"AC for {it['key'][3:]} in this session"
            for c, v in enumerate([when or "—", fmt_time(float(start))[:8], FIELD_NAMES.get(it["field"], it["field"]), value, said or ""], 1):
                self.table.setItem(r, c, QTableWidgetItem(str(v)))
        self.table.resizeRowsToContents()
        lay.addWidget(self.table, 1)
        b = QDialogButtonBox(QDialogButtonBox.Close)
        undo = b.addButton("Undo ticked…", QDialogButtonBox.ActionRole); undo.clicked.connect(self._undo)
        move = b.addButton("Re-attach ticked", QDialogButtonBox.ActionRole); move.clicked.connect(self._move)
        move.setToolTip("For choices marked ⚠: put them on the row the roll is found at now.")
        move.setEnabled(any(i.get("move_to") for i in self.items))
        b.rejected.connect(self.reject); lay.addWidget(b)
        self._undone, self._moved = [], []

    def _undo(self):
        picked = [(self.items[r]["key"], self.items[r]["field"]) for r, b in enumerate(self.boxes) if b.isChecked()]
        if not picked:
            QMessageBox.information(self, "Undo", "Tick the changes to undo first."); return
        if QMessageBox.question(self, "Undo", f"Undo {len(picked)} change(s)? Those rows go back to the finder's own guess "
                                "(an added roll is removed).", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self._undone = picked
        self.accept()

    def undone(self):
        return self._undone

    def _move(self):
        picked = [self.items[r] for r, b in enumerate(self.boxes) if b.isChecked() and self.items[r].get("move_to")]
        if not picked:
            QMessageBox.information(self, "Re-attach", "Tick the choices marked ⚠ to put them on the row the roll is found at now."); return
        self._moved = picked
        self.accept()

    def moved(self):
        return self._moved


def _key_time(key):
    try:
        return float(str(key).split("|")[1] if str(key).startswith("taken|") else str(key).split("|")[0])
    except (ValueError, IndexError):
        return 0.0


class PlayerCharactersDialog(QDialog):
    """players: list of player names; characters: names offered; current: {player: character}."""

    def __init__(self, players, characters, current, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Players and characters")
        layout = QVBoxLayout(self)
        tip = QLabel("Which character does each player play in this campaign? Characters with a D&D Beyond link (refreshed at least once) "
                     "can be compared with their sheet. Leave the DM empty.")
        tip.setWordWrap(True); layout.addWidget(tip)
        grid = QGridLayout(); self.boxes = {}
        for i, p in enumerate(players):
            box = QComboBox(); box.setEditable(True); box.addItem("")
            for c in characters:
                box.addItem(c)
            box.setCurrentText(current.get(p, ""))
            grid.addWidget(QLabel(p), i, 0); grid.addWidget(box, i, 1); self.boxes[p] = box
        layout.addLayout(grid)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def mapping(self):
        return {p: b.currentText().strip() for p, b in self.boxes.items()}
