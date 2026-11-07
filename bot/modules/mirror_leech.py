from base64 import b64encode
from re import match as re_match

from asyncio import sleep
from html import escape
from aiofiles.os import path as aiopath, remove as aioremove
from bot.core.config_manager import Config

from pyrogram.enums import ButtonStyle

from .. import (
    DOWNLOAD_DIR,
    LOGGER,
    bot_loop,
    bot_cache,
    task_dict_lock,
    user_data,
    blacklisted_keywords,
)
from ..core.seedr_client import SeedrClient
from ..helper.ext_utils.db_handler import database
from ..helper.ext_utils.bot_utils import (
    COMMAND_USAGE,
    arg_parser,
    get_content_type,
    sync_to_async,
    new_task,
)
from ..helper.ext_utils.status_utils import (
    get_readable_file_size,
    get_progress_bar_string,
    get_readable_time,
)
from ..helper.telegram_helper.button_build import ButtonMaker
from ..helper.telegram_helper.bot_commands import BotCommands
from ..helper.telegram_helper.filters import CustomFilters
from ..helper.ext_utils.exceptions import DirectDownloadLinkException
from ..helper.ext_utils.links_utils import (
    is_gdrive_id,
    is_gdrive_link,
    is_mega_link,
    is_magnet,
    is_rclone_path,
    is_telegram_link,
    is_url,
    get_magnet_from_torrent,
    get_magnet_from_url,
)
from ..helper.ext_utils.task_manager import pre_task_check, check_blacklisted_keywords
from ..helper.ext_utils.telegraph_helper import telegraph
from ..helper.listeners.task_listener import TaskListener
from ..helper.mirror_leech_utils.download_utils.aria2_download import (
    add_aria2_download,
)
from ..helper.mirror_leech_utils.download_utils.direct_downloader import (
    add_direct_download,
)
from ..helper.mirror_leech_utils.download_utils.direct_link_generator import (
    direct_link_generator,
)
from ..helper.mirror_leech_utils.download_utils.gd_download import add_gd_download
from ..helper.mirror_leech_utils.download_utils.jd_download import add_jd_download
from ..helper.mirror_leech_utils.download_utils.mega_download import add_mega_download
from ..helper.mirror_leech_utils.download_utils.nzb_downloader import add_nzb
from ..helper.mirror_leech_utils.download_utils.qbit_download import add_qb_torrent
from ..helper.mirror_leech_utils.download_utils.rclone_download import (
    add_rclone_download,
)
from ..helper.mirror_leech_utils.download_utils.seedr_download import (
    add_seedr_download,
    _build_contents,
)
from ..helper.mirror_leech_utils.download_utils.telegram_download import (
    TelegramDownloadHelper,
)
from ..helper.telegram_helper.message_utils import (
    auto_delete_message,
    delete_message,
    delete_links,
    get_tg_link_message,
    send_message,
    edit_message,
)


