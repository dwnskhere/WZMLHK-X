from contextlib import suppress
from dataclasses import dataclass
from difflib import SequenceMatcher
from logging import getLogger
import re
from typing import Optional
from urllib.parse import quote
from niquests import AsyncSession

from bot.core.config_manager import Config
from .bot_utils import sync_to_async

LOGGER = getLogger(__name__)


_STOPWORDS = {
    "is",
    "the",
    "a",
    "an",
    "of",
    "and",
    "in",
    "on",
    "at",
    "by",
    "to",
    "for",
    "with",
    "or",
}


def is_title_similar(query: str, res_title: str, min_ratio: float = 0.55) -> bool:
    if not query or not res_title:
        return False

    q_norm = re.sub(r"([a-z])([A-Z])", r"\1 \2", query)
    r_norm = re.sub(r"([a-z])([A-Z])", r"\1 \2", res_title)

    q_norm = re.sub(r"[^\w\s]", "", q_norm.lower()).strip()
    r_norm = re.sub(r"[^\w\s]", "", r_norm.lower()).strip()

    if not q_norm or not r_norm:
        return False

    q_words = [w for w in q_norm.split() if w not in _STOPWORDS] or q_norm.split()
    r_words = [w for w in r_norm.split() if w not in _STOPWORDS] or r_norm.split()

    q_set = set(q_words)
    r_set = set(r_words)

    if not q_set or not r_set:
        return False

    intersection = q_set & r_set
    overlap_q = len(intersection) / len(q_set)
    overlap_r = len(intersection) / len(r_set)

    seq_ratio = SequenceMatcher(None, q_norm, r_norm).ratio()

    if seq_ratio >= min_ratio or (overlap_q >= 0.5 and overlap_r >= 0.5):
        return True

    return False


@dataclass
class CanonicalMetadata:
    title: Optional[str] = None
    year: Optional[str] = None
    media_type: Optional[str] = None
    series_title: Optional[str] = None
    episode_title: Optional[str] = None
    provider: Optional[str] = None
    provider_id: Optional[str] = None
    ott: Optional[str] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    source: Optional[str] = None
    selection_type: Optional[str] = None
    episode_titles: Optional[dict[int, str]] = None
    provider_conflict: bool = False


def _normalized_provider_title(value: Optional[str]) -> str:
    value = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(value or ""))
    value = re.sub(r"[^a-z0-9]+", " ", value.casefold())
    return " ".join(w for w in value.split() if w not in _STOPWORDS)


def _providers_disagree(left: CanonicalMetadata, right: CanonicalMetadata) -> bool:
    left_title = left.series_title or left.title
    right_title = right.series_title or right.title
    if left_title and right_title and _normalized_provider_title(left_title) != _normalized_provider_title(right_title):
        return True
    if left.year and right.year and str(left.year)[:4] != str(right.year)[:4]:
        return True
    if (
        left.season is not None
        and right.season is not None
        and left.episode is not None
        and right.episode is not None
        and (left.season, left.episode) != (right.season, right.episode)
    ):
        return True
    if left.media_type and right.media_type and left.media_type != right.media_type:
        return True
    return False


