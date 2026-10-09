import copy
import json
import zipfile
import io
import os
import re
import queue
import shutil
import time
import threading
import uuid
from pathlib import Path
from typing import Optional, Union, Any

from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from .exporter import get_available_fonts, export_chapter, _parse_color, autofit_chapter_boxes, has_any_font
from translator.providers import (
    get_providers_public,
    get_provider,
    save_provider_config,
    delete_provider,
    test_connection,
    fetch_models,
    translate_texts,
)

app = FastAPI(title="Manga AI Editor", version="1.0.0")


def _atomic_json_dump(path, payload):
    """Write JSON atomically so interrupted writes don't corrupt the file."""
    import os as _os
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        try:
            _os.fsync(f.fileno())
        except Exception:
            pass
    _os.replace(tmp, str(path))


# Per-file locks so concurrent read-modify-write cycles don't race
_file_locks: dict[str, threading.Lock] = {}
_file_locks_lock = threading.Lock()


def _get_file_lock(path):
    str_path = str(path)
    with _file_locks_lock:
        if str_path not in _file_locks:
            _file_locks[str_path] = threading.Lock()
        return _file_locks[str_path]


from contextlib import contextmanager


@contextmanager
def _file_transaction(path):
    """Acquire per-file lock, read JSON, yield data, write back on success."""
    lock = _get_file_lock(path)
    lock.acquire()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        yield data
        _atomic_json_dump(path, data)
    finally:
        lock.release()


# ── Scrape task tracking (background) ──────────────────────
scrape_tasks = {}  # task_id -> {"status","url","log","slug","chapter","error","done"}
scrape_tasks_lock = threading.Lock()

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(127\.0\.0\.1|localhost)(:\d+)?",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

OUTPUT_DIR = Path(__file__).parent.parent / "output"
FONTS_DIR = Path(__file__).parent.parent / "fonts"
FONTS_USER_DIR = FONTS_DIR / "_user"
APP_CONFIG_FILE = Path(__file__).parent.parent / "config.json"


def _resolve_output_dir() -> Path:
    """Base manga folder: env MANGA_OUTPUT_DIR > config.json > ./output.

    Every manga gets its own subfolder by slug; chapters of the same
    manga always land in that same folder. A stale absolute path from
    another machine (config copied over) is ignored safely.
    """
    env = (os.environ.get("MANGA_OUTPUT_DIR", "") or "").strip()
    if env:
        return Path(env)
    try:
        if APP_CONFIG_FILE.exists():
            cfg = json.loads(APP_CONFIG_FILE.read_text(encoding="utf-8"))
            if cfg.get("output_dir"):
                p = Path(cfg["output_dir"])
                if p.exists() or p == Path(__file__).parent.parent / "output":
                    return p
                print(f"[i] Ignoring stale output_dir from another machine: {p}")
    except Exception:
        pass
    return Path(__file__).parent.parent / "output"


OUTPUT_DIR = _resolve_output_dir()
try:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FONTS_USER_DIR.mkdir(parents=True, exist_ok=True)
except PermissionError as e:
    raise RuntimeError(
        "Cannot write the app folder (read-only? OneDrive lock?). "
        f"Move the app elsewhere or set another manga folder. [{e}]")


class OutputDirPayload(BaseModel):
    output_dir: str


@app.get("/api/settings")
def get_settings():
    return {"output_dir": str(OUTPUT_DIR)}


@app.post("/api/settings/output-dir")
def set_output_dir(payload: OutputDirPayload):
    global OUTPUT_DIR
    p = Path(payload.output_dir or "").expanduser()
    if not str(p).strip():
        raise HTTPException(400, "Empty output_dir")
    try:
        p.mkdir(parents=True, exist_ok=True)
        test = p / ".write_test"
        test.write_text("ok", encoding="utf-8")
        test.unlink()
    except Exception as e:
        raise HTTPException(400, f"Folder not writable: {e}")
    OUTPUT_DIR = p.resolve()
    try:
        with _get_file_lock(APP_CONFIG_FILE):
            cfg = {}
            if APP_CONFIG_FILE.exists():
                cfg = json.loads(APP_CONFIG_FILE.read_text(encoding="utf-8"))
            cfg["output_dir"] = str(OUTPUT_DIR)
            _atomic_json_dump(APP_CONFIG_FILE, cfg)
    except Exception:
        pass
    return {"status": "ok", "output_dir": str(OUTPUT_DIR)}


DEFAULT_EXPORT_SETTINGS: dict = {
    "format": "webp",
    "quality": 90,
    "merge": True,
    "max_height": 9000,
    "font_scale": 1.0,
    "line_gap": 2,
    "force_stroke": True,
    "stroke_w": 1.5,
    "stroke_color": "#ffffff",
    "bg_color": "#ffffff",
    "export_dir": "",
}


def _load_export_settings() -> dict:
    merged = dict(DEFAULT_EXPORT_SETTINGS)
    try:
        if APP_CONFIG_FILE.exists():
            cfg = json.loads(APP_CONFIG_FILE.read_text(encoding="utf-8"))
            saved = (cfg or {}).get("export")
            if isinstance(saved, dict):
                for k in merged:
                    if k in saved and saved[k] is not None:
                        merged[k] = saved[k]
    except Exception:
        pass
    return merged


def _save_export_settings(patch: dict) -> dict:
    current = _load_export_settings()
    for k in DEFAULT_EXPORT_SETTINGS:
        if k in patch and patch[k] is not None:
            current[k] = patch[k]
    try:
        with _get_file_lock(APP_CONFIG_FILE):
            cfg = {}
            if APP_CONFIG_FILE.exists():
                cfg = json.loads(APP_CONFIG_FILE.read_text(encoding="utf-8")) or {}
            cfg["export"] = current
            _atomic_json_dump(APP_CONFIG_FILE, cfg)
    except Exception:
        pass
    return current


class ExportSettingsPayload(BaseModel):
    format: Optional[str] = None
    quality: Optional[int] = None
    merge: Optional[bool] = None
    max_height: Optional[int] = None
    font_scale: Optional[float] = None
    line_gap: Optional[int] = None
    force_stroke: Optional[bool] = None
    stroke_w: Optional[float] = None
    stroke_color: Optional[str] = None
    bg_color: Optional[str] = None
    export_dir: Optional[str] = None


def _resolve_export_dir(slug: str, chapter: str) -> Path:
    """Where finished exported files live (the 'done' archive).

    - If the user configured an export base (settings): <base>/<slug>/chapter_<N>/
    - Default (smart): <app>/published/<slug>/chapter_<N>/ — created automatically,
      one folder per manga, finished chapters ordered inside. The working
      output/<slug>/ tree stays for work-in-progress only.
    """
    _safe_slug(slug)
    base = (_load_export_settings().get("export_dir") or "").strip()
    if base:
        d = Path(base) / slug / f"chapter_{chapter}"
        d.mkdir(parents=True, exist_ok=True)
        return d
    d = Path(__file__).parent.parent / "published" / slug / f"chapter_{chapter}"
    d.mkdir(parents=True, exist_ok=True)
    return d


@app.get("/api/settings/export")
def get_export_settings():
    return _load_export_settings()


@app.post("/api/settings/export")
def save_export_settings(payload: ExportSettingsPayload):
    patch = {k: v for k, v in payload.model_dump().items() if v is not None}
    if patch.get("export_dir"):
        p = Path(str(patch["export_dir"])).expanduser()
        try:
            p.mkdir(parents=True, exist_ok=True)
            t = p / ".write_test"
            t.write_text("ok", encoding="utf-8")
            t.unlink()
            patch["export_dir"] = str(p.resolve())
        except Exception as e:
            raise HTTPException(400, f"Export folder not writable: {e}")
    elif "export_dir" in patch and not patch["export_dir"]:
        patch["export_dir"] = ""
    return _save_export_settings(patch)


# ── Path protection ──────────────────────────────────────
_SAFE_SLUG_RE = re.compile(r"^[a-z0-9-]+$")
_CHAPTER_RE = re.compile(r"^[0-9]+(\.[0-9]+)?$")


def _safe_slug(s: str) -> str:
    """Allow only lowercase alphanumeric + hyphen slugs."""
    if not s or not _SAFE_SLUG_RE.match(s):
        raise HTTPException(400, f"Invalid slug: {s!r}")
    return s


def _chapter_dir(slug: str, chapter: str) -> Path:
    """Validate slug/chapter and return resolved chapter dir inside OUTPUT_DIR."""
    _safe_slug(slug)
    if not chapter or not _CHAPTER_RE.match(str(chapter)):
        raise HTTPException(400, f"Invalid chapter: {chapter!r}")
    base = OUTPUT_DIR.resolve()
    ch_dir = (base / slug / f"chapter_{chapter}").resolve()
    try:
        ch_dir.relative_to(base)
    except ValueError:
        raise HTTPException(400, "Invalid chapter path")
    return ch_dir


class TextUpdate(BaseModel):
    arabic_text: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None
    font_size_px: Optional[float] = None
    style: Optional[dict] = None


class BatchStyleUpdate(BaseModel):
    style: dict
    scope: str = "all"


class ChapterSavePayload(BaseModel):
    pages: list[dict]


def _default_text_style(font_size: float = 45) -> dict:
    return {
        "font": "Hayah",
        "font_size": int(font_size),
        "line_height": 1.1,
        "color": "#000000",
        "stroke_color": "#ffffff",
        "stroke_width": 1,
        "stroke_enabled": True,
        "align": "center",
        "rotation": 0,
    }


def _normalize_text_obj(t: dict, page_num: Optional[int] = None) -> dict:
    font_size = float(t.get("font_size_px", t.get("style", {}).get("font_size", 45)) or 45)
    if font_size < 6:
        font_size = 45

    incoming_style = dict(t.get("style") or {})
    if "stroke_color" not in incoming_style and t.get("stroke_color"):
        incoming_style["stroke_color"] = t.get("stroke_color")
    if "stroke_width" not in incoming_style and t.get("stroke_width") is not None:
        incoming_style["stroke_width"] = t.get("stroke_width")
    if "stroke_enabled" not in incoming_style and t.get("stroke_enabled") is not None:
        incoming_style["stroke_enabled"] = t.get("stroke_enabled")
    style = {**_default_text_style(font_size), **incoming_style}
    if str(style.get("color", "")).startswith("rgb("):
        c = _parse_color(style["color"])
        style["color"] = f"#{c[0]:02x}{c[1]:02x}{c[2]:02x}"
    if str(style.get("stroke_color", "")).startswith("rgb("):
        c = _parse_color(style["stroke_color"])
        style["stroke_color"] = f"#{c[0]:02x}{c[1]:02x}{c[2]:02x}"
    if str(style.get("stroke_color", "")).startswith("#"):
        style["stroke_color"] = str(style["stroke_color"]).lower()

    stroke_enabled = style.get("stroke_enabled", True)
    if isinstance(stroke_enabled, str):
        stroke_enabled = stroke_enabled.lower() not in ("false", "0", "")
    else:
        stroke_enabled = bool(stroke_enabled)
    style["stroke_enabled"] = stroke_enabled

    stroke_width = float(style.get("stroke_width", 1) or 0)
    if stroke_enabled and stroke_width <= 0:
        stroke_width = 1
    style["stroke_width"] = stroke_width

    style["font_size"] = int(float(style.get("font_size", font_size) or font_size))
    style["line_height"] = max(0.7, min(3.0, float(style.get("line_height", t.get("line_height", 1.1)) or 1.1)))

    normalized = dict(t)
    normalized["page"] = page_num if page_num is not None else t.get("page")
    normalized["original_text"] = t.get("original_text", "") or ""
    normalized["arabic_text"] = t.get("arabic_text", "") or ""
    normalized["x"] = float(t.get("x", 0) or 0)
    normalized["y"] = float(t.get("y", 0) or 0)
    normalized["width"] = max(20.0, float(t.get("width", 200) or 200))
    normalized["height"] = max(10.0, float(t.get("height", 60) or 60))
    normalized["font_size_px"] = font_size
    normalized["line_height"] = style["line_height"]
    normalized["scale_factor"] = float(t.get("scale_factor", 1.0) or 1.0)
    normalized["style"] = style
    return normalized


