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
    ("https://news.google.com/rss/search?q=%E4%B8%AD%E5%9B%BD+%E6%94%BF%E6%B2%BB+%E6%94%BF%E7%AD%96&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "政治", 3),
    ("https://news.google.com/rss/search?q=%E5%9B%BD%E9%99%85+%E5%9C%B0%E7%BC%98%E6%94%BF%E6%B2%BB+%E5%A4%96%E4%BA%A4&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "国际", 3),
    ("https://news.google.com/rss/search?q=%E4%B8%AD%E5%9B%BD+%E8%B4%A2%E7%BB%8F+%E5%AE%8F%E8%A7%82&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "财经", 3),
    ("https://news.google.com/rss/search?q=%E5%A4%AE%E8%A1%8C+%E8%B4%A7%E5%B8%81%E6%94%BF%E7%AD%96+%E5%88%A9%E7%8E%87&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "金融", 3),
    ("https://news.google.com/rss/search?q=%E7%A7%91%E6%8A%80+AI+%E8%8A%AF%E7%89%87&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "科技", 2),
    ("https://news.google.com/rss/search?q=%E6%A5%BC%E5%B8%82+%E6%88%BF%E8%B4%B7+%E6%88%BF%E5%9C%B0%E4%BA%A7&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "房市", 2),
    ("https://news.google.com/rss/search?q=%E4%B8%AD%E5%9B%BD+%E4%BC%81%E4%B8%9A+%E5%88%B6%E9%80%A0%E4%B8%9A&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "企业", 2),
    ("https://news.google.com/rss/search?q=%E6%B0%94%E5%80%99+%E5%A4%A9%E6%B0%94+%E7%81%BE%E5%AE%B3&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "气候", 2),
    ("https://news.google.com/rss/search?q=%E7%A4%BE%E4%BC%9A+%E6%B0%91%E7%94%9F+%E7%83%AD%E7%82%B9&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "社会", 2),
    ("https://news.google.com/rss/search?q=%E5%9B%BD%E9%99%85%E8%B4%B8%E6%98%93+%E5%85%B3%E7%A8%8E+%E5%8E%9F%E6%B2%B9&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "贸易", 2),
]

CATEGORY_ORDER = [c for _, c, _ in FEEDS]
MAX_ITEMS = 10
MAX_AGE_SECONDS = 72 * 3600
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
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=25)
    resp.raise_for_status()
    return feedparser.parse(resp.content)


def fetch_items():
    items = {}
    for idx, (url, category, weight) in enumerate(FEEDS):
        try:
            parsed = fetch_feed(url)
        except Exception as exc:
            print(f"[feed error] {url}: {exc}", file=sys.stderr)
            continue
        if not parsed.entries and getattr(parsed, "bozo", False):
            print(f"[feed error] {url}: {parsed.bozo_exception}", file=sys.stderr)
            continue
        for entry in parsed.entries[:15]:
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
        time.sleep(0.4)
    return list(items.values())


def sort_key(item):
    if item["published"]:
        age = max(0.0, time.time() - item["published"].timestamp())
    else:
        age = 999999.0
    return (-item["weight"], age)


def build_candidates(items, per_category=3):
    by_cat = {}
    for item in items:
        by_cat.setdefault(item["category"], []).append(item)
    candidates = []
    for cat in CATEGORY_ORDER:
        group = sorted(by_cat.get(cat, []), key=sort_key)[:per_category]
        candidates.extend(group)
    return candidates


def summarize_selection(items):
    if not DEEPSEEK_API_KEY:
        return None
    lines = "\n".join(f"{i}. {item['category']}：{item['title']}" for i, item in enumerate(items, 1))
    prompt = (
        "你是资深新闻编辑，正在制作每日早报。下面是从今天新闻中抓取的候选，每条格式为「板块：标题」。\n"
        "任务：从中挑选最重要的10条；同一事件、同一家公司或同一主题只保留一条，选完后若不足10条用其他候选补足。\n"
        "对每条写一句中文摘要，必须包含明确的结论、观点或影响（20-60字）；禁止只复述标题、只写话题而没有下文。\n"
        "只输出10行，每行严格使用「板块：内容」格式，板块名沿用候选中的板块名，不要编号，不要输出其他文字。\n\n" + lines
    )
    payload = {
        "model": "deepseek-v4-pro",
        "messages": [
            {"role": "system", "content": "你是专业的每日新闻简报编辑，文风简洁、信息密度高，注重提炼结论与影响。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 2000,
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
        if len(result) == MAX_ITEMS:
            return result
    except Exception as exc:
        print(f"[summarize error] {exc}", file=sys.stderr)
    return None


def select_fallback(items):
    best = {}
    for item in items:
        cat = item["category"]
        if cat not in best or sort_key(item) < sort_key(best[cat]):
            best[cat] = item
    selected = sorted(best.values(), key=lambda x: x["idx"])
    counts = {}
    used = set()
    for item in selected:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
        used.add(item["title"].lower())
    rest = sorted(items, key=sort_key)
    for item in rest:
        if len(selected) >= MAX_ITEMS:
            break
        if item["title"].lower() in used:
            continue
        if item["category"] not in counts:
            selected.append(item)
            counts[item["category"]] = 1
            used.add(item["title"].lower())
    for item in rest:
        if len(selected) >= MAX_ITEMS:
            break
        if item["title"].lower() in used:
            continue
        if counts.get(item["category"], 0) >= 2:
            continue
        selected.append(item)
        counts[item["category"]] = counts.get(item["category"], 0) + 1
        used.add(item["title"].lower())
    return [{"category": item["category"], "content": item["title"]} for item in selected[:MAX_ITEMS]]


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

    candidates = build_candidates(items)
    final = summarize_selection(candidates)
    if not final:
        final = select_fallback(items)

    now = datetime.now(timezone(timedelta(hours=8)))
    date_str = f"{now.month}月{now.day}日早"
    lines = [f"**{date_str}，读报时间：**", ""]
    for i, entry in enumerate(final, 1):
        content = entry["content"]
        if len(content) > 180:
            content = content[:180].rstrip() + "…"
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