def assess_canonical_candidate(
    query: str,
    query_year: Optional[str],
    media_type: Optional[str],
    candidate: Optional[CanonicalMetadata],
    query_season: Optional[int] = None,
    query_episode: Optional[int] = None,
) -> dict:
    """Classify a provider candidate conservatively before autorename applies it."""
    if not candidate:
        return {"confidence": "NONE", "reason": "no_metadata_candidate"}
    if candidate.provider_conflict:
        return {"confidence": "LOW", "reason": "provider_disagreement"}

    expected_media_type = "movie" if media_type == "movie" else "tv"
    if candidate.media_type and candidate.media_type != expected_media_type:
        return {
            "confidence": "LOW",
            "reason": "media_type_mismatch",
            "candidate_media_type": candidate.media_type,
        }

    if media_type == "episode_range":
        return {
            "confidence": "MEDIUM",
            "reason": "episode_range_requires_season_check",
        }

    if media_type in ("single_episode", "episode_range") and query_season is not None:
        if candidate.season is None or candidate.episode is None:
            return {
                "confidence": "MEDIUM",
                "reason": "episode_coordinates_unverified",
            }
        if candidate.season != query_season or (
            query_episode is not None and candidate.episode != query_episode
        ):
            return {
                "confidence": "LOW",
                "reason": "episode_coordinates_mismatch",
            }

    candidate_title = (
        candidate.series_title or candidate.title or ""
        if media_type in ("single_episode", "episode_range", "season_pack")
        else candidate.title or candidate.series_title or ""
    )
    if not query or not candidate_title:
        return {"confidence": "NONE", "reason": "missing_title"}

    def normalize(value: str) -> str:
        value = re.sub(r"([a-z])([A-Z])", r"\1 \2", value)
        value = re.sub(r"[^a-z0-9]+", " ", value.casefold())
        words = [w for w in value.split() if w not in _STOPWORDS]
        return " ".join(words)

    query_norm = normalize(query)
    candidate_norm = normalize(candidate_title)
    if not query_norm or not candidate_norm:
        return {"confidence": "NONE", "reason": "unusable_title"}

    query_year = str(query_year or "").strip()[:4]
    candidate_year = str(candidate.year or "").strip()[:4]
    if query_year and candidate_year and query_year != candidate_year:
        return {
            "confidence": "LOW",
            "reason": "year_mismatch",
            "candidate_year": candidate_year,
        }

    if query_norm == candidate_norm:
        if query_year and not candidate_year:
            return {
                "confidence": "MEDIUM",
                "reason": "candidate_year_unavailable",
            }
        return {"confidence": "HIGH", "reason": "exact_title_match"}

    query_words = set(query_norm.split())
    candidate_words = set(candidate_norm.split())
    shared = query_words & candidate_words
    overlap_query = len(shared) / max(len(query_words), 1)
    overlap_candidate = len(shared) / max(len(candidate_words), 1)
    sequence_ratio = SequenceMatcher(None, query_norm, candidate_norm).ratio()

    # Fuzzy/franchise matches are never auto-approved. A user must verify them.
    if len(shared) < 2 or overlap_query < 0.60 or overlap_candidate < 0.60:
        return {
            "confidence": "LOW",
            "reason": "distinctive_title_mismatch",
            "overlap_query": round(overlap_query, 3),
            "overlap_candidate": round(overlap_candidate, 3),
        }
    return {
        "confidence": "MEDIUM",
        "reason": "fuzzy_title_match",
        "sequence_ratio": round(sequence_ratio, 3),
        "overlap_query": round(overlap_query, 3),
        "overlap_candidate": round(overlap_candidate, 3),
    }


class KitsuProvider:
    KITSU_BASE = "https://kitsu.io/api/edge"

    async def _request(self, path: str, params: dict | None = None):
        try:
            async with AsyncSession(timeout=10) as client:
                headers = {
                    "Accept": "application/vnd.api+json",
                    "Content-Type": "application/vnd.api+json",
                }
                resp = await client.get(
                    f"{self.KITSU_BASE}{path}", params=params, headers=headers
                )
                if resp.status_code != 200:
                    return None
                return resp.json()
        except Exception as exc:
            LOGGER.warning(f"Kitsu request failed: {exc}")
            return None

    async def find_title(
        self,
        title: str,
        year: Optional[str] = None,
        media_type: Optional[str] = None,
    ) -> Optional[CanonicalMetadata]:
        params = {"filter[text]": title, "page[limit]": "5"}
        data = await self._request("/anime", params)
        if not data or "data" not in data or not data["data"]:
            return None

        best_match = None
        for item in data["data"]:
            attrs = item.get("attributes", {})
            titles = attrs.get("titles", {})
            canonical = (
                attrs.get("canonicalTitle")
                or titles.get("en")
                or titles.get("en_jp")
                or titles.get("ja_jp")
            )

            if not canonical:
                continue

            q_clean = title.strip().lower()
            c_clean = canonical.strip().lower()
            if q_clean == c_clean:
                start_date = attrs.get("startDate") or ""
                disp_year = start_date[:4] if len(start_date) >= 4 else year
                item_id = str(item.get("id"))
                return CanonicalMetadata(
                    title=canonical,
                    year=disp_year,
                    media_type="movie" if media_type == "movie" else "tv",
                    series_title=canonical
                    if media_type in ("single_episode", "episode_range", "season_pack")
                    else None,
                    provider="kitsu",
                    provider_id=item_id,
                )

            if not best_match and is_title_similar(title, canonical):
                start_date = attrs.get("startDate") or ""
                disp_year = start_date[:4] if len(start_date) >= 4 else year
                item_id = str(item.get("id"))
                best_match = CanonicalMetadata(
                    title=canonical,
                    year=disp_year,
                    media_type="movie" if media_type == "movie" else "tv",
                    series_title=canonical
                    if media_type in ("single_episode", "episode_range", "season_pack")
                    else None,
                    provider="kitsu",
                    provider_id=item_id,
                )

        return best_match

    async def find_episode(
        self,
        series_title: str,
        season: int,
        episode: int,
        year: Optional[str] = None,
    ) -> Optional[CanonicalMetadata]:
        anime_meta = await self.find_title(
            series_title, year=year, media_type="single_episode"
        )
        if not anime_meta or not anime_meta.provider_id:
            return None

        anime_id = anime_meta.provider_id
        ep_data = await self._request(
            f"/anime/{anime_id}/episodes",
            params={"filter[number]": str(episode)},
        )

        ep_title = None
        episode_found = bool(ep_data and ep_data.get("data"))
        if episode_found:
            ep_attrs = ep_data["data"][0].get("attributes", {})
            ep_titles = ep_attrs.get("titles", {})
            candidate = (
                ep_attrs.get("canonicalTitle")
                or ep_titles.get("en_us")
                or ep_titles.get("en")
                or ep_titles.get("en_jp")
            )
            if candidate and not re.match(r"(?i)^episode\s*\d+$", candidate.strip()):
                ep_title = candidate

        return CanonicalMetadata(
            series_title=anime_meta.series_title,
            year=anime_meta.year,
            media_type="tv",
            episode_title=ep_title,
            provider="kitsu",
            provider_id=anime_id,
        )


