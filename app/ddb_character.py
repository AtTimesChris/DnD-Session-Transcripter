"""A dated summary of a D&D Beyond character (v2.34, extended in v2.35). Qt-free and unit-tested.

D&D Beyond's character data holds the inputs, not the finished sheet numbers, so they are worked out here
the way the sheet does for ordinary cases: ability scores and modifiers, proficiency bonus, saving throws,
skills (proficiency, expertise, Jack of All Trades), passive Perception / Investigation / Insight,
initiative, speed, spell save DC and attack, hit points and Armour Class.

v2.35 fixes: modifiers from items only count when the item is equipped (and attuned if it needs it) —
D&D Beyond lists the modifiers of every carried item; and "set AC to N" is a minimum (the sheet keeps the
higher of N and the worked-out AC), which is how Pip's sheet shows 16, not 20.

Armour Class is still labelled an estimate and lists each step, because homebrew can do things this does
not know. Snapshots are stored per refresh, so session notes can use the sheet as it was then.
"""
import re

from .ddb_spells_items import parse_character, GEAR

STATS = {1: "STR", 2: "DEX", 3: "CON", 4: "INT", 5: "WIS", 6: "CHA"}
STAT_NAMES = {1: "strength", 2: "dexterity", 3: "constitution", 4: "intelligence", 5: "wisdom", 6: "charisma"}
SKILLS = {"acrobatics": 2, "animal-handling": 5, "arcana": 4, "athletics": 1, "deception": 6, "history": 4,
          "insight": 5, "intimidation": 6, "investigation": 4, "medicine": 5, "nature": 4, "perception": 5,
          "performance": 6, "persuasion": 6, "religion": 4, "sleight-of-hand": 2, "stealth": 2, "survival": 5}
_ARMOUR_TYPES = {1: "light", 2: "medium", 3: "heavy"}


def _mod(score):
    return (int(score) - 10) // 2


def _val(m):
    return int(m.get("value") or m.get("fixedValue") or 0)


def _title(slug):
    return slug.replace("-", " ").title().replace("Of", "of")


def _items(data):
    return {(it.get("definition") or {}).get("id"): it for it in data.get("inventory") or []}


def active_modifiers(data):
    """Every modifier that applies now. Item modifiers only when that item is equipped, and attuned if the
    modifier needs attunement (D&D Beyond lists modifiers of every carried item)."""
    items = _items(data)
    out = []
    for source, mods in (data.get("modifiers") or {}).items():
        for m in mods or []:
            if not isinstance(m, dict):
                continue
            if source == "item":
                it = items.get(m.get("componentId"))
                if it is None or not it.get("equipped"):
                    continue
                if m.get("requiresAttunement") and not it.get("isAttuned"):
                    continue
            out.append({**m, "_source": source})
    return out


def _source_name(m, items):
    it = items.get(m.get("componentId"))
    return ((it or {}).get("definition") or {}).get("name") or m["_source"]


def ability_scores(data, mods=None):
    mods = mods if mods is not None else active_modifiers(data)
    base = {s["id"]: s.get("value") for s in data.get("stats") or [] if isinstance(s, dict)}
    bonus = {s["id"]: s.get("value") for s in data.get("bonusStats") or [] if isinstance(s, dict)}
    override = {s["id"]: s.get("value") for s in data.get("overrideStats") or [] if isinstance(s, dict)}
    scores = {}
    for sid, label in STATS.items():
        if override.get(sid):
            scores[label] = int(override[sid]); continue
        value = int(base.get(sid) or 10) + int(bonus.get(sid) or 0)
        sets = []
        for m in mods:
            if m.get("subType") == f"{STAT_NAMES[sid]}-score":
                if m.get("type") == "bonus":
                    value += _val(m)
                elif m.get("type") == "set":
                    sets.append(_val(m))
        scores[label] = max([value] + sets)
    return scores


def proficiency_bonus(level):
    return 2 + (max(1, level) - 1) // 4


