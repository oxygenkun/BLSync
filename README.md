# BLSync

[BLSync](https://github.com/oxygenkun/BLSync) 是一个 Bilibili 收藏夹同步工具。

> 疯狂完善功能中……

## 功能

- [x] 支持收藏夹同步
- [x] 支持外部 API 下载请求
- [ ] 支持稍后观看同步
- [ ] 支持 UP 主视频同步
- [ ] 支持 UP 主动态图片、动态文字同步
- [ ] 支持个人动态同步
- [ ] 支持 外部下载工具
- [ ] 支持 WebUI

# 使用

## docker-compose 运行

`compsoe.yaml` 模板

```yaml
services:
  app:
    image: oxygenkun1/blsync:latest
    ports:
      - "8000:8000"
    volumes:
      - ./config:/app/config
      - ./sync:/app/sync
```
- `/app/config` ：[配置文件](./README.md#配置文件)所在目录，存储配置文件 `config.toml`
- `/app/sync` ：默认存储位置


## 源码运行

1. 安装 `rye` 包管理器

2. 安装 `ffmpeg`, `yutto`

3. 使用 `rye` 运行

  ```bash
  rye sync
  rye run bs -c config/config.toml
  ```

# 配置文件

当前版本的默认示例文件 `./config/config.toml` 如下：

```toml
interval = 1200
request_timeout = 300
data_path = "config/"

[credential]
sessdata = ""
bili_jct = ""
buvid3 = ""
dedeuserid = ""
ac_time_value = ""

[favorite_list]
-1="sync/"
<收藏夹id> = "<保存的路径>"
```

- `interval` ：表示程序每次执行扫描下载的间隔时间，单位为秒。
- `request_timeout` ：表示程序获取b站信息请求超时时间。一般不需要更改。
- `data_path` ：程序运行数据保存的 sqlite 文件保存地址，避免重复下载。
- **`credential`** ：哔哩哔哩账号的身份凭据，请参考凭据获取[流程获取](https://nemo2011.github.io/bilibili-api/#/get-credential)
  - `sessdata`,`bili_jct`,`buvid3`,`dedeuserid` ：cookies 存储
  - `ac_time_value` ：LocalStorage 存储
- `favorite_list` ：你想要下载的收藏夹fid与想要保存的位置。简单示例：

  ```bash
  3115878158 = "~/bili-sync/"
  ```

## 收藏夹 id 获取方法

![image](https://github.com/user-attachments/assets/02efefe9-0a3a-46d6-8646-a6aa462d62c2)

浏览器可以看到 `fid=xxxx`，只需要后面数字即可

![image](https://github.com/user-attachments/assets/270c7f2f-b1b1-49a1-a450-a133f0d459fa)


# 参考与借鉴

该项目实现过程中主要参考借鉴了如下的项目，感谢他们的贡献：

- [bili-sync](https://github.com/amtoaer/bili-sync) 项目功能和配置文件参考
- [bili-sync-yt-dlp](https://github.com/cap153/bili-sync-yt-dlp) 基础代码逻辑
- [bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect) B 站的第三方接口文档
- [bilibili-api](https://github.com/Nemo2011/bilibili-api) 使用 Python 调用接口的参考实现