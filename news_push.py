import os
import sys
import time
import re
import html
from datetime import datetime, timezone, timedelta

import feedparser
import requests

SENDKEY = os.environ.get("SERVERCHAN_SENDKEY", "").strip()
DRY_RUN = "--dry-run" in sys.argv

FEEDS = [
    ("https://news.google.com/rss/search?q=%E4%B8%AD%E5%9B%BD+%E6%94%BF%E6%B2%BB+%E6%94%BF%E7%AD%96&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "政治", 3),
    ("https://news.google.com/rss/search?q=%E5%9B%BD%E9%99%85+%E5%9C%B0%E7%BC%98%E6%94%BF%E6%B2%BB+%E5%A4%96%E4%BA%A4&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "国际", 3),
    ("https://news.google.com/rss/search?q=%E4%B8%AD%E5%9B%BD+%E8%B4%A2%E7%BB%8F+%E5%AE%8F%E8%A7%82&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "财经", 3),
    ("https://news.google.com/rss/search?q=%E7%8E%AF%E7%90%83+%E5%B8%82%E5%9C%BA+%E8%82%A1%E5%B8%82&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "金融", 2),
    ("https://news.google.com/rss/search?q=%E7%A7%91%E6%8A%80+AI+%E8%8A%AF%E7%89%87&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "科技", 2),
    ("https://news.google.com/rss/search?q=%E6%A5%BC%E5%B8%82+%E6%88%BF%E8%B4%B7+%E6%88%BF%E5%9C%B0%E4%BA%A7&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "房市", 2),
    ("https://news.google.com/rss/search?q=%E4%B8%AD%E5%9B%BD+%E4%BC%81%E4%B8%9A+%E5%88%B6%E9%80%A0%E4%B8%9A&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "企业", 2),
    ("https://news.google.com/rss/search?q=%E6%B0%94%E5%80%99+%E5%A4%A9%E6%B0%94+%E7%81%BE%E5%AE%B3&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "气候", 2),
    ("https://news.google.com/rss/search?q=%E7%A4%BE%E4%BC%9A+%E6%B0%91%E7%94%9F+%E7%83%AD%E7%82%B9&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "社会", 2),
    ("https://news.google.com/rss/search?q=%E5%9B%BD%E9%99%85%E8%B4%B8%E6%98%93+%E5%85%B3%E7%A8%8E+%E5%8E%9F%E6%B2%B9&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "贸易", 2),
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
    for idx, (url, category, weight) in enumerate(FEEDS):
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
            title = translate(title)
            key = title.lower()
            existing = items.get(key)
            if existing is None or weight > existing["weight"]:
                items[key] = {
                    "title": title,
                    "category": category,
                    "published": published,
                    "weight": weight,
                    "idx": idx,
                }
    return list(items.values())


def sort_key(item):
    if item["published"]:
        age = max(0.0, time.time() - item["published"].timestamp())
    else:
        age = 999999.0
    return (-item["weight"], age)


def main():
    items = fetch_items()
    if not items:
        print("no items fetched", file=sys.stderr)
        return 1

    best = {}
    for item in items:
        cat = item["category"]
        if cat not in best or sort_key(item) < sort_key(best[cat]):
            best[cat] = item
    selected = sorted(best.values(), key=lambda x: x["idx"])

    if len(selected) < MAX_ITEMS:
        used = {x["title"].lower() for x in selected}
        rest = [x for x in items if x["title"].lower() not in used]
        rest.sort(key=sort_key)
        selected += rest[:MAX_ITEMS - len(selected)]
    selected = selected[:MAX_ITEMS]

    now = datetime.now(timezone(timedelta(hours=8)))
    date_str = f"{now.month}月{now.day}日早"
    lines = []
    for i, item in enumerate(selected, 1):
        content = item["title"]
        if len(content) > 150:
            content = content[:150].rstrip() + "…"
        lines.append(f"{i}、{item['category']}：{content}")
    desp = "\n".join(lines)

    print(desp)
    if DRY_RUN:
        print("\n[dry-run] no message sent")
        return 0
    if not SENDKEY:
        print("SERVERCHAN_SENDKEY is not set", file=sys.stderr)
        return 1

    result = send(f"{date_str}，读报时间", desp)
    print(result)
    return 0 if result.get("code") == 0 else 1


def send(title, desp):
    url = f"https://sctapi.ftqq.com/{SENDKEY}.send"
    resp = requests.post(url, data={"title": title, "desp": desp}, timeout=30)
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    sys.exit(main())