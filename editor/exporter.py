import json
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

# Arabic text reshaping for correct RTL rendering in Pillow
try:
    import arabic_reshaper
    from bidi.algorithm import get_display as bidi_display
    _HAS_ARABIC_SHAPING = True
except ImportError:
    _HAS_ARABIC_SHAPING = False


FONTS_DIR = Path(__file__).parent.parent / "fonts"


def get_available_fonts() -> list[dict]:
    fonts = []
    if not FONTS_DIR.exists():
        return fonts

    for f in sorted(FONTS_DIR.iterdir()):
        if f.suffix.lower() in (".ttf", ".otf", ".ttc"):
            fonts.append({
                "name": f.stem,
                "filename": f.name,
                "path": str(f),
                "url": str(f.relative_to(FONTS_DIR)).replace("\\", "/"),
            })

    user_dir = FONTS_DIR / "_user"
    if user_dir.exists():
        for f in sorted(user_dir.iterdir()):
            if f.suffix.lower() in (".ttf", ".otf", ".ttc"):
                name = f.stem
                if not any(fo["name"] == name for fo in fonts):
                    fonts.append({
                        "name": name,
                        "filename": f.name,
                        "path": str(f),
                        "url": str(f.relative_to(FONTS_DIR)).replace("\\", "/"),
                        "user_uploaded": True,
                    })

    return fonts


FALLBACK_FONTS = [
    r"C:\Windows\Fonts\tradbdo.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
]

FONT_SCALE = 1.0
AUTO_FIT_MIN_FONT = 30
AUTO_FIT_MAX_FONT = 60
AUTO_FIT_TARGET_FILL = 0.60
TEXT_PADDING = 6

# Latin measuring correction: the export Latin font (Arial) is ~1.25x wider
# than the site font (measured 1.22-1.29). Without it, an extra wrap line
# appears and the shrink loop collapses sizes needlessly.
LATIN_WIDTH_FACTOR = 0.8

# Smart-fit result boost (user-tuned): fitted sizes are enlarged by
# this factor. Never shrinks.
AUTO_FIT_BOOST = 1.35

# Global Arabic minimum: auto-fit NEVER goes below this (user rule).
# Manual overrides still win — explicit intent beats auto.
ARABIC_MIN_FONT = 14


def find_font_path(font_name: str) -> Optional[str]:
    for f in get_available_fonts():
        if f["name"] == font_name or f["filename"] == font_name:
            return f["path"]

    for ext in (".ttf", ".otf", ".ttc"):
        try_path = FONTS_DIR / f"{font_name}{ext}"
        if try_path.exists():
            return str(try_path)
        user_path = FONTS_DIR / "_user" / f"{font_name}{ext}"
        if user_path.exists():
            return str(user_path)

    for fb in FALLBACK_FONTS:
        if Path(fb).exists():
            return fb

    return None


# Font cache: TTF parsing per (path, size) happens ONCE. measure_fitted
# used to re-parse the font on every shrink step (thousands of redundant
# loads per chapter). Fonts are immutable per size — cache forever.
_FONT_CACHE: dict = {}


def _load_font(font_path: str, size: int):
    key = (font_path, int(size))
    font = _FONT_CACHE.get(key)
    if font is None:
        try:
            font = ImageFont.truetype(font_path, int(size))
        except Exception:
            return None
        # Bound growth: sizes 8..120 x a handful of fonts — tiny.
        if len(_FONT_CACHE) < 4096:
            _FONT_CACHE[key] = font
    return font


def _parse_color(val) -> tuple:
    if not val:
        return (0, 0, 0)
    val = str(val).strip()
    if val.startswith("#"):
        h = val.lstrip("#")
        if len(h) == 6:
            return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
        if len(h) == 3:
            return tuple(int(h[i]+h[i], 16) for i in range(3))
        return (0, 0, 0)
    m_rgb = __import__('re').match(r'rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)', val)
    if m_rgb:
        return (int(m_rgb.group(1)), int(m_rgb.group(2)), int(m_rgb.group(3)))
    return (0, 0, 0)


def _has_arabic(text: str) -> bool:
    if not text:
        return False
    return bool(__import__('re').search(
        '[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]', text))


LATIN_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
]


def _pick_font_path(font_name: str, text: str) -> Optional[str]:
    """Script-aware font: Arabic -> requested font (Hayah);
    Latin-only -> Arial-like font with site-like metrics
    (Hayah's Latin glyphs are ~28% narrower than the site font)."""
    if _has_arabic(text):
        p = find_font_path(font_name)
        if p:
            return p
        # Requested Arabic font missing (e.g. fresh GitHub clone):
        # use ANY available font rather than dropping the text.
        for f in get_available_fonts():
            if Path(f["path"]).exists():
                print(f"   [i] Font '{font_name}' missing, using '{f['name']}' instead.")
                return f["path"]
        return None
    for cand in LATIN_FONT_CANDIDATES:
        if Path(cand).exists():
            return cand
    return find_font_path(font_name)


