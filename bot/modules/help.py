from pyrogram import raw
from ..core.tg_client import TgClient
from .. import LOGGER
from ..helper.ext_utils.bot_utils import COMMAND_USAGE, new_task
from ..helper.ext_utils.help_messages import (
    YT_HELP_DICT,
    MIRROR_HELP_DICT,
    CLONE_HELP_DICT,
    help_string,
)
from ..helper.telegram_helper.button_build import ButtonMaker
from ..helper.telegram_helper.message_utils import (
    edit_message,
    delete_message,
    send_message,
)


from html import escape
from ..core.config_manager import Config
from ..helper.telegram_helper.bot_commands import BotCommands


def _format_cmd(key):
    """Retrieve command string representation from BotCommands."""
    cmd_attr = getattr(BotCommands, f"{key}Command", None)
    if not cmd_attr:
        return ""
    if isinstance(cmd_attr, list):
        return f"/{' or /'.join(cmd_attr)}"
    return f"/{cmd_attr}"


def build_rich_help_html():
    """Build a comprehensive, categorized Rich Text v2 help interface containing ALL commands."""
    author = escape(Config.AUTHOR_NAME or "WZML-X")

    # Command lookup helpers
    c = {
        "Mirror": _format_cmd("Mirror"),
        "QbMirror": _format_cmd("QbMirror"),
        "JdMirror": _format_cmd("JdMirror"),
        "NzbMirror": _format_cmd("NzbMirror"),
        "Ytdl": _format_cmd("Ytdl"),
        "UpHoster": _format_cmd("UpHoster"),
        "Leech": _format_cmd("Leech"),
        "QbLeech": _format_cmd("QbLeech"),
        "JdLeech": _format_cmd("JdLeech"),
        "NzbLeech": _format_cmd("NzbLeech"),
        "YtdlLeech": _format_cmd("YtdlLeech"),
        "Sleech": _format_cmd("Sleech"),
        "StremioList": _format_cmd("StremioList"),
        "StremioDuplicate": _format_cmd("StremioDuplicate"),
        "SeedrLink": _format_cmd("SeedrLink"),
        "SeedrClean": _format_cmd("SeedrClean"),
        "Clone": _format_cmd("Clone"),
        "Count": _format_cmd("Count"),
        "Delete": _format_cmd("Delete"),
        "List": _format_cmd("List"),
        "RcRefreshIndex": _format_cmd("RcRefreshIndex"),
        "Recent": _format_cmd("Recent"),
        "Latest": _format_cmd("Latest"),
        "RclStorage": _format_cmd("RclStorage"),
        "RclDelete": _format_cmd("RclDelete"),
        "Search": _format_cmd("Search"),
        "GDClean": _format_cmd("GDClean"),
        "Status": _format_cmd("Status"),
        "CancelTask": _format_cmd("CancelTask"),
        "CancelAll": _format_cmd("CancelAll"),
        "ForceStart": _format_cmd("ForceStart"),
        "Select": _format_cmd("Select"),
        "UserSet": _format_cmd("UserSet"),
        "BotSet": _format_cmd("BotSet"),
        "Stats": _format_cmd("Stats"),
        "Ping": _format_cmd("Ping"),
        "Restart": _format_cmd("Restart"),
        "RestartSessions": _format_cmd("RestartSessions"),
        "Broadcast": _format_cmd("Broadcast"),
        "Log": _format_cmd("Log"),
        "Plugins": _format_cmd("Plugins"),
        "Rss": _format_cmd("Rss"),
        "MediaInfo": _format_cmd("MediaInfo"),
        "IMDB": _format_cmd("IMDB"),
        "Shell": _format_cmd("Shell"),
        "AExec": _format_cmd("AExec"),
        "Exec": _format_cmd("Exec"),
        "ClearLocals": _format_cmd("ClearLocals"),
        "Users": _format_cmd("Users"),
        "Authorize": _format_cmd("Authorize"),
        "UnAuthorize": _format_cmd("UnAuthorize"),
        "AddSudo": _format_cmd("AddSudo"),
        "RmSudo": _format_cmd("RmSudo"),
    }

    return f"""<h1>{author} Command Guide</h1>
<i>Tap any category below to expand commands and usage syntax.</i>

<details>
<summary><b>Mirror & Cloud Upload</b></summary>
<table bordered striped compact>
<tr><th>Command</th><th>Description</th></tr>
<tr><td><code>{c["Mirror"]}</code></td><td>Mirror direct/cloud link to destination</td></tr>
<tr><td><code>{c["QbMirror"]}</code></td><td>Torrent mirror via qBittorrent</td></tr>
<tr><td><code>{c["JdMirror"]}</code></td><td>Download via JDownloader engine</td></tr>
<tr><td><code>{c["NzbMirror"]}</code></td><td>Usenet NZB download via SABnzbd</td></tr>
<tr><td><code>{c["Ytdl"]}</code></td><td>Mirror yt-dlp supported audio/video</td></tr>
<tr><td><code>{c["UpHoster"]}</code></td><td>Upload to DDL hoster servers</td></tr>
</table>
</details>

<details>
<summary><b>Leech to Telegram & Stremio</b></summary>
<table bordered striped compact>
<tr><th>Command</th><th>Description</th></tr>
<tr><td><code>{c["Leech"]}</code></td><td>Download direct link & send to Telegram</td></tr>
<tr><td><code>{c["QbLeech"]}</code></td><td>Leech torrent via qBittorrent</td></tr>
<tr><td><code>{c["JdLeech"]}</code></td><td>Leech via JDownloader engine</td></tr>
<tr><td><code>{c["NzbLeech"]}</code></td><td>Leech Usenet NZB via SABnzbd</td></tr>
<tr><td><code>{c["YtdlLeech"]}</code></td><td>Extract & leech yt-dlp audio/video</td></tr>
<tr><td><code>{c["Sleech"]}</code></td><td>Leech directly to Stremio Dump channel</td></tr>
<tr><td><code>{c["StremioList"]}</code></td><td>List movies/series in Stremio database</td></tr>
<tr><td><code>{c["StremioDuplicate"]}</code></td><td>Audit database for media duplicates</td></tr>
<tr><td><code>{c["SeedrLink"]}</code></td><td>Get direct Seedr HTTP download links</td></tr>
<tr><td><code>{c["SeedrClean"]}</code></td><td>Clean / delete files from Seedr account</td></tr>
</table>
</details>

<details>
<summary><b>Cloud, Drive, Rclone & Search</b></summary>
<table bordered striped compact>
<tr><th>Command</th><th>Description</th></tr>
<tr><td><code>{c["Clone"]}</code></td><td>Fast copy GDrive or Rclone file/folder</td></tr>
<tr><td><code>{c["Count"]}</code></td><td>Count file/folder contents in Google Drive</td></tr>
<tr><td><code>{c["Delete"]}</code></td><td>Delete file/folder from Google Drive</td></tr>
<tr><td><code>{c["GDClean"]}</code></td><td>Clean empty folders/shortcuts in GDrive</td></tr>
<tr><td><code>{c["List"]}</code></td><td>Search files in Google Drive or Rclone</td></tr>
<tr><td><code>{c["Recent"]}</code></td><td>Get recent Rclone search queries</td></tr>
<tr><td><code>{c["Latest"]}</code></td><td>Get list of latest files from Rclone</td></tr>
<tr><td><code>{c["RclStorage"]}</code></td><td>Check Rclone remote storage capacity</td></tr>
<tr><td><code>{c["RclDelete"]}</code></td><td>Delete file/folder from Rclone remote</td></tr>
<tr><td><code>{c["RcRefreshIndex"]}</code></td><td>Refresh Rclone remote search index</td></tr>
<tr><td><code>{c["Search"]}</code></td><td>Search torrents via Jackett / Plugins with Sleech/Mirror</td></tr>
</table>
</details>

<details>
<summary><b>Task Control & Queue</b></summary>
<table bordered striped compact>
<tr><th>Command</th><th>Description</th></tr>
<tr><td><code>{c["Status"]}</code></td><td>Show real-time transfer progress and speed</td></tr>
<tr><td><code>{c["CancelTask"]}</code></td><td>Cancel task by GID or replying to message</td></tr>
<tr><td><code>{c["CancelAll"]}</code></td><td>Cancel all tasks in the queue or by status</td></tr>
<tr><td><code>{c["ForceStart"]}</code></td><td>Bypass queue limits & start task immediately</td></tr>
<tr><td><code>{c["Select"]}</code></td><td>Select files to download from Torrent / NZB</td></tr>
<tr><td><code>{c["MediaInfo"]}</code></td><td>Generate MediaInfo details for file/stream</td></tr>
<tr><td><code>{c["IMDB"]}</code></td><td>Search movies and TV shows on IMDB</td></tr>
</table>
</details>

<details>
<summary><b>User & Bot Settings</b></summary>
<table bordered striped compact>
<tr><th>Command</th><th>Description</th></tr>
<tr><td><code>{c["UserSet"]}</code></td><td>Manage personal user settings & tokens</td></tr>
<tr><td><code>{c["BotSet"]}</code></td><td>Configure global bot variables (Sudo)</td></tr>
<tr><td><code>{c["Stats"]}</code></td><td>Host system & bot resource statistics</td></tr>
<tr><td><code>{c["Ping"]}</code></td><td>Check bot ping & response latency</td></tr>
<tr><td><code>{c["Rss"]}</code></td><td>Open and manage RSS feed subscriptions</td></tr>
<tr><td><code>{c["Plugins"]}</code></td><td>View installed and enabled bot plugins</td></tr>
</table>
</details>

<details>
<summary><b>Owner & Sudo Admin Tools</b></summary>
<table bordered striped compact>
<tr><th>Command</th><th>Description</th></tr>
<tr><td><code>{c["Restart"]}</code></td><td>Restart and update the bot</td></tr>
<tr><td><code>{c["RestartSessions"]}</code></td><td>Restart user MTProto client sessions</td></tr>
<tr><td><code>{c["Broadcast"]}</code></td><td>Broadcast message to all bot users</td></tr>
<tr><td><code>{c["Log"]}</code></td><td>Send recent bot log file for debugging</td></tr>
<tr><td><code>{c["Users"]}</code></td><td>Show users database and settings</td></tr>
<tr><td><code>{c["Authorize"]}</code></td><td>Authorize chat or user to use the bot</td></tr>
<tr><td><code>{c["UnAuthorize"]}</code></td><td>Revoke authorization from chat or user</td></tr>
<tr><td><code>{c["AddSudo"]}</code> / <code>{c["RmSudo"]}</code></td><td>Add or remove bot sudo administrators</td></tr>
<tr><td><code>{c["Shell"]}</code></td><td>Execute shell commands on host (Owner)</td></tr>
<tr><td><code>{c["AExec"]}</code> / <code>{c["Exec"]}</code></td><td>Execute async/sync python functions</td></tr>
<tr><td><code>{c["ClearLocals"]}</code></td><td>Clear execution environment locals</td></tr>
</table>
</details>

<details>
<summary><b>Common Flags & Syntax Examples</b></summary>
<ul>
<li><code>-n &lt;name&gt;</code> — Rename downloaded file or root folder</li>
<li><code>-z [pass]</code> — Zip archive contents before uploading</li>
<li><code>-e [pass]</code> — Extract archive before uploading</li>
<li><code>-up &lt;dest&gt;</code> — Specify custom upload destination</li>
<li><code>-s</code> — Select specific files in multi-file torrent/nzb</li>
<li><code>-b</code> — Bulk task execution (from text file or links)</li>
<li><code>-i &lt;count&gt;</code> — Multi-link processing by reply</li>
<li><code>-m &lt;folder&gt;</code> — Group multiple downloads into one folder</li>
<li><code>-rcflags &lt;flags&gt;</code> — Pass custom rclone flags</li>
</ul>
</details>

<hr/>
<i>Tip: Send <code>{_format_cmd("Help")} mirror</code>, <code>{_format_cmd("Help")} yt</code>, or <code>{_format_cmd("Help")} clone</code> for interactive flag pagers.</i>"""


