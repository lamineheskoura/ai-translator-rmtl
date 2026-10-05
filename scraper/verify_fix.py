"""Quick verification unit: (a) martial scale, (b) contract X, (c) series."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scraper.spider import fetch_chapter_page, detect_left_mode
from scraper.coordinator import (
    estimate_vertical_geometry, assign_texts_to_images,
)
from scraper.series import expand_series, clean_slug


def check_a():
    print("== (a) martial-evolution-158 ==")
    url = ("https://manhuarmtl.com/manga/"
           "martial-evolution-the-beast-king-awakens/chapter-158/")
    res = fetch_chapter_page(url)
    assert res is not None, "spider fetch failed"
    image_urls, page_texts, rendered_w, css_heights, title, geo_tops = res
    n_texts = sum(len(v) for v in page_texts.values())
    # cached PNG dims (avoid 54 downloads)
    d = json.load(open(
        ROOT / "output/martial-evolution-the-beast-king-awakens"
        "/chapter_158/chapter_data.json", encoding="utf-8"))
    png_dims = [(p["width"], p["height"]) for p in d["pages"]]
    s, h0 = estimate_vertical_geometry(page_texts, png_dims, geo_tops)
    pages = assign_texts_to_images(page_texts, png_dims, s, css_heights,
                                  trust_page_id=True, geo_tops=geo_tops)
    assigned = sum(len(p["texts"]) for p in pages)
    print(f"images={len(image_urls)} overlays={n_texts} "
          f"assigned={assigned} scale={s:.4f} H0={h0:.1f}")
    assert len(image_urls) == 54, len(image_urls)
    assert n_texts == 34, n_texts
    assert assigned == 34, assigned
    assert abs(s - 1.0724) < 0.001, s
    assert abs(h0 - 280.0) < 5.0, h0
    # cached file unchanged?
    assert d["scale_factor"] == round(s, 5) or abs(d["scale_factor"]-1.07237) < 0.001
    print("PASS (a): 34/34 scale 1.0724 H0~280")
    return len(image_urls), n_texts, assigned, s, h0


def check_b():
    print("== (b) contract-admission-1 X ==")
    url = "https://manhuarmtl.com/manga/contract-admission/chapter-1/"
    res = fetch_chapter_page(url)
    assert res is not None, "spider fetch failed"
    image_urls, page_texts, rendered_w, css_heights, title, geo_tops = res
    d = json.load(open(ROOT / "output/contract-admission/chapter_1"
                       "/chapter_data.json", encoding="utf-8"))
    png_dims = [(p["width"], p["height"]) for p in d["pages"]]
    from scraper.coordinator import estimate_vertical_geometry as evg
    s, h0 = evg(page_texts, png_dims, geo_tops)
    if s is None:
        s = 1.0
    pages = assign_texts_to_images(page_texts, png_dims, s, css_heights,
                                  trust_page_id=True, geo_tops=geo_tops)
    bad = 0
    tot = 0
    for p in pages:
        W = p["width"]
        for t in p["texts"]:
            tot += 1
            if not (t["x"] >= -1e-6 and t["x"] + t["width"] <= W + 1.01):
                bad += 1
    print(f"images={len(image_urls)} texts={tot} bad_X={bad} "
          f"scale={s:.4f} W0={png_dims[0][0] if png_dims else 0}")
    assert tot >= 200, tot
    assert bad == 0, f"{bad}/{tot} X-overflow"
    print(f"PASS (b): {tot}/{tot} X inside page width")
    return len(image_urls), tot, bad


def check_c():
    print("== (c) expand_series ==")
    calls = []
    def fake_ok(url):
        calls.append(url)
        return True
    out = expand_series(
        "https://manhuarmtl.com/manga/contract-admission/chapter-1/",
        5, check_url=fake_ok)
    urls = [d["url"] for d in out]
    print("gen5:", urls)
    assert len(out) == 5, out
    assert out[0]["chapter"] == "1" and out[-1]["chapter"] == "5"
    assert out[0]["slug"] == "contract-admission"
    # numeric ordering: 2 < 10 (lexicographic would fail)
    out2 = expand_series(
        "https://manhuarmtl.com/manga/foo/chapter-8/", 4,
        check_url=fake_ok)
    chs = [d["chapter"] for d in out2]
    print("gen8+4:", chs)
    assert chs == ["8", "9", "10", "11"], chs
    # smart skip: chapter-10 fails -> skipped
    def fake_skip(url):
        return "chapter-10/" not in url
    out3 = expand_series(
        "https://manhuarmtl.com/manga/foo/chapter-9/", 3,
        check_url=fake_skip)
    chs3 = [d["chapter"] for d in out3]
    print("skip10:", chs3)
    assert chs3 == ["9", "11"], chs3
    assert clean_slug("Contract_Admission ") == "contract-admission"
    print("PASS (c): numeric order + skip + slug ok")
    return urls, chs, chs3


if __name__ == "__main__":
    a = check_a()
    b = check_b()
    c = check_c()
    print("ALL PASS")
