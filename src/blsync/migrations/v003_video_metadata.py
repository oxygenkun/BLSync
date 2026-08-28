"""Create video metadata and download file tables."""

VERSION = 3

STATEMENTS = (
    """
    CREATE TABLE videos (
        id INTEGER NOT NULL PRIMARY KEY,
        bvid VARCHAR(50) NOT NULL,
        aid INTEGER,
        title VARCHAR(500) NOT NULL,
        description TEXT,
        pic VARCHAR(500),
        pubdate INTEGER,
        duration INTEGER,
        videos_count INTEGER NOT NULL DEFAULT 1,
        owner_mid INTEGER,
        owner_name VARCHAR(200),
        owner_face VARCHAR(500),
        tags TEXT,
        stat_likes INTEGER NOT NULL DEFAULT 0,
        stat_coins INTEGER NOT NULL DEFAULT 0,
        stat_favorites INTEGER NOT NULL DEFAULT 0,
        stat_shares INTEGER NOT NULL DEFAULT 0,
        stat_views INTEGER NOT NULL DEFAULT 0,
        stat_danmakus INTEGER NOT NULL DEFAULT 0,
        stat_replies INTEGER NOT NULL DEFAULT 0,
        raw_info TEXT,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX ix_videos_bvid ON videos (bvid)",
    "CREATE INDEX ix_videos_owner_mid ON videos (owner_mid)",
    """
    CREATE TABLE video_pages (
        id INTEGER NOT NULL PRIMARY KEY,
        video_id INTEGER NOT NULL,
        cid INTEGER,
        page_index INTEGER NOT NULL,
        title VARCHAR(500) NOT NULL,
        duration INTEGER,
        description TEXT,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        FOREIGN KEY (video_id) REFERENCES videos (id) ON DELETE CASCADE
    )
    """,
    "CREATE UNIQUE INDEX ix_video_pages_video_page ON video_pages (video_id, page_index)",
    """
    CREATE TABLE download_files (
        id INTEGER NOT NULL PRIMARY KEY,
        task_id INTEGER,
        video_id INTEGER NOT NULL,
        page_id INTEGER,
        file_type VARCHAR(20) NOT NULL,
        file_path TEXT NOT NULL,
        file_size INTEGER,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE SET NULL,
        FOREIGN KEY (video_id) REFERENCES videos (id) ON DELETE CASCADE,
        FOREIGN KEY (page_id) REFERENCES video_pages (id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX ix_download_files_task_id ON download_files (task_id)",
    "CREATE INDEX ix_download_files_video_id ON download_files (video_id)",
)
