"""Status maps, engine catalogue and allowlists. Pure data, no imports from WZML."""

API_VERSION = "1"
SCHEMA_VERSION = "1.0.0"

# The 16 primary WZML MirrorStatus values -> API stage.
WZML_STATUS_STAGE = {
    "Download": "downloading",
    "Upload": "uploading",
    "Clone": "cloning",
    "QueueDl": "download_queue",
    "QueueUp": "upload_queue",
    "Pause": "paused",
    "Archive": "archiving",
    "Extract": "extracting",
    "Split": "splitting",
    "CheckUp": "checking",
    "Seed": "seeding",
    "SamVid": "sample_video",
    "Convert": "converting",
    "FFmpeg": "ffmpeg_processing",
    "YouTube": "youtube_processing",
    "Metadata": "metadata_processing",
}

# Engine-native values that WZML passes through as the "status" (SABnzbd, JDownloader).
NATIVE_STATUS_STAGE = {
    "QuickCheck": "checking",
    "Verifying": "checking",
    "Repairing": "checking",
    "Extracting": "extracting",
    "Fetching": "other",
    "Moving": "other",
}

STAGES = (
    "downloading", "uploading", "cloning", "seeding", "checking", "archiving",
    "extracting", "splitting", "sample_video", "converting", "ffmpeg_processing",
    "youtube_processing", "metadata_processing", "download_queue", "upload_queue",
    "paused", "other", "unknown",
)
LIFECYCLES = (
    "queued", "running", "paused", "succeeded", "failed", "cancelled",
    "stopped", "rejected", "unknown",
)
TERMINAL_EVENTS = {
    "task.succeeded", "task.failed", "task.cancelled", "task.stopped",
    "task.rejected", "task.cleanup_completed",
}
EVENT_TYPES = (
    "task.registered", "task.queued", "task.started", "task.stage_changed",
    "task.stage_completed", "task.seeding_started", "task.succeeded",
    "task.failed", "task.cancelled", "task.stopped", "task.rejected",
    "task.cleanup_completed",
)

# Status wrapper class name -> (engine id, display label, capabilities)
_ALL = ("progress", "bytes", "speed", "eta")
ENGINE_MAP = {
    "Aria2Status": ("aria2", "Aria2", _ALL + ("torrent", "seeding", "engine_gid")),
    "QbittorrentStatus": ("qbittorrent", "qBittorrent", _ALL + ("torrent", "seeding", "engine_hash")),
    "DirectStatus": ("direct", "Direct link", _ALL),
    "GoogleDriveStatus": ("gdrive_api", "Google Drive API", _ALL + ("engine_gid",)),
    "JDownloaderStatus": ("jdownloader", "JDownloader", _ALL + ("engine_gid",)),
    "MegaDownloadStatus": ("mega", "MEGA", _ALL + ("engine_gid",)),
    "SabnzbdStatus": ("sabnzbd", "SABnzbd", _ALL + ("engine_gid",)),
    "SeedrStatus": ("seedr", "Seedr", _ALL + ("engine_gid",)),
    "RcloneStatus": ("rclone", "Rclone", _ALL + ("engine_gid",)),
    "TelegramStatus": ("telegram", "Telegram", _ALL),
    "YtDlpStatus": ("yt_dlp", "yt-dlp", _ALL),
    "YtStatus": ("youtube_api", "YouTube API", _ALL),
    "UphosterStatus": ("uphoster", "Uphoster", _ALL + ("engine_gid",)),
    "FFmpegStatus": ("ffmpeg", "FFmpeg", _ALL),
    "SevenZStatus": ("sevenz", "7-Zip", _ALL),
    "MetadataStatus": ("metadata", "Metadata", _ALL),
    "QueueStatus": ("queue_system", "Queue system", ()),
}

INPUT_MODE = {
    "tgmedia": "telegram_media", "aria2": "direct_link", "qbit": "torrent",
    "sabnzbd": "usenet", "jdown": "jdownloader", "rclonedl": "rclone",
    "ytdlp": "yt_dlp", "gdrive": "google_drive", "mega": "mega", "seedr": "seedr",
}
OUTPUT_MODE = {
    "leech": "telegram", "gdrive": "google_drive", "rclone": "rclone",
    "clone": "clone", "uphosters": "uphoster", "uphosterupload": "uphoster",
}