class CinemetaProvider:
    CINEMETA_BASE = "https://v3-cinemeta.strem.io"

    async def _request(self, path: str):
        try:
            async with AsyncSession(timeout=10) as client:
                resp = await client.get(f"{self.CINEMETA_BASE}{path}")
                if resp.status_code != 200:
                    return None
                return resp.json()
        except Exception as exc:
            LOGGER.warning(f"Cinemeta request failed: {exc}")
            return None

    async def find_title(
        self,
        title: str,
        year: Optional[str] = None,
        media_type: Optional[str] = None,
    ) -> Optional[CanonicalMetadata]:
        stype = "movie" if media_type == "movie" else "series"
        q_encoded = quote(title.strip())
        data = await self._request(f"/catalog/{stype}/top/search={q_encoded}.json")
        if not data or "metas" not in data or not data["metas"]:
            return None

        for item in data["metas"]:
            item_name = item.get("name")
            if not item_name:
                continue

            if is_title_similar(title, item_name):
                item_year = item.get("releaseInfo") or item.get("year") or year
                if item_year:
                    item_year = str(item_year)[:4]

                return CanonicalMetadata(
                    title=item_name,
                    year=item_year,
                    media_type="movie" if stype == "movie" else "tv",
                    series_title=item_name
                    if media_type in ("single_episode", "episode_range", "season_pack")
                    else None,
                    provider="cinemeta",
                    provider_id=item.get("id"),
                )

        return None

    async def find_episode(
        self,
        series_title: str,
        season: int,
        episode: int,
        year: Optional[str] = None,
    ) -> Optional[CanonicalMetadata]:
        meta = await self.find_title(
            series_title, year=year, media_type="single_episode"
        )
        if not meta or not meta.provider_id:
            return None

        detail = await self._request(f"/meta/series/{meta.provider_id}.json")
        ep_title = None
        mapped_season = None
        mapped_ep = None

        if detail and "meta" in detail:
            videos = detail["meta"].get("videos", [])
            for vid in videos:
                if vid.get("season") == season and vid.get("episode") == episode:
                    candidate = vid.get("title") or vid.get("name")
                    if candidate and not re.match(
                        r"(?i)^episode\s*\d+$", candidate.strip()
                    ):
                        ep_title = candidate
                    mapped_season = vid.get("season")
                    mapped_ep = vid.get("episode")
                    break

            if not ep_title and episode > 20:
                for idx, vid in enumerate(videos):
                    if vid.get("number") == episode or (idx + 1) == episode:
                        candidate = vid.get("title") or vid.get("name")
                        if candidate and not re.match(
                            r"(?i)^episode\s*\d+$", candidate.strip()
                        ):
                            ep_title = candidate
                        mapped_season = vid.get("season")
                        mapped_ep = vid.get("episode")
                        break

        return CanonicalMetadata(
            series_title=meta.series_title,
            year=meta.year,
            media_type="tv",
            episode_title=ep_title,
            season=mapped_season,
            episode=mapped_ep,
            provider="cinemeta",
            provider_id=meta.provider_id,
        )