def _normalize_chapter_data(data: dict):
    total_texts = 0
    for page in data.get("pages", []):
        page_num = page.get("page")
        texts = page.get("texts", []) or []
        page["texts"] = [_normalize_text_obj(t, page_num) for t in texts if t.get("id")]
        # Hard cap: no box may exceed its page (save-time choke point —
        # covers panel/resize/PUT/drag in one place).
        try:
            pw = float(page.get("width", 0) or 0)
        except Exception:
            pw = 0
        if pw > 20:
            for t in page["texts"]:
                try:
                    w = min(float(t.get("width", 200) or 200), pw - 8.0)
                    w = max(20.0, w)
                    x = min(max(float(t.get("x", 0) or 0), 0.0),
                            max(0.0, pw - w))
                    t["width"] = round(w, 2)
                    t["x"] = round(x, 2)
                except Exception:
                    continue
        total_texts += len(page["texts"])
    data["total_texts"] = total_texts


def _chapter_sort_key(p) -> tuple:
    """Numeric chapter sort: chapter_2 before chapter_10 (not lexicographic)."""
    m = re.search(r"chapter_([\d.]+)", p.name if hasattr(p, "name") else str(p))
    try:
        return (0, float(m.group(1))) if m else (1, 0.0)
    except Exception:
        return (1, 0.0)


@app.get("/api/chapters")
def list_chapters():
    if not OUTPUT_DIR.exists():
        return {"chapters": []}
    chapters = []
    for slug_dir in sorted(OUTPUT_DIR.iterdir()):
        # Skip hidden/system dirs (e.g. output/.trash from chapter deletes)
        # — otherwise trashed chapters haunt the list as undeletable
        # phantoms (their real dir is gone -> every action 404s).
        if not slug_dir.is_dir() or slug_dir.name.startswith("."):
            continue
        for ch_dir in sorted(slug_dir.iterdir(), key=_chapter_sort_key):
            if ch_dir.name.startswith("."):
                continue
            if ch_dir.is_dir() and (ch_dir / "chapter_data.json").exists():
                    try:
                        with open(ch_dir / "chapter_data.json", "r", encoding="utf-8") as f:
                            data = json.load(f)
                    except Exception:
                        continue
                    _n_tr = _n_ap = _n_tx = 0
                    for _pg in data.get("pages", []) or []:
                        for _t in _pg.get("texts", []) or []:
                            _n_tx += 1
                            if (_t.get("arabic_text") or "").strip():
                                _n_tr += 1
                            if _t.get("approved") == "approved":
                                _n_ap += 1
                    try:
                        _has_exp = any((ch_dir / "exported").glob("*")) if (ch_dir / "exported").exists() else False
                    except Exception:
                        _has_exp = False
                    chapters.append({
                        "slug": data.get("slug", slug_dir.name),
                        "chapter": data.get("chapter", ch_dir.name),
                        "title": data.get("title", ""),
                        "path": str(ch_dir),
                        "total_pages": data.get("total_images", 0),
                        "total_texts": data.get("total_texts", _n_tx),
                        "translated_texts": _n_tr,
                        "approved_texts": _n_ap,
                        "has_exported": bool(_has_exp),
                    })
    return {"chapters": chapters}


@app.get("/api/chapter/{slug}/{chapter}")
def get_chapter(slug: str, chapter: str):
    ch_dir = _chapter_dir(slug, chapter)
    json_path = ch_dir / "chapter_data.json"
    if not json_path.exists():
        raise HTTPException(404, f"Chapter not found: {ch_dir}")

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    _normalize_chapter_data(data)
    return data


@app.get("/api/chapter/{slug}/{chapter}/page/{page_num}")
def get_page_image(slug: str, chapter: str, page_num: int):
    ch_dir = _chapter_dir(slug, chapter)
    png_path = ch_dir / "pages" / f"page_{page_num:03d}.png"
    if not png_path.exists():
        raise HTTPException(404, f"Page {page_num} not found")
    return FileResponse(str(png_path), media_type="image/png")


@app.put("/api/chapter/{slug}/{chapter}/text/{text_id}")
def update_text(slug: str, chapter: str, text_id: str, update: TextUpdate):
    ch_dir = _chapter_dir(slug, chapter)
    json_path = ch_dir / "chapter_data.json"

    with _file_transaction(json_path) as data:
        found = False
        for page in data.get("pages", []):
            for t in page.get("texts", []):
                if t["id"] == text_id:
                    if update.arabic_text is not None:
                        t["arabic_text"] = update.arabic_text
                    if update.x is not None:
                        t["x"] = update.x
                    if update.y is not None:
                        t["y"] = update.y
                    if update.width is not None:
                        t["width"] = update.width
                    if update.height is not None:
                        t["height"] = update.height
                    if update.font_size_px is not None:
                        t["font_size_px"] = update.font_size_px
                    if update.style is not None:
                        t["style"] = {**t.get("style", {}), **update.style}
                    found = True
                    break
            if found:
                break

        if not found:
            raise HTTPException(404, f"Text {text_id} not found")

        _normalize_chapter_data(data)

    return {"status": "ok", "id": text_id}


@app.put("/api/chapter/{slug}/{chapter}")
def save_chapter(slug: str, chapter: str, payload: ChapterSavePayload):
    ch_dir = _chapter_dir(slug, chapter)
    json_path = ch_dir / "chapter_data.json"
    if not json_path.exists():
        raise HTTPException(404, f"Chapter not found: {ch_dir}")

    _backup_json(json_path)
    with _file_transaction(json_path) as data:
        old_pages = data.get("pages", [])
        old_pages_by_num = {p.get("page"): p for p in old_pages}
        new_pages = []

        for incoming_page in payload.pages:
            page_num = incoming_page.get("page")
            old_page = old_pages_by_num.get(page_num, {})
            fallback_filename = old_page.get("filename", "")
            if not fallback_filename and isinstance(page_num, int):
                fallback_filename = f"page_{page_num:03d}.png"
            new_pages.append({
                "page": page_num,
                "filename": incoming_page.get("filename", fallback_filename),
                "width": incoming_page.get("width", old_page.get("width", 0)),
                "height": incoming_page.get("height", old_page.get("height", 0)),
                "texts": [_normalize_text_obj(t, page_num) for t in (incoming_page.get("texts", []) or []) if t.get("id")],
            })

        data["pages"] = sorted(new_pages, key=lambda p: (p.get("page") is None, p.get("page", 0)))
        data["slug"] = slug
        data["chapter"] = chapter
        data["total_images"] = len(new_pages)
        _normalize_chapter_data(data)

    return {"status": "ok", "saved_pages": len(payload.pages)}


@app.delete("/api/chapter/{slug}/{chapter}/text/{text_id}")
def delete_text(slug: str, chapter: str, text_id: str):
    ch_dir = _chapter_dir(slug, chapter)
    json_path = ch_dir / "chapter_data.json"
    if not json_path.exists():
        raise HTTPException(404, f"Chapter not found: {ch_dir}")

    with _file_transaction(json_path) as data:
        removed = False
        for page in data.get("pages", []):
            before = len(page.get("texts", []))
            page["texts"] = [t for t in page.get("texts", []) if t.get("id") != text_id]
            if len(page["texts"]) != before:
                removed = True
                break

        if not removed:
            raise HTTPException(404, f"Text {text_id} not found")

        _normalize_chapter_data(data)

    return {"status": "ok", "id": text_id}


@app.put("/api/chapter/{slug}/{chapter}/batch-style")
def batch_update_style(slug: str, chapter: str, update: BatchStyleUpdate,
                       page: Optional[int] = Query(default=None, description="Apply to specific page only")):
    ch_dir = _chapter_dir(slug, chapter)
    json_path = ch_dir / "chapter_data.json"

    _backup_json(json_path)
    with _file_transaction(json_path) as data:
        modified = 0
        pages_to_update = data.get("pages", [])
        if page is not None:
            pages_to_update = [p for p in pages_to_update if p.get("page") == page]

        for page_data in pages_to_update:
            for t in page_data.get("texts", []):
                if "style" not in t:
                    t["style"] = {}
                t["style"] = {**t["style"], **update.style}
                modified += 1

        _normalize_chapter_data(data)

    return {"status": "ok", "modified": modified}


@app.post("/api/chapter/{slug}/{chapter}/export")
def export_chapter_endpoint(
    slug: str, chapter: str,
    export_format: str = Query(default="webp", alias="format"),
    export_quality: int = Query(default=90, alias="quality"),
    export_merge: Optional[bool] = Query(default=None, alias="merge"),
    export_max_height: int = Query(default=9000, alias="max_height"),
    export_pages: str = Query(default="", alias="pages"),
    font_scale: float = Query(default=1.0, alias="font_scale"),
    line_gap: int = Query(default=2, alias="line_gap"),
    bg_color: str = Query(default="#ffffff", alias="bg_color"),
    force_stroke: Optional[bool] = Query(default=None, alias="force_stroke"),
    export_stroke_w: float = Query(default=1.5, alias="export_stroke_w"),
    export_stroke_color: str = Query(default="#ffffff", alias="export_stroke_color"),
):
    ch_dir = _chapter_dir(slug, chapter)
    export_dir = _resolve_export_dir(slug, chapter)
    if not has_any_font():
        raise HTTPException(
            400, "لا يوجد أي خط صالح (ارفع خطاً عربياً من زر F+ في المحرر).")
    page_range = None
    _ex_defaults = _load_export_settings()
    if export_merge is None:
        export_merge = bool(_ex_defaults.get("merge", True))
    if force_stroke is None:
        force_stroke = bool(_ex_defaults.get("force_stroke", True))
    if export_pages:
        try:
            parts = [int(x.strip()) for x in export_pages.split(",") if x.strip()]
            if parts:
                page_range = parts
        except ValueError:
            raise HTTPException(400, "Invalid pages list (example: 1,2,3)")
    bg_rgb = _parse_color(bg_color)
    stroke_rgb = _parse_color(export_stroke_color)
    files = export_chapter(
        ch_dir, export_dir,
        fmt=export_format, quality=export_quality,
        merge=export_merge, max_height=export_max_height,
        page_range=page_range,
        font_scale=font_scale,
        line_gap=line_gap,
        bg_color=bg_rgb,
        force_stroke=force_stroke,
        export_stroke_width=export_stroke_w,
        export_stroke_color=stroke_rgb,
    )
    filenames = [Path(f).name for f in files]
    return {"status": "ok", "exported": str(export_dir), "files": filenames}


