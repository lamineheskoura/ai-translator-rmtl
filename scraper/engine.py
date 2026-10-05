import re
import os
import time
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from PIL import Image

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException
    from webdriver_manager.chrome import ChromeDriverManager
    _SELENIUM_OK = True
except Exception as _e:  # partial install on worker machines: spider path still works
    webdriver = None
    Service = None
    By = None
    WebDriverWait = None
    EC = None
    TimeoutException = Exception
    ChromeDriverManager = None
    _SELENIUM_OK = False
    _SELENIUM_IMPORT_ERROR = str(_e)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://manhuarmtl.com/",
}
MAX_WORKERS = 6
DL_DELAY = 0.12
PAGE_SETTLE_TIME = 3.5
DL_TIMEOUT = 10  # per-image timeout (seconds) - down from 45 to fail fast
DL_MAX_RETRIES = 2


def sanitize(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()


def _find_brave() -> str | None:
    candidates = [
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\BraveSoftware\Brave-Browser\Application\brave.exe"),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None


def _find_chrome() -> str | None:
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    try:
        out = subprocess.run(["where", "chrome"], capture_output=True,
                             timeout=10, text=True)
        for line in (out.stdout or "").splitlines():
            line = line.strip().strip('"')
            if line.lower().endswith("chrome.exe") and os.path.exists(line):
                return line
    except Exception:
        pass
    return None


def check_browser_env() -> dict:
    """Preflight: what scraping engines can actually run here?

    Returns {"scrapling": bool, "brave": path|None, "chrome": path|None,
             "selenium": bool, "hint": str}. Never raises.
    """
    info: dict = {"scrapling": False, "brave": None, "chrome": None,
                  "selenium": False, "hint": ""}
    try:
        import scrapling  # noqa: F401
        info["scrapling"] = True
    except Exception as e:
        info["hint"] = f"scrapling missing: {e}"
        return info
    # scrapling browser downloaded? (patchright/playwright chromium)
    try:
        cands = [
            Path.home() / ".cache" / "ms-playwright",
            Path(os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright")),
            Path(os.path.expandvars(r"%APPDATA%")) / "ms-playwright",
        ]
        for c in cands:
            try:
                if c.exists() and any(c.iterdir()):
                    info["scrapling_browser"] = True
                    break
            except Exception:
                continue
        else:
            info["scrapling_browser"] = False
            info["hint"] = ("scrapling browser not downloaded — "
                            "re-run install.bat (step 5/5)")
    except Exception:
        info["scrapling_browser"] = False
    info["brave"] = _find_brave()
    info["chrome"] = _find_chrome()
    try:
        import selenium  # noqa: F401
        info["selenium"] = True
    except Exception:
        pass
    if not info["brave"] and not info["chrome"]:
        info["hint"] += (" | No Brave/Chrome found — install official Chrome "
                         "or rely on the scrapling browser.")
    return info


def _clear_stale_wdm_locks(max_age_min: float = 10.0) -> int:
    """Delete webdriver-manager lock files left by crashed runs.

    A stale `wdm-lock-*` makes every later run hang with
    "Timed out waiting for webdriver". Returns count removed.
    """
    removed = 0
    try:
        cache = Path.home() / ".wdm"
        if not cache.exists():
            return 0
        import time as _t
        now = _t.time()
        for lock in cache.rglob("wdm-lock-*"):
            try:
                age_min = (now - lock.stat().st_mtime) / 60.0
                if age_min > max_age_min:
                    if lock.is_dir():
                        shutil.rmtree(lock, ignore_errors=True)
                    else:
                        lock.unlink(missing_ok=True)
                    removed += 1
                    print(f"   [i] Removed stale webdriver lock "
                          f"({age_min:.0f} min old): {lock.name}")
            except Exception:
                continue
    except Exception:
        pass
    return removed


def _kill_process(name: str):
    try:
        subprocess.run(
            ["taskkill", "/f", "/im", name],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


def _clear_wdm_cache():
    """Delete webdriver-manager cache to force fresh chromedriver download."""
    cache = Path.home() / ".wdm"
    if cache.exists():
        try:
            shutil.rmtree(cache)
            print(f"   [i] Cleared chromedriver cache: {cache}")
        except Exception as e:
            print(f"   [!] Could not clear cache: {e}")


def _build_options(
    headless: bool,
    binary_path: str | None = None,
    profile_dir: str | None = None,
) -> "webdriver.ChromeOptions":
    if not _SELENIUM_OK:
        raise RuntimeError("Selenium unavailable.")
    options = webdriver.ChromeOptions()

    if headless:
        options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,10000")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--remote-debugging-port=0")
    options.add_argument("--disable-search-engine-choice-screen")

    if profile_dir:
        options.add_argument(f"--user-data-dir={profile_dir}")

    if binary_path:
        options.binary_location = binary_path

    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    return options


def setup_driver(headless: bool = True, browser: str = "brave") -> "webdriver.Chrome":
    if not _SELENIUM_OK:
        raise RuntimeError(
            "مسار Selenium غير متوفر (selenium ناقص: "
            f"{_SELENIUM_IMPORT_ERROR[:150]}). أعد تشغيل install.bat.")
    # NOTE: _kill_process("chromedriver.exe") disabled (no-op) to avoid killing adjacent tasks.
    pass

    brave_path = _find_brave() if browser == "brave" else None
    if browser == "brave" and not brave_path:
        print("   [!] Brave not found. Falling back to Chrome.")
        browser = "chrome"
    if browser == "chrome" and not _find_chrome():
        # No system browser at all: the driver alone cannot render pages.
        # Fail fast with a human message instead of a cryptic timeout.
        raise RuntimeError(
            "لا يوجد Brave ولا Chrome على هذا الجهاز. "
            "ثبّت متصفح Chrome الرسمي من google.com/chrome ثم أعد المحاولة، "
            "أو تأكد أن خطوة متصفح السكرابر في install.bat تمت بنجاح."
        )

    # A previous crashed run may have left a webdriver-manager lock behind;
    # without cleanup every later run hangs on "Timed out waiting for webdriver".
    _clear_stale_wdm_locks()

    browser_order = [browser]
    if browser == "brave":
        browser_order.append("chrome")

    last_error = None
    temp_dirs = []

    for attempt_browser in browser_order:
        is_brave = attempt_browser == "brave"
        label = "Brave" if is_brave else "Chrome"
        profile_dir = tempfile.mkdtemp(prefix=f"manga_{attempt_browser}_")
        temp_dirs.append(profile_dir)

        print(f"   [i] Launching {label} (headless={headless})...")
        if is_brave:
            print(f"       binary: {brave_path}")

        opts = _build_options(
            headless=headless,
            binary_path=brave_path if is_brave else None,
            profile_dir=profile_dir,
        )

        try:
            try:
                driver_path = ChromeDriverManager().install()
            except Exception as e:
                msg = str(e)
                hint = ("تعذّر تحميل chromedriver (إنترنت؟ بروكسي؟ مضاد فيروسات؟). "
                        "أعد تشغيل install.bat، وإن تكرر أرسل install.log للدعم. "
                        f"التفاصيل: {msg[:200]}")
                raise RuntimeError(hint)
            service = Service(driver_path)
            driver = webdriver.Chrome(service=service, options=opts)
            driver.execute_script(
                "Object.defineProperty(navigator, 'webdriver', "
                "{get: () => undefined})"
            )
            # success — clean up unused temp dirs from failed attempts
            for d in temp_dirs:
                if d != profile_dir:
                    shutil.rmtree(d, ignore_errors=True)
            return driver
        except Exception as e:
            print(f"   [!] {label} failed: {e}")
            last_error = e
            if is_brave:
                print("   [i] Retrying with Chrome...")
            continue

    # All attempts failed — clean up and raise
    for d in temp_dirs:
        shutil.rmtree(d, ignore_errors=True)

    msg = f"لم يتمكن من تشغيل أي متصفح. الخطأ الأخير: {last_error}"
    raise RuntimeError(msg)


def get_rendered_image_width(driver, imgs_elements: list) -> float:
    if imgs_elements:
        w = float(imgs_elements[0].size.get("width", 0))
        if w > 0:
            return w
    try:
        w = driver.execute_script(
            "var imgs = document.querySelectorAll('.reading-content img.wp-manga-chapter-img');"
            "if (imgs.length > 0) return imgs[0].getBoundingClientRect().width;"
            "return 0;"
        )
        return float(w) if w else 0.0
    except Exception:
        return 0.0


def get_image_x_offset(driver) -> float:
    try:
        offset = driver.execute_script("""
            var img = document.querySelector(
                '.reading-content img.wp-manga-chapter-img'
            );
            var container = document.querySelector(
                '#ocr-global-overlay-container'
            );
            if (!img || !container) return 0;
            var imgRect = img.getBoundingClientRect();
            var containerRect = container.getBoundingClientRect();
            return Math.max(0, imgRect.left - containerRect.left);
        """)
        val = float(offset) if offset is not None else 0.0
        if val > 0:
            print(f"   [i] Image X offset: {val:.2f}px")
        return val
    except Exception as e:
        print(f"   [!] X offset detection failed ({e}) - using 0")
        return 0.0


def _has_valid_image_magic(path: Path) -> bool:
    """Check magic-bytes for JPEG/PNG/WEBP/GIF cache validation."""
    try:
        with open(path, "rb") as f:
            head = f.read(4)
        if len(head) < 3:
            return False
        if head[:3] == b"\xff\xd8\xff":
            return True  # JPEG
        if len(head) < 4:
            return False
        if head == b"\x89PNG":
            return True  # PNG
        if head == b"RIFF":
            return True  # WEBP (RIFF....WEBP)
        if head == b"GIF8":
            return True  # GIF
        return False
    except Exception:
        return False


def _download_one(args):
    url, save_path, idx, total = args
    if save_path.exists() and save_path.stat().st_size > 512:
        if not _has_valid_image_magic(save_path):
            try:
                save_path.unlink()
            except Exception:
                pass
            print(f"   [!] {idx:03d}/{total:03d}  {save_path.name} (bad magic — re-download)")
        else:
            print(f"   [=] {idx:03d}/{total:03d}  {save_path.name} (cached)")
            return save_path
    last_error = None
    for attempt in range(1, DL_MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=DL_TIMEOUT)
            r.raise_for_status()
            save_path.write_bytes(r.content)
            print(f"   [v] {idx:03d}/{total:03d}  {save_path.name}")
            return save_path
        except Exception as e:
            last_error = e
            print(f"   [!] {idx:03d}/{total:03d}  retry {attempt}/{DL_MAX_RETRIES} - {e}")
            time.sleep(DL_DELAY)
    print(f"   [x] {idx:03d}/{total:03d}  {url[:60]} - {last_error}")
    return None


def download_images(urls: list, out_dir: Path) -> list:
    args = []
    for i, url in enumerate(urls, 1):
        ext = Path(urlparse(url).path).suffix.lower()
        if ext not in {".webp", ".jpg", ".jpeg", ".png", ".gif"}:
            ext = ".webp"
        args.append((url, out_dir / f"page_{i:03d}{ext}", i, len(urls)))

    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_download_one, a): a for a in args}
        for f in as_completed(futures):
            r = f.result()
            if r:
                results.append(r)

    return sorted(results, key=lambda p: p.name)


def convert_all_to_png(raw_paths: list, png_dir: Path) -> list:
    png_dir.mkdir(parents=True, exist_ok=True)
    png_paths = []
    for raw in sorted(raw_paths, key=lambda p: p.name):
        dest = png_dir / (raw.stem + ".png")
        if dest.exists() and dest.stat().st_size > 512:
            if not _has_valid_image_magic(dest):
                try:
                    dest.unlink()
                except Exception:
                    pass
                print(f"   [!] {dest.name} (bad magic — re-convert)")
            else:
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