class Mirror(TaskListener):
    def __init__(
        self,
        client,
        message,
        is_qbit=False,
        is_leech=False,
        is_stremio=False,
        is_jd=False,
        is_nzb=False,
        is_seedr=False,
        is_uphoster=False,
        same_dir=None,
        bulk=None,
        multi_tag=None,
        options="",
        **kwargs,
    ):
        if same_dir is None:
            same_dir = {}
        if bulk is None:
            bulk = []
        self.message = message
        self.client = client
        self.multi_tag = multi_tag
        self.options = options
        self.same_dir = same_dir
        self.bulk = bulk
        super().__init__()
        self.is_qbit = is_qbit
        self.is_leech = is_leech
        self.is_stremio = is_stremio
        self.is_jd = is_jd
        self.is_nzb = is_nzb
        self.is_seedr = is_seedr
        self.is_uphoster = is_uphoster

    async def new_event(self):
        text = self.message.text.split("\n")
        input_list = text[0].split(" ")

        check_msg, check_button = await pre_task_check(self.message)
        if check_msg:
            await delete_links(self.message)
            await auto_delete_message(
                await send_message(self.message, check_msg, check_button)
            )
            return

        args = {
            "-doc": False,
            "-med": False,
            "-d": False,
            "-j": False,
            "-s": False,
            "-b": False,
            "-e": False,
            "-z": False,
            "-sv": False,
            "-ss": False,
            "-ssd": False,
            "-dup": False,
            "-f": False,
            "-fd": False,
            "-fu": False,
            "-hl": False,
            "-bt": False,
            "-ut": False,
            "-yt": False,
            "-i": 0,
            "-sp": 0,
            "link": "",
            "-n": "",
            "-m": "",
            "-meta": "",
            "-up": "",
            "-ud": "",
            "-rcf": "",
            "-au": "",
            "-ap": "",
            "-h": "",
            "-t": "",
            "-ca": "",
            "-cv": "",
            "-ns": "",
            "-tl": "",
            "-ff": set(),
            "-sd": False,
            "-seedr": False,
        }

        arg_parser(input_list[1:], args)

        if Config.DISABLE_BULK and args.get("-b", False):
            await send_message(self.message, "Bulk downloads are currently disabled.")
            return

        if Config.DISABLE_MULTI and int(args.get("-i", 1)) > 1:
            await send_message(
                self.message,
                "Multi-downloads are currently disabled. Please try without the -i flag.",
            )
            return

        if Config.DISABLE_SEED and args.get("-d", False):
            await send_message(
                self.message,
                "Seeding is currently disabled. Please try without the -d flag.",
            )
            return

        if Config.DISABLE_FF_MODE and args.get("-ff"):
            await send_message(self.message, "FFmpeg commands are currently disabled.")
            return

        self.select = args["-s"]
        self.seed = args["-d"]
        self.name = args["-n"]
        self.custom_name = args["-n"]
        self.up_dest = args["-up"]
        self.dump_dest = args["-ud"]
        self.rc_flags = args["-rcf"]
        self.link = args["link"]
        self.compress = args["-z"]
        self.extract = args["-e"]
        self.join = args["-j"]
        self.thumb = args["-t"]
        self.split_size = args["-sp"]
        self.sample_video = args["-sv"]
        self.screen_shots = args["-ss"]
        self.skip_stop_duplicate = args["-ssd"] or args["-dup"]
        self.force_run = args["-f"]
        self.force_download = args["-fd"]
        self.force_upload = args["-fu"]
        self.convert_audio = args["-ca"]
        self.convert_video = args["-cv"]
        self.name_swap = args["-ns"]
        self.hybrid_leech = args["-hl"]
        self.thumbnail_layout = args["-tl"]
        self.as_doc = args["-doc"]
        self.as_med = args["-med"]
        self.folder_name = f"/{args['-m']}".rstrip("/") if len(args["-m"]) > 0 else ""
        self.bot_trans = args["-bt"]
        self.user_trans = args["-ut"]
        self.is_yt = args["-yt"]
        self.is_seedr = (
            self.is_seedr or args.get("-sd", False) or args.get("-seedr", False)
        )
        if self.is_seedr:
            if Config.DISABLE_SEEDR:
                await send_message(
                    self.message, "Seedr is currently disabled by the Bot Owner."
                )
                return
            user_dict = user_data.get(self.message.from_user.id, {})
            email = user_dict.get("SEEDR_EMAIL") or Config.SEEDR_EMAIL
            password = user_dict.get("SEEDR_PASSWORD") or Config.SEEDR_PASSWORD
            uset_cmd = (
                f"/{BotCommands.UserSetCommand[0]}"
                if isinstance(BotCommands.UserSetCommand, list)
                else f"/{BotCommands.UserSetCommand}"
            )
            if not email or not password:
                await send_message(
                    self.message,
                    f"Seedr credentials are not configured! Please set SEEDR_EMAIL and SEEDR_PASSWORD in {uset_cmd} or bot config.",
                )
                return
            if self.link.strip().lower() in (
                "clear",
                "clean",
                "delete",
                "-clear",
                "-delete",
            ):
                await seedr_clear(self.client, self.message)
                return
        self.metadata_dict = self.default_metadata_dict.copy()
        self.audio_metadata_dict = self.audio_metadata_dict.copy()
        self.video_metadata_dict = self.video_metadata_dict.copy()
        self.subtitle_metadata_dict = self.subtitle_metadata_dict.copy()
        if args["-meta"]:
            meta = self.metadata_processor.parse_string(args["-meta"])
            self.metadata_dict = self.metadata_processor.merge_dicts(
                self.metadata_dict, meta
            )

        headers = args["-h"]
        is_bulk = args["-b"]

        bulk_start = 0
        bulk_end = 0
        ratio = None
        seed_time = None
        reply_to = None
        file_ = None
        session = ""

        try:
            self.multi = int(args["-i"])
        except Exception:
            self.multi = 0

        try:
            if args["-ff"]:
                if isinstance(args["-ff"], set):
                    self.ffmpeg_cmds = args["-ff"]
                else:
                    self.ffmpeg_cmds = eval(args["-ff"])
        except Exception as e:
            self.ffmpeg_cmds = None
            LOGGER.error(e)

        if not isinstance(self.seed, bool):
            dargs = self.seed.split(":")
            ratio = dargs[0] or None
            if len(dargs) == 2:
                seed_time = dargs[1] or None
            self.seed = True

        if not isinstance(is_bulk, bool):
            dargs = is_bulk.split(":")
            bulk_start = dargs[0] or 0
            if len(dargs) == 2:
                bulk_end = dargs[1] or 0
            is_bulk = True

        if not is_bulk:
            if self.multi > 0:
                if self.folder_name:
                    async with task_dict_lock:
                        if self.folder_name in self.same_dir:
                            self.same_dir[self.folder_name]["tasks"].add(self.mid)
                            for fd_name in self.same_dir:
                                if fd_name != self.folder_name:
                                    self.same_dir[fd_name]["total"] -= 1
                        elif self.same_dir:
                            self.same_dir[self.folder_name] = {
                                "total": self.multi,
                                "tasks": {self.mid},
                            }
                            for fd_name in self.same_dir:
                                if fd_name != self.folder_name:
                                    self.same_dir[fd_name]["total"] -= 1
                        else:
                            self.same_dir = {
                                self.folder_name: {
                                    "total": self.multi,
                                    "tasks": {self.mid},
                                }
                            }
                elif self.same_dir:
                    async with task_dict_lock:
                        for fd_name in self.same_dir:
                            self.same_dir[fd_name]["total"] -= 1
        else:
            await self.init_bulk(input_list, bulk_start, bulk_end, Mirror)
            return

        if len(self.bulk) != 0:
            del self.bulk[0]

        await self.run_multi(input_list, Mirror)

        await self.get_tag(text)

        path = f"{DOWNLOAD_DIR}{self.mid}{self.folder_name}"

        if not self.link and (reply_to := self.message.reply_to_message):
            if reply_to.text:
                self.link = reply_to.text.split("\n", 1)[0].strip()
        if is_telegram_link(self.link):
            try:
                reply_to, session = await get_tg_link_message(self.link)
            except Exception as e:
                await send_message(self.message, f"ERROR: {e}")
                await self.remove_from_same_dir()
                await delete_links(self.message)
                return

        if isinstance(reply_to, list):
            self.bulk = reply_to
            b_msg = input_list[:1]
            self.options = " ".join(input_list[1:])
            b_msg.append(f"{self.bulk[0]} -i {len(self.bulk)} {self.options}")
            nextmsg = await send_message(self.message, " ".join(b_msg))
            nextmsg = await self.client.get_messages(
                chat_id=self.message.chat.id, message_ids=nextmsg.id
            )
            if self.message.from_user:
                nextmsg.from_user = self.user
            else:
                nextmsg.sender_chat = self.user
            await Mirror(
                self.client,
                nextmsg,
                self.is_qbit,
                self.is_leech,
                self.is_jd,
                self.is_nzb,
                self.is_seedr,
                self.is_uphoster,
                self.same_dir,
                self.bulk,
                self.multi_tag,
                self.options,
            ).new_event()
            return

        if reply_to:
            file_ = (
                reply_to.document
                or reply_to.photo
                or reply_to.video
                or reply_to.audio
                or reply_to.voice
                or reply_to.video_note
                or reply_to.sticker
                or reply_to.animation
                or None
            )
            self.file_details = {"caption": reply_to.caption}

            if file_ is None:
                if reply_text := reply_to.text:
                    self.link = reply_text.split("\n", 1)[0].strip()
                else:
                    reply_to = None
            elif reply_to.document and (
                file_.mime_type == "application/x-bittorrent"
                or file_.file_name.endswith((".torrent", ".dlc", ".nzb"))
            ):
                self.link = await reply_to.download()
                file_ = None

        if (
            not self.link
            and file_ is None
            or is_telegram_link(self.link)
            and reply_to is None
            or file_ is None
            and not is_url(self.link)
            and not is_magnet(self.link)
            and not await aiopath.exists(self.link)
            and not is_rclone_path(self.link)
            and not is_gdrive_id(self.link)
            and not is_gdrive_link(self.link)
            and not is_mega_link(self.link)
        ):
            await send_message(
                self.message, COMMAND_USAGE["mirror"][0], COMMAND_USAGE["mirror"][1]
            )
            await self.remove_from_same_dir()
            await delete_links(self.message)
            return

        if len(self.link) > 0:
            if is_magnet(self.link):
                LOGGER.info(f"Magnet link provided: {self.link[:60]}...")
            else:
                LOGGER.info(self.link)

        try:
            await self.before_start()
        except Exception as e:
            await send_message(self.message, e)
            await self.remove_from_same_dir()
            await delete_links(self.message)
            return

        if self.is_cancelled:
            await self.remove_from_same_dir()
            await delete_links(self.message)
            return

        self._set_mode_engine()

        if (
            not self.is_jd
            and not self.is_nzb
            and not self.is_qbit
            and not self.is_seedr
            and not is_magnet(self.link)
            and not is_rclone_path(self.link)
            and not is_gdrive_link(self.link)
            and not self.link.endswith(".torrent")
            and file_ is None
            and not is_gdrive_id(self.link)
            and not is_mega_link(self.link)
        ):
            content_type = await get_content_type(self.link)
            if content_type is None or re_match(r"text/html|text/plain", content_type):
                try:
                    self.link = await sync_to_async(direct_link_generator, self.link)
                    if isinstance(self.link, tuple):
                        self.link, headers = self.link
                    elif isinstance(self.link, str):
                        LOGGER.info(f"Generated link: {self.link}")
                except DirectDownloadLinkException as e:
                    e = str(e)
                    if "This link requires a password!" not in e:
                        LOGGER.info(e)
                    if e.startswith("ERROR:"):
                        await send_message(self.message, e)
                        await self.remove_from_same_dir()
                        await delete_links(self.message)
                        return
                except Exception as e:
                    await send_message(self.message, e)
                    await self.remove_from_same_dir()
                    await delete_links(self.message)
                    return

        await delete_links(self.message)

        if file_ is not None:
            await TelegramDownloadHelper(self).add_download(
                reply_to, f"{path}/", session
            )
        elif isinstance(self.link, dict):
            await add_direct_download(self, path)
        elif self.is_jd:
            await add_jd_download(self, path)
        elif self.is_seedr:
            await add_seedr_download(self, path)
        elif self.is_qbit:
            await add_qb_torrent(self, path, ratio, seed_time)
        elif self.is_nzb:
            await add_nzb(self, path)
        elif is_rclone_path(self.link):
            await add_rclone_download(self, f"{path}/")
        elif is_gdrive_link(self.link) or is_gdrive_id(self.link):
            await add_gd_download(self, path)
        elif is_mega_link(self.link):
            await add_mega_download(self, f"{path}/")
        else:
            ussr = args["-au"]
            pssw = args["-ap"]
            if ussr or pssw:
                auth = f"{ussr}:{pssw}"
                headers += (
                    f" authorization: Basic {b64encode(auth.encode()).decode('ascii')}"
                )
            await add_aria2_download(self, path, headers, ratio, seed_time)


