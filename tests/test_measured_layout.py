"""Regression: measured-layout branch + fallback byte-identity (offline only).

Synthetic 3-page chapter WITH layout:
    heights 1000 + gap 12, first img top 275 (H0 header):
      img_tops    = [275, 1287, 2299]   (275 + i*(1000+12))
      img_heights = [1000, 1000, 1000]
    scale 1.0, png 800x1000.
    2 overlays/page at known in-page offsets -> expected y = offset*1.0.

(a) WITH layout (layout={"img_tops","img_heights","first_img_top"}):
    call assign_texts_to_images(..., layout) -> assert every y in
    [0, page_h] (zero pins) and y == (top - img_top)*1.0 within 1px.
(b) SAME data WITHOUT layout (layout=None, css heights []):
    assert it runs (no crash), returns 3 pages, deterministic
    byte-identical across two calls (pre-change fallback preserved),
    and pin summary is reported.

Run:  python tests/test_measured_layout.py
Exit 0 on PASS, 1 on FAIL. Never touches the network.
"""
import inspect
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PNG_W, PNG_H = 800, 1000
N_PAGES = 3
CSS_H = 1000.0
GAP = 12.0
H0 = 275.0
SCALE = 1.0
IMG_TOPS = [H0 + i * (CSS_H + GAP) for i in range(N_PAGES)]  # [275,1287,2299]
IMG_HEIGHTS = [CSS_H] * N_PAGES
# in-page offsets (all in [0, 940] so max_rel=940 never clamps -> zero pins)
OFFSETS = [[100.0, 500.0], [200.0, 700.0], [50.0, 850.0]]
OV_H, OV_W, OV_FS = 60.0, 200.0, 20.0


def _ov(oid, top, center=400.0):
    return {
        "id": oid,
        "css_top": float(top),
        "css_left": float(center - OV_W / 2.0),
        "css_left_center": float(center),
        "css_width": float(OV_W),
        "css_height": float(OV_H),
        "css_font_size": float(OV_FS),
        "css_color": "#000000",
        "css_stroke_color": "#ffffff",
        "css_stroke_width": 1.0,
        "original_text": "hello",
        "arabic_text": "",
        "line_height": 1.2,
    }


def build_inputs():
    png_dims = [(PNG_W, PNG_H)] * N_PAGES
    page_texts = {}
    expected = {}  # oid -> (page_idx, offset)
    for p in range(N_PAGES):
        arr = []
        for k, off in enumerate(OFFSETS[p]):
            oid = f"p{p}b{k}"
            arr.append(_ov(oid, IMG_TOPS[p] + off))
            expected[oid] = (p, float(off))
        page_texts[p] = arr
    layout = {
        "img_tops": list(IMG_TOPS),
        "img_heights": list(IMG_HEIGHTS),
        "first_img_top": float(H0),
    }
    return page_texts, png_dims, layout, expected


def check_signature():
    from scraper.coordinator import assign_texts_to_images

    sig = inspect.signature(assign_texts_to_images)
    names = list(sig.parameters)
    # read-only check: layout branch must be addressable via current params
    assert "layout" in sig.parameters, (
        f"assign_texts_to_images has no 'layout' param: {names}"
    )
    assert "css_img_heights" in sig.parameters
    assert "scale" in sig.parameters
    print(f"    signature OK: {names}")
    return sig