def hit_points(data, scores, level, mods=None):
    mods = mods if mods is not None else active_modifiers(data)
    if data.get("overrideHitPoints"):
        hp_max = int(data["overrideHitPoints"])
    else:
        per_level = sum(_val(m) for m in mods if m.get("type") == "bonus" and m.get("subType") == "hit-points-per-level")
        hp_max = (int(data.get("baseHitPoints") or 0) + int(data.get("bonusHitPoints") or 0)
                  + (_mod(scores["CON"]) + per_level) * max(1, level))
    removed = int(data.get("removedHitPoints") or 0)
    return {"hp_max": hp_max, "hp_current": max(0, hp_max - removed), "hp_temp": int(data.get("temporaryHitPoints") or 0)}


def armour_class(data, scores, mods=None):
    """(estimate, [steps])."""
    mods = mods if mods is not None else active_modifiers(data)
    items = _items(data)
    dex = _mod(scores["DEX"])
    worn = [it for it in data.get("inventory") or [] if it.get("equipped") and (it.get("definition") or {}).get("armorTypeId") in (1, 2, 3)]
    shield = [it for it in data.get("inventory") or [] if it.get("equipped") and (it.get("definition") or {}).get("armorTypeId") == 4]
    steps = []
    if worn:
        d = worn[0]["definition"]; kind = d.get("armorTypeId"); base = int(d.get("armorClass") or 10)
        add = dex if kind == 1 else (min(dex, 2) if kind == 2 else 0)
        ac = base + add
        steps.append(f"{d.get('name')} ({_ARMOUR_TYPES[kind]}) {base}" + (f" + DEX {add}" if kind in (1, 2) else ""))
    else:
        ac = 10 + dex; steps.append(f"no armour: 10 + DEX {dex}")
        for m in mods:   # unarmoured defence (barbarian CON, monk WIS) and spell-style bases (Mage Armor 13 + DEX)
            if m.get("subType") == "unarmored-armor-class":
                if m.get("type") == "bonus" and m.get("statId") in STATS:
                    extra = _mod(scores[STATS[m["statId"]]]); ac += extra; steps.append(f"+ {STATS[m['statId']]} {extra} (unarmoured defence)")
                elif m.get("type") == "set" and _val(m):
                    new = _val(m) + dex
                    if new > ac:
                        ac = new; steps.append(f"base {_val(m)} + DEX {dex} ({_source_name(m, items)})")
    if shield:
        v = int(shield[0]["definition"].get("armorClass") or 2); ac += v; steps.append(f"+ {v} ({shield[0]['definition'].get('name')})")
    for m in mods:
        if m.get("subType") == "armor-class" and m.get("type") == "bonus" and _val(m):
            ac += _val(m); steps.append(f"+ {_val(m)} ({_source_name(m, items)})")
    for m in mods:   # "set AC to N" is a floor: the sheet keeps the higher value
        if m.get("subType") == "armor-class" and m.get("type") == "set" and _val(m):
            if _val(m) > ac:
                steps.append(f"raised to {_val(m)} by {_source_name(m, items)}"); ac = _val(m)
            else:
                steps.append(f"({_source_name(m, items)} sets at least {_val(m)}: already higher)")
    return ac, steps


def _restricted(m):
    r = str(m.get("restriction") or "").strip()
    return f" ({r})" if r else ""


