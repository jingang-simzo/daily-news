import os
import sys
import time
import re
import html
from datetime import datetime, timezone

import feedparser
import requests

SENDKEY = os.environ.get("SERVERCHAN_SENDKEY", "").strip()
DRY_RUN = "--dry-run" in sys.argv

FEEDS = [
    ("https://news.google.com/rss/search?q=%E8%B4%A2%E7%BB%8F+%E4%B8%AD%E5%9B%BD&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "国内", 3),
    ("https://news.google.com/rss/search?q=%E4%B8%AD%E5%9B%BD+%E6%94%BF%E6%B2%BB+%E6%94%BF%E7%AD%96&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "国内", 3),
    ("https://news.google.com/rss/search?q=Fed+OR+ECB+OR+central+bank+interest+rate&hl=en-US&gl=US&ceid=US:en", "国际", 3),
    ("https://news.google.com/rss/search?q=geopolitics+OR+markets+OR+economy&hl=en-US&gl=US&ceid=US:en", "国际", 2),
    ("http://feeds.bbci.co.uk/news/rss.xml", "国际", 1),
    ("https://www.cnbc.com/id/100003114/device/rss/rss.html", "国际", 1),
]

MAX_ITEMS = 10
MAX_AGE_SECONDS = 72 * 3600


def clean(value):
    if not value:
        return ""
    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def parse_time(entry):
    for field in ("published_parsed", "updated_parsed"):
        parsed = entry.get(field)
        if parsed:
            return datetime(*parsed[:6], tzinfo=timezone.utc)
    return None


def fetch_items():
    items = {}
    for url, category, weight in FEEDS:
        try:
            parsed = feedparser.parse(url)
        except Exception as exc:
            print(f"[feed error] {url}: {exc}", file=sys.stderr)
            continue
        if not parsed.entries and getattr(parsed, "bozo", False):
            print(f"[feed error] {url}: {parsed.bozo_exception}", file=sys.stderr)
            continue
        for entry in parsed.entries[:15]:
            title = clean(entry.get("title"))
            if not title or len(title) < 4:
                continue
            published = parse_time(entry)
            if published:
                age = max(0.0, time.time() - published.timestamp())
                if age > MAX_AGE_SECONDS:
                    continue
            source = ""
            source_obj = entry.get("source")
            if isinstance(source_obj, dict):
                source = clean(source_obj.get("title") or source_obj.get("value") or "")
            summary = clean(entry.get("summary") or entry.get("description"))
            if len(summary) > 180:
                summary = summary[:180].rstrip() + "…"
            link = entry.get("link", "")
            key = title.lower()
            existing = items.get(key)
            if existing is None or weight > existing["weight"]:
                items[key] = {
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "category": category,
                    "source": source,
                    "published": published,
                    "weight": weight,
                }
    return list(items.values())


def sort_key(item):
    if item["published"]:
        age = max(0.0, time.time() - item["published"].timestamp())
    else:
        age = 999999.0
    return (-item["weight"], age)


def format_markdown(items):
    lines = ["## 每日财经政治新闻精选", ""]
    for i, item in enumerate(items, 1):
        cat = item["category"]
        src = item["source"] or "综合"
        summary = item["summary"] or "暂无摘要"
        link = item["link"] or ""
        lines.append(f"{i}. **{item['title']}**（{cat}）")
        lines.append(f"   来源：{src}｜{summary}")
        if link:
            lines.append(f"   [阅读原文]({link})")
        lines.append("")
    return "\n".join(lines).strip()


def send(title, desp):
    url = f"https://sctapi.ftqq.com/{SENDKEY}.send"
    resp = requests.post(url, data={"title": title, "desp": desp}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def main():
    items = fetch_items()
    if not items:
        print("no items fetched", file=sys.stderr)
        return 1
    items.sort(key=sort_key)
    selected = items[:MAX_ITEMS]
    md = format_markdown(selected)
    print(md)
    if DRY_RUN:
        print("\n[dry-run] no message sent")
        return 0
    if not SENDKEY:
        print("SERVERCHAN_SENDKEY is not set", file=sys.stderr)
        return 1
    result = send("每日财经政治新闻精选", md)
    print(result)
    return 0 if result.get("code") == 0 else 1


if __name__ == "__main__":
    sys.exit(main())