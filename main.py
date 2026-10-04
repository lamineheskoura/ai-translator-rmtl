#!/usr/bin/env python3
"""
Manga AI Editor — главная точка входа
=======================================
Использование:
    python main.py                  # Запустить редактор (веб-сервер)
    python main.py --scrape URL     # Скрейпинг без редактора
    python main.py --help           # Справка
"""

import sys
import argparse
import webbrowser
from pathlib import Path

# Force UTF-8 for stdout/stderr (fixes cp1252 charmap errors on Windows)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Add project root to path
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# Load .env first (API keys, OUTPUT dir, port) — safe no-op if missing
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def ensure_dirs() -> Path:
    """Create required working folders. Returns the output base dir."""
    import os
    out = Path(os.environ.get("MANGA_OUTPUT_DIR", "") or (ROOT / "output"))
    out.mkdir(parents=True, exist_ok=True)
    (ROOT / "fonts" / "_user").mkdir(parents=True, exist_ok=True)
    return out


APP_CONFIG_FILE = ROOT / "config.json"


def get_app_config() -> dict:
    if APP_CONFIG_FILE.exists():
        try:
            import json
            return json.loads(APP_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def resolve_output_dir() -> Path:
    import os
    env = (os.environ.get("MANGA_OUTPUT_DIR", "") or "").strip()
    if env:
        return Path(env)
    cfg = get_app_config()
    if cfg.get("output_dir"):
        return Path(cfg["output_dir"])
    return ROOT / "output"


def ensure_fonts() -> None:
    """Guarantee at least one Arabic-capable font exists.

    Keeps bundled/_user fonts; otherwise downloads Amiri (OFL) once.
    Offline machines keep working with system fonts (with a warning).
    """
    fonts_dir = ROOT / "fonts"
    user_dir = fonts_dir / "_user"
    user_dir.mkdir(parents=True, exist_ok=True)
    have = [p for p in list(fonts_dir.glob("*.ttf")) + list(fonts_dir.glob("*.otf"))
            + list(user_dir.glob("*.ttf")) + list(user_dir.glob("*.otf"))
            if p.is_file()]
    if have:
        return
    url = ("https://github.com/google/fonts/raw/main/ofl/amiri/"
           "Amiri-Regular.ttf")
    dest = user_dir / "Amiri-Regular.ttf"
    try:
        import urllib.request
        print("[i] No Arabic font found — downloading Amiri (OFL, once)...")
        urllib.request.urlretrieve(url, str(dest))
        print(f"[+] Font saved: {dest}")
    except Exception as e:
        print(f"[!] Font download failed ({e}). "
              f"Put a .ttf/.otf Arabic font in {user_dir} manually.")


def pick_free_port(host: str, start: int, tries: int = 11) -> int:
    import socket
    for p in range(start, start + tries):
        s = socket.socket()
        try:
            s.bind((host, p))
            s.close()
            return p  # truly free (bind-tested, not just probed)
        except OSError:
            try:
                s.close()
            except Exception:
                pass
            continue
    # Every preferred port busy: let the OS pick a random free one.
    s = socket.socket()
    s.bind((host, 0))
    port = s.getsockname()[1]
    s.close()
    print(f"[i] Ports {start}-{start + tries - 1} busy, using random port {port}")
    return port


APP_LOCK_FILE = ROOT / "output" / ".manga.lock"


def single_instance_or_exit(host: str, port: int) -> None:
    """Prevent two app copies from corrupting chapters/queue.

    If a live server answers on --port (or neighbours), open the browser
    on it and exit instead of starting a second copy.
    """
    import json
    import urllib.request
    for p in range(port, port + 12):
        try:
            with urllib.request.urlopen(f"http://{host}:{p}/api/chapters",
                                        timeout=1) as r:
                if r.status == 200:
                    print(f"[i] App already running on {host}:{p} - opening it.")
                    webbrowser.open(f"http://{host}:{p}")
                    raise SystemExit(0)
        except SystemExit:
            raise
        except Exception:
            continue
    try:
        APP_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        APP_LOCK_FILE.write_text(json.dumps({"port": port}), encoding="utf-8")
    except Exception:
        pass


def start_server(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = True):
    """Start the web editor server."""
    import os
    try:
        ensure_dirs()
    except PermissionError as e:
        print(f"[!] {e}")
        input("Press Enter to exit...")
        raise SystemExit(1)
    ensure_fonts()
    single_instance_or_exit(host, port)
    port = pick_free_port(host, port)
    # Fix for Windows cp1252 console - use simple ASCII
    print(f"""
+----------------------------------------------------+
|              Manga AI Editor v1.0                  |
|                                                     |
|  http://{host}:{port}                               |
|                                                     |
|  Open browser and start editing                     |
|                                                     |
|  Ctrl+C to stop the server                          |
+----------------------------------------------------+
""")

    if open_browser:
        import threading
        threading.Timer(
            1.5, webbrowser.open, args=(f"http://{host}:{port}",)).start()

    import uvicorn
    uvicorn.run(
        "editor.server:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )


def run_scraper(url: str, headless: bool = True):
    """Run scraper only mode (no local translation — translation via server/queue)."""
    from scraper.coordinator import scrape_chapter

    print(f"\n{'='*60}")
    print(f"  Manga AI - Scraper")
    print(f"{'='*60}")

    ch_dir = scrape_chapter(url, headless=headless)

    print(f"\n[+] Done! Editor: python main.py")


def main():
    import multiprocessing
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(
        description="Manga AI Editor — Full pipeline: scrape → translate → edit → export",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
   python main.py                          # Запустить редактор
   python main.py --scrape "https://..."   # Скрейпинг
   python main.py --port 8080              # Другой порт
        """,
    )

    parser.add_argument("--scrape", type=str, metavar="URL",
                        help="URL главы для скрейпинга")
    parser.add_argument("--no-headless", action="store_true",
                        help="Показать окно браузера при скрейпинге")
    parser.add_argument("--host", type=str, default="127.0.0.1",
                        help="Хост для веб-сервера (по умолч. 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None,
                        help="Порт для веб-сервера (по умолч. MANGA_PORT أو 8000)")
    parser.add_argument("--no-browser", action="store_true",
                        help="Не открывать браузер автоматически")

    args = parser.parse_args()

    if args.scrape:
        run_scraper(
            args.scrape,
            headless=not args.no_headless,
        )
    else:
        import os
        try:
            port = int(args.port or os.environ.get("MANGA_PORT", "") or 8000)
        except ValueError:
            port = 8000
        start_server(
            host=args.host,
            port=port,
            open_browser=not args.no_browser,
        )


if __name__ == "__main__":
    main()
