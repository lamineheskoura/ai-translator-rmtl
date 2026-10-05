"""Series expansion: numeric chapter URL generation (no server link).

Owner wires this into the server later. Standalone by design:
no import from coordinator / server / translator.
"""

import re
import time

try:
    import requests
    _REQUESTS_AVAILABLE = True
except Exception:  # pragma: no cover
    _REQUESTS_AVAILABLE = False

CHAPTER_RE = re.compile(
    r"^(https?://[^/]+)/manga/([^/]+)/chapter-([0-9]+(?:\.[0-9]+)?)/?.*$"
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://manhuarmtl.com/",
}


def clean_slug(slug: str) -> str:
    """Clean slug -> [a-z0-9-] (same rule scrape_chapter validates)."""
    s = (slug or "").strip().lower()
    s = s.replace("_", "-").replace(" ", "-")
    s = re.sub(r"[^a-z0-9-]", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def _format_chapter_num(value: float) -> str:
    if float(value).is_integer():
        return str(int(round(float(value))))
    s = ("%f" % float(value)).rstrip("0").rstrip(".")
    return s or "0"


def _default_check(url: str, timeout: int = 10) -> bool:
    """Smart-skip probe: True if the chapter URL looks alive."""
    if not _REQUESTS_AVAILABLE:
        return True
    try:
        r = requests.head(url, headers=_HEADERS, timeout=timeout,
                          allow_redirects=True)
        if r.status_code == 200:
            return True
        # Some WAFs reject HEAD (403/405) -> fall back to light GET.
        if r.status_code in (400, 403, 405, 501):
            g = requests.get(url, headers=_HEADERS, timeout=timeout)
            if g.status_code == 200 and "reading-content" in g.text:
                return True
            return False
        return False
    except Exception:
        try:
            g = requests.get(url, headers=_HEADERS, timeout=timeout)
            return g.status_code == 200 and "reading-content" in g.text
        except Exception:
            return False


def expand_series(start_url: str, count: int, check_url=None,
                  timeout: int = 10) -> list:
    """Generate the next `count` chapter links numerically from start_url.

    - start_url: e.g. .../manga/<slug>/chapter-1/ (inclusive start).
    - count: number of chapters to generate (start, start+1, ...).
    - check_url(url)->bool: optional probe; default = light HTTP check.
      Failing URLs are logged and skipped (smart skip).
    - Returns numerically ordered [{url, slug, chapter, chapter_num}].
      slug is cleaned per chapter. No server import.
    """
    m = CHAPTER_RE.match((start_url or "").strip())
    if not m:
        print(f"   [!] expand_series: bad start URL {start_url!r}")
        return []
    origin, slug_raw, ch_raw = m.group(1), m.group(2), m.group(3)
    slug = clean_slug(slug_raw)
    if not slug:
        print(f"   [!] expand_series: bad slug {slug_raw!r}")
        return []
    try:
        start_num = float(ch_raw)
    except Exception:
        return []
    try:
        n = max(0, int(count))
    except Exception:
        n = 0
    if n <= 0:
        return []

    probe = check_url if callable(check_url) else (
        lambda u: _default_check(u, timeout=timeout))

    out: list = []
    for i in range(n):
        ch_num = start_num + i
        ch_str = _format_chapter_num(ch_num)
        url = f"{origin}/manga/{slug}/chapter-{ch_str}/"
        try:
            ok = bool(probe(url))
        except Exception as e:
            print(f"   [!] skip {url} (probe error: {e})")
            continue
        if not ok:
            print(f"   [!] skip {url} (unreachable)")
            continue
        out.append({
            "url": url,
            "slug": slug,
            "chapter": ch_str,
            "chapter_num": float(ch_num),
        })
        time.sleep(0.05)

    # Numeric ordering (not lexicographic: 2 < 10).
    out.sort(key=lambda d: d["chapter_num"])
    return out