async def mirror(client, message):
    bot_loop.create_task(Mirror(client, message).new_event())


async def qb_mirror(client, message):
    bot_loop.create_task(Mirror(client, message, is_qbit=True).new_event())


async def jd_mirror(client, message):
    bot_loop.create_task(Mirror(client, message, is_jd=True).new_event())


async def nzb_mirror(client, message):
    text_parts = message.text.split()
    nzb_id = None
    if len(text_parts) > 1 and not text_parts[1].startswith(("http", "ftp", "/")):
        potential_id = text_parts[1]
        clean = potential_id.lstrip("-").replace("_", "")
        if clean.isalnum() and not (potential_id.startswith("-") and clean.isalpha()):
            nzb_id = potential_id
            nzb_url = f"{Config.HYDRA_IP.rstrip('/')}/getnzb/api/{nzb_id}?apikey={Config.HYDRA_API_KEY}"
            extra = " ".join(text_parts[2:])
            message.text = f"/nzbmirror {nzb_url} -e {extra}".strip()
    else:
        if "-e" not in message.text:
            message.text += " -e"
    mirror_task = Mirror(client, message, is_nzb=True)
    if nzb_id:
        mirror_task.nzb_id = nzb_id
    bot_loop.create_task(mirror_task.new_event())


async def leech(client, message):
    if Config.DISABLE_LEECH:
        await message.reply("The Leech command is currently disabled.")
        return
    bot_loop.create_task(Mirror(client, message, is_leech=True).new_event())


