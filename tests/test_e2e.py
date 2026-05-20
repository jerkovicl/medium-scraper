"""
End-to-end tests for medium_scraper.py

These tests make REAL HTTP requests to Medium and verify the scraper
works against the live site. They are slow and require internet access.

Run with:  pytest tests/test_e2e.py -v
Skip with: pytest tests/test_unit.py  (unit tests only)
"""

import pytest
import requests

from medium_scraper import collect_post_urls, scrape_post

TARGET_URL = "https://medium.com/netanelbasal/"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s


@pytest.fixture(scope="module")
def collected(session):
    """Collect post URLs once and reuse across all e2e tests."""
    import medium_scraper
    medium_scraper.REQUEST_DELAY = 0.5  # be polite but not slow
    urls, tags_by_url = collect_post_urls(TARGET_URL, session)
    return urls, tags_by_url


@pytest.fixture(scope="module")
def first_post(collected, session):
    """Scrape the first post once and reuse."""
    import medium_scraper
    medium_scraper.REQUEST_DELAY = 0.5
    urls, tags_by_url = collected
    url = urls[0]
    rss_tags = tags_by_url.get(url.rstrip("/"))
    return scrape_post(url, session, rss_tags=rss_tags)


# ---------------------------------------------------------------------------
# URL collection
# ---------------------------------------------------------------------------
class TestE2EUrlCollection:
    def test_collects_at_least_one_url(self, collected):
        urls, _ = collected
        assert len(urls) >= 1, "Expected at least 1 post URL from the publication"

    def test_urls_point_to_correct_publication(self, collected):
        urls, _ = collected
        for url in urls:
            assert "medium.com/netanelbasal" in url, f"Unexpected URL domain: {url}"

    def test_urls_look_like_post_slugs(self, collected):
        """Post URLs should have a slug with letters and a hex ID at the end."""
        import re
        urls, _ = collected
        slug_pattern = re.compile(r"medium\.com/netanelbasal/[\w-]+-[a-f0-9]{10,}$")
        matched = [u for u in urls if slug_pattern.search(u)]
        assert len(matched) >= 1, "No URLs matched expected slug pattern"

    def test_no_duplicate_urls(self, collected):
        urls, _ = collected
        assert len(urls) == len(set(urls)), "Duplicate URLs found in collected list"

    def test_rss_tags_returned_for_recent_posts(self, collected):
        """RSS feed covers the ~10 most recent posts; at least one should have tags."""
        _, tags_by_url = collected
        posts_with_tags = [u for u, t in tags_by_url.items() if t]
        assert len(posts_with_tags) >= 1, "Expected at least one post with tags from RSS"


# ---------------------------------------------------------------------------
# Post scraping
# ---------------------------------------------------------------------------
class TestE2EPostScraping:
    def test_title_is_non_empty(self, first_post):
        assert first_post.title, "Post title should not be empty"

    def test_title_is_a_string(self, first_post):
        assert isinstance(first_post.title, str)
        assert len(first_post.title) > 5

    def test_author_is_non_empty(self, first_post):
        assert first_post.author, "Post author should not be empty"

    def test_published_at_is_iso_format(self, first_post):
        """article:published_time gives ISO 8601 format."""
        import re
        iso_pattern = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
        assert iso_pattern.search(first_post.published_at), (
            f"published_at '{first_post.published_at}' is not ISO format"
        )

    def test_subtitle_is_non_empty(self, first_post):
        assert first_post.subtitle, "Post subtitle/og:description should not be empty"

    def test_reading_time_matches_pattern(self, first_post):
        import re
        if first_post.reading_time:  # some posts may not have reading time
            assert re.search(r"\d+\s*min\s*read", first_post.reading_time, re.I), (
                f"Unexpected reading_time format: '{first_post.reading_time}'"
            )

    def test_tags_are_a_list(self, first_post):
        assert isinstance(first_post.tags, list)

    def test_url_is_preserved(self, first_post, collected):
        urls, _ = collected
        assert first_post.url == urls[0]

    def test_tags_non_empty_for_rss_post(self, first_post, collected):
        """The first post from RSS should have tags (from RSS categories)."""
        _, tags_by_url = collected
        if first_post.url in tags_by_url:
            assert first_post.tags, "RSS post should have tags"

    def test_no_field_contains_raw_html(self, first_post):
        """Parsed fields must not contain raw HTML tags."""
        for field_val in [first_post.title, first_post.author, first_post.subtitle]:
            assert "<" not in field_val and ">" not in field_val, (
                f"Field contains raw HTML: {field_val!r}"
            )
