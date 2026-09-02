# 每日财经政治新闻微信推送（云端版）

通过 GitHub Actions 定时抓取新闻并通过 Server酱推送到微信，无需本机开机。

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