@app.get("/api/chapter/{slug}/{chapter}/exported/{filename}")
def get_exported(slug: str, chapter: str, filename: str):
    ch_dir = _chapter_dir(slug, chapter)
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    export_dir = _resolve_export_dir(slug, chapter)
    file_path = (export_dir / filename).resolve()
    try:
        file_path.relative_to(export_dir.resolve())
    except ValueError:
        raise HTTPException(400, "Invalid filename")
    if not file_path.exists():
        # Backward compat: exports made before the published/ era live
        # next to the working files in <chapter>/exported/.
        legacy = (ch_dir / "exported" / filename).resolve()
        try:
            legacy.relative_to(ch_dir.resolve())
        except ValueError:
            raise HTTPException(400, "Invalid filename")
        file_path = legacy
    if not file_path.exists():
        raise HTTPException(404, "File not found")
    ext = Path(filename).suffix.lower()
    media_types = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}
    return FileResponse(str(file_path), media_type=media_types.get(ext, "image/png"))


@app.get("/api/chapter/{slug}/{chapter}/download-zip")
def download_zip(slug: str, chapter: str,
                 dl_format: str = Query(default="webp", alias="format"),
                 dl_quality: int = Query(default=90, alias="quality"),
                 dl_merge: bool = Query(default=False, alias="merge"),
                 dl_max_height: int = Query(default=9000, alias="max_height")):
    ch_dir = _chapter_dir(slug, chapter)
    export_dir = _resolve_export_dir(slug, chapter)

    if not list(export_dir.iterdir()):
        legacy = ch_dir / "exported"
        if legacy.exists() and list(legacy.iterdir()):
            export_dir = legacy  # pre-published-era exports stay reachable
        else:
            export_chapter(ch_dir, export_dir, fmt=dl_format, quality=dl_quality, merge=dl_merge, max_height=dl_max_height)

    buf = io.BytesIO()
    exts = {".png", ".jpg", ".jpeg", ".webp"}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(export_dir.iterdir(), key=_chapter_sort_key):
            if f.suffix.lower() in exts:
                zf.write(f, arcname=f.name)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={slug}_chapter_{chapter}.zip"},
    )


@app.get("/api/fonts")
def list_fonts():
    fonts = get_available_fonts()
    return {"fonts": fonts}


@app.post("/api/fonts/upload")
async def upload_font(file: UploadFile = File(...)):
    FONTS_USER_DIR.mkdir(parents=True, exist_ok=True)

    if not file.filename:
        raise HTTPException(400, "Filename is required")

    safe_name = Path(file.filename).name
    if not safe_name or safe_name in (".", ".."):
        raise HTTPException(400, "Invalid filename")

    ext = Path(safe_name).suffix.lower()
    if ext not in (".ttf", ".otf", ".ttc"):
        raise HTTPException(400, "Only .ttf, .otf, .ttc files allowed")

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(413, "Font file too large (max 5MB)")

    base = FONTS_USER_DIR.resolve()
    dest = (base / safe_name).resolve()
    try:
        dest.relative_to(base)
    except ValueError:
        raise HTTPException(400, "Invalid filename")
    with open(dest, "wb") as f:
        f.write(content)

    return {"status": "ok", "font": safe_name, "path": str(dest)}


# ── Server ───────────────────────────────────────────────

@app.post("/api/shutdown")
def shutdown_server(request: Request, token: Optional[str] = Query(default=None)):
    expected = os.getenv("LOCAL_ADMIN_TOKEN", "")
    provided = token or request.headers.get("x-admin-token") or request.headers.get("x-local-admin-token") or request.headers.get("token", "")
    # Strip Bearer prefix if present
    if provided and provided.lower().startswith("bearer "):
        provided = provided[7:].strip()
    if not expected:
        client_host = request.client.host if request.client else ""
        if client_host not in ("127.0.0.1", "::1"):
            raise HTTPException(403, "Forbidden: shutdown allowed only from 127.0.0.1")
    else:
        if not provided or provided != expected:
            raise HTTPException(403, "Forbidden: invalid admin token")
    t = threading.Timer(0.5, os._exit, args=[0])
    t.start()
    return {"status": "shutting_down"}


@app.post("/api/scrape")
def scrape_new(url: str = Query(..., description="Chapter URL"),
                headless: bool = Query(True, description="Run browser headless"),
                browser: str = Query("brave", description="Browser to use: 'brave' or 'chrome'")):
    """Start scraping in background. Returns task_id immediately."""
    task_id = uuid.uuid4().hex
    with scrape_tasks_lock:
        scrape_tasks[task_id] = {
            "status": "started",
            "url": url,
            "log": f"بدء التحميل في الخلفية (متصفح={browser}, headless={headless})...\n",
            "slug": None,
            "chapter": None,
            "error": None,
            "done": False,
            "cancel_requested": False,
            "started_at": time.time(),
        }
        # keep only the last 50 tasks (drop oldest FINISHED first —
        # evicting a running task orphans its worker with a KeyError).
        if len(scrape_tasks) > 50:
            oldest = sorted(scrape_tasks.items(), key=lambda kv: kv[1].get("started_at", 0))
            for tid, tinfo in oldest[:len(scrape_tasks) - 50]:
                if tinfo.get("done"):
                    scrape_tasks.pop(tid, None)

    def _run_scrape():
        try:
            from scraper.coordinator import scrape_chapter, ScrapeCancelled

            def _task_cancelled():
                with scrape_tasks_lock:
                    t = scrape_tasks.get(task_id)
                    return bool(t and t.get("cancel_requested"))

            print(f"[i] Scrape task {task_id[:8]} starting for {url} (browser={browser}, headless={headless})", flush=True)
            ch_dir = scrape_chapter(url, headless=headless, browser=browser,
                                    should_cancel=_task_cancelled)
            with scrape_tasks_lock:
                t = scrape_tasks.get(task_id)
                if t is None:
                    return
                if t.get("cancel_requested"):
                    t["status"] = "cancelled"
                    t["log"] += "\n✗ أُلغي من المستخدم\n"
                elif ch_dir:
                    t["status"] = "ok"
                    t["slug"] = ch_dir.parent.name
                    t["chapter"] = ch_dir.name.replace("chapter_", "")
                    t["log"] += "\n✓ تم التحميل بنجاح\n"
                    # Library hook: record the download (title opportunistic).
                    try:
                        _t = ""
                        _jp = Path(ch_dir) / "chapter_data.json"
                        if _jp.exists():
                            with open(_jp, "r", encoding="utf-8") as _f:
                                _t = (json.load(_f).get("title") or "")
                        _library_record_download(url, t["slug"], t["chapter"], _t)
                    except Exception:
                        pass
                else:
                    t["status"] = "error"
                    t["error"] = "فشل التحميل - لم يتم العثور على صور"
                    t["log"] += "\n✗ لم يتم العثور على صور\n"
        except ScrapeCancelled:
            print(f"[i] Scrape task {task_id[:8]} cancelled by user", flush=True)
            with scrape_tasks_lock:
                t = scrape_tasks.get(task_id)
                if t is None:
                    return
                t["status"] = "cancelled"
                t["log"] += "\n✗ أُلغي من المستخدم\n"
        except Exception as e:
            print(f"[!] Scrape task {task_id[:8]} error: {e}", flush=True)
            with scrape_tasks_lock:
                t = scrape_tasks.get(task_id)
                if t is None:
                    return
                t["status"] = "error"
                t["error"] = f"خطأ: {str(e)}"
                t["log"] += f"\n✗ خطأ: {str(e)}\n"
        finally:
            with scrape_tasks_lock:
                if task_id in scrape_tasks:
                    scrape_tasks[task_id]["done"] = True

    t = threading.Thread(target=_run_scrape, daemon=True)
    t.start()
    return {"status": "started", "task_id": task_id}


@app.post("/api/scrape/cancel/{task_id}")
def cancel_scrape_task(task_id: str):
    with scrape_tasks_lock:
        t = scrape_tasks.get(task_id)
        if not t:
            raise HTTPException(404, "Task not found")
        if t.get("done") or t.get("status") in ("ok", "error", "cancelled"):
            return {"status": t.get("status"), "task_id": task_id,
                    "note": "already finished — nothing to cancel"}
        t["cancel_requested"] = True
    return {"status": "cancelling", "task_id": task_id}


@app.get("/api/scrape/task/{task_id}")
def scrape_task_status(task_id: str):
    with scrape_tasks_lock:
        task = scrape_tasks.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task


@app.get("/api/scrape/tasks")
def list_scrape_tasks():
    with scrape_tasks_lock:
        return {"tasks": [
            {"id": tid, "status": t["status"], "url": t["url"], "done": t["done"], "slug": t["slug"], "chapter": t["chapter"], "error": t["error"]}
            for tid, t in scrape_tasks.items()
        ]}


# ════════════════════════════════════════════════════════════
#  PROVIDERS MANAGEMENT
# ════════════════════════════════════════════════════════════

class ProviderUpdate(BaseModel):
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    default_model: Optional[str] = None
    default_model_name: Optional[str] = None
    enabled: Optional[bool] = None
    models: Optional[list] = None


@app.get("/api/providers")
def list_providers():
    return get_providers_public()


@app.post("/api/providers/{provider_id}/save")
def save_provider(provider_id: str, update: ProviderUpdate):
    updates = {k: v for k, v in update.model_dump().items() if v is not None}
    save_provider_config(provider_id, updates)
    return {"status": "ok"}


@app.post("/api/providers/{provider_id}/test")
def provider_test(provider_id: str):
    ok, msg = test_connection(provider_id)
    return {"status": "ok" if ok else "error", "message": msg}


@app.post("/api/providers/{provider_id}/fetch-models")
def provider_fetch_models(provider_id: str):
    models = fetch_models(provider_id)
    if models is None:
        return {"status": "error", "message": "فشل في جلب النماذج — تحقق من API key"}
    return {"status": "ok", "models": models}


@app.post("/api/providers/{provider_id}/delete")
def provider_delete(provider_id: str):
    delete_provider(provider_id)
    return {"status": "ok"}