async def send_rich_help(message, rich_html, fallback_text, buttons=None):
    """Deliver help with Telegram Rich Text v2, gracefully falling back on older clients."""
    try:
        peer = await TgClient.bot.resolve_peer(message.chat.id)
        reply_to_id = message.id if hasattr(message, "id") else None
        markup = await buttons.write(TgClient.bot) if buttons else None
        await TgClient.bot.invoke(
            raw.functions.messages.SendMessage(
                peer=peer,
                message="",
                random_id=TgClient.bot.rnd_id(),
                reply_to=raw.types.InputReplyToMessage(reply_to_msg_id=reply_to_id)
                if reply_to_id
                else None,
                reply_markup=markup,
                rich_message=raw.types.InputRichMessageHTML(
                    html=rich_html,
                    files=None,
                ),
            )
        )
    except Exception as exc:
        LOGGER.warning(f"Rich Text v2 help failed, falling back to standard: {exc}")
        await send_message(message, fallback_text, buttons)


@new_task
async def arg_usage(_, query):
    data = query.data.split()
    message = query.message
    await query.answer()
    if data[1] == "close":
        return await delete_message(message, message.reply_to_message)
    pg_no = int(data[3])
    if data[1] == "nex":
        if data[2] == "mirror":
            await edit_message(
                message, COMMAND_USAGE["mirror"][0], COMMAND_USAGE["mirror"][pg_no + 1]
            )
        elif data[2] == "yt":
            await edit_message(
                message, COMMAND_USAGE["yt"][0], COMMAND_USAGE["yt"][pg_no + 1]
            )
        elif data[2] == "clone":
            await edit_message(
                message, COMMAND_USAGE["clone"][0], COMMAND_USAGE["clone"][pg_no + 1]
            )
    elif data[1] == "pre":
        if data[2] == "mirror":
            await edit_message(
                message, COMMAND_USAGE["mirror"][0], COMMAND_USAGE["mirror"][pg_no - 1]
            )
        elif data[2] == "yt":
            await edit_message(
                message, COMMAND_USAGE["yt"][0], COMMAND_USAGE["yt"][pg_no - 1]
            )
        elif data[2] == "clone":
            await edit_message(
                message, COMMAND_USAGE["clone"][0], COMMAND_USAGE["clone"][pg_no - 1]
            )
    elif data[1] == "back":
        if data[2] == "m":
            await edit_message(
                message, COMMAND_USAGE["mirror"][0], COMMAND_USAGE["mirror"][pg_no + 1]
            )
        elif data[2] == "y":
            await edit_message(
                message, COMMAND_USAGE["yt"][0], COMMAND_USAGE["yt"][pg_no + 1]
            )
        elif data[2] == "c":
            await edit_message(
                message, COMMAND_USAGE["clone"][0], COMMAND_USAGE["clone"][pg_no + 1]
            )
    elif data[1] == "mirror":
        buttons = ButtonMaker()
        buttons.data_button("Back", f"help back m {pg_no}")
        button = buttons.build_menu()
        await edit_message(message, MIRROR_HELP_DICT[data[2]], button)
    elif data[1] == "yt":
        buttons = ButtonMaker()
        buttons.data_button("Back", f"help back y {pg_no}")
        button = buttons.build_menu()
        await edit_message(message, YT_HELP_DICT[data[2]], button)
    elif data[1] == "clone":
        buttons = ButtonMaker()
        buttons.data_button("Back", f"help back c {pg_no}")
        button = buttons.build_menu()
        await edit_message(message, CLONE_HELP_DICT[data[2]], button)


@new_task
async def bot_help(_, message):
    rich_html = build_rich_help_html()
    button = ButtonMaker()
    button.data_button("Close", "help close", position="footer")
    await send_rich_help(message, rich_html, help_string, button.build_menu(1))