def has_any_font() -> bool:
    """True if at least one usable font file exists (bundled/user/system)."""
    if get_available_fonts():
        return True
    return any(Path(c).exists() for c in LATIN_FONT_CANDIDATES)


def _wrap_lines(draw, text: str, font, max_w: float) -> list[str]:
    """Word-wrap (Arabic-aware) to max_w. Returns display-ready lines."""
    raw_lines = (text or "").split("\n")
    wrapped = []
    for raw_line in raw_lines:
        if not raw_line.strip():
            wrapped.append("")
            continue
        _lat = 1.0 if _has_arabic(raw_line) else LATIN_WIDTH_FACTOR
        reshaped_words = _reshape_arabic_no_bidi(raw_line).split()
        current_words = []
        for w in reshaped_words:
            test_words = current_words + [w]
            test = " ".join(test_words)
            try:
                tb = draw.textbbox((0, 0), test, font=font)
                tw = (tb[2] - tb[0]) * _lat
            except Exception:
                tw = 0
            if tw > max_w and current_words:
                line_text = " ".join(current_words)
                if _HAS_ARABIC_SHAPING:
                    try:
                        line_text = bidi_display(line_text)
                    except Exception:
                        pass
                wrapped.append(line_text)
                current_words = [w]
            else:
                current_words = test_words
        if current_words:
            line_text = " ".join(current_words)
            if _HAS_ARABIC_SHAPING:
                try:
                    line_text = bidi_display(line_text)
                except Exception:
                    pass
            wrapped.append(line_text)
    return wrapped or [""]


def _measure_block(draw, wrapped: list[str], font, line_height_factor: float,
                   line_gap: int, fallback_size: int):
    """Measure wrapped lines. Returns (line_widths, total_h)."""
    line_widths = []
    line_heights = []
    max_line_h = 0
    for line in wrapped:
        if not line:
            line_heights.append(0)
            line_widths.append(0)
            continue
        try:
            tb = draw.textbbox((0, 0), line, font=font)
            lw = (tb[2] - tb[0]) * (1.0 if _has_arabic(line) else LATIN_WIDTH_FACTOR)
            lh = tb[3] - tb[1]
        except Exception:
            lw = 0
            lh = fallback_size
        line_widths.append(lw)
        line_heights.append(lh)
        max_line_h = max(max_line_h, lh)
    base_line_h = max(max_line_h, int(round(font.size * line_height_factor)), 1)
    total_text_h = (base_line_h * len(wrapped)) + (max(len(wrapped) - 1, 0) * line_gap)
    return line_widths, line_heights, base_line_h, total_text_h


def measure_fitted(draw, text: str, font_path: str, start_size: int,
                   max_w: float, max_h: float, line_height_factor: float = 1.2,
                   line_gap: int = 2, measure_stroke: float = 0.0,
                   min_size: int = 8) -> tuple:
    """Shared fitter used by render AND box-autofit (single truth).

    Starts at start_size; EN-only text never goes below start_size
    (site proves it fits); translated text shrinks to min_size if needed.
    Returns (fitted_size, font_or_None, wrapped, line_widths, total_h).
    """
    floor = start_size if not _has_arabic(text) else max(min_size, ARABIC_MIN_FONT)
    fitted = max(start_size, min_size)
    font = None
    wrapped, line_widths, line_heights, base_lh, total_h = [""], [0], [0], 0, 0
    while True:
        font = _load_font(font_path, fitted)
        if font is None:
            break
        wrapped = _wrap_lines(draw, text, font, max_w)
        line_widths, line_heights, base_lh, total_h = _measure_block(
            draw, wrapped, font, line_height_factor, line_gap, fitted)
        widest = max(line_widths) if line_widths else 0
        if total_h <= max_h and widest + 2 * measure_stroke <= max_w:
            break
        if fitted <= floor:
            break
        fitted -= 1
    return fitted, font, wrapped, line_widths, line_heights, base_lh, total_h


def boost_fitted(draw, text: str, font_path: str, fitted: int,
                 max_w: float, max_h: float, line_height_factor: float = 1.2,
                 line_gap: int = 2) -> int:
    """User rule: fit FIRST (box growth + shrink), then grow the result by
    AUTO_FIT_BOOST unconditionally. No re-verification against the box —
    the user explicitly wants the +25% on the fitted number itself.
    """
    try:
        return max(fitted, min(120, int(round(fitted * AUTO_FIT_BOOST))))
    except Exception:
        return fitted