@app.post("/api/chapter/{slug}/{chapter}/translate-with-provider")
def translate_with_provider(
    slug: str, chapter: str,
    provider_id: str = Query(...),
    model: str = Query(...),
    clear: bool = Query(False, description="Clear existing translations before retranslate"),
):
    ch_dir = _chapter_dir(slug, chapter)
    json_path = ch_dir / "chapter_data.json"

    if not json_path.exists():
        raise HTTPException(404, f"Chapter not found")

    # Phase 1: quick transaction — clear translations only (with .bak first,
    # so a later translation failure can restore what was wiped).
    if clear:
        _backup_json(json_path)
        with _file_transaction(json_path) as data:
            print("[i] Clearing existing translations...")
            for page in data.get("pages", []):
                for t in page.get("texts", []):
                    t["arabic_text"] = ""

    # Phase 2: read data, collect texts needing translation by ID
    with open(json_path, "r", encoding="utf-8") as f:
        data_snap = json.load(f)

    all_texts = []
    text_ids = []
    for page in data_snap.get("pages", []):
        for t in page.get("texts", []):
            if not t.get("arabic_text") and (t.get("original_text") or "").strip():
                all_texts.append(t)
                text_ids.append(t.get("id", ""))

    if not all_texts:
        return {"status": "ok", "translated": 0, "provider": provider_id, "model": model}

    # Phase 3: translate outside any lock (can take minutes)
    print(f"[->] Translating {len(all_texts)} texts via {provider_id}/{model}...")
    result = translate_texts(all_texts, provider_id, model)

    if result is None:
        if clear:
            # Restore what Phase 1 wiped — never leave the chapter emptier
            # than we found it.
            try:
                bak = json_path.with_suffix(".json.bak")
                if bak.exists():
                    with open(bak, "r", encoding="utf-8") as f:
                        bak_data = json.load(f)
                    bak_map = {t.get("id", ""): t.get("arabic_text", "")
                               for p in bak_data.get("pages", [])
                               for t in p.get("texts", [])}
                    with _file_transaction(json_path) as data:
                        for page in data.get("pages", []):
                            for t in page.get("texts", []):
                                old_ar = bak_map.get(t.get("id", ""), "")
                                if old_ar and not t.get("arabic_text"):
                                    t["arabic_text"] = old_ar
            except Exception:
                pass
        raise HTTPException(500, f"فشلت الترجمة عبر {provider_id}. تحقق من API key والموديل.")

    # Build id -> arabic_text map from results
    translated_map = {}
    for i, t in enumerate(result):
        tid = text_ids[i] if i < len(text_ids) else ""
        arabic = t.get("arabic_text", "").strip()
        if tid and arabic:
            translated_map[tid] = arabic

    # Phase 4: apply results by ID in a short transaction
    with _file_transaction(json_path) as data:
        for page in data.get("pages", []):
            for t in page.get("texts", []):
                tid = t.get("id", "")
                if tid in translated_map and not t.get("arabic_text"):
                    t["arabic_text"] = translated_map[tid]
        # Library hook (piggyback, zero extra writes): fully translated?
        try:
            if _chapter_fully_translated(data):
                _library_record_translated(slug, chapter)
        except Exception:
            pass

    total_ok = len(translated_map)
    return {"status": "ok", "translated": total_ok, "provider": provider_id, "model": model}


def _backup_json(json_path: Path):
    """Best-effort .bak snapshot before any destructive chapter write."""
    try:
        if json_path.exists():
            shutil.copy2(str(json_path), str(json_path) + ".bak")
    except Exception:
        pass


@app.delete("/api/chapter/{slug}/{chapter}")
def delete_chapter(slug: str, chapter: str):
    """Move a chapter to output/.trash (recoverable) instead of deleting."""
    ch_dir = _chapter_dir(slug, chapter)
    if not ch_dir.exists():
        raise HTTPException(404, f"Chapter not found: {ch_dir}")
    import time as _time
    try:
        trash = OUTPUT_DIR / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        dest = trash / f"{slug}__{chapter}__{int(_time.time())}"
        shutil.move(str(ch_dir), str(dest))
        _library_forget_chapter(slug, chapter)
        return {"status": "deleted", "slug": slug, "chapter": chapter,
                "trash": dest.name}
    except Exception as e:
        raise HTTPException(500, f"Delete failed: {e}")


@app.delete("/api/series/{slug}")
def delete_series(slug: str):
    """Move a whole series (all its chapters) to output/.trash."""
    _safe_slug(slug)
    base = OUTPUT_DIR.resolve()
    slug_dir = (base / slug).resolve()
    try:
        slug_dir.relative_to(base)
    except ValueError:
        raise HTTPException(400, "Invalid series path")
    if not slug_dir.exists() or not slug_dir.is_dir():
        raise HTTPException(404, f"Series not found: {slug}")
    import time as _time
    try:
        trash = OUTPUT_DIR / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        dest = trash / f"SERIES__{slug}__{int(_time.time())}"
        shutil.move(str(slug_dir), str(dest))
        _library_forget_chapter(slug, "")
        return {"status": "deleted", "slug": slug, "trash": dest.name}
    except Exception as e:
        raise HTTPException(500, f"Delete failed: {e}")


@app.post("/api/chapter/{slug}/{chapter}/refetch-texts")
def refetch_texts(slug: str, chapter: str):
    """Re-fetch ONLY overlay texts (geometry+source text) for slow networks.

    Images are cached on disk so nothing big re-downloads. Existing
    translations, styles and review flags are restored by bubble id.
    """
    from scraper.coordinator import scrape_chapter
    ch_dir = _chapter_dir(slug, chapter)
    json_path = ch_dir / "chapter_data.json"
    if not json_path.exists():
        raise HTTPException(404, f"Chapter not found: {ch_dir}")
    with open(json_path, "r", encoding="utf-8") as f:
        old = json.load(f)
    url = (old.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "No source URL stored for this chapter")
    # Snapshot BEFORE the minutes-long rescrape wipes the file; a .bak is
    # kept too (edits made mid-scrape are recoverable, never silently lost).
    _backup_json(json_path)
    saved: dict = {}
    for page in old.get("pages", []):
        for t in page.get("texts", []) or []:
            if t.get("id"):
                saved[t["id"]] = {
                    "arabic_text": t.get("arabic_text", "") or "",
                    "style": t.get("style", {}) or {},
                    "approved": t.get("approved", ""),
                }
    new_dir = scrape_chapter(url)
    if not new_dir:
        raise HTTPException(502, "فشل إعادة الجلب (إنترنت بطيء؟ أعد المحاولة)")
    with _file_transaction(json_path) as data:
        restored = 0
        for page in data.get("pages", []):
            for t in page.get("texts", []) or []:
                s = saved.get(t.get("id", ""))
                if s:
                    if s["arabic_text"] and not t.get("arabic_text"):
                        t["arabic_text"] = s["arabic_text"]
                        restored += 1
                    if s["style"]:
                        t["style"] = {**(t.get("style", {}) or {}), **s["style"]}
                    if s["approved"]:
                        t["approved"] = s["approved"]
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    found = sum(len(p.get("texts", []) or []) for p in data.get("pages", []))
    return {"status": "ok", "slug": slug, "chapter": chapter,
            "found": found, "restored": restored,
            "kept_translations": len(saved)}


@app.post("/api/chapter/{slug}/{chapter}/repair-geometry")
def repair_geometry(slug: str, chapter: str):
    """Re-apply FRESH site coordinates to existing boxes (by bubble id).

    Fetches overlay tops only (no image download — page PNGs on disk are
    reused for dimensions). Stored translations, styles, fonts, review
    flags are NEVER touched; only x/y/width/height move to site truth.
    Boxes the site removed are kept (user work is never deleted); boxes
    the site added are appended untranslated. .bak first, counts returned.
    """
    from scraper.spider import fetch_chapter_page
    from scraper.coordinator import assign_texts_to_images
    from PIL import Image as _PILImage
    ch_dir = _chapter_dir(slug, chapter)
    json_path = ch_dir / "chapter_data.json"
    if not json_path.exists():
        raise HTTPException(404, f"Chapter not found: {ch_dir}")
    with open(json_path, "r", encoding="utf-8") as f:
        old = json.load(f)
    url = (old.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "No source URL stored for this chapter")
    pages_dir = ch_dir / "pages"
    png_files = sorted(pages_dir.glob("page_*.png")) if pages_dir.exists() else []
    if not png_files:
        raise HTTPException(400, "No page images on disk — re-scrape fully instead")
    try:
        png_dims = []
        for pf in png_files:
            with _PILImage.open(pf) as im:
                png_dims.append((im.width, im.height))
    except Exception as e:
        raise HTTPException(500, f"Cannot read page images: {e}")
    res = fetch_chapter_page(url)
    if not res:
        raise HTTPException(502, "فشل جلب الموقع (إنترنت بطيء؟ أعد المحاولة)")
    image_urls, page_texts, rendered_w, css_heights, title, geo_tops = res[:6]
    try:
        fresh_pages = assign_texts_to_images(
            page_texts, png_dims, 1.0, [], trust_page_id=True,
            geo_tops=geo_tops)
    except Exception as e:
        raise HTTPException(500, f"Geometry mapping failed: {e}")
    fresh_by_id = {t.get("id"): (pg.get("page"), t)
                   for pg in fresh_pages for t in pg.get("texts", [])
                   if t.get("id")}
    _backup_json(json_path)
    updated = added = 0
    with _file_transaction(json_path) as data:
        have_ids = set()
        for page in data.get("pages", []):
            for t in page.get("texts", []) or []:
                tid = t.get("id", "")
                if not tid:
                    continue
                have_ids.add(tid)
                f = fresh_by_id.get(tid)
                if not f:
                    continue
                _, ft = f
                try:
                    t["x"] = round(float(ft.get("x", t.get("x", 0))), 2)
                    t["y"] = round(float(ft.get("y", t.get("y", 0))), 2)
                    t["width"] = round(max(20.0, float(ft.get("width", 200))), 2)
                    t["height"] = round(max(10.0, float(ft.get("height", 60))), 2)
                    # Never-fitted texts also take the site font size;
                    # fitted ones keep the user's reviewed size.
                    st = t.get("style") or {}
                    if not st.get("_fit_final"):
                        t["font_size_px"] = float(ft.get("font_size_px",
                                                         t.get("font_size_px", 45)))
                    updated += 1
                except Exception:
                    continue
        # Append site-added boxes (untranslated, site geometry as-is).
        new_ids = [tid for tid in fresh_by_id if tid not in have_ids]
        if new_ids:
            by_page: dict = {}
            for tid in new_ids:
                pgnum, ft = fresh_by_id[tid]
                by_page.setdefault(pgnum, []).append(ft)
            id_pages = {p.get("page"): p for p in data.get("pages", [])}
            for pgnum in sorted(by_page):
                if pgnum in id_pages:
                    id_pages[pgnum].setdefault("texts", []).extend(by_page[pgnum])
                    added += len(by_page[pgnum])
    return {"status": "ok", "slug": slug, "chapter": chapter,
            "updated": updated, "added": added,
            "note": "translations/styles/fonts untouched — run smart-fit once after"}


@app.post("/api/chapter/{slug}/{chapter}/reset")
def reset_chapter(slug: str, chapter: str):
    """Reset all arabic_text fields to empty string."""
    ch_dir = _chapter_dir(slug, chapter)
    json_path = ch_dir / "chapter_data.json"
    if not json_path.exists():
        raise HTTPException(404, f"Chapter not found: {ch_dir}")
    _backup_json(json_path)
    with _file_transaction(json_path) as data:
        count = 0
        for page in data.get("pages", []):
            for t in page.get("texts", []):
                if t.get("arabic_text"):
                    t["arabic_text"] = ""
                    count += 1
    return {"status": "reset", "slug": slug, "chapter": chapter, "cleared": count}