def checks(data, scores, level, mods=None):
    """Saving throws, skills, passives, initiative, and the advantage notes, as the sheet adds them up."""
    mods = mods if mods is not None else active_modifiers(data)
    items = _items(data)
    pb = proficiency_bonus(level)
    has = lambda t, sub: any(m.get("type") == t and m.get("subType") == sub for m in mods)
    bonus = lambda sub: sum(_val(m) for m in mods if m.get("type") == "bonus" and m.get("subType") == sub)
    half_checks = has("half-proficiency", "ability-checks")   # Jack of All Trades
    saves = {}
    for sid, label in STATS.items():
        prof = has("proficiency", f"{STAT_NAMES[sid]}-saving-throws")
        saves[label] = {"bonus": _mod(scores[label]) + (pb if prof else 0) + bonus(f"{STAT_NAMES[sid]}-saving-throws") + bonus("saving-throws"),
                        "proficient": prof}
    skills = {}
    for slug, sid in SKILLS.items():
        level_ = "expertise" if has("expertise", slug) else ("proficient" if has("proficiency", slug) else
                                                              ("half" if half_checks or has("half-proficiency", slug) else ""))
        add = {"expertise": 2 * pb, "proficient": pb, "half": pb // 2, "": 0}[level_]
        skills[_title(slug)] = {"bonus": _mod(scores[STATS[sid]]) + add + bonus(slug) + bonus("ability-checks") + bonus("skill-checks"),
                                "stat": STATS[sid], "level": level_}
    passive = {k: 10 + skills[k]["bonus"] + bonus(f"passive-{k.lower()}") for k in ("Perception", "Investigation", "Insight")}
    initiative = _mod(scores["DEX"]) + bonus("initiative") + (pb if has("proficiency", "initiative") else 0)
    notes = []
    for m in mods:
        if m.get("type") in ("advantage", "disadvantage"):
            notes.append(f"{m['type'].title()} on {_title(m.get('subType') or '')}{_restricted(m)} — {_source_name(m, items)}")
    for c in data.get("customProficiencies") or []:
        if c.get("name"):
            notes.append(f"Custom: {c['name']}" + (f" — {c.get('notes') or c.get('description')}" if (c.get("notes") or c.get("description")) else ""))
    return {"proficiency_bonus": pb, "saves": saves, "skills": skills, "passive": passive, "initiative": initiative,
            "check_notes": notes}


def proficiencies(mods):
    skills = set(SKILLS)
    saves = {f"{n}-saving-throws" for n in STAT_NAMES.values()}
    prof = sorted({_title(m["subType"]) for m in mods if m.get("type") == "proficiency" and m.get("subType")
                   and m["subType"] not in skills and m["subType"] not in saves and m["subType"] != "initiative"})
    languages = sorted({_title(m["subType"]) for m in mods if m.get("type") == "language" and m.get("subType")})
    return prof, languages


def speed(data, mods):
    race = data.get("race") if isinstance(data.get("race"), dict) else {}
    walk = int((((race.get("weightSpeeds") or {}).get("normal") or {}).get("walk")) or 30)
    sets = [_val(m) for m in mods if m.get("type") == "set" and m.get("subType") in ("speed", "innate-speed-walking")]
    walk = max([walk] + sets) + sum(_val(m) for m in mods if m.get("type") == "bonus" and m.get("subType") in ("speed", "speed-walking"))
    return walk


def spellcasting(data, scores, level):
    """[(class, ability, save DC, attack bonus)] for classes that cast spells."""
    pb = proficiency_bonus(level)
    out = []
    for c in data.get("classes") or []:
        d, sub = c.get("definition") or {}, c.get("subclassDefinition") or {}
        sid = d.get("spellCastingAbilityId") or sub.get("spellCastingAbilityId")
        if sid in STATS:
            m = _mod(scores[STATS[sid]])
            out.append({"class": d.get("name", ""), "ability": STATS[sid], "dc": 8 + pb + m, "attack": pb + m})
    return out


def weapons(data, scores, level, mods=None):
    """Attack bonus and damage per weapon carried (v2.38), the way the sheet adds them up for ordinary weapons:
    ability = DEX for ranged, the better of STR/DEX for Finesse, else STR; + proficiency bonus if proficient
    (simple / martial weapons, or that weapon by name); + magic bonus. Damage = dice + ability modifier + magic.
    -> [{'name','type','equipped','attack','dice','damage_bonus','damage_type','ability','proficient','magic','properties'}]"""
    mods = mods if mods is not None else active_modifiers(data)
    pb = proficiency_bonus(level)
    profs = {m.get("subType") for m in mods if m.get("type") == "proficiency"}
    out, seen = [], set()
    for it in data.get("inventory") or []:
        d = it.get("definition") or {}
        dmg = d.get("damage") or {}
        if not dmg.get("diceString") or d.get("attackType") not in (1, 2):
            continue
        props = [p.get("name", "") for p in d.get("properties") or []]
        if d.get("attackType") == 2:
            ability = "DEX"
        elif "Finesse" in props:
            ability = "DEX" if _mod(scores["DEX"]) >= _mod(scores["STR"]) else "STR"
        else:
            ability = "STR"
        kind = str(d.get("type") or d.get("name") or "").lower().replace(" ", "-")
        proficient = ("simple-weapons" in profs and d.get("categoryId") == 1) or ("martial-weapons" in profs and d.get("categoryId") == 2) \
            or kind in profs or kind + "s" in profs
        magic = sum(int(m.get("value") or 0) for m in d.get("grantedModifiers") or [] if m.get("type") == "bonus" and m.get("subType") == "magic")
        mod = _mod(scores[ability])
        name = d.get("name", "")
        key = (name, it.get("equipped"))
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "type": d.get("type") or name, "equipped": bool(it.get("equipped")),
                    "attack": mod + (pb if proficient else 0) + magic, "dice": dmg.get("diceString"),
                    "damage_bonus": mod + magic, "damage_type": d.get("damageType") or "", "ability": ability,
                    "proficient": bool(proficient), "magic": magic, "properties": props})
    # one row per weapon name: an equipped copy wins
    best = {}
    for w in out:
        if w["name"] not in best or (w["equipped"] and not best[w["name"]]["equipped"]):
            best[w["name"]] = w
    return sorted(best.values(), key=lambda w: (not w["equipped"], w["name"].casefold()))


