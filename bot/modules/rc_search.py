import subprocess
import json
import re
import uuid
from threading import Lock
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin, quote
from datetime import datetime, timedelta
from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from rapidfuzz import fuzz

from .. import LOGGER
from ..core.config_manager import Config, BinConfig

# Get rclone configuration from Config
RCLONE_REMOTE = getattr(Config, "RCLONE_REMOTE", "")
RCLONE_SERVE_URL = getattr(Config, "RCLONE_SERVE_URL", "")
REMOTE_BASE_PATH = getattr(Config, "REMOTE_BASE_PATH", "")
RESULTS_PER_PAGE = getattr(Config, "RESULTS_PER_PAGE", 4)
SUDO_USERS = getattr(Config, "SUDO_USERS", "")

# Store search results temporarily
search_cache = {}

# Store last 10 searches per user
search_history = {}

# Convert sudo users string → set of ints
SUDO_USERS_SET = set(int(x) for x in SUDO_USERS.split() if x.isdigit())

# Store pending deletions (key -> (requester_id, full_path, is_dir))
pending_deletions = {}

# ---------------- Auto Index Refresh Cache ---------------- #
global_file_index = []
global_index_timestamp = None
INDEX_TTL = 600  # Refresh every 10 minutes
_index_lock = Lock()


# ---------------- Helper Functions ---------------- #
def run_rclone_command(cmd, description="rclone command"):
    """Run rclone command with logging and error handling."""
    LOGGER.info(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode()
        LOGGER.info(f"{description} succeeded.")

        return result

    except subprocess.CalledProcessError as e:
        LOGGER.error(
            f"{description} failed with code {e.returncode}: {e.output.decode()}"
        )

        return None

    except Exception as e:
        LOGGER.error(f"{description} unexpected error: {e}")

        return None


def refresh_global_index(force=False):
    """
    Refresh global index every INDEX_TTL seconds.
    If force=True → force-refresh the index.
    """
    global global_file_index, global_index_timestamp

    with _index_lock:
        now = datetime.now()

        # Use cached index if still valid
        if (
            not force
            and global_index_timestamp
            and (now - global_index_timestamp).total_seconds() < INDEX_TTL
        ):
            return global_file_index

        try:
            result = run_rclone_command(
                [
                    BinConfig.RCLONE_NAME,
                    "--config",
                    "rclone.conf",  # Use local rclone.conf
                    "lsjson",
                    RCLONE_REMOTE,
                    "--fast-list",
                    "--recursive",
                ],
                description="Refreshing global index",
            )

            if result:
                try:
                    global_file_index = json.loads(result)
                    global_index_timestamp = now
                    LOGGER.info(
                        f"Global index refreshed with {len(global_file_index)} files."
                    )
                    return global_file_index
                except Exception as e:
                    LOGGER.error(f"Failed to parse global index JSON: {e}")

        except Exception as e:
            LOGGER.error(f"Error refreshing global index: {e}")

        return global_file_index if global_file_index else None


def search_files():
    """
    Return file list from global cache.
    Auto refreshes every INDEX_TTL seconds.
    """
    return refresh_global_index()


def parse_size(size_str):
    """Convert size string like '1GB', '500MB' to bytes."""
    size_str = size_str.upper().strip()

    units = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}

    # Match number and unit
    match = re.match(r"^([\d.]+)\s*(B|KB|MB|GB|TB)$", size_str)
    if not match:
        return None

    value = float(match.group(1))
    unit = match.group(2)

    return int(value * units[unit])


def format_size(size_bytes):
    """Convert size to human-readable format."""
    if size_bytes <= 0:
        return "-1.00B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.2f}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f}PB"


def normalize(text):
    """Normalize filenames and queries."""
    return re.sub(r"[._-]", " ", text).lower()


def highlight_match(text, query):
    """Highlight matched words in text using markdown bold."""
    query_words = normalize(query).split()
    result = text

    for word in query_words:
        if len(word) < 2:  # Skip very short words
            continue
        # Case-insensitive replacement with bold markdown
        pattern = re.compile(re.escape(word), re.IGNORECASE)
        result = pattern.sub(lambda m: f"**{m.group(0)}**", result)

    return result


