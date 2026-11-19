import asyncio
import re
import xml.etree.ElementTree as ET

from httpx import AsyncClient

from .. import LOGGER, bot_loop
from ..core.config_manager import Config
from ..core.torrent_manager import TorrentManager
from ..helper.ext_utils.bot_utils import new_task
from ..helper.ext_utils.status_utils import get_readable_file_size
from ..helper.telegram_helper.button_build import ButtonMaker
from ..helper.telegram_helper.message_utils import (
    delete_message,
    edit_message,
    send_message,
)
from .mirror_leech import Mirror

PLUGINS = []
SEARCH_CACHE = {}
SERIES_QUEUE_LOCK = asyncio.Lock()


async def initiate_search_tools():
    if Config.DISABLE_TORRENTS:
        LOGGER.warning("Torrents are disabled. Skipping search plugin initialization.")
        return
    try:
        qb_plugins = await TorrentManager.qbittorrent.search.plugins()
        if qb_plugins:
            names = [plugin.name for plugin in qb_plugins]
            await TorrentManager.qbittorrent.search.uninstall_plugin(names)
            PLUGINS.clear()
        if Config.SEARCH_PLUGINS:
            await TorrentManager.qbittorrent.search.install_plugin(
                Config.SEARCH_PLUGINS
            )
            for p in await TorrentManager.qbittorrent.search.plugins():
                PLUGINS.append(p.name)
    except Exception as e:
        LOGGER.warning(f"Error initiating qBittorrent search plugins: {e}")


def parse_season_info(title: str):
    """
    Detect if release is a TV Season pack or Episode.
    Returns:
      is_series: bool
      season_num: int or None
      is_complete: bool
    """
    clean_title = re.sub(r"[._\-]", " ", title)

    if re.search(
        r"\b(complete series|s01\s*[-–]\s*s\d+|seasons?\s*1\s*[-–]\s*\d+)\b",
        clean_title,
        re.IGNORECASE,
    ):
        return True, None, True

    match = re.search(r"\b(?:s|season\s*)(\d{1,2})\b", clean_title, re.IGNORECASE)
    if match:
        season_num = int(match.group(1))
        if re.search(r"\b(?:s\d{1,2})?e\d{1,3}\b", clean_title, re.IGNORECASE):
            return True, season_num, False
        return True, season_num, True

    return False, None, False


async def query_jackett(query: str, limit: int = None):
    """
    Query Jackett Torznab API.
    """
    if not Config.JACKETT_URL or not Config.JACKETT_API_KEY:
        return []

    base_url = Config.JACKETT_URL.rstrip("/")
    api_key = Config.JACKETT_API_KEY
    limit = limit or Config.JACKETT_SEARCH_LIMIT or 50

    params = {
        "apikey": api_key,
        "t": "search",
        "q": query,
    }

    # Support full Torznab URLs, Prowlarr endpoints, or Jackett endpoints
    if "/torznab" in base_url or "/api" in base_url:
        url = base_url
    elif "prowlarr" in base_url.lower() or ":9696" in base_url:
        url = f"{base_url}/api/v1/indexer/all/torznab/api"
    else:
        url = f"{base_url}/api/v2.0/indexers/all/results/torznab/api"

    results = []

    try:
        async with AsyncClient() as session:
            resp = await session.get(url, params=params, timeout=20)
            if resp.status_code != 200:
                LOGGER.error(f"Jackett API returned status {resp.status_code}")
                return []

            root = ET.fromstring(resp.text)
            channel = root.find("channel")
            if channel is None:
                return []

            for item in channel.findall("item"):
                title_elem = item.find("title")
                title = title_elem.text if title_elem is not None else "Unknown"

                size_elem = item.find("size")
                size = (
                    int(size_elem.text)
                    if size_elem is not None and size_elem.text
                    else 0
                )

                link_elem = item.find("link")
                torrent_link = link_elem.text if link_elem is not None else ""

                seeders = 0
                magnet_url = ""
                for attr in item.findall("{http://torznab.com/schemas/2015/feed}attr"):
                    attr_name = attr.get("name")
                    attr_value = attr.get("value")
                    if attr_name == "seeders" and attr_value:
                        try:
                            seeders = int(attr_value)
                        except ValueError:
                            pass
                    elif attr_name == "magneturl" and attr_value:
                        magnet_url = attr_value

                download_url = magnet_url or torrent_link
                if download_url:
                    is_series, season_num, is_pack = parse_season_info(title)
                    results.append(
                        {
                            "title": title,
                            "size": size,
                            "readable_size": get_readable_file_size(size),
                            "seeders": seeders,
                            "url": download_url,
                            "is_series": is_series,
                            "season": season_num,
                            "is_pack": is_pack,
                        }
                    )

                if len(results) >= limit:
                    break

    except Exception as e:
        LOGGER.error(f"Error querying Jackett: {e}")

    results.sort(key=lambda x: x["seeders"], reverse=True)
    return results


