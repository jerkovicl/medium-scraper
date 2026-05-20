"""
Medium Blog Scraper using BeautifulSoup4
Usage: python medium_scraper.py --url https://medium.com/netanelbasal/ --output-dir ./out

Approach:
  1. Fetch all post URLs via the publication's RSS feed + sitemap
  2. Parse each post page with BeautifulSoup4 for full metadata
"""

import argparse
import csv
import json
import logging
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
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

log = logging.getLogger("medium_scraper")


@dataclass
class ScraperConfig:
    delay: float = REQUEST_DELAY
    timeout: int = REQUEST_TIMEOUT


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
    reading_time: str = ""
    description: str = ""
    image: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get(
    url: str,
    session: requests.Session,
    config: ScraperConfig,
    retries: int = 3,
) -> Optional[requests.Response]:
    for attempt in range(retries):
        try:
            resp = session.get(url, headers=HEADERS, timeout=config.timeout)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            log.warning("attempt %d/%d failed for %s: %s", attempt + 1, retries, url, e)
            time.sleep(2 ** attempt)
    return None


def publication_handle(url: str) -> str:
    """
    Extract the publication handle from a Medium URL.

    Supports:
      https://medium.com/netanelbasal/   → 'netanelbasal'
      https://medium.com/@netanelbasal/ → '@netanelbasal'
      https://netbasal.medium.com/       → '@netbasal'
    """
    parsed = urlparse(url)
    # Personal blog subdomain: username.medium.com
    if parsed.netloc.endswith(".medium.com") and parsed.netloc != "medium.com":
        username = parsed.netloc.split(".medium.com")[0]
        return f"@{username}"
    # Publication or @username path on medium.com
    path = parsed.path.strip("/")
    return path.split("/")[0]


def rss_url_for(handle: str) -> str:
    return f"https://medium.com/feed/{handle}"


def sitemap_url_for(handle: str) -> str:
    return f"https://medium.com/sitemap/{handle}"


# ---------------------------------------------------------------------------
# Step 1: collect post URLs
# ---------------------------------------------------------------------------
def fetch_rss_post_urls(
    handle: str,
    session: requests.Session,
    config: ScraperConfig,
) -> tuple[list[str], dict[str, list[str]]]:
    """
    Fetch the RSS feed for a publication.
    Returns (urls, tags_by_url). RSS gives ~10 most recent posts plus tags.
    """
    url = rss_url_for(handle)
    log.info("[rss] fetching %s", url)
    resp = get(url, session, config)
    if not resp:
        return [], {}
    soup = BeautifulSoup(resp.content, "xml")
    urls: list[str] = []
    tags_by_url: dict[str, list[str]] = {}
    for item in soup.find_all("item"):
        link = item.find("link")
        item_url = (
            link.text.strip()
            if link
            else (item.find("guid").text.strip() if item.find("guid") else None)
        )
        if not item_url:
            continue
        clean = _clean_url(item_url)
        urls.append(clean)
        tags_by_url[clean] = [c.text.strip() for c in item.find_all("category") if c.text.strip()]
    log.info("[rss] found %d posts", len(urls))
    return urls, tags_by_url


def _parse_medium_json(text: str) -> dict:
    """Strip Medium's XSS protection prefix and parse JSON."""
    idx = text.find("{")
    if idx == -1:
        return {}
    try:
        return json.loads(text[idx:])
    except json.JSONDecodeError:
        return {}


def fetch_api_post_urls(
    base_url: str,
    handle: str,
    session: requests.Session,
    config: ScraperConfig,
) -> list[str]:
    """
    Retrieve ALL post URLs via Medium's internal JSON API.
    Paginates automatically until all posts are fetched.

    Medium returns 25 posts per page. Each page provides a `paging.next.to`
    token used to request the next page via:
      GET /_/api/collections/{collectionId}/stream?to={token}&limit=25
    """
    first_url = base_url.rstrip("/") + "?format=json&limit=25"
    log.info("[api] fetching %s", first_url)
    resp = get(first_url, session, config)
    if not resp:
        log.info("[api] unavailable")
        return []

    data = _parse_medium_json(resp.text)
    payload = data.get("payload", {})
    collection = payload.get("collection", {})
    collection_id = collection.get("id")
    if not collection_id:
        log.warning("[api] could not find collection ID")
        return []

    post_urls: list[str] = []
    seen: set[str] = set()

    def _extract_urls(payload: dict) -> None:
        posts = payload.get("references", {}).get("Post", {})
        for post in posts.values():
            slug = post.get("uniqueSlug") or post.get("slug")
            if not slug:
                continue
            url = f"https://medium.com/{handle.lstrip('@')}/{slug}"
            clean = _clean_url(url)
            if clean not in seen:
                seen.add(clean)
                post_urls.append(clean)

    _extract_urls(payload)
    log.info("[api] page 1: %d posts so far", len(post_urls))

    paging = payload.get("paging", {})
    page = 2
    while paging.get("next"):
        to = paging["next"].get("to")
        if not to:
            break
        time.sleep(config.delay)
        page_url = f"https://medium.com/_/api/collections/{collection_id}/stream?to={to}&limit=25"
        log.info("[api] page %d: %s", page, page_url)
        r = get(page_url, session, config)
        if not r:
            break
        page_data = _parse_medium_json(r.text)
        page_payload = page_data.get("payload", {})
        before = len(post_urls)
        _extract_urls(page_payload)
        log.info("[api] page %d: %d new posts (%d total)", page, len(post_urls) - before, len(post_urls))
        paging = page_payload.get("paging", {})
        page += 1

    log.info("[api] found %d post URLs total", len(post_urls))
    return post_urls


