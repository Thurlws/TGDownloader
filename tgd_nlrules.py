# SPDX-License-Identifier: MIT
"""Natural-language to smart-playlist rules.

Turns "energetic hip hop I haven't played in a month, 30 tracks max" into the
option dict `_create_smart_playlist` already understands:

    {"genre": "hip hop", "not_played_days": 30, "bpm_min": 120, "limit": 30}

Two tiers, in this order:

1. A deterministic local parser (`parse_local`). No network, no API key, no
   dependencies. The rule vocabulary is small and mostly numeric, so plain
   pattern matching covers the common phrasings.
2. Claude (`parse_llm`), for anything the local pass leaves on the floor.
   Opt-in via the `nl_playlist_llm` config flag plus an API key; when it is off
   or unreachable the local result stands on its own.

`translate()` runs tier 1, decides whether tier 2 is worth a call, and merges.
The caller gets the rules *plus* which fields were filled and which phrases went
unmatched, so the UI can show its work rather than silently guessing.
"""

from __future__ import annotations

import logging
import os
import re

import tgd_common

logger = logging.getLogger("tgd_nlrules")

# Every field the rule engine reads, with the value that means "ignore me".
# Mirrors _create_smart_playlist's option handling in TGDownloader_GUI.py.
EMPTY_RULES: dict = {
    "format":          "",
    "genre":           "",
    "artist":          "",
    "added_days":      0,
    "limit":           0,
    "min_rating":      0,
    "not_played_days": 0,
    "bpm_min":         0,
    "bpm_max":         0,
    "sort":            "az",
}

FORMATS = ("flac", "mp3", "m4a", "opus", "ogg")

# Genre words we can recognise without asking the library. Matched longest
# first so "drum and bass" wins over "bass" and "hip hop" over "hop".
GENRE_WORDS = (
    "drum and bass", "drum & bass", "hip hop", "hip-hop", "r&b", "rhythm and blues",
    "lo-fi", "lofi", "trip hop", "post rock", "post-rock", "synthwave", "vaporwave",
    "chillwave", "chillout", "dubstep", "hardstyle", "eurodance", "breakbeat",
    "classical", "electronic", "alternative", "instrumental", "orchestral",
    "shoegaze", "grunge", "hardcore", "dancehall", "afrobeat", "bluegrass",
    "ambient", "country", "reggae", "reggaeton", "techno", "trance", "garage",
    "gospel", "acoustic", "indie", "metal", "house", "disco", "blues", "jazz",
    "punk", "folk", "soul", "funk", "rock", "rap", "edm", "pop", "ska", "emo",
)

# Tempo words, used only when the text carries no explicit BPM numbers.
# (bpm_min, bpm_max); 0 means "no bound on this side".
TEMPO_WORDS = {
    "workout":   (120, 180),
    "gym":       (120, 180),
    "cardio":    (120, 180),
    "running":   (150, 180),
    "energetic": (120, 0),
    "upbeat":    (120, 0),
    "high energy": (128, 0),
    "party":     (118, 0),
    "danceable": (118, 0),
    "fast":      (140, 0),
    "chill":     (0, 100),
    "chilled":   (0, 100),
    "relaxing":  (0, 100),
    "mellow":    (0, 100),
    "calm":      (0, 95),
    "study":     (0, 100),
    "focus":     (0, 100),
    "sleep":     (0, 80),
    "slow":      (0, 90),
}

_NUM_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "ninety": 90, "hundred": 100,
}
_UNIT_DAYS = {"day": 1, "week": 7, "fortnight": 14, "month": 30, "year": 365}

# Words that carry no rule meaning, plus the rule vocabulary itself: both are
# dropped before reporting leftovers. A word the patterns consumed was
# understood, so listing it as "not understood" would be wrong, and it would
# also send queries to Claude that the local parser already handled.
_STOPWORDS = frozenset("""
a an and the of my me i in on at to for from with that which some any all
give make build create playlist mix songs song tracks track music stuff
please just only really very more most something anything everything
is are was were be been do does did have has had want like
havent hasnt hadnt dont doesnt didnt wont isnt arent cant couldnt

new newest recent recently latest old older thing things put together
play played playing listen listened listening heard hear spun touched
add added adding download downloaded downloading grabbed imported saved
rate rated rating ratings star stars starred favourite favourites fave faves
bpm tempo beat beats minute limit max maximum min minimum first last past
album albums file files format quality between under over above below around
day days week weeks month months year years top best sort sorted order
""".split())


