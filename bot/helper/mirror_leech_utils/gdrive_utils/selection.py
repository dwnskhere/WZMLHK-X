from asyncio import sleep
from html import escape
from time import time
from pyrogram.enums import ButtonStyle

from .... import bot_cache
from ....helper.ext_utils.status_utils import get_readable_file_size, get_readable_time
from ....helper.telegram_helper.button_build import ButtonMaker
from ....helper.telegram_helper.message_utils import (
    send_message,
    edit_message,
    delete_message,
)


def _build_gd_selection_menu(cache_key: str, user_id: int, page: int = 1):
    cache = bot_cache[cache_key]
    items = cache["items"]  # list of dicts: {"id", "name", "size", "mimeType"}
    selected_ids = cache["selected_ids"]  # set of selected file ids
    timeout = 180
    elapsed = time() - cache["start_time"]
    remaining = max(0, int(timeout - elapsed))

    PAGE_SIZE = 10
    total_items = len(items)
    total_pages = max(1, (total_items + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))
    cache["page"] = page

    start_idx = (page - 1) * PAGE_SIZE
    end_idx = min(start_idx + PAGE_SIZE, total_items)
    page_items = items[start_idx:end_idx]

    # Calculate selected count and size
    sel_count = len(selected_ids)
    sel_size = sum(item.get("size", 0) for item in items if item["id"] in selected_ids)

    caption = (
        f"📂 <b><u>Google Drive File Selection</u></b>\n\n"
        f"📦 <b>Folder:</b> <code>{escape(cache['folder_name'])}</code>\n"
        f"📊 <b>Total Files:</b> {total_items} | <b>Selected:</b> {sel_count} ({get_readable_file_size(sel_size)})\n"
        f"📄 <b>Page:</b> {page}/{total_pages} | ⏱️ <b>Timeout:</b> {get_readable_time(remaining)}\n\n"
    )

    for idx, item in enumerate(page_items, start=start_idx + 1):
        is_sel = item["id"] in selected_ids
        mark = "✅" if is_sel else "❌"
        sz = get_readable_file_size(item.get("size", 0))
        caption += (
            f"<b>{idx}.</b> {mark} <code>{escape(item['name'])}</code> <i>({sz})</i>\n"
        )

    buttons = ButtonMaker()

    # Numeric toggle buttons for the current page (5 columns)
    for idx, item in enumerate(page_items, start=start_idx + 1):
        is_sel = item["id"] in selected_ids
        btn_text = f"{'✅' if is_sel else '❌'} {idx}"
        buttons.data_button(
            btn_text,
            f"gdsel {user_id} {cache_key} toggle {idx - 1}",
        )

    # Row 1 of footer: Navigation buttons (2 columns)
    if total_pages > 1:
        prev_btn = "⬅️ Prev" if page > 1 else " "
        prev_cb = (
            f"gdsel {user_id} {cache_key} page {page - 1}"
            if page > 1
            else f"gdsel {user_id} {cache_key} noop"
        )
        next_btn = "Next ➡️" if page < total_pages else " "
        next_cb = (
            f"gdsel {user_id} {cache_key} page {page + 1}"
            if page < total_pages
            else f"gdsel {user_id} {cache_key} noop"
        )
        buttons.data_button(prev_btn, prev_cb, position="l_body")
        buttons.data_button(next_btn, next_cb, position="l_body")

    # Row 2 of footer: Bulk toggle actions (2 columns)
    buttons.data_button(
        "Select All", f"gdsel {user_id} {cache_key} all", position="footer"
    )
    buttons.data_button(
        "Invert", f"gdsel {user_id} {cache_key} invert", position="footer"
    )

    # Row 3 of footer: Cancel & Done (2 columns)
    buttons.data_button(
        "Cancel",
        f"gdsel {user_id} {cache_key} cancel",
        position="footer",
        style=ButtonStyle.DANGER,
    )
    buttons.data_button(
        f"Done ({sel_count})",
        f"gdsel {user_id} {cache_key} done",
        position="footer",
        style=ButtonStyle.SUCCESS,
    )

    return caption, buttons.build_menu(b_cols=5, lb_cols=2, f_cols=2)