def extra_damage(data):
    """Features that add damage dice (Sneak Attack 2d6, Divine Smite …) as listed in the character's actions."""
    out = []
    for source, acts in (data.get("actions") or {}).items():
        for a in acts or []:
            dice = (a.get("dice") or {}).get("diceString")
            name = a.get("name") or ""
            if dice and name and not re.search(r"inspiration|healing|hit dice", name, re.I):
                out.append({"name": name, "dice": dice})
    return out


def summarize(payload):
    """The parts of a character worth keeping per refresh."""
    data = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
    mods = active_modifiers(data)
    classes = [(c.get("definition") or {}).get("name", "") + (f" ({(c.get('subclassDefinition') or {}).get('name')})" if c.get("subclassDefinition") else "")
               + f" {c.get('level', 0)}" for c in data.get("classes") or []]
    level = sum(int(c.get("level") or 0) for c in data.get("classes") or [])
    scores = ability_scores(data, mods)
    ac, steps = armour_class(data, scores, mods)
    who, rows = parse_character(payload)
    race = data.get("race") if isinstance(data.get("race"), dict) else {}
    prof, languages = proficiencies(mods)
    return {"name": who or str(data.get("name") or ""), "level": level, "classes": " / ".join(classes),
            "race": race.get("fullName") or race.get("baseRaceName") or "",
            **hit_points(data, scores, level, mods), "ac_estimate": ac, "ac_steps": steps, "scores": scores,
            "modifiers": {k: _mod(v) for k, v in scores.items()},
            **checks(data, scores, level, mods), "speed": speed(data, mods), "spellcasting": spellcasting(data, scores, level),
            "proficiencies": prof, "languages": languages,
            "weapons": weapons(data, scores, level, mods), "extra_damage": extra_damage(data),
            "spells": sorted(r["name"] for r in rows if r["category"] == "Spell"),
            "items": sorted(r["name"] for r in rows if r["category"] in ("Item", GEAR)),
            "magic_items": sorted(r["name"] for r in rows if r["category"] == "Item"),
            "feats": sorted(r["name"] for r in rows if r["category"] == "Feat")}


def _signed(n):
    return f"+{n}" if int(n) >= 0 else str(n)