class TVDBProvider:
    """Uses Cinemeta TVDB catalogue and lookup endpoints as TVDB proxy."""

    async def find_title(
        self,
        title: str,
        year: Optional[str] = None,
        media_type: Optional[str] = None,
    ) -> Optional[CanonicalMetadata]:
        cinemeta = CinemetaProvider()
        res = await cinemeta.find_title(title, year=year, media_type=media_type)
        if res:
            res.provider = "tvdb"
        return res

    async def find_episode(
        self,
        series_title: str,
        season: int,
        episode: int,
        year: Optional[str] = None,
    ) -> Optional[CanonicalMetadata]:
        cinemeta = CinemetaProvider()
        res = await cinemeta.find_episode(
            series_title, season=season, episode=episode, year=year
        )
        if res:
            res.provider = "tvdb"
        return res


class IMDbProvider:
    async def find_title(
        self,
        title: str,
        year: Optional[str] = None,
        media_type: Optional[str] = None,
    ) -> Optional[CanonicalMetadata]:
        def lookup():
            try:
                from imdbio import search_title, get_movie
            except ImportError:
                return None

            clean_q = title.strip().lower()
            if not clean_q:
                return None

            res = search_title(clean_q)
            results = getattr(res, "titles", []) or []
            if not results:
                return None

            if year:
                matched_year = [
                    item
                    for item in results
                    if str(getattr(item, "year", "") or "") == str(year)
                ]
                if matched_year:
                    results = matched_year

            if media_type == "movie":
                preferred = [
                    item for item in results if getattr(item, "kind", None) == "movie"
                ]
            elif media_type in ("single_episode", "episode_range", "season_pack"):
                preferred = [
                    item
                    for item in results
                    if getattr(item, "kind", None)
                    in ("tvSeries", "tvMiniSeries", "tvShow")
                ]
            else:
                preferred = [
                    item
                    for item in results
                    if getattr(item, "kind", None)
                    in ("movie", "tvSeries", "tvMiniSeries")
                ]

            targets = preferred or results
            target = None
            for item in targets:
                item_title = getattr(item, "title", None)
                if item_title and is_title_similar(title, item_title):
                    target = item
                    break

            if not target:
                return None

            item_id = getattr(target, "id", None)
            if not item_id:
                return None

            m = get_movie(item_id)
            if not m:
                return None

            disp_title = getattr(m, "title", None) or getattr(target, "title", None)
            if not disp_title or not is_title_similar(title, disp_title):
                return None

            disp_year = (
                str(getattr(m, "year", None) or getattr(target, "year", None) or "")
                or None
            )

            return CanonicalMetadata(
                title=disp_title,
                year=disp_year,
                media_type="movie" if getattr(target, "kind", None) == "movie" else "tv",
                series_title=disp_title
                if media_type in ("single_episode", "episode_range", "season_pack")
                else None,
                provider="imdb",
                provider_id=f"tt{item_id}",
            )

        try:
            return await sync_to_async(lookup)
        except Exception as exc:
            LOGGER.warning(f"IMDb Smart Rename lookup failed: {exc}")
            return None

    async def find_episode(
        self,
        series_title: str,
        season: int,
        episode: int,
        year: Optional[str] = None,
    ) -> Optional[CanonicalMetadata]:
        def lookup():
            try:
                from imdbio import search_title, get_movie
            except ImportError:
                return None

            clean_q = series_title.strip().lower()
            if not clean_q:
                return None

            res = search_title(clean_q)
            results = getattr(res, "titles", []) or []
            if not results:
                return None

            if year:
                matched_year = [
                    item
                    for item in results
                    if str(getattr(item, "year", "") or "") == str(year)
                ]
                if matched_year:
                    results = matched_year

            series_matches = [
                item
                for item in results
                if getattr(item, "kind", None) in ("tvSeries", "tvMiniSeries", "tvShow")
            ] or results

            target = None
            for item in series_matches:
                item_title = getattr(item, "title", None)
                if item_title and is_title_similar(series_title, item_title):
                    target = item
                    break

            if not target:
                return None

            series_id = getattr(target, "id", None)
            if not series_id:
                return None

            s_obj = get_movie(series_id)
            series_disp = (
                getattr(s_obj, "title", None)
                if s_obj
                else getattr(target, "title", None)
            )

            if not series_disp or not is_title_similar(series_title, series_disp):
                return None

            ep_title = None
            episode_found = False
            if s_obj and hasattr(s_obj, "episodes"):
                with suppress(Exception):
                    episodes_data = getattr(s_obj, "episodes", None)
                    if episodes_data:
                        se_eps = episodes_data.get(season, {})
                        ep_obj = se_eps.get(episode)
                        if ep_obj:
                            episode_found = True
                            ep_title = getattr(ep_obj, "title", None)

            return CanonicalMetadata(
                series_title=series_disp,
                media_type="tv",
                episode_title=ep_title,
                season=season if episode_found else None,
                episode=episode if episode_found else None,
                provider="imdb",
                provider_id=f"tt{series_id}",
            )

        try:
            return await sync_to_async(lookup)
        except Exception as exc:
            LOGGER.warning(f"IMDb episode lookup failed: {exc}")
            return None