def _mask(text: str, start: int, end: int) -> str:
    """Blank out a matched span so later patterns can't re-read it. Keeps the
    string length stable, which keeps every offset we captured earlier valid."""
    return text[:start] + " " * (end - start) + text[end:]


def _qty(raw: str) -> int:
    """A count written as digits or as an English word."""
    raw = (raw or "").strip().lower()
    if raw.isdigit():
        return int(raw)
    return _NUM_WORDS.get(raw, 0)


def _clamp(value, low, high):
    return max(low, min(high, value))


# ── Tier 1: local parser ─────────────────────────────────────────────────────

_DUR_RE = re.compile(
    r"\b(\d+|" + "|".join(_NUM_WORDS) + r")\s+"
    r"(day|week|fortnight|month|year)s?\b", re.I)

# "this week" / "the past month" / "last year", no explicit count.
_BARE_DUR_RE = re.compile(
    r"\b(?:this|the\s+)?(?:past|last)?\s*(day|week|fortnight|month|year)\b", re.I)

# Verbs that mark a duration as "hasn't been played in N days" rather than
# "was added in the last N days". Searched in the text *before* the duration.
_PLAY_CUE = re.compile(
    r"(?:play|listen|heard|hear|spun|touch|open)\w*\b[^.;]{0,40}$", re.I)
_ADD_CUE = re.compile(
    r"(?:add|download|grab|import|rip|got|acquir|save)\w*\b[^.;]{0,40}$", re.I)


def _parse_durations(text: str, rules: dict, filled: list) -> str:
    """added_days / not_played_days. Which one a duration means is decided by
    the verb in front of it: "not played in 3 months" vs "added this month"."""
    for regex, has_qty in ((_DUR_RE, True), (_BARE_DUR_RE, False)):
        for m in list(regex.finditer(text)):
            if not text[m.start():m.end()].strip():
                continue                      # already consumed by an earlier pass
            if has_qty:
                qty  = _qty(m.group(1))
                unit = m.group(2).lower()
            else:
                qty, unit = 1, m.group(1).lower()
                # A bare unit only counts with a "this/last/past" qualifier;
                # otherwise "day" in "days of thunder" would set a rule.
                if not re.search(r"\b(?:this|last|past)\s*$", text[:m.start(1)], re.I):
                    continue
            if qty <= 0:
                continue
            days   = qty * _UNIT_DAYS[unit]
            before = text[:m.start()]
            if _PLAY_CUE.search(before):
                field = "not_played_days"
            elif _ADD_CUE.search(before):
                field = "added_days"
            else:
                # No verb cue: "from the last 2 weeks" reads as recency of
                # addition, which is how the UI labels the field too.
                field = "added_days"
            if not rules[field]:
                rules[field] = days
                filled.append(field)
            text = _mask(text, m.start(), m.end())
    return text


_RATING_RE = re.compile(
    r"\b(\d|" + "|".join(_NUM_WORDS) + r")[\s-]*star", re.I)
_STAR_GLYPH_RE = re.compile(r"(★{1,5})")
_TOP_RATED_RE = re.compile(r"\b(?:top[\s-]?rated|highly[\s-]?rated|best[\s-]?rated)\b", re.I)
_FAVE_RE = re.compile(r"\b(?:favou?rites?|faves?)\b", re.I)


def _parse_rating(text: str, rules: dict, filled: list, notes: list) -> str:
    m = _RATING_RE.search(text)
    if m:
        val = _clamp(_qty(m.group(1)), 1, 5)
        rules["min_rating"] = val
        filled.append("min_rating")
        # The engine's only rating rule is a floor, so "5 star only" and
        # "5 star and up" behave identically. Say so rather than pretend.
        if val == 5 and re.search(r"\bonly\b", text, re.I):
            notes.append("Rating is a minimum, so 5 stars only and 5 stars and up are the same rule.")
        return _mask(text, m.start(), m.end())

    m = _STAR_GLYPH_RE.search(text)
    if m:
        rules["min_rating"] = _clamp(len(m.group(1)), 1, 5)
        filled.append("min_rating")
        return _mask(text, m.start(), m.end())

    m = _TOP_RATED_RE.search(text)
    if m:
        rules["min_rating"] = 4
        filled.append("min_rating")
        return _mask(text, m.start(), m.end())

    m = _FAVE_RE.search(text)
    if m:
        rules["min_rating"] = 5
        filled.append("min_rating")
        return _mask(text, m.start(), m.end())
    return text


