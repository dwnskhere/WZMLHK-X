# ruff: noqa: F403, F405

from pyrogram.filters import command, regex
from pyrogram.handlers import CallbackQueryHandler, EditedMessageHandler, MessageHandler
from pyrogram.types import BotCommand

from ..core.config_manager import Config
from ..helper.ext_utils.help_messages import BOT_COMMANDS
from ..helper.telegram_helper.bot_commands import BotCommands
from ..helper.telegram_helper.filters import CustomFilters
from ..modules import *
from .tg_client import TgClient


def add_handlers():
    TgClient.bot.add_handler(
        MessageHandler(
            authorize,
            filters=command(BotCommands.AuthorizeCommand, case_sensitive=True)
            & CustomFilters.sudo,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            unauthorize,
            filters=command(BotCommands.UnAuthorizeCommand, case_sensitive=True)
            & CustomFilters.sudo,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            add_sudo,
            filters=command(BotCommands.AddSudoCommand, case_sensitive=True)
            & CustomFilters.sudo,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            remove_sudo,
            filters=command(BotCommands.RmSudoCommand, case_sensitive=True)
            & CustomFilters.sudo,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            send_bot_settings,
            filters=command(BotCommands.BotSetCommand, case_sensitive=True)
            & CustomFilters.sudo,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            broadcast,
            filters=command(BotCommands.BroadcastCommand, case_sensitive=True)
            & CustomFilters.sudo,
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(
            edit_bot_settings, filters=regex("^botset") & CustomFilters.sudo
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            cancel,
            filters=regex(rf"^/{BotCommands.CancelTaskCommand[1]}?(?:_\w+).*$")
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            cancel_all_buttons,
            filters=command(BotCommands.CancelAllCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(cancel_all_update, filters=regex("^canall"))
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(cancel_multi, filters=regex("^stopm"))
    )
    TgClient.bot.add_handler(
        MessageHandler(
            clone_node,
            filters=command(BotCommands.CloneCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            aioexecute,
            filters=command(BotCommands.AExecCommand, case_sensitive=True)
            & CustomFilters.sudo,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            execute,
            filters=command(BotCommands.ExecCommand, case_sensitive=True)
            & CustomFilters.sudo,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            clear,
            filters=command(BotCommands.ClearLocalsCommand, case_sensitive=True)
            & CustomFilters.sudo,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            select,
            filters=command(BotCommands.SelectCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(confirm_selection, filters=regex("^sel"))
    )
    TgClient.bot.add_handler(
        MessageHandler(
            remove_from_queue,
            filters=command(BotCommands.ForceStartCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            count_node,
            filters=command(BotCommands.CountCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            delete_file,
            filters=command(BotCommands.DeleteCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            drive_clean,
            filters=command(BotCommands.GDCleanCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(confirm_drive_clean_cb, filters=regex("^gdccat"))
    )
    TgClient.bot.add_handler(
        MessageHandler(
            gdrive_search,
            filters=command(BotCommands.ListCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(select_type, filters=regex("^list_types"))
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(select_dest, filters=regex("^list_dest"))
    )
    TgClient.bot.add_handler(
        MessageHandler(
            rcrefreshindex_command,
            filters=command(BotCommands.RcRefreshIndexCommand, case_sensitive=True)
            & CustomFilters.sudo,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            recent_searches,
            filters=command(BotCommands.RecentCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            rclstorage_command,
            filters=command(BotCommands.RclStorageCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            rcldelete_command,
            filters=command(BotCommands.RclDeleteCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            latest_uploads,
            filters=command(BotCommands.LatestCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(handle_pagination, filters=regex("^page"))
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(confirm_delete_callback, filters=regex("^confirm_delete"))
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(cancel_delete_callback, filters=regex("^cancel"))
    )
    TgClient.bot.add_handler(CallbackQueryHandler(arg_usage, filters=regex("^help")))
    TgClient.bot.add_handler(
        MessageHandler(
            mirror,
            filters=command(BotCommands.MirrorCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            qb_mirror,
            filters=command(BotCommands.QbMirrorCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            jd_mirror,
            filters=command(BotCommands.JdMirrorCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            nzb_mirror,
            filters=command(BotCommands.NzbMirrorCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            leech,
            filters=command(BotCommands.LeechCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            stremio_leech,
            filters=command(BotCommands.SleechCommand, case_sensitive=True)
            & CustomFilters.stremio,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            stremio_list,
            filters=command(BotCommands.StremioListCommand, case_sensitive=True)
            & CustomFilters.stremio,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            stremio_duplicate,
            filters=command(BotCommands.StremioDuplicateCommand, case_sensitive=True)
            & CustomFilters.stremio,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            qb_leech,
            filters=command(BotCommands.QbLeechCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            jd_leech,
            filters=command(BotCommands.JdLeechCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            nzb_leech,
            filters=command(BotCommands.NzbLeechCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            seedr_link,
            filters=command(BotCommands.SeedrLinkCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            seedr_clean,
            filters=command(BotCommands.SeedrCleanCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(seedr_clean_cb, filters=regex("^seedrclean"))
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(seedr_del_cb, filters=regex("^seedrdel"))
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(seedrsync_cb, filters=regex("^seedrsync"))
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(seedrcancel_cb, filters=regex("^seedrcancel"))
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(confirm_dump_chat, filters=regex("^sdump"))
    )
    TgClient.bot.add_handler(
        MessageHandler(
            uphoster,
            filters=command(BotCommands.UpHosterCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            get_rss_menu,
            filters=command(BotCommands.RssCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(CallbackQueryHandler(rss_listener, filters=regex("^rss")))
    TgClient.bot.add_handler(
        MessageHandler(
            run_shell,
            filters=command(BotCommands.ShellCommand, case_sensitive=True)
            & CustomFilters.sudo,
        )
    )
    TgClient.bot.add_handler(
        EditedMessageHandler(
            run_shell,
            filters=command(BotCommands.ShellCommand, case_sensitive=True)
            & CustomFilters.owner,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            start, filters=command(BotCommands.StartCommand, case_sensitive=True)
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            login, filters=command(BotCommands.LoginCommand, case_sensitive=True)
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            log,
            filters=command(BotCommands.LogCommand, case_sensitive=True)
            & CustomFilters.sudo,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            restart_bot,
            filters=command(BotCommands.RestartCommand, case_sensitive=True)
            & CustomFilters.sudo,
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(
            confirm_restart, filters=regex("^botrestart") & CustomFilters.sudo
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            restart_sessions,
            filters=command(BotCommands.RestartSessionsCommand, case_sensitive=True)
            & CustomFilters.sudo,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            imdb_search,
            filters=command(BotCommands.IMDBCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(imdb_callback, filters=regex("^imdb"))
    )
    TgClient.bot.add_handler(
        MessageHandler(
            ping,
            filters=command(BotCommands.PingCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            bot_help,
            filters=command(BotCommands.HelpCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            mediainfo,
            filters=command(BotCommands.MediaInfoCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )

    TgClient.bot.add_handler(
        MessageHandler(
            bot_stats,
            filters=command(BotCommands.StatsCommand, case_sensitive=True)
            & CustomFilters.sudo,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            task_status,
            filters=command(BotCommands.StatusCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(status_pages, filters=regex("^status"))
    )
    TgClient.bot.add_handler(CallbackQueryHandler(stats_pages, filters=regex("^stats")))
    TgClient.bot.add_handler(CallbackQueryHandler(log_cb, filters=regex("^log")))
    TgClient.bot.add_handler(CallbackQueryHandler(start_cb, filters=regex("^start")))
    TgClient.bot.add_handler(
        MessageHandler(
            torrent_search,
            filters=command(BotCommands.SearchCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(torrent_search_update, filters=regex("^torser"))
    )
    TgClient.bot.add_handler(
        MessageHandler(
            get_users_settings,
            filters=command(BotCommands.UsersCommand, case_sensitive=True)
            & CustomFilters.sudo,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            send_user_settings,
            filters=command(BotCommands.UserSetCommand, case_sensitive=True)
            & CustomFilters.authorized_uset,
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(edit_user_settings, filters=regex("^userset"))
    )
    TgClient.bot.add_handler(
        MessageHandler(
            ytdl,
            filters=command(BotCommands.YtdlCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            ytdl_leech,
            filters=command(BotCommands.YtdlLeechCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            hydra_search,
            filters=command(BotCommands.NzbSearchCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(MessageHandler(auto_leech_listener), group=10)
    if Config.SET_COMMANDS:
        global BOT_COMMANDS

        def insert_at(d, k, v, i):
            return dict(list(d.items())[:i] + [(k, v)] + list(d.items())[i:])

        if Config.JD_EMAIL and Config.JD_PASS:
            BOT_COMMANDS = insert_at(
                BOT_COMMANDS,
                "JdMirror",
                "[link/file] Mirror to Upload Destination using JDownloader",
                2,
            )
            BOT_COMMANDS = insert_at(
                BOT_COMMANDS,
                "JdLeech",
                "[link/file] Leech files to Upload to Telegram using JDownloader",
                6,
            )

        if not Config.DISABLE_SEEDR:
            BOT_COMMANDS = insert_at(
                BOT_COMMANDS,
                "SeedrLink",
                "[magnet] Get direct Seedr HTTP download links",
                7,
            )
            BOT_COMMANDS = insert_at(
                BOT_COMMANDS,
                "SeedrClean",
                "Clean or delete active Seedr cloud downloads",
                10,
            )

        if len(Config.USENET_SERVERS) != 0:
            BOT_COMMANDS = insert_at(
                BOT_COMMANDS,
                "NzbMirror",
                "[nzb] Mirror to Upload Destination using Sabnzbd",
                2,
            )
            BOT_COMMANDS = insert_at(
                BOT_COMMANDS,
                "NzbLeech",
                "[nzb] Leech files to Upload to Telegram using Sabnzbd",
                6,
            )

        if Config.LOGIN_PASS:
            BOT_COMMANDS = insert_at(
                BOT_COMMANDS, "Login", "[password] Login to Bot", 14
            )

        if not Config.DISABLE_PLUGINS:
            BOT_COMMANDS = insert_at(
                BOT_COMMANDS,
                "Plugins",
                "[SUDO] Manage user plugins",
                len(BOT_COMMANDS),
            )

        TgClient.bot.set_bot_commands(
            [
                BotCommand(
                    cmds[0] if isinstance(cmds, list) else cmds,
                    description,
                )
                for cmd, description in BOT_COMMANDS.items()
                for cmds in [getattr(BotCommands, f"{cmd}Command", None)]
                if cmds is not None
            ]
        )
