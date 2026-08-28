# 视频元信息与下载文件存储设计

> 本文档记录 BLSync 数据库对视频元信息（含分 P）与下载实体文件位置的存储设计，已获确认并按此实现。

## 背景

原有设计中，视频元信息只在使用时从 Bilibili API 即时获取，不落库；下载文件位置仅保存在
`tasks.task_data` JSON 的 `downloaded_files` 字段中。为支持后续基于元信息的查询与管理，
并让服务能够根据数据库记录直接定位、传输最终下载实体文件，新增三张表：
`videos`、`video_pages`、`download_files`。

## 表结构

### videos —— 视频主信息（1 条 / BVID）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增主键 |
| `bvid` | VARCHAR(50) | B 站 BV 号，唯一索引 `ix_videos_bvid` |
| `aid` | INTEGER | AV 号 |
| `title` | VARCHAR(500) | 视频标题 |
| `description` | TEXT | 视频简介 |
| `pic` | VARCHAR(500) | 封面 URL |
| `pubdate` | INTEGER | 投稿时间戳 |
| `duration` | INTEGER | 总时长（秒）|
| `videos_count` | INTEGER | 分 P 数量，默认 1 |
| `owner_mid` | INTEGER | UP 主 uid，索引 `ix_videos_owner_mid` |
| `owner_name` | VARCHAR(200) | UP 主名称 |
| `owner_face` | VARCHAR(500) | UP 主头像 URL |
| `tags` | TEXT (JSON) | 标签名数组，如 `["教程", "编程"]` |
| `stat_likes` / `stat_coins` / `stat_favorites` / `stat_shares` / `stat_views` / `stat_danmakus` / `stat_replies` | INTEGER | 点赞、硬币、收藏、转发、播放、弹幕、回复数 |
| `raw_info` | TEXT | `bilibili_api` 返回的原始 JSON，便于后续扩展 |
| `created_at` / `updated_at` | DATETIME | 创建 / 更新时间 |

### video_pages —— 分 P 信息（N 条 / video）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增主键 |
| `video_id` | INTEGER FK → `videos(id)` ON DELETE CASCADE | 所属视频 |
| `cid` | INTEGER | 分 P cid |
| `page_index` | INTEGER | 分 P 序号（从 1 开始）|
| `title` | VARCHAR(500) | 分 P 标题 |
| `duration` | INTEGER | 分 P 时长（秒）|
| `description` | TEXT | 分 P 简介 |
| `created_at` / `updated_at` | DATETIME | 创建 / 更新时间 |

约束：`UNIQUE(video_id, page_index)`（唯一索引 `ix_video_pages_video_page`）。

### download_files —— 下载实体文件位置

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增主键 |
| `task_id` | INTEGER FK → `tasks(id)` ON DELETE SET NULL，nullable | 产生该文件的下载任务 |
| `video_id` | INTEGER FK → `videos(id)` ON DELETE CASCADE | 所属视频 |
| `page_id` | INTEGER FK → `video_pages(id)` ON DELETE CASCADE，nullable | 所属分 P（通过 yutto 注入的分 P 元信息关联，多分 P 同样适用；关联失败时置 NULL）|
| `file_type` | VARCHAR(20) | 文件类型，见下表 |
| `file_path` | TEXT NOT NULL | **最终实体文件绝对路径**，服务据此直接传输文件 |
| `file_size` | INTEGER | 文件大小（字节）|
| `created_at` / `updated_at` | DATETIME | 创建 / 更新时间 |

索引：`ix_download_files_task_id`、`ix_download_files_video_id`。

`file_type` 取值与后缀映射：

| file_type | 后缀 |
|---|---|
| `video` | `.mp4` `.m4v` `.mkv` `.flv` `.mov` `.webm` |
| `audio` | `.aac` `.mp3` `.flac` `.m4a` |
| `cover` | `.jpg` `.jpeg` `.png` `.webp`（yutto 产物为 `{stem}-poster.jpg`）|
| `metadata` | `.nfo`（yutto `--with-metadata` 产物）|
| `subtitle` | `.ass` `.srt` `.vtt` |
| `danmaku` | `.xml` |

## ER 关系

```
tasks (1) ──< (0..*) download_files
videos (1) ──< (0..*) video_pages
videos (1) ──< (0..*) download_files
video_pages (1) ──< (0..*) download_files
```

## 数据流转

1. **下载任务执行时（consumer）**：`BiliVideoTask.execute()` 获取 `get_video_info()` 后，
   调用 `VideoDAL.upsert_video_info()` 按 `bvid` upsert `videos`，并按 `(video_id, page_index)`
   逐条 upsert `video_pages`（保留既有 `page_id`，避免级联删除文件记录）。
   标签通过 `BScraper.get_video_tags()` 尽力获取，失败不阻断下载。
2. **下载完成时**：yutto wrapper 在调用 `extract_ugc_video_data` 时向 `EpisodeData` 注入分 P 元信息
   （`_blsync_page_index` / `_blsync_page_cid` / `_blsync_page_name`），并在 `_record_yutto_process_download`
   中把每个输出文件与分 P 元信息一并记录（`downloaded_episodes`）。
   扫描 yutto 最终产物（媒体文件 + 同 stem 的 `.nfo` / `-poster.jpg` 等），按后缀分类后调用
   `VideoDAL.replace_task_files()` 写入 `download_files`（绝对路径 + 文件大小 + page_id）。
   分 P 关联优先按 `page_cid` 匹配 `video_pages.cid`，失败则回退到 `page_index`；单 P 视频兜底直接关联唯一分 P。
   同一任务重复下载时按 `task_id` 整体替换。元信息/文件记录失败仅记录警告，不影响任务状态。
3. **API 文件传输**：`/file/{task_id}` 与 `/tasks` 列表优先从 `download_files` 读取
   `file_type='video'` 的记录；若无记录（历史任务），回退到 `tasks.task_data.downloaded_files`。

## 关键决策

- **元信息在 consumer 落库而非 producer**：扫描收藏夹只能拿到 bvid 列表，
  逐个调用 `get_video_info` 代价高；consumer 下载前本就要获取视频信息，顺带落库无额外开销。
- **分 P 与文件的关联**：yutto 的 `EpisodeData` 不携带 cid，因此 wrapper 在 extractor 层
  （`extract_ugc_video_data`）向 `EpisodeData` 注入分 P 元信息（`id`/`cid`/`name`），
  `_record_yutto_process_download` 把每个输出文件与分 P 元信息绑定后随完成事件传出。
  consumer 优先按 `cid`、其次按 `page_index` 匹配 `video_pages`，封面/元数据等与媒体文件同 stem 的产物
  跟随媒体文件关联到同一分 P；匹配失败时置 NULL，保证数据不撒谎。
- **路径统一存绝对路径**：避免工作目录变化导致文件定位失败。
- **`task_data.downloaded_files` 暂时保留**：作为旧数据兼容与冗余备份，新代码以 `download_files` 表为准。
- **元信息统计数为快照**：`stat_*` 在每次下载/更新任务时刷新，非实时数据。

## 迁移

新增迁移 `src/blsync/migrations/v003_video_metadata.py`，schema 版本升级到 3。
三张表通过 `CREATE TABLE` + 索引语句创建，沿用既有迁移机制（`migrate_database`）。