def fetch_sitemap_post_urls(
    handle: str,
    session: requests.Session,
    config: ScraperConfig,
) -> list[str]:
    """
    Try Medium's sitemap for the publication.
    Falls back gracefully if unavailable (many publications return 404).
    """
    index_url = sitemap_url_for(handle)
    log.info("[sitemap] fetching index %s", index_url)
    resp = get(index_url, session, config)
    if not resp:
        log.info("[sitemap] unavailable, relying on RSS only")
        return []

    soup = BeautifulSoup(resp.content, "lxml")
    monthly_urls = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "sitemap" in href and handle in href:
            if not href.startswith("http"):
                href = "https://medium.com" + href
            monthly_urls.append(href)

    if not monthly_urls:
        monthly_urls = [index_url]

    post_urls: list[str] = []
    seen: set[str] = set()
    for smap_url in monthly_urls:
        time.sleep(config.delay)
        log.info("[sitemap] fetching %s", smap_url)
        r = get(smap_url, session, config)
        if not r:
            continue
        s = BeautifulSoup(r.content, "xml")
        for loc in s.find_all("loc"):
            clean = _clean_url(loc.text.strip())
            if (
                clean
                and f"medium.com/{handle}" in clean
                and clean != f"https://medium.com/{handle}"
                and clean not in seen
            ):
                seen.add(clean)
                post_urls.append(clean)

    log.info("[sitemap] found %d post URLs", len(post_urls))
    return post_urls


def collect_post_urls(
    base_url: str,
    session: requests.Session,
    config: ScraperConfig,
) -> tuple[list[str], dict[str, list[str]]]:
    """Returns (deduplicated_urls, tags_by_url).

    Sources (most-complete first):
      1. JSON API  — paginates through ALL posts via Medium's internal API
      2. Sitemap   — structured XML, fallback for publications with sitemap
      3. RSS feed  — only ~10 latest, but carries tag/category data
    """
    handle = publication_handle(base_url)
    urls_rss, tags_by_url = fetch_rss_post_urls(handle, session, config)
    time.sleep(config.delay)
    urls_sitemap = fetch_sitemap_post_urls(handle, session, config)
    time.sleep(config.delay)
    urls_api = fetch_api_post_urls(base_url, handle, session, config)

    seen: set[str] = set()
    merged: list[str] = []
    # API first (most complete), then sitemap, then RSS
    for u in urls_api + urls_sitemap + urls_rss:
        clean = _clean_url(u)
        if clean and clean not in seen:
            seen.add(clean)
            merged.append(clean)

    log.info("[collect] %d unique post URLs total", len(merged))
    return merged, tags_by_url


def _clean_url(url: str) -> str:
    return url.split("?")[0].split("#")[0].rstrip("/")