def _effective_stroke(style: dict, force_stroke: bool = True,
                      export_stroke_width: float = 1.5) -> float:
    """Stroke pixels the truth (render) reserves — autofit must too."""
    try:
        sw = float((style or {}).get("stroke_width", 1))
        enabled = (style or {}).get("stroke_enabled", True)
        if isinstance(enabled, str):
            enabled = enabled.lower() not in ("false", "0", "")
        elif isinstance(enabled, (int, float)):
            enabled = bool(enabled)
        if force_stroke and not enabled:
            return float(export_stroke_width or 0)
        if enabled:
            return max(1.0, sw) if sw < 1 else float(sw)
        return 0.0
    except Exception:
        return 0.0


def _grow_width(draw, text: str, font_path: str, size: int,
                x: float, y: float, w: float, h: float, w_orig: float,
                h_orig: float, page_w: float, page_h: float, texts: list,
                idx: int, lh: float, line_gap: int, margin: float,
                max_grow: float, mstroke: float = 0.0,
                next_top: float = 0.0):
    """Grow the box WIDTH (symmetric around center) to hold `size`.

    Hard caps: page width, same-row neighbor boxes, w_orig*max_grow.
    Width goes FIRST (unwrapping lines reduces the height needed).
    When vertical growth is blocked (next_top), width COMPENSATES: it
    widens toward the single-line width divided by the lines the
    current height allows. Returns (x, w, max_w, pad_x, grew_bool).
    """
    pad_x = min(6.0, max(2.0, w * 0.02))
    pad_y = min(6.0, max(2.0, h * 0.10))
    max_w = max(1, w - pad_x * 2)
    max_h = max(1, h - pad_y * 2)
    try:
        vcap = min(float(page_h or 0) - y - 4.0,
                   float(next_top or 0) - y - float(margin),
                   float(h_orig or h) * float(max_grow))
    except Exception:
        vcap = h + 1.0
    vblocked = vcap <= h + 1.0
    try:
        _, _, _, line_widths, _, _, _th = measure_fitted(
            draw, text, font_path, size, max_w, max_h, lh, line_gap,
            mstroke, size)  # pinned: measure overflow, don't shrink
    except Exception:
        return x, w, max_w, pad_x, False
    widest = max(line_widths) if line_widths else 0
    # Idempotency first: if the size ALREADY fits, touch nothing — growth
    # must never oscillate across repeated fits.
    if _th <= max_h and widest + mstroke * 2 <= max_w:
        return x, w, max_w, pad_x, False
    need_w = widest + mstroke * 2 + pad_x * 2 + 2
    if need_w <= w and not vblocked:
        return x, w, max_w, pad_x, False
    if page_w > 20:
        left_lim, right_lim = 4.0, page_w - 4.0
    else:
        left_lim, right_lim = 4.0, x + w - 4.0
    for j, other in enumerate(texts):
        if j == idx:
            continue
        try:
            ox = float(other.get("x", 0) or 0)
            oy = float(other.get("y", 0) or 0)
            ow = max(20.0, float(other.get("width", 200) or 200))
            oh = max(10.0, float(other.get("height", 60) or 60))
        except Exception:
            continue
        if oy < y + h and y < oy + oh:  # same row: respect horizontally
            if ox + ow <= x:
                left_lim = max(left_lim, ox + ow + margin)
            elif ox >= x + w:
                right_lim = min(right_lim, ox - margin)
    cap_w = min(right_lim - left_lim, w_orig * max_grow)
    if page_w > 20:
        cap_w = min(cap_w, page_w - 8.0)
    # Smart compensation: if vertical growth is blocked (a neighbor sits
    # right below), widen FURTHER so the text fits in the lines the
    # current height allows — width substitutes for height.
    if vblocked and cap_w > w:
        try:
            line_px = max(1.0, float(size) * float(lh) + float(line_gap))
            lines_avail = max(1, int(max_h / line_px))
            fnt0 = _load_font(font_path, int(size))
            if fnt0 is not None:
                total_w = draw.textbbox((0, 0), " ".join(
                    (text or "").split()), font=fnt0)[2]
                w_target = (total_w / lines_avail + mstroke * 2
                            + pad_x * 2 + 4)
                need_w = max(need_w, min(w_target, cap_w))
        except Exception:
            pass
    if cap_w <= w:
        return x, w, max_w, pad_x, False
    w2 = min(need_w, cap_w)
    cx = x + w / 2.0
    x2 = min(max(cx - w2 / 2.0, left_lim), max(left_lim, right_lim - w2))
    if page_w > 20:
        x2 = min(max(x2, 4.0), max(4.0, page_w - w2 - 4.0))
    pad_x2 = min(6.0, max(2.0, w2 * 0.02))
    return x2, w2, max(1, w2 - pad_x2 * 2), pad_x2, True


def _next_top(texts: list, idx: int, x: float, y: float, w: float,
              page_h: float) -> float:
    """Top of the first box below (with horizontal overlap)."""
    next_top = page_h
    for other in texts[idx + 1:]:
        try:
            oy = float(other.get("y", 0) or 0)
            ox = float(other.get("x", 0) or 0)
            ow = max(20.0, float(other.get("width", 200) or 200))
        except Exception:
            continue
        if oy > y and ox < x + w and x < ox + ow:
            next_top = min(next_top, oy)
            break
    return next_top


