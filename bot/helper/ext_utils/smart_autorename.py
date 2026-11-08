from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from logging import getLogger
from os import path as ospath
import re
from typing import Optional, Tuple
from copy import copy

from aiofiles.os import rename, path as aiopath

from .media_utils import get_streams, get_document_type
from .smart_autorename_providers import (
    CanonicalMetadataResolver,
    CanonicalMetadata,
    assess_canonical_candidate,
)

LOGGER = getLogger(__name__)


class SmartMediaType(str, Enum):
    MOVIE = "movie"
    SINGLE_EPISODE = "single_episode"
    EPISODE_RANGE = "episode_range"
    SEASON_PACK = "season_pack"
    UNKNOWN = "unknown"


class SmartRenameError(Exception):
    pass


class SmartRenameLengthError(SmartRenameError):
    pass


class SmartRenameMetadataError(SmartRenameError):
    pass


@dataclass
class SmartFilenameContext:
    original_filename: str
    media_extension: str
    split_suffix: str = ""

    title: str = ""
    year: Optional[str] = None

    season: Optional[int] = None
    episode_start: Optional[int] = None
    episode_end: Optional[int] = None

    media_type: SmartMediaType = SmartMediaType.UNKNOWN
    is_anime: bool = False
    has_explicit_season: bool = False

    ott: Optional[str] = None
    filename_quality: Optional[str] = None
    filename_codec: Optional[str] = None
    filename_audio: Optional[str] = None
    filename_esubs: bool = False


@dataclass
class AudioTrackDetail:
    index: int
    languages: str
    codec: str
    channels: str
    bitrate: Optional[str] = None
    flags: tuple[str, ...] = ()


@dataclass
class SmartMediaMetadata:
    quality: Optional[str] = None
    video_codec: Optional[str] = None
    audio_language: Optional[str] = None
    subtitle_languages: tuple[str, ...] = ()
    has_english_subtitle: bool = False
    audio_count: int = 0
    subtitle_count: int = 0
    audio_tracks_detail: tuple[AudioTrackDetail, ...] = ()
    subtitle_flags: tuple[str, ...] = ()


@dataclass
class MetadataSnapshot:
    original_filename: str
    title: str
    year: Optional[str] = None
    season: Optional[int] = None
    episode_start: Optional[int] = None
    episode_end: Optional[int] = None
    episode_title: Optional[str] = None
    media_type: SmartMediaType = SmartMediaType.UNKNOWN
    is_anime: bool = False

    quality: Optional[str] = None
    source_tokens: tuple[str, ...] = ()
    encoder_tokens: tuple[str, ...] = ()
    codec_tokens: tuple[str, ...] = ()
    bit_depth: Optional[str] = None
    hdr: Optional[str] = None
    fps: Optional[str] = None
    edition_flags: tuple[str, ...] = ()

    audio_track_count: int = 0
    audio_summary: str = ""
    audio_tracks: tuple[AudioTrackDetail, ...] = ()
    explicit_release_audio_label: Optional[str] = None

    subtitle_track_count: int = 0
    subtitle_summary: str = ""
    subtitle_languages: tuple[str, ...] = ()
    subtitle_flags: tuple[str, ...] = ()

    ott: Optional[str] = None
    extension: str = ".mkv"
    split_suffix: str = ""


@dataclass
class SmartNameParts:
    title: str
    identity: str = ""
    year: Optional[str] = None
    episode_title: Optional[str] = None
    quality: Optional[str] = None
    ott: Optional[str] = None
    audio: Optional[str] = None
    codec: Optional[str] = None
    esubs: bool = False
    extension: str = ".mkv"
    split_suffix: str = ""


_MEDIA_EXT_PATTERN = r"\.(?:mkv|mp4|avi|webm|flv|mov|m4v|3gp|ts|m2ts|wmv|asf|divx|ogv|vob|mpg|mpeg|rar|zip|7z|tar|gz|iso)"

_SPLIT_PATTERN_RE = re.compile(
    r"(?i)(?:"
    r"(?P<part_pre>\.part\d+)(?P<ext1>" + _MEDIA_EXT_PATTERN + r")|"
    r"(?P<ext2>" + _MEDIA_EXT_PATTERN + r")(?P<split_post>\.(?:part\d+|\d+))|"
    r"(?P<standalone>\.(?:part\d+|\d+))"
    r")$"
)

_VIDEO_EXT_RE = _SPLIT_PATTERN_RE

_RANGE_RE = re.compile(
    r"(?i)(?:^|[\s_.-])S(?P<season>\d{1,2})\s*E(?P<start>\d{1,4})\s*(?:-|–|~|\+|&|,|to|and)\s*E?(?P<end>\d{1,4})\b"
)
_SINGLE_RE = re.compile(
    r"(?i)(?:^|[\s_.-])S(?P<season>\d{1,2})\s*E(?P<episode>\d{1,4})\b"
)
_SEASON_RE = re.compile(
    r"(?i)(?:^|[\s_.-])S(\d{1,2})\b|\bSeason\s*(\d{1,2})\b|\b(\d{1,2})(?:st|nd|rd|th)\s*Season\b"
)

_ANIME_RANGE_RE = re.compile(
    r"(?i)(?:^|[\s_.-])(?:-\s*|(?:E|EP|Episode)\.?\s*)(?P<start>\d{1,4})\s*(?:-|–|~|\+|to)\s*(?:E|EP|Episode)?(?P<end>\d{1,4})\b"
)
_ANIME_SINGLE_RE = re.compile(
    r"(?i)(?:^|[\s_.-])(?P<prefix>-\s*|(?:E|EP|Episode)\.?\s*)(?P<episode>\d{1,4})(?=\s*v\d+)?(?=[\s_.\-\]\)]|$)"
)

_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")

_QUALITY_RE = re.compile(
    r"(?i)(?<!\d)(480p|540p|576p|720p|1080p|1440p|2160p|4320p|4k)(?!\w)"
)
_CODEC_RE = re.compile(r"(?i)(?<!\w)(x264|x265|h264|h265|hevc|av1|vp9|vp8)(?!\w)")

OTT_ALIASES = {
    "amzn": "AMZN",
    "amazon": "AMZN",
    "nf": "NF",
    "netflix": "NF",
    "dsnp": "DSNP",
    "disney": "DSNP",
    "hmax": "HMAX",
    "max": "MAX",
    "atv": "ATVP",
    "atvp": "ATVP",
    "apple": "ATVP",
    "hulu": "Hulu",
    "hstar": "HOTSTAR",
    "hotstar": "HOTSTAR",
    "jio": "JIO",
    "jc": "JIO",
    "jiocinema": "JIO",
    "sonyliv": "SONYLIV",
    "wb": "WB",
    "cr": "CR",
    "crunchyroll": "CR",
    "zee5": "ZEE5",
}

