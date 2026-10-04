"""Scrapling-based chapter fetcher (new primary path).

Uses StealthyFetcher (rendered HTML, Cloudflare bypass) instead of Selenium.
Keeps engine.py / extractor.py as legacy fallback — this module is standalone.

Returns the same tuple shape as extractor.parse_chapter_full:
    (image_urls, page_texts, rendered_w, css_heights, title)
or None on failure.
"""

import re


def _parse_rgb(value: str) -> str | None:
    """Parse 'rgb(r, g, b)' or '#rrggbb' -> '#rrggbb' hex (lowercase)."""
    if not value:
        return None
    v = value.strip().lower()
    m = re.match(r"#([0-9a-f]{6})$", v)
    if m:
        return "#" + m.group(1)
    m = re.match(r"#([0-9a-f]{3})$", v)
    if m:
        return "#" + "".join(c * 2 for c in m.group(1))
    m = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", v)
    if m:
        r, g, b = (max(0, min(255, int(m.group(i)))) for i in (1, 2, 3))
        return f"#{r:02x}{g:02x}{b:02x}"
    return None


def _parse_shadow(span_style: str) -> tuple[str | None, float]:
    """Parse span text-shadow -> (stroke_hex, stroke_width_px).

    Site uses 4-directional shadows e.g.
    'rgb(255,255,255) -2.31px -2.31px 0px, ...' -> white, 2.31px.
    """
    if not span_style:
        return None, 0.0
    m = re.search(r"(rgba?\([^)]+\)|#[0-9a-fA-F]{3,6})", span_style)
    color = _parse_rgb(m.group(1)) if m else None
    offs = re.findall(r"(-?[0-9.]+)px", span_style)
    width = 0.0
    if offs:
        try:
            width = max(abs(float(x)) for x in offs)
        except Exception:
            width = 0.0
    return color, width


def parse_float(value, default: float = 0.0) -> float:
    try:
        m = re.search(r"[0-9]+(?:\.[0-9]+)?", str(value))
        return float(m.group()) if m else default
    except (TypeError, AttributeError):
        return default


def parse_style_px(style: str, prop: str) -> float:
    m = re.search(rf"{re.escape(prop)}\s*:\s*([0-9.]+)\s*px", style or "")
    return float(m.group(1)) if m else 0.0


def _pick_img_src(attrib: dict) -> str | None:
    """Fallback chain: src -> data-src -> data-lazy-src -> srcset(first URL)."""
    for key in ("src", "data-src", "data-lazy-src"):
        v = (attrib.get(key) or "").strip()
        if v and not v.startswith("data:"):
            return v
    srcset = (attrib.get("srcset") or attrib.get("data-srcset") or "").strip()
    if srcset:
        first = srcset.split(",")[0].strip().split()
        if first:
            cand = first[0].strip()
            if cand and not cand.startswith("data:"):
                return cand
    return None


def extract_image_urls(page) -> list:
    """Extract deduped image URLs from .reading-content img.wp-manga-chapter-img."""
    try:
        img_els = page.css(".reading-content img.wp-manga-chapter-img")
    except Exception:
        return []
    seen, urls = set(), []
    for el in img_els:
        try:
            attrib = el.attrib or {}
        except Exception:
            continue
        src = _pick_img_src(attrib)
        if src and src not in seen:
            seen.add(src)
            urls.append(src)
    return urls


