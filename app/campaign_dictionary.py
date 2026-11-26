"""Campaign names (with other real names and common mistranscriptions) and optional D&D Beyond sheet links."""
import json, re
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QTableWidget,QTableWidgetItem,
    QPushButton,QDialogButtonBox,QHeaderView,QFileDialog,QMessageBox,QInputDialog,
    QPlainTextEdit,QComboBox,QFormLayout)
from .ddb_import import fetch_public_character

# Aliases are legitimate nicknames, shortened names or alternate names.
# Candidate transcription mishearings are intentionally not stored here;
# context-aware mishearing resolution is reserved for Phase 3.
SUGGESTED_ALIASES = {}
AMBIGUOUS = {"Hither","Yon","Order Up","Red","Juniper","Star","Ernest","Iris","Tasha"}
CATEGORIES = ["Character","NPC","Location","Item","Spell","Monster / Creature","Faction / Organisation","Deity","Region / Plane","Quest","Event","Other"]
WHISPER_HELP = ("Use in Whisper: when ticked and Campaign context is on, this spelling and its “also known as” names (never the "
                "mistranscriptions) are given to Whisper before transcribing, as a vocabulary hint, so names like “Vaelith” are spelled "
                "correctly. Untick names that are also ordinary words (Red, Star, Order Up): hinting them can make Whisper insert them "
                "where nobody said them. Unticked entries are still used by Correct names.")
AKA_HELP = ("Also known as: other real names of the same character, place or thing, separated by commas (Creeping Lyn for "
            "Endelyn Moongrave, Ernie for Ernest). They are correct names in their own right: Correct names never changes them to the "
            "main name, and fixes misspellings of them to the name itself (Creeping Linn -> Creeping Lyn).")
MIS_HELP = ("Common mistranscriptions: wrong spellings Whisper writes for this name, separated by commas (Bisquit, Biskit for "
            "Biscuit). Correct names offers to change them to the canonical spelling. They are never sent to Whisper.")
COLUMNS = ["Canonical spelling", "Also known as (other real names)", "Common mistranscriptions (wrong spellings)", "Category",
           "Source", "D&D Beyond URL (optional)", "Last refresh", "Use in Whisper"]
C_NAME, C_AKA, C_MIS, C_CAT, C_SRC, C_URL, C_REF, C_WHISPER = range(len(COLUMNS))
FIELDS = ["name", "aka", "aliases", "category", "source", "url", "refreshed"]


