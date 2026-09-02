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
    ("https://news.google.com/rss/search?q=%E4%B8%AD%E5%9B%BD+%E8%B4%A2%E7%BB%8F&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "国内", 3),
    ("https://news.google.com/rss/search?q=%E4%B8%AD%E5%9B%BD+%E6%94%BF%E6%B2%BB+%E6%94%BF%E7%AD%96&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "国内", 3),
    ("https://news.google.com/rss/search?q=%E5%A4%AE%E8%A1%8C+%E4%BA%BA%E6%B0%91%E5%B8%81+%E8%82%A1%E5%B8%82&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "国内", 2),
    ("https://news.google.com/rss/search?q=%E7%BE%8E%E8%81%94%E5%82%A8+%E5%88%A9%E7%8E%87+%E7%BE%8E%E5%85%83&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "国际", 3),
    ("https://news.google.com/rss/search?q=%E6%AC%A7%E6%B4%B2%E5%A4%AE%E8%A1%8C+%E6%AC%A7%E6%B4%B2%E7%BB%8F%E6%B5%8E&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "国际", 3),
    ("https://news.google.com/rss/search?q=%E5%9C%B0%E7%BC%98%E6%94%BF%E6%B2%BB+%E5%9B%BD%E9%99%85%E8%B4%B8%E6%98%93+%E5%8E%9F%E6%B2%B9&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "国际", 2),
]

MAX_ITEMS = 10
DOMESTIC_COUNT = 6
INTERNATIONAL_COUNT = 4
MAX_AGE_SECONDS = 72 * 3600


def clean(value):
    if not value:
        return ""
    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def is_mostly_english(text):
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    ascii_count = sum(1 for ch in letters if ch.isascii())
    return ascii_count / len(letters) >= 0.5


def translate(text):
    if not text or not is_mostly_english(text):
        return text
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        resp = requests.get(url, params={"client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t", "q": text}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        translated = "".join(seg[0] for seg in data[0] if seg and seg[0])
        return translated if translated else text
    except Exception:
        return text


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
            title = translate(title)
            summary = translate(summary)
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


def pick(items, category, count):
    chosen = [x for x in items if x["category"] == category]
    chosen.sort(key=sort_key)
    return chosen[:count]


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
    selected = pick(items, "国内", DOMESTIC_COUNT) + pick(items, "国际", INTERNATIONAL_COUNT)
    if len(selected) < MAX_ITEMS:
        used = {x["title"].lower() for x in selected}
        rest = [x for x in items if x["title"].lower() not in used]
        rest.sort(key=sort_key)
        selected += rest[:MAX_ITEMS - len(selected)]
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