def _fit_or_grow(draw, text: str, font_path: str, size: int, floor: int,
                 max_w: float, max_h: float, lh: float, line_gap: int,
                 y: float, h: float, h_orig: float, page_h: float,
                 next_top: float, margin: float, max_grow: float,
                 pad_y: float, mstroke: float = 0.0):
    """Grow-first guard: measure the boosted size PINNED (no shrink-back),
    grow the box to hold it (same downward rules, capped at h_orig*max_grow
    total), then fit inside the grown box — shrink only as last resort,
    never below `floor`. Returns (size, h, grew_bool).
    """
    try:
        _, _, _, _, _, _, th = measure_fitted(
            draw, text, font_path, size, max_w, max_h, lh, line_gap,
            mstroke, size)
    except Exception:
        return size, h, False
    if th <= max_h:
        return size, h, False
    need_h = th + pad_y * 2 + 4
    if need_h <= h:
        return size, h, False
    cap_h = min(page_h - y - 4, next_top - y - margin, h_orig * max_grow)
    if cap_h <= h:
        # Box cannot grow: shrink the font to fit, floor = pre-boost size.
        try:
            s, _, _, _, _, _, _ = measure_fitted(
                draw, text, font_path, size, max_w, max_h, lh, line_gap,
                mstroke, floor)
        except Exception:
            return size, h, False
        return max(floor, s), h, False
    h2 = min(need_h, cap_h)
    pad_y2 = min(6.0, max(2.0, h2 * 0.10))
    max_h2 = max(1, h2 - pad_y2 * 2)
    try:
        s2, _, _, _, _, _, _ = measure_fitted(
            draw, text, font_path, size, max_w, max_h2, lh, line_gap,
            mstroke, floor)
    except Exception:
        return size, h, False
    return max(floor, s2), h2, True