class SmartFitPayload(BaseModel):
    grow_boxes: bool = True
    unify_stroke: bool = False
    stroke_width: float = 1.5
    stroke_color: str = "#ffffff"
    scope: str = "all"
    page: Optional[int] = None


@app.post("/api/chapter/{slug}/{chapter}/smart-fit")
def smart_fit_chapter(slug: str, chapter: str, payload: SmartFitPayload):
    """Server-side smart fit (same function the auto worker uses).

    Grows boxes downward so translated text fits at site size, then fits
    fonts (shrink last resort). Optional stroke unification. Returns counts.
    """
    ch_dir = _chapter_dir(slug, chapter)
    json_path = ch_dir / "chapter_data.json"
    if not json_path.exists():
        raise HTTPException(404, f"Chapter not found: {ch_dir}")
    _backup_json(json_path)
    ex = _load_export_settings()
    with _file_transaction(json_path) as data:
        if payload.unify_stroke:
            for page in data.get("pages", []):
                for t in page.get("texts", []) or []:
                    st = t.get("style") or {}
                    if not st.get("stroke_enabled"):
                        st["stroke_enabled"] = True
                        st["stroke_width"] = payload.stroke_width
                        st["stroke_color"] = payload.stroke_color
                    t["style"] = st
        stats = autofit_chapter_boxes(
            data, max_grow=2.0 if payload.grow_boxes else 1.0,
            line_gap=int(ex.get("line_gap", 2)),
            force_stroke=bool(ex.get("force_stroke", True)),
            export_stroke_width=float(ex.get("stroke_w", ex.get("export_stroke_w", 1.5))),
            only_pages={payload.page} if payload.scope == "page" and payload.page is not None else None)
    return {"status": "ok", "slug": slug, "chapter": chapter, **stats}


class TextImportPayload(BaseModel):
    content: Optional[str] = None
    text: Optional[str] = None
    overwrite: bool = False


def _flat_text(s: str) -> str:
    return (s or "").replace("\r\n", " ").replace("\n", " ").replace("\r", " ").strip()


@app.get("/api/chapter/{slug}/{chapter}/text-export")
def text_export(slug: str, chapter: str,
                only_untranslated: bool = Query(default=False)):
    ch_dir = _chapter_dir(slug, chapter)
    json_path = ch_dir / "chapter_data.json"
    if not json_path.exists():
        raise HTTPException(404, f"Chapter not found: {ch_dir}")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    items = []
    for page in data.get("pages", []):
        for t in page.get("texts", []) or []:
            if not t.get("id"):
                continue
            if not (t.get("original_text") or "").strip():
                continue
            if only_untranslated and (t.get("arabic_text") or "").strip():
                continue
            items.append(t)
    lines = [f"MANGA-TEXT v1 slug={slug} chapter={chapter} count={len(items)}"]
    for i, t in enumerate(items, 1):
        tid = t.get("id", "")
        page = t.get("page", "")
        en = _flat_text(t.get("original_text", ""))
        ar = _flat_text(t.get("arabic_text", ""))
        lines.append(f"--- [{i:03d}] page={page} id={tid} ---")
        lines.append(f"EN: {en}")
        lines.append(f"AR: {ar}")
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; charset=utf-8")


@app.post("/api/chapter/{slug}/{chapter}/text-import")
def text_import(slug: str, chapter: str, payload: TextImportPayload):
    ch_dir = _chapter_dir(slug, chapter)
    json_path = ch_dir / "chapter_data.json"
    if not json_path.exists():
        raise HTTPException(404, f"Chapter not found: {ch_dir}")
    content = payload.content or payload.text or ""
    if len(content) > 500_000:
        raise HTTPException(413, "Import content too large (max 500000 chars)")
    lines = content.splitlines()
    id_re = re.compile(r"id=(\S+)")
    pairs: list[tuple[str, str]] = []
    for idx, line in enumerate(lines):
        m = id_re.search(line)
        if not m:
            continue
        tid = m.group(1).rstrip("-").strip()
        ar_text = ""
        found_ar = False
        for j in range(idx + 1, len(lines)):
            nxt = lines[j].lstrip()
            if id_re.search(lines[j]) and j > idx:
                # next block started before AR line -> treat as empty
                # (only break if this line looks like a block header)
                if lines[j].strip().startswith("---"):
                    break
            if nxt.startswith("AR:"):
                ar_text = lines[j].split("AR:", 1)[1].strip()
                found_ar = True
                break
        pairs.append((tid, ar_text if found_ar else ""))
    total = len(pairs)
    # backup before write
    try:
        bak_path = Path(str(json_path) + ".bak")
        shutil.copy2(str(json_path), str(bak_path))
    except Exception:
        pass
    imported = 0
    skipped_empty = 0
    unknown_id = 0
    skipped_existing = 0
    with _file_transaction(json_path) as data:
        id_map = {}
        for page in data.get("pages", []):
            for t in page.get("texts", []) or []:
                if t.get("id"):
                    id_map[t["id"]] = t
        for tid, ar in pairs:
            if not ar:
                skipped_empty += 1
                continue
            obj = id_map.get(tid)
            if obj is None:
                unknown_id += 1
                continue
            if (obj.get("arabic_text") or "").strip() and not payload.overwrite:
                skipped_existing += 1
                continue
            obj["arabic_text"] = ar
            imported += 1
    return {"status": "ok", "imported": imported, "updated": imported,
            "skipped_empty": skipped_empty, "skipped": skipped_empty,
            "unknown_id": unknown_id, "not_found": unknown_id,
            "skipped_existing": skipped_existing, "total": total}


# ── Batch scrape queue (in-memory + persist output/.queue.json) ──
class BatchScrapePayload(BaseModel):
    start_url: Optional[str] = None
    urls: Optional[list[str]] = None
    count: Optional[int] = None
    headless: bool = True
    browser: str = "brave"
    auto_translate: Optional[Union[bool, dict]] = None
    provider_id: Optional[str] = None
    model: Optional[str] = None
    auto_export: Optional[dict] = None
    delay_sec: float = 0


def _normalize_auto_translate(auto_translate: Optional[Union[bool, dict]],
                              provider_id: Optional[str],
                              model: Optional[str]) -> Optional[dict]:
    """Accept auto_translate as bool|dict|None + root provider_id/model fallback."""
    if auto_translate is True:
        # True means use root provider_id/model
        if provider_id and model:
            return {"provider_id": provider_id, "model": model}
        return None
    if auto_translate is False:
        return None
    if isinstance(auto_translate, dict):
        # fill missing keys from root fields
        pid = auto_translate.get("provider_id") or provider_id
        mdl = auto_translate.get("model") or model
        if (not auto_translate.get("provider_id") or not auto_translate.get("model")) and pid and mdl:
            merged = dict(auto_translate)
            merged["provider_id"] = pid
            merged["model"] = mdl
            return merged
        return auto_translate
    # None: accept root provider_id/model as alternative (auto-enable)
    if auto_translate is None and provider_id and model:
        return {"provider_id": provider_id, "model": model}
    return auto_translate


batch_jobs: dict[str, dict] = {}
batches: dict[str, dict] = {}
batch_lock = threading.Lock()
_batch_queue: queue.Queue = queue.Queue()


def _queue_file() -> Path:
    """Queue path follows the CURRENT output dir (not import-time one)."""
    return OUTPUT_DIR / ".queue.json"


def _save_queue():
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with batch_lock:
            # Bound growth: drop oldest TERMINAL jobs beyond 300 (active
            # jobs are never touched; get_batch already tolerates missing).
            if len(batch_jobs) > 400:
                terminal = ("review", "exported", "failed", "cancelled")
                old = sorted(
                    (jid for jid, j in batch_jobs.items()
                     if j.get("status") in terminal),
                    key=lambda jid: batch_jobs[jid].get("created_at", 0))
                for jid in old[:len(batch_jobs) - 300]:
                    batch_jobs.pop(jid, None)
            # Deep-copy INSIDE the lock: the worker mutates job dicts, and
            # dumping a live reference outside the lock tears the snapshot.
            payload = {"batches": copy.deepcopy(batches),
                       "jobs": copy.deepcopy(batch_jobs)}
        _atomic_json_dump(_queue_file(), payload)
    except Exception:
        pass


def _load_queue():
    try:
        if _queue_file().exists():
            with open(_queue_file(), "r", encoding="utf-8") as f:
                saved = json.load(f)
            with batch_lock:
                for bid, b in (saved.get("batches") or {}).items():
                    batches[bid] = b
                for jid, j in (saved.get("jobs") or {}).items():
                    if j.get("status") in ("scraping", "translating"):
                        j["status"] = "failed"
                        j["error"] = "interrupted by restart"
                    batch_jobs[jid] = j
                    if j.get("status") == "queued":
                        # Reloaded jobs start PAUSED — never auto-run behind
                        # the user's back after a restart. User resumes.
                        j["status"] = "paused"
                        j["paused_from"] = "queued"
                        j["log"] = (j.get("log") or "") + "⏸ متوقف بعد إعادة التشغيل — اضغط استئناف\n"
    except Exception:
        pass


# ─── LIBRARY (series registry + update checker) ──────────────────────
# Records every downloaded series so translated work is never lost track
# of. Update checks are SEQUENTIAL (concurrency=1, paced) — never a storm.
library: dict[str, dict] = {}
library_lock = threading.Lock()
check_lock = threading.Lock()
check_queue: "queue.Queue" = queue.Queue()
check_state: dict = {"run_id": None, "status": "idle", "total": 0,
                     "done": 0, "results": [], "cancel_requested": False,
                     "started_at": 0}
CHECK_DELAY_SEC = 3.0
CHECK_MAX_SERIES = 50


def _library_file() -> Path:
    return OUTPUT_DIR / ".library.json"


def _chapter_num_key(ch: str) -> tuple:
    try:
        return (0, float(ch))
    except Exception:
        return (1, 0.0)


def _series_url_from_chapter_url(url: str) -> str:
    m = re.match(r"^(https?://[^/]+/manga/[^/]+)/chapter-[0-9]+(?:\.[0-9]+)?/?",
                 (url or "").strip())
    return m.group(1) + "/" if m else ""


def _origin_from_url(url: str) -> str:
    m = re.match(r"^(https?://[^/]+)", (url or "").strip())
    return m.group(1) if m else ""


def _save_library():
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        # One-generation backup: the log is irreplaceable (deleted series
        # live ONLY here). A corrupt/lost file must never wipe history.
        try:
            _lf = _library_file()
            if _lf.exists() and _lf.stat().st_size > 2:
                shutil.copy2(str(_lf), str(_lf) + ".bak")
        except Exception:
            pass
        with library_lock:
            payload = copy.deepcopy(library)
        _atomic_json_dump(_library_file(), payload)
    except Exception:
        pass


def _library_upsert(slug: str, **fields):
    if not slug:
        return
    with library_lock:
        rec = library.get(slug) or {"slug": slug, "title": slug,
                                    "series_url": "", "origin": "",
                                    "chapters_downloaded": [],
                                    "last_translated": None,
                                    "latest_known": None,
                                    "last_checked": None}
        for k, v in fields.items():
            rec[k] = v
        library[slug] = rec
    _save_library()


