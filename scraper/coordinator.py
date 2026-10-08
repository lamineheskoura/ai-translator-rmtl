import json
import re
import time
from pathlib import Path

try:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException
    _SELENIUM_AVAILABLE = True
except Exception:
    By = None
    WebDriverWait = None
    EC = None
    TimeoutException = Exception
    _SELENIUM_AVAILABLE = False

try:
    from .engine import (
        setup_driver, download_images, convert_all_to_png,
        get_png_dimensions, sanitize,
    )
    _ENGINE_AVAILABLE = True
except Exception:
    _ENGINE_AVAILABLE = False

    def sanitize(name: str) -> str:
        return re.sub(r'[\\/:*?"<>|]', "_", name).strip()

    setup_driver = None
    download_images = None
    convert_all_to_png = None
    get_png_dimensions = None

try:
    from .extractor import parse_chapter_full, extract_chapter_info
    _EXTRACTOR_AVAILABLE = True
except Exception:
    _EXTRACTOR_AVAILABLE = False
    parse_chapter_full = None

    def extract_chapter_info(url: str):
        m = re.search(r"/manga/([^/]+)/chapter-([0-9]+(?:\.[0-9]+)?)", url)
        return (m.group(1), m.group(2)) if m else (None, None)

CHAPTER_DELAY = 3.0


def compute_scale(rendered_css_width: float, actual_png_width: int) -> float:
    if rendered_css_width <= 0 or actual_png_width <= 0:
        print("   [!] Cannot compute scale factor - defaulting to 1.0")
        return 1.0
    s = actual_png_width / rendered_css_width
    print(f"   [i] Scale factor: {s:.4f}  "
          f"(CSS {rendered_css_width:.1f}px -> PNG {actual_png_width}px)")
    return s


def estimate_horizontal_scale(page_texts: dict, png_dims: list) -> float | None:
    """Horizontal scale s_h from WIDTHS only (never from tops).

    Displayed image width = min(natural width, theme content width).
    The widest overlay right edge (P95) approximates the CSS content width:
        s_h = median(png_w) / P95(css_left + css_width)
    Falls back to the theme rule displayed = min(natural, 940).
    """
    import statistics
    rights = []
    for v in (page_texts or {}).values():
        for t in (v or []):
            try:
                w = float(t.get("css_width", 0) or 0)
                if w > 0:
                    rights.append(float(t.get("css_left", 0) or 0) + w)
            except Exception:
                continue
    png_ws = [w for w, _ in (png_dims or []) if w and w > 0]
    if not rights or not png_ws:
        return None
    rights.sort()
    p95 = rights[min(len(rights) - 1, int(len(rights) * 0.95))]
    med_w = statistics.median(png_ws)
    if p95 > 0:
        s = med_w / p95
        if 0.5 <= s <= 3.0:
            print(f"   [i] Horizontal scale s_h={s:.4f} "
                  f"(PNG {med_w:.0f} / CSS {p95:.0f})")
            return s
    s = med_w / min(med_w, 940.0)
    print(f"   [i] Horizontal scale s_h={s:.4f} (fallback rule)")
    return s


