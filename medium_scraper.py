"""
Medium Blog Scraper using BeautifulSoup4
Usage: python medium_scraper.py --url https://medium.com/netanelbasal/ --output posts.json

Approach:
  1. Fetch all post URLs via the publication's RSS feed + sitemap
  2. Parse each post page with BeautifulSoup4 for full metadata
"""

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
REQUEST_DELAY = 1.0   # seconds between requests (be polite)
REQUEST_TIMEOUT = 15  # seconds


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class Post:
    title: str = ""
    url: str = ""
    author: str = ""
    published_at: str = ""
    tags: list[str] = field(default_factory=list)
    subtitle: str = ""
    claps: str = ""
    reading_time: str = ""
    description: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get(url: str, session: requests.Session, retries: int = 3) -> Optional[requests.Response]:
    for attempt in range(retries):
        try:
            resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            print(f"  [warn] attempt {attempt + 1}/{retries} failed for {url}: {e}")
            time.sleep(2 ** attempt)
    return None


def publication_handle(url: str) -> str:
    """Extract 'netanelbasal' from 'https://medium.com/netanelbasal/'"""
    path = urlparse(url).path.strip("/")
    # e.g. 'netanelbasal' or '@username'
    return path.split("/")[0]


# ---------------------------------------------------------------------------
# Step 1: collect post URLs
# ---------------------------------------------------------------------------
def fetch_rss_post_urls(handle: str, session: requests.Session) -> tuple[list[str], dict[str, list[str]]]:
    """
    Medium RSS feeds provide up to ~10 most-recent posts.
    Returns (urls, tags_by_url) where tags_by_url maps cleaned URL → list of tags.
    """
    rss_url = f"https://medium.com/feed/{handle}"
    print(f"[rss] fetching {rss_url}")
    resp = get(rss_url, session)
    if not resp:
        return [], {}
    soup = BeautifulSoup(resp.content, "xml")
    urls: list[str] = []
    tags_by_url: dict[str, list[str]] = {}
    for item in soup.find_all("item"):
        link = item.find("link")
        url = link.text.strip() if link else (item.find("guid").text.strip() if item.find("guid") else None)
        if not url:
            continue
        clean = url.split("?")[0].split("#")[0].rstrip("/")
        urls.append(clean)
        tags_by_url[clean] = [c.text.strip() for c in item.find_all("category") if c.text.strip()]
    print(f"[rss] found {len(urls)} posts")
    return urls, tags_by_url


def fetch_sitemap_post_urls(handle: str, session: requests.Session) -> list[str]:
    """
    Try Medium's sitemap for the publication. Medium serves sitemaps at:
      https://medium.com/sitemap/<handle>  (may 404 for some publications)
    Falls back gracefully if unavailable.
    """
    sitemap_index_url = f"https://medium.com/sitemap/{handle}"
    print(f"[sitemap] fetching index {sitemap_index_url}")
    resp = get(sitemap_index_url, session)
    if not resp:
        print("[sitemap] sitemap unavailable, relying on RSS only")
        return []

    soup = BeautifulSoup(resp.content, "lxml")
    # Find links to monthly XML sitemaps
    monthly_urls = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "sitemap" in href and handle in href:
            if not href.startswith("http"):
                href = "https://medium.com" + href
            monthly_urls.append(href)

    if not monthly_urls:
        monthly_urls = [sitemap_index_url]

    post_urls: list[str] = []
    for smap_url in monthly_urls:
        time.sleep(REQUEST_DELAY)
        print(f"[sitemap] fetching {smap_url}")
        r = get(smap_url, session)
        if not r:
            continue
        s = BeautifulSoup(r.content, "xml")
        for loc in s.find_all("loc"):
            u = loc.text.strip()
            clean = u.split("?")[0].split("#")[0].rstrip("/")
            if clean and f"medium.com/{handle}" in clean and clean != f"https://medium.com/{handle}":
                post_urls.append(clean)

    print(f"[sitemap] found {len(post_urls)} post URLs")
    return post_urls


def collect_post_urls(base_url: str, session: requests.Session) -> tuple[list[str], dict[str, list[str]]]:
    """Returns (urls, tags_by_url)."""
    handle = publication_handle(base_url)
    urls_rss, tags_by_url = fetch_rss_post_urls(handle, session)
    time.sleep(REQUEST_DELAY)
    urls_sitemap = fetch_sitemap_post_urls(handle, session)

    # Merge & de-duplicate; sitemap is more complete so goes first
    seen: set[str] = set()
    merged: list[str] = []
    for u in urls_sitemap + urls_rss:
        clean = u.split("?")[0].split("#")[0].rstrip("/")
        if clean and clean not in seen:
            seen.add(clean)
            merged.append(clean)

    print(f"[collect] {len(merged)} unique post URLs total")
    return merged, tags_by_url