async def stremio_leech(client, message):
    if Config.DISABLE_LEECH:
        await message.reply("The Leech command is currently disabled.")
        return
    bot_loop.create_task(
        Mirror(client, message, is_leech=True, is_stremio=True).new_event()
    )


async def stremio_list(client, message):
    reply_msg = await send_message(
        message, "⏳ <i>Fetching Stremio library catalog from database...</i>"
    )
    items = await database.get_stremio_items(limit=2000)
    if not items:
        await edit_message(reply_msg, "<i>No Stremio media items found in catalog!</i>")
        return

    movies = [i for i in items if i.get("collection") == "movie"]
    tv_shows = [i for i in items if i.get("collection") == "tv"]
    other_items = [i for i in items if i.get("collection") not in ("movie", "tv")]

    PAGE_SIZE = 80
    pages_content = []

    # Organize catalog entries
    all_sections = []
    if movies:
        all_sections.append(("🍿 <b>Movies</b>", movies, True))
    if tv_shows:
        all_sections.append(("📺 <b>TV Series</b>", tv_shows, True))
    if other_items:
        all_sections.append(("📁 <b>Other Media</b>", other_items, False))

    pages = []
    for header, section_items, is_media in all_sections:
        for i in range(0, len(section_items), PAGE_SIZE):
            chunk = section_items[i : i + PAGE_SIZE]
            pages.append((header, chunk, is_media))

    total_pages = len(pages)

    for idx, (header, chunk, is_media) in enumerate(pages, start=1):
        content = f"<h3>🎬 <b><u>Stremio Library Catalog</u></b> (Page {idx}/{total_pages})</h3>"
        content += f"<h4>{header}</h4><ol>"
        for item in chunk:
            if is_media:
                title = escape(
                    item.get("title") or item.get("title_english") or "Unknown Title"
                )
                year = item.get("release_year", "")
                year_str = f" ({year})" if year else ""
                rating = item.get("rating")
                rating_str = f" — ⭐ <b>{rating}</b>" if rating else ""
                imdb_id = item.get("imdb_id")
                imdb_url = f"https://www.imdb.com/title/{imdb_id}" if imdb_id else "#"
                if imdb_url != "#":
                    content += f"<li><a href='{imdb_url}'><b>{title}</b></a>{year_str}{rating_str}</li>"
                else:
                    content += f"<li><b>{title}</b>{year_str}{rating_str}</li>"
            else:
                name = escape(
                    item.get("title")
                    or item.get("name")
                    or item.get("filename")
                    or "Unknown Item"
                )
                content += f"<li><b>{name}</b></li>"
        content += "</ol>"
        pages_content.append(content)

    try:
        created_paths = []
        for idx, p_content in enumerate(pages_content, start=1):
            t_page = await telegraph.create_page(
                title=f"Stremio Catalog - Page {idx}", content=p_content
            )
            t_path = t_page.get("path", "") if isinstance(t_page, dict) else ""
            if t_path:
                created_paths.append(t_path)

        if created_paths:
            # Edit pages to link Next / Prev
            await telegraph.edit_telegraph(created_paths, pages_content)
            main_url = f"https://telegra.ph/{created_paths[0]}"
            buttons = ButtonMaker()
            buttons.url_button("📖 Open Stremio Library", main_url)
            await edit_message(
                reply_msg,
                f"⌬ <b><u>Stremio Media Catalog</u></b>\n\nTotal Items: <b>{len(items)}</b>\nTotal Pages: <b>{len(created_paths)}</b>",
                buttons.build_menu(1),
            )
        else:
            await edit_message(reply_msg, "<i>Failed to generate Telegraph page!</i>")
    except Exception as e:
        LOGGER.error(f"Failed to create Stremio Telegraph page: {e}", exc_info=True)
        await edit_message(reply_msg, f"❌ <b>Error:</b> {e}")
        await edit_message(reply_msg, f"Failed to generate Telegraph page: {e}")


async def qb_leech(client, message):
    bot_loop.create_task(
        Mirror(client, message, is_qbit=True, is_leech=True).new_event()
    )


async def jd_leech(client, message):
    bot_loop.create_task(Mirror(client, message, is_leech=True, is_jd=True).new_event())


async def nzb_leech(client, message):
    text_parts = message.text.split()
    nzb_id = None
    if len(text_parts) > 1 and not text_parts[1].startswith(("http", "ftp", "/")):
        potential_id = text_parts[1]
        clean = potential_id.lstrip("-").replace("_", "")
        if clean.isalnum() and not (potential_id.startswith("-") and clean.isalpha()):
            nzb_id = potential_id
            nzb_url = f"{Config.HYDRA_IP.rstrip('/')}/getnzb/api/{nzb_id}?apikey={Config.HYDRA_API_KEY}"
            extra = " ".join(text_parts[2:])
            message.text = f"/nzbleech {nzb_url} -e {extra}".strip()
    else:
        if "-e" not in message.text:
            message.text += " -e"
    mirror_task = Mirror(client, message, is_leech=True, is_nzb=True)
    if nzb_id:
        mirror_task.nzb_id = nzb_id
    bot_loop.create_task(mirror_task.new_event())