_BPM_RANGE_RE = re.compile(
    r"\b(\d{2,3})\s*(?:-|–|to|and)\s*(\d{2,3})\s*(?:bpm)?\b(?=[^%]*bpm|\s*bpm)", re.I)
_BPM_RANGE_PREFIX_RE = re.compile(
    r"\bbpm\s*(?:between|of|from)?\s*(\d{2,3})\s*(?:-|–|to|and)\s*(\d{2,3})\b", re.I)
_BPM_MIN_RE = re.compile(
    r"\b(?:over|above|faster than|at least|more than|>=?)\s*(\d{2,3})\s*bpm\b", re.I)
_BPM_MAX_RE = re.compile(
    r"\b(?:under|below|slower than|at most|less than|<=?)\s*(\d{2,3})\s*bpm\b", re.I)
_BPM_ONE_RE = re.compile(r"\b(?:around|about|~)?\s*(\d{2,3})\s*bpm\b", re.I)


def _parse_bpm(text: str, rules: dict, filled: list, notes: list) -> str:
    """Explicit BPM numbers first; tempo adjectives only if none were given."""
    for regex in (_BPM_RANGE_PREFIX_RE, _BPM_RANGE_RE):
        m = regex.search(text)
        if m:
            lo, hi = sorted((int(m.group(1)), int(m.group(2))))
            rules["bpm_min"], rules["bpm_max"] = lo, hi
            filled += ["bpm_min", "bpm_max"]
            notes.append(_BPM_NOTE)
            return _mask(text, m.start(), m.end())

    hit = False
    m = _BPM_MIN_RE.search(text)
    if m:
        rules["bpm_min"] = int(m.group(1)); filled.append("bpm_min"); hit = True
        text = _mask(text, m.start(), m.end())
    m = _BPM_MAX_RE.search(text)
    if m:
        rules["bpm_max"] = int(m.group(1)); filled.append("bpm_max"); hit = True
        text = _mask(text, m.start(), m.end())
    if hit:
        notes.append(_BPM_NOTE)
        return text

    m = _BPM_ONE_RE.search(text)
    if m:
        # A single tempo is a target, not a hard equality: give it a window,
        # otherwise the rule matches almost nothing.
        centre = int(m.group(1))
        rules["bpm_min"], rules["bpm_max"] = max(1, centre - 5), centre + 5
        filled += ["bpm_min", "bpm_max"]
        notes.append(f"Read {centre} BPM as a {rules['bpm_min']}-{rules['bpm_max']} range.")
        notes.append(_BPM_NOTE)
        return _mask(text, m.start(), m.end())

    for word in sorted(TEMPO_WORDS, key=len, reverse=True):
        m = re.search(r"\b" + re.escape(word) + r"\b", text, re.I)
        if not m:
            continue
        lo, hi = TEMPO_WORDS[word]
        if lo:
            rules["bpm_min"] = lo; filled.append("bpm_min")
        if hi:
            rules["bpm_max"] = hi; filled.append("bpm_max")
        notes.append(f'Read "{word}" as a tempo rule.')
        notes.append(_BPM_NOTE)
        return _mask(text, m.start(), m.end())
    return text


_BPM_NOTE = ("BPM rules skip tracks with no tempo data. Run Tempo Analysis "
             "first or the playlist will come out short.")

_LIMIT_RES = (
    re.compile(r"\b(?:top|first|best)\s+(\d{1,4})\b", re.I),
    re.compile(r"\b(?:limit|max|maximum|up to|no more than)\s*(?:of|to|:)?\s*(\d{1,4})\b", re.I),
    re.compile(r"\b(?:just|only)\s+(\d{1,4})\b", re.I),
    re.compile(r"\b(\d{1,4})\s*(?:tracks?|songs?|files?)\b", re.I),
)


def _parse_limit(text: str, rules: dict, filled: list) -> str:
    for regex in _LIMIT_RES:
        m = regex.search(text)
        if m:
            val = int(m.group(1))
            if val > 0:
                rules["limit"] = val
                filled.append("limit")
                return _mask(text, m.start(), m.end())
    return text