TMDB_API_BASE = "https://api.themoviedb.org/3"


class TMDbProvider:
    async def _request(self, path: str, params: dict | None = None):
        token = str(Config.TMDB_ACCESS_TOKEN or "").strip()
        if not token:
            return None

        params = dict(params or {})
        headers = {"accept": "application/json"}

        if len(token) < 50:
            params["api_key"] = token
        else:
            headers["Authorization"] = f"Bearer {token}"

        try:
            async with AsyncSession(timeout=15) as client:
                resp = await client.get(
                    f"{TMDB_API_BASE}{path}",
                    params=params,
                    headers=headers,
                )
                if resp.status_code != 200:
                    return None
                return resp.json()
        except Exception as exc:
            LOGGER.warning(f"TMDb Smart Rename request failed: {exc}")
            return None

    async def find_title(
        self,
        title: str,
        year: Optional[str] = None,
        media_type: Optional[str] = None,
    ) -> Optional[CanonicalMetadata]:
        if not str(Config.TMDB_ACCESS_TOKEN or "").strip():
            return None

        endpoint = (
            "/search/movie"
            if media_type == "movie"
            else "/search/tv"
            if media_type in ("single_episode", "episode_range", "season_pack")
            else "/search/multi"
        )
        params = {"query": title, "include_adult": "false", "language": "en-US"}
        if year and media_type == "movie":
            params["year"] = str(year)
        elif year and media_type != "movie":
            params["first_air_date_year"] = str(year)

        data = await self._request(endpoint, params)
        if not data:
            return None

        results = [
            r for r in data.get("results", []) if r.get("media_type") != "person"
        ]
        if not results:
            return None

        item = None
        for r in results:
            disp = (
                r.get("title")
                or r.get("name")
                or r.get("original_title")
                or r.get("original_name")
            )
            if disp and is_title_similar(title, disp):
                item = r
                break

        if not item:
            return None

        disp_title = (
            item.get("title")
            or item.get("name")
            or item.get("original_title")
            or item.get("original_name")
        )
        rel_date = item.get("release_date") or item.get("first_air_date") or ""
        disp_year = rel_date[:4] if len(rel_date) >= 4 else None

        networks = None
        if item.get("media_type") == "tv" or media_type in (
            "single_episode",
            "episode_range",
            "season_pack",
        ):
            tv_details = await self._request(f"/tv/{item['id']}")
            if tv_details and tv_details.get("networks"):
                nets = [
                    n.get("name")
                    for n in tv_details.get("networks", [])
                    if n.get("name")
                ]
                if nets:
                    networks = nets[0]

        return CanonicalMetadata(
            title=disp_title,
            year=disp_year,
            media_type=(
                "movie"
                if endpoint == "/search/movie"
                else "tv"
                if endpoint == "/search/tv"
                else item.get("media_type")
            ),
            series_title=disp_title
            if media_type in ("single_episode", "episode_range", "season_pack")
            else None,
            provider="tmdb",
            provider_id=str(item.get("id")),
            ott=networks,
        )

    async def resolve_manual(
        self,
        kind: str,
        tmdb_id: int,
        season: Optional[int] = None,
        episode: Optional[int] = None,
    ) -> tuple[Optional[CanonicalMetadata], Optional[str]]:
        """Resolve an exact user-supplied TMDB record without fuzzy search."""
        if not str(Config.TMDB_ACCESS_TOKEN or "").strip():
            return None, "TMDB_ACCESS_TOKEN is not configured."

        if kind == "movie":
            data = await self._request(f"/movie/{tmdb_id}")
            if not data or not data.get("title"):
                return None, "TMDB could not retrieve that movie. Check the ID or TMDB API credentials."
            release_date = str(data.get("release_date") or "")
            return (
                CanonicalMetadata(
                    title=data.get("title") or data.get("original_title"),
                    year=release_date[:4] if len(release_date) >= 4 else None,
                    media_type="movie",
                    provider="tmdb",
                    provider_id=str(tmdb_id),
                    source="manual_tmdb",
                    selection_type="movie",
                ),
                None,
            )

        if kind in ("single_episode", "season_pack"):
            tv = await self._request(f"/tv/{tmdb_id}")
            if not tv or not (tv.get("name") or tv.get("original_name")):
                return None, "TMDB could not retrieve that TV series. Check the ID or TMDB API credentials."
            first_air_date = str(tv.get("first_air_date") or "")
            year = first_air_date[:4] if len(first_air_date) >= 4 else None
            networks = [n.get("name") for n in tv.get("networks", []) if n.get("name")]
            series_title = tv.get("name") or tv.get("original_name")

            if kind == "single_episode":
                ep = await self._request(
                    f"/tv/{tmdb_id}/season/{season}/episode/{episode}"
                )
                if not ep or not ep.get("name"):
                    return None, "TMDB could not retrieve that episode. Verify the season and episode numbers."
                if int(ep.get("season_number", season)) != season or int(
                    ep.get("episode_number", episode)
                ) != episode:
                    return None, "TMDB returned an episode with different season/episode coordinates."
                return (
                    CanonicalMetadata(
                        series_title=series_title,
                        year=year,
                        media_type="tv",
                        episode_title=ep.get("name"),
                        provider="tmdb",
                        provider_id=str(tmdb_id),
                        ott=networks[0] if networks else None,
                        season=season,
                        episode=episode,
                        source="manual_tmdb",
                        selection_type="single_episode",
                    ),
                    None,
                )

            season_data = await self._request(f"/tv/{tmdb_id}/season/{season}")
            if not season_data or "episodes" not in season_data:
                return None, "TMDB could not retrieve that season. Verify the series and season numbers."
            if int(season_data.get("season_number", season)) != season:
                return None, "TMDB returned a different season number."
            episode_titles = {}
            for item in season_data.get("episodes", []):
                try:
                    number = int(item.get("episode_number"))
                except (TypeError, ValueError):
                    continue
                episode_titles[number] = item.get("name") or ""
            return (
                CanonicalMetadata(
                    series_title=series_title,
                    year=year,
                    media_type="tv",
                    provider="tmdb",
                    provider_id=str(tmdb_id),
                    ott=networks[0] if networks else None,
                    season=season,
                    source="manual_tmdb",
                    selection_type="season_pack",
                    episode_titles=episode_titles,
                ),
                None,
            )

        return None, "Unsupported TMDB URL type."

    async def find_episode(
        self,
        series_title: str,
        season: int,
        episode: int,
        year: Optional[str] = None,
    ) -> Optional[CanonicalMetadata]:
        if not str(Config.TMDB_ACCESS_TOKEN or "").strip():
            return None

        params = {"query": series_title, "include_adult": "false", "language": "en-US"}
        if year:
            params["first_air_date_year"] = str(year)

        search_data = await self._request("/search/tv", params)
        if not search_data or not search_data.get("results"):
            return None

        tv_item = None
        for r in search_data["results"]:
            disp = r.get("name") or r.get("original_name")
            if disp and is_title_similar(series_title, disp):
                tv_item = r
                break

        if not tv_item:
            return None

        tv_id = tv_item["id"]
        series_disp = tv_item.get("name") or tv_item.get("original_name")

        ep_data = await self._request(f"/tv/{tv_id}/season/{season}/episode/{episode}")
        if not ep_data:
            return None
        ep_title = ep_data.get("name")

        tv_details = await self._request(f"/tv/{tv_id}")
        first_air_date = str((tv_details or {}).get("first_air_date") or "")
        networks = [
            n.get("name")
            for n in (tv_details or {}).get("networks", [])
            if n.get("name")
        ]
        return CanonicalMetadata(
            series_title=series_disp,
            year=first_air_date[:4] if len(first_air_date) >= 4 else None,
            media_type="tv",
            episode_title=ep_title,
            ott=networks[0] if networks else None,
            season=season,
            episode=episode,
            provider="tmdb",
            provider_id=str(tv_id),
        )


