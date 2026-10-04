"""Scraper package: Scrapling spider (primary) + Selenium legacy fallback."""

from .coordinator import scrape_chapter

__all__ = ["scrape_chapter"]