def autofit_chapter_boxes(chapter_data: dict, max_grow: float = 2.0,
                          margin: float = 6.0, line_gap: int = 2,
                          force_stroke: bool = True,
                          export_stroke_width: float = 1.5,
                          only_pages: Optional[set] = None) -> dict:
    """Grow boxes (down only) so translated text fits at site size.

    For each text with arabic_text: if it overflows at site size, extend
    the box downward (capped by page bottom, next box top, max_grow×).
    Then fit the font (recorded into style) — shrink only as last resort.
    Mutates chapter_data in place. Returns counts dict.
    """
    grown = shrunk = kept = skipped = boosted = 0
    work = Image.new("RGB", (8, 8), (255, 255, 255))
    draw = ImageDraw.Draw(work)
    for page in chapter_data.get("pages", []):
        if only_pages is not None and page.get("page") not in only_pages:
            continue
        try:
            page_w = float(page.get("width", 0) or 0)
            page_h = float(page.get("height", 0) or 0)
        except Exception:
            continue
        texts = sorted((page.get("texts", []) or []),
                       key=lambda t: float(t.get("y", 0) or 0))
        # NO-PUSH RULE (user): autofit NEVER moves any box's y, no matter
        # how much this or another box grows. Growth is only CAPPED by the
        # next box top (respect without touching). Overlaps from the site
        # stay exactly as the site authored them.
        for idx, t in enumerate(texts):
            arabic = (t.get("arabic_text") or "").strip()
            if not arabic:
                skipped += 1
                continue
            try:
                x = float(t.get("x", 0) or 0)
                y = float(t.get("y", 0) or 0)
                w = max(20.0, float(t.get("width", 200) or 200))
                h = max(10.0, float(t.get("height", 60) or 60))
                style = t.get("style", {}) or {}
                site_size = max(8, int(round(float(
                    style.get("font_size", t.get("font_size_px", 45))))))
                lh = float(style.get("line_height", t.get("line_height", 1.1)) or 1.1)
                font_name = style.get("font", "Hayah")
            except Exception:
                skipped += 1
                continue
            # Hard caps (same as save-time): the box must sit INSIDE its page.
            # Clamp width first, then shift x left (never below 4); only as
            # a last resort shrink width. h_orig/w_orig capture clamped vals.
            if page_w > 20:
                _ox, _ow = x, w
                if w > page_w - 8.0:
                    w = page_w - 8.0
                if x + w > page_w - 4.0:
                    x = page_w - 4.0 - w
                    if x < 4.0:
                        x = 4.0
                        w = max(20.0, page_w - 8.0)
                if x != _ox or w != _ow:
                    t["width"] = round(w, 2)
                    t["x"] = round(x, 2)
            mstroke = _effective_stroke(style, force_stroke, export_stroke_width)
            h_orig = h
            w_orig = w
            text_grew = False
            font_path = _pick_font_path(font_name, arabic)
            if not font_path:
                skipped += 1
                continue
            # Idempotency anchors: repeated fits must converge, never ratchet.
            # _fit_base = pre-boost fitted size from the last fit;
            # _fit_final = the boosted size stored then. If the current size
            # still equals _fit_final, this is a re-fit of unchanged data:
            # restart from _fit_base (not from the boosted size) so the
            # x1.35 applies exactly ONCE. If the user changed the size (or
            # the text), treat it as fresh input.
            try:
                _prev_base = int(style.get("_fit_base") or 0)
            except Exception:
                _prev_base = 0
            try:
                _prev_final = int(style.get("_fit_final") or 0)
            except Exception:
                _prev_final = 0
            if _prev_base > 0 and site_size == _prev_final:
                anchor = _prev_base
            else:
                anchor = site_size
            pad_x = min(6.0, max(2.0, w * 0.02))
            pad_y = min(6.0, max(2.0, h * 0.10))
            max_w, max_h = max(1, w - pad_x * 2), max(1, h - pad_y * 2)
            # Box-aware start: short text in a big bubble grows toward fill
            # instead of sitting tiny at site size.
            nch = max(1, len(" ".join(arabic.split())))
            area_start = max(8, min(120, round(
                0.6 * ((max(w * h, 400.0) / nch) ** 0.5))))
            start_size = min(120, max(anchor, area_start))
            fitted, _, _, _, _, _, _ = measure_fitted(
                draw, arabic, font_path, start_size, max_w, max_h, lh,
                line_gap, mstroke)
            # Next box top (shared by the pre-grow and the overflow guard).
            next_top = _next_top(texts, idx, x, y, w, page_h)
            if fitted >= start_size:
                # Fits (at site size or area size) -> apply the forced +25%
                # on the fitted number itself, ALWAYS (even at site size).
                # Then grow the box if the boost overflows (never clip).
                final = boost_fitted(draw, arabic, font_path, start_size,
                                     max_w, max_h, lh, line_gap)
                if final > site_size:
                    boosted += 1
                # Width FIRST (unwraps lines, reduces height need), then height.
                x, w, max_w, pad_x, wgrew = _grow_width(
                    draw, arabic, font_path, final, x, y, w, h, w_orig,
                    h_orig, page_w, page_h, texts, idx, lh, line_gap,
                    margin, max_grow, mstroke, next_top)
                if wgrew:
                    if not text_grew:
                        grown += 1
                        text_grew = True
                    t["x"] = round(x, 2)
                    t["width"] = round(w, 2)
                    # Widening may now overlap boxes it didn't before.
                    next_top = _next_top(texts, idx, x, y, w, page_h)
                final, h, grew = _fit_or_grow(
                    draw, arabic, font_path, final, start_size,
                    max_w, max_h, lh, line_gap, y, h, h_orig, page_h,
                    next_top, margin, max_grow, pad_y, mstroke)
                if grew and not text_grew:
                    grown += 1
                    text_grew = True
                    t["height"] = round(h, 2)
                style["font_size"] = int(final)
                t["font_size_px"] = float(final)
                style["_fit_base"] = int(start_size)
                style["_fit_final"] = int(final)
                t["style"] = style
                kept += 1
                continue
            # Grow the box downward before touching the font size.
            cap_h = min(page_h - y - 4, next_top - y - margin, h_orig * max_grow)
            if cap_h > h:
                h = cap_h
                t["height"] = round(h, 2)
                pad_y = min(6.0, max(2.0, h * 0.10))
                max_h = max(1, h - pad_y * 2)
                grown += 1
                text_grew = True
                fitted, _, _, _, _, _, _ = measure_fitted(
                    draw, arabic, font_path, start_size, max_w, max_h, lh,
                    line_gap, mstroke)
            if fitted < start_size:
                shrunk += 1
            else:
                kept += 1
            pre = fitted
            final = boost_fitted(draw, arabic, font_path, fitted,
                                 max_w, max_h, lh, line_gap)
            if final > site_size:
                boosted += 1
            # Width FIRST (unwraps lines, reduces height need), then height.
            x, w, max_w, pad_x, wgrew = _grow_width(
                draw, arabic, font_path, final, x, y, w, h, w_orig,
                h_orig, page_w, page_h, texts, idx, lh, line_gap,
                margin, max_grow, mstroke, next_top)
            if wgrew:
                if not text_grew:
                    grown += 1
                    text_grew = True
                t["x"] = round(x, 2)
                t["width"] = round(w, 2)
                # Widening may now overlap boxes it didn't before.
                next_top = _next_top(texts, idx, x, y, w, page_h)
            final, h, grew = _fit_or_grow(
                draw, arabic, font_path, final, pre,
                max_w, max_h, lh, line_gap, y, h, h_orig, page_h,
                next_top, margin, max_grow, pad_y, mstroke)
            if grew and not text_grew:
                grown += 1
                text_grew = True
                t["height"] = round(h, 2)
            style["font_size"] = int(final)
            t["font_size_px"] = float(final)
            style["_fit_base"] = int(pre)
            style["_fit_final"] = int(final)
            t["style"] = style
    return {"grown": grown, "shrunk": shrunk, "kept": kept,
            "skipped": skipped, "boosted": boosted}