def _library_record_download(url: str, slug: str, chapter: str, title: str = ""):
    if not slug or not chapter:
        return
    series_url = _series_url_from_chapter_url(url)
    origin = _origin_from_url(url)
    with library_lock:
        rec = library.get(slug) or {"slug": slug, "title": title or slug,
                                    "series_url": series_url, "origin": origin,
                                    "chapters_downloaded": [],
                                    "last_translated": None,
                                    "latest_known": None,
                                    "last_checked": None}
        if title:
            rec["title"] = title
        if series_url:
            rec["series_url"] = series_url
        if origin:
            rec["origin"] = origin
        dl = rec.get("chapters_downloaded") or []
        if chapter not in dl:
            dl.append(chapter)
            dl.sort(key=_chapter_num_key)
        rec["chapters_downloaded"] = dl
        library[slug] = rec
    _save_library()


def _library_record_translated(slug: str, chapter: str):
    """Mark chapter fully translated if it sorts above the current mark."""
    if not slug or not chapter:
        return
    try:
        with library_lock:
            rec = library.get(slug)
            if not rec:
                return
            cur = rec.get("last_translated")
            if cur is None or _chapter_num_key(chapter) > _chapter_num_key(cur):
                rec["last_translated"] = chapter
                library[slug] = rec
            else:
                return
        _save_library()
    except Exception:
        pass


def _library_forget_chapter(slug: str, chapter: str):
    """A deleted chapter leaves the log: drop it from downloaded, keep
    everything else (translations history, latest_known, checks). If the
    series has nothing left on disk it becomes archived — still checked,
    never forgotten, until explicitly removed from the library."""
    if not slug:
        return
    try:
        with library_lock:
            rec = library.get(slug)
            if not rec:
                return
            if chapter:
                dl = [c for c in (rec.get("chapters_downloaded") or [])
                      if str(c) != str(chapter)]
                rec["chapters_downloaded"] = dl
            else:
                rec["chapters_downloaded"] = []
            if not rec["chapters_downloaded"]:
                rec["archived"] = True
            library[slug] = rec
        _save_library()
    except Exception:
        pass


def _chapter_fully_translated(data: dict) -> bool:
    total = trans = 0
    for page in data.get("pages", []) or []:
        for t in page.get("texts", []) or []:
            if not (t.get("original_text") or "").strip():
                continue
            total += 1
            if (t.get("arabic_text") or "").strip():
                trans += 1
    return total > 0 and trans >= total


def _load_library():
    try:
        if _library_file().exists():
            with open(_library_file(), "r", encoding="utf-8") as f:
                saved = json.load(f)
            with library_lock:
                for slug, rec in (saved or {}).items():
                    if isinstance(rec, dict):
                        library[slug] = rec
            return
    except Exception:
        pass
    # Migrate: adopt every series already on disk (translations kept).
    try:
        if not OUTPUT_DIR.exists():
            return
        for slug_dir in sorted(OUTPUT_DIR.iterdir()):
            if not slug_dir.is_dir() or slug_dir.name.startswith("."):
                continue
            slug = slug_dir.name
            rec = {"slug": slug, "title": slug, "series_url": "",
                   "origin": "", "chapters_downloaded": [],
                   "last_translated": None, "latest_known": None,
                   "last_checked": None, "archived": False}
            best_tr = None
            for ch_dir in sorted(slug_dir.iterdir(), key=_chapter_sort_key):
                jp = ch_dir / "chapter_data.json"
                if not (ch_dir.is_dir() and jp.exists()):
                    continue
                try:
                    with open(jp, "r", encoding="utf-8") as f:
                        d = json.load(f)
                except Exception:
                    continue
                ch = str(d.get("chapter") or ch_dir.name.replace("chapter_", ""))
                rec["chapters_downloaded"].append(ch)
                if d.get("title"):
                    rec["title"] = d["title"]
                u = (d.get("url") or "").strip()
                if u and not rec["series_url"]:
                    rec["series_url"] = _series_url_from_chapter_url(u)
                    rec["origin"] = _origin_from_url(u)
                if _chapter_fully_translated(d):
                    if best_tr is None or _chapter_num_key(ch) > _chapter_num_key(best_tr):
                        best_tr = ch
            rec["chapters_downloaded"].sort(key=_chapter_num_key)
            rec["last_translated"] = best_tr
            if rec["chapters_downloaded"]:
                with library_lock:
                    library[slug] = rec
        _save_library()
    except Exception:
        pass


def _probe_chapter(url: str, timeout: int = 10) -> str:
    """Tri-state probe: 'alive' | 'miss' (clean 404) | 'error' (net/WAF)."""
    try:
        import requests
        from scraper.series import _HEADERS
    except Exception:
        return "error"
    try:
        r = requests.head(url, headers=_HEADERS, timeout=timeout,
                          allow_redirects=True)
        if r.status_code == 200:
            return "alive"
        if r.status_code in (400, 403, 405, 501):
            try:
                g = requests.get(url, headers=_HEADERS, timeout=timeout)
                if g.status_code == 200 and "reading-content" in g.text:
                    return "alive"
                if g.status_code == 404:
                    return "miss"
                return "error"
            except Exception:
                return "error"
        if r.status_code == 404:
            return "miss"
        return "error"
    except Exception:
        return "error"


def _batch_busy() -> bool:
    try:
        with batch_lock:
            for j in batch_jobs.values():
                if j.get("status") in ("queued", "scraping", "translating"):
                    return True
    except Exception:
        pass
    try:
        with scrape_tasks_lock:
            for t in scrape_tasks.values():
                if not t.get("done"):
                    return True
    except Exception:
        pass
    return False


def _check_worker():
    import random
    import time as _time
    while True:
        run = check_queue.get()
        try:
            _run_library_check(run)
        except Exception as e:
            try:
                with check_lock:
                    check_state["status"] = "failed"
                    check_state["results"] = (
                        check_state.get("results") or []) + [
                        {"slug": "", "error": str(e)}]
            except Exception:
                pass
        finally:
            try:
                check_queue.task_done()
            except Exception:
                pass


def _run_library_check(run: dict):
    import time as _time
    import random as _random
    run_id = run.get("run_id")
    slugs = run.get("slugs") or []
    with check_lock:
        check_state.update({"run_id": run_id, "status": "running",
                            "total": len(slugs), "done": 0, "results": [],
                            "cancel_requested": False,
                            "started_at": _time.time()})
    err_streak = 0
    for slug in slugs:
        with check_lock:
            if check_state.get("cancel_requested"):
                check_state["status"] = "cancelled"
                break
        # Never hammer the site while production work runs: wait it out
        # (cancel still honored), abort the run after ~10min busy.
        _waited = 0
        while _batch_busy():
            with check_lock:
                if check_state.get("cancel_requested"):
                    break
            _time.sleep(5)
            _waited += 5
            if _waited >= 600:
                break
        with check_lock:
            if check_state.get("cancel_requested"):
                check_state["status"] = "cancelled"
                break
        if _waited >= 600:
            with check_lock:
                check_state["results"].append(
                    {"slug": slug, "deferred": True,
                     "note": "batch busy — check later"})
                check_state["done"] += 1
            continue
        res = _check_series(slug)
        if res.get("probe") == "error":
            err_streak += 1
        else:
            err_streak = 0
        with check_lock:
            check_state["results"].append(res)
            check_state["done"] += 1
        if err_streak >= 3:
            with check_lock:
                check_state["status"] = "stalled"
                check_state["results"].append(
                    {"slug": "", "note": "network/WAF trouble — stopped early, resume later"})
            break
        _time.sleep(CHECK_DELAY_SEC + _random.uniform(0, 1))
    with check_lock:
        if check_state.get("status") == "running":
            check_state["status"] = "done"


def _check_series(slug: str) -> dict:
    import datetime as _dt
    with library_lock:
        rec = copy.deepcopy(library.get(slug) or {})
    origin = rec.get("origin") or ""
    if not origin:
        surl = rec.get("series_url") or ""
        origin = _origin_from_url(surl)
    if not origin:
        return {"slug": slug, "error": "no series URL known"}
    try:
        base = max([_chapter_num_key(c)[1] for c in
                    (rec.get("chapters_downloaded") or [])] + [0.0])
        if rec.get("latest_known"):
            try:
                base = max(base, float(rec["latest_known"]))
            except Exception:
                pass
    except Exception:
        base = 0.0
    found: list[str] = []
    outcome = "miss"
    # Walk integers AND halves (12, 12.5, 13, 13.5 ...): x.5 chapters are
    # real releases. One miss tolerated (gap), second consecutive miss ends.
    # Hard cap of 12 probes so a runaway never storms the site.
    seq: list[str] = []
    _start = int(base) + 1
    if _start < 1:
        _start = 1
    _k = _start
    while len(seq) < 12:
        seq.append(str(_k))
        seq.append(f"{_k}.5")
        _k += 1
    misses = 0
    for ch in seq:
        url = f"{origin}/manga/{slug}/chapter-{ch}/"
        st = _probe_chapter(url)
        if st == "alive":
            found.append(ch)
            outcome = "alive"
            misses = 0
        elif st == "error":
            outcome = "error"
            break
        else:
            misses += 1
            if misses >= 2:
                break
        _sleep_probe()
    now = _dt.datetime.now().isoformat(timespec="seconds")
    latest = found[-1] if found else rec.get("latest_known")
    with library_lock:
        if slug in library:
            if latest:
                try:
                    cur = library[slug].get("latest_known")
                    if cur is None or _chapter_num_key(str(latest)) > _chapter_num_key(str(cur)):
                        library[slug]["latest_known"] = str(latest)
                except Exception:
                    library[slug]["latest_known"] = str(latest)
            library[slug]["last_checked"] = now
    _save_library()
    return {"slug": slug, "latest_known": latest,
            "new_chapters": found, "probe": outcome}


def _sleep_probe():
    import time as _time
    try:
        _time.sleep(0.5)
    except Exception:
        pass


_load_library()
_thread_check = threading.Thread(target=_check_worker, daemon=True)
_thread_check.start()


_CHAPTER_URL_RE = re.compile(r"/manga/([^/]+)/chapter-([0-9]+(?:\.[0-9]+)?)")


