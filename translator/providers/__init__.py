import json
import base64
import os
import threading
import time
from pathlib import Path
from typing import Optional

# Internal prompt constants (moved from translator/prompts.py so providers work standalone)
STRICT_SYSTEM_PROMPT = """You translate manga dialogue from English to Arabic. You speak fluent Arabic and understand manga storytelling.

WORKFLOW (do all of this before producing output):
1. Silently read ALL English texts below as ONE continuous passage.
2. Identify the scene, characters, tone (comedy/battle/dramatic), dialects.
3. For each numbered line, translate into natural, fluent Arabic that fits the surrounding context - readers should feel they are reading an Arabic manga, not a word-by-word machine translation.
4. Use natural spoken Arabic where appropriate, not stiff literal translations.

OUTPUT FORMAT (strict):
- One Arabic line per line, in the SAME ORDER as the input numbers.
- No numbering, no prefix, no quotes, no JSON, no markdown.
- No English, no Chinese, no other language anywhere.
- Empty lines are forbidden.
- Each line corresponds ONE-TO-ONE with the input line.
- Only Arabic characters and punctuation are allowed in each output line.

If a line is "..." output "..." (Arabic period).
If a line is a name (e.g. "Cain"), transliterate to Arabic letters (e.g. كاين) and DO NOT leave it romanized.
If a line is pure onomatopoeia ("*sigh*"), translate as onomatopoeia in Arabic or keep the brackets and translate the inside word.

Begin output directly on the first line. No preamble. No explanation."""

SINGLE_SYSTEM_PROMPT = """Translate the following English sentence to Arabic. Output ONLY the Arabic translation — no English, no explanation, no quotes, no extra text."""

_STRICT_SYSTEM = None
_SINGLE_SYSTEM = None

def _get_prompts():
    global _STRICT_SYSTEM, _SINGLE_SYSTEM
    if _STRICT_SYSTEM is None:
        _STRICT_SYSTEM = STRICT_SYSTEM_PROMPT
        _SINGLE_SYSTEM = SINGLE_SYSTEM_PROMPT
    return _STRICT_SYSTEM, _SINGLE_SYSTEM

CONFIG_FILE = Path(__file__).parent / "providers_config.json"

# Environment variable names per provider. Env is the secure source of
# truth (never written to disk); JSON file is the fallback for UI-saved keys.
ENV_KEYS = {
    "google": "GOOGLE_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "custom": "CUSTOM_API_KEY",
}
ENV_URLS = {
    "custom": "CUSTOM_BASE_URL",
}


def _apply_env(cfg: dict) -> dict:
    """Overlay env keys over file config (read-only view, never persisted)."""
    for pid, var in ENV_KEYS.items():
        val = os.environ.get(var, "").strip()
        if val and pid in cfg:
            cfg[pid]["api_key"] = val
    for pid, var in ENV_URLS.items():
        val = os.environ.get(var, "").strip()
        if val and pid in cfg:
            cfg[pid]["base_url"] = val
    return cfg