async def clear_seedr_account(email, password):
    client = SeedrClient(email, password)
    await client.login()
    res = await client.list_contents("0")
    if not isinstance(res, dict):
        return 0, 0
    t_count = 0
    f_count = 0
    for t in res.get("torrents", []):
        t_id = t.get("id") or t.get("user_torrent_id")
        if t_id:
            try:
                await client.delete("torrent", t_id)
                t_count += 1
            except Exception:
                pass
    for f in res.get("folders", []):
        f_id = f.get("id")
        if f_id:
            try:
                await client.delete("folder", f_id)
                f_count += 1
            except Exception:
                pass
    return t_count, f_count


@new_task
async def seedr_clear(client, message):
    if Config.DISABLE_SEEDR:
        await message.reply("Seedr is currently disabled by the Bot Owner.")
        return
    user_dict = user_data.get(message.from_user.id, {})
    email = user_dict.get("SEEDR_EMAIL") or Config.SEEDR_EMAIL
    password = user_dict.get("SEEDR_PASSWORD") or Config.SEEDR_PASSWORD
    uset_cmd = (
        f"/{BotCommands.UserSetCommand[0]}"
        if isinstance(BotCommands.UserSetCommand, list)
        else f"/{BotCommands.UserSetCommand}"
    )
    if not email or not password:
        await message.reply(
            f"Seedr credentials are not configured! Please set SEEDR_EMAIL and SEEDR_PASSWORD in {uset_cmd} or bot config."
        )
        return
    msg = await send_message(message, "<i>Clearing Seedr Cloud Storage...</i>")
    try:
        t_count, f_count = await clear_seedr_account(email, password)
        await edit_message(
            msg,
            f"<b>Seedr Storage Cleared!</b>\nRemoved <b>{t_count}</b> active torrent(s) and <b>{f_count}</b> folder(s).",
        )
    except Exception as e:
        await edit_message(
            msg, f"<b>Failed to clear Seedr account:</b> {escape(str(e))}"
        )


async def uphoster(client, message):
    bot_loop.create_task(Mirror(client, message, is_uphoster=True).new_event())


