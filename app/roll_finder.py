"""Roll finder (v2.37, attacks / spells / damage in v2.38). Qt-free and unit-tested.

Finds where a roll was asked for or made in a transcript ("Roll me a dex check", "roll for attack",
"spell attack", "roll damage", "DC 13") and what people said in the next stretch ("17 plus 6",
"1d4 plus 4 plus 2d6 sneak attack", "That's a nat 20", "9 damage"). Each result is matched to the speaker's
character and compared with that character's D&D Beyond snapshot:
- checks, saves, initiative: the skill / save / ability bonus;
- attack rolls: the weapon named nearby ("my dagger"), else any equipped weapon, or the spell attack bonus;
- spell attacks and a "DC" a player says: spell attack bonus and spell save DC;
- damage: the weapon's dice and bonus, plus damage features such as Sneak Attack.

It only reads; nothing is changed. Speech-to-text and table talk are messy, so every row is something to
listen to, not a verdict.
"""
import re

SKILLS = {"acrobatics": "Acrobatics", "animal handling": "Animal Handling", "arcana": "Arcana", "athletics": "Athletics",
          "deception": "Deception", "history": "History", "insight": "Insight", "intimidation": "Intimidation",
          "intimidate": "Intimidation", "investigation": "Investigation", "investigate": "Investigation",
          "medicine": "Medicine", "nature": "Nature", "perception": "Perception", "performance": "Performance",
          "persuasion": "Persuasion", "persuade": "Persuasion", "religion": "Religion", "sleight of hand": "Sleight of Hand",
          "sleight": "Sleight of Hand", "stealth": "Stealth", "survival": "Survival"}
ABILITIES = {"strength": "STR", "str": "STR", "dexterity": "DEX", "dex": "DEX", "constitution": "CON", "con": "CON",
             "intelligence": "INT", "int": "INT", "wisdom": "WIS", "wis": "WIS", "charisma": "CHA", "cha": "CHA"}

_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
          "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
          "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30}
_NUM = r"(?:\d{1,2}|twenty[- ](?:one|two|three|four|five|six|seven|eight|nine)|" + "|".join(sorted(_WORDS, key=len, reverse=True)) + r")"

_SKILL_RX = "|".join(sorted((re.escape(k) for k in SKILLS), key=len, reverse=True))
_ABIL_RX = "|".join(sorted((re.escape(k) for k in ABILITIES), key=len, reverse=True))
_CHECK = re.compile(
    r"\b(?P<spell>spell attack)s?\b"
    rf"|\b(?P<skill>{_SKILL_RX})\b(?:\s+(?:check|roll|save))?"
    rf"|\b(?P<abil>{_ABIL_RX})\b\.?\s+(?P<kind>check|save|saving throw|saving)s?\b"
    r"|\b(?:your|a|the|that|what|my|his|her|their)\s+(?P<anysave>saving throw|save)s?\b"
    r"|\b(?P<init>initiative)\b"
    r"|\b(?P<death>death sav(?:e|ing throw))s?\b"
    r"|\b(?P<hit>to hit|attack roll|roll (?:to|for) (?:hit|attack))\b"
    r"|\b(?P<dmg>damage)\b", re.I)
_ASK = re.compile(r"\b(roll|rolling|rolled|make|give me|do|does|did|deal|deals|check|save|saving|saving throw|fail|pass|initiative|attack roll|spell attack|how much)\b", re.I)
_DC = re.compile(rf"\bDC\s*(?:of\s*|is\s*|=\s*)?(?:a\s*)?(?P<n>{_NUM})\b", re.I)

_NAT = re.compile(r"\bnat(?:ural)?\s*(?P<n>20|twenty|1|one)\b", re.I)
_PLUS = re.compile(rf"(?<![dD\w])(?P<die>{_NUM})\s*(?:plus|\+)\s*(?P<bonus>{_NUM})\b(?:\s*(?:=|is|equals|makes)\s*(?P<total>{_NUM}))?", re.I)
_MINUS = re.compile(rf"(?<![dD\w])(?P<die>{_NUM})\s*(?:minus|-)\s*(?P<bonus>{_NUM})\b", re.I)
_SAID = re.compile(rf"\b(?:rolled|got|that's|that is|it's|it is|total(?: of)?|makes|for)\s+(?:a |an )?(?P<n>{_NUM})\b(?![.,]\d)(?!\s*(?:plus|\+|minus|feet|ft|gold|damage|hit points|hp|d\d|ones|twos|times|of|in a row|more|left|people|turns?|rounds?|sides?))", re.I)
_DICE = re.compile(r"\b\d*\s*d\s?\d{1,3}\b", re.I)   # d20, 1d4, d 100: dice names, never a d20 result
# damage: "1d4 plus 4 plus 2d6", "d12 plus seven", "2d6"
_DIE_TERM = rf"(?:(?P<count>\d|a|one|two|three|four|five|six|eight|ten)\s*)?d\s?(?P<sides>4|6|8|10|12)\b"
_DMG_EXPR = re.compile(rf"(?:{_DIE_TERM})(?P<rest>(?:\s*(?:plus|\+)\s*(?:(?:\d|a|one|two|three|four|six|eight|ten)?\s*d\s?(?:4|6|8|10|12)\b|{_NUM}\b))*)", re.I)
_DMG_TOTAL = re.compile(rf"\b(?P<n>{_NUM})\s*(?:points? of\s*)?(?:\w+\s)?damage\b|\b(?:does|did|deal|deals|dealt|for)\s+(?P<m>{_NUM})\s*(?:(?:points? of\s*)?(?:\w+\s)?damage)?\b", re.I)


_ANY_NUMBER = re.compile(rf"\d|\b{_NUM}\b", re.I)


def number(text):
    t = str(text).strip().lower().replace("-", " ")
    if t.isdigit():
        return int(t)
    if t in ("a", "an"):
        return 1
    if t in _WORDS:
        return _WORDS[t]
    parts = t.split()
    if len(parts) == 2 and parts[0] == "twenty" and parts[1] in _WORDS:
        return 20 + _WORDS[parts[1]]
    return None


def detect_check(text):
    """('skill', 'Stealth') / ('check', 'DEX') / ('save', 'DEX') / ('initiative', None) / ('death', None) /
    ('attack', None) / ('spell_attack', None) / ('damage', None) / ('dc', None), or None."""
    text = text or ""
    m = _CHECK.search(text)
    if m and _ASK.search(text):
        if m.group("spell"):
            return ("spell_attack", None)
        if m.group("skill"):
            return ("skill", SKILLS[m.group("skill").lower()])
        if m.group("anysave"):
            return ("save", None)
        if m.group("abil"):
            kind = m.group("kind").lower()
            return ("save" if kind.startswith("sav") else "check", ABILITIES[m.group("abil").lower().rstrip(".")])
        if m.group("init"):
            return ("initiative", None)
        if m.group("death"):
            return ("death", None)
        if m.group("hit"):
            return ("attack", None)
        if m.group("dmg"):
            return ("damage", None)
    if _DC.search(text):
        return ("dc", None)
    return None


