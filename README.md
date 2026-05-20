# medium-scraper

A Python scraper using **BeautifulSoup4** to scrape all blog posts from any Medium publication.

## Features

- Collects post URLs via **RSS feed** + **sitemap** (most complete coverage)
- Scrapes each post for: title, subtitle, author, publish date, tags, reading time, description
- Outputs to **JSON** or **CSV**
- Configurable delay between requests

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```bash
# Scrape all posts, save to JSON (default)
python medium_scraper.py --url https://medium.com/netanelbasal/

# Save to CSV
python medium_scraper.py --url https://medium.com/netanelbasal/ --output posts.csv

# Only collect post URLs (skip detail scraping)
python medium_scraper.py --url https://medium.com/netanelbasal/ --urls-only

# Limit to 20 posts with 2s delay
python medium_scraper.py --url https://medium.com/netanelbasal/ --limit 20 --delay 2
```

## CLI Options

| Option | Default | Description |
|--------|---------|-------------|
| `--url` | `https://medium.com/netanelbasal/` | Medium publication URL |
| `--output` | `posts.json` | Output file (`.json` or `.csv`) |
| `--delay` | `1.0` | Seconds between requests |
| `--limit` | `0` (no limit) | Max posts to scrape |
| `--urls-only` | — | Only print collected URLs |

## Output Fields

| Field | Description |
|-------|-------------|
| `title` | Post title |
| `url` | Canonical post URL |
| `author` | Author name |
| `published_at` | ISO publish date |
| `tags` | List of tags/topics |
| `subtitle` | Post subtitle |
| `reading_time` | e.g. `5 min read` |
| `description` | Meta description |

## Testing

Install test dependencies:

```bash
pip install pytest pytest-mock
```

Run all tests:

```bash
pytest -v
```

Run unit tests only (fast, no internet required):

```bash
pytest tests/test_unit.py -v
```

Run e2e tests (requires internet, hits live Medium):

```bash
pytest tests/test_e2e.py -v
```

### Test structure

| File | Type | Tests | Description |
|------|------|-------|-------------|
| `tests/test_unit.py` | Unit | 31 | All HTTP mocked via fixtures — tests URL parsing, RSS/sitemap parsing, post field extraction (primary + fallback paths), JSON/CSV output, deduplication |
| `tests/test_e2e.py` | E2E | 15 | Live requests to `https://medium.com/netanelbasal/` — validates URL collection, post fields, ISO date format, no raw HTML in output |
| `tests/fixtures/` | — | — | HTML/XML fixture files used by unit tests |

## Notes

- Medium's pages are heavily JS-rendered; the scraper uses RSS/sitemap to discover posts reliably.
- Please respect Medium's Terms of Service and rate-limit your requests using `--delay`.