DEFAULT_CONFIG = {
    "google": {
        "name": "Google AI Studio (Gemini)",
        "type": "google",
        "api_key": "",
        "enabled": False,
        "default_model": "gemini-2.0-flash",
        "models": [
            {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash"},
            {"id": "gemini-1.5-flash", "name": "Gemini 1.5 Flash"},
            {"id": "gemini-1.5-pro", "name": "Gemini 1.5 Pro"},
        ],
    },
    "nvidia": {
        "name": "NVIDIA NIM",
        "type": "openai",
        "api_key": "",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "enabled": False,
        "default_model": "",
        "default_model_name": "",
        "models": [],
    },
    "openrouter": {
        "name": "OpenRouter",
        "type": "openai",
        "api_key": "",
        "base_url": "https://openrouter.ai/api/v1",
        "enabled": False,
        "default_model": "",
        "default_model_name": "",
        "models": [],
    },
    "groq": {
        "name": "Groq (سريع ومجاني)",
        "type": "openai",
        "api_key": "",
        "base_url": "https://api.groq.com/openai/v1",
        "enabled": False,
        "default_model": "",
        "default_model_name": "",
        "models": [],
    },
    "custom": {
        "name": "API مخصص (OpenAI-compatible)",
        "type": "openai",
        "api_key": "",
        "base_url": "",
        "enabled": False,
        "default_model": "",
        "default_model_name": "",
        "models": [],
    },
}


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            for pid, defaults in DEFAULT_CONFIG.items():
                if pid not in cfg:
                    cfg[pid] = defaults
                else:
                    for k, v in defaults.items():
                        if k not in cfg[pid]:
                            cfg[pid][k] = v
            return _apply_env(cfg)
        except Exception:
            pass
    return _apply_env({k: dict(v) for k, v in DEFAULT_CONFIG.items()})


_config_lock = threading.Lock()


def _save_config(cfg: dict):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Never persist env-sourced secrets to disk (env stays the source).
    scrubbed = json.loads(json.dumps(cfg))
    for pid, var in ENV_KEYS.items():
        env_val = os.environ.get(var, "").strip()
        if env_val and pid in scrubbed and scrubbed[pid].get("api_key") == env_val:
            scrubbed[pid]["api_key"] = ""
    tmp = CONFIG_FILE.with_suffix(CONFIG_FILE.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(scrubbed, f, ensure_ascii=False, indent=2)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    os.replace(tmp, CONFIG_FILE)


def get_providers_public() -> dict:
    cfg = _load_config()
    public = {}
    for pid, p in cfg.items():
        entry = {k: v for k, v in p.items() if k != "api_key"}
        entry["has_key"] = bool(p.get("api_key"))
        public[pid] = entry
    return public


def get_provider(provider_id: str) -> Optional[dict]:
    cfg = _load_config()
    return cfg.get(provider_id)


def save_provider_config(provider_id: str, updates: dict):
    with _config_lock:
        cfg = _load_config()
        if provider_id not in cfg:
            cfg[provider_id] = dict(DEFAULT_CONFIG.get(provider_id, {}))
        for k, v in updates.items():
            cfg[provider_id][k] = v
        if provider_id in ("custom",) and updates.get("base_url"):
            cfg[provider_id]["name"] = f"API مخصص — {updates['base_url']}"
        _save_config(cfg)


def delete_provider(provider_id: str):
    with _config_lock:
        cfg = _load_config()
        if provider_id in cfg:
            cfg[provider_id]["api_key"] = ""
            cfg[provider_id]["enabled"] = False
            _save_config(cfg)


# ─── TRANSLATION ─────────────────────────────────────────

import urllib.request
import urllib.error
import json as json_mod


def _post_json(url: str, headers: dict, payload: dict, timeout: int = 120) -> tuple[bool, str, dict]:
    """POST JSON to API. Returns (ok, error_message, parsed_body)."""
    req = urllib.request.Request(
        url,
        data=json_mod.dumps(payload).encode("utf-8"),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json_mod.loads(resp.read().decode("utf-8"))
            return True, "", body
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        try:
            err_json = json_mod.loads(err_body)
        except Exception:
            err_json = {}
        return False, f"HTTP {e.code}: {err_body[:500]}", err_json
    except Exception as e:
        return False, str(e), {}


def _is_rate_limit(err_json: dict) -> bool:
    if not err_json:
        return False
    detail = err_json.get("detail", "") or ""
    if isinstance(detail, dict):
        detail = str(detail)
    code = err_json.get("code", 0) or 0
    status = err_json.get("status", "") or ""
    return code == 429 or status == "RESOURCE_EXHAUSTED" or "rate" in str(detail).lower() or "quota" in str(detail).lower()


def _retry_delay(err_json: dict) -> float:
    """Try to extract retry-after seconds from API error."""
    detail = err_json.get("detail", "")
    if isinstance(detail, str):
        import re
        m = re.search(r"retry in ([0-9.]+)s", detail)
        if m:
            try:
                return min(float(m.group(1)) + 1.0, 60.0)
            except Exception:
                pass
    return 4.0


def translate_via_google(texts: list[str], model: str, api_key: str, tags: list[str] | None = None) -> Optional[list[str]]:
    system, _ = _get_prompts()
    user = _build_user_prompt(texts, tags)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [{"text": user}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 8192},
    }
    ok, err, body = _post_json(
        url,
        {"Content-Type": "application/json"},
        payload,
        timeout=120,
    )
    if not ok:
        print(f"  [GOOGLE ERROR] {err}")
        return None
    candidates = body.get("candidates", [])
    if not candidates:
        return None
    raw = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
    return _parse_output(raw, len(texts), tags)


def translate_via_openai(texts: list[str], model: str, api_key: str, base_url: str, tags: list[str] | None = None) -> Optional[list[str]]:
    system, _ = _get_prompts()
    user = _build_user_prompt(texts, tags)
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
        "max_tokens": 8192,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    ok, err, body = _post_json(url, headers, payload, timeout=180)
    if not ok:
        print(f"  [{base_url} ERROR] {err}")
        return None
    choices = body.get("choices", [])
    if not choices:
        return None
    raw = choices[0].get("message", {}).get("content", "")
    return _parse_output(raw, len(texts), tags)


def _make_tags(n: int) -> list[str]:
    import random, string
    used = set()
    tags = []
    for _ in range(n):
        while True:
            t = ''.join(random.choices(string.ascii_lowercase, k=3))
            if t not in used:
                used.add(t)
                tags.append(t)
                break
    return tags


def _tag_texts(texts: list[str], tags: list[str]) -> list[str]:
    return [f"[{tags[i]}] {texts[i]}" for i in range(len(texts))]


def _apply_results(results: list[str], texts_list: list[dict]):
    """Assign translations to texts by index (1:1 mapping, already ordered by tags)."""
    for i, t in enumerate(texts_list):
        if i < len(results) and results[i]:
            t["arabic_text"] = results[i]

def translate_texts(texts: list[dict], provider_id: str, model: str,
                    should_cancel=None) -> Optional[list[dict]]:
    """Smart translation: try batch first, fall back to smaller chunks,
    only retry lines individually on final pass with rate-limit backoff.
    should_cancel (optional callable) aborts between chunks, keeping
    partial results."""

    def _cancelled():
        try:
            return bool(should_cancel and should_cancel())
        except Exception:
            return False
    cfg = _load_config()
    p = cfg.get(provider_id)
    if not p:
        print(f"  [PROVIDER] Unknown provider: {provider_id}")
        return None
    if provider_id != "ollama" and not p.get("api_key"):
        print(f"  [PROVIDER] No API key for {provider_id}")
        return None
    texts_list = list(texts)
    original_texts = [t.get("original_text", "") for t in texts_list]
    if not any(original_texts):
        for t in texts_list:
            t["arabic_text"] = ""
        return texts_list

    n = len(texts_list)
    # Each text gets a unique 3-letter tag to prevent order mixups
    tags = _make_tags(n)
    tagged_texts = _tag_texts(original_texts, tags)

    def _do_request(batch_texts: list[str], batch_tags: list[str]) -> tuple[Optional[list[str]], dict]:
        if p["type"] == "google":
            return translate_via_google(batch_texts, model, p["api_key"], tags=batch_tags), None
        if p["type"] == "openai":
            res = translate_via_openai(batch_texts, model, p.get("api_key", ""), p["base_url"], tags=batch_tags)
            return res, None
        return None, {}

    def _send_with_retry(batch_texts: list[str], batch_tags: list[str], max_attempts: int = 3):
        last_err = None
        for attempt in range(1, max_attempts + 1):
            res = _do_request(batch_texts, batch_tags)
            if isinstance(res, tuple):
                result, _ = res
            else:
                result = res
            if result is not None and len(result) >= len(batch_texts):
                return result
            last_err = f"batch parse returned {len(result) if result else 0} of {len(batch_texts)}"
            time.sleep(0.5)
        return None

    def _split_into_chunks(items: list, size: int):
        for i in range(0, len(items), size):
            yield items[i:i + size]

    # ─── Step 1: Try full batch ────────────────────────────
    print(f"   [STEP1] batch translate {n} texts...")
    full_result = _send_with_retry(tagged_texts, tags, max_attempts=1)
    if full_result is not None:
        _apply_results(full_result, texts_list)
        return texts_list

    # ─── Step 2: Sub-batch in chunks ───────────────────────
    print(f"   [STEP2] chunked translate, chunk_size=25...")
    chunk_size = 25
    for chunk_idx, (indices, idx_map_texts) in enumerate(_chunk_with_indices(texts_list, chunk_size)):
        if _cancelled():
            print("   [!] cancelled mid-translate — keeping partial results")
            break
        chunk_tags = [tags[i] for i in indices]
        chunk_tagged = [tagged_texts[i] for i in indices]
        result = _send_with_retry(chunk_tagged, chunk_tags, max_attempts=2)
        if result is not None and len(result) >= len(chunk_tags):
            _apply_results(result, [texts_list[i] for i in indices])

    # ─── Step 3: Fill any remaining empty texts individually ──
    empty_indices = [i for i, t in enumerate(texts_list) if not t.get("arabic_text")]
    if empty_indices:
        print(f"   [STEP3] {len(empty_indices)} empty, translate individually...")
        for idx in empty_indices:
            if _cancelled():
                print("   [!] cancelled mid-translate — keeping partial results")
                break
            t = texts_list[idx]
            single = _translate_single_text(t.get("original_text", ""), p, model)
            if single:
                texts_list[idx]["arabic_text"] = single

    for t in texts_list:
        if "arabic_text" not in t:
            t["arabic_text"] = ""
    return texts_list


def _chunk_with_indices(items: list, size: int):
    """Yield tuples of (indices, items) per chunk."""
    for i in range(0, len(items), size):
        chunk = items[i:i + size]
        indices = list(range(i, i + len(chunk)))
        yield indices, chunk


def _translate_single_text(text: str, provider: dict, model: str) -> str:
    if not text:
        return ""
    _, single_system = _get_prompts()
    try:
        import urllib.request
        import json as json_mod
        if provider["type"] == "google":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={provider['api_key']}"
            payload = {
                "system_instruction": {"parts": [{"text": single_system}]},
                "contents": [{"parts": [{"text": f"Translate: {text}"}]}],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 512},
            }
            req = urllib.request.Request(url, data=json_mod.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json_mod.loads(r.read().decode("utf-8"))
            raw = body.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            return raw.strip().strip('"').strip("'")
        else:
            url = f"{provider['base_url'].rstrip('/')}/chat/completions"
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": single_system},
                    {"role": "user", "content": f"Translate: {text}"},
                ],
                "temperature": 0.1,
                "max_tokens": 512,
            }
            req = urllib.request.Request(url, data=json_mod.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", "Authorization": f"Bearer {provider['api_key']}"})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json_mod.loads(r.read().decode("utf-8"))
            raw = body.get("choices", [{}])[0].get("message", {}).get("content", "")
            return raw.strip().strip('"').strip("'")
    except Exception as e:
        print(f"  [SINGLE PROVIDER] {e}")
        return ""


def fetch_models(provider_id: str) -> Optional[list[dict]]:
    cfg = _load_config()
    p = cfg.get(provider_id)
    if not p or not p.get("api_key"):
        return None

    if provider_id == "groq":
        return _fetch_groq_models(p["api_key"])
    elif provider_id == "openrouter":
        return _fetch_openrouter_models(p["api_key"])
    elif provider_id == "nvidia":
        return _fetch_nvidia_models(p["api_key"])
    elif provider_id == "google":
        return _fetch_google_models(p["api_key"])
    return None


def _fetch_groq_models(api_key: str):
    import urllib.request
    import json as json_mod
    try:
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json_mod.loads(resp.read().decode("utf-8"))
        models = []
        for m in body.get("data", []):
            mid = m.get("id", "")
            if "vision" not in mid and "whisper" not in mid:
                models.append({"id": mid, "name": mid})
        return models
    except Exception as e:
        print(f"  [GROQ] fetch models error: {e}")
        return None


def _fetch_openrouter_models(api_key: str):
    import urllib.request
    import json as json_mod
    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json_mod.loads(resp.read().decode("utf-8"))
        return [{"id": m.get("id", ""), "name": m.get("name", m.get("id", ""))} for m in body.get("data", [])]
    except Exception as e:
        print(f"  [OPENROUTER] fetch models error: {e}")
        return None


def _fetch_nvidia_models(api_key: str):
    import urllib.request
    import json as json_mod
    try:
        req = urllib.request.Request(
            "https://integrate.api.nvidia.com/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json_mod.loads(resp.read().decode("utf-8"))
        return [{"id": m.get("id", ""), "name": m.get("id", "")} for m in body.get("data", []) if "chat" in m.get("id","").lower() or "llm" in m.get("id","").lower()]
    except Exception as e:
        print(f"  [NVIDIA] fetch models error: {e}")
        return None


def _fetch_google_models(api_key: str):
    import urllib.request
    import json as json_mod
    try:
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json_mod.loads(resp.read().decode("utf-8"))
        models = []
        for m in body.get("models", []):
            mid = m.get("name", "").replace("models/", "")
            if "gemini" in mid:
                models.append({"id": mid, "name": m.get("displayName", mid)})
        return models
    except Exception as e:
        print(f"  [GOOGLE] fetch models error: {e}")
        return None


# ─── PROMPT BUILDING ─────────────────────────────────────

def _build_user_prompt(texts: list[str], tags: list[str] | None = None) -> str:
    lines = []
    for i, t in enumerate(texts):
        line = (t or "").strip()
        if not line:
            line = "."
        lines.append(f"[{i+1}] {line}")
    passage = "\n".join(lines)
    if tags:
        # texts are pre-tagged, use tags for mapping
        return (
            f"Translate each line to Arabic:\n{passage}\n\n"
            "Output each translation prefixed with its [tag] from above, one per line.\n"
            "Keep the [tag] exactly. NEVER reorder, merge, split, or skip lines."
        )
    return (
        f"Translate each line to Arabic:\n{passage}\n\n"
        "Output one Arabic line per input line, in the SAME order.\n"
        "Keep the [N] numbering exactly. NEVER reorder, merge, split, or skip lines."
    )


def _parse_output(raw: str, expected: int, tags: list[str] | None = None) -> list[str]:
    import re
    result = [""] * expected
    raw_clean = re.sub(r"^```\w*\s*", "", raw, flags=re.MULTILINE)
    raw_clean = re.sub(r"\s*```\s*$", "", raw_clean, flags=re.MULTILINE)
    lines = [l.strip() for l in raw_clean.split("\n") if l.strip()]

    # Pass 1: extract [tag] (3+ lowercase letters) — even if prefixed by [N]
    if tags:
        tag_set = set(tags)
        for line in lines:
            line = re.sub(r"^(Output|Arabic|Translation|Here is|Arabic translation)\s*[:：]\s*", "", line, flags=re.IGNORECASE).strip()
            # Strip optional [N] prefix before looking for [tag]
            stripped = re.sub(r"^\[\d+\]\s*", "", line).strip()
            m = re.match(r"^[\[\(\{]([a-z]{3,}\w*)[\]\)\}]\s*(.*)$", stripped)
            if m and m.group(1) in tag_set:
                idx = tags.index(m.group(1))
                txt = m.group(2).strip()
                if txt and not result[idx]:
                    result[idx] = txt
                    continue

    # Pass 2: try [N] / N. numbering
    for line in lines:
        if all(result):
            break
        line = re.sub(r"^(Output|Arabic|Translation|Here is|Arabic translation)\s*[:：]\s*", "", line, flags=re.IGNORECASE).strip()
        m = re.match(r"^[\[\(\{<](\d+)[\]\)\}>][\.\)]?\s*(.*)$", line)
        if m:
            idx = int(m.group(1)) - 1
            txt = m.group(2).strip()
            if 0 <= idx < expected and txt and not result[idx]:
                result[idx] = txt
                continue
        m = re.match(r"^(\d+)[\.\)\-—:]\s*(.*)$", line)
        if m:
            idx = int(m.group(1)) - 1
            txt = m.group(2).strip()
            if 0 <= idx < expected and txt and not result[idx]:
                result[idx] = txt
                continue

    # Pass 3: fallback — fill empty slots in order
    for line in lines:
        if all(result):
            break
        line = re.sub(r"^(Output|Arabic|Translation|Here is|Arabic translation)\s*[:：]\s*", "", line, flags=re.IGNORECASE).strip()
        if line and not line.startswith("[") and not line.startswith("("):
            for i in range(expected):
                if not result[i]:
                    result[i] = line
                    break

    return result


def test_connection(provider_id: str) -> tuple[bool, str]:
    import urllib.request
    import urllib.error
    cfg = _load_config()
    p = cfg.get(provider_id)
    if not p:
        return False, "Provider not found"
    if not p.get("api_key"):
        return False, "No API key configured"

    try:
        if provider_id == "google":
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={p['api_key']}"
            req = urllib.request.Request(url)
        elif provider_id == "groq":
            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {p['api_key']}"},
            )
        elif provider_id == "openrouter":
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {p['api_key']}"},
            )
        elif provider_id == "nvidia":
            req = urllib.request.Request(
                "https://integrate.api.nvidia.com/v1/models",
                headers={"Authorization": f"Bearer {p['api_key']}"},
            )
        elif provider_id == "custom":
            url = f"{p['base_url'].rstrip('/')}/models"
            req = urllib.request.Request(
                url,
                headers={"Authorization": f"Bearer {p['api_key']}"},
            )
        else:
            return False, "Unknown provider type"

        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status < 400:
                return True, "Connected"
            return False, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return False, f"Connection failed: {e.reason}"
    except Exception as e:
        return False, str(e)