def read_damage(text):
    """Damage said: [{'kind':'damage','dice':[(count, sides)],'bonus':int,'total':int|None,'said'}]."""
    out = []
    for m in _DMG_EXPR.finditer(text or ""):
        count = number(m.group("count") or "1") or 1
        dice, bonus = [(count, int(m.group("sides")))], None   # None: no bonus said
        for term in re.split(r"\s*(?:plus|\+)\s*", m.group("rest") or "")[1:]:
            dm = re.fullmatch(r"(?:(\d|a|one|two|three|four|six|eight|ten)\s*)?d\s?(4|6|8|10|12)", term.strip(), re.I)
            if dm:
                dice.append((number(dm.group(1) or "1") or 1, int(dm.group(2))))
            elif number(term) is not None:
                bonus = (bonus or 0) + number(term)
        out.append({"kind": "damage", "dice": dice, "bonus": bonus, "total": None, "said": m.group(0).strip()})
    clean = _DICE.sub(" ", text or "")
    for m in _SUM_DMG.finditer(clean):   # v2.46: "12 plus 15 damage" (weapon + smite) is 27
        a, b = number(m.group("a")), number(m.group("b"))
        if a is not None and b is not None and 1 <= a + b <= 150:
            out.append({"kind": "damage", "dice": [], "bonus": None, "total": a + b, "said": m.group(0).strip(),
                        "sum_of": (a, b)})
            clean = clean[:m.start()] + " " * (m.end() - m.start()) + clean[m.end():]
    for m in _DMG_TOTAL.finditer(clean):
        n = number(m.group("n") or m.group("m"))
        if n is not None and 1 <= n <= 99 and (m.group("n") or re.search(r"damage", m.group(0), re.I)):
            out.append({"kind": "damage", "dice": [], "bonus": None, "total": n, "said": m.group(0).strip()})
    return out


_SUM_DMG = re.compile(rf"\b(?P<a>{_NUM})\s*(?:plus|\+|and)\s*(?P<b>{_NUM})\s*(?:points? of\s*)?(?:\w+\s)?damage\b", re.I)
_COUNT_NUM = re.compile(r"\b(\d{1,3})\b")


def counting(text):
    """v2.46: a line counting out movement ("5, 10, 15, 20, 25": squares of 5 ft), not a roll: three or more numbers,
    all multiples of 5, never going down, reaching 15 or more."""
    nums = [int(n) for n in _COUNT_NUM.findall(text or "")]
    if len(nums) < 3:
        return False
    return all(n % 5 == 0 for n in nums) and all(b >= a for a, b in zip(nums, nums[1:])) and nums[-1] >= 15


def read_results(text):
    """Numbers someone says about a d20 roll: [{'kind':'d20','die','bonus','total','nat','said'}] (missing parts None),
    a 'DC 13' a player states ({'kind':'dc','total':13}), and damage rolls (see read_damage)."""
    if not _ANY_NUMBER.search(text or ""):
        return []   # v2.41: every result needs a digit or a number word; most lines have neither
    if counting(text):
        return []   # v2.46: counting squares of movement, not a roll
    out, used = [], []
    for m in _DC.finditer(text or ""):
        out.append({"kind": "dc", "die": None, "bonus": None, "total": number(m.group("n")), "nat": None, "said": m.group(0)})
    damage = read_damage(text)
    clean = _DICE.sub(" ", text or "")
    clean = _DC.sub(" ", clean)
    clean = re.sub(rf"\b{_NUM}\s*(?:points? of\s*)?(?:\w+\s)?damage\b", " ", clean, flags=re.I)

    def free(m):
        return all(m.end() <= a or m.start() >= b for a, b in used)
    for m in _NAT.finditer(clean):
        n = 20 if m.group("n").lower() in ("20", "twenty") else 1
        # v2.43: "double nat 20" (advantage or disadvantage) is one roll but two dice showing 20: both are counted
        two = bool(re.search(r"\b(?:double|two|both)\s+$", clean[:m.start()], re.I))
        out.append({"kind": "d20", "die": n, "bonus": None, "total": None, "nat": n, "nat_dice": 2 if two else 1,
                    "said": m.group(0)}); used.append(m.span())
    for rx, sign in ((_PLUS, 1), (_MINUS, -1)):
        for m in rx.finditer(clean):
            if not free(m):
                continue
            die, bonus = number(m.group("die")), number(m.group("bonus"))
            if die is None or bonus is None or bonus > 15:
                continue
            total = number(m.group("total")) if m.groupdict().get("total") else None
            out.append({"kind": "d20", "die": die, "bonus": sign * bonus, "total": total if total is not None else die + sign * bonus,
                        "nat": None, "said": m.group(0)}); used.append(m.span())
    for m in _SAID.finditer(clean):
        if not free(m):
            continue
        n = number(m.group("n"))
        if n is None or not 1 <= n <= 45:
            continue
        out.append({"kind": "d20", "die": None, "bonus": None, "total": n, "nat": None, "said": m.group(0)}); used.append(m.span())
    if not out and not damage:   # a line that is only a number ("Seventeen.", "12")
        words = re.findall(r"[a-z0-9]+", clean.lower())
        if 1 <= len(words) <= 2:
            n = number(" ".join(words))
            if n is not None and 1 <= n <= 45:
                out.append({"kind": "d20", "die": None, "bonus": None, "total": n, "nat": None, "said": clean.strip()})
    return out + damage


# ---------- rolls nobody asked for, the DM's attacks, names (v2.40) -------------------------------
_MEASURE = re.compile(r"\b(?:foot|feet|ft|tall|wide|long|high|deep|hours?|minutes?|seconds?|gold|gp|silver|copper|speed|"
                      r"years?|days?|miles?|weeks?|months?|o'?clock|percent|pounds?|lbs?|times?|people)\b", re.I)
_ROLLED = re.compile(rf"\brolled\s+(?:a |an )?{_NUM}\b(?!\s*(?:d\s?\d|times|ones|twos|in a row))", re.I)
# said by the DM: "does a 15 hit?", "what's your AC?", "this is going to hit you", "it bites you"
# v2.43: more of the ways the DM says it ("lunges at you", "has a go at you", "makes a grab for you",
# "The wolf is going to attack you", "What is your AC?", "the tail comes round to hit you")
_DM_ATTACK = re.compile(rf"\b(?:does|do|would|will|did)\s+(?:a |an )?{_NUM}\s+(?:hit|beat|get|meet)\b"
                        r"|\bwhat(?:'?s| is) your (?:AC|armou?r class)\b|\bwhat AC\b|\b(?:going to|gonna|is to|will) hit you\b"
                        r"|\b(?:get|gets|beat|beats|meet|meets|hit|hits) your (?:AC|armou?r class)\b|\bagainst your (?:AC|armou?r)\b"
                        r"|\b(?:attacks?|swings? at|lunges? at|bites?|claws? at|slash(?:es)? at|strikes? at|shoots? at|fires? at"
                        r"|stabs? at|lashes out at|(?:takes?|have|has) a swing at|(?:going to|gonna|will|'ll) attack"
                        r"|smash(?:es)?(?: down on)?) you\b"
                        r"|\b(?:in for a|takes? a) swing\b", re.I)
# said by a player about the DM's roll: "does it get my AC?", "does that hit me?"
_HIT_ME = re.compile(r"\b(?:get|gets|beat|beats|meet|meets|hit|hits) my (?:AC|armou?r class)\b|\bdoes (?:it|that|he|she) hit me\b"
                     r"|\b(?:what|how much) (?:did|does) (?:it|he|she) (?:roll|get)\b", re.I)
# said by a player about their own attack: "does that hit?", "does a 19 hit?"
_DOES_HIT = re.compile(rf"\b(?:does|would|will|did)\s+(?:that|it|this|mine|a {_NUM}|an {_NUM}|{_NUM})\s+hit\b(?!\s+(?:me|you)\b)", re.I)
# damage said about someone else's roll: "you did 10 damage to the wolf?", "6 damage with that hit?"
_ABOUT_OTHER = re.compile(r"\byou(?:'re| are)?\s+(?:do|did|doing|deal|dealt|dealing)\b|\byour\b[^.!]*\?\s*$", re.I)
_AC_SAID = re.compile(rf"\b(?:my )?(?:AC|armou?r class)\s+(?:is |of |'?s )?(?:a |an )?(?P<n>{_NUM})\b", re.I)
_ATTACK_NUMBER = re.compile(rf"\b(?:does|do|would|will|did)\s+(?:a |an )?(?P<n>{_NUM})\s+(?:hit|beat|get|meet)\b", re.I)


