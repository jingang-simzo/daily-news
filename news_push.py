import os
import sys
import time
import re
import html
from datetime import datetime, timezone, timedelta

import feedparser
import requests

SENDKEY = os.environ.get("SERVERCHAN_SENDKEY", "").strip()
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
DRY_RUN = "--dry-run" in sys.argv

FEEDS = [
    ("https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "综合", 3),
    ("https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "财经", 3),
    ("https://news.google.com/rss/headlines/section/topic/WORLD?hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "国际", 3),
    ("https://news.google.com/rss/headlines/section/topic/NATION?hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "国内", 2),
    ("https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "科技", 2),
]

MAX_ITEMS = 20
MAX_AGE_SECONDS = 96 * 3600
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


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


def fetch_feed(url):
    last_exc = None
    for attempt in range(2):
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=25)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
            if parsed.entries:
                return parsed
            last_exc = Exception("empty feed")
        except Exception as exc:
            last_exc = exc
        time.sleep(1.5)
    raise last_exc or Exception("feed failed")


def fetch_items():
    items = {}
    for idx, (url, category, weight) in enumerate(FEEDS):
        try:
            parsed = fetch_feed(url)
        except Exception as exc:
            print(f"[feed error] {url}: {exc}", file=sys.stderr)
            continue
        for entry in parsed.entries[:20]:
            title = clean(entry.get("title"))
            source = ""
            source_obj = entry.get("source")
            if isinstance(source_obj, dict):
                source = clean(source_obj.get("title") or source_obj.get("value") or "")
            if source:
                title = re.sub(r"\s*[-–—|]\s*" + re.escape(source) + r"$", "", title).strip()
            if not title or len(title) < 4:
                continue
            published = parse_time(entry)
            if published:
                age = max(0.0, time.time() - published.timestamp())
                if age > MAX_AGE_SECONDS:
                    continue
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
        time.sleep(0.8)
    return list(items.values())


def sort_key(item):
    if item["published"]:
        age = max(0.0, time.time() - item["published"].timestamp())
    else:
        age = 999999.0
    return (-item["weight"], age)


def dedupe_titles(items, limit=80):
    seen = set()
    result = []
    for item in sorted(items, key=sort_key):
        key = item["title"].lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item["title"])
        if len(result) >= limit:
            break
    return result


def summarize(titles):
    if not DEEPSEEK_API_KEY:
        return None
    lines = "\n".join(f"{i}. {t}" for i, t in enumerate(titles, 1))
    prompt = (
        "你是资深新闻编辑，正在制作每日早报。下面是从今天新闻中抓到的标题（已去重）。\n"
        "任务：从中挑选最重要的15条左右（10到20条），覆盖尽量多样的板块（如政治、国际、军事、财经、金融、科技、房市、企业、灾害、社会、热议等），"
        "同一事件、同一家公司或同一主题只保留一条，并优先覆盖战争进展、天灾人祸、网上热议、重大政策与市场影响等公众关注热点。\n"
        "为每条分配一个最合适的板块，并写3-5句中文摘要，约120-220字，内容更详实，包含事件要点、关键数字或背景、前因后果，以及明确的结论、观点或影响；基于标题补充必要背景，但不要编造无法确定的具体数字或事实；"
        "禁止只复述标题、只写话题而没有下文。\n"
        "只输出15行左右（10到20行），每行严格使用「板块：内容」格式，不要编号，不要输出其他文字。\n\n" + lines
    )
    payload = {
        "model": "deepseek-v4-pro",
        "messages": [
            {"role": "system", "content": "你是专业的每日新闻简报编辑，文风简洁但信息密度高，注重提炼结论、背景与影响。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 5000,
    }
    try:
        resp = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=90,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()
        result = []
        for raw_line in content.splitlines():
            line = re.sub(r"^\s*\d+[、\.\)\s]*", "", raw_line).strip()
            if not line:
                continue
            m = re.match(r"^([^：:]{1,8})[：:](.+)$", line)
            if m:
                result.append({"category": m.group(1).strip(), "content": m.group(2).strip()})
        if 1 <= len(result) <= MAX_ITEMS:
            return result
    except Exception as exc:
        print(f"[summarize error] {exc}", file=sys.stderr)
    return None


def fallback(items):
    out = []
    seen = set()
    for item in sorted(items, key=sort_key):
        key = item["title"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"category": item["category"], "content": item["title"]})
        if len(out) >= MAX_ITEMS:
            break
    return out


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

    titles = dedupe_titles(items)
    final = summarize(titles)
    if not final:
        final = fallback(items)

    now = datetime.now(timezone(timedelta(hours=8)))
    date_str = f"{now.month}月{now.day}日早"
    lines = [f"**{date_str}，读报时间：**", ""]
    for i, entry in enumerate(final, 1):
        content = entry["content"]
        if len(content) > 320:
            content = content[:320].rstrip() + "…"
        lines.append(f"{i}、{entry['category']}：{content}")
    desp = "\n\n".join(lines)

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


if __name__ == "__main__":
    sys.exit(main())