def _log(msg: str) -> None:
    """Console log immune to cp1252 consoles (Arabic text safe)."""
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        try:
            print(msg.encode("ascii", "replace").decode("ascii"), flush=True)
        except Exception:
            pass


def _shape_arabic(text: str) -> str:
    """Reshape Arabic text for correct RTL rendering in Pillow (with bidi)."""
    if not text or not _HAS_ARABIC_SHAPING:
        return text
    try:
        reshaped = arabic_reshaper.reshape(text)
        return bidi_display(reshaped)
    except Exception:
        return text


def _reshape_arabic_no_bidi(text: str) -> str:
    """Reshape Arabic only (no bidi reordering) — preserves word order for wrapping."""
    if not text or not _HAS_ARABIC_SHAPING:
        return text
    try:
        return arabic_reshaper.reshape(text)
    except Exception:
        return text
def _render_page(background: Image.Image, texts: list[dict],
                 font_scale: float = 1.0, line_gap: int = 2,
                 bg_color: tuple = None, force_stroke: bool = False,
                 export_stroke_width: float = 1.5, export_stroke_color: tuple = None) -> Image.Image:
    """Render texts onto a background image, return composited RGBA image."""
    overlay = Image.new("RGBA", background.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for t in texts:
        arabic = t.get("arabic_text") or t.get("original_text", "")
        if not arabic:
            continue

        font_name = t.get("style", {}).get("font", "Hayah")
        font_size = int(float(t.get("style", {}).get("font_size", t.get("font_size_px", 45))))
        if font_size < 8:
            font_size = 8
        text_color = _parse_color(t.get("style", {}).get("color", "#000000"))
        stroke_color = _parse_color(t.get("style", {}).get("stroke_color", "#FFFFFF"))
        stroke_width = float(t.get("style", {}).get("stroke_width", 1))
        stroke_enabled = t.get("style", {}).get("stroke_enabled", True)
        if isinstance(stroke_enabled, str):
            stroke_enabled = stroke_enabled.lower() not in ("false", "0", "")
        elif isinstance(stroke_enabled, (int, float)):
            stroke_enabled = bool(stroke_enabled)
        if stroke_enabled and stroke_width < 1:
            stroke_width = 1.0
        text_align = t.get("style", {}).get("align", "center")
        line_height_factor = float(t.get("style", {}).get("line_height", t.get("line_height", 1.1)) or 1.1)
        line_height_factor = max(0.7, min(3.0, line_height_factor))
        rotation = float(t.get("style", {}).get("rotation", 0) or t.get("rotation", 0) or 0)

        # round(), not int(): int() floors (biased up/left ~1px per box).
        box_x = int(round(float(t.get("x", 0) or 0)))
        box_y = int(round(float(t.get("y", 0) or 0)))
        box_w = int(round(float(t.get("width", 200) or 200)))
        box_h = int(round(float(t.get("height", 100) or 100)))

        if box_w < 20:
            box_w = 200
        if box_h < 10:
            box_h = 60

        font_path = _pick_font_path(font_name, arabic)
        if not font_path:
            print(f"   [!] Font '{font_name}' not found, skipping: {arabic[:30]}...")
            continue

        padding_x = min(float(TEXT_PADDING), max(2.0, box_w * 0.02))
        padding_y = min(float(TEXT_PADDING), max(2.0, box_h * 0.10))
        max_w = max(1, box_w - (padding_x * 2))
        max_h = max(1, box_h - (padding_y * 2))
        # Stroke eats pixels too (textbbox ignores it).
        _mstroke = 0.0
        try:
            if force_stroke and not stroke_enabled:
                _mstroke = float(export_stroke_width or 0)
            elif stroke_enabled:
                _mstroke = float(stroke_width or 0)
        except Exception:
            _mstroke = 0.0

        # Single shared truth with box-autofit: fit via measure_fitted.
        requested = max(8, int(round(font_size * FONT_SCALE * font_scale)))
        (fitted_font_size, font, wrapped, line_widths,
         line_heights, base_line_h, total_text_h) = measure_fitted(
            draw, arabic, font_path, requested, max_w, max_h,
            line_height_factor, line_gap, _mstroke)
        if fitted_font_size < requested:
            _log(f"   [i] Shrunk '{arabic[:20]}...' {requested}->{max(fitted_font_size, 8)} to fit")

        if font is None:
            print(f"   [!] Could not load font '{font_name}' at size {fitted_font_size}")
            continue

        draw_stroke = stroke_enabled and stroke_width > 0
        eff_stroke_w = stroke_width
        eff_stroke_c = stroke_color
        if force_stroke and not stroke_enabled:
            draw_stroke = export_stroke_width > 0
            eff_stroke_w = export_stroke_width
            eff_stroke_c = export_stroke_color if export_stroke_color else stroke_color

        eff_stroke_w = int(round(max(eff_stroke_w, 0)))
        box_canvas = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
        box_draw = ImageDraw.Draw(box_canvas)
        # DOM-parity vertical placement: the browser centers EM line boxes
        # (baseline-anchored, descender void kept below the baseline), NOT
        # tight ink bboxes. Centering ink instead sat text ~0.2em too HIGH.
        # So: CSS line box LB=size*lh stacked with gaps, block centered in
        # max_h; each line placed by its BASELINE with half-leading split.
        try:
            _asc, _desc = font.getmetrics()
        except Exception:
            _asc, _desc = fitted_font_size, 0
        _em_h = _asc + _desc
        _lb = max(1.0, float(fitted_font_size) * float(line_height_factor))
        _total_em = _lb * len(wrapped) + max(len(wrapped) - 1, 0) * line_gap
        start_y = padding_y + max(0, (max_h - _total_em) / 2)

        for i, line in enumerate(wrapped):
            try:
                tb = box_draw.textbbox((0, 0), line, font=font)
                tw = tb[2] - tb[0]
            except Exception:
                tb = (0, 0, 0, 0)
                tw = 0

            if text_align == "center":
                lx = (box_w - tw) / 2
            elif text_align == "right":
                lx = box_w - tw - padding_x
            else:
                lx = padding_x

            slot_top = start_y + (i * (_lb + line_gap))
            baseline = slot_top + max(0.0, (_lb - _em_h) / 2.0) + _asc
            # Horizontal: exact ink-left (as before). Vertical: ascender-top
            # for the default "la" anchor == baseline - ascent.
            dx, dy = lx - tb[0], baseline - _asc

            if draw_stroke and eff_stroke_w > 0:
                box_draw.text((dx, dy), line, font=font, fill=eff_stroke_c, stroke_width=eff_stroke_w, stroke_fill=eff_stroke_c, align="left")
            box_draw.text((dx, dy), line, font=font, fill=text_color, align="left")

        if abs(rotation) > 0.01:
            pad = int(max(box_w, box_h) * 0.6)
            rot_canvas = Image.new("RGBA", (box_w + pad, box_h + pad), (0, 0, 0, 0))
            rot_canvas.paste(box_canvas, (pad // 2, pad // 2), box_canvas)
            rotated = rot_canvas.rotate(-rotation, resample=Image.BICUBIC, expand=True)
            cx = box_x + box_w / 2
            cy = box_y + box_h / 2
            px = int(cx - rotated.width / 2)
            py = int(cy - rotated.height / 2)
            overlay.paste(rotated, (px, py), rotated)
        else:
            overlay.paste(box_canvas, (box_x, box_y), box_canvas)

    return Image.alpha_composite(background.convert("RGBA"), overlay).convert("RGB")


def _merge_chunk(pages_with_images: list, bg_color=None) -> Image.Image:
    """Merge a list of (name, rendered_rgb_image, height) tuples into one vertical strip."""
    imgs = [item[1] for item in pages_with_images]
    total_w = max(im.width for im in imgs)
    total_h = sum(im.height for im in imgs)
    if isinstance(bg_color, (tuple, list)) and len(bg_color) >= 3:
        bg = (int(bg_color[0]), int(bg_color[1]), int(bg_color[2]))
    else:
        bg = (255, 255, 255)
    strip = Image.new("RGB", (total_w, total_h), bg)
    y_off = 0
    for im in imgs:
        x_off = (total_w - im.width) // 2
        strip.paste(im, (x_off, y_off))
        y_off += im.height
    return strip


def _pillow_format(fmt: str) -> str:
    """Map user format to a Pillow-recognized format name (PIL has no 'JPG')."""
    f = (fmt or "webp").lower()
    if f == "jpg":
        return "JPEG"
    if f == "jpeg":
        return "JPEG"
    if f == "webp":
        return "WEBP"
    if f == "png":
        return "PNG"
    return "WEBP"


def _save_image(img: Image.Image, out_path: Path, pillow_fmt: str, quality: int):
    """Save with quality only for JPEG/WEBP (PNG ignores quality)."""
    if pillow_fmt in ("JPEG", "WEBP"):
        q = max(1, min(100, int(quality)))
        img.save(str(out_path), format=pillow_fmt, quality=q, optimize=True)
    else:
        img.save(str(out_path), format=pillow_fmt, optimize=True)


def export_chapter(
    chapter_dir: Path,
    output_dir: Optional[Path] = None,
    fmt: str = "webp",
    quality: int = 90,
    merge: bool = False,
    max_height: int = 9000,
    page_range: Optional[list[int]] = None,
    font_scale: float = 1.0,
    line_gap: int = 2,
    bg_color: tuple = None,
    force_stroke: bool = False,
    export_stroke_width: float = 1.5,
    export_stroke_color: tuple = None,
) -> list[str]:
    """Export chapter with professional quality.

    - Each output image keeps original page width (650-800px typical)
    - In merge mode: groups pages into chunks where each chunk's total
      height is as close to max_height as possible WITHOUT exceeding it.
      Chunks are split at page boundaries (never mid-page).
    - Each chunk is rendered as a vertical strip.
    - Default: WebP quality 90.

    Returns list of output filenames.
    """
    try:
        max_height = int(max_height)
    except (TypeError, ValueError):
        max_height = 9000
    max_height = max(1000, min(30000, max_height))
    json_path = chapter_dir / "chapter_data.json"
    if not json_path.exists():
        print(f"[!] chapter_data.json not found: {json_path}")
        return []

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if output_dir is None:
        output_dir = chapter_dir / "exported"
    # Clear previous export files (avoid mixing merge/non-merge)
    if output_dir.exists():
        for f in output_dir.iterdir():
            if f.suffix.lower() in (".webp", ".png", ".jpg", ".jpeg"):
                f.unlink()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Filter + render pages
    all_pages = data.get("pages", [])
    if page_range:
        all_pages = [p for p in all_pages if p.get("page") in page_range]

    if not all_pages:
        print("[!] No pages to export")
        return []

    # Render all pages with text
    rendered_pages = []  # list of (filename_base, rgb_image, height)
    for page in all_pages:
        page_num = page["page"]
        src = chapter_dir / "pages" / page["filename"]
        if not src.exists():
            print(f"   [!] Page image not found: {src}")
            continue
        img = Image.open(src)
        composited = _render_page(
            img, page.get("texts", []),
            font_scale=font_scale,
            line_gap=line_gap,
            bg_color=bg_color,
            force_stroke=force_stroke,
            export_stroke_width=export_stroke_width,
            export_stroke_color=export_stroke_color,
        )
        rendered_pages.append((
            f"page_{page_num:03d}",
            composited,
            composited.height,
        ))

    if not rendered_pages:
        return []

    img_format = fmt.lower()
    if img_format not in ("webp", "png", "jpg", "jpeg"):
        img_format = "webp"
    pillow_fmt = _pillow_format(img_format)

    exported = []
    part_counter = 1

    if merge:
        # Group pages into chunks of ≤ max_height, splitting at page boundaries
        chunks = []        # list of list of (name, img, h)
        current_chunk = []
        current_h = 0

        for item in rendered_pages:
            name, img, h = item
            if h > max_height:
                # Page itself is taller than max — export it alone (rare for manga)
                if current_chunk:
                    chunks.append(current_chunk)
                    current_chunk = []
                    current_h = 0
                chunks.append([item])
            elif current_h + h > max_height:
                # Adding this page would exceed max → start new chunk
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = [item]
                current_h = h
            else:
                current_chunk.append(item)
                current_h += h

        if current_chunk:
            chunks.append(current_chunk)

        # Merge + save each chunk
        for chunk in chunks:
            strip = _merge_chunk(chunk, bg_color=bg_color)
            out_name = f"output_p{part_counter:03d}.{img_format}"
            part_counter += 1
            out_path = output_dir / out_name
            _save_image(strip, out_path, pillow_fmt, quality)
            exported.append(out_path.name)
            pages_str = "صفحة" if len(chunk) == 1 else f"{len(chunk)} صفحات"
            _log(f"   [+] {out_path.name} ({strip.width}x{strip.height}) — {pages_str}")

    else:
        # Non-merge: each page exported individually
        for name, img, h in rendered_pages:
            # If a single page exceeds max_height (unusual), split it
            if h > max_height:
                # Split vertically at approximate thirds or halves
                # For manga pages this practically never happens
                strip_h = h
                y = 0
                sp = 1
                while y < h:
                    ch = min(max_height, h - y)
                    part = img.crop((0, y, img.width, y + ch))
                    out_path = output_dir / f"{name}_s{sp}.{img_format}"
                    _save_image(part, out_path, pillow_fmt, quality)
                    exported.append(out_path.name)
                    print(f"   [SPLIT] {out_path.name} ({part.width}x{part.height})")
                    y += ch
                    sp += 1
            else:
                out_path = output_dir / f"{name}.{img_format}"
                _save_image(img, out_path, pillow_fmt, quality)
                exported.append(out_path.name)
                print(f"   [+] {out_path.name} ({img.width}x{img.height})")

    print(f"\n[+] Exported {len(exported)} files to: {output_dir}")
    return exported