# a request in the same line as a number ("roll me a 15 dex save?", "give me a d20") is still a request
_ASKED = re.compile(r"\b(?:roll|make|give me|can you|could you|go ahead)\b[^.!]*(?:\?|$)|\?\s*$", re.I)


def attack_number(text):
    """The total in "does a 17 hit?" as a d20 result, or None."""
    m = _ATTACK_NUMBER.search(text or "")
    n = number(m.group("n")) if m else None
    if n is None or not 1 <= n <= 45:
        return None
    return {"kind": "d20", "die": None, "bonus": None, "total": n, "nat": None, "said": m.group(0)}


def unasked_roll(text):
    """A line that is clearly a roll although nobody asked for one: "17 plus 6", "I rolled a one", "D12 plus seven",
    "1d4 plus 4 plus 2d6", "Ten damage to the wolf". -> ('unknown', None) / ('damage', None) / None.
    Measurements ("five foot tall", "four hours") are left out."""
    text = text or ""
    if _MEASURE.search(text) or text.strip().endswith("?") and not re.search(r"\bdamage\b", text, re.I):
        return None
    rs = read_results(text)
    if any(r["kind"] == "d20" and r["die"] is not None and r["bonus"] is not None for r in rs):
        return ("unknown", None)
    if _ROLLED.search(text) and any(r["kind"] == "d20" for r in rs):
        return ("unknown", None)
    damage = [r for r in rs if r["kind"] == "damage"]
    if any(r["dice"] and (len(r["dice"]) > 1 or r["bonus"] is not None or r["dice"][0][0] > 1) for r in damage):
        return ("damage", None)
    if any(r["total"] is not None for r in damage) and re.search(r"\bdamage\b", text, re.I) and not _TAKEN.search(text):
        return ("damage", None)
    return None


def named(text, people):
    """The first person named in a line. people: {spoken name (any case): canonical name}. -> canonical or ''."""
    low = (text or "").casefold()
    best = None
    for spoken, canonical in (people or {}).items():
        k = str(spoken or "").casefold().strip()
        if len(k) < 3:
            continue
        m = re.search(rf"\b{re.escape(k)}\b", low)
        if m and (best is None or m.start() < best[0] or (m.start() == best[0] and len(k) > best[1])):
            best = (m.start(), len(k), canonical)
    return best[2] if best else ""


def dm_attack_check(text, speaker, dm):
    """('dm_attack', None) when the line is the DM attacking a player (said by the DM, or a player asking whether it
    gets their AC), ('attack', None) for a player asking whether their own roll hits, else None."""
    is_dm = speaker in dm
    if is_dm and _DM_ATTACK.search(text or ""):
        return ("dm_attack", None)
    if not is_dm and dm and _HIT_ME.search(text or ""):
        return ("dm_attack", None)
    if not is_dm and _DOES_HIT.search(text or ""):
        return ("attack", None)
    return None


