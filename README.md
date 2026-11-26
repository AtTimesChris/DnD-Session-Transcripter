# Local SessionKeeper — v2.48

Local-first Windows desktop application for D&D recording management, transcription, speaker diarization, transcript correction and campaign terminology. Transcription and diarization run locally. No paid API or subscription is required.

## Install
Windows only.

1. Install [Python 3.11 (64-bit)](https://www.python.org/downloads/windows/), keeping the `py` launcher option ticked.
2. On this GitHub page choose **Code → Download ZIP**, then unzip it to a folder you can write to.
3. Double-click `INSTALL.bat`. It needs an internet connection and downloads several GB; with an NVIDIA GPU it also downloads the CUDA runtime (about 850 MB).
4. Double-click `RUN.bat`.

Your settings, database and recordings are created inside that folder on your own PC.

Speaker identification also needs a free Hugging Face account: accept the terms for [pyannote Community-1](https://huggingface.co/pyannote/speaker-diarization-community-1) and create a read token. The app asks for the token the first time.

## v2.48 (current)

- The transcript editor stays fast however far you scroll: lines far off screen give their boxes back (at most 240 lines have them). On a 5,877-line transcript a page scrolls in about 53 ms all the way through, where v2.47 reached 800 ms per page after 300 pages. Nothing in the transcript changes.

## v2.47

- Shift+> / Shift+< playback speed in the editor. Notes in a yellow bar at the bottom of every open window (12 s). View → Colours…: light / dark theme, three colour-blind-safe speaker colour sets (one with no reds, greens or browns), and a symbol beside each speaker. One way to process recordings (Transcribe / Identify speakers / Both go through the queue, which starts by itself), a menu bar, right-click and double-click on recordings, a Progress column, drag and drop to import, first-steps hints. Editor: short headers, Reviewed lines tinted, a Reviewed progress bar. INSTALL.bat checks every package.

## v2.46.1

- Editor shortcuts: Ctrl+Space play / pause, Alt+← / Alt+→ previous / next segment, Ctrl+Enter tick Reviewed and go to the next unreviewed, F1 for the list. Windows open at the size and place you left them (`data\window_state.ini`). Short notes show in a yellow bar instead of a pop-up. One shared set of colours.

## v2.46

- Book names: the whole book is read and only real names are ticked (capitalised mid-sentence), used by Correct names (new tick box, 85% close) and the editor's Suggestions (80%), never by Whisper. Find names again…, one Import D&D books… window, "part of" matching and Combine into one book… (Strixhaven), a books folder with Re-link all, D&D Beyond details open at once. Correct names stays open beside the editor, hides words already a name, and can add a name. Roll finder: Shield, "What did it roll?", more misses, counting squares, attacks said before the number, Add roll here…, Your changes… with Undo, double-click to edit a line, DM row and notable moments. Alternatives stays open, highlights differences and adds short pieces. The mouse wheel never changes a closed list; Enter in Find no longer closes the editor; & shows on buttons; recording buttons greyed until one is selected.

## v2.45

- Alternatives… re-transcribes a selected stretch three ways (careful search, with your names, looser) and lets you pick one; the roll finder can stay open beside the editor; D&D Beyond book details (edition, publisher, date, suggested category) from its public catalogue, at import and in the D&D books window.

## v2.44

- Roll finder: attacks grouped with their damage and damage taken; damage said on its own linked to the attack before it (spell and critical hit carried over); flags to check (repeated totals, AC answers, nothing said before a roll); Ignore this number; Asked for and Used can be set; Normal advantage; Line column; summary numbers list their rolls. Enter on a line with | offers to split it.

## v2.43

- D&D books window (check, re-link, re-read, change category, remove); Whisper gets the most-used names instead of P to Z; roll finder display fixes (no ghost box, normal row heights, ▶ selects, filters keep your place); more DM attacks found; whole spell names in Correct names; Edit transcript opens the version the arrows show.

## v2.42

- Split times taken from the original Whisper words (edited lines still split in the right place); spell damage checked against spell dice read from imported books; the controls sit together in the editor and roll finder, and columns can be dragged into your own order.

## v2.41

- Split lines with `{name} text | {name} text` (editor "Split at | …" and the roll finder's Apply changes); roll finder Earlier / Later, spell damage and critical hit notes, advantage / disadvantage column and stats; Correct names, roll finder and re-transcription merge are faster.

## v2.40

- Roll finder: finds rolls nobody asked for, loosely said saves and the DM's attacks (Hit / Miss against AC); credits rolls by names said and by who made the check; damage totals said afterwards; "No roll" with a reason; speakers can be corrected from the lines panel.

## v2.39

- Roll finder: the lines around each roll with ▶ and in-place correction, play on 30 s, pass / fail (guessed from what is said next, settable), who (settable), damage taken, and session / campaign summaries per character. Results are saved for player stats.

## v2.38

- Roll finder checks attack rolls against the weapon used, spell attacks and save DCs, and damage rolls against weapon dice and features such as Sneak Attack. Snapshots record weapon attack and damage numbers.

## v2.37

- **Roll finder** in the editor: checks asked for, the numbers said after them, and the bonus on the character's D&D Beyond snapshot, with possible mix-ups and natural 20s/1s flagged and ▶ to listen. Set "Players and characters…" once per campaign.

## v2.36

- Campaign names has **Also known as** (other real names, never corrected away, hinted to Whisper) and **Common mistranscriptions** (wrong spellings to correct; the old Aliases column), plus a "Make also known as of…" button.
- Character history shows your names for characters ("Pip (D&D Beyond: Pipkin)").

## v2.35

- Character snapshots record saves, skills, passives, initiative, speed, spell DC / attack, advantage notes and proficiencies, for checking rolls in the recordings later.
- AC estimate counts only equipped items and treats "set AC" as a minimum (as shown on the character sheet).

## v2.34

- **Refresh selected D&D Beyond** uses your connected account and keeps a dated snapshot of each character (level, HP, AC estimate, ability scores, spells, items).
- **Character history…** in Campaign names: every refresh per character, with what changed between them (level ups, new or lost spells and items).
- Spells & items import also notes what changed since the last refresh; names no longer on a sheet are flagged, never removed.

## v2.33

- **D&D Beyond account (optional)** in Campaign names: connect your own account by pasting its session cookie, so spells and items can be read from campaign-only characters your account can see. Saved encrypted for your Windows user only, outside the program folder; Disconnect deletes it. Nobody else who gets the program (for example from Git) gets your connection.

## v2.32.1

- Compare window: a speaker you pick marks the new line Reviewed (option, on by default); Listen is the last column.

## v2.32

- **D&D Beyond spells & items** in Campaign names: spells, magic items and feats from your characters' D&D Beyond pages (Public characters) or a saved character file, cleaned up and offered in a tick list.
- **Delete recording…** (with a backup first; the audio file is never deleted).
- Correct tooltips on every main-window button; more room for the recordings list.
- Suggested ranges: tick several and re-transcribe them together; ▶ also pauses.
- Compare window: split a new line between two speakers; play all rows one after another.
- The editor goes to the first new line after a merge; lighter autosave.

## v2.31.2

- The main window keeps the selected recording when the list refreshes.
- Separate **Edit transcript**, **Compare versions** and **Delete version…** buttons.
- **⏯ Pause / resume** (and Space) in Correct names and the re-transcribe compare window.
- Re-transcribe range: choose the **speaker for new lines** per row, and check names in just the new lines after applying.

## v2.31.1

- **Suggested ranges…** in the editor: a list of likely problem spots (gaps where there is speech-level sound, looping text, low Whisper confidence, very long segments) with ▶ to listen and a button that opens Re-transcribe range on the one you pick. The list itself changes nothing.

## v2.31

- **Re-transcribe range…** in the editor: Whisper runs again on just the selected stretch, then a compare window lets you keep the current text or use the new text row by row (or fill only the holes). Reviewed segments are always kept; one Ctrl+Z undoes it.
- Speaker identification saves its speaker turns so re-transcribed lines get speakers.
- Correct names… has "Skip in this transcript" for suggestions that are wrong here but right elsewhere.
- Each log line is written once.

## v2.30.1

- Correct names… never "corrects" players' names and can suggest misheard ones, finds names split into two words ("Vale Ith", "vay lith"), and lets you rewrite a whole line ("Edit line…" or double-click the Context) when more than the name is wrong.

## v2.30r2

- Correct names… now finds names that older transcripts wrote in lower case (for example "Vaelit" and "Vaelithe").
- Changing audio device or the Listen setting keeps your place in the list and in the recording.
- The log no longer reports the Correct names window as a "slow step" while you are using it.

## v2.30r1

- **Hotfix:** the editor's Show filter crashed ("apply_speaker_filter() takes 1 positional argument but 2 were given"), and the new Correct names… button had the same fault. Fixed.
- Correct names… has a ▶ button on every row (and Space plays the selected row) so you can hear a line before ticking it.

## v2.30

- **Correct names…** (transcript editor, Transcript group): lists possible misspelled campaign names for you to tick. Known mishearings are exact matches of your aliases and accepted corrections ("Sibylna" -> "Zybilna"); near misses are capitalised words close to a name that never appear in lower case in your transcripts ("Agdon" -> "Agden"). Nothing is ticked or changed until you apply; Shift+click ticks a range; Reviewed segments are never changed; one Ctrl+Z undoes it. Lets you keep Campaign context off for Whisper.

## v2.29.1

- **Punctuation style hint** (Audio settings, on by default): Whisper gets one short, ordinary punctuated sentence (no names) before every 30-second window, so it keeps writing full sentences instead of cutting after almost every phrase. In a full-session test, mid-sentence one-word segments went from 285 to 0. Affects new transcriptions only; existing transcripts are not changed.

## v2.29r2

- **Hotwords are off by default** and not recommended. In a full-session test they made Whisper loop on names and lose about 11 whole 30-second stretches of speech; with hotwords off, none were lost. If your `config.json` has `"whisper_use_hotwords": true`, untick Audio settings -> Hotwords (the app does not change your settings for you).
- **Only canonical spellings are sent to Whisper.** Aliases (nicknames and mishearings such as "Glean" for "Gleam") are kept for correcting names afterwards and are never used as Whisper vocabulary.
- The sections below are older release notes.

## v2.20 changes

- Import reviewed work between transcript versions of the same recording.
- Saved re-check report per transcript version.
- Learn from my edits (approve recurring corrections into the campaign vocabulary).
- Maximisable / snappable editor window.

## v2.19 changes

### Speaker re-check (learns from your corrections)
- **Identify / re-check speakers** can now be run again and again on the same transcript. Every segment you tick **Reviewed** is locked: it is never changed, and it is used as evidence to fix the rest.
- A new diarization pass produces anonymous clusters. Each reviewed segment votes for the cluster it sits in; a cluster with enough consistent votes becomes that speaker. One person split across several clusters is merged automatically, and every unreviewed segment in those clusters follows.
- Clusters with no reviewed clips can still be matched by **voice similarity** (pyannote cluster embeddings) to a speaker you already reviewed in the same recording, or to **voiceprints saved from earlier sessions** of the campaign. These matches are listed in the result so you can spot-check them.
- Result summary after each run: reviewed time per person, what was matched by voice, and which clusters still need 2–3 reviewed clips.
- A copy of the transcript is kept in the edit history before each re-check (`transcript_edit_history`).
- The editor shows **reviewed speech per speaker** so you can see when someone has about a minute.
- How much is needed: roughly **one minute of reviewed speech per person, in short clips (even 3–10 seconds) taken from different parts of the session**, ideally from each cluster. You do not need long uninterrupted passages, perfect timestamps, or to review the whole session. Clips that overlap several voices are ignored as evidence.
- Voiceprints are only saved from clusters your own review identified (at least 10 s of evidence) and a mapped player name. They are stored per campaign in the local database.

### Transcription
- New `whisper_vad_threshold` (default 0.4; faster-whisper's default is 0.5). Lower values keep quieter speech, which can recover the start of a session that was skipped as "silence". Set 0 to use the library default. Raise it if you see invented text in silent stretches.

### Housekeeping
- Version labels in the installers, launcher and README.

## v2.18r1 changes
Autosave connection fix, safe editor close, correct-row remove/add, hotwords, faster alignment and recording list, and more.

## Known limitations in v2.19
- Voice-similarity matching needs pyannote to return speaker embeddings (`speaker_embeddings` on the pipeline output, pyannote.audio 4.x). If your version does not, re-check still works from reviewed segments alone; the result dialog says so. Similarity thresholds (`diarization_match_threshold`, default 0.60) have not been tuned on your recordings.
- A cluster that pyannote built from two different people cannot be split by relabelling; it is reported as "contains two people". Review the clips in it manually.
- Close the transcript editor before running Identify / re-check speakers (both write to the same transcript).
- The transcript grid still uses a widget-per-segment table; very long transcripts take time to open.
- D&D Beyond import remains the public-page HTML importer. Speaker splitting into a new physical speaker ID is not included.
- Not changed since v2.18: `credentials.py`, `install_cuda_runtime.py` (no download checksum yet), `vault_import.py`, `campaign_dictionary.py`, `reference_library.py`.

## Upgrade
1. Close SessionKeeper and back up `data`, `recordings`, `config.json`, `backups` and `runtime`.
2. Unzip the release overlay over your existing folder (it never contains `config.json`, `.venv`, `data` or `recordings`).
3. Run `RUN.bat`. Run `INSTALL.bat` again if the `requirements` files changed. The database upgrades itself on first start.

## Your data
`config.json`, `data/`, `recordings/`, `logs/`, `backups/`, `runtime/` and `.venv/` hold your own data and are never uploaded (see `.gitignore`). `config.example.json` shows the settings.