@new_task
async def seedr_link(client, message):
    if Config.DISABLE_SEEDR:
        await message.reply("Seedr is currently disabled by the Bot Owner.")
        return
    user_id = message.from_user.id
    user_dict = user_data.get(user_id, {})
    email = user_dict.get("SEEDR_EMAIL") or Config.SEEDR_EMAIL
    password = user_dict.get("SEEDR_PASSWORD") or Config.SEEDR_PASSWORD
    uset_cmd = (
        f"/{BotCommands.UserSetCommand[0]}"
        if isinstance(BotCommands.UserSetCommand, list)
        else f"/{BotCommands.UserSetCommand}"
    )
    seedrlink_cmd = (
        f"/{BotCommands.SeedrLinkCommand[0]}"
        if isinstance(BotCommands.SeedrLinkCommand, list)
        else f"/{BotCommands.SeedrLinkCommand}"
    )
    if not email or not password:
        await message.reply(
            f"Seedr credentials are not configured! Please set SEEDR_EMAIL and SEEDR_PASSWORD in {uset_cmd} or bot config."
        )
        return

    link = ""
    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].strip().lower() in (
        "clear",
        "clean",
        "delete",
        "-clear",
        "-delete",
    ):
        await seedr_clear(client, message)
        return
    if len(args) > 1:
        link = args[1].strip()
    elif reply_to := message.reply_to_message:
        if reply_to.document and (
            reply_to.document.mime_type == "application/x-bittorrent"
            or reply_to.document.file_name.endswith(".torrent")
        ):
            link = await reply_to.download()
        elif reply_to.text:
            link = reply_to.text.split("\n", 1)[0].strip()

    if isinstance(link, str) and not is_magnet(link):
        if await aiopath.exists(link):
            tor_path = link
            try:
                link = get_magnet_from_torrent(tor_path)
            except Exception as e:
                LOGGER.error(
                    f"Failed to parse telegram torrent file for SeedrLink: {e}"
                )
            finally:
                if await aiopath.exists(tor_path):
                    try:
                        await aioremove(tor_path)
                    except Exception:
                        pass
        elif is_url(link):
            try:
                link = await get_magnet_from_url(link)
            except Exception as e:
                LOGGER.error(f"Failed to fetch/parse torrent URL for SeedrLink: {e}")
                await message.reply(f"Failed to fetch/parse .torrent from URL: {e}")
                return

    if not link or not is_magnet(link):
        await message.reply(
            f"Please provide a valid magnet link or .torrent URL!\n\n<b>Usage:</b> <code>{seedrlink_cmd} magnet:...</code> or <code>{seedrlink_cmd} https://.../file.torrent</code>"
        )
        return

    bl_kws = user_dict.get("BLACKLISTED_KEYWORDS") or (
        blacklisted_keywords if "BLACKLISTED_KEYWORDS" not in user_dict else []
    )
    listener_info = type("Listener", (), {"blacklisted_keywords": bl_kws})()

    is_bl, bl_kw = await check_blacklisted_keywords(listener_info, link)
    if is_bl:
        await message.reply(
            f"Task cancelled! Link contains blacklisted keyword: <code>{bl_kw}</code>"
        )
        if link and await aiopath.exists(link):
            try:
                await aioremove(link)
            except Exception:
                pass
        return

    msg = await send_message(message, "<i>Processing Seedr Magnet Link...</i>")
    seedr_client = SeedrClient(email, password)

    try:
        await seedr_client.login()
        log_link = f"{link[:60]}..." if is_magnet(link) else link
        LOGGER.info(f"SeedrLink: Adding magnet: {log_link}")
        result = await seedr_client.add_torrent(link)
        torrent_id = result.get("torrent_id") or result.get("user_torrent_id")
        title = result.get("title") or ""

        if not torrent_id:
            raise ValueError("Failed to obtain Seedr torrent ID!")

        if title:
            is_bl, bl_kw = await check_blacklisted_keywords(listener_info, title)
            if is_bl:
                try:
                    await seedr_client.delete("torrent", torrent_id)
                except Exception:
                    pass
                await edit_message(
                    msg,
                    f"Task cancelled! Title contains blacklisted keyword: <code>{bl_kw}</code>",
                )
                return
            await edit_message(
                msg,
                f"<b>Added to Seedr Cloud!</b>\n\n<b>Title:</b> <code>{escape(title)}</code>\n<i>Fetching cloud progress...</i>",
            )

        folder_id = None
        not_found_count = 0
        last_progress = ""

        while True:
            await sleep(3)
            res = await seedr_client.list_contents("0")
            torrent = next(
                (
                    t
                    for t in res.get("torrents", [])
                    if t.get("id") == torrent_id
                    or t.get("user_torrent_id") == torrent_id
                ),
                None,
            )
            folder = next(
                (f for f in res.get("folders", []) if title and f.get("name") == title),
                None,
            )

            prog = 0.0
            if torrent is not None:
                not_found_count = 0
                raw_prog = torrent.get("progress", 0) or 0
                prog = float(raw_prog)
                if 0 < prog <= 1.0:
                    prog *= 100.0
                prog = min(max(prog, 0.0), 100.0)

                name_str = torrent.get("name") or title or "Torrent"
                is_bl, bl_kw = await check_blacklisted_keywords(listener_info, name_str)
                if is_bl:
                    try:
                        await seedr_client.delete("torrent", torrent_id)
                    except Exception:
                        pass
                    if folder_id:
                        try:
                            await seedr_client.delete("folder", folder_id)
                        except Exception:
                            pass
                    await edit_message(
                        msg,
                        f"Task cancelled! Name contains blacklisted keyword: <code>{bl_kw}</code>",
                    )
                    return

                total_bytes = int(torrent.get("size", 0) or 0)
                downloaded_bytes = int((total_bytes * prog) / 100)
                speed = float(torrent.get("speed", 0) or 0) * 1024
                eta = int((total_bytes - downloaded_bytes) / speed) if speed > 0 else 0

                p_bar = get_progress_bar_string(prog)
                prog_card = (
                    f"<b><u>Seedr Cloud Download...</u></b>\n\n"
                    f"<b><i>{escape(name_str)}</i></b>\n"
                    f"┟ {p_bar} <i>{prog:.2f}%</i>\n"
                    f"┠ <b>Downloaded:</b> <i>{get_readable_file_size(downloaded_bytes)} of {get_readable_file_size(total_bytes)}</i>\n"
                    f"┠ <b>Status:</b> <b>Seedr Cloud Download</b>\n"
                    f"┠ <b>Speed:</b> <i>{get_readable_file_size(speed)}/s</i>\n"
                    f"┠ <b>Time:</b> <i>{get_readable_time(eta) if eta > 0 else '0s'}</i>\n"
                    f"┖ <b>Engine:</b> <i>Seedr</i>"
                )

                if prog_card != last_progress:
                    last_progress = prog_card
                    buttons = ButtonMaker()
                    buttons.data_button("🔄 Sync", f"seedrsync {user_id}")
                    buttons.data_button(
                        "🚫 Cancel Task", f"seedrcancel {user_id} {torrent_id}"
                    )
                    await edit_message(msg, prog_card, buttons.build_menu(2))

            if folder is not None:
                folder_name = folder.get("name", title)
                is_bl, bl_kw = await check_blacklisted_keywords(
                    listener_info, folder_name
                )
                if is_bl:
                    try:
                        await seedr_client.delete("torrent", torrent_id)
                    except Exception:
                        pass
                    try:
                        await seedr_client.delete("folder", folder["id"])
                    except Exception:
                        pass
                    await edit_message(
                        msg,
                        f"Task cancelled! Name contains blacklisted keyword: <code>{bl_kw}</code>",
                    )
                    return
                folder_contents = await seedr_client.list_contents(folder["id"])
                if folder_contents.get("files") and (torrent is None or prog >= 100.0):
                    folder_id = folder["id"]
                    break
            else:
                if torrent is None:
                    not_found_count += 1
                    if not_found_count >= 36:
                        raise ValueError("Torrent not found on Seedr account!")

        await edit_message(msg, "<i>Generating Seedr Direct Download Links...</i>")
        contents, total_size = await _build_contents(seedr_client, folder_id)
        if not contents:
            raise ValueError("No downloadable files found in Seedr folder!")

        for item in contents:
            file_path = f"{item['path']}/{item['filename']}".strip("/")
            is_bl, bl_kw = await check_blacklisted_keywords(listener_info, file_path)
            if is_bl:
                try:
                    await seedr_client.delete("folder", folder_id)
                except Exception:
                    pass
                await edit_message(
                    msg,
                    f"Task cancelled! File list entry contains blacklisted keyword: <code>{bl_kw}</code>",
                )
                return

        page_title = title or contents[0]["filename"]
        telegraph_html = f"<h3><b>{escape(page_title)}</b></h3>"
        telegraph_html += (
            f"<b>Total Size:</b> {get_readable_file_size(total_size)}<br><br>"
        )

        for idx, item in enumerate(contents, start=1):
            fname = escape(item["filename"])
            furl = item["url"]
            fsize = get_readable_file_size(item["size"])
            telegraph_html += f"{idx}. <a href='{furl}'>{fname}</a> ({fsize})<br>"

        telegraph_page = await telegraph.create_page(
            title=f"Seedr Direct Links - {page_title}"[:60],
            content=telegraph_html,
        )
        telegraph_url = telegraph_page["url"]

        buttons = ButtonMaker()
        if len(contents) == 1:
            buttons.url_button("🚀 Direct Download", contents[0]["url"])
        buttons.url_button("🌐 View Telegraph Page", telegraph_url)
        buttons.data_button("🗑 Delete", f"seedrdel {user_id} {folder_id}")

        out_text = (
            f"<b><u>Seedr Direct Links Generated!</u></b>\n\n"
            f"<b>Title:</b> <code>{escape(page_title)}</code>\n"
            f"<b>Total Size:</b> <code>{get_readable_file_size(total_size)}</code>\n"
            f"<b>Total Files:</b> <code>{len(contents)}</code>\n\n"
            f"🌐 <a href='{telegraph_url}'><b>Telegraph Instant View</b></a>"
        )

        await edit_message(
            msg,
            out_text,
            buttons.build_menu(2),
            disable_web_page_preview=False,
        )

    except Exception as e:
        LOGGER.error(f"SeedrLink error: {e}")
        await edit_message(msg, f"<b>Seedr Link Failed:</b> {escape(str(e))}")
    finally:
        if link and await aiopath.exists(link):
            try:
                await aioremove(link)
            except Exception:
                pass


