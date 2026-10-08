"""Regression: H0 bias when no flush box exists (offline only).

Every overlay sits >=200px deep in its page with TRUE H0=275, i.e.
    css_top = cum_png + 275 + 200 + small jitter
several overlays per page so the "corroborated minimum" guard passes.
The biased min-residual estimator returns ~475 (275+200); the fixed
estimator must return H0 <= 275.5.

NOTE on geometry: estimate_vertical_geometry needs a >2000px cum_png
baseline for its Theil-Sen slope (pairs with dc > 2000). 3 pages x 1000px
spans exactly 2000px so it yields (None, 0.0) vacuously and can never
expose the bias. We use 3 pages x 1500px (span 3000px) to preserve the
spec intent (uniform tall pages, TRUE H0=275, all boxes deep) while
actually exercising the estimator.

Run:  python tests/test_h0_bias.py
Exit 0 on PASS, 1 on FAIL. Never touches the network.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TRUE_H0 = 275.0
DEEP = 200.0          # every overlay sits >=200px below its page top
PNG_W, PNG_H = 800, 1500
N_PAGES = 3
JITTERS = [0.0, 3.0, 6.0, 9.0]  # small (<25px) so corroboration passes


def build_inputs():
    png_dims = [(PNG_W, PNG_H)] * N_PAGES
    page_texts: dict = {}
    geo_tops: dict = {}
    for p in range(N_PAGES):
        cum = p * PNG_H
        tops = [cum + TRUE_H0 + DEEP + j for j in JITTERS]
        page_texts[p] = [
            {"id": f"p{p}b{i}", "css_top": float(t)} for i, t in enumerate(tops)
        ]
        geo_tops[p] = [float(t) for t in tops]
    return page_texts, png_dims, geo_tops


def check_h0_bias():
    from scraper.coordinator import estimate_vertical_geometry

    page_texts, png_dims, geo_tops = build_inputs()
    s, h0 = estimate_vertical_geometry(page_texts, png_dims, geo_tops)
    # Deepest in-page offset actually used (should all be >=200).
    min_inpage = min(
        float(t["css_top"]) - (p * PNG_H + TRUE_H0)
        for p, arr in page_texts.items() for t in arr
    )
    print(f"    scale s={s} H0={h0} (TRUE_H0={TRUE_H0}, deep>={DEEP}, "
          f"min_inpage={min_inpage:.1f})")
    assert s is not None, (
        f"no scale baseline (s=None) — geometry spans "
        f"{(N_PAGES - 1) * PNG_H}px, need >2000px for slope pairs"
    )
    assert h0 <= 275.5, (
        f"H0 biased high: got {h0:.1f}, expected <= 275.5 "
        f"(TRUE_H0={TRUE_H0}; biased min-residual ~= {TRUE_H0 + DEEP})"
    )
    return s, h0


def test_h0_bias():
    """pytest entry point."""
    check_h0_bias()


def main():
    print("[h0-bias] all overlays >=200px deep, TRUE H0=275 ...")
    try:
        s, h0 = check_h0_bias()
    except AssertionError as e:
        print(f"H0 BIAS: FAIL ({e})")
        return 1
    except Exception as e:
        print(f"H0 BIAS: FAIL ({type(e).__name__}: {e})")
        return 1
    print(f"H0 BIAS: PASS (s={s:.4f} H0={h0:.1f} <= 275.5)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