def extract_overlays(page, num_images: int = 0) -> dict:
    """Extract overlays -> {page_idx: [dict]} same shape as extractor.py.

    - img_x_offset assumed 0 (no JS measurement; rendered HTML).
    - page index from id regex split_(\\d+), else 0.
    - data-box-width / data-box-height / data-base-font-size as fallback.
    """
    try:
        overlay_els = page.css("div.manga-ocr-button-overlay")
    except Exception:
        return {}

    page_texts: dict = {}
    geo_tops: dict = {}
    for el in overlay_els:
        try:
            attrib = el.attrib or {}
            el_id = (attrib.get("id") or "").strip()
            style = attrib.get("style") or ""

            # span text (same as Selenium span.text) + span style for stroke
            text = ""
            span_style = ""
            try:
                spans = el.css("span")
                if len(spans) > 0:
                    text = (spans[0].text or "").strip()
                    try:
                        span_style = (spans[0].attrib or {}).get("style") or ""
                    except Exception:
                        span_style = ""
            except Exception:
                text = ""

            # Geometry side-channel: record EVERY overlay top (even empty
            # placeholder boxes) for header/scale estimation. Texts keep
            # only non-empty overlays.
            try:
                _pg = 0
                _m = re.search(r"split_(\d+)", el_id)
                if _m:
                    _pg = int(_m.group(1))
                if num_images > 0:
                    _pg = max(0, min(_pg, num_images - 1))
                _top = parse_style_px(style, "top")
                if _top > 0:
                    geo_tops.setdefault(_pg, []).append(_top)
            except Exception:
                pass
            if not text:
                continue

            # Site typography: text color from overlay style,
            # stroke (outline) from span text-shadow.
            site_color = _parse_rgb(
                (re.search(r"color\s*:\s*([^;'\"]+)", style).group(1)
                 if re.search(r"color\s*:\s*([^;'\"]+)", style) else "")
            ) or "#000000"
            shadow_color, shadow_w = _parse_shadow(span_style)

            left_center_raw = parse_style_px(style, "left")
            top_global = parse_style_px(style, "top")
            css_width = parse_style_px(style, "max-width")
            css_height = parse_style_px(style, "height")
            css_fontsize = parse_style_px(style, "font-size")

            if css_width == 0:
                css_width = parse_float(attrib.get("data-box-width"))
            if css_height == 0:
                css_height = parse_float(attrib.get("data-box-height"))
            if css_fontsize == 0:
                css_fontsize = parse_float(
                    attrib.get("data-base-font-size")
                    or attrib.get("data-font-size")
                )

            # No JS offset measurement -> 0
            left_center = left_center_raw
            css_left = left_center - css_width / 2.0
            if css_left < 0:
                css_left = 0

            page_num = 0
            m = re.search(r"split_(\d+)", el_id)
            if m:
                try:
                    page_num = int(m.group(1))
                except Exception:
                    page_num = 0
            if num_images > 0:
                if page_num < 0:
                    page_num = 0
                if page_num >= num_images:
                    page_num = num_images - 1

            if page_num not in page_texts:
                page_texts[page_num] = []

            page_texts[page_num].append({
                "id": el_id,
                "original_text": text,
                "arabic_text": "",
                "css_top": top_global,
                "css_left": css_left,
                "css_left_center": left_center,
                "css_width": css_width,
                "css_height": css_height,
                "css_font_size": css_fontsize,
                "css_color": site_color,
                "css_stroke_color": shadow_color or "#ffffff",
                "css_stroke_width": round(shadow_w, 2),
                "line_height": 1.2,
            })
        except Exception:
            continue

    for p in list(page_texts.keys()):
        page_texts[p].sort(key=lambda x: x["css_top"])
    return page_texts, geo_tops


def extract_title(page) -> str:
    try:
        els = page.css("h1#chapter-heading")
        if len(els) > 0:
            return (els[0].text or "").strip()
    except Exception:
        pass
    return ""


def fetch_chapter_page(url: str, timeout: int = 60000):
    """Fetch chapter with Scrapling StealthyFetcher.

    Returns (image_urls, page_texts, rendered_w, css_heights, title, geo_tops)
    or None on failure. geo_tops = {page_idx: [tops]} for ALL overlays
    (incl. empty placeholders) used only for geometry estimation.
    """
    try:
        from scrapling import StealthyFetcher
    except Exception as e:
        print(f"   [!] Scrapling import failed: {e}")
        return None

    try:
        page = StealthyFetcher.fetch(
            url,
            solve_cloudflare=True,
            wait_selector=".reading-content img",
            wait=3000,
            timeout=timeout,
            network_idle=False,
        )
    except Exception as e:
        print(f"   [!] StealthyFetcher.fetch failed: {e}")
        return None

    if page is None:
        print("   [!] StealthyFetcher returned None.")
        return None

    try:
        image_urls = extract_image_urls(page)
        page_texts, geo_tops = extract_overlays(page, num_images=len(image_urls))
        title = extract_title(page)
        # No reliable JS width measurement here; use common PNG width
        # so coordinator falls back to PNG-based estimation.
        rendered_w = 800.0
        css_heights: list = []
        total = sum(len(v) for v in page_texts.values())
        print(f"   [+] Spider: {len(image_urls)} images | {total} overlays "
              f"| rendered_w={rendered_w}")
        return image_urls, page_texts, rendered_w, css_heights, title, geo_tops
    except Exception as e:
        print(f"   [!] Spider parse failed: {e}")
        return None