def _expand_batch_urls(start_url: Optional[str], urls: Optional[list[str]],
                       count: Optional[int]) -> list[str]:
    if urls:
        cleaned = [u.strip() for u in urls if u and u.strip()]
        if not cleaned:
            raise HTTPException(400, "urls is empty")
        # count + urls: if count > len(urls) and start_url present, generate the rest;
        # if no start_url, ignore count.
        if count and count > len(cleaned) and start_url and start_url.strip():
            need = count - len(cleaned)
            # Try to continue the sequence from the last URL
            base_url = cleaned[-1]
            m = _CHAPTER_URL_RE.search(base_url)
            if m is None:
                m = _CHAPTER_URL_RE.search(start_url.strip())
                base_url = start_url.strip()
            if m is not None:
                prefix_start, prefix_end = m.span(2)
                # rebuild prefix/suffix relative to base_url
                prefix = base_url[:prefix_start]
                suffix = base_url[prefix_end:]
                chapter_str = m.group(2)
                try:
                    start_num = float(chapter_str)
                except ValueError:
                    return cleaned
                is_int = "." not in chapter_str
                for i in range(1, need + 1):
                    n = (int(start_num) + i) if is_int else (start_num + i)
                    num_s = str(int(n)) if is_int else ("%g" % n)
                    nxt = f"{prefix}{num_s}{suffix}"
                    if nxt not in cleaned:
                        cleaned.append(nxt)
                    if len(cleaned) >= count:
                        break
        return cleaned
    if not start_url or not start_url.strip():
        raise HTTPException(400, "Provide start_url or urls")
    start_url = start_url.strip()
    if not count or count <= 1:
        return [start_url]
    m = _CHAPTER_URL_RE.search(start_url)
    if not m:
        raise HTTPException(400, "Cannot expand count: start_url has no /manga/{slug}/chapter-{N} pattern")
    prefix_start, prefix_end = m.span(2)
    prefix = start_url[:prefix_start]
    suffix = start_url[prefix_end:]
    chapter_str = m.group(2)
    try:
        start_num = float(chapter_str)
    except ValueError:
        raise HTTPException(400, "Invalid chapter number in start_url")
    is_int = "." not in chapter_str
    out = []
    base_int = int(start_num) if is_int else start_num
    for i in range(count):
        n = base_int + i if is_int else start_num + i
        if is_int:
            num_s = str(int(n))
        else:
            num_s = ("%g" % n)
        out.append(f"{prefix}{num_s}{suffix}")
    return out


def _batch_worker():
    while True:
        job_id = _batch_queue.get()
        try:
            with batch_lock:
                job = batch_jobs.get(job_id)
            if not job:
                continue
            with batch_lock:
                if job.get("status") in ("cancelled", "paused"):
                    continue
                job["status"] = "scraping"
                job["progress"] = 5
                job["log"] = (job.get("log") or "") + "بدء السكراب...\n"
            _save_queue()
            # --- phase 1: scrape ---
            try:
                from scraper.coordinator import scrape_chapter
                ch_dir = scrape_chapter(job["url"], headless=job.get("headless", True),
                                        browser=job.get("browser", "brave"))
            except Exception as e:
                with batch_lock:
                    if batch_jobs.get(job_id, {}).get("cancel_requested"):
                        batch_jobs[job_id]["status"] = "cancelled"
                    else:
                        batch_jobs[job_id]["status"] = "failed"
                        batch_jobs[job_id]["error"] = str(e)
                        batch_jobs[job_id]["log"] = (batch_jobs[job_id].get("log") or "") + f"✗ scrape error: {e}\n"
                _save_queue()
                continue
            if not ch_dir:
                with batch_lock:
                    batch_jobs[job_id]["status"] = "failed"
                    batch_jobs[job_id]["error"] = "scrape returned no images"
                _save_queue()
                continue
            ch_dir = Path(ch_dir)
            slug = ch_dir.parent.name
            ch = ch_dir.name.replace("chapter_", "")
            with batch_lock:
                if batch_jobs.get(job_id, {}).get("cancel_requested"):
                    batch_jobs[job_id]["status"] = "cancelled"
                    _save_queue()
                    continue
                batch_jobs[job_id]["slug"] = slug
                batch_jobs[job_id]["chapter"] = ch
                batch_jobs[job_id]["progress"] = 40
                batch_jobs[job_id]["log"] = (batch_jobs[job_id].get("log") or "") + "✓ تم السكراب\n"
            _save_queue()
            # Library hook: record the download.
            try:
                _library_record_download(job.get("url", ""), slug, ch)
            except Exception:
                pass
            # --- phase 2: optional translate ---
            auto_tr = job.get("auto_translate")
            if auto_tr:
                with batch_lock:
                    if batch_jobs.get(job_id, {}).get("cancel_requested"):
                        batch_jobs[job_id]["status"] = "cancelled"
                        _save_queue()
                        continue
                    batch_jobs[job_id]["status"] = "translating"
                    batch_jobs[job_id]["progress"] = 55
                _save_queue()
                try:
                    provider_id = auto_tr.get("provider_id")
                    model = auto_tr.get("model")
                    if not provider_id or not model:
                        raise ValueError("auto_translate requires provider_id and model")
                    json_path = ch_dir / "chapter_data.json"
                    with open(json_path, "r", encoding="utf-8") as f:
                        snap = json.load(f)
                    need, ids = [], []
                    for page in snap.get("pages", []):
                        for t in page.get("texts", []) or []:
                            if not t.get("arabic_text") and (t.get("original_text") or "").strip():
                                need.append(t)
                                ids.append(t.get("id", ""))
                    if need:
                        def _job_cancelled(jid=job_id):
                            with batch_lock:
                                return bool(batch_jobs.get(jid, {}).get("cancel_requested"))
                        res = translate_texts(need, provider_id, model,
                                              should_cancel=_job_cancelled)
                        with batch_lock:
                            if batch_jobs.get(job_id, {}).get("cancel_requested"):
                                batch_jobs[job_id]["status"] = "cancelled"
                                _save_queue()
                                continue
                        if res is None:
                            raise RuntimeError(f"translate via {provider_id} failed")
                        tmap = {}
                        for k, t in enumerate(res):
                            tid = ids[k] if k < len(ids) else ""
                            ar = (t.get("arabic_text") or "").strip()
                            if tid and ar:
                                tmap[tid] = ar
                        with _file_transaction(json_path) as data:
                            for page in data.get("pages", []):
                                for t in page.get("texts", []) or []:
                                    if t.get("id") in tmap and not t.get("arabic_text"):
                                        t["arabic_text"] = tmap[t["id"]]
                    with batch_lock:
                        batch_jobs[job_id]["progress"] = 75
                        batch_jobs[job_id]["log"] = (batch_jobs[job_id].get("log") or "") + "✓ تمت الترجمة\n"
                    _save_queue()
                except Exception as e:
                    with batch_lock:
                        batch_jobs[job_id]["status"] = "failed"
                        batch_jobs[job_id]["error"] = f"translate error: {e}"
                        batch_jobs[job_id]["log"] = (batch_jobs[job_id].get("log") or "") + f"✗ translate error: {e}\n"
                    _save_queue()
                    continue
            # --- phase 2b: smart fit + stroke unify (same as manual) ---
            # hoisted: runs whenever smart_font is on, independent of need / scrape-only
            _at = job.get("auto_translate") or {}
            try:
                fit_path = ch_dir / "chapter_data.json"
                _backup_json(fit_path)
                with _file_transaction(fit_path) as data:
                    _normalize_chapter_data(data)
                ex = {**_load_export_settings(), **(job.get("auto_export") or {})}
                ex_line_gap = int(ex.get("line_gap", 2))
                ex_force = bool(ex.get("force_stroke", True))
                ex_sw = float(ex.get("stroke_w", ex.get("export_stroke_w", 1.5)))
                ex_sc = ex.get("stroke_color", "#ffffff")
                fit_stats = {"grown": 0, "shrunk": 0, "boosted": 0}
                fit_skipped = False
                with _file_transaction(fit_path) as data:
                    if _at.get("unify_stroke"):
                        for page in data.get("pages", []):
                            for t in page.get("texts", []) or []:
                                st = t.get("style") or {}
                                if not st.get("stroke_enabled"):
                                    st["stroke_enabled"] = True
                                    st["stroke_width"] = ex_sw
                                    st["stroke_color"] = ex_sc
                                t["style"] = st
                    if _at.get("smart_font", True):
                        fit_stats = autofit_chapter_boxes(
                            data, line_gap=ex_line_gap,
                            force_stroke=ex_force,
                            export_stroke_width=ex_sw)
                    else:
                        fit_skipped = True
                with batch_lock:
                    if fit_skipped:
                        batch_jobs[job_id]["log"] = (
                            batch_jobs[job_id].get("log") or "") + (
                            "fit skipped (smart_font off)\n")
                    else:
                        batch_jobs[job_id]["log"] = (
                            batch_jobs[job_id].get("log") or "") + (
                            f"✓ ضبط ذكي (صناديق {fit_stats.get('grown', 0)}، "
                            f"خط {fit_stats.get('shrunk', 0)}، "
                            f"تعزيز {fit_stats.get('boosted', 0)})\n")
            except Exception as e:
                with batch_lock:
                    batch_jobs[job_id]["log"] = (
                        batch_jobs[job_id].get("log") or "") + f"✗ smart-fit error: {e}\n"
            with batch_lock:
                if batch_jobs.get(job_id, {}).get("cancel_requested"):
                    batch_jobs[job_id]["status"] = "cancelled"
                    _save_queue()
                    continue
            # --- phase 3: optional export (full prefs = saved defaults + job) ---
            auto_ex = job.get("auto_export")
            if auto_ex:
                try:
                    ex = {**_load_export_settings(), **auto_ex}
                    ex_fmt = ex.get("format", ex.get("fmt", "webp"))
                    ex_quality = int(ex.get("quality", 90))
                    ex_merge = bool(ex.get("merge", True))
                    ex_max_h = int(ex.get("max_height", ex.get("maxHeight", 9000)))
                    ex_font_scale = float(ex.get("font_scale", 1.0))
                    ex_line_gap = int(ex.get("line_gap", 2))
                    ex_bg = _parse_color(ex.get("bg_color", "#ffffff"))
                    ex_force = bool(ex.get("force_stroke", True))
                    ex_sw = float(ex.get("stroke_w", ex.get("export_stroke_w", 1.5)))
                    ex_sc = _parse_color(ex.get("stroke_color",
                                                ex.get("export_stroke_color", "#ffffff")))
                    try:
                        export_dir = _resolve_export_dir(
                            ch_dir.parent.name,
                            ch_dir.name.replace("chapter_", ""))
                    except HTTPException:
                        export_dir = ch_dir / "exported"
                        export_dir.mkdir(parents=True, exist_ok=True)
                    export_chapter(ch_dir, export_dir, fmt=ex_fmt, quality=ex_quality,
                                   merge=ex_merge, max_height=ex_max_h,
                                   font_scale=ex_font_scale, line_gap=ex_line_gap,
                                   bg_color=ex_bg, force_stroke=ex_force,
                                   export_stroke_width=ex_sw,
                                   export_stroke_color=ex_sc)
                    with batch_lock:
                        batch_jobs[job_id]["status"] = "exported"
                        batch_jobs[job_id]["progress"] = 100
                        batch_jobs[job_id]["log"] = (batch_jobs[job_id].get("log") or "") + "✓ تم التصدير\n"
                    _save_queue()
                    # Library hook: fully translated at export time?
                    try:
                        _jp = ch_dir / "chapter_data.json"
                        if _jp.exists():
                            with open(_jp, "r", encoding="utf-8") as _f:
                                if _chapter_fully_translated(json.load(_f)):
                                    _library_record_translated(slug, ch)
                    except Exception:
                        pass
                except Exception as e:
                    with batch_lock:
                        batch_jobs[job_id]["status"] = "failed"
                        batch_jobs[job_id]["error"] = f"export error: {e}"
                    _save_queue()
                    continue
            else:
                with batch_lock:
                    # keep translating status only if translation ran; else review
                    batch_jobs[job_id]["status"] = "review"
                    batch_jobs[job_id]["progress"] = 100
                    batch_jobs[job_id]["log"] = (batch_jobs[job_id].get("log") or "") + "بانتظار المراجعة\n"
                _save_queue()
                # Library hook: fully translated at review time?
                try:
                    _jp = ch_dir / "chapter_data.json"
                    if _jp.exists():
                        with open(_jp, "r", encoding="utf-8") as _f:
                            if _chapter_fully_translated(json.load(_f)):
                                _library_record_translated(slug, ch)
                except Exception:
                    pass
            delay = max(0.0, float(job.get("delay_sec") or 0))
            if delay > 0:
                time.sleep(delay)
        except Exception as e:
            # Last-resort guard: ONE bad job must never kill the worker
            # thread (else the whole queue starves silently forever).
            try:
                with batch_lock:
                    if job_id in batch_jobs:
                        batch_jobs[job_id]["status"] = "failed"
                        batch_jobs[job_id]["error"] = f"worker error: {e}"
                _save_queue()
            except Exception:
                pass
        finally:
            try:
                _batch_queue.task_done()
            except Exception:
                pass