def estimate_vertical_geometry(page_texts: dict, png_dims: list,
                             geo_tops: dict | None = None
                             ) -> tuple[float | None, float]:
    """Estimate uniform scale s + header H0 from overlay tops (spider path).

    Model: top = H0 + cum_png / s + r  (r = in-page offset >= 0).
    s comes from a robust Theil-Sen slope of (cum_png, top) over pairs
    with a wide baseline; H0 from the median of the smallest residuals
    (pages whose topmost box sits nearly flush with the page top reveal
    the ad-header offset; live-browser verified).
    geo_tops ({page: [tops]}, incl. empty placeholder boxes) adds extra
    geometry points without creating texts.
    Returns (None, 0.0) when fewer than 3 points exist — including ALL
    single-page chapters (no cross-page baseline possible by construction);
    callers then degrade to the width-based horizontal scale.
    """
    cum = [0.0]
    for _, h in (png_dims or []):
        cum.append(cum[-1] + (h or 0))
    pts: list[tuple[float, float]] = []
    seen: set = set()
    for idx, arr in (page_texts or {}).items():
        try:
            ci = int(idx)
        except Exception:
            continue
        if ci < 0 or ci >= len(png_dims) or not arr:
            continue
        for t in arr:
            try:
                f = float(t.get("css_top", 0) or 0)
                seen.add((cum[ci], round(f, 1)))
                pts.append((cum[ci], f))
            except Exception:
                continue
    for idx, tops in (geo_tops or {}).items():
        try:
            ci = int(idx)
        except Exception:
            continue
        if ci < 0 or ci >= len(png_dims):
            continue
        for tp in (tops or []):
            try:
                f = float(tp)
                # Dedupe: geo_tops repeats non-empty overlay tops already
                # added above; without this the minimum self-corroborates
                # and the outlier guard always passes.
                if f > 0 and (cum[ci], round(f, 1)) not in seen:
                    seen.add((cum[ci], round(f, 1)))
                    pts.append((cum[ci], f))
            except Exception:
                continue
    if len(pts) < 3:
        return None, 0.0
    pts.sort()
    n = len(pts)
    slopes = []
    for i in range(n):
        ci, ti = pts[i]
        for j in range(i + 1, n):
            dc = pts[j][0] - ci
            if dc > 2000.0:
                slopes.append((pts[j][1] - ti) / dc)
    if not slopes:
        return None, 0.0
    slopes.sort()
    a = slopes[len(slopes) // 2]
    if not (0.3 <= a <= 3.0):
        return None, 0.0
    s = 1.0 / a
    res = sorted(t - c / s for c, t in pts)
    # H0 = smallest residual, corroborated: the flushest box top across
    # all pages sits ~at the ad-header height (in-page offsets are >= 0).
    # Live-browser verified on 2 chapters (both H0=275.0 exactly).
    # Guardrails: need a 2nd residual within 25px (rejects single
    # misassigned-overlay outliers), H0 within [0, 600] (ad headers are
    # small; larger means no flush box exists), else fall back below.
    h0 = None
    h0_raw = None
    if res:
        lo = res[0]
        if 0.0 <= lo <= 600.0 and sum(1 for r in res if r <= lo + 25.0) >= 2:
            h0_raw = lo
            h0 = min(lo, 275.0)
    if h0 is None:
        k = min(7, len(res))
        h0_raw = sorted(res[:max(k, 1)])[max(k, 1) // 2] if res else 0.0
        h0 = min(h0_raw, 275.0)
        print(f"   [!] H0 fallback (no corroborated flush box).")
    h0 = max(0.0, min(600.0, h0))
    # Snap zone: H0 is live-verified at 275.0. A raw estimate within ±25
    # of it is estimator wobble (box depths/site re-OCR shift run to run),
    # NOT a different header — snapping kills the ±16px uniform jitter
    # between scrapes of the same chapter. Genuinely different headers
    # (raw far from 275) still pass through untouched.
    try:
        _raw_f = float(h0_raw) if h0_raw is not None else None
    except Exception:
        _raw_f = None
    if _raw_f is not None and abs(_raw_f - 275.0) <= 25.0 and h0 != 275.0:
        print(f"   [i] H0 snap: raw={_raw_f:.1f} within 25 of verified 275 -> using 275.0")
        h0 = 275.0
    print(f"   [i] Vertical geometry: s={s:.4f} H0 raw={h0_raw:.1f} capped={h0:.1f}css "
          f"({len(pts)} overlays, {len(slopes)} pairs)")
    return s, h0


def estimate_header_and_gaps(page_texts: dict, png_dims: list,
                             scale: float) -> tuple[float, float]:
    """Estimate ad-header H0 + per-page gap g (CSS px) from the data.

    Residual R = top - cum_png/scale. Under the model R = H0 + p*g + r
    (r = true in-page offset >= 0), the lower envelope of R over pages
    reveals H0 (intercept) and g (slope) via robust statistics.
    """
    cum = [0.0]
    for _, h in png_dims:
        cum.append(cum[-1] + (h or 0) / scale)
    per_page_min: dict[int, float] = {}
    for idx, arr in (page_texts or {}).items():
        if not arr or idx < 0 or idx >= len(png_dims):
            continue
        try:
            per_page_min[int(idx)] = min(
                float(t.get("css_top", 0) or 0) - cum[int(idx)] for t in arr)
        except Exception:
            continue
    if len(per_page_min) < 2:
        h0 = min(per_page_min.values()) if per_page_min else 0.0
        return max(0.0, min(600.0, h0)), 0.0
    items = sorted(per_page_min.items())
    slopes = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            dp = items[j][0] - items[i][0]
            if 0 < dp <= 10:
                slopes.append((items[j][1] - items[i][1]) / dp)
    slopes.sort()
    g = slopes[len(slopes) // 2] if slopes else 0.0
    g = max(0.0, min(10.0, g))
    resids = sorted(r - g * p for p, r in items)
    h0 = resids[max(0, int(len(resids) * 0.10))]
    h0 = max(0.0, min(600.0, h0))
    print(f"   [i] Header H0={h0:.1f}css + gap g={g:.2f}css/page "
          f"({len(items)} pages)")
    return h0, g


def calibrate_scale_from_tops(page_texts: dict, png_dims: list) -> float | None:
    """Fallback scale when real CSS measurements are unavailable (spider path).

    The site's overlay `top` values live in the browser's CSS-pixel space,
    which includes inter-image gaps, so dividing PNG size by an assumed
    rendered width (e.g. 800px) misplaces page boundaries and drops texts.
    Instead, derive the uniform scale from vertical geometry:
        scale = total_png_height / document_css_height
    where document_css_height ~= max overlay top + its height + margin.
    Returns None when there are no overlays to calibrate from.
    """
    all_ov = [t for v in page_texts.values() for t in v]
    if not all_ov or not png_dims:
        return None
    max_top = max(t.get("css_top", 0) for t in all_ov)
    max_h = max(t.get("css_height", 0) for t in all_ov)
    doc_css_h = max_top + max_h + 100.0  # bottom margin
    if doc_css_h <= 0:
        return None
    total_png_h = sum(h for _, h in png_dims)
    s = total_png_h / doc_css_h
    if not (0.3 <= s <= 3.0):
        return None
    print(f"   [i] Calibrated scale from overlay tops: {s:.4f}  "
          f"(PNG {total_png_h}px / CSS {doc_css_h:.1f}px)")
    return s


def assign_texts_to_images(
    page_texts: dict,
    png_dims: list,
    scale: float,
    css_img_heights: list,
    trust_page_id: bool = False,
    geo_tops: dict | None = None,
    layout=None,
) -> list:
    results = []
    total_pages = len(png_dims)
    clamped = 0
    dropped = 0
    # Drift visibility (zero behavior change): per-page counts of texts
    # pinned at top (rel<0 -> y=0, the "climbing" signature) vs bottom.
    # A top-pin rate growing with page index == cumulative pitch drift.
    pin_top: dict[int, int] = {}
    pin_bot: dict[int, int] = {}

    # Measured layout (optional): when layout carries img_tops/img_heights
    # matching total_pages, use MEASURED tops as absolute doc positions
    # (they already include any header offset, so h0=0 in this branch).
    # Otherwise fall through to the EXACT legacy path below (byte-identical).
    _m_tops: list | None = None
    _m_heights: list | None = None
    _m_first_top: float | None = None
    _use_measured = False
    if layout is not None and total_pages > 0:
        try:
            if isinstance(layout, dict):
                _raw_tops = layout.get("img_tops")
                _raw_heights = layout.get("img_heights")
                _raw_first = layout.get("first_img_top", None)
            else:
                _raw_tops = getattr(layout, "img_tops", None)
                _raw_heights = getattr(layout, "img_heights", None)
                _raw_first = getattr(layout, "first_img_top", None)
            if (_raw_tops is not None and _raw_heights is not None
                    and len(_raw_tops) == total_pages
                    and len(_raw_heights) == total_pages):
                _m_tops = [float(x) for x in _raw_tops]
                _m_heights = [float(x) for x in _raw_heights]
                # Degenerate guard: matching length is not enough — all-zero
                # heights (unloaded images) or NaN/inf would collapse every
                # span to a point. Fall back to legacy instead.
                import math as _m
                if (not all(_m.isfinite(t) for t in _m_tops)
                        or not all(_m.isfinite(h) and h > 0
                                   for h in _m_heights)):
                    raise ValueError("degenerate measured layout")
                try:
                    _m_first_top = (float(_raw_first)
                                    if _raw_first is not None else float(_m_tops[0]))
                except Exception:
                    _m_first_top = float(_m_tops[0])
                _use_measured = True
        except Exception:
            _m_tops = None
            _m_heights = None
            _m_first_top = None
            _use_measured = False

    if _use_measured:
        # Measured tops are absolute -> no H0 added (h0=0 for mapping).
        # H0 below is for LOGGING only (first image top clamped to [0,600]).
        h0, gap = 0.0, 0.0
        try:
            _h0_log = float(_m_first_top)
        except Exception:
            _h0_log = 0.0
        h0_log = max(0.0, min(600.0, _h0_log))
    elif trust_page_id and not any(h > 0 for h in (css_img_heights or [])):
        # No measured CSS sizes (spider path): header offset H0 (ad block
        # above the first image) shifts every top down. Estimate uniform
        # scale + H0 per chapter from the tops themselves (live-verified).
        sv, h0est = estimate_vertical_geometry(page_texts, png_dims, geo_tops)
        if sv:
            scale = sv
            h0, gap = h0est, 0.0
        else:
            h0, gap = estimate_header_and_gaps(page_texts, png_dims, scale)
        h0_log = h0
    else:
        h0, gap = 0.0, 0.0
        h0_log = h0

    # Static layout geometry (live-browser verified on 2 chapters):
    # the site authors overlays in a ~970px container; page images render
    # at min(natural, 940) centered inside it. Overlay `left` is the box
    # CENTER in container space (translateX(-50%)), `top` is exact.
    import statistics as _st
    x_scale, x_offset = scale, 0.0
    try:
        _nat_ws = [w for w, _ in (png_dims or []) if w and w > 0]
        if _nat_ws:
            _med_nat = _st.median(_nat_ws)
            _displayed = min(_med_nat, 940.0)
            if _displayed > 0:
                x_scale = _med_nat / _displayed
                x_offset = max(0.0, (970.0 - _displayed) / 2.0)
                if x_offset > 0:
                    print(f"   [i] X-offset: container 970css vs "
                          f"page {_displayed:.0f}css -> "
                          f"shift {x_offset:.1f}css, s_x={x_scale:.4f}")
    except Exception:
        x_scale, x_offset = scale, 0.0

    try:
        import statistics as _st_ymap
        _med_nat_ymap = _st_ymap.median([w for w, _ in (png_dims or []) if w and w > 0])
    except Exception:
        _med_nat_ymap = 0
    print(f"   [i] Y-map: sv={scale:.4f} H0={h0_log:.1f} gap={gap:.2f} med_nat={_med_nat_ymap:.0f} pages={total_pages}")

    if _use_measured:
        # MEASURED tops are absolute doc positions (header already included).
        css_cumulative = ([float(x) for x in _m_tops]
                          + [float(_m_tops[-1]) + float(_m_heights[-1])])
    else:
        css_cumulative = [0.0]
    # REVERTED (regression): per-page s_xi pitch/Y moved ALL texts on real
    # chapters. The live-verified sv model (H0=275, dx=0.0, Y ±2.3px) stays
    # the truth until real-chapter numbers prove otherwise. We only LOG the
    # width-derived scale beside sv now, to diagnose drift with data.
    try:
        _uniq_ws = [w for w, _ in (png_dims or []) if w and w > 0]
        import statistics as _st2
        _med_w = _st2.median(_uniq_ws) if _uniq_ws else 0
        _s_x = (_med_w / min(_med_w, 940.0)) if _med_w > 0 else 0
        print(f"   [i] Pitch scales: sv={scale:.4f} vs width-derived={_s_x:.4f}")
    except Exception:
        pass
    if not _use_measured:
        for i in range(total_pages):
            if i < len(css_img_heights) and css_img_heights[i] > 0:
                css_cumulative.append(css_cumulative[-1] + css_img_heights[i])
            else:
                css_cumulative.append(
                    css_cumulative[-1] + png_dims[i][1] / scale + gap)

    # Containment verification (LOG ONLY, never remap/drop): count overlays
    # whose css_top falls outside their id-page span [start, end].
    try:
        _ct_out = 0
        _ct_tot = 0
        for _ci, _arr in (page_texts or {}).items():
            try:
                _pi = int(_ci)
            except Exception:
                continue
            if _pi < 0 or _pi >= total_pages or not _arr:
                continue
            if _use_measured:
                _s = float(_m_tops[_pi])
                _e = _s + float(_m_heights[_pi])
            else:
                _s = css_cumulative[_pi] + h0
                _e = css_cumulative[_pi + 1] + h0
            for _ov in _arr:
                try:
                    _t = float(_ov.get("css_top", 0) or 0)
                except Exception:
                    continue
                _ct_tot += 1
                if _t < _s or _t > _e:
                    _ct_out += 1
        print(f"   [i] containment: {_ct_out}/{_ct_tot} outside id-page")
    except Exception:
        pass

    for page_idx in range(total_pages):
        page_num = page_idx + 1
        if _use_measured:
            # MEASURED: absolute tops already include header -> no H0 added;
            # per-page pitch from measured heights.
            page_css_start = float(_m_tops[page_idx])
            page_css_end = page_css_start + float(_m_heights[page_idx])
        else:
            page_css_start = css_cumulative[page_idx] + h0
            page_css_end = css_cumulative[page_idx + 1] + h0
        page_width, page_height = png_dims[page_idx]
        if page_width <= 0:
            # PIL dimension probe failed: fall back to the chapter's
            # displayed width so the X-safety clamps below still hold
            # instead of storing unbounded boxes.
            try:
                page_width = float(_displayed or 800.0)
            except Exception:
                page_width = 800.0

        texts = page_texts.get(page_idx, [])
        page_texts_out = []

        for ov in texts:
            css_top = ov["css_top"]

            if css_top < page_css_start or css_top > page_css_end:
                if trust_page_id:
                    # Page index comes from the overlay id (split_N) — trust it
                    # and clamp inside the page instead of deleting the text.
                    rel_css_top = css_top - page_css_start
                    clamped += 1
                else:
                    dropped += 1
                    continue
            else:
                rel_css_top = css_top - page_css_start

            page_css_h = page_css_end - page_css_start
            ov_css_h = ov.get("css_height", 0) or 0
            max_rel = max(0.0, page_css_h - ov_css_h)
            if rel_css_top < 0:
                pin_top[page_idx] = pin_top.get(page_idx, 0) + 1
            elif rel_css_top > max_rel:
                pin_bot[page_idx] = pin_bot.get(page_idx, 0) + 1
            rel_css_top = min(max(rel_css_top, 0.0), max_rel)

            # Box width = style max-width (canonical block geometry, as the
            # site authors it; live deviations are prior drag-edits, not
            # to be cloned). Center/top are exact; Arabic is re-fitted
            # at render time by the exporter anyway.
            fit_css_w = float(ov.get("css_width", 0) or 0)
            css_left_fit = float(ov["css_left_center"]) - fit_css_w / 2.0

            x_px = round(max(0.0, css_left_fit - x_offset) * x_scale, 2)
            y_px = round(rel_css_top * scale, 2)
            x_center_px = round(
                max(0.0, ov["css_left_center"] - x_offset) * x_scale, 2)
            width_px = round(fit_css_w * x_scale, 2)
            height_px = round(ov["css_height"] * scale, 2)
            font_px = round(ov["css_font_size"] * scale, 2)

            x_px = max(0.0, x_px)
            if trust_page_id and x_px + width_px > page_width:
                x_px = max(0.0, page_width - width_px)
            # X-safety: box must fit inside the page (contract-admission has
            # max-width boxes wider than narrow 700px pages; martial 1008px
            # pages never trigger this, so its behaviour is unchanged).
            if page_width > 0 and width_px > page_width:
                width_px = float(page_width)
                x_px = 0.0
            if page_width > 0 and x_px + width_px > page_width:
                width_px = max(0.0, float(page_width) - x_px)

            # Site typography (passed through; server merges it over defaults):
            # exact text color (black/white/...) + outline from text-shadow.
            site_font = max(6.0, font_px)
            site_stroke_w = round((ov.get("css_stroke_width") or 0) * scale, 2)
            site_style = {
                "font": "Hayah",
                "font_size": int(round(site_font)),
                "line_height": 1.2,
                "color": (ov.get("css_color") or "#000000").lower(),
                "stroke_color": (ov.get("css_stroke_color") or "#ffffff").lower(),
                "stroke_width": site_stroke_w if site_stroke_w > 0 else 1.0,
                "stroke_enabled": True,
                "align": "center",
                "rotation": 0,
            }

            page_texts_out.append({
                "id": ov["id"],
                "page": page_num,
                "original_text": ov["original_text"],
                "arabic_text": ov["arabic_text"],
                "x": x_px,
                "y": y_px,
                "x_center": x_center_px,
                "width": width_px,
                "height": height_px,
                "font_size_px": site_font,
                "line_height": ov["line_height"],
                "scale_factor": round(scale, 5),
                "style": site_style,
            })

        results.append({
            "page": page_num,
            "filename": f"page_{page_num:03d}.png",
            "width": page_width,
            "height": page_height,
            "texts": page_texts_out,
        })

    if clamped:
        print(f"   [!] {clamped} texts clamped inside their id-page (fallback mapping).")
    if dropped:
        print(f"   [!] {dropped} texts dropped (out of range, untrusted page).")
    if pin_top or pin_bot:
        pages_hit = sorted(set(pin_top) | set(pin_bot))
        detail = ", ".join(
            f"p{p + 1}:top{pin_top.get(p, 0)}/bot{pin_bot.get(p, 0)}"
            for p in pages_hit)
        print(f"   [i] Pin report (per page): {detail}")
        top_total = sum(pin_top.values())
        if top_total:
            late = sum(c for p, c in pin_top.items()
                       if p >= total_pages // 2)
            print(f"   [{'!' if late * 2 >= top_total and top_total >= 3 else 'i'}] "
                  f"Top-pinned texts: {top_total} total, {late} in LATER half "
                  f"({'drift signature — pitch overshoots down the chapter' if late * 2 >= top_total and top_total >= 3 else 'scattered — no cumulative drift'})")

    # Self-check: Y distribution must cover the page, not hug the bottom.
    try:
        fracs = []
        for p in results:
            ph = p.get("height") or 0
            if ph > 0:
                for t in p.get("texts", []):
                    fracs.append((t.get("y", 0) or 0) / ph)
        if fracs:
            fracs.sort()
            med = fracs[len(fracs) // 2]
            top_half = sum(1 for f in fracs if f < 0.5) / len(fracs)
            print(f"   [i] Y-check: median {med:.2f}, top-half {top_half:.0%}")
            if med > 0.8:
                print("   [!] Y-check FAILED: texts hug the bottom — mapping suspect.")
    except Exception:
        pass

    return results


def process_chapter(driver, url: str, slug: str, ch_num: str, base_dir: Path,
                    should_cancel=None):
    # Legacy Selenium path (fallback). Requires selenium + engine.
    if not _SELENIUM_AVAILABLE or not _ENGINE_AVAILABLE or parse_chapter_full is None:
        print("[!] Legacy Selenium path unavailable (selenium not installed).")
        return None
    print(f"\n{'='*65}")
    print(f"  Chapter {ch_num}  -  {url}")
    print(f"{'='*65}")

    driver.get(url)
    try:
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".reading-content"))
        )
    except TimeoutException:
        print("[!] Page load timeout.")
        try:
            dbg = base_dir / f"chapter_{ch_num}" / "_debug"
            dbg.mkdir(parents=True, exist_ok=True)
            driver.save_screenshot(str(dbg / "timeout.png"))
            (dbg / "timeout.html").write_text(
                driver.page_source or "", encoding="utf-8", errors="replace")
            print(f"   [i] Debug snapshot saved: {dbg} "
                  f"(open timeout.png to see Cloudflare wall / 404 / blank)")
        except Exception:
            pass
        return None

    result = parse_chapter_full(driver)
    if result is None:
        return None
    image_urls, page_texts, rendered_w, css_heights, title = result

    total_texts = sum(len(v) for v in page_texts.values())
    print(f"[+] Images: {len(image_urls)}  |  Overlays: {total_texts}  |"
          f"  Rendered CSS width: {rendered_w:.1f}px")

    if not image_urls:
        print("[-] No images found - skipping.")
        return None

    ch_dir = base_dir / f"chapter_{ch_num}"
    raw_dir = ch_dir / "_raw"
    pages_dir = ch_dir / "pages"
    raw_dir.mkdir(parents=True, exist_ok=True)
    pages_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[v] Downloading {len(image_urls)} images...")
    raw_paths = download_images(image_urls, raw_dir)
    if not raw_paths:
        print("[-] Download failed.")
        return None
    _throw_if_cancelled(should_cancel)

    print(f"\n[>] Converting to PNG...")
    png_paths = convert_all_to_png(raw_paths, pages_dir)
    if not png_paths:
        print("[-] No PNG files produced.")
        return None

    print(f"\n[>] Computing dimensions...")
    png_dims = get_png_dimensions(png_paths)
    actual_w = png_dims[0][0] if png_dims else 0
    scale = compute_scale(rendered_w, actual_w)

    # Warn if pages have varying widths
    widths = set(w for w, h in png_dims)
    if len(widths) > 1:
        print(f"   [!] Warning: pages have different widths {widths}. Scale may be inaccurate for some pages.")

    print(f"\n[>] Assigning text coordinates per page...")
    pages_data = assign_texts_to_images(page_texts, png_dims, scale, css_heights,
                                        trust_page_id=True)
    assigned_texts = sum(len(p["texts"]) for p in pages_data)

    print(f"\n[>] Saving chapter data...")
    chapter_data = {
        "title": title,
        "url": url,
        "slug": slug,
        "chapter": ch_num,
        "total_images": len(png_dims),
        "total_texts": assigned_texts,
        "overlays_found": total_texts,
        "scale_factor": round(scale, 5),
        "pages": pages_data,
    }

    json_path = ch_dir / "chapter_data.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(chapter_data, f, ensure_ascii=False, indent=2)
    print(f"   [+] chapter_data.json  ({assigned_texts}/{total_texts} texts across {len(pages_data)} pages)")

    total_pages_with_text = sum(1 for p in pages_data if p["texts"])
    print(f"\n[+] Chapter {ch_num} complete!  Folder: {ch_dir.resolve()}")
    print(f"    Title: {title or '(unknown)'}")
    print(f"    Total pages: {len(png_dims)}  |  Pages with text: {total_pages_with_text}")
    print(f"    Total texts: {assigned_texts}/{total_texts}  |  Scale: {scale:.4f}")

    return ch_dir


class ScrapeCancelled(Exception):
    """Raised at stage boundaries when the user cancels a scrape task."""
    pass


def _throw_if_cancelled(should_cancel):
    try:
        if should_cancel is not None and should_cancel():
            raise ScrapeCancelled("cancelled by user")
    except ScrapeCancelled:
        raise
    except Exception:
        pass


def process_chapter_spider(url: str, slug: str, ch_num: str, base_dir: Path,
                           should_cancel=None):
    """New primary path: Scrapling spider + pipeline (no Selenium)."""
    from .spider import fetch_chapter_page
    from .pipeline import (
        download_images_scraping,
        convert_all_to_png,
        get_png_dimensions,
    )

    print(f"\n{'='*65}")
    print(f"  Chapter {ch_num}  -  {url}  [spider]")
    print(f"{'='*65}")

    result = fetch_chapter_page(url)
    if result is None:
        print("[-] Spider fetch failed.")
        return None
    _throw_if_cancelled(should_cancel)
    if len(result) == 7:
        image_urls, page_texts, rendered_w, css_heights, title, geo_tops, layout = result
    elif len(result) == 6:
        image_urls, page_texts, rendered_w, css_heights, title, geo_tops = result
        layout = {"img_tops": [], "img_heights": [],
                  "first_img_top": 0.0, "container_w": 0.0}
    else:  # backward compat with 5-tuple callers
        image_urls, page_texts, rendered_w, css_heights, title = result
        geo_tops = {}
        layout = {"img_tops": [], "img_heights": [],
                  "first_img_top": 0.0, "container_w": 0.0}

    total_texts = sum(len(v) for v in page_texts.values())
    print(f"[+] Images: {len(image_urls)}  |  Overlays: {total_texts}  |"
          f"  Rendered CSS width: {rendered_w:.1f}px")

    if not image_urls:
        print("[-] No images found - skipping.")
        return None

    ch_dir = base_dir / f"chapter_{ch_num}"
    raw_dir = ch_dir / "_raw"
    pages_dir = ch_dir / "pages"
    raw_dir.mkdir(parents=True, exist_ok=True)
    pages_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[v] Downloading {len(image_urls)} images...")
    raw_paths = download_images_scraping(image_urls, raw_dir)
    if not raw_paths:
        print("[-] Download failed.")
        return None
    _throw_if_cancelled(should_cancel)

    print(f"\n[>] Converting to PNG...")
    png_paths = convert_all_to_png(raw_paths, pages_dir)
    if not png_paths:
        print("[-] No PNG files produced.")
        return None

    print(f"\n[>] Computing dimensions...")
    png_dims = get_png_dimensions(png_paths)
    actual_w = png_dims[0][0] if png_dims else 0
    # Uniform scale + header from the tops themselves (live-verified);
    # widths-only and legacy estimators are fallbacks.
    scale, _h0geo = estimate_vertical_geometry(page_texts, png_dims, geo_tops)
    if scale is None:
        scale = estimate_horizontal_scale(page_texts, png_dims)
    if scale is None:
        scale = calibrate_scale_from_tops(page_texts, png_dims)
    if scale is None:
        scale = compute_scale(rendered_w, actual_w)

    # Warn if pages have varying widths
    widths = set(w for w, h in png_dims)
    if len(widths) > 1:
        print(f"   [!] Warning: pages have different widths {widths}. Scale may be inaccurate for some pages.")

    print(f"\n[>] Assigning text coordinates per page...")
    pages_data = assign_texts_to_images(page_texts, png_dims, scale, css_heights,
                                        trust_page_id=True, geo_tops=geo_tops,
                                        layout=layout)
    assigned_texts = sum(len(p["texts"]) for p in pages_data)
    _throw_if_cancelled(should_cancel)

    print(f"\n[>] Saving chapter data...")
    chapter_data = {
        "title": title,
        "url": url,
        "slug": slug,
        "chapter": ch_num,
        "total_images": len(png_dims),
        "total_texts": assigned_texts,
        "overlays_found": total_texts,
        "scale_factor": round(scale, 5),
        "pages": pages_data,
    }

    json_path = ch_dir / "chapter_data.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(chapter_data, f, ensure_ascii=False, indent=2)
    print(f"   [+] chapter_data.json  ({assigned_texts}/{total_texts} texts across {len(pages_data)} pages)")

    total_pages_with_text = sum(1 for p in pages_data if p["texts"])
    print(f"\n[+] Chapter {ch_num} complete!  Folder: {ch_dir.resolve()}")
    print(f"    Title: {title or '(unknown)'}")
    print(f"    Total pages: {len(png_dims)}  |  Pages with text: {total_pages_with_text}")
    print(f"    Total texts: {assigned_texts}/{total_texts}  |  Scale: {scale:.4f}")

    return ch_dir


def _resolve_base_root() -> Path:
    """Manga base folder WITHOUT importing the server (no cycles).

    Priority: env MANGA_OUTPUT_DIR > ../config.json > ./output.
    Stale absolute paths from another machine are ignored safely.
    """
    import os
    env = (os.environ.get("MANGA_OUTPUT_DIR", "") or "").strip()
    if env:
        return Path(env)
    try:
        cfg_path = Path(__file__).parent.parent / "config.json"
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            if cfg.get("output_dir"):
                p = Path(cfg["output_dir"])
                if p.exists():
                    return p
    except Exception:
        pass
    return Path.cwd() / "output"


def scrape_chapter(url: str, headless: bool = True, browser: str = "brave",
                   should_cancel=None):
    slug, ch_num = extract_chapter_info(url)
    if not slug:
        print("[!] Could not parse chapter URL.")
        return None
    if not re.fullmatch(r"[a-z0-9-]+", slug):
        print(f"[!] Invalid slug: {slug!r}")
        return None

    base_dir = _resolve_base_root() / sanitize(slug)
    base_dir.mkdir(parents=True, exist_ok=True)

    # Preflight: report what can actually run here (see server/UI logs).
    spider_reason = ""
    try:
        from .engine import check_browser_env
        env = check_browser_env()
        print(f"   [i] Engines: scrapling={env.get('scrapling')} "
              f"spider_browser={env.get('scrapling_browser')} "
              f"brave={bool(env.get('brave'))} "
              f"chrome={bool(env.get('chrome'))}")
        if env.get("hint"):
            print(f"   [i] Hint: {env['hint']}")
    except Exception:
        pass

    # Attempt 1: new Scrapling spider + pipeline path
    try:
        print("\n[i] Trying Scrapling spider path...")
        ch_dir = process_chapter_spider(url, slug, ch_num, base_dir,
                                        should_cancel=should_cancel)
        if ch_dir is not None:
            return ch_dir
        spider_reason = "spider returned no data"
        print("   [!] Spider path returned None - falling back to Selenium.")
    except ScrapeCancelled:
        raise
    except Exception as e:
        spider_reason = str(e)[:300]
        print(f"   [!] Spider path failed ({spider_reason}) - falling back to Selenium.")

    # Attempt 2: legacy Selenium fallback
    if not _SELENIUM_AVAILABLE or setup_driver is None:
        raise RuntimeError(
            "فشل المسار الأساسي (spider): " + (spider_reason or "unknown") +
            ". ولا يوجد مسار Selenium احتياطي على هذا الجهاز.")
    _throw_if_cancelled(should_cancel)
    print(f"\n[i] Starting {browser} (headless={headless})...")
    try:
        driver = setup_driver(headless=headless, browser=browser)
    except Exception as e:
        raise RuntimeError(
            "فشل المسار الأساسي (spider): " + (spider_reason or "unknown") +
            f". وفشل الاحتياطي (selenium): {e}")
    try:
        ch_dir = process_chapter(driver, url, slug, ch_num, base_dir,
                                 should_cancel=should_cancel)
        return ch_dir
    finally:
        driver.quit()
