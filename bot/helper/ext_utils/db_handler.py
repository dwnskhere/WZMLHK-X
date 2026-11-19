from importlib import import_module

from aiofiles import open as aiopen
from aiofiles.os import path as aiopath
from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError
from pymongo.server_api import ServerApi

from ... import LOGGER, qbit_options, rss_dict, user_data
from ...core.config_manager import Config
from ...core.tg_client import TgClient


class DbManager:
    def __init__(self):
        self._return = True
        self._conn = None
        self.db = None

    async def connect(self):
        try:
            if self._conn is not None:
                await self._conn.close()
            self._conn = AsyncMongoClient(
                Config.DATABASE_URL, server_api=ServerApi("1")
            )
            self.db = self._conn.wzmlx
            self._return = False
        except PyMongoError as e:
            LOGGER.error(f"Error in DB connection: {e}")
            self.db = None
            self._return = True
            self._conn = None

    async def disconnect(self):
        self._return = True
        if self._conn is not None:
            await self._conn.close()
        self._conn = None

    async def update_deploy_config(self):
        if self._return:
            return
        settings = import_module("config")
        config_file = {
            key: value.strip() if isinstance(value, str) else value
            for key, value in vars(settings).items()
            if not key.startswith("__")
        }
        await self.db.settings.deployConfig.replace_one(
            {"_id": TgClient.ID}, config_file, upsert=True
        )

    async def update_config(self, dict_):
        if self._return:
            return
        await self.db.settings.config.update_one(
            {"_id": TgClient.ID}, {"$set": dict_}, upsert=True
        )

    async def update_aria2(self, key, value):
        if self._return:
            return
        await self.db.settings.aria2c.update_one(
            {"_id": TgClient.ID}, {"$set": {key: value}}, upsert=True
        )

    async def update_qbittorrent(self, key, value):
        if self._return:
            return
        await self.db.settings.qbittorrent.update_one(
            {"_id": TgClient.ID}, {"$set": {key: value}}, upsert=True
        )

    async def save_qbit_settings(self):
        if self._return:
            return
        await self.db.settings.qbittorrent.update_one(
            {"_id": TgClient.ID}, {"$set": qbit_options}, upsert=True
        )

    async def update_private_file(self, path):
        if self._return:
            return
        db_path = path.replace(".", "__")
        if await aiopath.exists(path):
            async with aiopen(path, "rb+") as pf:
                pf_bin = await pf.read()
            await self.db.settings.files.update_one(
                {"_id": TgClient.ID}, {"$set": {db_path: pf_bin}}, upsert=True
            )
            if path == "config.py":
                await self.update_deploy_config()
        else:
            await self.db.settings.files.update_one(
                {"_id": TgClient.ID}, {"$unset": {db_path: ""}}, upsert=True
            )

    async def update_nzb_config(self):
        if self._return:
            return
        async with aiopen("sabnzbd/SABnzbd.ini", "rb+") as pf:
            nzb_conf = await pf.read()
        await self.db.settings.nzb.replace_one(
            {"_id": TgClient.ID}, {"SABnzbd__ini": nzb_conf}, upsert=True
        )

    async def update_user_data(self, user_id):
        if self._return:
            return
        data = user_data.get(user_id, {})
        data = data.copy()
        for key in ("THUMBNAIL", "RCLONE_CONFIG", "TOKEN_PICKLE", "USER_COOKIE_FILE"):
            data.pop(key, None)
        pipeline = [
            {
                "$replaceRoot": {
                    "newRoot": {
                        "$mergeObjects": [
                            data,
                            {
                                "$arrayToObject": {
                                    "$filter": {
                                        "input": {"$objectToArray": "$$ROOT"},
                                        "as": "field",
                                        "cond": {
                                            "$in": [
                                                "$$field.k",
                                                [
                                                    "THUMBNAIL",
                                                    "RCLONE_CONFIG",
                                                    "TOKEN_PICKLE",
                                                    "USER_COOKIE_FILE",
                                                ],
                                            ]
                                        },
                                    }
                                }
                            },
                        ]
                    }
                }
            }
        ]
        await self.db.users[TgClient.ID].update_one(
            {"_id": user_id}, pipeline, upsert=True
        )

    async def update_user_doc(self, user_id, key, path=""):
        if self._return:
            return
        if path:
            async with aiopen(path, "rb+") as doc:
                doc_bin = await doc.read()
            await self.db.users[TgClient.ID].update_one(
                {"_id": user_id}, {"$set": {key: doc_bin}}, upsert=True
            )
        else:
            await self.db.users[TgClient.ID].update_one(
                {"_id": user_id}, {"$unset": {key: ""}}, upsert=True
            )

    async def rss_update_all(self):
        if self._return:
            return
        for user_id in list(rss_dict.keys()):
            await self.db.rss[TgClient.ID].replace_one(
                {"_id": user_id}, rss_dict[user_id], upsert=True
            )

    async def rss_update(self, user_id):
        if self._return:
            return
        await self.db.rss[TgClient.ID].replace_one(
            {"_id": user_id}, rss_dict[user_id], upsert=True
        )

    async def rss_delete(self, user_id):
        if self._return:
            return
        await self.db.rss[TgClient.ID].delete_one({"_id": user_id})

    async def add_incomplete_task(self, cid, link, tag, msg_id=None):
        if self._return:
            return
        doc = {"_id": link, "cid": cid, "tag": tag}
        if msg_id is not None:
            doc["msg_id"] = msg_id
        await self.db.tasks[TgClient.ID].replace_one({"_id": link}, doc, upsert=True)

    async def get_pm_uids(self):
        if self._return:
            return
        return [doc["_id"] async for doc in self.db.pm_users[TgClient.ID].find({})]

    async def set_pm_users(self, user_id):
        if self._return:
            return
        if not bool(await self.db.pm_users[TgClient.ID].find_one({"_id": user_id})):
            await self.db.pm_users[TgClient.ID].insert_one({"_id": user_id})
            LOGGER.info(f"New PM User Added : {user_id}")

    async def rm_pm_user(self, user_id):
        if self._return:
            return
        await self.db.pm_users[TgClient.ID].delete_one({"_id": user_id})

    async def rm_complete_task(self, link):
        if self._return:
            return
        await self.db.tasks[TgClient.ID].delete_one({"_id": link})

    async def get_incomplete_tasks(self):
        notifier_dict = {}
        task_docs = []
        if self._return:
            return notifier_dict, task_docs
        if await self.db.tasks[TgClient.ID].find_one():
            rows = self.db.tasks[TgClient.ID].find({})
            async for row in rows:
                task_docs.append(row)
                if row["cid"] in list(notifier_dict.keys()):
                    if row["tag"] in list(notifier_dict[row["cid"]]):
                        notifier_dict[row["cid"]][row["tag"]].append(row["_id"])
                    else:
                        notifier_dict[row["cid"]][row["tag"]] = [row["_id"]]
                else:
                    notifier_dict[row["cid"]] = {row["tag"]: [row["_id"]]}
        await self.db.tasks[TgClient.ID].drop()
        return notifier_dict, task_docs

    async def update_plugins(self, plugins_dict):
        if self._return:
            return
        await self.db.plugins[TgClient.ID].replace_one(
            {"_id": "plugins_state"},
            {"_id": "plugins_state", "data": plugins_dict},
            upsert=True,
        )

    async def get_plugins(self):
        if self._return:
            return {}
        doc = await self.db.plugins[TgClient.ID].find_one({"_id": "plugins_state"})
        return doc.get("data", {}) if doc else {}

    async def trunc_table(self, name):
        if self._return:
            return
        await self.db[name][TgClient.ID].drop()

    async def get_bandwidth(self):
        if self._return:
            return 0
        doc = await self.db.bandwidth[TgClient.ID].find_one({"_id": "total_bw"})
        return doc.get("bytes", 0) if doc else 0

    async def update_bandwidth(self, bytes_count):
        if self._return:
            return
        await self.db.bandwidth[TgClient.ID].update_one(
            {"_id": "total_bw"}, {"$set": {"bytes": bytes_count}}, upsert=True
        )

    async def get_stremio_items(self, limit=200):
        if not Config.STREMIO_DB_URL:
            return []
        urls = [
            u.strip()
            for u in str(Config.STREMIO_DB_URL).replace(",", " ").split()
            if u.strip()
        ]
        if not urls:
            return []

        all_items = []
        for url in urls:
            try:
                client = AsyncMongoClient(url, server_api=ServerApi("1"))
                try:
                    default_db = client.get_default_database()
                    dbs_to_scan = [default_db]
                except Exception:
                    db_names = await client.list_database_names()
                    target_names = [
                        d for d in db_names if d not in ("admin", "local", "config")
                    ]
                    dbs_to_scan = [client[d] for d in target_names]

                for db in dbs_to_scan:
                    collections = await db.list_collection_names()
                    for coll in collections:
                        if coll in ("admin", "local", "config"):
                            continue
                        find_cursor = db[coll].find()
                        if limit:
                            find_cursor = find_cursor.limit(limit)
                        async for doc in find_cursor:
                            doc["collection"] = coll
                            all_items.append(doc)
                await client.close()
            except Exception as e:
                LOGGER.error(
                    f"Failed to fetch Stremio items from MongoDB ({url[:20]}...): {e}"
                )

        return all_items[:limit] if limit else all_items


database = DbManager()
