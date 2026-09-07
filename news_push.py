import os
import sys
import time
import re
import html
import base64
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import feedparser
import requests

SENDKEY = os.environ.get("SERVERCHAN_SENDKEY", "").strip()
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
DRY_RUN = "--dry-run" in sys.argv

SENDKEYS = []
if SENDKEY:
    SENDKEYS.append(SENDKEY)
for key in os.environ.get("SERVERCHAN_SENDKEYS", "").replace("，", ",").split(","):
    key = key.strip()
    if key and key not in SENDKEYS:
        SENDKEYS.append(key)

FEEDS = [
    ("https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "综合", 3),
    ("https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "财经", 3),
    ("https://news.google.com/rss/headlines/section/topic/WORLD?hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "国际", 3),
    ("https://news.google.com/rss/headlines/section/topic/NATION?hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "国内", 2),
    ("https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "科技", 2),
]

BACKUP_FEEDS = [
    ("https://www.chinanews.com.cn/rss/scroll-news.xml", "国内", 2),
    ("https://feeds.a.dj.com/rss/RSSMarketsMain.xml", "财经", 2),
]

MAX_ITEMS = 20
MAX_AGE_SECONDS = 96 * 3600
ENRICH_LIMIT = 30
ENRICH_TIMEOUT = 8
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def clean(value):
    if not value:
        return ""
    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def decode_google_link(link):
    match = re.search(r"/articles/([A-Za-z0-9_-]+)", link or "")
    if not match:
        return None
    token = match.group(1)
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except Exception:
        return None
    try:
        text = raw.decode("latin-1", errors="ignore")
    except Exception:
        return None
    found = re.search(r"https?://[^\s\"'<>]+", text)
    if not found:
        return None
    url = found.group(0).rstrip(")]}\"'")
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return url


def extract_text(html_text):
    if not html_text:
        return ""
    html_text = re.sub(r"(?is)<(script|style|noscript|iframe)[^>]*>.*?</\1>", " ", html_text)
    paragraphs = re.findall(r"(?is)<p[^>]*>(.*?)</p>", html_text)
    parts = []
    for p in paragraphs:
        p = clean(p)
        if len(p) >= 15:
            parts.append(p)
    body = " ".join(parts)
    if len(body) < 60:
        meta = re.search(r'(?is)<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)', html_text)
        if meta:
            body = clean(meta.group(1))
    return body


def fetch_article_text(url):
    if not url:
        return ""
    candidates = []
    decoded = decode_google_link(url)
    if decoded:
        candidates.append(decoded)
    candidates.append(url)
    for candidate in candidates:
        try:
            resp = requests.get(candidate, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5"}, timeout=ENRICH_TIMEOUT, allow_redirects=True)
            if resp.status_code != 200:
                continue
            final_url = resp.url or ""
            if "news.google.com" in final_url:
                continue
            content_type = resp.headers.get("Content-Type", "")
            if "text/html" not in content_type:
                continue
            if "charset" not in content_type.lower():
                resp.encoding = resp.apparent_encoding or "utf-8"
            text = extract_text(resp.text)
            if text:
                return text
        except Exception:
            continue
    return ""


def enrich_items(items, limit=ENRICH_LIMIT):
    top = sorted(items, key=sort_key)[:limit]
    def work(item):
        url = decode_google_link(item.get("link") or "")
        text = fetch_article_text(url)
        return item.get("title"), text
    ctx = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(work, item) for item in top]
        for future in as_completed(futures):
            try:
                title, text = future.result()
            except Exception:
                continue
            if text:
                ctx[title] = text
    for item in items:
        item["ctx"] = ctx.get(item["title"], "")
    return items


def parse_time(entry):
    for field in ("published_parsed", "updated_parsed"):
        parsed = entry.get(field)
        if parsed:
            return datetime(*parsed[:6], tzinfo=timezone.utc)
    return None


def fetch_feed(url):
    last_exc = None
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=12)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        if parsed.entries:
            return parsed
        last_exc = Exception("empty feed")
    except Exception as exc:
        last_exc = exc
    raise last_exc or Exception("feed failed")


def fetch_items():
    items = {}
    feeds = list(FEEDS)
    for idx, (url, category, weight) in enumerate(feeds):
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
                    "link": entry.get("link") or "",
                }
        time.sleep(0.8)
    # If Google News was unreachable, fall back to sources that work on most networks.
    if len(items) < 10:
        for idx, (url, category, weight) in enumerate(BACKUP_FEEDS, start=100):
            try:
                parsed = fetch_feed(url)
            except Exception as exc:
                print(f"[backup feed error] {url}: {exc}", file=sys.stderr)
                continue
            for entry in parsed.entries[:30]:
                title = clean(entry.get("title"))
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
                        "link": entry.get("link") or "",
                    }
            time.sleep(0.5)
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
        result.append(item)
        if len(result) >= limit:
            break
    return result


def summarize(items):
    if not DEEPSEEK_API_KEY:
        print("[summarize] DEEPSEEK_API_KEY is empty", file=sys.stderr)
        return None, "未检测到 DeepSeek 密钥"
    parts = []
    for i, item in enumerate(items, 1):
        line = f"{i}. {item['title']}"
        ctx = (item.get("ctx") or "").strip()
        if ctx:
            line += f"\n[摘要] {ctx[:400]}"
        parts.append(line)
    lines = "\n".join(parts)
    prompt = (
        "你是资深新闻编辑，正在制作每日早报。下面是从今天新闻中抓到的标题（部分带正文摘要，已去重）。\n"
        "任务：从中挑选最重要的15条左右（10到20条），覆盖尽量多样的板块（如政治、国际、军事、财经、金融、科技、房市、企业、灾害、社会、热议等），"
        "同一事件、同一家公司或同一主题只保留一条，并优先覆盖战争进展、天灾人祸、网上热议、重大政策与市场影响等公众关注热点。\n"
        "为每条分配一个最合适的板块，并写4-6句中文摘要，约160-280字。要求：优先使用[摘要]里的真实信息，写清具体数字或数据（如中签率、涨跌幅、指数点位、死亡或失踪人数、金额、日期等）、双方表态或会谈要点、事件起因经过结果，以及明确的结论、观点或影响；"
        "若某条没有[摘要]，请用标题和常识把“这是什么事件、为什么重要、带来什么影响”讲清楚，但不得编造无法确定的具体数字或事实；"
        "严禁只复述标题、只写话题而没有下文。\n"
        "只输出15行左右（10到20行），每行严格使用「板块：内容」格式，不要编号，不要输出其他文字。\n\n" + lines
    )
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是专业的每日新闻简报编辑，文风简洁但信息密度高，用具体数字、事实和结论说话。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 8000,
    }
    try:
        resp = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=180,
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
        if result:
            return result[:MAX_ITEMS], None
        return None, "模型返回内容无法解析"
    except Exception as exc:
        print(f"[summarize error] {exc}", file=sys.stderr)
        return None, f"接口调用失败：{str(exc)[:120]}"


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
    results = []
    for key in SENDKEYS:
        url = f"https://sctapi.ftqq.com/{key}.send"
        try:
            resp = requests.post(url, data={"title": title, "desp": desp}, timeout=30)
            resp.raise_for_status()
            results.append(resp.json())
        except Exception as exc:
            results.append({"code": -1, "message": f"{key[:8]}...: {exc}"})
    return results


def main():
    items = fetch_items()
    if not items:
        print("no items fetched", file=sys.stderr)
        return 1

    candidates = dedupe_titles(items, limit=40)
    enrich_items(candidates)
    final, summary_error = summarize(candidates)
    if not final:
        print("[warn] DeepSeek summary failed; using fallback title list", file=sys.stderr)
        final = fallback(items)

    now = datetime.now(timezone(timedelta(hours=8)))
    date_str = f"{now.month}月{now.day}日早"
    lines = [f"**{date_str}，读报时间：**", ""]
    if summary_error:
        lines.append(f"⚠️ {summary_error}，本次为标题列表")
        lines.append("")
    for i, entry in enumerate(final, 1):
        content = entry["content"]
        if len(content) > 420:
            content = content[:420].rstrip() + "…"
        lines.append(f"{i}、{entry['category']}：{content}")
    desp = "\n\n".join(lines)

    print(desp)
    if DRY_RUN:
        print("\n[dry-run] no message sent")
        return 0
    if not SENDKEYS:
        print("no ServerChan send key configured", file=sys.stderr)
        return 1

    results = send(f"{date_str}，读报时间", desp)
    print(results)
    return 0 if any(r.get("code") == 0 for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
