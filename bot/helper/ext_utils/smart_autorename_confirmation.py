"""Interactive safety gate for uncertain Smart Autorename metadata matches."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from html import escape
from logging import getLogger
import re
from secrets import token_hex
from urllib.parse import urlparse

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.core.config_manager import Config
from bot.core.tg_client import TgClient
from bot.helper.ext_utils.smart_autorename_providers import TMDbProvider

LOGGER = getLogger(__name__)

_PENDING: dict[str, dict] = {}

_MOVIE_RE = re.compile(r"^/movie/([1-9][0-9]*)/?$")
_EPISODE_RE = re.compile(r"^/tv/([1-9][0-9]*)/season/([1-9][0-9]*)/episode/([1-9][0-9]*)/?$")
_SEASON_RE = re.compile(r"^/tv/([1-9][0-9]*)/season/([1-9][0-9]*)/?$")
_ALLOWED_HOSTS = {"themoviedb.org", "www.themoviedb.org"}

_REASON_TEXT = {
    "no_metadata_candidate": "No provider returned a usable metadata result.",
    "missing_title": "The provider result is missing a usable title.",
    "unusable_title": "The title could not be compared reliably.",
    "year_mismatch": "The candidate release/first-air year conflicts with the year in the filename.",
    "candidate_year_unavailable": "The provider did not return a year to verify the match.",
    "media_type_mismatch": "The provider returned a different media type (movie versus TV).",
    "episode_coordinates_unverified": "The provider could not verify the requested season and episode.",
    "episode_coordinates_mismatch": "The provider returned a different season or episode number.",
    "provider_disagreement": "Metadata providers returned conflicting titles, years, or media types.",
    "episode_range_requires_season_check": "This release contains an episode range; the season URL is needed to verify every episode in the range.",
    "distinctive_title_mismatch": "The match may be based only on shared franchise or generic words.",
    "fuzzy_title_match": "The title is similar but is not an exact match.",
}


def _expected_label(media_type: str, ctx) -> str:
    if media_type == "movie":
        return "Movie"
    if media_type == "season_pack":
        season = getattr(ctx, "season", None)
        return f"Season pack (S{season:02d})" if season is not None else "Season pack"
    season = getattr(ctx, "season", None)
    episode = getattr(ctx, "episode_start", None)
    if season is not None and episode is not None:
        return f"Single episode (S{season:02d}E{episode:02d})"
    return "Single episode"


def _expected_url_help(media_type: str, ctx) -> str:
    if media_type == "movie":
        return "https://www.themoviedb.org/movie/{movie_id}"
    if media_type == "season_pack":
        return "https://www.themoviedb.org/tv/{series_id}/season/{season_number}"
    return (
        "https://www.themoviedb.org/tv/{series_id}/season/{season_number}/"
        "episode/{episode_number}"
    )


def _parse_tmdb_url(value: str) -> tuple[str, int, int | None, int | None] | None:
    value = (value or "").strip().strip("<>\"'")
    try:
        parsed = urlparse(value)
        if (
            parsed.scheme.lower() != "https"
            or (parsed.hostname or "").lower() not in _ALLOWED_HOSTS
            or parsed.username
            or parsed.password
        ):
            return None
    except Exception:
        return None

    path = parsed.path or ""
    match = _MOVIE_RE.fullmatch(path)
    if match:
        return "movie", int(match.group(1)), None, None
    match = _EPISODE_RE.fullmatch(path)
    if match:
        return (
            "single_episode",
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        )
    match = _SEASON_RE.fullmatch(path)
    if match:
        return "season_pack", int(match.group(1)), int(match.group(2)), None
    return None


async def _resolve_manual_url(value: str, expected_type: str, ctx):
    parsed = _parse_tmdb_url(value)
    if not parsed:
        return None, (
            "That is not a supported TMDB URL. Use the exact URL format shown in "
            "the confirmation prompt."
        )

    kind, tmdb_id, season, episode = parsed
    if kind != expected_type:
        expected = _expected_url_help(expected_type, ctx)
        return None, (
            f"This task requires a {expected_type.replace('_', ' ')} URL. "
            f"Expected format: {expected}"
        )

    ctx_season = getattr(ctx, "season", None)
    ctx_episode = getattr(ctx, "episode_start", None)
    if expected_type in ("single_episode", "season_pack"):
        if ctx_season is not None and season != ctx_season:
            return None, (
                f"Season mismatch: filename contains season {ctx_season}, "
                f"but the URL points to season {season}."
            )
        if expected_type == "single_episode" and ctx_episode is not None and episode != ctx_episode:
            return None, (
                f"Episode mismatch: filename contains episode {ctx_episode}, "
                f"but the URL points to episode {episode}."
            )

    metadata, error = await TMDbProvider().resolve_manual(
        kind, tmdb_id, season=season, episode=episode
    )
    return metadata, error


async def request_confirmation(
    listener,
    filename: str,
    ctx,
    candidate,
    assessment: dict,
    expected_type: str | None = None,
):
    """Pause one listener until its owner approves, supplies TMDB URL, or skips."""
    expected_type = expected_type or getattr(ctx.media_type, "value", str(ctx.media_type))
    if expected_type == "episode_range":
        expected_type = "season_pack"

    token = token_hex(5)
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    candidate_title = (
        getattr(candidate, "series_title", None)
        or getattr(candidate, "title", None)
        or "No provider suggestion"
    )
    candidate_year = getattr(candidate, "year", None)
    provider = getattr(candidate, "provider", None) or "None"
    confidence = assessment.get("confidence", "NONE")
    reason = _REASON_TEXT.get(
        assessment.get("reason"), assessment.get("reason", "Metadata is uncertain.")
    )
    title_line = escape(str(candidate_title))
    if candidate_year:
        title_line += f" ({escape(str(candidate_year))})"

    filename = filename if len(filename) <= 680 else f"{filename[:677]}..."
    original = escape(filename)

    message_text = (
        "⚠️ <b>Smart Autorename needs your confirmation</b>\n\n"
        f"<b>Original file:</b> <code>{original}</code>\n"
        f"<b>Detected type:</b> {escape(_expected_label(expected_type, ctx))}\n"
        f"<b>Detected year:</b> {escape(str(getattr(ctx, 'year', None) or 'Unknown'))}\n"
        f"<b>Bot suggestion:</b> {title_line}\n"
        f"<b>Provider:</b> {escape(str(provider))}\n"
        f"<b>Confidence:</b> {escape(str(confidence))}\n"
        f"<b>Reason:</b> {escape(str(reason))}\n\n"
        "Choose how to continue:"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Good to Go", callback_data=f"sac:{token}:good"),
                InlineKeyboardButton("🔎 Send TMDB ID", callback_data=f"sac:{token}:tmdb"),
            ],
            [InlineKeyboardButton("⏭ Skip Autorename", callback_data=f"sac:{token}:skip")],
        ]
    )

    pending = {
        "token": token,
        "future": future,
        "listener": listener,
        "user_id": listener.user_id,
        "chat_id": listener.message.chat.id,
        "expected_type": expected_type,
        "ctx": ctx,
        "state": "choosing",
        "message": None,
        "prompt_message_id": None,
    }
    _PENDING[token] = pending
    LOGGER.info(
        "Smart Autorename: confirmation required (task_id=%s, user_id=%s, filename=%s, confidence=%s, provider=%s, reason=%s)",
        listener.mid,
        listener.user_id,
        filename,
        confidence,
        provider,
        assessment.get("reason"),
    )

    try:
        prompt = await TgClient.bot.send_message(
            chat_id=listener.message.chat.id,
            text=message_text,
            reply_markup=keyboard,
            reply_to_message_id=listener.message.id,
            parse_mode="html",
            disable_web_page_preview=True,
        )
        pending["message"] = prompt
        timeout = max(
            30, int(getattr(Config, "SMART_AUTORENAME_CONFIRM_TIMEOUT", 900) or 900)
        )
        deadline = loop.time() + timeout
        while True:
            if listener.is_cancelled:
                LOGGER.info(
                    "Smart Autorename: pending confirmation cancelled (task_id=%s)",
                    listener.mid,
                )
                with suppress(Exception):
                    await prompt.edit_reply_markup(reply_markup=None)
                    await prompt.edit_text(
                        f"{message_text}\n\n✖ <b>Task cancelled.</b>",
                        parse_mode="html",
                        disable_web_page_preview=True,
                    )
                return {"action": "cancel"}
            remaining = deadline - loop.time()
            if remaining <= 0:
                LOGGER.info(
                    "Smart Autorename: confirmation timed out; original filename retained (task_id=%s)",
                    listener.mid,
                )
                with suppress(Exception):
                    await prompt.edit_reply_markup(reply_markup=None)
                    await prompt.edit_text(
                        f"{message_text}\n\n⏱ <b>Timed out.</b> Original filename retained; continuing without Smart Autorename.",
                        parse_mode="html",
                        disable_web_page_preview=True,
                    )
                return {"action": "skip", "reason": "timeout"}
            try:
                return await asyncio.wait_for(
                    asyncio.shield(future), timeout=min(1.0, remaining)
                )
            except asyncio.TimeoutError:
                continue
    finally:
        _PENDING.pop(token, None)
        if not future.done():
            future.cancel()


async def smart_autorename_callback(_, query):
    parts = (query.data or "").split(":")
    if len(parts) != 3:
        await query.answer("Invalid or expired confirmation.", show_alert=True)
        return
    _, token, action = parts
    pending = _PENDING.get(token)
    if not pending or pending["future"].done():
        await query.answer("This confirmation has expired.", show_alert=True)
        return
    if query.from_user.id != pending["user_id"]:
        await query.answer("Only the task owner can respond to this confirmation.", show_alert=True)
        return

    if action == "tmdb":
        if pending["state"] == "awaiting_url":
            await query.answer("Reply to the TMDB URL prompt with the link.")
            return
        if not str(getattr(Config, "TMDB_ACCESS_TOKEN", "") or "").strip():
            await query.answer(
                "TMDB_ACCESS_TOKEN is not configured. Configure it before sending a TMDB ID.",
                show_alert=True,
            )
            return
        pending["state"] = "awaiting_url"
        example = _expected_url_help(pending["expected_type"], pending["ctx"])
        prompt = await TgClient.bot.send_message(
            chat_id=pending["chat_id"],
            text=(
                "🔎 <b>Send the correct TMDB URL</b>\n\n"
                "Reply to this message with the full HTTPS URL.\n"
                f"<code>{escape(example)}</code>\n\n"
                "The bot will fetch this exact TMDB record; it will not run fuzzy title search again."
            ),
            reply_to_message_id=query.message.id,
            parse_mode="html",
            disable_web_page_preview=True,
        )
        pending["prompt_message_id"] = prompt.id
        await query.answer("Send the full TMDB URL as a reply to the new prompt.")
        return

    if action not in {"good", "skip"}:
        await query.answer("Unknown confirmation action.", show_alert=True)
        return

    with suppress(Exception):
        await query.message.edit_reply_markup(reply_markup=None)
    pending["future"].set_result(
        {"action": "good_to_go" if action == "good" else "skip"}
    )
    await query.answer("Approved. Continuing." if action == "good" else "Autorename skipped.")
    LOGGER.info(
        "Smart Autorename: user selected %s (task_id=%s, user_id=%s)",
        action,
        pending["listener"].mid,
        query.from_user.id,
    )




def is_pending_url_reply(message) -> bool:
    """Filter only replies addressed to an active Smart Autorename URL prompt."""
    if (
        not message
        or not getattr(message, "from_user", None)
        or not getattr(message, "reply_to_message_id", None)
        or not getattr(message, "text", None)
    ):
        return False
    return any(
        pending["state"] == "awaiting_url"
        and pending["chat_id"] == message.chat.id
        and pending["user_id"] == message.from_user.id
        and pending["prompt_message_id"] == message.reply_to_message_id
        and not pending["future"].done()
        for pending in _PENDING.values()
    )


async def smart_autorename_url_reply(_, message):
    if not message.from_user or not message.reply_to_message_id or not message.text:
        return
    for pending in list(_PENDING.values()):
        if (
            pending["state"] != "awaiting_url"
            or pending["chat_id"] != message.chat.id
            or pending["user_id"] != message.from_user.id
            or pending["prompt_message_id"] != message.reply_to_message_id
            or pending["future"].done()
        ):
            continue

        metadata, error = await _resolve_manual_url(
            message.text, pending["expected_type"], pending["ctx"]
        )
        if pending["future"].done():
            return
        if error:
            await TgClient.bot.send_message(
                chat_id=message.chat.id,
                text=f"❌ {escape(error)}\n\nPlease check the URL or TMDB response and try again, or use the confirmation buttons.",
                reply_to_message_id=message.id,
                parse_mode="html",
                disable_web_page_preview=True,
            )
            LOGGER.info(
                "Smart Autorename: invalid manual TMDB URL (task_id=%s, reason=%s)",
                pending["listener"].mid,
                error,
            )
            return

        if pending["future"].done():
            return
        pending["future"].set_result({"action": "manual_tmdb", "metadata": metadata})
        with suppress(Exception):
            await pending["message"].edit_reply_markup(reply_markup=None)
        await TgClient.bot.send_message(
            chat_id=message.chat.id,
            text=(
                "✅ <b>TMDB metadata verified</b>\n"
                f"<b>Selected:</b> {escape(str(metadata.series_title or metadata.title))}"
                + (f" ({escape(str(metadata.year))})" if metadata.year else "")
                + "\nContinuing the task."
            ),
            reply_to_message_id=message.id,
            parse_mode="html",
            disable_web_page_preview=True,
        )
        LOGGER.info(
            "Smart Autorename: manual TMDB metadata resolved (task_id=%s, tmdb_id=%s, type=%s)",
            pending["listener"].mid,
            metadata.provider_id,
            metadata.selection_type,
        )
        return