async def query_qb_plugins(query: str, limit: int = None):
    """
    Query installed qBittorrent search plugins.
    """
    limit = limit or Config.SEARCH_LIMIT or 50
    results = []
    try:
        if not PLUGINS:
            pl = await TorrentManager.qbittorrent.search.plugins()
            for i in pl:
                PLUGINS.append(i.name)
        if not PLUGINS:
            return []

        search_job = await TorrentManager.qbittorrent.search.start(
            pattern=query, plugins=["all"], category="all"
        )
        search_id = search_job.id

        for _ in range(30):
            await asyncio.sleep(1)
            result_status = await TorrentManager.qbittorrent.search.status(search_id)
            if result_status and result_status[0].status != "Running":
                break

        dict_search_results = await TorrentManager.qbittorrent.search.results(
            id=search_id, limit=limit
        )
        await TorrentManager.qbittorrent.search.delete(search_id)

        for r in getattr(dict_search_results, "results", []):
            url = getattr(r, "fileUrl", "")
            if not url:
                continue
            title = getattr(r, "fileName", "Unknown")
            size = getattr(r, "fileSize", 0)
            seeders = getattr(r, "nbSeeders", 0)
            is_series, season_num, is_pack = parse_season_info(title)

            results.append(
                {
                    "title": title,
                    "size": size,
                    "readable_size": get_readable_file_size(size),
                    "seeders": seeders,
                    "url": url,
                    "is_series": is_series,
                    "season": season_num,
                    "is_pack": is_pack,
                }
            )
    except Exception as e:
        LOGGER.error(f"Error querying qBittorrent plugins: {e}")

    results.sort(key=lambda x: x["seeders"], reverse=True)
    return results[:limit]