def find_rolls(segments, window=45.0, lead=12.0, people=None, dm=(), late_total=30.0, wide_lead=30.0):
    """Roll requests and the results said after them.
    -> [{'start','end','edit_id','asked_by','check','ask_text','context','results':[...],'target','target_sid','ac_said'}]
    'context' is the text from a little before the ask to the end of the window (to spot the weapon or spell named).
    v2.40: a line that is clearly a roll starts a row even when nobody asked (unasked_roll); the DM's attacks are
    rows of their own (dm: the DM's speaker ids); people ({spoken name: canonical}) gives the person named in the
    request ('target') and in each result line ('named'); a plain number said within late_total seconds of damage
    dice without a total ("1d4 plus 4 plus 2d6" … "14") is that damage's total, for whoever rolled the dice."""
    ordered = sorted(segments, key=lambda s: float(s["start"]))
    dm = set(dm or ())
    events, current = [], None
    declared = None   # v2.46: (time, speaker, text) of "I attack the wolf"
    for i, seg in enumerate(ordered):
        text = str(seg.get("text") or "")
        speaker = seg.get("speaker_id", "")
        t = float(seg["start"])
        if speaker not in dm and _DECLARE.search(text) and not read_results(text):
            declared = (t, speaker, text.strip())
        pending = current and current.get("_pending")
        in_window = current is not None and (t - current["start"] <= window or bool(pending and t - pending["t"] <= late_total))
        if (in_window and declared and declared[1] == speaker and t - declared[0] <= 15 and speaker not in dm
                and current["asked_by"] != speaker and not (pending and pending["speaker"] == speaker)
                and any(r["kind"] == "d20" and r["total"] is not None for r in read_results(text))):
            in_window = False   # v2.46: someone else's roll was still open, but this player just said what they attack
        check = dm_attack_check(text, speaker, dm) if not in_window or (current and current["check"][0] != "dm_attack") else None
        if check and check[0] == "attack" and in_window:
            check = None                # "does that hit?" inside a roll already found belongs to it
        if not check:
            check = detect_check(text)
        unasked = False   # v2.43: a row nobody asked for shows "—" under Asked by
        if not check and not in_window and declared and declared[1] == speaker and t - declared[0] <= 15 and not counting(text):
            plain = [r for r in read_results(text) if r["kind"] == "d20" and r["total"] is not None and 5 <= r["total"] <= 45]
            if plain:   # v2.46: "I attack the wolf." … "So, that's a 13."
                check = ("attack", None); unasked = True
        if not check and not in_window:
            check = unasked_roll(text); unasked = bool(check)
        elif not check and current["check"][0] in ("damage", "dc") and unasked_roll(text) == ("unknown", None):
            check = ("unknown", None); unasked = True   # "17 plus 6" just after someone talked about damage is a new d20 roll
        if not check and _NAT.search(text) and not in_window:
            check = ("unknown", None); unasked = True   # a natural 20 / 1 said on its own is worth a row
        if check and check[0] != "dm_attack" and not unasked and read_results(text) and not _ASKED.search(text):
            unasked = True   # the line gives its own result ("Ten points of damage"): said, not asked for
        if check and check[0] == "damage" and in_window and current["check"][0] in ("attack", "spell_attack", "unknown", "dm_attack"):
            check = None                # "…and the damage" after an attack belongs to that attack
        if check and check[0] == "dc" and in_window:
            check = None
        if check:
            same = current and current["check"] == check and t - current["start"] < 8
            if not same:
                before = " ".join(str(s.get("text") or "") for s in ordered[max(0, i - 6):i] if t - float(s["start"]) <= lead)
                if declared and declared[1] == speaker and t - declared[0] <= 15 and declared[2] not in before:
                    before = (declared[2] + " " + before).strip()
                current = {"start": t, "end": float(seg["end"]), "edit_id": seg.get("_edit_id"), "asked_by": speaker,
                           "check": check, "ask_text": text.strip(), "context": (before + " " + text).strip(), "results": [],
                           "target": named(text, people), "target_sid": "", "ac_said": None, "_pending": None,
                           "unasked": unasked, "_asked_roll": None}
                if check[0] == "dm_attack":
                    if speaker not in dm:
                        current["target_sid"] = speaker      # "does it get my AC?"
                    else:                                     # the first player to answer
                        nxt = next((s for s in ordered[i + 1:] if float(s["start"]) - t <= 15
                                    and s.get("speaker_id", "") and s.get("speaker_id", "") not in dm), None)
                        current["target_sid"] = nxt.get("speaker_id", "") if nxt else ""
                events.append(current)
                in_window = True
            elif text.strip() not in current["context"]:
                current["context"] += " " + text.strip()
        if in_window:
            if seg is not ordered[0] and text.strip() and text.strip() not in current["context"]:
                current["context"] += " " + text.strip()
            kind = current["check"][0]
            if kind == "dm_attack":
                m = _AC_SAID.search(text)
                if m and speaker not in dm and current["ac_said"] is None:
                    current["ac_said"] = number(m.group("n"))
                if _WHAT_ROLL.search(text):
                    current["_asked_roll"] = t   # v2.46: "What did it roll?": the bare number said next is the DM's
                if speaker not in dm and _SHIELD.search(text) and not current.get("shield"):
                    current["shield"] = {"t": t, "speaker": speaker, "text": text.strip()}
            found = read_results(text)
            if kind in ("dm_attack", "attack") and not any(r["kind"] == "d20" for r in found) and attack_number(text):
                found.insert(0, attack_number(text))   # "does a 17 hit?"
            for r in found:
                if r["kind"] == "damage" and kind not in ("attack", "spell_attack", "damage", "unknown", "dm_attack"):
                    continue   # dice during a skill check or save are something else (a loot table, a d12 …)
                asked = current.get("_asked_roll")
                if (kind == "dm_attack" and dm and speaker not in dm and asked is not None and t - asked <= 8 and r["kind"] == "d20"
                        and r["die"] is None and r["bonus"] is None and not any(x["kind"] == "d20" for x in current["results"])):
                    # v2.46: "What did it roll?" … "22." The answer is the DM's number even when the line is labelled
                    # as a player (the speaker labels often get a quick answer wrong)
                    r = {**r, "dm_answer": True}
                    current["_asked_roll"] = None
                elif kind == "dm_attack" and dm and speaker not in dm:
                    continue   # the DM's roll is the DM's number; players answering ("16?") are not it
                if kind == "dm_attack" and _AC_SAID.search(text) and r["kind"] == "d20" and r["die"] is None:
                    continue   # "AC 16" is not the roll
                if kind == "damage" and r["kind"] == "d20" and r["die"] is None and r["total"] is not None:
                    r = {**r, "kind": "damage", "dice": [], "bonus": None}   # "Nine." after "roll damage"
                extra = {"speaker": speaker, "edit_id": seg.get("_edit_id"), "start": t, "end": float(seg["end"]),
                         "text": text.strip(), "named": named(text, people),
                         "about_other": bool(r["kind"] == "damage" and _ABOUT_OTHER.search(text))}
                pend = current["_pending"]
                plain = r["kind"] == "d20" and r["die"] is None and r["bonus"] is None and not r["nat"] and r["total"] is not None
                said_total = r["kind"] == "damage" and not r["dice"] and r["total"] is not None
                if pend and t - pend["t"] <= late_total and (plain or said_total) and seg.get("_edit_id") != pend["edit_id"]:
                    # the total of the damage dice said a moment ago, by the roller or read out by someone else
                    r = {**r, "kind": "damage", "dice": [], "bonus": None, "die": None}
                    extra.update(speaker=pend["speaker"], said_by=speaker, total_for=pend["said"], named=pend["named"])
                    current["_pending"] = None
                elif r["kind"] == "damage" and r["dice"] and r["total"] is None:
                    current["_pending"] = {"t": t, "speaker": speaker, "edit_id": seg.get("_edit_id"), "said": r["said"],
                                           "named": extra["named"]}
                elif r["kind"] == "damage" and r["total"] is not None:
                    current["_pending"] = None
                current["results"].append({**r, **extra})
                current["end"] = max(current["end"], float(seg["end"]))
        elif current is not None:
            current = None
    starts = [float(x["start"]) for x in ordered]
    import bisect
    for e in events:
        e.pop("_pending", None)
        e.pop("_asked_roll", None)
        # v2.41: the lines from 30 s before the roll to its end, to spot the spell cast and a critical hit
        lo = bisect.bisect_left(starts, e["start"] - wide_lead)
        hi = bisect.bisect_right(starts, e["end"])
        e["wide_context"] = " ".join(str(x.get("text") or "").strip() for x in ordered[lo:hi])
        # what the roller, the asker and the DM said from 10 s before to the last number (for advantage)
        who = {e["asked_by"]} | {r.get("speaker", "") for r in e["results"]} | dm
        last = max([r["end"] for r in e["results"]] or [e["end"] + 10])
        lo, hi = bisect.bisect_left(starts, e["start"] - 10), bisect.bisect_right(starts, last)
        e["adv_text"] = " ".join(str(x.get("text") or "").strip() for x in ordered[lo:hi] if x.get("speaker_id", "") in who)
        # v2.44: a roll nobody asked for with nothing about a roll in the 30 s before it: maybe a gap in the transcript
        if e.get("unasked"):
            lo = bisect.bisect_left(starts, e["start"] - 30)
            before = [str(x.get("text") or "") for x in ordered[lo:] if float(x["start"]) < e["start"]]
            e["no_lead"] = not any(detect_check(b) or _DM_ATTACK.search(b) or _CAST.search(b) or _LEAD.search(b) for b in before)
        if e["check"][0] == "dm_attack" and not e.get("shield"):
            last = max([r["end"] for r in e["results"]] or [e["end"]])
            lo = bisect.bisect_right(starts, e["start"])
            for x in ordered[lo:]:
                if float(x["start"]) - last > 15:
                    break
                if x.get("speaker_id", "") not in dm and _SHIELD.search(str(x.get("text") or "")):
                    e["shield"] = {"t": float(x["start"]), "speaker": x.get("speaker_id", ""), "text": str(x.get("text")).strip()}
                    break
        # v2.44: "What is your AC?" answered with a bare number ("16."): offered as the AC, never used on its own
        if e["check"][0] == "dm_attack" and e.get("ac_said") is None and _AC_ASKED.search(e["ask_text"]):
            lo = bisect.bisect_right(starts, e["start"])
            for x in ordered[lo:]:
                if float(x["start"]) - e["start"] > 10:
                    break
                m = _BARE_NUMBER.fullmatch(str(x.get("text") or "").strip())
                if m and x.get("speaker_id", "") not in dm and number(m.group("n")) and 5 <= number(m.group("n")) <= 30:
                    e["ac_answer"] = {"n": number(m.group("n")), "speaker": x.get("speaker_id", ""), "text": str(x.get("text")).strip()}
                    break
    return events


_LEAD = re.compile(r"\b(?:attack|attacks|hit|hits|roll|rolls|rolling|damage|swing|shoot|stab|slash|cast|spell|smit\w*|sneak|d\d{1,2}|dice)\b", re.I)
# v2.46: a player saying what they attack ("I attack the wolf"): the number they say next is that attack
_DECLARE = re.compile(r"\b(?:take a swing|swing at|swings? at|attack(?:ing)?|go(?:ing)? for (?:the|him|her|it|them)|shoot(?:ing)?|"
                      r"stab(?:bing)?|slash(?:ing)?|hit (?:it|him|her|them|the \w+) with|smit(?:e|ing))\b", re.I)
_WHAT_ROLL = re.compile(r"\b(?:what|how much) (?:did|does|do) (?:it|he|she|they|that|you) (?:roll|get)\b", re.I)
_SHIELD = re.compile(r"^\W*shield\W*$|\b(?:cast(?:ing|s)?|use|using|I'?ll|I) (?:the )?shield\b|\bshield spell\b", re.I)
_AC_ASKED = re.compile(r"\b(?:AC|armou?r class)\b", re.I)
_BARE_NUMBER = re.compile(rf"\W*(?:it'?s |my AC is |AC )?(?P<n>{_NUM})\W*", re.I)