def changes(old, new):
    """What changed between two summaries, as short lines (empty list when nothing did). Fields an older
    snapshot did not record yet are not compared."""
    if not old:
        return []
    out = []
    for key, label in (("level", "Level"), ("hp_max", "Max HP"), ("ac_estimate", "AC (estimate)"),
                       ("proficiency_bonus", "Proficiency bonus"), ("speed", "Speed"), ("initiative", "Initiative")):
        if key in old and old.get(key) != new.get(key):
            out.append(f"{label} {old.get(key)} → {new.get(key)}")
    for label in STATS.values():
        a, b = (old.get("scores") or {}).get(label), (new.get("scores") or {}).get(label)
        if a is not None and a != b:
            out.append(f"{label} {a} → {b}")
    for group, label in (("saves", "save"), ("skills", "")):
        if group not in old:
            continue
        for name, now in (new.get(group) or {}).items():
            was = (old.get(group) or {}).get(name)
            if was and was.get("bonus") != now.get("bonus"):
                out.append(f"{name}{' ' + label if label else ''} {_signed(was['bonus'])} → {_signed(now['bonus'])}")
    for key, label in (("spells", "spell"), ("magic_items", "magic item"), ("feats", "feat"), ("items", "item"),
                       ("proficiencies", "proficiency"), ("languages", "language")):
        if key not in old:
            continue
        before, after = set(old.get(key) or []), set(new.get(key) or [])
        if key == "items":   # ordinary gear only, magic items are reported above
            before -= set(old.get("magic_items") or []); after -= set(new.get("magic_items") or [])
        gained, lost = sorted(after - before), sorted(before - after)
        if gained:
            out.append(f"New {label}(s): " + ", ".join(gained))
        if lost:
            out.append(f"No longer on the sheet ({label}): " + ", ".join(lost))
    return out


def one_line(summary):
    return (f"Level {summary['level']} {summary['classes']} · HP {summary['hp_current']}/{summary['hp_max']}"
            + (f" (+{summary['hp_temp']} temp)" if summary.get("hp_temp") else "")
            + f" · AC ≈ {summary['ac_estimate']}"
            + (f" · Passive Perception {summary['passive']['Perception']}" if summary.get("passive") else "")
            + f" · {len(summary['spells'])} spells · {len(summary['magic_items'])} magic items")


def sheet_text(summary):
    """The numbers in a readable block (Character history, and later session notes)."""
    if "skills" not in summary:
        return "(This version was saved before saves and skills were recorded. Refresh to record them.)"
    sc, md = summary.get("scores") or {}, summary.get("modifiers") or {}
    lines = ["Abilities: " + "  ".join(f"{k} {sc[k]} ({_signed(md.get(k, 0))})" for k in STATS.values() if k in sc),
             f"Proficiency bonus {_signed(summary['proficiency_bonus'])} · Initiative {_signed(summary['initiative'])} · Speed {summary.get('speed')} ft",
             "Saving throws: " + "  ".join(f"{k} {_signed(summary['saves'][k]['bonus'])}{'•' if summary['saves'][k]['proficient'] else ''}"
                                          for k in STATS.values() if k in summary["saves"]),
             "Skills: " + "  ".join(f"{k} {_signed(v['bonus'])}{ {'expertise': '••', 'proficient': '•', 'half': '½'}.get(v['level'], '') }"
                                   for k, v in sorted(summary["skills"].items())),
             "Passive: " + "  ".join(f"{k} {summary['passive'][k]}" for k in ("Perception", "Investigation", "Insight") if k in summary["passive"])]
    for s in summary.get("spellcasting") or []:
        lines.append(f"Spellcasting ({s['class']}, {s['ability']}): save DC {s['dc']} · spell attack {_signed(s['attack'])}")
    for w in summary.get("weapons") or []:
        lines.append(f"Attack: {w['name']}{'' if w['equipped'] else ' (not equipped)'} {_signed(w['attack'])} to hit, "
                     f"{w['dice']}{_signed(w['damage_bonus']) if w['damage_bonus'] else ''} {w['damage_type'].lower()}"
                     + ("" if w["proficient"] else " (not proficient)"))
    for x in summary.get("extra_damage") or []:
        lines.append(f"Extra damage: {x['name']} {x['dice']}")
    if summary.get("check_notes"):
        lines.append("Advantage / notes: " + "; ".join(summary["check_notes"]))
    if summary.get("proficiencies"):
        lines.append("Proficiencies: " + ", ".join(summary["proficiencies"]))
    if summary.get("languages"):
        lines.append("Languages: " + ", ".join(summary["languages"]))
    lines.append("(• proficient, •• expertise, ½ half proficiency e.g. Jack of All Trades)")
    return "\n".join(lines)