def match_file(query, filename, threshold=85):
    """
    Strict matching:
    1. Substring match (full query in filename)
    2. OR strong fuzzy match using token_sort_ratio
    Prevents partial word false matches (e.g., "forza" won't match "formant").
    """
    query_norm = normalize(query)
    filename_norm = normalize(filename)

    # Exact substring match
    if query_norm in filename_norm:
        return True

    # Fuzzy match (strict)
    query_words = query_norm.split()
    for word in query_words:
        score = fuzz.token_sort_ratio(word, filename_norm)
        if score < threshold:
            return False
    return True


def parse_date_filter(date_str):
    """Parse date string like '7d', '30d', '1y' and return datetime."""
    date_str = date_str.lower().strip()

    match = re.match(r"^(\d+)(d|w|m|y)$", date_str)
    if not match:
        return None

    value = int(match.group(1))
    unit = match.group(2)

    now = datetime.now()

    if unit == "d":
        return now - timedelta(days=value)
    elif unit == "w":
        return now - timedelta(weeks=value)
    elif unit == "m":
        return now - timedelta(days=value * 30)  # Approximate
    elif unit == "y":
        return now - timedelta(days=value * 365)  # Approximate

    return None


def parse_search_args(args):
    """
    Parse search arguments and extract filters.
    Returns: (query, file_type, min_size, max_size, date_filter)

    Examples:
        /list software --type zip
        /list movie --type mkv --min 1GB --max 10GB
        /list document --date 7d
    """
    query_parts = []
    file_type = None
    min_size = None
    max_size = None
    date_filter = None

    i = 0
    while i < len(args):
        arg = args[i]

        if arg == "--type" and i + 1 < len(args):
            file_type = args[i + 1].lower().lstrip(".")
            i += 2
        elif arg == "--min" and i + 1 < len(args):
            min_size = parse_size(args[i + 1])
            i += 2
        elif arg == "--max" and i + 1 < len(args):
            max_size = parse_size(args[i + 1])
            i += 2
        elif arg == "--date" and i + 1 < len(args):
            date_filter = parse_date_filter(args[i + 1])
            i += 2
        else:
            query_parts.append(arg)
            i += 1

    query = " ".join(query_parts)
    return query, file_type, min_size, max_size, date_filter


def apply_filters(
    files, query, file_type=None, min_size=None, max_size=None, date_filter=None
):
    """Apply all filters to file list."""
    matched_files = []

    for f in files:
        # Skip directories for size and type filters
        is_dir = f.get("IsDir", False)

        # Match query
        if query and not match_file(query, f["Name"]):
            continue

        # Filter by file type
        if file_type:
            # When a type filter is used, ignore directories
            if is_dir:
                continue

            file_ext = Path(f["Name"]).suffix.lower().lstrip(".")
            if file_ext != file_type:
                continue

        # Filter by size
        if not is_dir:
            file_size = f.get("Size", 0)

            if min_size is not None and file_size < min_size:
                continue

            if max_size is not None and file_size > max_size:
                continue

        # Filter by date
        if date_filter:
            mod_time_str = f.get("ModTime", "")
            if mod_time_str:
                try:
                    # Parse ISO format timestamp
                    mod_time = datetime.fromisoformat(
                        mod_time_str.replace("Z", "+00:00")
                    )
                    if mod_time < date_filter:
                        continue
                except Exception:
                    pass

        matched_files.append(f)

    return matched_files


def is_valid_query(query):
    """
    Validate the search query.
    Returns True if valid, False otherwise.
    """
    # Remove leading/trailing whitespace
    query = query.strip()

    # Check if query is empty
    if not query:
        return False

    # Check if query is just special characters or wildcards
    # Only alphanumeric characters are considered valid
    if not any(c.isalnum() for c in query):
        return False

    # Check for single character wildcards
    if query in ["*", "?", ".", ".."]:
        return False

    return True