def link_damage(rows, window=90.0):
    """v2.44: damage said as a roll of its own ("Ten points of damage") belongs to the attack the same
    character made just before it (within window seconds), so it can take that attack's spell and critical hit.
    rows: the roll finder's rows (type 'roll', kind, character / player, start, event).
    -> {damage event start: attack event}."""
    attacks = [x for x in rows if x.get("type") == "roll" and x.get("kind") == "roll"
               and x["event"]["check"][0] in ("attack", "spell_attack", "unknown")]
    out = {}
    for x in rows:
        if x.get("type") != "roll" or x.get("kind") != "damage" or x["event"]["check"][0] in ("attack", "spell_attack", "unknown", "dm_attack"):
            continue
        e = x["event"]
        if e["start"] in out:
            continue
        who = x.get("character") or x.get("player")
        if not who:
            continue
        before = [a for a in attacks if (a.get("character") or a.get("player")) == who and a["event"]["start"] < e["start"]
                  and e["start"] - a["event"]["start"] <= window]
        if before:
            out[e["start"]] = before[-1]["event"]
    return out


def repeat_flags(rows, window=120.0):
    """v2.44: a roll that says again a damage total already counted for the same character within window seconds
    ("Ten points of damage" ... then "You did 10 damage, didn't you?" in a roll of its own). Only the last
    total of each roll is counted, so the flag goes on that row (the one that would be added twice). Only flagged
    for you to check, never left out by itself. -> {row key: (earlier start, total)}."""
    groups = {}
    for x in sorted((x for x in rows if x.get("type") == "roll" and x.get("kind") == "damage"
                     and x.get("outcome") != "ignore"), key=lambda x: x["start"]):
        total = damage_total(x["result"]) if x.get("result") else None
        who = x.get("character") or x.get("player")
        if total is not None and who:
            groups.setdefault((x["event"]["start"], who), []).append((x, total))
    out, counted = {}, []   # counted: (start, who, total, event start) of each roll's last total
    for (ev, who), items in sorted(groups.items(), key=lambda kv: kv[1][0][0]["start"]):
        last, last_total = items[-1]
        said = {tot for _x, tot in items}
        hit = next((c for c in reversed(counted) if c[1] == who and c[3] != ev and c[2] in said
                    and items[0][0]["start"] - c[0] <= window), None)
        if hit:
            out[last["key"]] = (hit[0], hit[2])
        counted.append((last["start"], who, last_total, ev))
    return out


# v2.44: what "Asked for" can be set to (label, check)
def check_choices():
    out = [("Attack roll", ("attack", None)), ("Spell attack", ("spell_attack", None)), ("Damage", ("damage", None)),
           ("Initiative", ("initiative", None)), ("Death save", ("death", None)), ("Saving throw", ("save", None))]
    for code in ("STR", "DEX", "CON", "INT", "WIS", "CHA"):
        out.append((f"{code} save", ("save", code)))
    for code in ("STR", "DEX", "CON", "INT", "WIS", "CHA"):
        out.append((f"{code} check", ("check", code)))
    for name in sorted(set(SKILLS.values())):
        out.append((f"{name} check", ("skill", name)))
    out += [("Save DC said", ("dc", None)), ("Roll (check not said)", ("unknown", None))]
    return out


def check_from_label(label):
    return next((c for l, c in check_choices() if l == label), None)


# ---------- what the sheet says ----------------------------------------------------------------

def _mentioned_weapons(summary, context):
    text = (context or "").casefold()
    found = []
    for w in summary.get("weapons") or []:
        words = {w["type"].casefold()} | {p for p in re.findall(r"[a-z']{4,}", w["name"].casefold())}
        if any(re.search(rf"\b{re.escape(x)}s?\b", text) for x in words if x not in ("dagger,",)):
            found.append(w)
    return found


def _spell_named(summary, context):
    text = (context or "").casefold()
    return any(s.casefold() in text for s in summary.get("spells") or []) or bool(re.search(r"\bspell|cantrip|bolt|blast|ray\b", text))


_LEVEL = re.compile(r"\b(?:at\s+|a\s+)?(?:level|lvl)\s*(?P<n>[1-9]|one|two|three|four|five|six|seven|eight|nine)\b"
                    r"|\b(?P<m>[1-9])(?:st|nd|rd|th)[- ]level\b", re.I)
_CRIT = re.compile(r"\bcrit(?:s|ted|ical)?\b|\bnat(?:ural)?\s*(?:20|twenty)\b", re.I)
_CAST = re.compile(r"\bcast(?:s|ing)?\b|\bspell\b|\bcantrip\b|\bupcast\b", re.I)
_SPELL_STOP = {"protection", "spirit", "summon", "greater", "lesser", "conjure", "create", "detect", "dispel", "remove",
               "healing", "minor", "major", "mass", "wall", "guardian", "magic", "shield", "light", "word"}


def critical_said(text, result=None):
    """A critical hit said around the roll ("nat 20", "crit", "critical")."""
    return bool((result or {}).get("nat") == 20 or _CRIT.search(text or ""))


def spell_cast(summary, text, near=None):
    """{'names', 'level', 'label'} when a spell on the sheet is named in text (the full name, or a distinctive word of
    it such as "chromatic", which also catches "chromatic award"), or "cast" / "spell" is said in near (the roll's own
    lines; defaults to text); else None. level: "at level 2", "2nd-level"."""
    low = (text or "").casefold()
    names = []
    for sp in (summary or {}).get("spells") or []:
        name = sp.casefold()
        words = [w for w in re.findall(r"[a-z']+", name) if len(w) >= 6 and w not in _SPELL_STOP]
        if name in low or any(re.search(rf"\b{re.escape(w)}\b", low) for w in words):
            names.append(sp)
    if not names and not _CAST.search(text if near is None else near):
        return None
    m = _LEVEL.search(text or "")
    level = number(m.group("n") or m.group("m")) if m else None
    inside = " / ".join(names[:3]) + ("?" if names else "")
    if level:
        inside = (inside + " " if inside else "") + f"cast at level {level}"
    return {"names": names, "level": level, "label": "spell damage" + (f" ({inside})" if inside else "")}


def detect_advantage(event):
    """('adv' | 'dis' | 'both' | '', evidence) from what is said around a d20 roll: "with advantage", "I have
    disadvantage", "2d20 take the lower"… Both said (or "straight roll") -> 'both'. A negation ("no advantage",
    "don't have disadvantage") does not count."""
    if "adv_text" in event:
        text = event["adv_text"]
    else:
        text = " ".join([event.get("context") or ""] + [r.get("text") or "" for r in event.get("results") or []])
    found = {}
    for m in re.finditer(r"\b(dis)?advantage\b", text, re.I):
        before = text[max(0, m.start() - 22):m.start()].casefold()
        if re.search(r"\b(?:no|not|no longer have|don'?t have|doesn'?t have|don'?t get|without|lose|lost|cancels?|cancelled)\s+(?:any\s+|the\s+|your\s+)?$", before):
            continue
        end = re.search(r"[.!?]", text[m.end():])
        if end and end.group(0) == "?":
            continue   # asked about ("any advantage to Thunderwave?"), not had
        found.setdefault("dis" if m.group(1) else "adv", text[max(0, m.start() - 40):m.end() + 20].strip())
    if re.search(r"\bstraight roll\b", text, re.I) or len(found) == 2:
        return "both", next(iter(found.values()), "straight roll")
    if found:
        k = next(iter(found)); return k, found[k]
    m = re.search(r"\b(?:take the|took the|the) (higher|lower|highest|lowest)\b", text, re.I)
    if m and re.search(r"\b(?:2|two)\s*d\s?20s?\b|\brolled (?:twice|two)\b", text, re.I):
        return ("adv" if m.group(1).lower().startswith("high") else "dis"), m.group(0)
    return "", ""