VIDEO_CODEC_MAP = {
    "h264": "x264",
    "avc1": "x264",
    "hevc": "x265",
    "h265": "x265",
    "hev1": "x265",
    "av1": "AV1",
    "vp9": "VP9",
    "vp8": "VP8",
    "mpeg4": "MPEG4",
    "mpeg2video": "MPEG2",
}

LANGUAGE_NAME_MAP = {
    "en": "English",
    "eng": "English",
    "english": "English",
    "hi": "Hindi",
    "hin": "Hindi",
    "hindi": "Hindi",
    "ja": "Japanese",
    "jpn": "Japanese",
    "japanese": "Japanese",
    "ko": "Korean",
    "kor": "Korean",
    "korean": "Korean",
    "ta": "Tamil",
    "tam": "Tamil",
    "tamil": "Tamil",
    "te": "Telugu",
    "tel": "Telugu",
    "telugu": "Telugu",
    "es": "Spanish",
    "spa": "Spanish",
    "spanish": "Spanish",
    "fr": "French",
    "fre": "French",
    "fra": "French",
    "french": "French",
    "de": "German",
    "ger": "German",
    "deu": "German",
    "german": "German",
    "zh": "Chinese",
    "chi": "Chinese",
    "zho": "Chinese",
    "chinese": "Chinese",
    "ru": "Russian",
    "rus": "Russian",
    "russian": "Russian",
    "it": "Italian",
    "ita": "Italian",
    "italian": "Italian",
    "ml": "Malayalam",
    "mal": "Malayalam",
    "malayalam": "Malayalam",
    "kn": "Kannada",
    "kan": "Kannada",
    "kannada": "Kannada",
    "mr": "Marathi",
    "mar": "Marathi",
    "marathi": "Marathi",
    "bn": "Bengali",
    "ben": "Bengali",
    "bengali": "Bengali",
    "pa": "Punjabi",
    "pan": "Punjabi",
    "punjabi": "Punjabi",
}


def split_media_filename(filename: str) -> tuple[str, str, str]:
    """Return (logical_stem, extension, split_suffix)."""
    match = _SPLIT_PATTERN_RE.search(filename)
    if match:
        stem = filename[: match.start()]
        if match.group("part_pre"):
            ext = match.group("ext1").lower()
            split = f"{match.group('part_pre')}{match.group('ext1')}"
            return stem, ext, split
        elif match.group("ext2"):
            ext = match.group("ext2").lower()
            split = match.group("split_post")
            return stem, ext, split
        elif match.group("standalone"):
            split = match.group("standalone")
            return stem, "", split

    path_stem, path_ext = ospath.splitext(filename)
    return path_stem, path_ext.lower(), ""


def normalize_video_codec(codec_name: str | None) -> str | None:
    if not codec_name:
        return None
    value = codec_name.strip().lower()
    return VIDEO_CODEC_MAP.get(value, value.upper())


def quality_from_height(height: int | None) -> str | None:
    if not height:
        return None
    if height <= 480:
        return "480p"
    if height <= 540:
        return "540p"
    if height <= 576:
        return "576p"
    if height <= 720:
        return "720p"
    if height <= 1080:
        return "1080p"
    if height <= 1440:
        return "1440p"
    if height <= 2160:
        return "2160p"
    if height <= 4320:
        return "4320p"
    return f"{height}p"


def normalize_language(lang_code: str | None) -> str | None:
    if not lang_code:
        return None
    code = lang_code.strip().lower()
    if code in {"und", "unknown", "none", "zxx"}:
        return None
    return LANGUAGE_NAME_MAP.get(code, code.capitalize())


_SITE_TAGS_RE = re.compile(
    r"(?i)\b(?:www\.)?[\w-]+\.(?:com|net|org|xyz|me|in|to|co|cc|info|tv|link|app|online|site|club|work|icu|top|vip|pro|party|fun|cam|lol|sbs|ws|is|download|store|page|click|live|ms|ai)\b|\b(?:vegamovies|1xbet|9xmovies|yts|rarbg|psa|bolly4u|desiremovies|hdhub4u|mkvking|katmoviehd|worldfree4u|filmyzilla|skymovieshd|uwatchfree|moviesmod)\b"
)


def extract_hdr_format(stem: str) -> Optional[str]:
    if not stem:
        return None

    # Compound DV + HDR variations
    if re.search(
        r"(?i)(?:\b|_)(?:dv|dovi|dolby\s*vision)[-._\s]+(?:hdr10\+|hdr10p)(?=[^a-zA-Z0-9]|$)",
        stem,
    ):
        return "DV.HDR10+" if "+" in stem else "DV.HDR10P"
    if re.search(
        r"(?i)(?:\b|_)(?:hdr10\+|hdr10p)[-._\s]+(?:dv|dovi|dolby\s*vision)(?=[^a-zA-Z0-9]|$)",
        stem,
    ):
        return "DV.HDR10+" if "+" in stem else "DV.HDR10P"
    if re.search(
        r"(?i)(?:\b|_)(?:dv|dovi|dolby\s*vision)[-._\s]+hdr10(?=[^a-zA-Z0-9]|$)",
        stem,
    ):
        return "DV.HDR10"
    if re.search(
        r"(?i)(?:\b|_)(?:dv|dovi|dolby\s*vision)[-._\s]+hdr(?=[^a-zA-Z0-9]|$)",
        stem,
    ):
        return "DV.HDR"
    if re.search(
        r"(?i)(?:\b|_)hdr[-._\s]+(?:dv|dovi|dolby\s*vision)(?=[^a-zA-Z0-9]|$)",
        stem,
    ):
        return "DV.HDR"

    # Standalone tokens across stem
    has_dv = bool(
        re.search(r"(?i)(?:\b|_)(?:dv|dovi|dolby\s*vision)(?=[^a-zA-Z0-9]|$)", stem)
    )
    has_hdr10p = bool(
        re.search(r"(?i)(?:\b|_)(?:hdr10\+|hdr10p)(?=[^a-zA-Z0-9]|$)", stem)
    )
    has_hdr10 = bool(re.search(r"(?i)(?:\b|_)(?:hdr10)(?=[^a-zA-Z0-9]|$)", stem))
    has_hdr = bool(re.search(r"(?i)(?:\b|_)hdr(?=[^a-zA-Z0-9]|$)", stem))
    has_sdr = bool(re.search(r"(?i)(?:\b|_)sdr(?=[^a-zA-Z0-9]|$)", stem))

    if has_dv and has_hdr10p:
        return "DV.HDR10+" if "+" in stem else "DV.HDR10P"
    if has_dv and has_hdr10:
        return "DV.HDR10"
    if has_dv and has_hdr:
        return "DV.HDR"
    if has_dv:
        if re.search(r"(?i)(?:\b|_)dovi(?=[^a-zA-Z0-9]|$)", stem):
            return "DoVi"
        return "DV"
    if has_hdr10p:
        return "HDR10+" if "+" in stem else "HDR10P"
    if has_hdr10:
        return "HDR10"
    if has_hdr:
        return "HDR"
    if has_sdr:
        return "SDR"
    return None


