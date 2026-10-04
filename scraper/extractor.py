import re
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from .engine import get_rendered_image_width, get_image_x_offset


def extract_chapter_info(url: str):
    m = re.search(r"/manga/([^/]+)/chapter-([0-9]+(?:\.[0-9]+)?)", url)
    return (m.group(1), m.group(2)) if m else (None, None)


def parse_float(value, default: float = 0.0) -> float:
    try:
        m = re.search(r"[0-9]+(?:\.[0-9]+)?", str(value))
        return float(m.group()) if m else default
    except (TypeError, AttributeError):
        return default


def parse_style_px(style: str, prop: str) -> float:
    m = re.search(rf"{re.escape(prop)}\s*:\s*([0-9.]+)\s*px", style)
    return float(m.group(1)) if m else 0.0


def parse_chapter_full(driver, page_settle_time: float = 3.5):
    print(f"[i] Waiting {page_settle_time}s for overlays to hydrate...")
    time.sleep(page_settle_time)

    img_els = driver.find_elements(
        By.CSS_SELECTOR, ".reading-content img.wp-manga-chapter-img"
    )
    seen, image_urls = set(), []
    for el in img_els:
        src = (el.get_attribute("src") or el.get_attribute("data-src") or "").strip()
        if src and src not in seen:
            seen.add(src)
            image_urls.append(src)

    rendered_width = get_rendered_image_width(driver, img_els)
    img_x_offset = get_image_x_offset(driver)

    css_img_heights = []
    for el in img_els:
        h = float(el.size.get("height", 0))
        if h == 0:
            try:
                h = float(driver.execute_script(
                    "return arguments[0].getBoundingClientRect().height;", el
                ))
            except Exception:
                h = 0.0
        css_img_heights.append(h)

    overlay_els = driver.find_elements(
        By.CSS_SELECTOR, "div.manga-ocr-button-overlay"
    )

    image_css_tops = [0.0]
    for el in img_els:
        try:
            h = float(driver.execute_script(
                "return arguments[0].getBoundingClientRect().height;", el
            ))
        except Exception:
            h = 1000.0
        image_css_tops.append(image_css_tops[-1] + h)

    def find_page_by_css_top(css_top: float) -> int:
        for i in range(len(image_css_tops) - 1):
            if image_css_tops[i] <= css_top < image_css_tops[i + 1]:
                return i
        return len(image_css_tops) - 2 if len(image_css_tops) > 1 else 0

    page_texts = {}
    for el in overlay_els:
        try:
            span = el.find_element(By.TAG_NAME, "span")
            text = span.text.strip()
            if not text:
                continue

            el_id = el.get_attribute("id") or ""
            style = el.get_attribute("style") or ""

            left_center_raw = parse_style_px(style, "left")
            top_global = parse_style_px(style, "top")
            css_width = parse_style_px(style, "max-width")
            css_height = parse_style_px(style, "height")
            css_fontsize = parse_style_px(style, "font-size")

            if css_width == 0:
                css_width = parse_float(el.get_attribute("data-box-width"))
            if css_height == 0:
                css_height = parse_float(el.get_attribute("data-box-height"))

            left_center = left_center_raw - img_x_offset
            css_left = left_center - css_width / 2.0
            if css_left < 0:
                css_left = 0

            page_num = find_page_by_css_top(top_global)
            m = re.search(r'split_(\d+)_webp', el_id)
            if m:
                page_num = int(m.group(1))

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
                "line_height": 1.2,
            })
        except Exception:
            continue

    sorted_pages = sorted(page_texts.keys())
    for p in sorted_pages:
        page_texts[p].sort(key=lambda x: x["css_top"])

    try:
        title = driver.find_element(By.CSS_SELECTOR, "h1#chapter-heading").text.strip()
    except Exception:
        title = ""

    return image_urls, page_texts, rendered_width, css_img_heights, title