# Safe allowlist of effective limits: Config attr -> (unit, scope, note)
LIMIT_KEYS = {
    "DIRECT_LIMIT": ("GB", "task", "0 = unlimited"),
    "TORRENT_LIMIT": ("GB", "task", "0 = unlimited"),
    "GD_DL_LIMIT": ("GB", "task", "0 = unlimited"),
    "RC_DL_LIMIT": ("GB", "task", "0 = unlimited"),
    "CLONE_LIMIT": ("GB", "task", "0 = unlimited"),
    "JD_LIMIT": ("GB", "task", "0 = unlimited"),
    "NZB_LIMIT": ("GB", "task", "0 = unlimited"),
    "YTDLP_LIMIT": ("GB", "task", "0 = unlimited"),
    "PLAYLIST_LIMIT": ("items", "task", "0 = unlimited"),
    "MEGA_LIMIT": ("GB", "task", "0 = unlimited"),
    "SEEDR_LIMIT": ("GB", "task", "0 = unlimited"),
    "LEECH_LIMIT": ("GB", "task", "0 = unlimited"),
    "ARCHIVE_LIMIT": ("GB", "task", "0 = unlimited"),
    "EXTRACT_LIMIT": ("GB", "task", "0 = unlimited"),
    "STORAGE_LIMIT": ("GB", "storage", "minimum free storage threshold; 0 = disabled"),
    "MONTHLY_BANDWIDTH": ("GB", "bandwidth", "0 = disabled"),
    "USER_TIME_INTERVAL": ("seconds", "user", "0 = disabled"),
    "USER_MAX_TASKS": ("tasks", "user", "0 = unlimited"),
    "BOT_MAX_TASKS": ("tasks", "bot", "0 = unlimited"),
    "QUEUE_ALL": ("tasks", "queue", "0 = unlimited"),
    "QUEUE_DOWNLOAD": ("tasks", "queue", "0 = unlimited"),
    "QUEUE_UPLOAD": ("tasks", "queue", "0 = unlimited"),
    "STATUS_LIMIT": ("tasks_per_page", "status_ui", "tasks shown per /status page"),
    "STATUS_UPDATE_INTERVAL": ("seconds", "status_ui", "WZML status refresh interval"),
}

# Python packages reported by the software section.
PACKAGES = (
    "pyrotgfork", "aiohttp", "aioaria2", "aioqbt", "yt-dlp", "fastapi", "uvicorn",
    "gunicorn", "httpx", "psutil", "pymongo", "google-api-python-client",
    "qbittorrent-api", "apscheduler",
)

# dependency id -> (BinConfig attr or None, fallback executable, display name,
#                   version argv suffix, criticality, features)
DEPENDENCIES = (
    ("aria2", "ARIA2_NAME", "aria2c", "Aria2", ("-v",), "critical",
     ("direct links", "torrents (metadata/seeding)")),
    ("qbittorrent", "QBIT_NAME", "qbittorrent-nox", "qBittorrent", ("--version",),
     "required_unless_torrents_disabled", ("torrents",)),
    ("ffmpeg", "FFMPEG_NAME", "ffmpeg", "FFmpeg", ("-version",), "important",
     ("convert", "sample video", "ffmpeg cmds", "metadata")),
    ("rclone", "RCLONE_NAME", "rclone", "Rclone", ("version",), "important",
     ("rclone upload/download", "clone")),
    ("sabnzbd", "SABNZBD_NAME", "sabnzbdplus", "SABnzbd", ("--version",), "optional",
     ("usenet",)),
    ("ffprobe", None, "ffprobe", "ffprobe", ("-version",), "important",
     ("media info", "split")),
    ("sevenz", None, "7z", "7-Zip", ("i",), "important", ("archive", "extract")),
    ("split", None, "split", "split", ("--version",), "optional", ("file splitting",)),
    ("java", None, "java", "Java runtime", ("-version",), "optional", ("jdownloader",)),
    ("cpulimit", None, "cpulimit", "cpulimit", ("--version",), "optional",
     ("sabnzbd cpu limiting",)),
    ("taskset", None, "taskset", "taskset", ("--version",), "optional", ("cpu pinning",)),
    ("megacmd", None, "mega-version", "MEGAcmd", ("",), "optional", ("mega",)),
)

SECRET_FRAGMENTS = (
    "token", "secret", "password", "pass", "key", "cookie", "session", "auth",
    "credential", "api_hash", "hash",
)
