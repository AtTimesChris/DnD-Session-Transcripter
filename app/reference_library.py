"""Local-only reference import. Adventure text is stored privately; Whisper sees approved terms only."""
from pathlib import Path
import hashlib
import re
import json
import urllib.parse
import urllib.request
import urllib.error
import time
import difflib
from functools import lru_cache


def read_reference(path):
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt", ".markdown"}:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("PDF support is not installed. Run INSTALL.bat again to install pypdf.") from exc
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    raise ValueError(f"Unsupported reference format: {suffix}")


def candidate_terms(text, limit=None):
    """Names and terms of a book (v2.46: the whole book, line by line; see book_names). Deliberately returns no
    descriptions or surrounding text."""
    from .book_names import book_terms, MAX_TERMS
    return [t["term"] for t in book_terms(text, limit or MAX_TERMS)]


def term_review(text):
    """(all terms, the ones the book writes as names) for the review window (v2.46)."""
    from .book_names import book_terms
    found = book_terms(text)
    return [t["term"] for t in found], {t["term"] for t in found if t["name_like"]}


def _candidate_terms_before_v246(text, limit=1500):
    """The old finder, kept for reference: it stopped after `limit` terms (the start of a long book only) and joined a
    heading to the first word of the next line."""
    found = set()
    patterns = [
        r"\b[A-Z][a-zA-Z’'-]{2,}(?:\s+(?:the\s+)?[A-Z][a-zA-Z’'-]{2,}){0,3}\b",
        r"\b[A-Z]{2,}(?:[-'][A-Z]{2,})?\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            term = " ".join(match.group(0).split()).strip(" -'’")
            if len(term) < 3 or len(term) > 64:
                continue
            if term.lower() in {"chapter one", "chapter two", "appendix a", "appendix b", "player characters"}:
                continue
            found.add(term)
            if len(found) >= limit:
                break
    return sorted(found, key=str.casefold)


def source_record(path, category, content):
    p = Path(path)
    return {
        "path": str(p.resolve()),
        "name": p.name,
        "category": category,
        "content": content,
        "hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


DND_BOOK_CATEGORIES = [
    "Core Rulebook", "Supplement / Supplemental Sourcebook", "Rules Expansion", "Adventure",
    "Campaign Setting", "Bestiary / Monster Book", "Player Option / Character Options",
    "Dungeon Master Resource", "Starter Set", "Essentials Kit", "Extra / Accessory",
    "Digital / Online Reference", "Homebrew", "Other / Unclassified"
]

def guess_book_title(path):
    """Use the actual filename as the initial title; document headings may be unrelated."""
    p = Path(path)
    return p.stem.replace("_", " ").replace("-", " ").strip()


class GoogleBooksRateLimitError(RuntimeError):
    """Google Books refused a request because its API quota/rate limit was reached."""


_LAST_METADATA_REQUEST = 0.0


def _space_metadata_request():
    """Keep optional catalogue lookups polite; do not send local file contents."""
    global _LAST_METADATA_REQUEST
    elapsed = time.monotonic() - _LAST_METADATA_REQUEST
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)
    _LAST_METADATA_REQUEST = time.monotonic()


class _DDBPageParser:
    """Small standard-library HTML metadata/link parser; no third-party scraper."""
    from html.parser import HTMLParser as _HTMLParser

    class Parser(_HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.meta = {}
            self.anchors = []
            self._anchor_href = None
            self._anchor_text = []
            self._in_h1 = False
            self.h1 = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag.lower() == "meta":
                key = (attrs.get("property") or attrs.get("name") or "").casefold()
                value = attrs.get("content", "")
                if key and value:
                    self.meta[key] = value
            elif tag.lower() == "a":
                self._anchor_href = attrs.get("href")
                self._anchor_text = []
            elif tag.lower() == "h1":
                self._in_h1 = True

        def handle_endtag(self, tag):
            if tag.lower() == "a" and self._anchor_href:
                self.anchors.append((self._anchor_href, " ".join("".join(self._anchor_text).split())))
                self._anchor_href = None
                self._anchor_text = []
            elif tag.lower() == "h1":
                self._in_h1 = False

        def handle_data(self, data):
            if self._anchor_href:
                self._anchor_text.append(data)
            if self._in_h1:
                self.h1.append(data)


def _parse_ddb_html(html):
    parser = _DDBPageParser.Parser()
    parser.feed(html)
    return parser


def _fetch_text(url, timeout=10, provider="D&D Beyond"):
    _space_metadata_request()
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; LocalSessionKeeper/2.16; book metadata lookup)",
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{provider} returned HTTP {exc.code} for its public catalogue page.") from exc
    except Exception as exc:
        raise RuntimeError(f"{provider} request failed: {type(exc).__name__}: {exc}") from exc


def _ddb_lookup(title, timeout=10):
    """Find public D&D Beyond source pages and read page-level metadata only.

    The only search input sent is the user-entered title. We do not crawl chapter
    links, read book passages, or download protected source content.
    """
    search_url = "https://www.dndbeyond.com/search?" + urllib.parse.urlencode({"q": title})
    search_html = _fetch_text(search_url, timeout, "D&D Beyond")
    search_page = _parse_ddb_html(search_html)

    source_links = []
    seen = set()
    for href, anchor_title in search_page.anchors:
        absolute = urllib.parse.urljoin("https://www.dndbeyond.com", href)
        parsed = urllib.parse.urlparse(absolute)
        path_parts = [part for part in parsed.path.strip("/").split("/") if part]
        if parsed.netloc.casefold() not in {"www.dndbeyond.com", "dndbeyond.com"}:
            continue
        # A source landing page has /sources/<collection>/<slug>. Chapter URLs
        # have additional path components and are intentionally ignored.
        if len(path_parts) != 3 or path_parts[0].casefold() != "sources":
            continue
        clean_url = urllib.parse.urlunparse(("https", "www.dndbeyond.com", parsed.path.rstrip("/"), "", "", ""))
        if clean_url not in seen:
            seen.add(clean_url)
            source_links.append((clean_url, anchor_title))
        if len(source_links) >= 5:
            break

    candidates = []
    query_norm = " ".join(re.findall(r"[a-z0-9]+", title.casefold()))
    for source_url, search_title in source_links:
        page_html = _fetch_text(source_url, timeout, "D&D Beyond")
        page = _parse_ddb_html(page_html)
        page_title = (" ".join(page.h1) or page.meta.get("og:title") or page.meta.get("twitter:title") or "").strip()
        page_title = re.sub(r"\s*[-|]\s*(?:Dungeons\s*&\s*Dragons|D&D Beyond|Sources).*?$", "", page_title, flags=re.I).strip()
        if not page_title:
            page_title = search_title
        page_norm = " ".join(re.findall(r"[a-z0-9]+", page_title.casefold()))
        score = difflib.SequenceMatcher(None, query_norm, page_norm).ratio()
        query_words = set(query_norm.split())
        overlap = len(query_words & set(page_norm.split())) / max(1, len(query_words))
        if score < 0.48 and overlap < 0.6:
            continue
        description = (page.meta.get("og:description") or page.meta.get("description") or "").strip()
        published = page.meta.get("article:published_time", "") or page.meta.get("date", "")
        if published:
            published = published[:10]
        # Keep this metadata limited to the source landing page, not chapter text.
        candidates.append({
            "provider": "D&D Beyond",
            "title": page_title,
            "subtitle": "",
            "authors": [],
            "publisher": "Wizards of the Coast",
            "published_date": published,
            "categories": ["D&D Beyond source"] if not description else ["D&D Beyond source", description[:350]],
            "description": description[:500],
            "info_link": source_url,
            "_match_score": score,
        })
    candidates.sort(key=lambda item: item.get("_match_score", 0), reverse=True)
    for item in candidates:
        item.pop("_match_score", None)

    # A direct title query can occasionally redirect to a source page without
    # exposing search-result anchors. Handle that only if the final page is a
    # canonical source landing page.
    if not candidates:
        parsed = urllib.parse.urlparse(search_url)
        del parsed
    return candidates


# ---------- v2.45: D&D Beyond's public catalogue ----------------------------------------------
# D&D Beyond publishes the list of every source (name, abbreviation, rules group, page link) for its character
# builder. It needs no sign-in, and only the request itself is sent: no title, file or account details.
DDB_CONFIG_URL = "https://www.dndbeyond.com/api/config/json"
_THIRD_PARTY_GROUPS = {"5e Expanded Rules", "5.5e Expanded Rules", "5e Core Rules", "5.5e Core Rules", "Core Rules", "Playtest",
                       "Hidden Sources", "Archived", "Legacy/Noncore", "Noncore D&D", "Adventurers League", "Internal",
                       "D&D Beyond", "D&D Beyond Drops", "Eberron", "Dragonlance", "Planescape", "Critical Role",
                       "Magic: The Gathering", "Rick and Morty", "Minecraft", "Runeterra", "Blizzard"}


def _unescape(s):
    import html as _html
    return _html.unescape(str(s or ""))


def parse_ddb_catalogue(config):
    """The sources in D&D Beyond's config JSON -> [{'id','abbr','name','group','url','released'}]."""
    groups = {c.get("id"): _unescape(c.get("name")) for c in config.get("sourceCategories") or []}
    out = []
    for s in config.get("sources") or []:
        out.append({"id": s.get("id"), "abbr": _unescape(s.get("name")), "name": _unescape(s.get("description")),
                    "group": groups.get(s.get("sourceCategoryId"), ""), "released": bool(s.get("isReleased")),
                    "url": ("https://www.dndbeyond.com/" + s["sourceURL"].lstrip("/")) if s.get("sourceURL") else ""})
    return out


@lru_cache(maxsize=1)
def ddb_catalogue(timeout=20):
    return parse_ddb_catalogue(json.loads(_fetch_text(DDB_CONFIG_URL, timeout, "D&D Beyond")))


def _title_norm(s):
    s = _unescape(s).casefold().replace("’", "'")
    s = re.sub(r"\((?:19|20)\d\d\)|\b(?:19|20)\d\d\b", " ", s)
    s = re.sub(r"[_:\-–—.,]", " ", s)
    return " ".join(w for w in re.findall(r"[a-z0-9'ûéè]+", s) if w not in ("the", "a", "of", "md"))


def match_ddb_source(title, catalogue, limit=5):
    """Best D&D Beyond sources for a book title (its file name is fine): [(score, source)], best first.
    A year in the title picks the edition: 2024 or later -> the 2024 (5.5e) book, 2014 -> the 2014 one."""
    year = re.search(r"\b(19|20)(\d\d)\b", title or "")
    year = int(year.group(0)) if year else None
    want = _title_norm(title)
    scored = []
    for s in catalogue:
        if not s.get("url") or not s.get("released"):
            continue
        name = _title_norm(s["name"])
        score = difflib.SequenceMatcher(None, want, name).ratio()
        if want == name:
            score += 0.2
        old = "(2014)" in s["name"] or s["url"].endswith("-2014") or s["group"].startswith("5e ")
        new = s["url"].endswith("-2024") or s["group"].startswith("5.5e")
        if year and year >= 2024:
            score += 0.15 if new else (-0.3 if old else 0)
        elif year and year < 2024:
            score += 0.15 if old else (-0.3 if new else 0)
        if score >= 0.6:
            scored.append((round(score, 3), s))
    scored.sort(key=lambda x: -x[0])
    return scored[:limit]


def ddb_category_guess(name, description=""):
    """One of DND_BOOK_CATEGORIES from a D&D Beyond source's name and page description."""
    title = name.casefold()
    text = (name + " " + description).casefold()
    if any(x in title for x in ("player's handbook", "player’s handbook")):
        return "Core Rulebook"
    if any(x in title for x in ("dungeon master's guide", "dungeon master’s guide")):
        return "Dungeon Master Resource"
    if any(x in title for x in ("monster manual", "bestiary", "monsters of", "creature")):
        return "Bestiary / Monster Book"
    if "sage advice" in title:
        return "Supplement / Supplemental Sourcebook"
    if "play-along" in title or "map pack" in title:
        return "Extra / Accessory"
    if any(x in text for x in ("campaign setting", "is a setting", "a setting for", "worldbook", "world of", "explore the world")):
        return "Campaign Setting"
    if any(x in text for x in ("an adventure", "this adventure", "adventure for characters", "wild beyond", "curse of strahd",
                               "levels 1", "from 1st", "1st-level")):
        return "Adventure"
    if any(x in text for x in ("character options", "subclasses", "player options", "new species")):
        return "Player Option / Character Options"
    return suggest_dnd_category({"title": name, "description": description})


def ddb_book_details(title, timeout=20, read_page=True):
    """D&D Beyond's details for a book title: [{provider, title, abbreviation, rules, publisher, published_date,
    description, info_link, suggested_category, score}], best first. read_page: also read the book's public page
    for its description and the date the page was published."""
    out = []
    catalogue = ddb_catalogue(timeout)
    matches = match_ddb_source(title, catalogue)
    part_of = False
    if not matches:   # v2.46: "Strixhaven_ A Reckoning in Ruins" is an adventure inside "Strixhaven: A Curriculum of Chaos"
        matches, part_of = book_this_is_part_of(title, catalogue), True
    for score, s in matches:
        publisher = s["group"] if s["group"] and s["group"] not in _THIRD_PARTY_GROUPS else "Wizards of the Coast"
        rules = ("2024 rules (5.5e)" if s["group"].startswith("5.5e") or s["url"].endswith("-2024") else
                 ("2014 rules (5e)" if s["group"].startswith("5e") or "(2014)" in s["name"] else s["group"]))
        item = {"provider": "D&D Beyond", "title": s["name"], "abbreviation": s["abbr"], "rules": rules, "ddb_group": s["group"],
                "publisher": publisher, "published_date": "", "description": "", "info_link": s["url"],
                "categories": [x for x in ("D&D Beyond source", s["group"], rules) if x], "score": score}
        if part_of:
            item["part_of"] = True
            item["categories"].append("part of this book")
        out.append(item)
    if read_page and out:
        ddb_page_details(out[0], timeout)   # the best match only: one polite request
    for item in out:
        item["suggested_category"] = ddb_category_guess(item["title"], item.get("description", ""))
    return out


def ddb_page_details(item, timeout=20):
    """Read a D&D Beyond book page for its description and the date the page was published (fills item in place).
    v2.46: separate, so the review window can open at once and fill these in the background."""
    try:
        page = _fetch_text(item["info_link"], timeout, "D&D Beyond")
        meta = _parse_ddb_html(page).meta
        desc = " ".join((meta.get("og:description") or meta.get("description") or "").split())
        if desc.casefold().startswith(item["title"].casefold()):
            desc = desc[len(item["title"]):].strip()
        item["description"] = desc[:500]
        m = re.search(r'itemprop="datePublished">\s*<abbr[^>]*data-epoch="(\d+)"', page)
        if m:
            from datetime import datetime, timezone
            item["published_date"] = datetime.fromtimestamp(int(m.group(1)), timezone.utc).strftime("%Y-%m-%d")
            item["date_note"] = "date the D&D Beyond page was published"
        item["suggested_category"] = ddb_category_guess(item["title"], item.get("description", ""))
    except Exception as exc:
        item["page_error"] = str(exc)
    return item


def book_this_is_part_of(title, catalogue):
    """v2.46: for a file that is one part of a book ("Strixhaven_ Hunt for Mage Tower"): the books whose name starts
    with the part before "_" or ":" ("Strixhaven"). -> [(score, source)] like match_ddb_source."""
    head = re.split(r"[_:]|\s{2,}| [-–—] ", title or "", maxsplit=1)   # "Strixhaven_ X", "Strixhaven: X", "Strixhaven  X"
    if len(head) < 2:
        words = (title or "").split()
        if len(words) < 3:
            return []
        head = [words[0], " ".join(words[1:])]   # the first word on its own, when a book starts with it
    want = _title_norm(head[0])
    if len(want) < 4:
        return []
    out = []
    for s in catalogue:
        if s.get("url") and s.get("released") and _title_norm(s["name"]).startswith(want):
            out.append((0.6, s))
    return out[:3]


def _google_books_lookup(title, timeout=8):
    query = urllib.parse.urlencode({
        "q": f'intitle:"{title}"', "maxResults": 5, "printType": "books"
    })
    request = urllib.request.Request(
        "https://www.googleapis.com/books/v1/volumes?" + query,
        headers={"User-Agent": "LocalSessionKeeper/2.16"}
    )
    try:
        _space_metadata_request()
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
        except Exception:
            pass
        quota_error = exc.code == 429
        if exc.code == 403:
            try:
                error_payload = json.loads(detail)
                reasons = [item.get("reason", "") for item in error_payload.get("error", {}).get("errors", [])]
                quota_error = any(reason in {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"} for reason in reasons)
            except Exception:
                pass
        if quota_error:
            raise GoogleBooksRateLimitError(f"Google Books returned HTTP {exc.code} (quota/rate limit).") from exc
        raise RuntimeError(f"Google Books returned HTTP {exc.code}: {detail}") from exc

    candidates = []
    for item in payload.get("items", []):
        info = item.get("volumeInfo", {})
        candidates.append({
            "provider": "Google Books", "title": info.get("title", ""),
            "subtitle": info.get("subtitle", ""), "authors": info.get("authors", []),
            "publisher": info.get("publisher", ""), "published_date": info.get("publishedDate", ""),
            "categories": info.get("categories", []), "description": info.get("description", "")[:500],
            "info_link": info.get("infoLink", ""),
        })
    return candidates


def _open_library_lookup(title, timeout=8):
    """Fallback catalogue search. Only the user-entered title is transmitted."""
    params = urllib.parse.urlencode({
        "title": title, "limit": 5,
        "fields": "title,author_name,publisher,first_publish_year,subject,key,edition_count,isbn",
    })
    _space_metadata_request()
    request = urllib.request.Request(
        "https://openlibrary.org/search.json?" + params,
        headers={"User-Agent": "LocalSessionKeeper/2.16 (optional book-title metadata lookup)", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    candidates = []
    for item in payload.get("docs", []):
        key = item.get("key", "")
        subjects = item.get("subject", []) or []
        candidates.append({
            "provider": "Open Library", "title": item.get("title", ""), "subtitle": "",
            "authors": item.get("author_name", []) or [],
            "publisher": (item.get("publisher", []) or [""])[0],
            "published_date": str(item.get("first_publish_year", "") or ""),
            "categories": subjects[:30], "description": "",
            "info_link": ("https://openlibrary.org" + key) if key.startswith("/") else "",
            "edition_count": item.get("edition_count", 0), "isbn": (item.get("isbn", []) or [])[:5],
        })
    return candidates


@lru_cache(maxsize=128)
def lookup_book_metadata(title, timeout=10):
    """Try D&D Beyond, then Google Books, then Open Library; title only is sent."""
    title = title.strip()
    if not title:
        return []

    failures = []
    providers = (
        ("D&D Beyond", lambda t, timeout: ddb_book_details(t, max(timeout, 15)) or _ddb_lookup(t, timeout)),   # v2.45: catalogue first
        ("Google Books", _google_books_lookup),
        ("Open Library", _open_library_lookup),
    )
    for provider, lookup in providers:
        try:
            results = lookup(title, timeout)
            if results:
                return results
        except Exception as exc:
            failures.append(f"{provider}: {type(exc).__name__}: {exc}")

    if failures:
        raise RuntimeError(
            "All online catalogue lookups failed or returned no usable match. "
            + " | ".join(failures)
            + " You can still import the book and choose its category manually."
        )
    return []


def suggest_dnd_category(metadata):
    text = " ".join([
        metadata.get("title", ""), metadata.get("subtitle", ""),
        " ".join(metadata.get("categories", [])), metadata.get("description", ""),
    ]).casefold()
    title = metadata.get("title", "").casefold()
    if any(x in title for x in ("player's handbook", "players handbook", "player handbook", "core rulebook")):
        return "Core Rulebook"
    if any(x in title for x in ("dungeon master's guide", "dungeon masters guide")):
        return "Dungeon Master Resource"
    if any(x in text for x in ("adventure", "campaign", "wild beyond", "curse of", "tomb of", "dragonlance", "journey through")):
        return "Adventure"
    if any(x in text for x in ("monster manual", "bestiary", "monsters")):
        return "Bestiary / Monster Book"
    if any(x in text for x in ("campaign setting", "setting guide", "world of", "realms")):
        return "Campaign Setting"
    if any(x in text for x in ("supplement", "expanded", "options", "compendium", "tasha", "xanathar", "mordenkainen")):
        return "Supplement / Supplemental Sourcebook"
    if any(x in text for x in ("rules expansion", "additional rules", "rules options")):
        return "Rules Expansion"
    return "Other / Unclassified"