_FORMAT_ALIASES = {"aac": "m4a", "lossless": "flac", "vorbis": "ogg", "mp4": "m4a"}


def _parse_format(text: str, rules: dict, filled: list) -> str:
    pattern = "|".join(list(FORMATS) + list(_FORMAT_ALIASES))
    m = re.search(r"\b(" + pattern + r")s?\b", text, re.I)
    if m:
        word = m.group(1).lower()
        rules["format"] = _FORMAT_ALIASES.get(word, word)
        filled.append("format")
        return _mask(text, m.start(), m.end())
    return text


_SORT_RECENT_RE = re.compile(
    r"\b(?:newest|latest|most recent|recently added|newest first|by date)\b", re.I)


def _parse_sort(text: str, rules: dict, filled: list) -> str:
    m = _SORT_RECENT_RE.search(text)
    if m:
        rules["sort"] = "recent"
        filled.append("sort")
        return _mask(text, m.start(), m.end())
    return text


_NAME_RE = re.compile(
    r"\b(?:called|named|call it|name it|titled)\s+[\"']?([^\"']{1,60}?)[\"']?\s*$", re.I)


def _parse_name(text: str, rules: dict, filled: list) -> str:
    m = _NAME_RE.search(text)
    if m:
        rules["name"] = m.group(1).strip()
        filled.append("name")
        return _mask(text, m.start(), m.end())
    return text


def _parse_genre(text: str, rules: dict, filled: list) -> str:
    for word in sorted(GENRE_WORDS, key=len, reverse=True):
        m = re.search(r"\b" + re.escape(word) + r"\b", text, re.I)
        if m:
            rules["genre"] = m.group(0).lower()
            filled.append("genre")
            return _mask(text, m.start(), m.end())
    return text


# "by X" / "from X", stopping at the next clause so "by Radiohead that I rated"
# doesn't swallow the rest of the sentence.
_ARTIST_RE = re.compile(
    r"\b(?:by|from)\s+(?!the\s+(?:last|past)\b)"
    r"([^,.;]{2,40}?)"
    r"(?=\s+(?:that|which|who|with|and|but|added|rated|not|played|in|under|over|sorted|limit)\b|[,.;]|$)",
    re.I)
_ARTIST_LABEL_RE = re.compile(r"\bartist[:\s]+([^,.;]{2,40})", re.I)


def _parse_artist(text: str, rules: dict, filled: list) -> str:
    for regex in (_ARTIST_LABEL_RE, _ARTIST_RE):
        m = regex.search(text)
        if not m:
            continue
        value = m.group(1).strip(" '\"")
        # "by artist" / "by album" is a sort instruction, not an artist filter.
        if value.lower() in ("artist", "album", "name", "date", "title"):
            continue
        if value and value.lower() not in _STOPWORDS:
            rules["artist"] = value
            filled.append("artist")
            return _mask(text, m.start(), m.end())
    return text


def _leftovers(text: str, rules: dict) -> list:
    """Words the parser never claimed, minus filler. These are what the UI
    shows as "not understood" and what makes the Claude pass worth trying."""
    words = re.findall(r"[a-z0-9&'-]+", text.lower())
    # Contractions are compared without the apostrophe so one stopword entry
    # covers "haven't", "havent" and "havent'" alike.
    out = [w for w in words
           if w not in _STOPWORDS and w.replace("'", "") not in _STOPWORDS and len(w) > 2]
    # A tempo adjective that lost to an explicit BPM number was still read
    # correctly, so it is not a gap in what we understood.
    if rules.get("bpm_min") or rules.get("bpm_max"):
        out = [w for w in out if w not in TEMPO_WORDS]
    seen: list = []
    for w in out:
        if w not in seen:
            seen.append(w)
    return seen


# Words that start a rule clause; the fallback name stops before them so it
# doesn't run on into "... that I haven't played in a month".
_NAME_BOUNDARY = frozenset("""
that which who whom with i we you it and but so then in on at by from for to
added rated played listened not under over between limit max sorted newest
""".split())