def sheet(summary, check, context="", result=None, wide="", spells=None, crit=None):
    """What the snapshot says for this roll: {'options': {bonus: label}, 'label': str, 'damage': [...]}.
    options is empty when the sheet has nothing to compare with. wide (v2.41): the lines from 30 s before, used to
    spot a spell cast ('spell') and a critical hit ('crit') for damage."""
    if crit is None:   # v2.44: crit can come from the attack this damage belongs to
        crit = critical_said(context, result)   # the roll's own lines: a "nat 20" 30 s earlier may be someone else's
    if not summary:
        return {"options": {}, "label": "", "damage": [], "crit": crit}
    kind, what = check
    opts = {}
    try:
        if kind == "skill":
            opts[summary["skills"][what]["bonus"]] = what
        elif kind == "save":
            opts[summary["saves"][what]["bonus"]] = f"{what} save"
        elif kind == "check":
            opts[summary["modifiers"][what]] = what
        elif kind == "initiative":
            opts[summary["initiative"]] = "initiative"
        elif kind == "death":
            opts[0] = "death save"
    except (KeyError, TypeError):
        pass
    weapons = _mentioned_weapons(summary, context) or [w for w in summary.get("weapons") or [] if w.get("equipped")]
    if kind in ("attack", "unknown", "damage") or (result and result.get("kind") == "damage"):
        for w in weapons:
            opts.setdefault(w["attack"], w["name"])
            if opts[w["attack"]] != w["name"] and w["name"] not in opts[w["attack"]]:
                opts[w["attack"]] += f" / {w['name']}"
    if kind in ("spell_attack",) or (kind == "attack" and _spell_named(summary, context)):
        for s in summary.get("spellcasting") or []:
            opts.setdefault(s["attack"], "spell attack")
    if kind == "dc" or (result and result.get("kind") == "dc"):
        opts = {s["dc"]: "spell save DC" for s in summary.get("spellcasting") or []}
    damage = [{"name": w["name"], "dice": w["dice"], "bonus": w["damage_bonus"]} for w in weapons]
    label = " · ".join(f"{v} {('+' if k >= 0 else '')}{k}" if kind in ("attack", "spell_attack", "unknown", "damage", "dc")
                       else f"{'+' if k >= 0 else ''}{k}" for k, v in sorted(opts.items(), key=lambda kv: -kv[0]))
    if kind == "dc" or (result and result.get("kind") == "dc"):
        label = " · ".join(f"DC {k}" for k in opts)
    spell = None
    if result and result.get("kind") == "damage":
        label = " · ".join(f"{d['name']} {d['dice']}{('+' if d['bonus'] >= 0 else '')}{d['bonus'] if d['bonus'] else ''}".rstrip("+")
                           for d in damage)
        label += "".join(f" · {x['name']} +{x['dice']}" for x in summary.get("extra_damage") or [])
        spell = spell_cast(summary, wide or context, near=context)
        if spell:
            label = spell["label"]
            # v2.42: the spell's dice from an imported book, for this slot level (and doubled on a critical)
            from .spell_book import expected_dice, dice_text
            book = next(((spells or {}).get(n.casefold()) for n in spell["names"] if (spells or {}).get(n.casefold())), None)
            if book and book.get("dice"):
                exp = expected_dice(book, spell["level"], summary.get("level"), crit)
                plain = expected_dice(book, spell["level"], summary.get("level"), False)
                spell.update(book=book, expected=exp, plain=plain)
                at = f" at level {spell['level']}" if spell["level"] and spell["level"] > book["level"] else ""
                label = (f"spell damage ({book['name']}{at}: {dice_text(exp)}" + (f" {book['type']}" if book["type"] else "")
                         + (", critical" if crit else "") + ")")
                spell["label"] = label
    return {"options": opts, "label": label, "damage": damage, "extra": summary.get("extra_damage") or [],
            "spell": spell, "crit": crit}


def expected_bonus(summary, check):
    """The single sheet bonus for a check (skills, saves, abilities, initiative), or None."""
    opts = sheet(summary, check)["options"]
    return next(iter(opts)) if len(opts) == 1 and check[0] not in ("attack", "spell_attack", "unknown", "damage", "dc") else None


def _dice_text(dice):
    return " + ".join(f"{c}d{s}" for c, s in dice)