async def open_gd_selection(message, folder_name: str, file_items: list):
    """
    Prompts the user with an interactive Telegram button menu to select/deselect files
    from a Google Drive folder before downloading begins.
    Returns:
        selected_file_ids: set of file_ids to download (or None if cancelled)
    """
    user_id = message.from_user.id
    msg_id = message.id
    cache_key = f"gdsel_{msg_id}_{int(time())}"

    # Default: all files selected
    selected_ids = {item["id"] for item in file_items}

    start_time = time()
    bot_cache[cache_key] = {
        "folder_name": folder_name,
        "items": file_items,
        "selected_ids": selected_ids,
        "page": 1,
        "start_time": start_time,
        "is_done": False,
        "is_cancelled": False,
    }

    caption, menu = _build_gd_selection_menu(cache_key, user_id, page=1)
    prompt = await send_message(message, caption, menu)

    # 180 seconds timeout loop
    while time() - start_time <= 180:
        await sleep(0.5)
        if cache_key not in bot_cache:
            break
        if bot_cache[cache_key]["is_done"] or bot_cache[cache_key]["is_cancelled"]:
            break

    if cache_key not in bot_cache:
        await delete_message(prompt)
        return None

    is_cancelled = bot_cache[cache_key]["is_cancelled"]
    selected_ids = set(bot_cache[cache_key]["selected_ids"])
    del bot_cache[cache_key]

    if is_cancelled or not selected_ids:
        await edit_message(prompt, "❌ <b>Google Drive file selection cancelled.</b>")
        return None

    await delete_message(prompt)
    return selected_ids


async def gd_selection_cb(client, query):
    user_id = query.from_user.id
    data = query.data.split()
    # data format: gdsel <owner_user_id> <cache_key> <action> [extra]
    if len(data) < 4:
        return

    owner_id = int(data[1])
    cache_key = data[2]
    action = data[3]

    if cache_key not in bot_cache:
        await query.answer("This file selection session has expired!", show_alert=True)
        await delete_message(query.message)
        return

    if user_id != owner_id:
        await query.answer("This selection menu is not for you!", show_alert=True)
        return

    cache = bot_cache[cache_key]

    if action == "noop":
        await query.answer()
        return

    elif action == "cancel":
        cache["is_cancelled"] = True
        await query.answer("Cancelled")
        return

    elif action == "done":
        if not cache["selected_ids"]:
            await query.answer("Select at least 1 file to proceed!", show_alert=True)
            return
        cache["is_done"] = True
        await query.answer("Starting download with selected files...")
        return

    elif action == "page":
        new_page = int(data[4])
        caption, menu = _build_gd_selection_menu(cache_key, user_id, page=new_page)
        await query.answer()
        await edit_message(query.message, caption, menu)

    elif action == "toggle":
        item_idx = int(data[4])
        item = cache["items"][item_idx]
        file_id = item["id"]
        if file_id in cache["selected_ids"]:
            cache["selected_ids"].remove(file_id)
        else:
            cache["selected_ids"].add(file_id)
        caption, menu = _build_gd_selection_menu(cache_key, user_id, page=cache["page"])
        await query.answer()
        await edit_message(query.message, caption, menu)

    elif action == "all":
        cache["selected_ids"] = {item["id"] for item in cache["items"]}
        caption, menu = _build_gd_selection_menu(cache_key, user_id, page=cache["page"])
        await query.answer("Selected all files.")
        await edit_message(query.message, caption, menu)

    elif action == "invert":
        all_ids = {item["id"] for item in cache["items"]}
        cache["selected_ids"] = all_ids - cache["selected_ids"]
        caption, menu = _build_gd_selection_menu(cache_key, user_id, page=cache["page"])
        await query.answer("Inverted selection.")
        await edit_message(query.message, caption, menu)