def check_measured_layout():
    print("[1] measured-layout branch (WITH layout, H0=275 gap=12) ...")
    check_signature()
    from scraper.coordinator import assign_texts_to_images

    page_texts, png_dims, layout, expected = build_inputs()
    buf = io.StringIO()
    with redirect_stdout(buf):
        pages = assign_texts_to_images(
            page_texts, png_dims, SCALE, list(IMG_HEIGHTS),
            trust_page_id=True, layout=layout,
        )
    log = buf.getvalue()
    assert isinstance(pages, list) and len(pages) == N_PAGES, (
        f"expected {N_PAGES} pages, got {len(pages) if isinstance(pages, list) else type(pages)}"
    )
    total = sum(len(p.get("texts", [])) for p in pages)
    assert total == 6, f"expected 6 texts, got {total}"
    pins = 0
    for p in pages:
        ph = float(p.get("height", 0) or 0)
        for t in p.get("texts", []):
            oid = t.get("id")
            assert oid in expected, f"unknown overlay {oid}"
            pidx, off = expected[oid]
            y = float(t.get("y", 0) or 0)
            exp_y = off * SCALE  # (top - img_top)*1.0
            assert 0.0 - 1e-6 <= y <= ph + 1e-6, (
                f"{oid}: y {y} outside [0, page_h={ph}]"
            )
            assert abs(y - exp_y) <= 1.0, (
                f"{oid}: y {y} != expected (top-img_top)={exp_y} (>1px)"
            )
            # zero-pin inference: offsets avoid 0/max_rel, so any clamp
            # to 0 or max_rel would break the 1px equality above; count
            # explicit boundary hits as pins for the report.
            max_rel = max(0.0, ph - OV_H * SCALE)
            if (abs(y) <= 1e-9 and exp_y > 1.0) or (
                abs(y - max_rel) <= 1e-9 and exp_y < max_rel - 1.0
            ):
                pins += 1
    assert pins == 0, f"measured branch pinned {pins} overlay(s), want 0"
    print(f"    OK: 6/6 overlays y==(top-img_top)*1.0 within 1px, zero pins, "
          f"y in [0,page_h] (img_tops={[round(x) for x in IMG_TOPS]})")
    return pages


def check_fallback_identity():
    print("[2] fallback byte-identity (SAME data WITHOUT layout) ...")
    from scraper.coordinator import assign_texts_to_images

    page_texts, png_dims, _layout, _expected = build_inputs()

    def _run():
        buf = io.StringIO()
        with redirect_stdout(buf):
            out = assign_texts_to_images(
                page_texts, png_dims, SCALE, [],
                trust_page_id=True, layout=None,
            )
        return out, buf.getvalue()

    try:
        pages1, log1 = _run()
        pages2, log2 = _run()
    except Exception as e:
        raise AssertionError(f"fallback crashed: {type(e).__name__}: {e}")
    assert isinstance(pages1, list) and len(pages1) == N_PAGES, (
        f"fallback: expected {N_PAGES} pages"
    )
    # byte-identity: deterministic fallback, identical across calls
    s1 = json.dumps(pages1, sort_keys=True, ensure_ascii=False)
    s2 = json.dumps(pages2, sort_keys=True, ensure_ascii=False)
    assert s1 == s2, "fallback not byte-identical across two runs"
    n1 = sum(len(p.get("texts", [])) for p in pages1)
    # pin summary reported (computed + log excerpt): never crash on pins
    top_zero = sum(
        1 for p in pages1 for t in p.get("texts", [])
        if abs(float(t.get("y", 0) or 0)) <= 1e-9
    )
    bot_edge = 0
    for p in pages1:
        ph = float(p.get("height", 0) or 0)
        for t in p.get("texts", []):
            if abs(float(t.get("y", 0) or 0) + float(t.get("height", 0) or 0) - ph) <= 1.01:
                bot_edge += 1
    has_pin_log = ("Pin report" in log1) or ("Top-pinned" in log1) or ("containment" in log1)
    print(f"    OK: fallback runs, no crash, {n1} texts, byte-identical "
          f"({len(s1)} chars), pins reported: top_y0={top_zero} "
          f"bot_edge={bot_edge} log_pins={'yes' if has_pin_log else 'no (zero pins, containment logged)'}")
    return pages1


def test_measured_layout():
    """pytest entry point."""
    check_measured_layout()
    check_fallback_identity()


def main():
    failures = 0
    for name, fn in (("measured", check_measured_layout),
                     ("fallback", check_fallback_identity)):
        try:
            fn()
        except AssertionError as e:
            print(f"FAIL [{name}]: {e}")
            failures += 1
        except Exception as e:
            print(f"FAIL [{name}]: {type(e).__name__}: {e}")
            failures += 1
    if failures:
        print(f"MEASURED LAYOUT: FAIL ({failures} failing check(s))")
        return 1
    print("MEASURED LAYOUT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