# ---------------------------------------------------------------------------
# Step 2: scrape individual post pages
# ---------------------------------------------------------------------------
def scrape_post(url: str, session: requests.Session, rss_tags: Optional[list[str]] = None) -> Post:
    post = Post(url=url)
    resp = get(url, session)
    if not resp:
        print(f"  [skip] could not fetch {url}")
        return post

    soup = BeautifulSoup(resp.content, "lxml")

    # --- Title ---
    og_title = soup.find("meta", property="og:title")
    if og_title:
        post.title = og_title.get("content", "")
    else:
        h1 = soup.find("h1")
        if h1:
            post.title = h1.get_text(strip=True)

    # --- Subtitle / description ---
    # og:description is reliable; h2 often captures author name on Medium
    og_desc = soup.find("meta", property="og:description")
    if og_desc:
        post.subtitle = og_desc.get("content", "")

    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc:
        post.description = meta_desc.get("content", "")

    # --- Author ---
    author_meta = soup.find("meta", attrs={"name": "author"})
    if author_meta:
        post.author = author_meta.get("content", "")

    if not post.author:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, dict) and "author" in data:
                    author = data["author"]
                    if isinstance(author, dict):
                        post.author = author.get("name", "")
                    elif isinstance(author, list) and author:
                        post.author = author[0].get("name", "")
            except (json.JSONDecodeError, AttributeError):
                pass

    # --- Published date (prefer article:published_time for clean ISO format) ---
    pub_meta = soup.find("meta", property="article:published_time")
    if pub_meta:
        post.published_at = pub_meta.get("content", "")
    else:
        time_tag = soup.find("time")
        if time_tag:
            post.published_at = time_tag.get("datetime", time_tag.get_text(strip=True))

    # --- Tags: RSS categories are most reliable for Medium ---
    if rss_tags:
        post.tags = rss_tags
    else:
        keywords_meta = soup.find("meta", attrs={"name": "keywords"})
        if keywords_meta:
            raw = keywords_meta.get("content", "")
            post.tags = [t.strip() for t in raw.split(",") if t.strip()]
        if not post.tags:
            post.tags = [
                m.get("content", "")
                for m in soup.find_all("meta", property="article:tag")
            ]

    # --- Reading time (twitter:data1 meta or inline text) ---
    tw_data = soup.find("meta", attrs={"name": "twitter:data1"})
    if tw_data and "read" in tw_data.get("content", "").lower():
        post.reading_time = tw_data.get("content", "")
    else:
        reading_re = re.compile(r"\d+\s*min\s*read", re.I)
        rt_match = soup.find(string=reading_re)
        if rt_match:
            post.reading_time = reading_re.search(rt_match).group()

    return post


# ---------------------------------------------------------------------------
# Step 3: output
# ---------------------------------------------------------------------------
def save_json(posts: list[Post], path: str) -> None:
    data = [asdict(p) for p in posts]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[output] saved {len(posts)} posts → {path}")


def save_csv(posts: list[Post], path: str) -> None:
    if not posts:
        return
    fieldnames = list(asdict(posts[0]).keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for p in posts:
            row = asdict(p)
            row["tags"] = ", ".join(row["tags"])  # flatten list for CSV
            writer.writerow(row)
    print(f"[output] saved {len(posts)} posts → {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    global REQUEST_DELAY

    parser = argparse.ArgumentParser(description="Scrape all blog posts from a Medium publication.")
    parser.add_argument(
        "--url",
        default="https://medium.com/netanelbasal/",
        help="Medium publication URL (default: https://medium.com/netanelbasal/)",
    )
    parser.add_argument(
        "--output",
        default="posts.json",
        help="Output file path. Use .json or .csv extension (default: posts.json)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=REQUEST_DELAY,
        help=f"Delay in seconds between requests (default: {REQUEST_DELAY})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max number of posts to scrape (0 = no limit)",
    )
    parser.add_argument(
        "--urls-only",
        action="store_true",
        help="Only collect and print post URLs, skip scraping post details",
    )
    args = parser.parse_args()
    REQUEST_DELAY = args.delay

    session = requests.Session()
    session.headers.update(HEADERS)

    # 1. Collect URLs
    post_urls, tags_by_url = collect_post_urls(args.url, session)
    if not post_urls:
        print("[error] No post URLs found. The publication might be JS-gated or the handle is wrong.")
        sys.exit(1)

    if args.urls_only:
        for u in post_urls:
            print(u)
        return

    if args.limit > 0:
        post_urls = post_urls[: args.limit]
        print(f"[info] limiting to {args.limit} posts")

    # 2. Scrape each post (pass RSS tags when available)
    posts: list[Post] = []
    for i, url in enumerate(post_urls, 1):
        print(f"[{i}/{len(post_urls)}] scraping {url}")
        rss_tags = tags_by_url.get(url.rstrip("/"))
        post = scrape_post(url, session, rss_tags=rss_tags)
        posts.append(post)
        time.sleep(REQUEST_DELAY)

    # 3. Save
    if args.output.endswith(".csv"):
        save_csv(posts, args.output)
    else:
        save_json(posts, args.output)

    # Quick summary
    print("\n--- Summary ---")
    print(f"Total posts scraped : {len(posts)}")
    print(f"Posts with title    : {sum(1 for p in posts if p.title)}")
    print(f"Posts with author   : {sum(1 for p in posts if p.author)}")
    print(f"Posts with date     : {sum(1 for p in posts if p.published_at)}")
    print(f"Posts with tags     : {sum(1 for p in posts if p.tags)}")


if __name__ == "__main__":
    main()