@lru_cache(maxsize=500)
def get_folder_size(path):
    """
    Returns (bytes, file_count) for a folder.
    First calculates from global_file_index in memory (instant, 0 network calls).
    Falls back to `rclone size` only if index is empty.
    """
    norm_path = path.strip("/").lower()

    # 1. Fast, instant in-memory calculation from cached index
    if global_file_index:
        total_bytes = 0
        file_count = 0
        found = False
        prefix = f"{norm_path}/"
        for item in global_file_index:
            item_path = item.get("Path", "").strip("/").lower()
            if item_path.startswith(prefix) and not item.get("IsDir", False):
                found = True
                total_bytes += item.get("Size", 0)
                file_count += 1
        if found:
            return total_bytes, file_count

    # 2. Fallback to `rclone size`
    try:
        remote = RCLONE_REMOTE.rstrip(":") + ":"
        parts = [p.strip("/") for p in [REMOTE_BASE_PATH, path] if p and p.strip("/")]
        remote_target = f"{remote}{'/'.join(parts)}"

        cmd = [
            BinConfig.RCLONE_NAME,
            "--config",
            "rclone.conf",
            "size",
            remote_target,
            "--json",
        ]

        result = run_rclone_command(cmd, description=f"Getting folder size for {path}")
        if result:
            data = json.loads(result)
            return data.get("bytes", 0), data.get("count", 0)

        return 0, 0
    except Exception as e:
        LOGGER.error(f"Error parsing folder size for {path}: {e}")
        return 0, 0


def create_result_text(matched_files, page, query, filters_applied):
    """Create text for a specific page of results."""
    total_results = len(matched_files)
    total_pages = (total_results + RESULTS_PER_PAGE - 1) // RESULTS_PER_PAGE

    start_idx = page * RESULTS_PER_PAGE
    end_idx = min(start_idx + RESULTS_PER_PAGE, total_results)

    header = f"🔎 **Search Results for:** {query}\n"

    # Show active filters
    if filters_applied:
        header += "🔧 **Filters:** " + ", ".join(filters_applied) + "\n"

    header += f"📊 **Total:** {total_results} | **Page:** {page + 1}/{total_pages}\n\n"

    reply = header

    for i in range(start_idx, end_idx):
        f = matched_files[i]
        name = f["Name"]

        # Highlight matched words in filename
        if query:
            highlighted_name = highlight_match(name, query)
        else:
            highlighted_name = name

        if f.get("IsDir", False):
            folder_path = f["Path"]

            # Clean path for rclone size
            if folder_path.startswith(f"{REMOTE_BASE_PATH}/"):
                folder_path = folder_path[len(REMOTE_BASE_PATH) + 1 :]

            bytes_size, file_count = get_folder_size(folder_path)
            size = f"{file_count} files | {format_size(bytes_size)}"
        else:
            size = format_size(f.get("Size", -1))

        path = f["Path"]

        # Remove duplicate folder from path
        if path.startswith(f"{REMOTE_BASE_PATH}/"):
            path = path[len(REMOTE_BASE_PATH) + 1 :]

        # URL encode the path for proper link generation
        encoded_path = quote(f"{REMOTE_BASE_PATH}/{path}")
        public_link = urljoin(RCLONE_SERVE_URL + "/", encoded_path)

        # Escape special characters for Markdown (but keep our bold highlights)
        escaped_name = highlighted_name

        icon = "📁" if f.get("IsDir", False) else "📄"

        reply += (
            f"**{i + 1}.** {icon} {escaped_name}\n"
            f"💾 **Size:** {size}\n"
            f"[🔗 Open Link]({public_link})\n\n"
        )

    return reply, total_pages


@lru_cache(maxsize=1)
def get_rclone_storage():
    """
    Returns (used, free, total) in bytes using `rclone about`.
    Cached to avoid frequent calls.
    """
    try:
        cmd = [
            BinConfig.RCLONE_NAME,
            "--config",
            "rclone.conf",
            "about",
            RCLONE_REMOTE,
            "--json",
        ]

        result = run_rclone_command(cmd, description="Getting rclone storage")
        if not result:
            return None

        data = json.loads(result)

        return (data.get("used", 0), data.get("free", 0), data.get("total", 0))

    except Exception as e:
        LOGGER.error(f"rclone storage error: {e}")
        return None