def _derive_name(query: str, rules: dict) -> str:
    """A playlist name for a request that didn't give one. Built from the rules
    we matched where possible, since those read better than a chopped sentence."""
    genre  = (rules.get("genre")  or "").strip()
    artist = (rules.get("artist") or "").strip()
    parts: list = []
    if rules.get("min_rating"):
        parts.append(f"{rules['min_rating']} Star")
    if genre and artist:
        parts.append(f"{genre.title()} by {artist}")
    elif genre:
        parts.append(genre.title())
    elif artist:
        parts.append(artist)
    elif rules.get("format"):
        parts.append(rules["format"].upper())
    if parts:
        return " ".join(parts)[:60]
    if rules.get("not_played_days"):
        return "Rediscover"
    if rules.get("added_days") or rules.get("sort") == "recent":
        return "Recently Added"

    cleaned = re.sub(r"^\s*(?:make|build|create|give me|i want)\s+(?:me\s+)?"
                     r"(?:a|an|the)?\s*(?:playlist\s+(?:of|with|for)?)?\s*",
                     "", query.strip(), flags=re.I)
    words: list = []
    for word in re.split(r"\s+", cleaned):
        bare = re.sub(r"[^\w'-]", "", word).lower()
        # Stop at a clause word or at the first number: whatever follows is
        # rule detail ("120-140 bpm"), not part of a readable title.
        if not bare or bare in _NAME_BOUNDARY or bare[0].isdigit():
            break
        words.append(word.strip(",.;"))
        if len(words) == 4:
            break
    if not words:
        return "Smart Playlist"
    return " ".join(w if w.isupper() else w.capitalize() for w in words)[:60]


def parse_local(query: str) -> dict:
    """Pattern-match a request into rules. Never raises, never calls out."""
    rules: dict = dict(EMPTY_RULES)
    filled: list = []
    notes:  list = []
    text = " " + (query or "").strip() + " "

    # Order matters: consume the number-bearing patterns before the generic
    # limit pass, or "30 days" becomes a 30-track limit.
    text = _parse_name(text, rules, filled)
    text = _parse_durations(text, rules, filled)
    text = _parse_rating(text, rules, filled, notes)
    text = _parse_bpm(text, rules, filled, notes)
    text = _parse_limit(text, rules, filled)
    text = _parse_format(text, rules, filled)
    text = _parse_sort(text, rules, filled)
    text = _parse_genre(text, rules, filled)
    text = _parse_artist(text, rules, filled)

    rules.setdefault("name", "")
    if not rules.get("name"):
        rules["name"] = _derive_name(query, rules)

    # De-duplicate while keeping order, and drop the derived name: it is not a
    # filter, and counting it would overstate how much we understood.
    seen: list = []
    for f in filled:
        if f not in seen:
            seen.append(f)
    return {
        "rules":    rules,
        "filled":   [f for f in seen if f != "name"],
        "unparsed": _leftovers(text, rules),
        "notes":    notes,
        "source":   "local",
    }


# ── Tier 2: Claude ───────────────────────────────────────────────────────────

MODEL = "claude-opus-5"

_SCHEMA = {
    "type": "object",
    "properties": {
        "format":          {"type": "string", "enum": ["", *FORMATS]},
        "genre":           {"type": "string"},
        "artist":          {"type": "string"},
        "added_days":      {"type": "integer"},
        "limit":           {"type": "integer"},
        "min_rating":      {"type": "integer", "enum": [0, 1, 2, 3, 4, 5]},
        "not_played_days": {"type": "integer"},
        "bpm_min":         {"type": "integer"},
        "bpm_max":         {"type": "integer"},
        "sort":            {"type": "string", "enum": ["az", "recent"]},
        "name":            {"type": "string"},
    },
    "required": ["format", "genre", "artist", "added_days", "limit", "min_rating",
                 "not_played_days", "bpm_min", "bpm_max", "sort", "name"],
    "additionalProperties": False,
}

_SYSTEM = """\
You turn a request for a music playlist into filter rules for a local library.

The library holds audio files tagged with artist, album, title, genre and track
number. Only these rules exist. Use 0 or "" for anything the request does not
ask for, and do not invent filters the user did not state.

format           file extension, one of flac/mp3/m4a/opus/ogg, "" for any
genre            substring matched against the track's genre tag
artist           substring matched against the artist folder name
added_days       only files added to the library within this many days
limit            maximum number of tracks, 0 for no limit
min_rating       minimum star rating 1-5, 0 for unrated-or-better (no filter)
not_played_days  only tracks NOT played in this many days
bpm_min/bpm_max  tempo bounds; 0 means no bound on that side
sort             "recent" for newest first, otherwise "az"
name             a short playlist name, from the request or describing it

Notes on intent:
- "haven't listened to in X" is not_played_days. "added/downloaded in the last
  X" is added_days. They are different rules; never set both from one phrase.
- Mood words map to tempo when they clearly imply one (workout is fast, study
  is slow). Leave both BPM fields 0 when the mood is about genre instead.
- Genre and artist are substrings, so prefer the shortest distinctive form
  ("hip hop", not "hip hop and rap").
"""


