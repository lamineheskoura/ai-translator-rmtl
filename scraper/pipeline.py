"""Download/convert pipeline for the Scrapling spider path.

Mirrors engine.download_images (ThreadPoolExecutor(6)) + magic-bytes
validation before accepting cache.
convert_all_to_png / get_png_dimensions are re-exported from .engine
to avoid duplication.
"""

import time
from pathlib import Path
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

try:
    # Re-use engine helpers when Selenium env is available.
    from .engine import (
        convert_all_to_png,  # noqa: F401  (re-export)
        get_png_dimensions,  # noqa: F401  (re-export)
        MAX_WORKERS,
        DL_DELAY,
        DL_TIMEOUT,
        DL_MAX_RETRIES,
    )
except Exception:  # engine imports selenium; spider path must work without it
    from PIL import Image

    MAX_WORKERS = 6
    DL_DELAY = 0.12
    DL_TIMEOUT = 10
    DL_MAX_RETRIES = 2

    def convert_all_to_png(raw_paths: list, png_dir: Path) -> list:
        png_dir.mkdir(parents=True, exist_ok=True)
        png_paths = []
        for raw in sorted(raw_paths, key=lambda p: p.name):
            dest = png_dir / (raw.stem + ".png")
            if dest.exists() and dest.stat().st_size > 512:
                png_paths.append(dest)
                continue
            try:
                with Image.open(raw) as img:
                    rgb = img.convert("RGB")
                    rgb.save(dest, format="PNG", optimize=False, compress_level=1)
                print(f"   [+] {raw.name}  ->  {dest.name}")
                png_paths.append(dest)
            except Exception as e:
                print(f"   [x] Convert {raw.name}: {e}")
        return sorted(png_paths, key=lambda p: p.name)

    def get_png_dimensions(png_paths: list) -> list:
        dims = []
        for p in png_paths:
            try:
                with Image.open(p) as img:
                    dims.append((img.width, img.height))
            except Exception:
                dims.append((0, 0))
        return dims

DEFAULT_REFERER = "https://manhuarmtl.com/"


def _is_valid_image_bytes(data: bytes) -> bool:
    """Magic-bytes check: JPEG (FF D8) / PNG / WEBP (RIFF....WEBP) / GIF."""
    if not data or len(data) < 2:
        return False
    # JPEG
    if data[0] == 0xFF and data[1] == 0xD8:
        return True
    # PNG 89 50 4E 47
    if len(data) >= 4 and data[:4] == b"\x89PNG":
        return True
    # GIF
    if len(data) >= 3 and data[:3] == b"GIF":
        return True
    # WEBP: RIFF....WEBP
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    return False


def _is_valid_image_file(path: Path) -> bool:
    try:
        if not path.exists() or path.stat().st_size <= 512:
            return False
        with open(path, "rb") as f:
            head = f.read(16)
        return _is_valid_image_bytes(head)
    except Exception:
        return False


def _download_one_scraping(args):
    url, save_path, idx, total, referer = args
    if _is_valid_image_file(save_path):
        print(f"   [=] {idx:03d}/{total:03d}  {save_path.name} (cached)")
        return save_path
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": referer,
    }
    last_error = None
    for attempt in range(1, DL_MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers=headers, timeout=DL_TIMEOUT)
            r.raise_for_status()
            if not _is_valid_image_bytes(r.content[:16] if len(r.content) >= 16 else r.content):
                raise ValueError("invalid image magic bytes")
            save_path.write_bytes(r.content)
            print(f"   [v] {idx:03d}/{total:03d}  {save_path.name}")
            return save_path
        except Exception as e:
            last_error = e
            print(f"   [!] {idx:03d}/{total:03d}  retry {attempt}/{DL_MAX_RETRIES} - {e}")
            time.sleep(DL_DELAY)
    print(f"   [x] {idx:03d}/{total:03d}  {url[:60]} - {last_error}")
    return None


def download_images_scraping(urls: list, raw_dir, referer: str = DEFAULT_REFERER) -> list:
    """Download chapter images with ThreadPoolExecutor(6) + magic-bytes check."""
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    args = []
    for i, url in enumerate(urls, 1):
        ext = Path(urlparse(url).path).suffix.lower()
        if ext not in {".webp", ".jpg", ".jpeg", ".png", ".gif"}:
            ext = ".webp"
        args.append((url, raw_dir / f"page_{i:03d}{ext}", i, len(urls), referer))

    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_download_one_scraping, a): a for a in args}
        for f in as_completed(futures):
            r = f.result()
            if r:
                results.append(r)

    return sorted(results, key=lambda p: p.name)