async def _get_seedr_clean_details(user_id, message_or_query):
    user_dict = user_data.get(user_id, {})
    email = user_dict.get("SEEDR_EMAIL", "")
    password = user_dict.get("SEEDR_PASSWORD", "")
    is_global = False
    if not (email and password):
        if await CustomFilters.sudo("", message_or_query):
            email = Config.SEEDR_EMAIL
            password = Config.SEEDR_PASSWORD
            is_global = True

    return email, password, is_global


async def get_seedr_clean_menu(user_id, message_or_query):
    email, password, is_global = await _get_seedr_clean_details(
        user_id, message_or_query
    )

    if not (email and password):
        uset_cmd = (
            f"/{BotCommands.UserSetCommand[0]}"
            if isinstance(BotCommands.UserSetCommand, list)
            else f"/{BotCommands.UserSetCommand}"
        )
        return (
            "<b>Seedr credentials not configured!</b>\n"
            f"Please set <code>SEEDR_EMAIL</code> and <code>SEEDR_PASSWORD</code> in {uset_cmd} to manage your personal Seedr cloud storage.",
            None,
        )

    try:
        sc = SeedrClient(email, password)
        await sc.login()
        res = await sc.list_contents("0")
    except Exception as e:
        return f"<b>Seedr Login Failed:</b> <code>{escape(str(e))}</code>", None

    if not isinstance(res, dict):
        return "<b>Failed to fetch Seedr contents!</b>", None

    space_max, space_used = await sc.get_space()
    torrents = res.get("torrents", [])
    folders = res.get("folders", [])

    account_type = "Global Shared Account" if is_global else "Personal User Account"
    text = (
        f"⌬ <b><u>Seedr Cloud Storage Manager</u></b>\n"
        f"│\n"
        f"┟ <b>Account</b> → {account_type}\n"
        f"┠ <b>Space Used</b> → <code>{get_readable_file_size(space_used)} / {get_readable_file_size(space_max)}</code>\n"
        f"┠ <b>Torrents</b> → <code>{len(torrents)}</code>\n"
        f"┖ <b>Folders</b> → <code>{len(folders)}</code>\n\n"
    )

    buttons = ButtonMaker()
    has_items = False

    if torrents:
        text += "〶 <b>Torrents:</b>\n"
        for t in torrents:
            t_id = t.get("id") or t.get("user_torrent_id")
            name = t.get("name") or t.get("title") or f"Torrent #{t_id}"
            size = get_readable_file_size(t.get("size", 0))
            text += f"• <code>{escape(name)}</code> ({size})\n"
            buttons.data_button(f"❌ {name[:20]}", f"seedrclean del_t {user_id} {t_id}")
            has_items = True

    if folders:
        if torrents:
            text += "\n"
        text += "〶 <b>Folders:</b>\n"
        for f in folders:
            f_id = f.get("id")
            name = f.get("name") or f"Folder #{f_id}"
            size = get_readable_file_size(f.get("size", 0))
            text += f"• <code>{escape(name)}</code> ({size})\n"
            buttons.data_button(f"❌ {name[:20]}", f"seedrclean del_f {user_id} {f_id}")
            has_items = True

    if not has_items:
        text += "<i>Seedr cloud storage is currently empty!</i>"
    else:
        buttons.data_button(
            "🗑️ Clear All", f"seedrclean clear_all {user_id}", position="footer"
        )

    buttons.data_button(
        "🔄 Refresh", f"seedrclean refresh {user_id}", position="footer"
    )
    buttons.data_button("✖️ Close", f"seedrclean close {user_id}", position="footer")

    return text, buttons.build_menu(2)