def _api_key(config: dict) -> str:
    return (config.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY") or "").strip()


def llm_available(config: "dict | None" = None) -> bool:
    """True when the Claude pass is switched on, keyed, and importable."""
    config = config if config is not None else tgd_common.load_config()
    if not config.get("nl_playlist_llm") or not _api_key(config):
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except Exception:
        return False


def parse_llm(query: str, config: "dict | None" = None) -> dict:
    """Ask Claude for the rule dict. Raises on transport or config problems so
    the caller can fall back to the local parse and say why."""
    config = config if config is not None else tgd_common.load_config()
    key = _api_key(config)
    if not key:
        raise RuntimeError("No Anthropic API key configured.")
    import anthropic

    client = anthropic.Anthropic(api_key=key)
    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=_SYSTEM,
        output_config={
            "effort": "low",                  # extraction, not reasoning
            "format": {"type": "json_schema", "schema": _SCHEMA},
        },
        messages=[{"role": "user", "content": query}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("The request was declined.")

    import json
    text = next((b.text for b in response.content if b.type == "text"), "")
    data = json.loads(text)
    return _coerce(data)


def _coerce(data: dict) -> dict:
    """Force whatever came back into the shape and ranges the engine accepts.
    The schema constrains types and enums; the numeric bounds are ours."""
    out = dict(EMPTY_RULES)
    out["name"] = ""
    for field, default in EMPTY_RULES.items():
        value = data.get(field, default)
        if isinstance(default, int):
            try:
                value = int(value)
            except (TypeError, ValueError):
                value = 0
            value = max(0, value)
        else:
            value = str(value or "").strip()
        out[field] = value
    out["name"] = str(data.get("name") or "").strip()
    out["format"] = out["format"].lower().lstrip(".")
    if out["format"] not in FORMATS:
        out["format"] = ""
    if out["sort"] != "recent":
        out["sort"] = "az"
    out["min_rating"] = _clamp(out["min_rating"], 0, 5)
    if out["bpm_min"] and out["bpm_max"] and out["bpm_min"] > out["bpm_max"]:
        out["bpm_min"], out["bpm_max"] = out["bpm_max"], out["bpm_min"]
    return out


# ── Orchestration ────────────────────────────────────────────────────────────

def _worth_asking(local: dict) -> bool:
    """Whether the Claude pass would add anything. Two cases: the local parse
    found nothing at all, or it found something but left real words behind."""
    return bool(not local["filled"] or local["unparsed"])


def translate(query: str, config: "dict | None" = None, allow_llm: bool = True) -> dict:
    """Full pipeline. Always returns a usable result; the LLM tier is best
    effort and its failure is reported, not raised."""
    query = (query or "").strip()
    if not query:
        return {"rules": dict(EMPTY_RULES, name=""), "filled": [], "unparsed": [],
                "notes": [], "source": "empty"}

    config = config if config is not None else tgd_common.load_config()
    local = parse_local(query)
    if not allow_llm or not _worth_asking(local) or not llm_available(config):
        return local

    try:
        rules = parse_llm(query, config)
    except Exception as exc:
        logger.info("Claude rule parse failed, keeping the local result: %s", exc)
        local["notes"] = local["notes"] + [f"Claude pass failed ({exc}); used the local parser."]
        return local

    filled = [f for f, empty in EMPTY_RULES.items()
              if f != "sort" and rules.get(f) != empty]
    if rules.get("sort") == "recent":
        filled.append("sort")
    if not rules.get("name"):
        rules["name"] = _derive_name(query, rules)

    notes = list(local["notes"])
    if (rules.get("bpm_min") or rules.get("bpm_max")) and _BPM_NOTE not in notes:
        notes.append(_BPM_NOTE)
    return {"rules": rules, "filled": filled, "unparsed": [], "notes": notes,
            "source": "claude"}