def _parse_dice(s):
    m = re.fullmatch(r"(\d+)d(\d+)", str(s or "").strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def assess(result, info):
    """A short note on one said result: '' when it fits, otherwise what looks odd (or what it included).
    info: from sheet(); for backwards use an int is taken as the single expected bonus."""
    if isinstance(info, int) or info is None:
        info = {"options": {} if info is None else {info: ""}, "damage": [], "extra": []}
    opts = info.get("options") or {}
    kind = result.get("kind", "d20")
    if kind == "dc":
        if opts and result.get("total") not in opts:
            return f"said DC {result['total']}, sheet DC {' / '.join(map(str, opts))}"
        return ""
    if kind == "damage":
        return _assess_damage(result, info)
    if result.get("nat") == 20:
        return "Natural 20"
    if result.get("nat") == 1:
        return "Natural 1"
    die, bonus, total = result.get("die"), result.get("bonus"), result.get("total")
    notes = []
    if die is not None and not 1 <= die <= 20:
        notes.append(f"{die} is not a d20 number")
    if bonus is not None and opts and bonus not in opts:
        notes.append(f"said {bonus:+d}, sheet " + " / ".join(f"{k:+d}" for k in sorted(opts, reverse=True)))
    if die is None and total is not None and opts:
        if not any(1 <= total - b <= 20 for b in opts):
            b = sorted(opts, reverse=True)[0]
            notes.append(f"{total} does not fit d20 {b:+d} (would need a {total - b})")
    return "; ".join(notes)


def _assess_damage(result, info):
    weapons, extras = info.get("damage") or [], info.get("extra") or []
    dice, bonus, total = result.get("dice") or [], result.get("bonus"), result.get("total")
    notes = []
    crit = info.get("crit")
    if info.get("spell"):
        sp = info["spell"]
        exp = sp.get("expected")
        if not exp:   # no imported book has this spell's text: nothing to compare with
            return "; ".join([sp["label"] + ": not compared with weapons"] + (["critical hit: damage dice doubled"] if crit else []))
        from .spell_book import dice_text
        said = sorted(dice)
        if said:
            if said == sorted(exp):
                return sp["label"] + ": fits"
            doubled = sorted((2 * c, s_) for c, s_ in sp.get("plain") or [])
            if not crit and said == doubled:
                return f"spell damage: {dice_text(dice)} is {sp['book']['name']} doubled: a critical hit?"
            return f"said {dice_text(dice)}, {sp['label'][len('spell damage ('):-1]}"
        if total is not None:
            lo, hi = sum(c for c, _s in exp), sum(c * s_ for c, s_ in exp)
            if not lo <= total <= hi:
                return f"{total} is outside {dice_text(exp)} ({lo}–{hi}) for {sp['book']['name']}"
            return sp["label"] + ": fits"
        return sp["label"]
    if crit:
        notes.append("critical hit: damage dice doubled")
        weapons = weapons + [{**w, "dice": f"{2 * _parse_dice(w['dice'])[0]}d{_parse_dice(w['dice'])[1]}"}
                             for w in weapons if _parse_dice(w["dice"])]
        extras = extras + [{**x, "dice": f"{2 * _parse_dice(x['dice'])[0]}d{_parse_dice(x['dice'])[1]}"}
                           for x in extras if _parse_dice(x["dice"])]
    if dice:
        extra_names = []
        rest = list(dice)
        for x in extras:
            xd = _parse_dice(x["dice"])
            if xd and xd in rest:
                rest.remove(xd); extra_names.append(f"{x['name']} {x['dice']}")
        fits = [w for w in weapons if _parse_dice(w["dice"]) in rest]
        if weapons and rest:
            if not fits:
                notes.append(f"{_dice_text(rest)} is not a weapon die here (" + ", ".join(f"{w['name']} {w['dice']}" for w in weapons) + "): spell or other dice?")
            elif bonus is not None and all(w["bonus"] != bonus for w in fits):
                notes.append(f"said +{bonus}, sheet " + " / ".join(f"{w['name']} {w['dice']}{w['bonus']:+d}" for w in fits))
        if extra_names:
            notes.append("includes " + ", ".join(extra_names))
    elif total is not None and weapons:
        lo = min(_parse_dice(w["dice"])[0] + w["bonus"] for w in weapons if _parse_dice(w["dice"]))
        hi = 0
        for w in weapons:
            d = _parse_dice(w["dice"])
            if d:
                extra_max = sum(_parse_dice(x["dice"])[0] * _parse_dice(x["dice"])[1] for x in extras if _parse_dice(x["dice"]))
                hi = max(hi, 2 * (d[0] * d[1] + extra_max) + w["bonus"])   # a critical with every damage feature
        if total < max(1, lo) or total > hi:
            notes.append(f"{total} is outside what the weapons here can do ({max(1, lo)}–{hi}, counting a critical and "
                         + (", ".join(x["name"] for x in extras) or "no extra dice") + ")")
    return "; ".join(notes)


def check_label(check):
    kind, what = check
    return {"skill": f"{what} check", "check": f"{what} check", "save": f"{what} save", "initiative": "Initiative",
            "death": "Death save", "attack": "Attack roll", "spell_attack": "Spell attack", "damage": "Damage",
            "dc": "Save DC said", "unknown": "Roll (check not said)", "dm_attack": "DM attack"}.get(kind, kind) \
        if not (kind == "save" and not what) else "Saving throw"


def snapshot_for(snapshots, when_iso):
    """The snapshot first seen on or before the session date, else the earliest one (with a note).
    snapshots: newest first, as Database.character_snapshots returns them. -> (snapshot or None, note)."""
    if not snapshots:
        return None, "no D&D Beyond snapshot"
    before = [s for s in snapshots if when_iso and s["fetched_at"][:10] <= when_iso[:10]]
    if before:
        return before[0], f"sheet as of {before[0]['fetched_at'][:10]}"
    first = snapshots[-1]
    return first, f"sheet from {first['fetched_at'][:10]}, after this session: numbers may have changed since"


# ---------- pass / fail and damage taken (v2.39) -------------------------------------------------
# What the table says after a roll. Failure words are checked first ("doesn't hit" contains "hit").
_FAIL = re.compile(r"\b(miss(?:es|ed)?|whiff(?:s|ed)?|bounces? off|bounced off|deflect(?:s|ed)?|glances? off|glanced off|"
                   r"doesn'?t (?:quite )?(?:hit|do enough|work|beat|make it|get the job done)|didn'?t (?:hit|work|make it|beat|get the job done)|"
                   r"(?:ain'?t|isn'?t|is not|not) (?:going to |gonna )?hit(?:ting)?|"
                   r"you don'?t (?:see|find|notice|hit|manage)|not enough|fail(?:s|ed)?|no luck|"
                   r"(?:it |he |she |they )?(?:blocks?|blocked|parr(?:y|ies|ied)|resist(?:s|ed)?)|too low|nothing happens|you see nothing)\b", re.I)
_PASS = re.compile(r"(\b(?:it|that|you|he|she) hits?\b(?! for)|\bwell hit\b|\bthat'?s a hit\b|\bconnects?\b|\bthat'?ll do\b|\bthat works\b|"
                   r"\byou(?:'re| are) in(?=[,.!]|\s*$)|\bsucceed(?:s|ed)?\b|\bsuccess\b|\bpass(?:es|ed)?\b|\bmade it\b|"
                   r"\byou (?:see|notice|spot|find|manage|recall|remember)\b|\b(?:we|i) (?:all )?believe you\b|\bpretty good\b|"
                   r"\b(?:great|good|nice) roll\b|\bbeats? (?:it|that|his|her|the)\b|\broll (?:for |me )?(?:your )?damage\b|"
                   r"\bthat kills\b|\byou kill\b|\bit dies\b)", re.I)
_TAKEN = re.compile(rf"\b(?:take|takes|took|taking|suffer|suffers)\s+(?:a\s+)?(?P<n>{_NUM})\s*(?:points? of\s*)?(?:\w+\s)?damage\b"
                    rf"|\b(?P<m>{_NUM})\s*(?:points? of\s*)?(?:\w+\s)?damage\s+(?:to you|as you|on you|to him|to her)\b", re.I)
_DEALT_BY_YOU = re.compile(rf"\byou\s+(?:do|deal|did|dealt|hit (?:it |him |her |them )?for)\s+(?P<n>{_NUM})\b", re.I)


def detect_outcome(event, segments, window=20.0):
    """('pass'|'fail', evidence text, edit_id) or (None, '', None) from what is said after the roll.
    A natural 20 / 1 decides it; damage said after an attack means it hit."""
    kind = event["check"][0]
    for r in event["results"]:
        if r.get("nat") == 20:
            return "pass", r["text"], r.get("edit_id")
        if r.get("nat") == 1:
            return "fail", r["text"], r.get("edit_id")
    if kind in ("attack", "spell_attack", "unknown", "dm_attack"):
        for r in event["results"]:
            if r.get("kind") == "damage":
                return "pass", r["text"], r.get("edit_id")
    if kind in ("damage", "dc"):
        return None, "", None
    start = max([event["start"]] + [r["start"] for r in event["results"]])
    result_ids = {r.get("edit_id") for r in event["results"]}
    for seg in sorted(segments, key=lambda s: float(s["start"])):
        t = float(seg["start"])
        if t <= start or seg.get("_edit_id") in result_ids:
            continue
        if t > start + window:
            break
        text = str(seg.get("text") or "")
        if detect_check(text) and t > start + 2:
            break   # the next roll has started
        if text.strip().endswith("?"):
            continue   # a question ("Can you see…?") is not an answer
        if _FAIL.search(text):
            return "fail", text.strip(), seg.get("_edit_id")
        if kind == "dm_attack" and _TAKEN.search(text):
            return "pass", text.strip(), seg.get("_edit_id")   # "you take 6 damage": the DM's attack hit
        if _PASS.search(text):
            return "pass", text.strip(), seg.get("_edit_id")
    return None, "", None


def find_damage_taken(segments, names=()):
    """Lines that say someone takes damage: [{'start','end','edit_id','speaker','amount','target','text'}].
    target is a name mentioned in the line (characters / players given in names), else '' (said as "you")."""
    out = []
    lowered = [(n, n.casefold()) for n in names if n]
    for seg in sorted(segments, key=lambda s: float(s["start"])):
        text = str(seg.get("text") or "")
        m = _TAKEN.search(text)
        if not m:
            continue
        n = number(m.group("n") or m.group("m"))
        if n is None or not 1 <= n <= 150:
            continue
        low = text.casefold()
        target = next((n_ for n_, l in lowered if re.search(rf"\b{re.escape(l)}\b", low)), "")
        out.append({"start": float(seg["start"]), "end": float(seg["end"]), "edit_id": seg.get("_edit_id"),
                    "speaker": seg.get("speaker_id", ""), "amount": n, "target": target, "text": text.strip()})
    return out


def damage_total(result):
    """A damage number that can be added up: a said total, or None (dice without a total)."""
    if result.get("kind") != "damage":
        return None
    return result.get("total")


def roll_key(event, result_index):
    """A stable-enough key for one row (to keep a pass/fail the user set)."""
    return f"{event['start']:.1f}|{event['check'][0]}|{event['check'][1] or ''}|{result_index}"


def ac_outcome(total, ac):
    """The DM's attack total against a character's AC: 'pass' (it hits) / 'fail' (it misses) / None."""
    if total is None or ac is None:
        return None
    return "pass" if total >= ac else "fail"


def contributions(r):
    """v2.44: what one roll-log row adds to the summary: [(who, column, amount)]. session_stats adds these up;
    stat_rows lists the rows behind one number (double-click in the summary)."""
    who = r.get("character") or r.get("player")
    kind = r.get("kind")
    if kind in ("ignored", "damage_repeat", "dm_attack_repeat", "dm_damage"):
        return []
    if kind == "dm_attack":
        # v2.46: the DM's own row: attacks, hits, misses and natural 20s / 1s
        out = [(DM_ROW, "attacks", 1)]
        if r.get("outcome") == "pass":
            out.append((DM_ROW, "hits", 1))
        elif r.get("outcome") == "fail":
            out.append((DM_ROW, "misses", 1))
        if r.get("nat") in (20, 1):
            out.append((DM_ROW, "nat20" if r["nat"] == 20 else "nat1", int(r.get("nat_dice") or 1)))
        if not who:
            return out
        out.append((who, "attacked", 1))
        if r.get("outcome") == "pass":
            out.append((who, "hit_by_dm", 1))
        elif r.get("outcome") == "fail":
            out.append((who, "missed_by_dm", 1))
        return out
    if r.get("role") == "DM":
        if kind != "roll":
            return []
        out = [(DM_ROW, "rolls", 1), (DM_ROW, {"pass": "pass", "fail": "fail"}.get(r.get("outcome"), "unknown"), 1)]
        if r.get("nat") in (20, 1):
            out.append((DM_ROW, "nat20" if r["nat"] == 20 else "nat1", int(r.get("nat_dice") or 1)))
        return out
    if kind == "cancelled":
        return [(who, "no_roll", 1)] if who and r.get("check_kind") != "damage" else []
    if kind == "taken":
        return [(r["target"], "damage_taken", int(r.get("amount") or 0))] if r.get("target") else []
    who = who or "(not set)"
    if kind == "damage":
        if r.get("damage") is not None:
            return [(who, "damage_dealt", int(r["damage"]))]
        return [(who, "damage_rolls_no_total", 1)]
    if kind != "roll":
        return []
    out = [(who, "rolls", 1), (who, {"pass": "pass", "fail": "fail"}.get(r.get("outcome"), "unknown"), 1)]
    if r.get("adv") == "adv":
        out.append((who, "advantage", 1))
    elif r.get("adv") == "dis":
        out.append((who, "disadvantage", 1))
    if r.get("nat") == 20:
        out.append((who, "nat20", int(r.get("nat_dice") or 1)))   # v2.43: "double nat 20" counts both dice
    if r.get("nat") == 1:
        out.append((who, "nat1", int(r.get("nat_dice") or 1)))
    if r.get("check_kind") in ("attack", "spell_attack"):
        out.append((who, "attacks", 1))
        if r.get("outcome") == "pass":
            out.append((who, "hits", 1))
        elif r.get("outcome") == "fail":
            out.append((who, "misses", 1))
    return out


DM_ROW = "DM"   # v2.46

STAT_KEYS = ("rolls", "pass", "fail", "unknown", "nat20", "nat1", "attacks", "hits", "misses", "damage_dealt",
             "damage_rolls_no_total", "damage_taken", "attacked", "hit_by_dm", "missed_by_dm", "no_roll", "advantage", "disadvantage")


def session_stats(rows):
    """Per character (or player when no character is set) from the roll log rows:
    {who: {'rolls','pass','fail','unknown','nat20','nat1','attacks','hits','misses','damage_dealt','damage_rolls_no_total',
           'damage_taken','attacked','hit_by_dm','missed_by_dm','no_roll','advantage','disadvantage'}}.
    Rows are what the roll finder saves: kind 'roll' | 'damage' | 'damage_repeat' | 'taken' | 'dm_attack' | 'cancelled'
    | 'ignored' (v2.44). A cancelled roll ("No roll") is only counted under no_roll; the DM attacking a character counts
    under that character; an ignored number is not counted."""
    stats = {}
    for r in rows:
        for who, key, amount in contributions(r):
            stats.setdefault(who, {k: 0 for k in STAT_KEYS})[key] += amount
    return stats


_BOUNCE = re.compile(r"\b(?:leaps?|leapt|jumps?|bounces?|bounced|ricochets?|arcs?) (?:to|onto|over|off|at)\b", re.I)


def notable_moments(rows):
    """v2.46: the rare things in a session (or campaign) worth remembering, from the roll log:
    [{'start', 'who', 'what', 'session'}] in time order. Two natural 20s (or 1s) on one roll, natural 20s or 1s twice
    in a row for one character, a Shield that made the DM miss, a big hit (30+ damage said), a spell that leaped to
    another target (Chromatic Orb)."""
    out = []
    last_nat = {}
    for r in sorted(rows, key=lambda r: (str(r.get("session") or ""), float(r.get("start") or 0))):
        if r.get("kind") in ("ignored", "cancelled", "damage_repeat", "dm_attack_repeat"):
            continue
        who = "DM" if r.get("role") == "DM" else (r.get("character") or r.get("player") or ("DM" if r.get("kind") == "dm_attack" else ""))
        base = {"start": float(r.get("start") or 0), "session": r.get("session", ""), "who": who or "(not set)"}
        nat, dice = r.get("nat"), int(r.get("nat_dice") or 1)
        if nat in (20, 1) and dice >= 2:
            out.append(dict(base, what=f"two natural {nat}s on one roll ({r.get('check') or 'roll'})"))
        elif nat in (20, 1) and who:
            key = (r.get("session", ""), who)
            prev = last_nat.get(key)
            if prev and prev[0] == nat:
                out.append(dict(base, what=f"natural {nat} again (the last one was at {int(prev[1]) // 3600}:"
                                           f"{int(prev[1]) % 3600 // 60:02d}:{int(prev[1]) % 60:02d})"))
            last_nat[key] = (nat, base["start"])
        if r.get("kind") == "dm_attack" and r.get("shield") and r.get("outcome") == "fail":
            out.append(dict(base, who=r.get("character") or r.get("player") or "(not set)", what="Shield turned the DM's hit into a miss"))
        if r.get("kind") == "damage" and (r.get("damage") or 0) >= 30:
            out.append(dict(base, what=f"{r['damage']} damage in one go"))
        if _BOUNCE.search(" ".join(str(r.get(k) or "") for k in ("text", "note", "context"))) and re.search(
                r"chromatic orb|chain lightning|ricochet|bounce", " ".join(str(r.get(k) or "") for k in ("text", "note", "context")), re.I):
            out.append(dict(base, what="the spell leaped to another target"))
    seen, unique = set(), []
    for m in out:
        k = (m["session"], round(m["start"]), m["what"])
        if k not in seen:
            seen.add(k); unique.append(m)
    return unique


def stat_rows(rows, who, key):
    """v2.44: the roll-log rows behind one summary number."""
    return [r for r in rows if any(w == who and k == key for w, k, _a in contributions(r))]