def is_sudo(user_id: int) -> bool:
    return user_id in SUDO_USERS_SET


def create_pagination_buttons(page, total_pages, user_id, query):
    """Create pagination buttons."""
    buttons = []
    nav_buttons = []

    # Previous button
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton(
                "⬅️ Previous", callback_data=f"page:{user_id}:{page - 1}:{query}"
            )
        )

    # Next button
    if page < total_pages - 1:
        nav_buttons.append(
            InlineKeyboardButton(
                "Next ➡️", callback_data=f"page:{user_id}:{page + 1}:{query}"
            )
        )

    if nav_buttons:
        buttons.append(nav_buttons)

    # Close button
    buttons.append(
        [
            InlineKeyboardButton(
                "❌ Close", callback_data=f"page:{user_id}:close:{query}"
            )
        ]
    )

    return InlineKeyboardMarkup(buttons)


async def run_rclone_search(
    client: Client, message: Message, args: list, edit_message_obj: Message = None
):
    # Extract command and arguments
    command_message_id = message.id
    chat_id = message.chat.id
    user_id = message.from_user.id

    query, file_type, min_size, max_size, date_filter = parse_search_args(args)

    # Validate query (can be empty if filters are provided)
    if query and not is_valid_query(query):
        err_text = (
            "❌ **Invalid command**\n\nUsage: `/list <query> [options]`\n\n"
            "Please provide a valid search query with alphanumeric characters."
        )
        if edit_message_obj:
            await edit_message_obj.edit_text(err_text, parse_mode=ParseMode.MARKDOWN)
        else:
            await message.reply_text(err_text, parse_mode=ParseMode.MARKDOWN)
        return

    # At least one filter or query must be provided
    if (
        not query
        and not file_type
        and not min_size
        and not max_size
        and not date_filter
    ):
        err_text = (
            "❌ **Invalid command**\n\nUsage: `/list <query> [options]`\n\n"
            "Please provide at least a search query or filter."
        )
        if edit_message_obj:
            await edit_message_obj.edit_text(err_text, parse_mode=ParseMode.MARKDOWN)
        else:
            await message.reply_text(err_text, parse_mode=ParseMode.MARKDOWN)
        return

    # Build filter description
    filters_applied = []
    if file_type:
        filters_applied.append(f"Type: {file_type}")
    if min_size:
        filters_applied.append(f"Min: {format_size(min_size)}")
    if max_size:
        filters_applied.append(f"Max: {format_size(max_size)}")
    if date_filter:
        filters_applied.append(f"Modified after: {date_filter.strftime('%Y-%m-%d')}")

    # "Searching for..." indicator
    search_text = f"🔍 Searching for: **{query or 'all files'}**"
    if filters_applied:
        search_text += f"\n🔧 Filters: {', '.join(filters_applied)}"
    search_text += " ..."

    if edit_message_obj:
        search_msg = edit_message_obj
        await search_msg.edit_text(search_text, parse_mode=ParseMode.MARKDOWN)
    else:
        search_msg = await message.reply_text(
            search_text, parse_mode=ParseMode.MARKDOWN
        )

    files = search_files()

    if not files:
        await search_msg.edit_text(
            f"❌ No results found for: **{query or 'your search'}**",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Apply all filters
    matched_files = apply_filters(
        files, query, file_type, min_size, max_size, date_filter
    )

    if not matched_files:
        await search_msg.edit_text(
            f"❌ No results found for: **{query or 'your search'}**\n\n"
            f"Try adjusting your filters or search query.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Save search query to history (dedupe, newest first, max 10)
    if query:
        old = search_history.get(user_id, [])
        new_list = [query] + [q for q in old if q != query]
        search_history[user_id] = new_list[:10]

    # Store results in cache with unique key including filters
    cache_key = uuid.uuid4().hex[:8]  # short, safe (8 bytes)
    search_cache[cache_key] = {
        "files": matched_files,
        "query": query or "all files",
        "filters": filters_applied,
        "cmd_msg_id": command_message_id,
        "chat_id": chat_id,
    }

    # Create first page
    reply, total_pages = create_result_text(
        matched_files, 0, query or "all files", filters_applied
    )
    buttons = create_pagination_buttons(0, total_pages, user_id, cache_key)

    if edit_message_obj:
        await search_msg.edit_text(
            reply,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=buttons,
            disable_web_page_preview=True,
        )
    else:
        try:
            await search_msg.delete()
        except Exception:
            pass
        await message.reply_text(
            reply,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=buttons,
            disable_web_page_preview=True,
        )


async def rcrefreshindex_command(client: Client, message: Message):
    status = await message.reply_text(
        "🔄 Forcing global index refresh...",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        files = refresh_global_index(force=True)

        if not files:
            return await status.edit_text(
                "❌ Failed to refresh index.",
                parse_mode=ParseMode.MARKDOWN,
            )

        await status.edit_text(
            f"✅ **Index refreshed successfully**\n\n"
            f"📦 Total files indexed: `{len(files)}`",
            parse_mode=ParseMode.MARKDOWN,
        )

    except Exception as e:
        LOGGER.error(f"Index refresh failed: {e}")
        await status.edit_text(
            "❌ Error occurred while refreshing index.",
            parse_mode=ParseMode.MARKDOWN,
        )


async def recent_searches(client: Client, message: Message):
    user_id = message.from_user.id
    history = search_history.get(user_id, [])

    if not history:
        return await message.reply_text(
            "📭 **No recent searches yet!**\nStart searching using `/list <keyword>`.",
            parse_mode=ParseMode.MARKDOWN,
        )

    formatted = "\n".join([f"**{i + 1}.** `{q}`" for i, q in enumerate(history)])

    await message.reply_text(
        f"🕘 **Your Recent Searches (last {len(history)}):**\n\n{formatted}",
        parse_mode=ParseMode.MARKDOWN,
    )


async def latest_uploads(client: Client, message: Message):
    """Show latest uploaded/modified files with pagination and highlights."""
    user_id = message.from_user.id

    # "Fetching latest uploads..." indicator
    fetching_msg = await message.reply_text(
        "⏳ Fetching latest uploads...", parse_mode=ParseMode.MARKDOWN
    )

    # Get all files from remote
    files = search_files()
    if not files:
        await fetching_msg.edit_text(
            "❌ Unable to fetch files from remote.", parse_mode=ParseMode.MARKDOWN
        )
        return

    # Filter out directories, only files
    file_list = [f for f in files if not f.get("IsDir", False)]

    # Sort files by ModTime descending
    def get_mod_time(f):
        try:
            return datetime.fromisoformat(f.get("ModTime", "").replace("Z", "+00:00"))
        except Exception:
            return datetime.min

    file_list.sort(key=get_mod_time, reverse=True)

    if not file_list:
        await fetching_msg.edit_text(
            "❌ No files found in remote.", parse_mode=ParseMode.MARKDOWN
        )
        return

    # Highlight filenames (optional, here we just keep names as is)
    for f in file_list:
        f["Name"] = highlight_match(
            f["Name"], ""
        )  # empty query, keeps original but can apply styles

    # Cache key for this user
    cache_key = f"latest:{user_id}"
    search_cache[cache_key] = {
        "files": file_list,
        "query": "Latest Uploads",
        "filters": [],
    }

    # Create first page
    reply, total_pages = create_result_text(file_list, 0, "Latest Uploads", [])
    buttons = create_pagination_buttons(0, total_pages, user_id, cache_key)

    # Edit fetching message with first page
    await fetching_msg.edit_text(
        reply,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=buttons,
        disable_web_page_preview=True,
    )


async def rclstorage_command(client: Client, message: Message):
    status_msg = await message.reply_text(
        "⏳ Fetching rclone storage info...", parse_mode=ParseMode.MARKDOWN
    )

    storage = get_rclone_storage()

    if not storage:
        return await status_msg.edit_text(
            "❌ **Unable to fetch storage information**\n\n"
            "This remote may not support `rclone about`.",
            parse_mode=ParseMode.MARKDOWN,
        )

    used, free, total = storage

    # Avoid division by zero
    percent = (used / total * 100) if total else 0

    # Simple text progress bar
    bar_len = 12
    filled = int(bar_len * percent / 100)
    bar = "█" * filled + "░" * (bar_len - filled)

    await status_msg.edit_text(
        f"💽 **Rclone Storage Info**\n\n"
        f"📦 **Used:** {format_size(used)}\n"
        f"🆓 **Free:** {format_size(free)}\n"
        f"📊 **Total:** {format_size(total)}\n\n"
        f"📈 **Usage:** `{bar}` **{percent:.2f}%**",
        parse_mode=ParseMode.MARKDOWN,
    )


async def rcldelete_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            "🗑 **Usage:** `/rcldelete <name>`\nExample: `/rcldelete foldername`",
            parse_mode=ParseMode.MARKDOWN,
        )

    target_query = " ".join(message.command[1:])

    # ------------------ Send "Searching" indicator ------------------
    searching_msg = await message.reply_text(
        f"🔍 Searching for **{target_query}** ...", parse_mode=ParseMode.MARKDOWN
    )

    # Fetch files
    files = search_files()
    if not files:
        await searching_msg.edit_text(
            "❌ Cannot fetch remote index.", parse_mode=ParseMode.MARKDOWN
        )
        return

    # Match files/folders
    matched = [f for f in files if match_file(target_query, Path(f["Path"]).name)]
    if not matched:
        await searching_msg.edit_text(
            f"❌ No files/folders found matching `{target_query}`.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Count folders/files
    folders = sum(1 for f in matched if f.get("IsDir", False))
    files_count = len(matched) - folders

    # Prepare sudo mentions
    sudo_mentions = []
    for uid in SUDO_USERS_SET:
        try:
            user = await client.get_users(uid)
            mention = (
                f"@{user.username}" if user.username else f"[{uid}](tg://user?id={uid})"
            )
        except Exception:
            mention = f"[{uid}](tg://user?id={uid})"
        sudo_mentions.append(mention)
    sudo_mentions_text = " ".join(sudo_mentions)

    # ------------------ Build Approval Message ------------------
    text = (
        f"⚠️ User @{message.from_user.username or message.from_user.id} requests deletion of `{target_query}`\n"
        f"🗂 Folders: {folders} | 📄 Files: {files_count} | ✅ Total: {len(matched)}\n\n"
        f"Sudo users, please confirm deletion by clicking below.\n\n"
        f"{sudo_mentions_text}"
    )

    # Generate a unique callback key
    key = str(uuid.uuid4())
    pending_deletions[key] = {
        "requester_id": message.from_user.id,
        "matches": matched,
        "chat_id": message.chat.id,
        "msg_id": message.id,
    }

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ YES", callback_data=f"confirm_delete:{key}"),
                InlineKeyboardButton("❌ CANCEL", callback_data=f"cancel_delete:{key}"),
            ]
        ]
    )

    # Edit "Searching" message to approval message
    await searching_msg.edit_text(
        text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard
    )


