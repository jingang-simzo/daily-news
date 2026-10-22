# 每日财经政治新闻微信推送（云端版）

通过 GitHub Actions 定时抓取新闻，一边用 Server酱推送到微信，一边生成当天语音版的手机播放页，无需本机开机。

## 手机听新闻

每天运行完成后会自动生成：

- `docs/data/latest.json`：当天简报文字
- `docs/audio/YYYY-MM-DD.mp3`：当天语音（微软 Edge 中文神经网络音色）
- `docs/data/archive/`：历史简报

把 `docs` 目录用 GitHub Pages 发布后，手机上打开站点并「添加到主屏幕」，就得到一个类似 App 的播放器：

- 点一次大按钮即可朗读，支持进度条与 1.0 / 1.25 / 1.5 倍速
- 支持锁屏、蓝牙和车机上的播放/暂停控制
- 自动记住上次听到的位置
- 某天没有生成音频时，自动改用手机自带的语音朗读

开启方式：`Settings -> Pages -> Build and deployment -> Deploy from a branch`，分支选 `main`，目录选 `/docs`。

## 配置

1. 在 GitHub 新建仓库并上传本目录内容。
2. 进入 `Settings -> Secrets and variables -> Actions`，新增 secret：
   - `SERVERCHAN_SENDKEY`：值为你的 Server酱 SendKey，例如 `SCTxxxx...`
3. 进入 `Actions` 页面启用 workflows。

## 时间

工作流使用 UTC 时间，`30 23 * * *` 表示每天 23:30 UTC，即北京时间 07:30。

## 手动测试

在 GitHub 仓库的 `Actions -> daily-news-push -> Run workflow` 点击运行即可手动触发。

本机可先本地测试抓取效果：

```bash
pip install -r requirements.txt
python news_push.py --dry-run
```