_load_queue()
_thread_batch = threading.Thread(target=_batch_worker, daemon=True)
_thread_batch.start()


@app.post("/api/batch-scrape")
def batch_scrape(payload: BatchScrapePayload):
    url_list = _expand_batch_urls(payload.start_url, payload.urls, payload.count)
    auto_tr_norm = _normalize_auto_translate(payload.auto_translate, payload.provider_id, payload.model)
    batch_id = uuid.uuid4().hex
    job_ids = []
    with batch_lock:
        batches[batch_id] = {"batch_id": batch_id, "total": len(url_list),
                             "job_ids": [], "created_at": time.time()}
    for u in url_list:
        jid = uuid.uuid4().hex
        job_ids.append(jid)
        with batch_lock:
            batch_jobs[jid] = {
                "id": jid, "job_id": jid, "batch_id": batch_id, "url": u,
                "status": "queued", "progress": 0, "log": "",
                "slug": None, "chapter": None, "error": None,
                "headless": payload.headless, "browser": payload.browser,
                "auto_translate": auto_tr_norm, "auto_export": payload.auto_export,
                "delay_sec": payload.delay_sec, "created_at": time.time(),
            }
            batches[batch_id]["job_ids"].append(jid)
        _batch_queue.put(jid)
    _save_queue()
    return {"batch_id": batch_id, "total": len(url_list), "job_ids": job_ids}


@app.get("/api/batch/{batch_id}")
def get_batch(batch_id: str):
    with batch_lock:
        b = batches.get(batch_id)
        if not b:
            raise HTTPException(404, "Batch not found")
        # Snapshot copies inside the lock: the worker mutates live dicts.
        jobs = [copy.deepcopy(batch_jobs[jid]) for jid in b.get("job_ids", []) if jid in batch_jobs]
        job_ids_snap = list(b.get("job_ids", []))
        total = b.get("total", len(jobs))
        counts: dict[str, int] = {}
        for j in jobs:
            counts[j.get("status", "queued")] = counts.get(j.get("status", "queued"), 0) + 1
        total = b.get("total", len(jobs))
        terminal = ("review", "exported", "failed", "cancelled")
        done_count = sum(1 for j in jobs if j.get("status") in terminal)
        done = (total > 0 and done_count == total)
        completed = sum(1 for j in jobs if j.get("status") in ("review", "exported"))
        progress = round(done_count / total * 100, 1) if total else 0
        results = [{"job_id": j.get("job_id", j.get("id")), "slug": j.get("slug"),
                    "chapter": j.get("chapter"), "status": j.get("status")} for j in jobs]
        return {"batch_id": batch_id, "total": total,
                "job_ids": job_ids_snap, "status_counts": counts, "jobs": jobs,
                "done": done, "completed": completed, "finished": done,
                "status": "completed" if done else "running",
                "progress": progress, "results": results}


@app.get("/api/queue")
def get_queue():
    with batch_lock:
        jobs = [copy.deepcopy(j) for j in batch_jobs.values()]
        batches_snap = [copy.deepcopy(b) for b in batches.values()]
        counts: dict[str, int] = {}
        for j in jobs:
            counts[j.get("status", "queued")] = counts.get(j.get("status", "queued"), 0) + 1
        return {"total": len(jobs), "status_counts": counts,
                "jobs": jobs, "batches": batches_snap}


@app.post("/api/queue/cancel/{job_id}")
def cancel_queue_job(job_id: str):
    with batch_lock:
        job = batch_jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        if job.get("status") in ("review", "exported", "failed", "cancelled"):
            return {"status": job.get("status"), "job_id": job_id,
                    "note": "already terminal — nothing to cancel"}
        job["cancel_requested"] = True
        if job.get("status") in ("queued", "paused"):
            job["status"] = "cancelled"
    _save_queue()
    return {"status": "cancelled", "job_id": job_id}


@app.post("/api/queue/resume/{job_id}")
def resume_queue_job(job_id: str):
    with batch_lock:
        job = batch_jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        if job.get("status") != "paused":
            return {"status": job.get("status"), "job_id": job_id,
                    "note": "not paused — nothing to resume"}
        job.pop("cancel_requested", None)
        job["status"] = "queued"
        job["progress"] = 0
        old_log = job.get("log") or ""
        job["log"] = (old_log[-2000:] + "\n▶ استئناف من المستخدم\n") if old_log else "▶ استئناف\n"
    _batch_queue.put(job_id)
    _save_queue()
    return {"status": "queued", "job_id": job_id}


@app.post("/api/batch/{batch_id}/resume")
def resume_batch(batch_id: str):
    with batch_lock:
        b = batches.get(batch_id)
        if not b:
            raise HTTPException(404, "Batch not found")
        resumed = []
        for jid in b.get("job_ids", []):
            j = batch_jobs.get(jid)
            if j and j.get("status") == "paused":
                j.pop("cancel_requested", None)
                j["status"] = "queued"
                j["progress"] = 0
                resumed.append(jid)
    for jid in resumed:
        _batch_queue.put(jid)
    _save_queue()
    return {"status": "ok", "resumed": len(resumed), "job_ids": resumed}


@app.get("/api/library")
def get_library():
    with library_lock:
        series = copy.deepcopy(list(library.values()))
    out = []
    for rec in series:
        try:
            dl = rec.get("chapters_downloaded") or []
            dl_max = max([_chapter_num_key(c)[1] for c in dl]) if dl else 0.0
            latest = rec.get("latest_known")
            has_new = False
            try:
                has_new = latest is not None and _chapter_num_key(str(latest))[1] > dl_max
            except Exception:
                has_new = False
            out.append({
                "slug": rec.get("slug"), "title": rec.get("title") or rec.get("slug"),
                "series_url": rec.get("series_url") or "",
                "downloaded": dl, "downloaded_count": len(dl),
                "last_translated": rec.get("last_translated"),
                "latest_known": latest, "last_checked": rec.get("last_checked"),
                "archived": bool(rec.get("archived")),
                "has_new": has_new,
            })
        except Exception:
            continue
    out.sort(key=lambda r: str(r.get("title") or "").lower())
    return {"series": out, "total": len(out)}


@app.post("/api/library/check")
def start_library_check(payload: Optional[dict] = None):
    slugs = None
    try:
        if isinstance(payload, dict) and payload.get("slugs"):
            slugs = [str(s) for s in payload["slugs"] if str(s).strip()]
    except Exception:
        slugs = None
    with library_lock:
        if not slugs:
            slugs = sorted(library.keys())
    slugs = slugs[:CHECK_MAX_SERIES]
    if not slugs:
        return {"status": "empty", "run_id": None}
    with check_lock:
        if check_state.get("status") == "running":
            return {"status": "busy", "run_id": check_state.get("run_id")}
        run_id = uuid.uuid4().hex
    check_queue.put({"run_id": run_id, "slugs": slugs})
    return {"status": "started", "run_id": run_id, "total": len(slugs)}


@app.get("/api/library/check-status")
def library_check_status():
    with check_lock:
        return copy.deepcopy(check_state)


@app.post("/api/library/check-cancel")
def library_check_cancel():
    with check_lock:
        if check_state.get("status") != "running":
            return {"status": check_state.get("status")}
        check_state["cancel_requested"] = True
    return {"status": "cancelling"}


@app.get("/api/library/{slug}/new-urls")
def library_new_urls(slug: str):
    with library_lock:
        rec = copy.deepcopy(library.get(slug) or {})
    if not rec:
        raise HTTPException(404, "Series not in library")
    origin = rec.get("origin") or _origin_from_url(rec.get("series_url") or "")
    if not origin:
        raise HTTPException(400, "No series URL known")
    try:
        dl_max = max([_chapter_num_key(c)[1] for c in (rec.get("chapters_downloaded") or [])] or [0.0])
        latest = rec.get("latest_known")
        top = float(latest) if latest is not None else dl_max
    except Exception:
        raise HTTPException(400, "No known chapters")
    urls = []
    k = int(dl_max) + 1
    if k < 1:
        k = 1
    while k <= int(top) + 1 and len(urls) < 40:
        for ch in (str(k), f"{k}.5"):
            try:
                if float(ch) <= top + 1e-9 and float(ch) > dl_max + 1e-9:
                    urls.append(f"{origin}/manga/{slug}/chapter-{ch}/")
            except Exception:
                pass
        k += 1
    return {"slug": slug, "urls": urls, "count": len(urls)}


@app.delete("/api/library/{slug}")
def library_remove_series(slug: str):
    """Explicitly drop a series from the log (stops all checks for it).

    The ONLY way a record disappears — deletes/translations never remove it.
    """
    with library_lock:
        if slug not in library:
            raise HTTPException(404, "Series not in library")
        library.pop(slug, None)
    _save_library()
    return {"status": "removed", "slug": slug}


@app.post("/api/batch/{batch_id}/retry-failed")
def retry_failed(batch_id: str):
    with batch_lock:
        b = batches.get(batch_id)
        if not b:
            raise HTTPException(404, "Batch not found")
        retried = []
        for jid in b.get("job_ids", []):
            j = batch_jobs.get(jid)
            if j and j.get("status") == "failed":
                j["status"] = "queued"
                j["error"] = None
                j["progress"] = 0
                j.pop("cancel_requested", None)
                # Trim the log: retry loops otherwise grow it (and the
                # persisted .queue.json) without bound.
                old_log = j.get("log") or ""
                j["log"] = (old_log[-2000:] + "\n↻ retry...\n") if old_log else "↻ retry...\n"
                retried.append(jid)
    for jid in retried:
        _batch_queue.put(jid)
    _save_queue()
    return {"status": "ok", "retried": len(retried), "job_ids": retried}


# Serve fonts for web (@font-face in browser)
fonts_static = FONTS_DIR
if fonts_static.exists():
    app.mount("/fonts", StaticFiles(directory=str(fonts_static)), name="fonts")

static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
