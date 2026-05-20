"""
Unit tests for medium_scraper.py

All HTTP calls are mocked — no network access required.
Run with:  pytest tests/test_unit.py -v
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
FIXTURES = Path(__file__).parent / "fixtures"


def _mock_response(fixture_file: str, status_code: int = 200) -> MagicMock:
    """Return a mock requests.Response backed by a fixture file."""
    content = (FIXTURES / fixture_file).read_bytes()
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.content = content
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(response=resp)
    return resp


def _mock_session(*side_effects) -> MagicMock:
    """Return a mock Session whose .get() returns responses in order."""
    session = MagicMock(spec=requests.Session)
    session.get.side_effect = list(side_effects)
    return session


# ---------------------------------------------------------------------------
# publication_handle
# ---------------------------------------------------------------------------
from medium_scraper import publication_handle


class TestPublicationHandle:
    def test_trailing_slash(self):
        assert publication_handle("https://medium.com/netanelbasal/") == "netanelbasal"

    def test_no_trailing_slash(self):
        assert publication_handle("https://medium.com/netanelbasal") == "netanelbasal"

    def test_at_username(self):
        assert publication_handle("https://medium.com/@netanelbasal/") == "@netanelbasal"

    def test_nested_path_returns_first_segment(self):
        assert publication_handle("https://medium.com/netanelbasal/some-post-slug") == "netanelbasal"


# ---------------------------------------------------------------------------
# fetch_rss_post_urls
# ---------------------------------------------------------------------------
from medium_scraper import fetch_rss_post_urls


class TestFetchRssPostUrls:
    def test_returns_urls_and_tags(self):
        session = _mock_session(_mock_response("rss_feed.xml"))
        urls, tags_by_url = fetch_rss_post_urls("netanelbasal", session)

        assert len(urls) == 2
        assert "https://medium.com/netanelbasal/programmatically-focusing-form-fields-43ef2b1b34e6" in urls
        assert "https://medium.com/netanelbasal/formvaluecontrol-angular-signal-forms-af68ce33df37" in urls

    def test_tags_parsed_per_post(self):
        session = _mock_session(_mock_response("rss_feed.xml"))
        urls, tags_by_url = fetch_rss_post_urls("netanelbasal", session)

        first_url = "https://medium.com/netanelbasal/programmatically-focusing-form-fields-43ef2b1b34e6"
        assert "angular" in tags_by_url[first_url]
        assert "forms" in tags_by_url[first_url]
        assert "javascript" in tags_by_url[first_url]

    def test_returns_empty_on_failed_request(self):
        session = MagicMock(spec=requests.Session)
        session.get.side_effect = requests.ConnectionError("unreachable")
        urls, tags_by_url = fetch_rss_post_urls("netanelbasal", session)

        assert urls == []
        assert tags_by_url == {}

    def test_urls_are_cleaned(self):
        """Query params and fragments must be stripped."""
        session = _mock_session(_mock_response("rss_feed.xml"))
        urls, _ = fetch_rss_post_urls("netanelbasal", session)
        for url in urls:
            assert "?" not in url
            assert "#" not in url
            assert not url.endswith("/")


# ---------------------------------------------------------------------------
# fetch_sitemap_post_urls
# ---------------------------------------------------------------------------
from medium_scraper import fetch_sitemap_post_urls


class TestFetchSitemapPostUrls:
    def test_returns_post_urls_from_sitemap(self):
        # First call: sitemap index HTML; second call: monthly XML
        session = _mock_session(
            _mock_response("sitemap_index.html"),
            _mock_response("sitemap_monthly.xml"),
            _mock_response("sitemap_monthly.xml"),
        )
        with patch("medium_scraper.REQUEST_DELAY", 0):
            urls = fetch_sitemap_post_urls("netanelbasal", session)

        assert len(urls) == 2
        assert "https://medium.com/netanelbasal/programmatically-focusing-form-fields-43ef2b1b34e6" in urls
        assert "https://medium.com/netanelbasal/formvaluecontrol-angular-signal-forms-af68ce33df37" in urls

    def test_excludes_publication_root_url(self):
        session = _mock_session(
            _mock_response("sitemap_index.html"),
            _mock_response("sitemap_monthly.xml"),
            _mock_response("sitemap_monthly.xml"),
        )
        with patch("medium_scraper.REQUEST_DELAY", 0):
            urls = fetch_sitemap_post_urls("netanelbasal", session)

        assert "https://medium.com/netanelbasal" not in urls

    def test_returns_empty_on_404(self):
        # get() retries 3 times, so supply 3 x 404 responses
        session = _mock_session(
            _mock_response("sitemap_index.html", status_code=404),
            _mock_response("sitemap_index.html", status_code=404),
            _mock_response("sitemap_index.html", status_code=404),
        )
        with patch("medium_scraper.REQUEST_DELAY", 0):
            urls = fetch_sitemap_post_urls("netanelbasal", session)

        assert urls == []


# ---------------------------------------------------------------------------
# scrape_post — primary meta tags (Happy Path)
# ---------------------------------------------------------------------------
from medium_scraper import scrape_post


class TestScrapePostPrimaryMetaTags:
    def setup_method(self):
        self.session = _mock_session(_mock_response("post_page.html"))
        self.url = "https://medium.com/netanelbasal/programmatically-focusing-form-fields-43ef2b1b34e6"

    def test_title_extracted(self):
        post = scrape_post(self.url, self.session)
        assert post.title == "Programmatically Focusing Form Fields in Angular Signal Forms"

    def test_subtitle_from_og_description(self):
        post = scrape_post(self.url, self.session)
        assert "programmatically focus" in post.subtitle.lower()

    def test_author_from_meta(self):
        post = scrape_post(self.url, self.session)
        assert post.author == "Netanel Basal"

    def test_published_at_iso_format(self):
        post = scrape_post(self.url, self.session)
        assert post.published_at == "2026-01-15T07:08:22.203Z"

    def test_reading_time_from_twitter_meta(self):
        post = scrape_post(self.url, self.session)
        assert post.reading_time == "2 min read"

    def test_description_from_meta_description(self):
        post = scrape_post(self.url, self.session)
        assert "Angular Signal Forms" in post.description

    def test_rss_tags_used_when_provided(self):
        session = _mock_session(_mock_response("post_page.html"))
        post = scrape_post(self.url, session, rss_tags=["angular", "forms"])
        assert post.tags == ["angular", "forms"]

    def test_url_preserved(self):
        session = _mock_session(_mock_response("post_page.html"))
        post = scrape_post(self.url, session)
        assert post.url == self.url


# ---------------------------------------------------------------------------
# scrape_post — fallback paths
# ---------------------------------------------------------------------------
class TestScrapePostFallbacks:
    def setup_method(self):
        self.session = _mock_session(_mock_response("post_page_fallbacks.html"))
        self.url = "https://medium.com/netanelbasal/fallback-test"

    def test_title_falls_back_to_h1(self):
        post = scrape_post(self.url, self.session)
        assert post.title == "Fallback Test Post Title"

    def test_date_falls_back_to_time_tag(self):
        post = scrape_post(self.url, self.session)
        assert post.published_at == "2025-06-01T10:00:00Z"

    def test_tags_from_keywords_meta(self):
        post = scrape_post(self.url, self.session)
        assert "angular" in post.tags
        assert "javascript" in post.tags
        assert "signals" in post.tags

    def test_author_from_json_ld_list(self):
        post = scrape_post(self.url, self.session)
        assert post.author == "Test Author"

    def test_reading_time_from_inline_text(self):
        post = scrape_post(self.url, self.session)
        assert post.reading_time == "5 min read"

    def test_empty_post_on_failed_request(self):
        session = MagicMock(spec=requests.Session)
        session.get.side_effect = requests.ConnectionError()
        post = scrape_post("https://medium.com/netanelbasal/bad-url", session)
        assert post.title == ""
        assert post.author == ""


# ---------------------------------------------------------------------------
# save_json / save_csv
# ---------------------------------------------------------------------------
from medium_scraper import Post, save_csv, save_json


class TestSaveJson:
    def test_creates_valid_json_file(self, tmp_path):
        posts = [Post(title="Test", url="https://example.com", author="Alice", tags=["a", "b"])]
        out = str(tmp_path / "out.json")
        save_json(posts, out)

        data = json.loads(Path(out).read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["title"] == "Test"
        assert data[0]["tags"] == ["a", "b"]

    def test_empty_list_writes_empty_array(self, tmp_path):
        out = str(tmp_path / "empty.json")
        save_json([], out)
        assert json.loads(Path(out).read_text()) == []


class TestSaveCsv:
    def test_creates_csv_with_header_and_row(self, tmp_path):
        posts = [Post(title="CSV Post", url="https://example.com", tags=["x", "y"])]
        out = str(tmp_path / "out.csv")
        save_csv(posts, out)

        lines = Path(out).read_text(encoding="utf-8").strip().splitlines()
        assert lines[0].startswith("title")        # header row
        assert "CSV Post" in lines[1]
        assert "x, y" in lines[1]                  # tags flattened

    def test_does_nothing_for_empty_list(self, tmp_path):
        out = str(tmp_path / "empty.csv")
        save_csv([], out)
        assert not Path(out).exists()


# ---------------------------------------------------------------------------
# collect_post_urls — deduplication
# ---------------------------------------------------------------------------
from medium_scraper import collect_post_urls


class TestCollectPostUrls:
    def test_deduplicates_rss_and_sitemap_overlap(self):
        with patch("medium_scraper.fetch_rss_post_urls") as mock_rss, \
             patch("medium_scraper.fetch_sitemap_post_urls") as mock_sitemap, \
             patch("medium_scraper.REQUEST_DELAY", 0):

            shared_url = "https://medium.com/netanelbasal/shared-post-abc123"
            mock_rss.return_value = ([shared_url], {shared_url: ["angular"]})
            mock_sitemap.return_value = [shared_url, "https://medium.com/netanelbasal/only-in-sitemap-xyz"]

            session = MagicMock()
            urls, tags = collect_post_urls("https://medium.com/netanelbasal/", session)

        assert urls.count(shared_url) == 1
        assert len(urls) == 2

    def test_sitemap_urls_come_first(self):
        with patch("medium_scraper.fetch_rss_post_urls") as mock_rss, \
             patch("medium_scraper.fetch_sitemap_post_urls") as mock_sitemap, \
             patch("medium_scraper.REQUEST_DELAY", 0):

            rss_url = "https://medium.com/netanelbasal/rss-only"
            sitemap_url = "https://medium.com/netanelbasal/sitemap-only"
            mock_rss.return_value = ([rss_url], {})
            mock_sitemap.return_value = [sitemap_url]

            session = MagicMock()
            urls, _ = collect_post_urls("https://medium.com/netanelbasal/", session)

        assert urls[0] == sitemap_url
        assert urls[1] == rss_url