# ---------------------------------------------------------------------------
# Step 2: scrape individual post pages
# ---------------------------------------------------------------------------
def scrape_post(
    url: str,
    session: requests.Session,
    config: ScraperConfig,
    rss_tags: Optional[list[str]] = None,
) -> Post:
    post = Post(url=url)
    resp = get(url, session, config)
    if not resp:
        log.warning("[skip] could not fetch %s", url)
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

    # --- Subtitle ---
    og_desc = soup.find("meta", property="og:description")
    if og_desc:
        post.subtitle = og_desc.get("content", "")

    # --- Description ---
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

    # --- Published date ---
    pub_meta = soup.find("meta", property="article:published_time")
    if pub_meta:
        post.published_at = pub_meta.get("content", "")
    else:
        time_tag = soup.find("time")
        if time_tag:
            post.published_at = time_tag.get("datetime", time_tag.get_text(strip=True))

    # --- Tags (RSS categories are most reliable for Medium) ---
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

    # --- Reading time ---
    tw_data = soup.find("meta", attrs={"name": "twitter:data1"})
    if tw_data and "read" in tw_data.get("content", "").lower():
        post.reading_time = tw_data.get("content", "")
    else:
        reading_re = re.compile(r"\d+\s*min\s*read", re.I)
        rt_match = soup.find(string=reading_re)
        if rt_match:
            post.reading_time = reading_re.search(rt_match).group()

    # --- Cover image ---
    og_image = soup.find("meta", property="og:image")
    if og_image:
        post.image = og_image.get("content", "")

    return post


# ---------------------------------------------------------------------------
# Step 3: output
# ---------------------------------------------------------------------------
def save_json(posts: list[Post], path: Path) -> None:
    path.write_text(
        json.dumps([asdict(p) for p in posts], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("[output] saved %d posts → %s", len(posts), path)


def save_csv(posts: list[Post], path: Path) -> None:
    if not posts:
        return
    fieldnames = list(asdict(posts[0]).keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for p in posts:
            row = asdict(p)
            row["tags"] = ", ".join(row["tags"])
            writer.writerow(row)
    log.info("[output] saved %d posts → %s", len(posts), path)


def resolve_output_paths(
    output_dir: str,
    handle: str,
    fmt: str,
) -> list[Path]:
    """Return list of output Path(s) based on --output-dir and --format."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"{handle.lstrip('@')}_{stamp}"
    paths = []
    if fmt in ("json", "both"):
        paths.append(out / f"{stem}.json")
    if fmt in ("csv", "both"):
        paths.append(out / f"{stem}.csv")
    return paths


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape all blog posts from a Medium publication.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Medium publication URL  e.g. https://medium.com/netanelbasal/ or https://netbasal.medium.com/",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        metavar="DIR",
        help="Directory to write output file(s) into. Auto-names files as <handle>_<timestamp>.<ext>",
    )
    parser.add_argument(
        "--format",
        choices=["json", "csv", "both"],
        default="json",
        help="Output format",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=REQUEST_DELAY,
        help="Seconds to wait between requests",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max posts to scrape (0 = no limit)",
    )
    parser.add_argument(
        "--urls-only",
        action="store_true",
        help="Only print collected post URLs, skip scraping details",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    config = ScraperConfig(delay=args.delay)
    session = requests.Session()
    session.headers.update(HEADERS)
    handle = publication_handle(args.url)

    # 1. Collect URLs
    post_urls, tags_by_url = collect_post_urls(args.url, session, config)
    if not post_urls:
        log.error("No post URLs found. Check the URL or try again later.")
        sys.exit(1)

    if args.urls_only:
        if args.output_dir != ".":
            out = Path(args.output_dir)
            out.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            txt_path = out / f"{handle.lstrip('@')}_{stamp}_urls.txt"
            txt_path.write_text("\n".join(post_urls), encoding="utf-8")
            log.info("[output] saved %d URLs → %s", len(post_urls), txt_path)
        else:
            for u in post_urls:
                print(u)
        return

    if args.limit > 0:
        post_urls = post_urls[: args.limit]
        log.info("[info] limiting to %d posts", args.limit)

    # 2. Scrape each post
    posts: list[Post] = []
    total = len(post_urls)
    for i, url in enumerate(post_urls, 1):
        log.info("[%d/%d] scraping %s", i, total, url)
        rss_tags = tags_by_url.get(url.rstrip("/"))
        posts.append(scrape_post(url, session, config, rss_tags=rss_tags))
        time.sleep(config.delay)

    # 3. Save
    for out_path in resolve_output_paths(args.output_dir, handle, args.format):
        if out_path.suffix == ".json":
            save_json(posts, out_path)
        else:
            save_csv(posts, out_path)

    # Summary
    log.info("\n--- Summary ---")
    log.info("Total posts scraped : %d", len(posts))
    log.info("Posts with title    : %d", sum(1 for p in posts if p.title))
    log.info("Posts with author   : %d", sum(1 for p in posts if p.author))
    log.info("Posts with date     : %d", sum(1 for p in posts if p.published_at))
    log.info("Posts with tags     : %d", sum(1 for p in posts if p.tags))
    log.info("Posts with image    : %d", sum(1 for p in posts if p.image))


if __name__ == "__main__":
    main()
