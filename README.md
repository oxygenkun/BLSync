# BILI-SYNC-PY

> Forked from [cap153/bili-sync-yt-dlp](https://github.com/cap153/bili-sync-yt-dlp) with much unmergeable changes.

## 功能

- [x] 支持收藏夹同步
- [ ] 支持外部 API 下载请求
- [ ] 支持稍后观看同步
- [ ] 支持 UP 主视频同步
- [ ] 支持 UP 主动态图片、动态文字同步
- [ ] 支持个人动态同步
- [ ] 支持 外部下载工具
- [ ] 支持 WebUI

# 使用

## 源码运行

1. 安装 `rye`

2. 安装 `yutto`

3. 使用 `rye` 运行

  ```bash
  rye sync
  rye run bs
  ```

# 配置文件

当前版本的默认示例文件 `config.toml` 如下：

```toml
interval = 1200
request_timeout = 300

[credential]
sessdata = ""
bili_jct = ""
buvid3 = ""
dedeuserid = ""
ac_time_value = ""

[favorite_list]
<收藏夹id> = "<保存的路径>"
<收藏夹id> = "<保存的路径>"
```

- `interval` ：表示程序每次执行扫描下载的间隔时间，单位为秒。
- `request_timeout` ：表示程序获取b站信息请求超时时间。一般不需要更改。
- `credential` ：哔哩哔哩账号的身份凭据，请参考凭据获取[流程获取](https://nemo2011.github.io/bilibili-api/#/get-credential)并对应填写至配置文件中，后续 bili-sync 会在必要时自动刷新身份凭据，不再需要手动管理。推荐使用匿名窗口获取，避免潜在的冲突。
  - `sessdata`,`bili_jct`,`buvid3`,`dedeuserid` ：cookies 存储
  - `ac_time_value` ：LocalStorage 存储
- `favorite_list` ：你想要下载的收藏夹与想要保存的位置。简单示例：

  ```bash
  3115878158 = "~/bili-sync/"
  ```

## 收藏夹 id 获取方法

![image](https://github.com/user-attachments/assets/02efefe9-0a3a-46d6-8646-a6aa462d62c2)

浏览器可以看到 `fid=xxxx`，只需要后面数字即可

![image](https://github.com/user-attachments/assets/270c7f2f-b1b1-49a1-a450-a133f0d459fa)


# Q&A

## Why not `bili-sync` ?

尝试使用但是下载报错了。想要显示LOG，提示使用 RUST 自带的 LOG 参数但是我不会 Rust。

## Why not `bili-sync-yt-dlp`?

想要扩展更多功能。

# 参考与借鉴

该项目实现过程中主要参考借鉴了如下的项目，感谢他们的贡献：

- [bili-sync](https://github.com/amtoaer/bili-sync) 项目功能和配置文件参考
- [bili-sync-yt-dlp](https://github.com/cap153/bili-sync-yt-dlp) 基础代码逻辑
- [bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect) B 站的第三方接口文档
- [bilibili-api](https://github.com/Nemo2011/bilibili-api) 使用 Python 调用接口的参考实现