@new_task
async def seedr_clean(client, message):
    if Config.DISABLE_SEEDR:
        await send_message(message, "Seedr is currently disabled by the Bot Owner.")
        return
    user_id = message.from_user.id
    msg_text, buttons = await get_seedr_clean_menu(user_id, message)
    await send_message(message, msg_text, buttons)


async def seedr_clean_cb(client, query):
    data = query.data.split()
    action = data[1]
    target_user_id = int(data[2])
    user_id = query.from_user.id

    if user_id != target_user_id and not await CustomFilters.sudo("", query):
        await query.answer("You cannot interact with this menu!", show_alert=True)
        return

    if action == "close":
        await query.answer()
        await delete_message(query.message)
        return

    email, password, _ = await _get_seedr_clean_details(target_user_id, query)

    if not (email and password):
        await query.answer("Seedr credentials missing!", show_alert=True)
        return

    sc = SeedrClient(email, password)

    if action == "clear_all":
        await query.answer("Clearing all Seedr storage...", show_alert=False)
        try:
            t_c, f_c = await clear_seedr_account(email, password)
            await query.answer(
                f"Removed {t_c} torrent(s) and {f_c} folder(s)!", show_alert=True
            )
        except Exception as e:
            await query.answer(f"Error: {e}"[:180], show_alert=True)
    elif action in ("del_t", "del_f"):
        item_id = data[3]
        item_type = "torrent" if action == "del_t" else "folder"
        await query.answer(f"Deleting {item_type}...", show_alert=False)
        try:
            await sc.login()
            await sc.delete(item_type, item_id)
            await query.answer(f"Deleted {item_type} successfully!", show_alert=False)
        except Exception as e:
            await query.answer(f"Failed to delete: {e}"[:180], show_alert=True)
    elif action == "refresh":
        await query.answer("Refreshing...", show_alert=False)

    msg_text, buttons = await get_seedr_clean_menu(target_user_id, query)
    await edit_message(query.message, msg_text, buttons)


async def seedr_del_cb(client, query):
    data = query.data.split()
    user_id = int(data[1])
    folder_id = data[2] if len(data) > 2 else None
    from_user_id = query.from_user.id

    if from_user_id != user_id and not await CustomFilters.sudo("", query):
        await query.answer(
            "You are not allowed to delete this message!", show_alert=True
        )
        return

    await query.answer("Deleting Seedr cloud files & messages...", show_alert=False)

    if folder_id:
        email, password, _ = await _get_seedr_clean_details(user_id, query)
        if email and password:
            sc = SeedrClient(email, password)
            try:
                await sc.login()
                await sc.delete("folder", folder_id)
            except Exception:
                try:
                    await sc.delete("torrent", folder_id)
                except Exception:
                    pass

    message = query.message
    reply_to = message.reply_to_message
    await delete_message(message, reply_to)


async def seedrsync_cb(client, query):
    data = query.data.split()
    user_id = int(data[1])
    if query.from_user.id != user_id and not await CustomFilters.sudo("", query):
        await query.answer("You cannot interact with this task!", show_alert=True)
        return
    await query.answer("Syncing Seedr progress...", show_alert=False)


async def seedrcancel_cb(client, query):
    data = query.data.split()
    user_id = int(data[1])
    torrent_id = data[2]
    if query.from_user.id != user_id and not await CustomFilters.sudo("", query):
        await query.answer("You cannot interact with this task!", show_alert=True)
        return

    await query.answer("Cancelling Seedr Cloud task...", show_alert=False)
    email, password, _ = await _get_seedr_clean_details(user_id, query)
    if email and password:
        sc = SeedrClient(email, password)
        try:
            await sc.login()
            await sc.delete("torrent", torrent_id)
        except Exception:
            pass

    message = query.message
    reply_to = message.reply_to_message
    await sleep(3)
    await delete_message(message, reply_to)


@new_task
async def confirm_dump_chat(_, query):
    user_id = query.from_user.id
    data = query.data.split()
    cmd_user_id = int(data[1])
    if user_id != cmd_user_id and not await CustomFilters.sudo("", query):
        await query.answer("This task is not for you!", show_alert=True)
        return
    msg_id = int(data[2])
    cache_key = f"sdump_{msg_id}"
    if cache_key not in bot_cache:
        await query.answer("Session Expired", show_alert=True)
        return
    action = data[3]
    if action == "scancel":
        bot_cache[cache_key][2] = True
        await query.answer("Cancelled!")
        return
    if action == "sdone":
        bot_cache[cache_key][1] = True
        await query.answer("Confirmed!")
        return
    await query.answer()
    idx = int(action)
    dump_chats = bot_cache[cache_key][4]
    dump_names = list(dump_chats)
    if 0 <= idx < len(dump_names):
        selected_name = dump_names[idx]
        bot_cache[cache_key][0] = dump_chats[selected_name]
        buttons = ButtonMaker()
        for i, name in enumerate(dump_names):
            buttons.data_button(
                f"{'✓️' if i == idx else ''} {name}",
                f"sdump {user_id} {msg_id} {i}",
            )
        buttons.data_button(
            "Cancel",
            f"sdump {user_id} {msg_id} scancel",
            position="footer",
            style=ButtonStyle.DANGER,
        )
        buttons.data_button(
            "Done (60)",
            f"sdump {user_id} {msg_id} sdone",
            position="footer",
            style=ButtonStyle.SUCCESS,
        )
        await edit_message(
            query.message,
            f"<b>Select the dump chat for this task</b>\n\n"
            f"<i><b>Dump Chat:</b></i> <code>{selected_name}</code>\n\n"
            f"<b>Timeout:</b> 60 sec",
            buttons.build_menu(3),
        )