def clean_candidate_title(stem: str) -> str:
    value = stem
    value = re.sub(r"https?://\S+", " ", value, flags=re.I)
    value = _SITE_TAGS_RE.sub(" ", value)
    value = re.sub(r"\[[^\]]*\]", " ", value)
    value = re.sub(r"\([^)]*\)", " ", value)

    value = re.sub(
        r"(?i)\bS\d{1,2}\s*E\d{1,4}\s*(?:-|–|~|\+|&|,|to|and)\s*E?\d{1,4}\b",
        " ",
        value,
    )
    value = re.sub(r"(?i)\bS\d{1,2}\s*E\d{1,4}\b", " ", value)
    value = re.sub(r"(?i)\bS\d{1,2}\b", " ", value)
    value = re.sub(r"(?i)\bSeason\s*\d{1,2}\b", " ", value)

    value = _QUALITY_RE.sub(" ", value)
    value = _CODEC_RE.sub(" ", value)
    value = re.sub(
        r"(?i)(?:\b|_)(?:dv[-. ]*(?:hdr10\+|hdr10p|hdr)|hdr10\+|hdr10p|hdr\d*|sdr|dovi|dolbyvision|\bdv\b)(?=[^a-zA-Z0-9]|$)",
        " ",
        value,
    )
    value = re.sub(
        r"(?i)\b(?:web[- ]?dl|web[- ]?rip|webrip|bluray|brrip|hdr\d*|10bit|8bit|remux|repack|esubs?|engsubs?|multi(?:audio|sub)?|combined)\b",
        " ",
        value,
    )

    for alias in OTT_ALIASES.keys():
        value = re.sub(rf"(?i)\b{alias}\b", " ", value)

    value = re.sub(r"(?i)\b(?:ds\d*k?|uhd|4k|2k|hd)\b", " ", value)
    value = re.sub(
        r"(?i)\b(?:ddp|dd\+|dd|aac|ac3|eac3|flac|dts|truehd|atmos|mp3)(?:\s*\d\.\d)?\b",
        " ",
        value,
    )
    value = re.sub(r"\b\d\.\d\b", " ", value)
    value = re.sub(
        r"(?i)\b(?:hindi|english|tamil|telugu|malayalam|kannada|marathi|bengali|punjabi|japanese|korean|spanish|french|german|chinese|italian|russian)\b",
        " ",
        value,
    )

    value = value.replace("_", " ")
    value = re.sub(r"([a-z])([A-Z])", r"\1 \2", value)
    value = re.sub(r"\.+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" .-_-")


def parse_smart_filename(filename: str) -> SmartFilenameContext:
    logical_stem, extension, split_suffix = split_media_filename(filename)

    range_match = _RANGE_RE.search(logical_stem)
    single_match = _SINGLE_RE.search(logical_stem)
    season_match = _SEASON_RE.search(logical_stem)

    anime_range_match = (
        _ANIME_RANGE_RE.search(logical_stem)
        if not range_match and not single_match
        else None
    )
    anime_single_match = None
    if not range_match and not single_match and not anime_range_match:
        candidates = []
        for m in _ANIME_SINGLE_RE.finditer(logical_stem):
            ep_str = m.group("episode")
            ep_val = int(ep_str)
            has_prefix = bool(m.group("prefix"))
            prefix_str = (m.group("prefix") or "").lower()

            if (
                (1900 <= ep_val <= 2099)
                and not has_prefix
                and len(ep_str) == 4
                and not ep_str.startswith("0")
            ):
                continue

            after_text = logical_stem[m.end() :].lstrip().lower()
            if after_text.startswith("p"):
                continue

            if not has_prefix and ep_str in {
                "480",
                "540",
                "576",
                "720",
                "1080",
                "1440",
                "2160",
                "4320",
            }:
                continue

            priority = 1.0
            if "e" in prefix_str or "ep" in prefix_str or "episode" in prefix_str:
                priority = 3.0
            elif "-" in prefix_str:
                priority = 2.0
            elif len(ep_str) >= 3 or ep_str.startswith("0"):
                priority = 1.5

            candidates.append((priority, m.start(), m))

        if candidates:
            candidates.sort(key=lambda x: (-x[0], x[1]))
            anime_single_match = candidates[0][2]

    season = None
    episode_start = None
    episode_end = None

    if range_match:
        media_type = SmartMediaType.EPISODE_RANGE
        season = int(range_match.group("season"))
        episode_start = int(range_match.group("start"))
        episode_end = int(range_match.group("end"))
        marker = range_match
    elif single_match:
        media_type = SmartMediaType.SINGLE_EPISODE
        season = int(single_match.group("season"))
        episode_start = int(single_match.group("episode"))
        marker = single_match
    elif anime_range_match:
        media_type = SmartMediaType.EPISODE_RANGE
        season = 1
        if season_match:
            with suppress(Exception):
                season = int(next(g for g in season_match.groups() if g is not None))
        episode_start = int(anime_range_match.group("start"))
        episode_end = int(anime_range_match.group("end"))
        marker = anime_range_match
    elif anime_single_match:
        media_type = SmartMediaType.SINGLE_EPISODE
        season = 1
        if season_match:
            with suppress(Exception):
                season = int(next(g for g in season_match.groups() if g is not None))
        episode_start = int(anime_single_match.group("episode"))
        marker = anime_single_match
    elif season_match:
        media_type = SmartMediaType.SEASON_PACK
        season = int(next(g for g in season_match.groups() if g is not None))
        marker = season_match
    else:
        media_type = SmartMediaType.MOVIE
        marker = None

    title_source = logical_stem
    if marker:
        pre_marker = logical_stem[: marker.start()].strip(" .-_-")
        if pre_marker:
            title_source = pre_marker
    else:
        year_match = _YEAR_RE.search(logical_stem)
        if year_match:
            pre_year = logical_stem[: year_match.start()].strip(" .-_-( ")
            if pre_year:
                title_source = pre_year

    year_match = _YEAR_RE.search(logical_stem)
    year = year_match.group(0) if year_match else None

    title_clean = clean_candidate_title(title_source)

    quality = None
    if q := _QUALITY_RE.search(logical_stem):
        q_str = q.group(1).lower()
        quality = "2160p" if q_str == "4k" else q_str

    codec = None
    if c := _CODEC_RE.search(logical_stem):
        codec = normalize_video_codec(c.group(1))

    esubs = bool(re.search(r"(?i)\b(?:esubs?|eng(?:lish)?subs?)\b", logical_stem))

    ott = None
    for alias, canonical in OTT_ALIASES.items():
        if re.search(rf"(?i)\b{alias}\b", logical_stem):
            ott = canonical
            break

    audio = None
    for code, name in LANGUAGE_NAME_MAP.items():
        if len(code) > 2 and re.search(rf"(?i)\b{code}\b", logical_stem):
            audio = name
            break

    _ANIME_TAG_RE = re.compile(
        r"(?i)\[(?:subsplease|erai-raws|horriblesubs|judas|subdesu|golumpa|asw|puyasubs|yamekii|anime|crunchyroll)\]"
    )
    is_anime = bool(
        _ANIME_TAG_RE.search(filename) or anime_range_match or anime_single_match
    )

    has_explicit_season = bool(range_match or single_match or season_match)

    return SmartFilenameContext(
        original_filename=filename,
        media_extension=extension,
        split_suffix=split_suffix,
        title=title_clean,
        year=year,
        season=season,
        episode_start=episode_start,
        episode_end=episode_end,
        media_type=media_type,
        is_anime=is_anime,
        has_explicit_season=has_explicit_season,
        ott=ott,
        filename_quality=quality,
        filename_codec=codec,
        filename_audio=audio,
        filename_esubs=esubs,
    )


async def probe_smart_media_metadata(
    file_path: str, ctx: SmartFilenameContext
) -> SmartMediaMetadata:
    streams = await get_streams(file_path) or []

    video = next(
        (s for s in streams if s.get("codec_type") == "video"),
        None,
    )

    audio_language = None
    subtitle_languages = []
    subtitle_flags = []
    audio_count = 0
    subtitle_count = 0
    audio_tracks_detail = []

    for stream in streams:
        codec_type = stream.get("codec_type")
        tags = stream.get("tags") or {}
        language = (tags.get("language") or "").strip()

        if codec_type == "audio":
            audio_count += 1
            lang_norm = normalize_language(language) or "Unknown"
            if not audio_language and language:
                if language.lower() not in {"und", "unknown", "none"}:
                    audio_language = lang_norm

            acodec = (stream.get("codec_name") or "AAC").upper()
            if acodec == "EAC3":
                acodec = "E-AC3"
            ch_num = stream.get("channels") or 2
            ch_str = "5.1" if ch_num == 6 else ("7.1" if ch_num == 8 else "2.0")
            bitrate = None
            bps = tags.get("BPS") or tags.get("bit_rate") or stream.get("bit_rate")
            if bps and str(bps).isdigit():
                bps_val = int(bps) // 1000
                if bps_val > 0:
                    bitrate = f"{bps_val}Kbps"

            audio_tracks_detail.append(
                AudioTrackDetail(
                    index=audio_count,
                    languages=lang_norm,
                    codec=acodec,
                    channels=ch_str,
                    bitrate=bitrate,
                )
            )

        elif codec_type == "subtitle":
            subtitle_count += 1
            disp = stream.get("disposition") or {}
            if disp.get("forced"):
                subtitle_flags.append("Forced")
            if disp.get("hearing_impaired"):
                subtitle_flags.append("SDH")

            if language:
                normalized = normalize_language(language)
                if normalized:
                    subtitle_languages.append(normalized)

    height = None
    codec = None
    if video:
        with suppress(Exception):
            height = int(video.get("height"))
        codec = normalize_video_codec(video.get("codec_name"))

    english = any(lang.lower() in {"english", "en"} for lang in subtitle_languages)

    return SmartMediaMetadata(
        quality=quality_from_height(height) or ctx.filename_quality,
        video_codec=codec or ctx.filename_codec,
        audio_language=audio_language or ctx.filename_audio,
        subtitle_languages=tuple(subtitle_languages),
        has_english_subtitle=english or ctx.filename_esubs,
        audio_count=audio_count,
        subtitle_count=subtitle_count,
        audio_tracks_detail=tuple(audio_tracks_detail),
        subtitle_flags=tuple(set(subtitle_flags)),
    )


def clean_component(value: str) -> str:
    value = re.sub(r"[<>:\"/\\|?*]", " ", str(value))
    # Replace en-dash / em-dash / isolated hyphens with space while preserving hyphenated technical tokens (e.g. WEB-DL)
    value = re.sub(r"(?<!\w)[-–—]|[-–—](?!\w)", " ", value)
    value = re.sub(r"[.]+", " ", value)
    value = re.sub(r"\s+", ".", value).strip(".")
    return value


def shorten_words(title: str, max_chars: int) -> str | None:
    title = clean_component(title)
    if len(title) <= max_chars:
        return title
    if max_chars <= 0:
        return None

    words = title.split()
    result = ""
    for word in words:
        candidate = word if not result else f"{result}.{word}"
        if len(candidate) <= max_chars:
            result = candidate
        else:
            break

    if result:
        return result

    return title[:max_chars].rstrip(".- ") or None


class SmartFilenameBuilder:
    def make_parts(
        self,
        ctx: SmartFilenameContext,
        canonical: Optional[CanonicalMetadata],
        media: SmartMediaMetadata,
    ) -> Optional[SmartNameParts]:
        if ctx.media_type == SmartMediaType.MOVIE:
            return self._make_movie_parts(ctx, canonical, media)

        if ctx.media_type == SmartMediaType.SINGLE_EPISODE:
            return self._make_single_episode_parts(ctx, canonical, media)

        if ctx.media_type == SmartMediaType.EPISODE_RANGE:
            return self._make_episode_range_parts(ctx, canonical, media)

        if ctx.media_type == SmartMediaType.SEASON_PACK:
            return self._make_season_pack_parts(ctx, canonical, media)

        return None

    def _technical_fields(self, ctx, media, canonical):
        quality = media.quality or ctx.filename_quality
        ott = (canonical.ott if canonical else None) or ctx.ott
        audio = media.audio_language or ctx.filename_audio
        codec = ctx.filename_codec or media.video_codec
        esubs = media.has_english_subtitle or ctx.filename_esubs
        return quality, ott, audio, codec, esubs

    def _make_movie_parts(self, ctx, canonical, media):
        title = (canonical.title if canonical else None) or ctx.title
        year = (canonical.year if canonical else None) or ctx.year

        if not title or not year:
            return None

        quality, ott, audio, codec, esubs = self._technical_fields(
            ctx, media, canonical
        )

        return SmartNameParts(
            title=title,
            identity="",
            year=year,
            quality=quality,
            ott=ott,
            audio=audio,
            codec=codec,
            esubs=esubs,
            extension=ctx.media_extension,
            split_suffix=ctx.split_suffix,
        )

    def _make_single_episode_parts(self, ctx, canonical, media):
        series_title = (
            canonical.series_title or canonical.title if canonical else None
        ) or ctx.title
        episode_title = canonical.episode_title if canonical else None

        if not series_title or ctx.season is None or ctx.episode_start is None:
            return None

        if ctx.is_anime and not ctx.has_explicit_season:
            se = f"E{ctx.episode_start}"
        else:
            se = f"S{ctx.season:02d}E{ctx.episode_start:02d}"

        quality, ott, audio, codec, esubs = self._technical_fields(
            ctx, media, canonical
        )

        return SmartNameParts(
            title=series_title,
            identity=se,
            episode_title=episode_title,
            quality=quality,
            ott=ott,
            audio=audio,
            codec=codec,
            esubs=esubs,
            extension=ctx.media_extension,
            split_suffix=ctx.split_suffix,
        )

    def _make_episode_range_parts(self, ctx, canonical, media):
        series_title = (
            canonical.series_title or canonical.title if canonical else None
        ) or ctx.title

        if (
            not series_title
            or ctx.season is None
            or ctx.episode_start is None
            or ctx.episode_end is None
        ):
            return None

        if ctx.is_anime and not ctx.has_explicit_season:
            season_range = f"E{ctx.episode_start}-E{ctx.episode_end}"
        else:
            season_range = (
                f"S{ctx.season:02d}E{ctx.episode_start:02d}-E{ctx.episode_end:02d}"
            )

        quality, ott, audio, codec, esubs = self._technical_fields(
            ctx, media, canonical
        )

        return SmartNameParts(
            title=series_title,
            identity=season_range,
            quality=quality,
            ott=ott,
            audio=audio,
            codec=codec,
            esubs=esubs,
            extension=ctx.media_extension,
            split_suffix=ctx.split_suffix,
        )

    def _make_season_pack_parts(self, ctx, canonical, media):
        series_title = (
            canonical.series_title or canonical.title if canonical else None
        ) or ctx.title

        if not series_title or ctx.season is None:
            return None

        season_str = f"S{ctx.season:02d}"
        quality, ott, audio, codec, esubs = self._technical_fields(
            ctx, media, canonical
        )

        return SmartNameParts(
            title=series_title,
            identity=season_str,
            quality=quality,
            ott=ott,
            audio=audio,
            codec=codec,
            esubs=esubs,
            extension=ctx.media_extension,
            split_suffix=ctx.split_suffix,
        )

    def build_snapshot(
        self,
        original_filename: str,
        ctx: SmartFilenameContext,
        canonical: Optional[CanonicalMetadata],
        media: SmartMediaMetadata,
    ) -> MetadataSnapshot:
        parts = self.make_parts(ctx, canonical, media)
        title = (
            (canonical.series_title or canonical.title if canonical else None)
            or (parts.title if parts else ctx.title)
            or ""
        )
        ep_title = (canonical.episode_title if canonical else None) or (
            parts.episode_title if parts else ""
        )
        year = str((canonical.year if canonical else None) or ctx.year or "") or None

        stem_source, ext, split_suffix = split_media_filename(original_filename)
        stem_clean = _SITE_TAGS_RE.sub(" ", stem_source)
        stem_clean = re.sub(r"https?://\S+", " ", stem_clean, flags=re.I)

        tech_pattern = re.compile(
            r"(?i)\b("
            r"480p|540p|576p|720p|1080p|1440p|2160p|4320p|4k|"
            r"hevc|h\.?265|h\.?264|x265|x264|avc1?|av1|vp9|vp8|"
            r"10bit|8bit|hdr\d*|hdr10p|hdr10\+|sdr|dovi|dolbyvision|\bdv\b|open\s*matte|remastered|60fps|ds\d*k?|uhd|remux|imax|"
            r"unrated|extended|uncut|untouched|directors\s*cut|theatrical\s*cut|upscaled|"
            r"web[- ]?dl|web[- ]?rip|bluray|brrip|hdrip|dvdrip|hdtvrip|hdtv|tv-dl|predvd|s-print|hdts|pre-hd|"
            r"amzn|amazon|nf|netflix|cr|crunchyroll|sm|shemaroo|hs|hotstar|hstar|mxp|crav|snxt|zee5|jc|jiocinema|it|itunes|atv|apple\s*tv|dsnp|disney\+?|hmax|hulu|pcok|peacock|sonyliv|bms|"
            r"ddp\d*\.?\d*|dd\+?\d*\.?\d*|ac3\d*\.?\d*|aac\d*\.?\d*|dts(?:-hd)?(?:\s*ma)?|truehd|opus|flac|mp3|"
            r"hindi|english|tamil|telugu|malayalam|kannada|marathi|bengali|punjabi|japanese|korean|spanish|french|german|chinese|italian|russian|"
            r"dual\s*audio|multi\s*audio|\d+\s*dubs|multi\s*sub|esubs?|engsubs?|msubs?|hc-subs?|sdh|forced"
            r")\b"
        )

        sources = []
        encoders = []
        codecs = []
        bit_depth = None
        hdr = extract_hdr_format(stem_clean)
        flags = []
        explicit_audio_label = None

        for m in tech_pattern.finditer(stem_clean):
            tok = m.group(0).strip(" ._-")
            tok_l = tok.lower()
            if tok_l in {
                "web-dl",
                "webdl",
                "web-rip",
                "webrip",
                "bluray",
                "bdrip",
                "brrip",
                "hdrip",
                "dvdrip",
                "hdtv",
                "hdtvrip",
                "remux",
            }:
                sources.append(tok)
            elif tok_l in {"x265", "x264"}:
                encoders.append(tok)
            elif tok_l in {
                "hevc",
                "h.265",
                "h265",
                "h.264",
                "h264",
                "avc",
                "avc1",
                "av1",
                "vp9",
                "vp8",
            }:
                codecs.append(tok)
            elif tok_l in {"10bit", "8bit"}:
                bit_depth = tok
            elif tok_l in {
                "hdr",
                "hdr10",
                "hdr10p",
                "hdr10+",
                "sdr",
                "dovi",
                "dolbyvision",
                "dv",
            }:
                if not hdr:
                    hdr = tok
            elif tok_l in {
                "ds4k",
                "ds2k",
                "open matte",
                "remastered",
                "60fps",
                "imax",
                "unrated",
                "extended",
                "uncut",
                "untouched",
                "directors cut",
                "theatrical cut",
            }:
                flags.append(tok)
            elif "multi" in tok_l and "audio" in tok_l:
                explicit_audio_label = "MultiAudio"
            elif "dual" in tok_l and "audio" in tok_l:
                explicit_audio_label = "DualAudio"

        if not codecs and media.video_codec:
            codecs.append(media.video_codec)
        quality_val = media.quality or ctx.filename_quality
        if not quality_val:
            quality_val = "1080p" if "1080p" in stem_clean.lower() else None

        if explicit_audio_label:
            audio_sum = explicit_audio_label
        elif media.audio_count >= 3:
            audio_sum = "MultiAudio"
        elif media.audio_count == 2:
            audio_sum = "DualAudio"
        elif media.audio_count == 1:
            audio_sum = media.audio_language or ctx.filename_audio or "English"
        else:
            audio_sum = ctx.filename_audio or ""

        if media.subtitle_count >= 2:
            sub_sum = "MSub"
        elif (
            media.subtitle_count == 1
            or media.has_english_subtitle
            or ctx.filename_esubs
        ):
            sub_sum = "ESub"
        else:
            sub_sum = ""

        ott_val = (
            (canonical.ott if canonical else None)
            or ctx.ott
            or (parts.ott if parts else None)
        )

        return MetadataSnapshot(
            original_filename=original_filename,
            title=title,
            year=year,
            season=ctx.season,
            episode_start=ctx.episode_start,
            episode_end=ctx.episode_end,
            episode_title=ep_title or None,
            media_type=ctx.media_type,
            is_anime=ctx.is_anime,
            quality=quality_val,
            source_tokens=tuple(dict.fromkeys(sources)),
            encoder_tokens=tuple(dict.fromkeys(encoders)),
            codec_tokens=tuple(dict.fromkeys(codecs)),
            bit_depth=bit_depth,
            hdr=hdr,
            edition_flags=tuple(dict.fromkeys(flags)),
            audio_track_count=media.audio_count,
            audio_summary=audio_sum,
            audio_tracks=media.audio_tracks_detail,
            explicit_release_audio_label=explicit_audio_label,
            subtitle_track_count=media.subtitle_count,
            subtitle_summary=sub_sum,
            subtitle_languages=media.subtitle_languages,
            subtitle_flags=media.subtitle_flags,
            ott=ott_val,
            extension=ext or ctx.media_extension or ".mkv",
            split_suffix=split_suffix or ctx.split_suffix or "",
        )

    def render_v2(
        self,
        snapshot: MetadataSnapshot,
        prefix: str = "",
        suffix: str = "",
    ) -> str:
        clean_prefix = re.sub(r"<.*?>", "", prefix or "").replace(r"\s", " ")
        clean_suffix = re.sub(r"<.*?>", "", suffix or "").replace(r"\s", " ")

        values = []
        if snapshot.title:
            values.append(clean_component(snapshot.title))

        if snapshot.media_type in (
            SmartMediaType.SINGLE_EPISODE,
            SmartMediaType.EPISODE_RANGE,
            SmartMediaType.SEASON_PACK,
        ):
            if snapshot.season is not None:
                if (
                    snapshot.episode_start is not None
                    and snapshot.episode_end is not None
                ):
                    if snapshot.is_anime:
                        values.append(
                            f"E{snapshot.episode_start}-E{snapshot.episode_end}"
                        )
                    else:
                        values.append(
                            f"S{snapshot.season:02d}E{snapshot.episode_start:02d}-E{snapshot.episode_end:02d}"
                        )
                elif snapshot.episode_start is not None:
                    if snapshot.is_anime:
                        values.append(f"E{snapshot.episode_start}")
                    else:
                        values.append(
                            f"S{snapshot.season:02d}E{snapshot.episode_start:02d}"
                        )
                else:
                    values.append(f"S{snapshot.season:02d}")

        if snapshot.year and snapshot.media_type == SmartMediaType.MOVIE:
            values.append(clean_component(snapshot.year))

        if snapshot.episode_title:
            values.append(clean_component(snapshot.episode_title))

        if snapshot.quality:
            values.append(clean_component(snapshot.quality))
        if snapshot.ott:
            values.append(clean_component(snapshot.ott))

        for src in snapshot.source_tokens:
            values.append(clean_component(src))
        if snapshot.hdr:
            values.append(clean_component(snapshot.hdr))
        if snapshot.bit_depth:
            values.append(clean_component(snapshot.bit_depth))

        for flag in snapshot.edition_flags:
            values.append(clean_component(flag))
        for enc in snapshot.encoder_tokens:
            values.append(clean_component(enc))
        for cod in snapshot.codec_tokens:
            values.append(clean_component(cod))

        if snapshot.audio_summary:
            values.append(clean_component(snapshot.audio_summary))
        if snapshot.subtitle_summary:
            values.append(clean_component(snapshot.subtitle_summary))

        seen = set()
        final_values = []
        for v in values:
            if v and v.lower() not in seen:
                seen.add(v.lower())
                final_values.append(v)

        stem = ".".join(final_values)
        res = f"{clean_prefix}{stem}{clean_suffix}{snapshot.extension}{snapshot.split_suffix}"
        CaptionValidator.validate(res, snapshot, is_v1=False)
        return res

    def render_v1(
        self,
        snapshot: MetadataSnapshot,
        prefix: str = "",
        suffix: str = "",
    ) -> str:
        clean_prefix = re.sub(r"<.*?>", "", prefix or "").replace(r"\s", " ")
        clean_suffix = re.sub(r"<.*?>", "", suffix or "").replace(r"\s", " ")

        values = []
        if snapshot.title:
            values.append(clean_component(snapshot.title))

        if snapshot.media_type in (
            SmartMediaType.SINGLE_EPISODE,
            SmartMediaType.EPISODE_RANGE,
            SmartMediaType.SEASON_PACK,
        ):
            if snapshot.season is not None:
                if (
                    snapshot.episode_start is not None
                    and snapshot.episode_end is not None
                ):
                    if snapshot.is_anime:
                        values.append(
                            f"E{snapshot.episode_start}-E{snapshot.episode_end}"
                        )
                    else:
                        values.append(
                            f"S{snapshot.season:02d}E{snapshot.episode_start:02d}-E{snapshot.episode_end:02d}"
                        )
                elif snapshot.episode_start is not None:
                    if snapshot.is_anime:
                        values.append(f"E{snapshot.episode_start}")
                    else:
                        values.append(
                            f"S{snapshot.season:02d}E{snapshot.episode_start:02d}"
                        )
                else:
                    values.append(f"S{snapshot.season:02d}")

        if snapshot.year and snapshot.media_type == SmartMediaType.MOVIE:
            values.append(clean_component(snapshot.year))

        if snapshot.episode_title:
            values.append(clean_component(snapshot.episode_title))

        if snapshot.quality:
            values.append(clean_component(snapshot.quality))
        if snapshot.ott:
            values.append(clean_component(snapshot.ott))

        for src in snapshot.source_tokens:
            values.append(clean_component(src))
        if snapshot.hdr:
            values.append(clean_component(snapshot.hdr))
        if snapshot.bit_depth:
            values.append(clean_component(snapshot.bit_depth))

        for flag in snapshot.edition_flags:
            values.append(clean_component(flag))
        for enc in snapshot.encoder_tokens:
            values.append(clean_component(enc))
        for cod in snapshot.codec_tokens:
            values.append(clean_component(cod))

        if snapshot.audio_tracks:
            track_strs = []
            for trk in snapshot.audio_tracks:
                parts = [trk.languages, trk.codec, trk.channels]
                if trk.bitrate:
                    parts.append(f"~.{trk.bitrate}")
                track_strs.append(".".join(p for p in parts if p))
            rendered_tracks = f"[{' .+. '.join(track_strs)}]"
            values.append(rendered_tracks)
        elif snapshot.audio_summary:
            values.append(clean_component(snapshot.audio_summary))

        if snapshot.subtitle_summary:
            values.append(clean_component(snapshot.subtitle_summary))

        seen = set()
        final_values = []
        for v in values:
            if v and v.lower() not in seen:
                seen.add(v.lower())
                final_values.append(v)

        stem = ".".join(final_values)
        res = f"{clean_prefix}{stem}{clean_suffix}{snapshot.extension}{snapshot.split_suffix}"
        CaptionValidator.validate(res, snapshot, is_v1=True)
        return res

    def make_smart_caption(
        self,
        original_filename: str,
        ctx: SmartFilenameContext,
        canonical: Optional[CanonicalMetadata],
        media: SmartMediaMetadata,
        prefix: str = "",
        suffix: str = "",
        version: str = "v2",
    ) -> str:
        snapshot = self.build_snapshot(original_filename, ctx, canonical, media)
        if version.lower() in {"v1", "smartcaptionv1"}:
            return self.render_v1(snapshot, prefix=prefix, suffix=suffix)
        return self.render_v2(snapshot, prefix=prefix, suffix=suffix)

    def make_smart_caption_v1(
        self,
        original_filename: str,
        ctx: SmartFilenameContext,
        canonical: Optional[CanonicalMetadata],
        media: SmartMediaMetadata,
        prefix: str = "",
        suffix: str = "",
    ) -> str:
        snapshot = self.build_snapshot(original_filename, ctx, canonical, media)
        return self.render_v1(snapshot, prefix=prefix, suffix=suffix)

    def make_smart_caption_v2(
        self,
        original_filename: str,
        ctx: SmartFilenameContext,
        canonical: Optional[CanonicalMetadata],
        media: SmartMediaMetadata,
        prefix: str = "",
        suffix: str = "",
    ) -> str:
        snapshot = self.build_snapshot(original_filename, ctx, canonical, media)
        return self.render_v2(snapshot, prefix=prefix, suffix=suffix)


class CaptionValidator:
    @staticmethod
    def validate(caption: str, snapshot: MetadataSnapshot, is_v1: bool = False) -> bool:
        if not caption or not snapshot.title:
            return False

        for tok in snapshot.source_tokens:
            tok_c = clean_component(tok).lower()
            if tok_c and tok_c not in caption.lower():
                LOGGER.warning(
                    f"CaptionValidator Warning: Source token '{tok}' missing from caption '{caption}'"
                )

        for tok in snapshot.encoder_tokens:
            tok_c = clean_component(tok).lower()
            if tok_c and tok_c not in caption.lower():
                LOGGER.warning(
                    f"CaptionValidator Warning: Encoder token '{tok}' missing from caption '{caption}'"
                )

        for tok in snapshot.codec_tokens:
            tok_c = clean_component(tok).lower()
            if tok_c and tok_c not in caption.lower():
                LOGGER.warning(
                    f"CaptionValidator Warning: Codec token '{tok}' missing from caption '{caption}'"
                )

        if snapshot.hdr:
            hdr_c = clean_component(snapshot.hdr).lower()
            if hdr_c and hdr_c not in caption.lower():
                LOGGER.warning(
                    f"CaptionValidator Warning: HDR token '{snapshot.hdr}' missing from caption '{caption}'"
                )

        if snapshot.split_suffix and not caption.endswith(snapshot.split_suffix):
            LOGGER.warning(
                f"CaptionValidator Warning: Split suffix '{snapshot.split_suffix}' missing from end of caption '{caption}'"
            )

        return True


TV_REDUCTION_ORDER = (
    "episode_title",
    "esubs",
    "ott",
    "audio",
    "codec",
)

MOVIE_REDUCTION_ORDER = (
    "esubs",
    "ott",
    "audio",
    "codec",
)


class SmartNameFitter:
    def fit(
        self,
        parts: SmartNameParts,
        media_type: SmartMediaType,
        prefix: str = "",
        suffix: str = "",
    ) -> Optional[str]:
        clean_prefix = re.sub(r"<.*?>", "", prefix or "").replace(r"\s", " ")
        clean_suffix = re.sub(r"<.*?>", "", suffix or "").replace(r"\s", " ")

        affix_overhead = len(clean_prefix) + len(clean_suffix)
        max_target_length = 60 - affix_overhead
        if max_target_length <= 0:
            return None

        def render():
            title_c = clean_component(parts.title)
            values = [title_c]
            if parts.identity:
                values.append(clean_component(parts.identity))
            if parts.year:
                values.append(clean_component(parts.year))
            if parts.episode_title:
                values.append(clean_component(parts.episode_title))
            if parts.quality:
                values.append(clean_component(parts.quality))
            if parts.ott:
                values.append(clean_component(parts.ott))
            if parts.audio:
                values.append(clean_component(parts.audio))
            if parts.codec:
                values.append(clean_component(parts.codec))
            if parts.esubs:
                values.append("ESubs")

            base = ".".join(v for v in values if v)
            return f"{clean_prefix}{base}{clean_suffix}{parts.extension}{parts.split_suffix}"

        candidate = render()
        if len(candidate) <= 60:
            return candidate

        reduction_order = (
            TV_REDUCTION_ORDER
            if media_type != SmartMediaType.MOVIE
            else MOVIE_REDUCTION_ORDER
        )

        for field in reduction_order:
            if field == "esubs":
                parts.esubs = False
            else:
                setattr(parts, field, None)

            candidate = render()
            if len(candidate) <= 60:
                return candidate

        fixed_parts = []
        if parts.identity:
            fixed_parts.append(clean_component(parts.identity))
        if parts.year:
            fixed_parts.append(clean_component(parts.year))
        if parts.quality:
            fixed_parts.append(clean_component(parts.quality))
        fixed_str = ".".join(fixed_parts)
        if fixed_str:
            fixed_str = f".{fixed_str}"

        non_title_overhead = len(
            f"{clean_prefix}{fixed_str}{clean_suffix}{parts.extension}{parts.split_suffix}"
        )
        title_budget = 60 - non_title_overhead

        shortened = shorten_words(parts.title, title_budget)
        if shortened:
            parts.title = shortened
            candidate = render()
            if len(candidate) <= 60:
                return candidate

        return None


class SmartAutoRename:
    def __init__(self, resolver: Optional[CanonicalMetadataResolver] = None):
        self.resolver = resolver or CanonicalMetadataResolver()
        self.builder = SmartFilenameBuilder()
        self.fitter = SmartNameFitter()

    async def rename(
        self,
        file_path: str,
        prefix: str = "",
        suffix: str = "",
    ) -> str:
        new_path, _ = await self.rename_get_meta(
            file_path, prefix=prefix, suffix=suffix
        )
        return new_path

    async def rename_get_meta(
        self,
        file_path: str,
        prefix: str = "",
        suffix: str = "",
        confirmation_callback=None,
    ) -> Tuple[str, dict]:
        filename = ospath.basename(file_path)
        meta = {}

        if not await is_video_media(file_path):
            LOGGER.info(f"Smart Autorename skipped (non-video media): {filename}")
            return file_path, meta

        ctx = parse_smart_filename(filename)
        if ctx.media_type == SmartMediaType.UNKNOWN:
            LOGGER.info(f"Smart Autorename skipped (unknown media type): {filename}")
            return file_path, meta

        media = await probe_smart_media_metadata(file_path, ctx)

        canonical = None
        if (
            ctx.media_type
            in (SmartMediaType.SINGLE_EPISODE, SmartMediaType.EPISODE_RANGE)
            and ctx.season is not None
            and ctx.episode_start is not None
        ):
            canonical = await self.resolver.resolve_episode(
                series_title=ctx.title,
                season=ctx.season,
                episode=ctx.episode_start,
                year=ctx.year,
                is_anime=ctx.is_anime,
            )
        elif ctx.title:
            canonical = await self.resolver.resolve_title(
                title=ctx.title,
                year=ctx.year,
                media_type=ctx.media_type.value,
                is_anime=ctx.is_anime,
            )

        if confirmation_callback:
            assessment = assess_canonical_candidate(
                ctx.title,
                ctx.year,
                ctx.media_type.value,
                canonical,
                query_season=ctx.season,
                query_episode=ctx.episode_start,
            )
            if assessment["confidence"] != "HIGH":
                decision = await confirmation_callback(
                    file_path, ctx, canonical, assessment
                )
                action = (decision or {}).get("action")
                if action in ("skip", "timeout"):
                    LOGGER.info(
                        "Smart Autorename skipped by confirmation decision: %s",
                        filename,
                    )
                    return file_path, meta
                if action == "cancel":
                    return file_path, meta
                if action == "manual_tmdb":
                    canonical = (decision or {}).get("metadata")
                elif action != "good_to_go":
                    LOGGER.warning(
                        "Smart Autorename received an unknown confirmation action for %s",
                        filename,
                    )
                    return file_path, meta

        # A season URL is reusable for every file in that season pack. Map the
        # current file's episode number to the exact episode title from TMDB.
        if canonical and canonical.selection_type == "season_pack":
            canonical = copy(canonical)
            if ctx.season is not None:
                canonical.season = ctx.season
            if ctx.episode_start is not None:
                episode_map = canonical.episode_titles or {}
                requested_episodes = range(
                    ctx.episode_start, (ctx.episode_end or ctx.episode_start) + 1
                )
                missing_episodes = [
                    number for number in requested_episodes if number not in episode_map
                ]
                if missing_episodes:
                    LOGGER.warning(
                        "Smart Autorename skipped: TMDB season %s has no mapping for episode(s) %s in %s",
                        ctx.season,
                        ", ".join(map(str, missing_episodes)),
                        filename,
                    )
                    return file_path, meta
                canonical.episode = ctx.episode_start
                canonical.episode_title = episode_map.get(ctx.episode_start) or None

        if canonical and canonical.season is not None and canonical.episode is not None:
            if (
                ctx.media_type == SmartMediaType.EPISODE_RANGE
                and ctx.episode_end is not None
                and ctx.episode_start is not None
            ):
                ep_diff = ctx.episode_end - ctx.episode_start
                ctx.episode_start = canonical.episode
                ctx.episode_end = canonical.episode + ep_diff
            else:
                ctx.episode_start = canonical.episode
            ctx.season = canonical.season
            ctx.has_explicit_season = True

        parts = self.builder.make_parts(ctx, canonical, media)

        if not parts:
            LOGGER.warning(
                f"Smart Autorename skipped: required metadata unavailable for {filename}"
            )
            return file_path, meta

        snapshot = self.builder.build_snapshot(filename, ctx, canonical, media)
        smart_caption_v1_val = self.builder.render_v1(
            snapshot, prefix=prefix, suffix=suffix
        )
        smart_caption_v2_val = self.builder.render_v2(
            snapshot, prefix=prefix, suffix=suffix
        )

        meta = {
            "show_name": snapshot.title or "",
            "season": f"{snapshot.season:02d}" if snapshot.season is not None else "",
            "episode": f"{snapshot.episode_start:02d}"
            if snapshot.episode_start is not None
            else "",
            "title": snapshot.episode_title or "",
            "year": str(snapshot.year or ""),
            "source": snapshot.ott
            or (snapshot.source_tokens[0] if snapshot.source_tokens else ""),
            "codec": ", ".join(snapshot.codec_tokens)
            if snapshot.codec_tokens
            else (parts.codec or ""),
            "encoder": ", ".join(snapshot.encoder_tokens)
            if snapshot.encoder_tokens
            else "",
            "quality": snapshot.quality or parts.quality or "",
            "bit_depth": snapshot.bit_depth or "",
            "hdr": snapshot.hdr or "",
            "audio_summary": snapshot.audio_summary or "",
            "subtitle": snapshot.subtitle_summary or "",
            "split_suffix": snapshot.split_suffix or "",
            "smartcaption": smart_caption_v2_val,
            "smartcaptionv1": smart_caption_v1_val,
            "smartcaptionv2": smart_caption_v2_val,
        }

        new_name = self.fitter.fit(
            parts,
            media_type=ctx.media_type,
            prefix=prefix,
            suffix=suffix,
        )

        if not new_name:
            raise SmartRenameLengthError(
                "Cannot perform Smart Autorename: the required filename exceeds Telegram's 60-character limit."
            )

        new_path = ospath.join(ospath.dirname(file_path), new_name)
        if new_path != file_path:
            if await aiopath.exists(new_path):
                if file_path != new_path:
                    raise SmartRenameError(f"Target already exists: {new_path}")
                return new_path, meta

            LOGGER.info(f"Smart Autorename: {filename} -> {new_name}")
            await rename(file_path, new_path)

        return new_path, meta


async def is_video_media(file_path: str) -> bool:
    doc_type = await get_document_type(file_path)
    if doc_type and isinstance(doc_type, (tuple, list)) and doc_type[0]:
        return True
    stem, ext, split = split_media_filename(ospath.basename(file_path))
    return ext in {
        ".mkv",
        ".mp4",
        ".avi",
        ".webm",
        ".flv",
        ".mov",
        ".m4v",
        ".3gp",
        ".ts",
        ".m2ts",
        ".wmv",
        ".asf",
        ".divx",
        ".ogv",
        ".vob",
        ".mpg",
        ".mpeg",
    }