# CALLBACK HANDLER UPDATE
async def confirm_delete_callback(client: Client, callback_query: CallbackQuery):
    try:
        # Show "Deleting..." in chat message
        await callback_query.message.edit_text(
            "🗑 **Deleting… Please wait...**", parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        LOGGER.warning(f"Failed to edit message to deleting: {e}")

    key = callback_query.data.split(":", 1)[1]

    if key not in pending_deletions:
        return

    data = pending_deletions.pop(key)
    requester_id = data["requester_id"]
    matches = data["matches"]
    chat_id = data["chat_id"]
    msg_id = data["msg_id"]

    user_id = callback_query.from_user.id

    # Only sudo users can confirm deletion
    if user_id not in SUDO_USERS_SET:
        return

    # Delete original command message
    try:
        await client.delete_messages(chat_id=chat_id, message_ids=msg_id)
    except Exception as e:
        LOGGER.error(f"Failed to delete command message: {e}")

    folders = [f for f in matches if f.get("IsDir")]
    files = [f for f in matches if not f.get("IsDir")]

    deleted_folders = set()
    deleted_count = 0
    skipped_files = 0

    # DELETE FOLDERS FIRST
    for f in folders:
        folder_path = f["Path"]
        full_path = f"{RCLONE_REMOTE}{folder_path}"

        result = run_rclone_command(
            [BinConfig.RCLONE_NAME, "--config", "rclone.conf", "purge", full_path],
            description="Rclone purge folder",
        )

        if result is not None:
            deleted_folders.add(folder_path)
            deleted_count += 1

    # DELETE FILES NOT INSIDE DELETED FOLDERS
    for f in files:
        file_path = f["Path"]

        # Skip files inside already-deleted folders
        if any(file_path.startswith(folder + "/") for folder in deleted_folders):
            skipped_files += 1
            continue

        full_path = f"{RCLONE_REMOTE}{file_path}"

        result = run_rclone_command(
            [BinConfig.RCLONE_NAME, "--config", "rclone.conf", "delete", full_path],
            description="Rclone delete file",
        )

        if result is not None:
            deleted_count += 1

    # Get requester's display name
    try:
        req_user = await client.get_users(requester_id)
        req_name = f"@{req_user.username}" if req_user.username else str(requester_id)
    except Exception:
        req_name = str(requester_id)

    # Force refresh global index after deletion
    refresh_global_index(force=True)

    # ✅ Update message with final summary
    await callback_query.message.edit_text(
        f"✅ **Deletion completed**\n\n"
        f"🗂 Deleted folders: {len(deleted_folders)}\n"
        f"📄 Deleted files: {deleted_count - len(deleted_folders)}\n"
        f"⏭ Skipped files: {skipped_files}\n\n"
        f"Requested by {req_name}",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cancel_delete_callback(client: Client, callback_query: CallbackQuery):
    try:
        await callback_query.answer("❌ Canceled")
    except Exception:
        pass

    key = callback_query.data.split(":", 1)[1]

    if key not in pending_deletions:
        return

    pending_deletions.pop(key, None)

    await callback_query.message.edit_text(
        "❌ **Deletion canceled.**", parse_mode=ParseMode.MARKDOWN
    )


async def handle_pagination(client: Client, callback_query: CallbackQuery):
    data = callback_query.data.split(":", 3)  # Split into max 4 parts
    user_id = int(data[1])

    # Check if user is authorized
    if callback_query.from_user.id != user_id:
        return await callback_query.answer(
            "❌ This is not your search!", show_alert=True
        )

    # Handle close button
    if data[2] == "close":
        try:
            await callback_query.answer("✅ Closed")
        except Exception:
            pass

        cache_key = data[3] if len(data) > 3 else None

        # Delete bot result message
        try:
            await callback_query.message.delete()
        except Exception:
            pass

        # Delete original /list command message
        if cache_key and cache_key in search_cache:
            data = search_cache.get(cache_key)
            try:
                await client.delete_messages(
                    chat_id=data["chat_id"], message_ids=data["cmd_msg_id"]
                )
            except Exception:
                pass

            # Cleanup cache
            search_cache.pop(cache_key, None)

        return

    # PAGINATION
    if len(data) < 4:
        return await callback_query.answer(
            "❌ Invalid pagination request", show_alert=True
        )

    page = int(data[2])
    cache_key = data[3]  # Full cache key with filters

    # Get cached results
    if cache_key not in search_cache:
        return await callback_query.answer(
            "❌ Search expired. Please search again.", show_alert=True
        )

    cache_data = search_cache[cache_key]

    # Create page text and buttons
    reply, total_pages = create_result_text(
        cache_data["files"], page, cache_data["query"], cache_data["filters"]
    )
    buttons = create_pagination_buttons(page, total_pages, user_id, cache_key)

    try:
        await callback_query.answer(f"📄 Page {page + 1}/{total_pages}")
    except Exception:
        pass

    # Update message
    await callback_query.message.edit_text(
        reply,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=buttons,
        disable_web_page_preview=True,
    )