class CanonicalMetadataResolver:
    def __init__(self):
        self.kitsu = KitsuProvider()
        self.tvdb = TVDBProvider()
        self.tmdb = TMDbProvider()
        self.imdb = IMDbProvider()
        self.cinemeta = CinemetaProvider()
        self.cache: dict[tuple, CanonicalMetadata] = {}

    def _get_provider_chain(
        self, media_type: Optional[str], is_anime: bool = False
    ) -> list:
        has_tmdb = bool(str(Config.TMDB_ACCESS_TOKEN or "").strip())

        if is_anime:
            # 🇯🇵 Anime: Kitsu > TVDB > TMDB (if token) > IMDb > Cinemeta
            chain = [self.kitsu, self.tvdb]
            if has_tmdb:
                chain.append(self.tmdb)
            chain.extend([self.imdb, self.cinemeta])
            return chain

        if media_type == "movie":
            # 🎬 Movies: TMDB (if token) > IMDb > Cinemeta
            chain = []
            if has_tmdb:
                chain.append(self.tmdb)
            chain.extend([self.imdb, self.cinemeta])
            return chain

        # 📺 Series: TVDB > TMDB (if token) > IMDb > Cinemeta
        chain = [self.tvdb]
        if has_tmdb:
            chain.append(self.tmdb)
        chain.extend([self.imdb, self.cinemeta])
        return chain

    async def resolve_title(
        self,
        title: str,
        year: Optional[str] = None,
        media_type: Optional[str] = None,
        is_anime: bool = False,
    ) -> Optional[CanonicalMetadata]:
        cache_key = (
            title.strip().lower(),
            str(year or ""),
            str(media_type or ""),
            bool(is_anime),
            "title",
        )
        if cache_key in self.cache:
            return self.cache[cache_key]

        providers = self._get_provider_chain(media_type, is_anime)
        res = None
        first_match_index = None
        for index, provider in enumerate(providers):
            candidate = await provider.find_title(title, year, media_type)
            if candidate and (candidate.title or candidate.series_title):
                if res is None:
                    res = candidate
                    first_match_index = index
                else:
                    if _providers_disagree(res, candidate):
                        res.provider_conflict = True
                    elif not res.year and candidate.year:
                        res.year = candidate.year
                    if not res.ott and candidate.ott:
                        res.ott = candidate.ott
                    break
            # Check one additional provider after the first match. This catches
            # disagreement without turning every rename into a full provider sweep.
            if res is not None and first_match_index is not None and index > first_match_index:
                break

        if res:
            if len(self.cache) > 200:
                self.cache.clear()
            self.cache[cache_key] = res
        return res

    async def resolve_episode(
        self,
        series_title: str,
        season: int,
        episode: int,
        year: Optional[str] = None,
        is_anime: bool = False,
    ) -> Optional[CanonicalMetadata]:
        cache_key = (
            series_title.strip().lower(),
            season,
            episode,
            str(year or ""),
            bool(is_anime),
            "episode",
        )
        if cache_key in self.cache:
            return self.cache[cache_key]

        providers = self._get_provider_chain("single_episode", is_anime)
        res = None
        for index, provider in enumerate(providers):
            cand = await provider.find_episode(series_title, season, episode, year)
            if cand and cand.series_title:
                if not res:
                    res = cand
                else:
                    if _providers_disagree(res, cand):
                        res.provider_conflict = True
                    if cand.episode_title and not res.episode_title:
                        res.episode_title = cand.episode_title
                    if (
                        cand.season is not None
                        and cand.episode is not None
                        and (res.season is None or res.episode is None)
                    ):
                        res.season = cand.season
                        res.episode = cand.episode

            # Once a complete candidate exists, inspect one additional provider
            # for disagreement, then stop to keep lookup latency bounded.
            if (
                index >= 1
                and res
                and res.episode_title
                and res.season is not None
                and res.episode is not None
            ):
                break

        if res:
            if len(self.cache) > 200:
                self.cache.clear()
            self.cache[cache_key] = res
        return res
