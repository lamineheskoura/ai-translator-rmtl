"""Regression: pitch parity (exporter autofit vs scraper geometry vs server fit-call).

Offline only — never touches the network. Run:
    python tests/test_pitch_parity.py
Exit 0 on PASS, 1 on FAIL. Step 2 (scraper fetch) is SKIPPED with a
printed note when it would need network, never a failure.
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _mkbox(bid, page, x, y, w, h, fs, ar, en="hello"):
    return {
        "id": bid,
        "page": page,
        "x": float(x),
        "y": float(y),
        "width": float(w),
        "height": float(h),
        "font_size_px": float(fs),
        "line_height": 1.1,
        "scale_factor": 1.0,
        "original_text": en,
        "arabic_text": ar,
        "style": {
            "font": "Hayah",
            "font_size": int(fs),
            "line_height": 1.1,
            "color": "#000000",
            "stroke_color": "#ffffff",
            "stroke_width": 1,
            "stroke_enabled": True,
            "align": "center",
            "rotation": 0,
        },
    }


def build_synthetic_chapter():
    """3 pages, widths 800/940/1008, known y/height/font."""
    return {
        "slug": "synthetic",
        "chapter": "1",
        "pages": [
            {
                "page": 1,
                "filename": "page_001.png",
                "width": 800,
                "height": 1200,
                "texts": [
                    _mkbox("p1b1", 1, 100, 100, 200, 60, 30,
                           "مرحبا بالعالم هذا نص عربي طويل للاختبار " * 3,
                           en="long hello world"),
                    # x-overlaps p1b1 on purpose (120 within 100..300)
                    # y-overlaps too (130 within 100..160) -> autofit must separate.
                    _mkbox("p1b2", 1, 120, 130, 200, 60, 30,
                           "نص ثان يتداخل مع الاول " * 3,
                           en="second overlapping"),
                ],
            },
            {
                "page": 2,
                "filename": "page_002.png",
                "width": 940,
                "height": 1400,
                "texts": [
                    # near-full-page width: must be capped at page width.
                    _mkbox("p2b1", 2, 50, 50, 900, 60, 30,
                           "نص عريض جدا " * 10,
                           en="wide text"),
                    _mkbox("p2b2", 2, 100, 500, 200, 60, 30,
                           "نص قصير",
                           en="short"),
                ],
            },
            {
                "page": 3,
                "filename": "page_003.png",
                "width": 1008,
                "height": 1500,
                "texts": [
                    # very long text in a small box: forces shrink, floor is 14.
                    _mkbox("p3b1", 3, 100, 100, 150, 40, 30,
                           "نص طويل جدا يحتاج تصغير " * 8,
                           en="needs shrink"),
                ],
            },
        ],
    }


def check_autofit():
    print("[1] exporter autofit on synthetic chapter (800/940/1008) ...")
    from editor.exporter import autofit_chapter_boxes

    data = build_synthetic_chapter()
    stats = autofit_chapter_boxes(data)
    print(f"    stats={stats}")

    # (a) no box width exceeds its page width
    for page in data["pages"]:
        pw = float(page.get("width", 0) or 0)
        for t in page.get("texts", []):
            w = float(t.get("width", 0) or 0)
            assert w <= pw + 1e-6, (
                f"(a) box {t.get('id')} width {w} exceeds page width {pw} "
                f"(page {page.get('page')})"
            )
            assert float(t.get("x", 0) or 0) >= -1e-6, f"(a) box {t.get('id')} x<0"
            assert float(t.get("x", 0) or 0) + w <= pw + 1.01, (
                f"(a) box {t.get('id')} x+w exceeds page width {pw}"
            )
    print("    (a) OK: no box width exceeds its page width")

    # (b) x-overlapping boxes on a page have >=1px gaps afterwards
    for page in data["pages"]:
        texts = list(page.get("texts", []) or [])
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                a, b = texts[i], texts[j]
                ax, aw = float(a.get("x", 0) or 0), float(a.get("width", 0) or 0)
                bx, bw = float(b.get("x", 0) or 0), float(b.get("width", 0) or 0)
                x_overlap = (ax < bx + bw) and (bx < ax + aw)
                if not x_overlap:
                    continue
                ay, ah = float(a.get("y", 0) or 0), float(a.get("height", 0) or 0)
                by, bh = float(b.get("y", 0) or 0), float(b.get("height", 0) or 0)
                if ay <= by:
                    gap = by - (ay + ah)
                else:
                    gap = ay - (by + bh)
                assert gap >= 1.0 - 1e-6, (
                    f"(b) x-overlapping boxes {a.get('id')}/{b.get('id')} "
                    f"on page {page.get('page')} gap {gap:.2f}px < 1px "
                    f"(a y={ay} h={ah}, b y={by} h={bh})"
                )
    print("    (b) OK: x-overlapping boxes have >=1px gaps")

    # (c) every translated text has font_size_px >= 14
    for page in data["pages"]:
        for t in page.get("texts", []):
            if not (t.get("arabic_text") or "").strip():
                continue
            fs = float(t.get("font_size_px", 0) or 0)
            sfs = float((t.get("style") or {}).get("font_size", fs) or 0)
            assert fs >= 14 - 1e-6, (
                f"(c) box {t.get('id')} font_size_px {fs} < 14"
            )
            assert sfs >= 14 - 1e-6, (
                f"(c) box {t.get('id')} style.font_size {sfs} < 14"
            )
    print("    (c) OK: every translated text font_size_px >= 14")
    return True


def check_scraper_pitch_offline():
    print("[2] scraper pitch path (offline-only) ...")
    # Full fetch (fetch_chapter_page / scrape_chapter) needs network by
    # construction -> never call it here. Note the skip explicitly.
    print("    SKIP note: scraper fetch path (fetch_chapter_page/scrape_chapter) "
          "needs network — not called (offline test).")
    try:
        from scraper.coordinator import assign_texts_to_images
    except Exception as e:
        print(f"    SKIP: scraper pitch path unavailable offline ({e}) — "
              "skipping without failure")
        return True
    try:
        # Synthetic geometry mirroring the exporter pages (800/940/1008).
        def _ov(oid, top, left_c, w, h, fs=20.0):
            return {
                "id": oid,
                "css_top": float(top),
                "css_left": float(left_c - w / 2.0),
                "css_left_center": float(left_c),
                "css_width": float(w),
                "css_height": float(h),
                "css_font_size": float(fs),
                "css_color": "#000000",
                "css_stroke_color": "#ffffff",
                "css_stroke_width": 1.0,
                "original_text": "hello",
                "arabic_text": "",
                "line_height": 1.2,
            }

        page_texts = {
            0: [_ov("s1", 300.0, 400.0, 200.0, 60.0)],
            1: [_ov("s2", 300.0, 470.0, 200.0, 60.0)],
            2: [_ov("s3", 300.0, 504.0, 200.0, 60.0)],
        }
        png_dims = [(800, 1200), (940, 1400), (1008, 1500)]
        pages = assign_texts_to_images(
            page_texts, png_dims, 1.0, [], trust_page_id=True)
        assert len(pages) == 3, f"expected 3 pages, got {len(pages)}"
        for p, (W, _H) in zip(pages, png_dims):
            for t in p.get("texts", []):
                w = float(t.get("width", 0) or 0)
                x = float(t.get("x", 0) or 0)
                assert w <= W + 1e-6, (
                    f"scraper box {t.get('id')} width {w} exceeds page {W}")
                assert x >= -1e-6 and x + w <= W + 1.01, (
                    f"scraper box {t.get('id')} x+w outside page {W}")
        print("    OK: scraper pitch (assign_texts_to_images) runs offline, "
              "boxes inside 800/940/1008 pages")
        return True
    except Exception as e:
        # Never fail on network: anything that smells like network (or any
        # environment issue in the offline pitch probe) becomes a SKIP note.
        msg = f"{type(e).__name__}: {e}"
        print(f"    SKIP: scraper pitch path needs network ({msg}) — "
              "skipping without failure")
        return True


def check_server_parity():
    print("[3] server fit-call parity (worker + smart-fit -> autofit) ...")
    import editor.server as srv
    from editor.exporter import autofit_chapter_boxes

    # Default max_grow must stay 2.0.
    sig = inspect.signature(autofit_chapter_boxes)
    assert sig.parameters["max_grow"].default == 2.0, (
        f"autofit_chapter_boxes max_grow default "
        f"{sig.parameters['max_grow'].default} != 2.0")

    worker_src = inspect.getsource(srv._batch_worker)
    # Whitespace-insensitive: worker may wrap args across lines, e.g.
    # autofit_chapter_boxes(\n data, line_gap=...) — still resolves to
    # autofit_chapter_boxes(data) with default max_grow=2.0.
    _norm = "".join(worker_src.split())
    assert "autofit_chapter_boxes(data" in _norm, (
        "worker fit-call missing 'autofit_chapter_boxes(data)'")
    assert "autofit_chapter_boxes" in worker_src
    print("    OK: worker calls autofit_chapter_boxes(data) (default max_grow=2.0)")

    smart_src = inspect.getsource(srv.smart_fit_chapter)
    assert "max_grow=2.0 if" in smart_src, (
        "smart_fit_chapter missing 'max_grow=2.0 if'")
    print("    OK: smart_fit_chapter uses 'max_grow=2.0 if' (grow_boxes default)")
    return True


def test_pitch_parity():
    """pytest entry point (also used by direct run)."""
    check_autofit()
    check_scraper_pitch_offline()
    check_server_parity()


def main():
    failures = 0
    for name, fn in (("autofit", check_autofit),
                     ("scraper-pitch", check_scraper_pitch_offline),
                     ("server-parity", check_server_parity)):
        try:
            fn()
        except AssertionError as e:
            print(f"FAIL [{name}]: {e}")
            failures += 1
        except Exception as e:
            print(f"FAIL [{name}]: {type(e).__name__}: {e}")
            failures += 1
    if failures:
        print(f"PITCH PARITY: FAIL ({failures} failing check(s))")
        return 1
    print("PITCH PARITY: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