class CampaignDictionaryDialog(QDialog):
    def __init__(self, db, campaign_id, parent=None):
        super().__init__(parent); self.db=db; self.campaign_id=campaign_id
        self.setWindowTitle("Campaign names and terminology"); self.resize(1250,680)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self._sort_state = {}
        root=QVBoxLayout(self)
        intro=QLabel("Canonical spelling is the name as it should be written. “Also known as” holds other real names of the same "
                     "character (Creeping Lyn for Endelyn Moongrave): they are never corrected away. “Common mistranscriptions” holds "
                     "wrong spellings that should become the canonical name (Bisquit for Biscuit). Only ticked names and their "
                     "“also known as” names are sent to Whisper. Click a column title to sort by it.")
        intro.setWordWrap(True); root.addWidget(intro)
        self.table=QTableWidget(0,len(COLUMNS)); self.table.setHorizontalHeaderLabels(COLUMNS)
        header=self.table.horizontalHeader(); header.setSectionsClickable(True); header.sectionClicked.connect(self._sort_by)
        for c in (C_NAME,C_CAT,C_SRC,C_REF,C_WHISPER): header.setSectionResizeMode(c,QHeaderView.ResizeToContents)
        for c in (C_AKA,C_MIS,C_URL): header.setSectionResizeMode(c,QHeaderView.Stretch)
        self.table.horizontalHeaderItem(C_WHISPER).setToolTip(WHISPER_HELP)
        self.table.horizontalHeaderItem(C_AKA).setToolTip(AKA_HELP)
        self.table.horizontalHeaderItem(C_MIS).setToolTip(MIS_HELP)
        root.addWidget(self.table,1)
        actions=QHBoxLayout()
        for label,fn in [("Add entry",self.add_row),("Bulk import names",self.bulk_import),("Set category of selected…",self.set_category),
                         ("Make “also known as” of…",self.make_aka),("Import character sheet (PDF / JSON)",self.import_sheet),
                         ("Import D&&D Beyond URL",self.import_ddb),("Refresh selected D&&D Beyond",self.refresh_ddb),
                         ("D&&D Beyond spells && items…",self.import_spells_items),("D&&D Beyond account…",self.ddb_account),
                         ("Character history…",self.character_history),("Remove selected",self.remove_rows)]:
            b=QPushButton(label); b.clicked.connect(fn); actions.addWidget(b)
            if fn==self.make_aka:
                b.setToolTip("Move the selected entry (for example Creeping Lyn) into another entry's “also known as” (Endelyn Moongrave), "
                             "taking its mistranscriptions with it, so both names count as the same character. Asks first.")
            if fn==self.character_history:
                b.setToolTip("Every D&D Beyond refresh of each character, with its date: level, HP, AC (estimate), saves, skills, spells "
                             "and items, and what changed between refreshes.")
            if fn==self.refresh_ddb:
                b.setToolTip("Read the selected characters (or all with a link) from D&D Beyond, using your connected account if there is "
                             "one. Keeps a dated snapshot in Character history. The names you typed are not changed.")
            if fn==self.ddb_account:
                b.setToolTip("Optional: connect your own D&D Beyond account so spells and items can also be read from characters "
                             "your account can see (other players' campaign-only characters). Saved encrypted for your Windows user only.")
            if fn==self.import_spells_items:
                b.setToolTip("Read the spells, magic items and feats of your characters (from the D&D Beyond links in this list, "
                             "for characters set to Public, or from a saved character file) and tick which to add.")
        root.addLayout(actions)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel); buttons.accepted.connect(self.save); buttons.rejected.connect(self.reject); root.addWidget(buttons)
        for e in db.campaign_dictionary_entries(campaign_id):
            keys = e.keys() if hasattr(e, "keys") else []
            self._append(e["canonical_name"],e["aliases"],e["category"],e["source"],bool(e["approved_for_whisper"]),e["source_url"],
                         e["last_refreshed"],e["import_status"],aka=e["also_known_as"] if "also_known_as" in keys else "")
        from .window_state import remember; remember(self)   # v2.46.1: opens at the size and place you left it
        from .notices import attach; attach(self)   # v2.47: yellow note bar at the bottom

    def _append(self,name="",aliases="",category="Character",source="Manual",approved=True,url="",refreshed="",status="",aka=""):
        r=self.table.rowCount(); self.table.insertRow(r)
        for c,v in ((C_NAME,name),(C_AKA,aka),(C_MIS,aliases),(C_CAT,category),(C_SRC,source),(C_URL,url),(C_REF,refreshed)):
            item=QTableWidgetItem(str(v)); self.table.setItem(r,c,item)
            if c==C_REF: item.setFlags(item.flags() & ~Qt.ItemIsEditable); item.setToolTip(status)
        check=QTableWidgetItem(); check.setFlags(Qt.ItemIsUserCheckable|Qt.ItemIsEnabled|Qt.ItemIsSelectable)
        check.setCheckState(Qt.Checked if approved else Qt.Unchecked); check.setToolTip(WHISPER_HELP)
        self.table.setItem(r,C_WHISPER,check)

    def _text(self,r,c):
        return self.table.item(r,c).text().strip() if self.table.item(r,c) else ""

    def ddb_names(self):
        """{D&D Beyond character id: the name typed in this list} (v2.36: shown instead of D&D Beyond's name)."""
        from .ddb_import import character_id_from_url
        out={}
        for r in range(self.table.rowCount()):
            try: out[character_id_from_url(self._text(r,C_URL))]=self._text(r,C_NAME)
            except Exception: pass
        return out

    # ---- sorting (on demand, by clicking a column title) ----
    def _row_values(self,r):
        return {"name":self._text(r,C_NAME),"aka":self._text(r,C_AKA),"aliases":self._text(r,C_MIS),"category":self._text(r,C_CAT),
                "source":self._text(r,C_SRC),"url":self._text(r,C_URL),"refreshed":self._text(r,C_REF),
                "status":self.table.item(r,C_REF).toolTip() if self.table.item(r,C_REF) else "",
                "approved":self.table.item(r,C_WHISPER).checkState()==Qt.Checked if self.table.item(r,C_WHISPER) else True}

    def _append_values(self,v):
        self._append(v["name"],v["aliases"],v["category"],v["source"],v["approved"],v["url"],v["refreshed"],v["status"],aka=v["aka"])

    def _sort_by(self,col):
        asc=not self._sort_state.get(col,False); self._sort_state={col:asc}
        rows=[self._row_values(r) for r in range(self.table.rowCount())]
        k=FIELDS[col] if col<len(FIELDS) else "approved"; second=lambda v:v["name"].casefold()
        key=(lambda v:(not v["approved"],second(v))) if k=="approved" else (lambda v:(str(v[k]).casefold(),second(v)))
        rows.sort(key=key,reverse=not asc)
        self.table.setRowCount(0)
        for v in rows: self._append_values(v)
        self.table.horizontalHeader().setSortIndicator(col,Qt.AscendingOrder if asc else Qt.DescendingOrder)
        self.table.horizontalHeader().setSortIndicatorShown(True)

    def make_aka(self):
        """Move the selected entries into another entry's "also known as" (with their mistranscriptions)."""
        rows=sorted({i.row() for i in self.table.selectedIndexes()})
        moving=[r for r in rows if self._text(r,C_NAME)]
        if not moving:
            QMessageBox.information(self,"Make also known as","Select the entry to move first (for example Creeping Lyn)."); return
        names=[self._text(r,C_NAME) for r in moving]
        targets=sorted({self._text(r,C_NAME) for r in range(self.table.rowCount()) if self._text(r,C_NAME) and r not in moving},key=str.casefold)
        target,ok=QInputDialog.getItem(self,"Make also known as",f"Which entry is “{', '.join(names)}” another name of?",targets,0,True)
        if not ok or not target.strip(): return
        t=next((r for r in range(self.table.rowCount()) if self._text(r,C_NAME).casefold()==target.strip().casefold()),-1)
        if t<0:
            QMessageBox.information(self,"Make also known as",f"“{target}” is not in the list."); return
        if QMessageBox.question(self,"Make also known as",
                                f"Add {', '.join(names)} to the “also known as” of {self._text(t,C_NAME)}, move their mistranscriptions "
                                f"there too, and remove their own row(s)?\n\nNothing is saved until you press Save.",
                                QMessageBox.Yes|QMessageBox.No)!=QMessageBox.Yes:
            return
        def merged(existing,extra):
            seen,out=set(),[]
            for x in [p.strip() for p in existing.split(",")]+extra:
                if x and x.casefold()!=self._text(t,C_NAME).casefold() and x.casefold() not in seen:
                    seen.add(x.casefold()); out.append(x)
            return ", ".join(out)
        aka_add,mis_add=[],[]
        for r in moving:
            aka_add.append(self._text(r,C_NAME)); aka_add += [p.strip() for p in self._text(r,C_AKA).split(",")]
            mis_add += [p.strip() for p in self._text(r,C_MIS).split(",")]
        akas={a.casefold() for a in aka_add}
        self.table.item(t,C_AKA).setText(merged(self._text(t,C_AKA),aka_add))
        self.table.item(t,C_MIS).setText(merged(self._text(t,C_MIS),[m for m in mis_add if m.casefold() not in akas]))
        for r in sorted(moving,reverse=True): self.table.removeRow(r)
        QMessageBox.information(self,"Make also known as","Done. Press Save to keep it.")

    def add_row(self):
        self._append(); self.table.setCurrentCell(self.table.rowCount()-1,C_NAME); self.table.editItem(self.table.item(self.table.rowCount()-1,C_NAME))

    def remove_rows(self):
        for r in sorted({i.row() for i in self.table.selectedIndexes()},reverse=True): self.table.removeRow(r)

    def set_category(self):
        rows=sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            QMessageBox.information(self,"Select rows","Select one or more rows first (click the first, Shift+click the last)."); return
        value,ok=QInputDialog.getItem(self,"Set category",f"Category for {len(rows)} selected row(s):",CATEGORIES,0,True)
        if ok and value.strip():
            for r in rows: self.table.item(r,C_CAT).setText(value.strip())

    def bulk_import(self):
        dlg=QDialog(self); dlg.setWindowTitle("Bulk import campaign names"); dlg.resize(650,540); layout=QVBoxLayout(dlg)
        layout.addWidget(QLabel("Paste one name per line. A line can also be: Name | wrong spelling 1, wrong spelling 2 | category — the middle part goes to Common mistranscriptions, and a category on the line wins over the type chosen below. Add other real names in the Also known as column afterwards."))
        layout.itemAt(0).widget().setWordWrap(True)
        form=QFormLayout()
        kind=QComboBox(); kind.setEditable(True); kind.addItems(CATEGORIES); kind.setToolTip("Every pasted line without its own category is imported as this type. You can type your own.")
        form.addRow("Import all of these as",kind); layout.addLayout(form)
        editor=QPlainTextEdit(); editor.setPlaceholderText("Longsword of Dawn\nBag of Holding\nCloak of Elvenkind") ; layout.addWidget(editor,1)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); buttons.accepted.connect(dlg.accept); buttons.rejected.connect(dlg.reject); layout.addWidget(buttons)
        if not dlg.exec(): return
        default_category=kind.currentText().strip() or "Other"
        existing={self._text(r,C_NAME).casefold() for r in range(self.table.rowCount())}
        added=0; duplicates=0
        for raw in editor.toPlainText().splitlines():
            line=raw.strip()
            if not line or line.startswith("#"): continue
            parts=[x.strip() for x in line.split("|",2)]; name=parts[0]
            # Correct the common Hither/Thither transcription/spelling mix-up at import time.
            if name.casefold() == "tither":
                name = "Thither"
            if not name or name.casefold() in existing: duplicates+=1; continue
            aliases=parts[1] if len(parts)>1 else SUGGESTED_ALIASES.get(name,"")
            category=parts[2] if len(parts)>2 and parts[2] else default_category
            approved=name not in AMBIGUOUS or bool(aliases)  # ordinary words start unticked for Whisper
            if name in AMBIGUOUS and not aliases: category += " (ambiguous/common word)"
            self._append(name,aliases,category,"Bulk import",approved); existing.add(name.casefold()); added+=1
        QMessageBox.information(self,"Bulk import complete",f"Added {added} as “{default_category}” (unless a line named its own). Skipped {duplicates} duplicate or empty line(s). Click Save to keep them.")

    def import_ddb(self):
        url,ok=QInputDialog.getText(self,"Import D&D Beyond character","Paste the character's PUBLIC share URL (not a private campaign URL):")
        if not ok or not url.strip(): return
        try:
            data=fetch_public_character(url)
            if data["name"].casefold() in {"d&d beyond character sheet", "d&d beyond", "character sheet"}:
                raise RuntimeError("D&D Beyond returned a generic page title instead of the character name. Enter the character name manually or try a PDF/JSON export.")
            self._append(data["name"],"","Character","D&D Beyond public page",True,data["url"],data["refreshed"],data["status"])
            extra="\n".join(x for x in (f"Race: {data['race']}" if data.get("race") else "",f"Class: {data['classes']}" if data.get("classes") else "") if x)
            QMessageBox.information(self,"Character imported",f"Imported name: {data['name']}\n{extra}\nLast refreshed: {data['refreshed']}\n\nOnly the public name and summary are kept in this list.")
        except Exception as exc:
            QMessageBox.warning(self,"D&D Beyond import unavailable",str(exc))
            if "no character name" in str(exc).casefold() or "generic page title" in str(exc).casefold():
                name,ok=QInputDialog.getText(self,"Enter character name","The public page did not expose the character name. Enter it manually:")
                if ok and name.strip():
                    self._append(name.strip(),"","Character","D&D Beyond public page (name entered manually)",True,url.strip(),"","Name entered manually; page metadata unavailable")

    def _fetch_characters(self, urls):
        """v2.34: read characters (with the connected account when there is one), keep a dated snapshot of each,
        and return (got [(url, payload, summary, changes)], problems)."""
        from . import ddb_auth
        from .ddb_import import fetch_character_payload, character_id_from_url
        from .ddb_character import summarize, changes
        from .background import run_blocking_in_thread
        connected = ddb_auth.has_session()

        def fetch_all():
            got, bad, note, token = [], [], "", None
            if connected:
                try: token = ddb_auth.account_token()   # one short-lived token for the whole batch
                except Exception as exc: note = f"Connected account not used: {exc}"
            for url in urls:
                try: got.append((url, fetch_character_payload(url, token)))
                except Exception as exc: bad.append((url, str(exc)))
            return got, bad, note
        got, bad, note = run_blocking_in_thread(fetch_all, parent=self, label="Reading characters from D&D Beyond…")
        names = {self._text(r, C_URL): self._text(r, C_NAME) for r in range(self.table.rowCount())}
        problems = [note] if note else []
        results = []
        for url, payload in got:
            try:
                summary = summarize(payload)
                ddb_id = character_id_from_url(url)
                old = self.db.character_snapshots(self.campaign_id, ddb_id, limit=1)
                diff = changes(old[0]["summary"], summary) if old else []
                self.db.save_character_snapshot(self.campaign_id, ddb_id, url, summary)
                results.append((url, payload, summary, diff, old[0]["fetched_at"] if old else ""))
            except Exception as exc:
                problems.append(f"{names.get(url, url)}: {exc}")
        problems += [f"{names.get(url, url)}: {why}" for url, why in bad]
        return results, problems

    def refresh_ddb(self):
        """Refresh the selected characters (or all with a D&D Beyond link): updates Last refresh and keeps a
        dated snapshot (level, HP, AC estimate, spells, items). The names you typed are never changed."""
        from .ddb_character import one_line
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        rows = [r for r in rows if "dndbeyond.com" in self._text(r, C_URL)]
        if not rows:
            rows = [r for r in range(self.table.rowCount()) if "dndbeyond.com" in self._text(r, C_URL)]
            if not rows:
                QMessageBox.information(self, "Refresh D&D Beyond", "No row has a D&D Beyond link."); return
            if QMessageBox.question(self, "Refresh D&D Beyond", f"No character row is selected. Refresh all {len(rows)} character(s) with a D&D Beyond link?",
                                    QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return
        url_rows = {}
        for r in rows:
            url_rows.setdefault(self._text(r, C_URL), []).append(r)
        try: results, problems = self._fetch_characters(list(url_rows))
        except Exception as exc: QMessageBox.warning(self, "Refresh D&D Beyond", str(exc)); return
        from datetime import datetime
        stamp = datetime.now().astimezone().isoformat(timespec="minutes")
        lines = []
        for url, _payload, summary, diff, since in results:
            for r in url_rows.get(url, []):
                self.table.item(r, C_REF).setText(stamp)
                self.table.item(r, C_REF).setToolTip(one_line(summary) + ("\n" + "\n".join(diff) if diff else ""))
                if self._text(r, C_SRC) in ("", "Manual"): self.table.item(r, C_SRC).setText("D&D Beyond")
                typed = self._text(r, C_NAME)
                note = f" (D&D Beyond calls them “{summary['name']}”)" if summary["name"] and summary["name"].casefold() != typed.casefold() else ""
                lines.append(f"{typed}{note}: {one_line(summary)}")
                if diff: lines += [f"    • {d}" for d in diff] + ([f"    (changes since {since[:16].replace('T', ' ')})"] if since else [])
        msg = f"Refreshed {len(results)} character(s). Each refresh is kept in Character history.\n\n" + "\n".join(lines)
        if problems: msg += "\n\nNot read:\n" + "\n".join(problems)
        msg += "\n\nPress Save to keep the new Last refresh dates."
        QMessageBox.information(self, "Refresh D&D Beyond", msg)

    def character_history(self):
        from .character_history_dialog import CharacterHistoryDialog
        CharacterHistoryDialog(self.db, self.campaign_id, self, names=self.ddb_names()).exec()

    def ddb_account(self):
        from .ddb_account_dialog import DdbAccountDialog
        from .background import run_blocking_in_thread
        DdbAccountDialog(self, run_blocking_in_thread).exec()

    def import_spells_items(self):
        """v2.32: spells, items and feats from D&D Beyond characters, offered in a tick list."""
        from .ddb_import import fetch_character_payload
        from .ddb_spells_items import parse_character, load_character_file
        from .ddb_spells_dialog import SpellsItemsDialog
        from .background import run_blocking_in_thread
        urls=[]
        for r in range(self.table.rowCount()):
            url=self._text(r,C_URL)
            if url and "dndbeyond.com" in url and url not in urls: urls.append(url)
        from . import ddb_auth
        connected=ddb_auth.has_session()
        sources=[f"From the D&D Beyond links in this list ({len(urls)})"+(" · using your connected account" if connected else " · Public characters only"),
                 "From a saved character file (JSON or PDF)…"]
        if not urls: sources=sources[1:]
        choice,ok=QInputDialog.getItem(self,"D&D Beyond spells & items","Where should the spells and items come from?",sources,0,False)
        if not ok: return
        rows,problems,changed=[],[],[]
        if choice.startswith("From the D&D Beyond links"):
            try: results,problems=self._fetch_characters(urls)
            except Exception as exc: QMessageBox.warning(self,"D&D Beyond",str(exc)); return
            typed={self._text(r,C_URL):self._text(r,C_NAME) for r in range(self.table.rowCount())}
            for url,payload,summary,diff,since in results:
                try:
                    ddb_who,found=parse_character(payload)
                    who=typed.get(url) or ddb_who   # v2.36: the name in this list, not D&D Beyond's
                except Exception as exc: problems.append(f"{summary.get('name') or url}: {exc}"); continue
                rows.extend({**f,"who":who,"url":url} for f in found)
                if diff: changed.append(f"{who} (since {since[:16].replace('T',' ')}): " + "; ".join(diff))
        else:
            path,_=QFileDialog.getOpenFileName(self,"Saved D&D Beyond character","","Character files (*.json *.pdf);;JSON (*.json);;PDF (*.pdf)")
            if not path: return
            try: who,found,how=load_character_file(path)
            except Exception as exc: QMessageBox.warning(self,"D&D Beyond spells & items",str(exc)); return
            rows=[{**f,"who":who or Path(path).stem,"url":""} for f in found]
        if not rows:
            QMessageBox.information(self,"D&D Beyond spells & items","No spells or items were found."+("\n\nNot read: "+"; ".join(problems) if problems else "")); return
        from .ddb_spells_items import combine
        rows=combine(rows)   # a spell two characters know is one row
        existing=[self._text(r,C_NAME) for r in range(self.table.rowCount()) if self._text(r,C_NAME)]+[a.strip() for r in range(self.table.rowCount()) for a in self._text(r,C_AKA).split(",") if a.strip()]
        dlg=SpellsItemsDialog(rows,existing,problems,self,changed=changed)
        if dlg.exec()!=QDialog.Accepted: return
        from datetime import datetime
        stamp=datetime.now().astimezone().isoformat(timespec="minutes")
        have={e.casefold() for e in existing}; added=0
        for c in dlg.chosen():
            if c["name"].casefold() in have: continue
            self._append(c["name"],"",c["category"],f"D&D Beyond: {c['who']}",c["whisper"],"",stamp,"Imported from the character's spells / items")
            have.add(c["name"].casefold()); added+=1
        QMessageBox.information(self,"D&D Beyond spells & items",f"Added {added} name(s) to the list. Press Save to keep them.")

    def import_sheet(self):
        path,_=QFileDialog.getOpenFileName(self,"Import a character sheet","","Character sheets (*.pdf *.json);;PDF files (*.pdf);;JSON files (*.json)")
        if not path:return
        p=Path(path); text=""
        try:
            if p.suffix.lower()==".json":
                data=json.loads(p.read_text(encoding="utf-8-sig"))
                def find_name(obj):
                    if isinstance(obj,dict):
                        for key in ("characterName","character_name","name"):
                            if isinstance(obj.get(key),str) and obj[key].strip(): return obj[key].strip()
                        for value in obj.values():
                            found=find_name(value)
                            if found:return found
                    elif isinstance(obj,list):
                        for value in obj:
                            found=find_name(value)
                            if found:return found
                    return ""
                name=find_name(data)
            else:
                from pypdf import PdfReader
                reader=PdfReader(path); text="\n".join(page.extract_text(extraction_mode="layout") or "" for page in reader.pages)
                name=""
                patterns=[r"(?im)Character\s*Name\s*[:\n]+\s*([^\n]{2,80})",r"(?im)^\s*Name\s*[:\n]+\s*([^\n]{2,80})",r"(?im)^\s*([A-Z][A-Za-z' -]{1,45})\s+(?:Level\s+\d+|\d+(?:st|nd|rd|th)\s+Level)"]
                for pattern in patterns:
                    m=re.search(pattern,text)
                    if m:
                        candidate=m.group(1).strip(" :\t")
                        if candidate.casefold() not in {"class","race","level","player name","character name"}: name=candidate; break
            if not name:
                preview="\n".join(text.splitlines()[:18])[:1800] if text else "(No readable text was extracted.)"
                QMessageBox.information(self,"PDF text preview","The importer could not confidently identify the character name. Here is the extracted text to help you identify the correct field:\n\n"+preview)
                name,ok=QInputDialog.getText(self,"Character name not detected","Enter the character's canonical name:")
                if not ok or not name.strip():return
            self._append(name.strip(),"","Character","Imported: "+p.name,True)
            QMessageBox.information(self,"Character imported",f"Added {name.strip()}. Review the name and add aliases if needed.")
        except Exception as exc: QMessageBox.warning(self,"Could not import character sheet",f"The file could not be read: {exc}")

    def save(self):
        entries=[]
        for r in range(self.table.rowCount()):
            name=self._text(r,C_NAME)
            if not name:continue
            entries.append({"canonical_name":name,"also_known_as":self._text(r,C_AKA),"aliases":self._text(r,C_MIS),
                "category":self._text(r,C_CAT) or "Other","source":self._text(r,C_SRC) or "Manual",
                "source_url":self._text(r,C_URL),"last_refreshed":self._text(r,C_REF),
                "import_status":self.table.item(r,C_REF).toolTip() if self.table.item(r,C_REF) else "",
                "approved_for_whisper":self.table.item(r,C_WHISPER).checkState()==Qt.Checked})
        try:self.db.save_campaign_dictionary(self.campaign_id,entries); self.accept()
        except Exception as exc:QMessageBox.critical(self,"Could not save dictionary",str(exc))
