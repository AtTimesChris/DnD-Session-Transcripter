"""Best-effort import of a publicly shared D&D Beyond character.

No login, cookies or stored credentials. The character must be set to Public in D&D Beyond
(character sheet -> Settings -> Privacy). Two sources are tried in order:

1. D&D Beyond's public character data service (JSON, the same data the sheet page loads).
   D&D Beyond renders its character pages with JavaScript, so the plain page HTML usually
   holds no name at all; this is why the HTML-only importer reported "no character name".
2. The page HTML (kept as a fallback).
"""
import re
import json
import urllib.request
import urllib.error
from html import unescape
from datetime import datetime, timezone
from urllib.parse import urlparse

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
API_URL = "https://character-service.dndbeyond.com/character/v5/character/{id}"


# ---------------------------------------------------------------- JSON service
def parse_character_json(payload: dict) -> dict:
    """Pick the useful public fields out of the character service response. Pure, unit-tested."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not str(data.get("name") or "").strip():
        raise RuntimeError("The character service answered but returned no character name.")
    race = data.get("race") if isinstance(data.get("race"), dict) else {}
    classes = []
    total_level = 0
    for entry in data.get("classes") or []:
        if not isinstance(entry, dict):
            continue
        definition = entry.get("definition") if isinstance(entry.get("definition"), dict) else {}
        sub = entry.get("subclassDefinition") if isinstance(entry.get("subclassDefinition"), dict) else {}
        level = int(entry.get("level") or 0)
        total_level += level
        label = str(definition.get("name") or "").strip()
        if sub.get("name"):
            label += f" ({sub['name']})"
        if label:
            classes.append(f"{label} {level}".strip())
    bg = data.get("background") if isinstance(data.get("background"), dict) else {}
    bg_def = bg.get("definition") if isinstance(bg.get("definition"), dict) else {}
    custom = bg.get("customBackground") if isinstance(bg.get("customBackground"), dict) else {}
    background = str(bg_def.get("name") or custom.get("name") or "").strip()
    traits = data.get("traits") if isinstance(data.get("traits"), dict) else {}
    gender = str(data.get("gender") or "").strip()
    return {
        "name": str(data["name"]).strip(),
        "gender": gender,
        "race": str(race.get("fullName") or race.get("baseRaceName") or "").strip(),
        "classes": " / ".join(classes),
        "level": total_level,
        "background": background,
        "traits": {k: str(traits.get(k) or "").strip() for k in ("personalityTraits", "ideals", "bonds", "flaws") if str(traits.get(k) or "").strip()},
        "alignment_id": data.get("alignmentId"),
    }


def _fetch_json(character_id: str, token: str = None) -> dict:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"   # v2.33: the connected account (ddb_auth); never logged
    request = urllib.request.Request(API_URL.format(id=character_id), headers=headers)
    with urllib.request.urlopen(request, timeout=18) as response:
        return json.loads(response.read(5_000_000).decode("utf-8", "replace"))


# ------------------------------------------------------------------ HTML fallback
def extract_public_character_details(html: str) -> dict:
    def class_text(class_name):
        pattern = r'<[^>]+class=["\'][^"\']*' + re.escape(class_name) + r'[^"\']*["\'][^>]*>(.*?)</[^>]+>'
        match = re.search(pattern, html, re.I | re.S)
        if not match:
            return ""
        value = unescape(re.sub(r"<[^>]+>", " ", match.group(1)))
        return re.sub(r"\s+", " ", value).strip()
    name = class_text("styles_characterName__2x8wQ")
    if not name:
        for match in re.finditer(r'<h1\b([^>]*)>(.*?)</h1>', html, re.I | re.S):
            attrs, body = match.groups()
            if re.search(r'character.?name|characterName|styles_characterName', attrs, re.I):
                name = re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]+>', ' ', body))).strip()
                if name:
                    break
    return {"name": name, "gender": class_text("ddbc-character-summary__gender"),
            "race": class_text("ddbc-character-summary__race"), "classes": class_text("ddbc-character-summary__classes")}


def clean_public_name_from_html(html: str) -> str:
    candidates = []
    generic = {"d&d beyond", "characters", "character sheet", "d&d beyond character sheet", "dungeons & dragons"}

    def clean_candidate(value):
        if not isinstance(value, str):
            return ""
        value = unescape(re.sub(r"<[^>]+>", " ", value)).strip()
        value = re.sub(r"\s*[|–-]\s*D&D Beyond.*$", "", value, flags=re.I).strip(" \t\r\n|–-")
        if not value or value.casefold() in generic or len(value) > 100:
            return ""
        return re.sub(r"\s+", " ", value)

    value = clean_candidate(extract_public_character_details(html).get("name", ""))
    if value:
        candidates.append((0, value))
    for script in re.findall(r'<script[^>]*>(.*?)</script>', html, re.I | re.S):
        if not script.strip() or len(script) > 5_000_000:
            continue
        try:
            obj = json.loads(script.strip())
        except Exception:
            continue
        stack = [obj]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                for key in ("characterName", "character_name", "charactername", "character_name_display"):
                    value = clean_candidate(item.get(key))
                    if value:
                        candidates.append((0, value))
                character = item.get("character")
                if isinstance(character, dict):
                    value = clean_candidate(character.get("name"))
                    if value:
                        candidates.append((0, value))
                if any(k in item for k in ("race", "classes", "class", "stats", "characterId")):
                    value = clean_candidate(item.get("name"))
                    if value:
                        candidates.append((1, value))
                stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
            elif isinstance(item, list):
                stack.extend(item)
    for pattern in (r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
                    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
                    r'<meta[^>]+name=["\']title["\'][^>]+content=["\']([^"\']+)',
                    r'<title[^>]*>(.*?)</title>'):
        match = re.search(pattern, html, re.I | re.S)
        if match:
            value = clean_candidate(match.group(1))
            if value:
                candidates.append((2, value))
    for pattern in (r'"characterName"\s*:\s*"([^"\\]{2,100})"', r'"character_name"\s*:\s*"([^"\\]{2,100})"'):
        for match in re.finditer(pattern, html, re.I):
            value = clean_candidate(match.group(1))
            if value:
                candidates.append((0, value))
    if not candidates:
        raise RuntimeError("The page loaded, but no character name could be identified. The page may expose only generic metadata or require a browser session. No data was changed.")
    candidates.sort(key=lambda x: (x[0], -len(x[1])))
    return candidates[0][1]


def character_id_from_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme != "https" or parsed.netloc.lower() not in {"www.dndbeyond.com", "dndbeyond.com"}:
        raise ValueError("Enter an HTTPS D&D Beyond character/share URL.")
    match = re.search(r"/characters/(\d+)", parsed.path)
    if not match:
        raise ValueError("The URL does not look like a D&D Beyond character URL.")
    return match.group(1)


def fetch_character_payload(url: str, token: str = None) -> dict:
    """The full character data (v2.32, for spells and items). Without a token only Public characters can be
    read; with the connected account's token (v2.33) also characters that account can see. Raises
    PermissionError when D&D Beyond refuses."""
    try:
        return _fetch_json(character_id_from_url(url), token)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404):
            why = ("your connected account cannot see this character" if token else "not public")
            raise PermissionError("%s (D&D Beyond answered HTTP %s)" % (why, exc.code)) from exc
        raise


def fetch_public_character(url: str) -> dict:
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() not in {"www.dndbeyond.com", "dndbeyond.com"}:
        raise ValueError("Enter an HTTPS D&D Beyond character/share URL.")
    match = re.search(r"/characters/(\d+)", parsed.path)
    if not match:
        raise ValueError("The URL does not look like a D&D Beyond character URL.")
    character_id = match.group(1)
    stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="minutes")
    problems = []

    try:
        details = parse_character_json(_fetch_json(character_id))
        details.update({"url": url, "refreshed": stamp, "source": "character service",
                        "status": "Imported from the public D&D Beyond character data (name, race, class and level, background, traits)."})
        return details
    except urllib.error.HTTPError as exc:
        problems.append(f"character data service: HTTP {exc.code}" + (" (character is not public)" if exc.code in (401, 403, 404) else ""))
    except Exception as exc:
        problems.append(f"character data service: {exc}")

    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
        with urllib.request.urlopen(request, timeout=18) as response:
            html = response.read(5_000_000).decode("utf-8", "replace")
        details = extract_public_character_details(html)
        details["name"] = clean_public_name_from_html(html)
        details.update({"url": url, "refreshed": stamp, "source": "page html",
                        "status": "Public character name and visible summary imported where available."})
        return details
    except Exception as exc:
        problems.append(f"page: {exc}")

    raise RuntimeError("Could not read this character. In D&D Beyond open the character, then Settings -> Privacy and set it to Public, "
                       "and use the share link. Details: " + "; ".join(problems))
