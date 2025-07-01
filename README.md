# BLSync

[BLSync](https://github.com/oxygenkun/BLSync) 是一个 Bilibili 收藏夹同步工具。

> 疯狂完善功能中……

# 功能

- [x] 支持收藏夹同步
- [x] 支持外部 API 下载请求
- [ ] 支持稍后观看同步
- [ ] 支持 UP 主视频同步
- [ ] 支持 UP 主动态图片、动态文字同步
- [ ] 支持个人动态同步
- [ ] 支持 外部下载工具
- [ ] 支持 WebUI

# 使用

## Docker Compose 运行（推荐）

1. 创建目录结构

```bash
mkdir blsync
cd blsync
mkdir config sync
```

2. 创建 `compose.yaml` 文件

```yaml
services:
  blsync:
    image: oxygenkun1/blsync:latest
    container_name: blsync
    ports:
      - "8000:8000"
    volumes:
      - ./config:/app/config
      - ./sync:/app/sync
    environment:
      - TZ=Asia/Shanghai
    restart: unless-stopped
```

3. 创建配置文件 `./config/config.toml`（参考[配置文件](#配置文件)章节）

4. 启动服务

```bash
# 启动服务（后台运行）
docker compose up -d

# 查看服务状态
docker compose ps

# 查看日志
docker compose logs -f

# 停止服务
docker compose down
```

### 目录说明

- `/app/config` ：[配置文件](#配置文件)所在目录，存储配置文件 `config.toml`；程序默认数据库存储位置
- `/app/sync` ：默认收藏夹视频存储位置

## 源码运行

1. 安装 `uv` 包管理器

2. 安装 `ffmpeg`, `yutto`

3. 使用 `uv` 运行

```bash
uv sync
uv run bs -c config/config.toml
```

# 配置文件

默认读取 `./config/config.toml` （参考模板文件 [`./config/config.template.toml`](./config/config.template.toml) 中的说明）。

## 收藏夹 id 获取方法

![image](https://github.com/user-attachments/assets/02efefe9-0a3a-46d6-8646-a6aa462d62c2)

浏览器可以看到 `fid=xxxx`，只需要后面数字即可

![image](https://github.com/user-attachments/assets/76c298d7-6437-4e12-8333-a80f4802b8d1)

# 特别感谢

该项目实现过程中主要参考借鉴了如下的项目，感谢他们的贡献：

- [bili-sync](https://github.com/amtoaer/bili-sync) 项目功能和配置文件参考
- [bili-sync-yt-dlp](https://github.com/cap153/bili-sync-yt-dlp) 基础代码逻辑参考
- [bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect) B 站的第三方接口文档
- [bilibili-api](https://github.com/Nemo2011/bilibili-api) 使用 Python 调用接口的参考实现
- [yutto](https://github.com/yutto-dev/yutto) 使用 yutto 下载视频