def build_search_page(search_id: str, session: dict, page_no: int = 1):
    results = session["results"]
    engine_name = session["engine"]
    query = session["query"]

    total_results = len(results)
    page_size = 8
    total_pages = max(1, (total_results + page_size - 1) // page_size)
    page_no = max(1, min(page_no, total_pages))

    start_idx = (page_no - 1) * page_size
    end_idx = min(start_idx + page_size, total_results)
    page_items = results[start_idx:end_idx]

    buttons = ButtonMaker()
    for idx, item in enumerate(page_items, start=start_idx):
        badge = f"[S{item['season']:02d}] " if item.get("season") else ""
        btn_text = f"[{idx + 1}] {badge}{item['readable_size']} | ⬆️ {item['seeders']}s"
        buttons.data_button(btn_text, f"torser {search_id} pick {idx}")

    # Pagination navigation buttons
    if page_no > 1:
        buttons.data_button("⬅️ Prev", f"torser {search_id} page prev")
    if page_no < total_pages:
        buttons.data_button("Next ➡️", f"torser {search_id} page next")

    has_seasons = any(r.get("season") for r in results)
    if has_seasons:
        buttons.data_button("📺 Filter by Season", f"torser {search_id} season_view")

    buttons.data_button("❌ Cancel", f"torser {search_id} cancel")
    menu = buttons.build_menu(2)

    caption = (
        f"<b>🍿 Search Results for:</b> <code>{query}</code>\n"
        f"<b>⚙️ Engine:</b> <code>{engine_name}</code> | <b>Page:</b> {page_no}/{total_pages} ({total_results} results)\n\n"
    )
    for idx, item in enumerate(page_items, start=start_idx):
        caption += (
            f"<b>{idx + 1}.</b> <code>{item['title']}</code>\n"
            f"   📦 <b>Size:</b> {item['readable_size']} | ⬆️ <b>Seeds:</b> {item['seeders']}\n"
        )
    caption += "\n<i>Select an item to Mirror, Leech, or Sleech:</i>"

    return caption, menu


@new_task
async def torrent_search(_, message):
    text_parts = message.text.split(maxsplit=1)
    if len(text_parts) == 1:
        await send_message(
            message,
            "<b>Usage:</b> <code>/search &lt;query&gt;</code>\n"
            "<i>Example:</i> <code>/search Inception</code> or <code>/search Loki</code>",
        )
        return

    query = text_parts[1].strip()
    status_msg = await send_message(
        message, f"🔍 <i>Searching torrents for:</i> <b>{query}</b>..."
    )

    engine_name = "Jackett"
    if Config.JACKETT_URL and (
        "prowlarr" in Config.JACKETT_URL.lower() or ":9696" in Config.JACKETT_URL
    ):
        engine_name = "Prowlarr"
    results = []

    # Priority 1: Torznab Indexer (Jackett / Prowlarr)
    if Config.JACKETT_URL and Config.JACKETT_API_KEY:
        results = await query_jackett(query)

    # Priority 2: Fallback to qBittorrent plugins
    if not results:
        engine_name = "qBit Plugins"
        results = await query_qb_plugins(query)

    if not results:
        await edit_message(
            status_msg,
            f"❌ No torrent results found for: <b>{query}</b>",
        )
        return

    search_id = f"{message.from_user.id}_{message.id}"
    SEARCH_CACHE[search_id] = {
        "results": results,
        "user_id": message.from_user.id,
        "query": query,
        "engine": engine_name,
        "page_no": 1,
    }

    caption, menu = build_search_page(search_id, SEARCH_CACHE[search_id], page_no=1)
    await edit_message(status_msg, caption, menu)


async def execute_task(client, message, torrent_url: str, mode: str):
    dummy_message = message.reply_to_message or message
    dummy_message.text = f"dummy {torrent_url}"

    if mode == "mirror":
        bot_loop.create_task(
            Mirror(client, dummy_message, is_qbit=True, is_leech=False).new_event()
        )
    elif mode == "leech":
        bot_loop.create_task(
            Mirror(client, dummy_message, is_qbit=True, is_leech=True).new_event()
        )
    elif mode == "sleech":
        bot_loop.create_task(
            Mirror(
                client,
                dummy_message,
                is_qbit=True,
                is_leech=True,
                is_stremio=True,
            ).new_event()
        )


async def process_series_queue(client, message, season_tasks: list, mode: str):
    async with SERIES_QUEUE_LOCK:
        cooldown = Config.JACKETT_SERIES_COOLDOWN or 180
        total = len(season_tasks)
        for idx, task in enumerate(season_tasks, start=1):
            await send_message(
                message,
                f"🚀 <b>Starting Season [{idx}/{total}]:</b>\n"
                f"<code>{task['title']}</code>\n"
                f"Mode: <b>{mode.upper()}</b>",
            )
            await execute_task(client, message, task["url"], mode)

            if idx < total:
                wait_note = await send_message(
                    message,
                    f"⏸ <i>Waiting {cooldown}s cooldown before next season to prevent flood limits...</i>",
                )
                await asyncio.sleep(cooldown)
                await delete_message(wait_note)


@new_task
async def torrent_search_update(client, query):
    user_id = query.from_user.id
    data = query.data.split()
    if len(data) < 3:
        await query.answer("Invalid request!", show_alert=True)
        return

    search_id = data[1]
    action = data[2]

    session = SEARCH_CACHE.get(search_id)
    if not session and action != "cancel":
        await query.answer(
            "Search session expired! Please search again.", show_alert=True
        )
        return

    if session and session["user_id"] != user_id:
        await query.answer("This search menu is not yours!", show_alert=True)
        return

    message = query.message

    if action == "cancel":
        SEARCH_CACHE.pop(search_id, None)
        await query.answer("Search canceled.")
        await edit_message(message, "❌ <i>Search session canceled.</i>")
        return

    results = session["results"]
    engine_name = session.get("engine", "Search Engine")

    page_no = session.get("page_no", 1)

    if action == "page":
        direction = data[3]
        total_pages = (len(results) + 7) // 8
        if direction == "next":
            page_no = min(page_no + 1, total_pages)
        elif direction == "prev":
            page_no = max(page_no - 1, 1)
        session["page_no"] = page_no

        caption, menu = build_search_page(search_id, session, page_no)
        await query.answer()
        await edit_message(message, caption, menu)
        return

    elif action == "season_view":
        seasons = {}
        for r in results:
            s_num = r.get("season")
            if s_num is not None and s_num not in seasons:
                seasons[s_num] = r

        buttons = ButtonMaker()
        for s_num in sorted(seasons.keys()):
            buttons.data_button(
                f"Season {s_num}", f"torser {search_id} pick_season {s_num}"
            )

        buttons.data_button(
            "📦 All Seasons (Safe Queue)", f"torser {search_id} pick_all_seasons"
        )
        buttons.data_button("⬅️ Back to List", f"torser {search_id} back_list")
        buttons.data_button("❌ Cancel", f"torser {search_id} cancel")

        await query.answer()
        await edit_message(
            message,
            f"<b>📺 TV Series Detected:</b> <code>{session['query']}</code>\n"
            f"<b>⚙️ Engine:</b> <code>{engine_name}</code>\n\n"
            f"Select an individual season or choose <b>All Seasons</b>.\n"
            f"<i>(All Seasons downloads one season at a time with anti-flood delay)</i>",
            buttons.build_menu(2),
        )
        return

    elif action == "back_list":
        caption, menu = build_search_page(search_id, session, session.get("page_no", 1))
        await query.answer()
        await edit_message(message, caption, menu)
        return

    elif action == "pick":
        idx = int(data[3])
        item = results[idx]
        buttons = ButtonMaker()
        buttons.data_button("📥 qbMirror", f"torser {search_id} run mirror {idx}")
        buttons.data_button("📤 qbLeech", f"torser {search_id} run leech {idx}")
        buttons.data_button(
            "📺 Sleech (Stremio)", f"torser {search_id} run sleech {idx}"
        )
        buttons.data_button("⬅️ Back", f"torser {search_id} back_list")
        buttons.data_button("❌ Cancel", f"torser {search_id} cancel")

        details = (
            f"<b>Selected Release:</b>\n"
            f"🎬 <code>{item['title']}</code>\n"
            f"📦 <b>Size:</b> {item['readable_size']}\n"
            f"⬆️ <b>Seeders:</b> {item['seeders']}\n"
            f"⚙️ <b>Engine:</b> <code>{engine_name}</code>\n\n"
            f"🧲 <b>Magnet / Link (tap to copy):</b>\n<code>{item['url']}</code>\n\n"
            f"<i>Choose action or copy link above for manual <code>-s</code> selection:</i>"
        )
        await query.answer()
        await edit_message(message, details, buttons.build_menu(2))
        return

    elif action == "pick_season":
        s_num = int(data[3])
        matched = [r for r in results if r.get("season") == s_num]
        if not matched:
            await query.answer("No release found for this season!", show_alert=True)
            return

        item = matched[0]
        item_idx = results.index(item)

        buttons = ButtonMaker()
        buttons.data_button("📥 qbMirror", f"torser {search_id} run mirror {item_idx}")
        buttons.data_button("📤 qbLeech", f"torser {search_id} run leech {item_idx}")
        buttons.data_button(
            "📺 Sleech (Stremio)", f"torser {search_id} run sleech {item_idx}"
        )
        buttons.data_button("⬅️ Back", f"torser {search_id} season_view")
        buttons.data_button("❌ Cancel", f"torser {search_id} cancel")

        details = (
            f"<b>Selected Season {s_num}:</b>\n"
            f"🎬 <code>{item['title']}</code>\n"
            f"📦 <b>Size:</b> {item['readable_size']}\n"
            f"⬆️ <b>Seeders:</b> {item['seeders']}\n"
            f"⚙️ <b>Engine:</b> <code>{engine_name}</code>\n\n"
            f"🧲 <b>Magnet / Link (tap to copy):</b>\n<code>{item['url']}</code>\n\n"
            f"<i>Choose action for Season {s_num} or copy link above:</i>"
        )
        await query.answer()
        await edit_message(message, details, buttons.build_menu(2))
        return

    elif action == "pick_all_seasons":
        buttons = ButtonMaker()
        buttons.data_button("📥 Mirror All", f"torser {search_id} run_all mirror")
        buttons.data_button("📤 Leech All", f"torser {search_id} run_all leech")
        buttons.data_button(
            "📺 Sleech All (Stremio)", f"torser {search_id} run_all sleech"
        )
        buttons.data_button("⬅️ Back", f"torser {search_id} season_view")
        buttons.data_button("❌ Cancel", f"torser {search_id} cancel")

        seasons = {}
        for r in results:
            s_num = r.get("season")
            if s_num is not None and s_num not in seasons:
                seasons[s_num] = r

        summary_text = (
            f"<b>📦 Queue All Seasons ({len(seasons)} seasons found):</b>\n"
            f"<b>⚙️ Engine:</b> <code>{engine_name}</code>\n\n"
        )
        for s_num in sorted(seasons.keys()):
            summary_text += f"• Season {s_num}: {seasons[s_num]['readable_size']} (Seeds: {seasons[s_num]['seeders']})\n"

        cooldown = Config.JACKETT_SERIES_COOLDOWN or 180
        summary_text += (
            f"\n⚠️ <i>Each season will be processed one by one with a {cooldown}s "
            f"rest period to prevent Telegram FloodWait.</i>\n\nChoose destination:"
        )

        await query.answer()
        await edit_message(message, summary_text, buttons.build_menu(2))
        return

    elif action == "run":
        mode = data[3]
        idx = int(data[4])
        item = results[idx]
        SEARCH_CACHE.pop(search_id, None)
        await query.answer(f"Starting {mode.upper()}...")
        await edit_message(
            message,
            f"✅ <b>Triggered {mode.upper()} via qBittorrent:</b>\n"
            f"<code>{item['title']}</code>\n"
            f"📦 <b>Size:</b> {item['readable_size']}\n"
            f"⚙️ <b>Engine:</b> <code>{engine_name}</code>",
        )
        await execute_task(client, message, item["url"], mode)

    elif action == "run_all":
        mode = data[3]
        seasons = {}
        for r in results:
            s_num = r.get("season")
            if s_num is not None and s_num not in seasons:
                seasons[s_num] = r

        if not seasons:
            await query.answer("No distinct seasons found!", show_alert=True)
            return

        sorted_tasks = [seasons[k] for k in sorted(seasons.keys())]
        SEARCH_CACHE.pop(search_id, None)
        await query.answer(f"Queued {len(sorted_tasks)} seasons for {mode.upper()}!")
        await edit_message(
            message,
            f"✅ <b>Queued {len(sorted_tasks)} seasons for {mode.upper()} via qBittorrent.</b>\n"
            f"⚙️ <b>Engine:</b> <code>{engine_name}</code>\n"
            f"Processing sequentially with safe cooldown...",
        )
        bot_loop.create_task(process_series_queue(client, message, sorted_tasks, mode))
