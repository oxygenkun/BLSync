import sqlite3

from .configs import Config


class SQLiteManager:
    def __init__(self, db_name):
        self.db_name = db_name

    def __enter__(self):
        self.conn = sqlite3.connect(self.db_name)
        self.cursor = self.conn.cursor()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.conn.commit()
        self.conn.close()

    def insert_data(self, table_name, value):
        self.cursor.execute(
            f"""CREATE TABLE IF NOT EXISTS "{table_name}" (bvid TEXT UNIQUE)"""
        )
        self.cursor.execute(
            f'INSERT OR IGNORE INTO "{table_name}" (bvid) VALUES (?)', (value,)
        )

    def get_values(self, table_name):
        self.cursor.execute(
            f"""CREATE TABLE IF NOT EXISTS "{table_name}" (bvid TEXT UNIQUE)"""
        )
        self.cursor.execute(f'SELECT bvid FROM "{table_name}"')
        return set(row[0] for row in self.cursor.fetchall())


def already_download_bvids(media_id, configs: Config):
    """
    数据库读取已经下载的视频bvids
    """
    with SQLiteManager(configs.data_path) as db_manager:
        return db_manager.get_values(table_name=media_id)


def already_download_bvids_add(media_id, bvid, configs: Config):
    """
    数据库读取已经下载的视频bvids
    """
    with SQLiteManager(configs.data_path) as db_manager:
        db_manager.insert_data(table_name=media_id, value=bvid)
