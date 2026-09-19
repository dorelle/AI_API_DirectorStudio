#!/usr/bin/env python3
"""
Directors Studio (nbs.py)
AI image generator powered by Google Gemini
Run: python nbs.py
"""

# ---------------------------------------------------------------------------
# Bootstrap Ã¢â‚¬â€ auto-install missing dependencies on first run
# ---------------------------------------------------------------------------
import sys
import subprocess
import importlib.util
import os as _os

APP_VERSION = "1.0"

def _bootstrap():
    deps = [
        ("flask",    "flask>=3.0.0"),
        ("PIL",      "Pillow"),
        ("requests", "requests>=2.31.0"),
        ("fal_client", "fal-client>=0.7.0"),
    ]
    missing = [(mod, pkg) for mod, pkg in deps if importlib.util.find_spec(mod) is None]
    if not missing:
        return
    pkgs = [pkg for _, pkg in missing]
    print("\n" + "="*52)
    print(f"  Directors Studio {APP_VERSION} - First-run Setup")
    print("="*52)
    print(f"  Missing packages: {', '.join(pkgs)}")
    print("  Installing automatically... (one-time only)\n")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet"] + pkgs
        )
        print("\n  Done! Starting Directors Studio...\n")
    except subprocess.CalledProcessError as e:
        print(f"\n  Install failed: {e}")
        print("  Try manually: pip install -r requirements.txt")
        sys.exit(1)

_bootstrap()

# ---------------------------------------------------------------------------
# End bootstrap Ã¢â‚¬â€ normal imports follow
# ---------------------------------------------------------------------------
import base64
import glob
import hashlib
import io
import json
import os
import random
import re
import secrets
import shutil
import sqlite3
import threading
import tempfile
import time
import unicodedata
import requests
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlparse, unquote
from uuid import uuid4
from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, send_from_directory)
from PIL import Image, ImageOps
import fal_client
import fal_catalog

app = Flask(__name__)
app.secret_key = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

# ---------------------------------------------------------------------------
# Credenziali login
# ---------------------------------------------------------------------------
USERS = {
    "admin": "banana2024"
}

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR         = os.path.dirname(__file__)
FAL_ENDPOINTS_PATH = os.path.join(BASE_DIR, fal_catalog.ENDPOINTS_FILENAME)
FAL_GENERATED_PATH = os.path.join(BASE_DIR, fal_catalog.GENERATED_FILENAME)
CONFIG_FILE      = os.path.join(BASE_DIR, "config.json")
IMAGE_ASSETS_DIR = os.path.join(BASE_DIR, "Image_assets")
LOVED_DIR        = os.path.join(IMAGE_ASSETS_DIR, "loved")
GENERATIONS_DIR  = os.path.join(IMAGE_ASSETS_DIR, "generations")
VIDEOS_DIR       = os.path.join(IMAGE_ASSETS_DIR, "videos")
EDIT_SESSIONS_DIR = os.path.join(IMAGE_ASSETS_DIR, "edit_sessions")
ELEMENTS_DIR     = os.path.join(BASE_DIR, "Elements")
STUDIO_DB_FILE   = os.path.join(BASE_DIR, "studio.db")
REFERENCE_ARCHIVE_DIR = os.path.join(IMAGE_ASSETS_DIR, "reference_archive")
REFERENCE_ARCHIVE_INDEX_FILE = os.path.join(REFERENCE_ARCHIVE_DIR, "_index.json")
REFERENCE_MASKS_DIR = os.path.join(IMAGE_ASSETS_DIR, "reference_masks")
REFERENCE_RENDERS_DIR = os.path.join(IMAGE_ASSETS_DIR, "reference_renders")
# Task 14b: every temp directory the app (or its test harness) creates uses this prefix AND
# carries this marker file. The cleaner removes nothing that lacks either.
APP_TEMP_PREFIX = "dstudio_tmp_"
APP_TEMP_MARKER = ".dstudio_temp"
LEGACY_HARNESS_TEMP_RE = re.compile(r"^aiapi_t\d\d[a-z]?_[A-Za-z0-9_]+$")   # the Task 02-14 harness's own naming
ASSET_UNCATEGORIZED_VALUE = "uncategorized"
ASSET_UNCATEGORIZED_FOLDER = "uncategorized"
ASSET_META_FIELDS = ("assetClient", "assetProject", "assetShot", "assetFilename")


def move_tree_contents(src_dir: str, dst_dir: str) -> None:
    if not os.path.isdir(src_dir):
        return
    os.makedirs(dst_dir, exist_ok=True)
    for item in os.listdir(src_dir):
        src = os.path.join(src_dir, item)
        dst = os.path.join(dst_dir, item)
        if os.path.exists(dst):
            if os.path.isfile(src) and os.path.basename(src) == ".gitkeep":
                os.remove(src)
                continue
            if os.path.isdir(src) and os.path.isdir(dst):
                move_tree_contents(src, dst)
                if os.path.isdir(src) and not os.listdir(src):
                    os.rmdir(src)
            continue
        shutil.move(src, dst)


def sanitize_asset_meta_text(value) -> str:
    text = str(value or "").strip()
    text = text.replace("\\", " ").replace("/", " ")
    text = re.sub(r"[\x00-\x1f]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_asset_scope_text(value) -> str:
    text = sanitize_asset_meta_text(value)
    if not text or text == "-" or text.lower() == "uncategorized":
        return ASSET_UNCATEGORIZED_VALUE
    return text


def sanitize_asset_path_segment(value: str, fallback: str = ASSET_UNCATEGORIZED_FOLDER) -> str:
    text = normalize_asset_scope_text(value)
    if not text or text == ASSET_UNCATEGORIZED_VALUE:
        return fallback
    text = re.sub(r'[<>:"/\\\\|?*]+', "_", text)
    text = text.rstrip(". ").strip()
    return text[:120] or fallback


def sanitize_asset_filename_stem(value: str, fallback: str = "untitled") -> str:
    text = os.path.splitext(sanitize_asset_meta_text(value))[0]
    text = re.sub(r'[<>:"/\\\\|?*]+', "_", text)
    text = re.sub(r"\s+", "_", text).strip("._- ")
    return text[:120] or fallback


def normalize_asset_metadata(raw: dict | None, *, require_filename: bool = False, fallback_filename: str = "") -> dict:
    raw = raw or {}
    relpath = str(raw.get("assetRelpath", "") or "").replace("\\", "/").strip("/")
    rel_parts = [part for part in relpath.split("/") if part]
    inferred_client = rel_parts[0] if len(rel_parts) >= 4 else ""
    inferred_project = rel_parts[1] if len(rel_parts) >= 4 else ""
    # Task 16: a film shot with a scene files as client/project/scene/shot/file (5 parts)
    inferred_scene = rel_parts[2] if len(rel_parts) >= 5 else ""
    inferred_shot = rel_parts[3] if len(rel_parts) >= 5 else (rel_parts[2] if len(rel_parts) >= 4 else "")
    inferred_filename = os.path.splitext(rel_parts[4])[0] if len(rel_parts) >= 5 else (os.path.splitext(rel_parts[3])[0] if len(rel_parts) >= 4 else "")
    client = normalize_asset_scope_text(raw.get("assetClient", raw.get("client", inferred_client))) or ASSET_UNCATEGORIZED_VALUE
    project = normalize_asset_scope_text(raw.get("assetProject", raw.get("project", inferred_project))) or ASSET_UNCATEGORIZED_VALUE
    shot = normalize_asset_scope_text(raw.get("assetShot", raw.get("shot", inferred_shot))) or ASSET_UNCATEGORIZED_VALUE
    scene = normalize_asset_scope_text(raw.get("assetScene", raw.get("scene_slug", inferred_scene)))
    filename_seed = raw.get("assetFilename", raw.get("filename_stem", raw.get("filenameStem", inferred_filename or fallback_filename)))
    filename = sanitize_asset_filename_stem(filename_seed, fallback=fallback_filename or "untitled") if (require_filename or str(filename_seed or "").strip()) else ""
    meta = {
        "assetClient": client,
        "assetProject": project,
        "assetShot": shot,
        "assetFilename": filename,
    }
    if scene and scene != ASSET_UNCATEGORIZED_VALUE:
        meta["assetScene"] = scene   # only present when a scene applies; everything else keeps its shape
    return meta


def merge_asset_metadata(params_meta: dict | None, payload: dict | None = None, *, fallback_source: dict | None = None) -> dict:
    merged = dict(params_meta or {})
    source = {}
    if isinstance(fallback_source, dict):
        source.update(fallback_source)
    if isinstance(payload, dict):
        source.update(payload)
    merged.update(normalize_asset_metadata(source, require_filename=False))
    return merged


SAFE_REQUEST_SETTING_KEYS = {
    "assetProjectId",
    "imageSize",
    "aspectRatio",
    "numberOfImages",
    "temperature",
    "topP",
    "thinkingLevel",
    "useSearch",
    "outputMode",
    "falSafetyChecker",
    "falSafetyTolerance",
    "geminiSafetyPreset",
    "byteplusSafetyMode",
    "seedMode",
    "seedValue",
    "videoInputMode",
    "duration",
    "resolution",
    "negativePrompt",
    "videoSafetyChecker",
    "videoOutputSafetyChecker",
    "videoWanMultiShots",
    "videoUpscaleMode",
    "videoUpscaleFactor",
    "videoUpscaleTargetResolution",
    "videoUpscaleNoiseScale",
    "videoUpscaleOutputFormat",
    "videoUpscaleOutputQuality",
    "videoUpscaleOutputWriteMode",
    "videoUpscaleSeed",
    "videoUpscaleSyncMode",
    "upscaleModel",
    "upscalePreset",
    "upscaleMode",
    "upscaleFactor",
    "upscaleTargetResolution",
    "upscaleTargetWidth",
    "upscaleTargetHeight",
    "upscaleTargetAnchor",
    "upscaleDisplaySize",
    "upscaleOutputWidth",
    "upscaleOutputHeight",
    "upscaleSourceDate",
    "upscaleSourceFilename",
}


def merge_request_settings(params_meta: dict | None, payload: dict | None = None) -> dict:
    merged = dict(params_meta or {})
    if not isinstance(payload, dict):
        return merged
    for key in SAFE_REQUEST_SETTING_KEYS:
        if key not in payload:
            continue
        value = payload.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            merged[key] = value
    return merged


def asset_scene_segment(asset_meta: dict) -> str:
    """Task 16: the scene slug between project and shot, or '' when the asset has no scene."""
    scene = normalize_asset_scope_text(asset_meta.get("assetScene", ""))
    return "" if (not scene or scene == ASSET_UNCATEGORIZED_VALUE) else scene


def build_asset_storage_relative_dir(asset_meta: dict) -> str:
    scene = asset_scene_segment(asset_meta)
    parts = [
        sanitize_asset_path_segment(asset_meta.get("assetClient")),
        sanitize_asset_path_segment(asset_meta.get("assetProject")),
    ]
    if scene:
        parts.append(sanitize_asset_path_segment(scene))
    parts.append(sanitize_asset_path_segment(asset_meta.get("assetShot")))
    return os.path.join(*parts)


def build_asset_storage_file_prefix(asset_meta: dict) -> str:
    prefix_parts = []
    for key in ("assetClient", "assetProject", "assetScene", "assetShot"):
        value = sanitize_asset_meta_text(asset_meta.get(key, ""))
        value = normalize_asset_scope_text(value)
        if value and value != ASSET_UNCATEGORIZED_VALUE:
            prefix_parts.append(sanitize_asset_filename_stem(value))
    prefix_parts.append(sanitize_asset_filename_stem(asset_meta.get("assetFilename", ""), fallback="untitled"))
    return "_".join([part for part in prefix_parts if part]) or "untitled"


def build_asset_storage_paths(root_dir: str, asset_meta: dict, extension: str, *, variant_suffix: str = "") -> tuple[str, str, str]:
    rel_dir = build_asset_storage_relative_dir(asset_meta)
    abs_dir = os.path.join(root_dir, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)
    ext = str(extension or "").lower().lstrip(".") or "bin"
    base_stem = build_asset_storage_file_prefix(asset_meta)
    if variant_suffix:
        base_stem = f"{base_stem}_{variant_suffix}"
    candidate = base_stem
    counter = 2
    while (
        os.path.exists(os.path.join(abs_dir, f"{candidate}.{ext}"))
        or os.path.exists(os.path.join(abs_dir, f"{candidate}.json"))
    ):
        candidate = f"{base_stem}_{counter}"
        counter += 1
    filename = f"{candidate}.{ext}"
    rel_path = os.path.join(rel_dir, filename)
    return abs_dir, rel_path.replace("\\", "/"), candidate


def asset_meta_value_matches(selected: str, actual: str) -> bool:
    selected_text = normalize_asset_scope_text(selected)
    actual_text = normalize_asset_scope_text(actual)
    if not selected_text:
        return True
    if selected_text == ASSET_UNCATEGORIZED_VALUE:
        return not actual_text or actual_text == ASSET_UNCATEGORIZED_VALUE
    return actual_text == selected_text


def ensure_asset_metadata_memory_shape(memory: dict | None) -> dict:
    memory = dict(memory or {})
    return {
        "clients": list(memory.get("clients")) if isinstance(memory.get("clients"), list) and memory.get("clients") else [ASSET_UNCATEGORIZED_VALUE],
        "projects": list(memory.get("projects")) if isinstance(memory.get("projects"), list) and memory.get("projects") else [ASSET_UNCATEGORIZED_VALUE],
        "shots": list(memory.get("shots")) if isinstance(memory.get("shots"), list) and memory.get("shots") else [ASSET_UNCATEGORIZED_VALUE],
        "filenames": list(memory.get("filenames")) if isinstance(memory.get("filenames"), list) else [],
    }


def update_asset_metadata_memory(config: dict, asset_meta: dict | None) -> dict:
    asset_meta = normalize_asset_metadata(asset_meta, require_filename=False)
    memory = ensure_asset_metadata_memory_shape(config.get("asset_metadata_memory"))

    def _upsert(bucket: list[str], value: str, *, allow_uncategorized: bool) -> list[str]:
        clean_value = sanitize_asset_meta_text(value)
        if not clean_value:
            clean_value = ASSET_UNCATEGORIZED_VALUE if allow_uncategorized else ""
        clean_value = normalize_asset_scope_text(clean_value) if allow_uncategorized else clean_value
        if not clean_value:
            return bucket
        deduped = [item for item in bucket if sanitize_asset_meta_text(item) != clean_value]
        deduped.insert(0, clean_value)
        return deduped[:200]

    memory["clients"] = _upsert(memory.get("clients", []), asset_meta.get("assetClient", ""), allow_uncategorized=True)
    memory["projects"] = _upsert(memory.get("projects", []), asset_meta.get("assetProject", ""), allow_uncategorized=True)
    memory["shots"] = _upsert(memory.get("shots", []), asset_meta.get("assetShot", ""), allow_uncategorized=True)
    if asset_meta.get("assetFilename"):
        memory["filenames"] = _upsert(memory.get("filenames", []), asset_meta.get("assetFilename", ""), allow_uncategorized=False)

    for key in ("clients", "projects", "shots"):
        unique_values = []
        for value in memory.get(key, []):
            clean_value = normalize_asset_scope_text(value) or ASSET_UNCATEGORIZED_VALUE
            if clean_value not in unique_values:
                unique_values.append(clean_value)
        if ASSET_UNCATEGORIZED_VALUE not in unique_values:
            unique_values.insert(0, ASSET_UNCATEGORIZED_VALUE)
        memory[key] = unique_values[:200]

    filename_values = []
    for value in memory.get("filenames", []):
        clean_value = sanitize_asset_filename_stem(value, fallback="")
        if clean_value and clean_value not in filename_values:
            filename_values.append(clean_value)
    memory["filenames"] = filename_values[:400]

    config["asset_metadata_memory"] = memory
    return memory


def resolve_asset_relpath(relpath: str = "", date_str: str = "", filename: str = "") -> str:
    raw = str(relpath or "").strip().replace("\\", "/")
    if raw:
        parts = [part for part in raw.split("/") if part not in ("", ".", "..")]
        return "/".join(parts)
    safe_date = str(date_str or "").strip().replace("\\", "/").strip("/")
    safe_filename = os.path.basename(str(filename or "").strip())
    if safe_date and safe_filename:
        return f"{safe_date}/{safe_filename}"
    raise ValueError("Missing asset file.")


def safe_asset_path(root_dir: str, relpath: str) -> str:
    safe_root = os.path.realpath(root_dir)
    local_path = os.path.realpath(os.path.join(root_dir, relpath.replace("/", os.sep)))
    if not local_path.startswith(safe_root + os.sep):
        raise ValueError("Invalid path.")
    if not os.path.exists(local_path):
        raise FileNotFoundError("File not found.")
    return local_path


def list_meta_files_recursive(root_dir: str) -> list[str]:
    meta_files: list[str] = []
    if not os.path.isdir(root_dir):
        return meta_files
    for current_root, _, filenames in os.walk(root_dir):
        for name in filenames:
            if name.lower().endswith(".json"):
                meta_files.append(os.path.join(current_root, name))
    meta_files.sort(key=lambda path: os.path.getmtime(path), reverse=True)
    return meta_files


def find_existing_binary_for_meta(meta_file: str, extensions: tuple[str, ...]) -> tuple[str | None, str | None]:
    base = meta_file[:-5]
    for ext in extensions:
        candidate = base + ext
        if os.path.exists(candidate):
            return candidate, os.path.basename(candidate)
    return None, None


def build_asset_public_url(prefix: str, relpath: str) -> str:
    clean_relpath = resolve_asset_relpath(relpath=relpath)
    return f"/{prefix}/{clean_relpath}"


def derive_asset_date_key(relpath: str, generated_at: str = "") -> str:
    rel_parts = [part for part in str(relpath or "").replace("\\", "/").split("/") if part]
    if rel_parts and re.fullmatch(r"\d{4}-\d{2}-\d{2}", rel_parts[0]):
        return rel_parts[0]
    generated = str(generated_at or "").strip()
    if generated:
        return generated[:10]
    return ""


def find_generation_relpath_by_filename(filename: str, *, preferred_dir: str = "", preferred_date: str = "") -> str:
    safe_name = os.path.basename(str(filename or "").strip())
    if not safe_name or not os.path.isdir(GENERATIONS_DIR):
        return ""
    preferred_dir = str(preferred_dir or "").replace("\\", "/").strip("/")
    preferred_date = str(preferred_date or "").strip()

    candidates: list[str] = []
    if preferred_dir:
        candidates.append(f"{preferred_dir}/{safe_name}")
    if preferred_date:
        candidates.append(f"{preferred_date}/{safe_name}")

    for relpath in candidates:
        try:
            local_path = safe_asset_path(GENERATIONS_DIR, relpath)
        except Exception:
            continue
        if os.path.exists(local_path):
            return relpath

    for root, _, files in os.walk(GENERATIONS_DIR):
        if safe_name not in files:
            continue
        full_path = os.path.join(root, safe_name)
        try:
            relpath = os.path.relpath(full_path, GENERATIONS_DIR).replace("\\", "/")
        except Exception:
            continue
        if preferred_dir and relpath.startswith(preferred_dir + "/"):
            return relpath
        if preferred_date and relpath.startswith(preferred_date + "/"):
            return relpath
        return relpath
    return ""
    if os.path.isdir(src_dir) and not os.listdir(src_dir):
        os.rmdir(src_dir)


def migrate_image_assets_layout() -> None:
    os.makedirs(IMAGE_ASSETS_DIR, exist_ok=True)
    legacy_dirs = {
        os.path.join(BASE_DIR, "loved"): LOVED_DIR,
        os.path.join(BASE_DIR, "generations"): GENERATIONS_DIR,
        os.path.join(BASE_DIR, "videos"): VIDEOS_DIR,
        os.path.join(BASE_DIR, "reference_archive"): REFERENCE_ARCHIVE_DIR,
        os.path.join(BASE_DIR, "reference_masks"): REFERENCE_MASKS_DIR,
        os.path.join(BASE_DIR, "reference_renders"): REFERENCE_RENDERS_DIR,
    }
    for legacy_dir, target_dir in legacy_dirs.items():
        if os.path.realpath(legacy_dir) == os.path.realpath(target_dir):
            continue
        if os.path.isdir(legacy_dir):
            move_tree_contents(legacy_dir, target_dir)
        else:
            os.makedirs(target_dir, exist_ok=True)

# Mapping folder to display name and icon for Elements
ELEMENTS_CATEGORIES = {
    "Model Managment": {"label": "Characters", "icon": "&#128100;", "slug": "characters"},
    "Locations":       {"label": "Locations",  "icon": "&#127757;", "slug": "locations"},
    "Props":           {"label": "Props",      "icon": "&#128230;", "slug": "props"},
}

DEFAULT_CONFIG = {
    "api_key": "",
    "seedream_api_key": "",
    "fal_api_key": "",
    "byteplus_api_key": "",
    "kling_api_token": "",
    "runway_api_key": "",
    "luma_api_key": "",
    "openai_api_key": "",
    "topaz_api_key": "",
    "bfl_api_key": "",       # dormant (Task 16): stored, not wired to any provider
    "clarity_api_key": "",   # dormant (Task 16): stored, not wired to any provider
    "openai_model": "gpt-4o-mini",
    "agent_instructions": "",
    "flask_secret_key": "",
    "asset_metadata_memory": {
        "clients": [ASSET_UNCATEGORIZED_VALUE],
        "projects": [ASSET_UNCATEGORIZED_VALUE],
        "shots": [ASSET_UNCATEGORIZED_VALUE],
        "filenames": [],
    },
    "stats": {
        "total_requests":   0,
        "total_images":     0,
        "total_cost_usd":   0.0,
        "requests_log":     [],
        # Vision / Analisi (Gemini text-only calls)
        "vision_calls":     0,
        "vision_cost_usd":  0.0,
        "vision_log":       [],
    }
}


ASYNC_JOBS_LOCK = threading.Lock()
ASYNC_JOBS: dict[str, dict] = {}


def clone_jsonable(value):
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except Exception:
        return value


def build_async_job_public_record(job: dict) -> dict:
    return {
        "ok": True,
        "jobId": job.get("job_id", ""),
        "kind": job.get("kind", ""),
        "status": job.get("status", "queued"),
        "createdAt": job.get("created_at", ""),
        "updatedAt": job.get("updated_at", ""),
        "completedAt": job.get("completed_at", ""),
        "result": clone_jsonable(job.get("result")),
        "error": job.get("error", ""),
        "debug": clone_jsonable(job.get("debug")),
    }


def create_async_job(kind: str) -> dict:
    now_ts = utc_now_iso()
    job = {
        "job_id": str(uuid4()),
        "kind": kind,
        "status": "queued",
        "created_at": now_ts,
        "updated_at": now_ts,
        "completed_at": "",
        "result": None,
        "error": "",
        "debug": None,
    }
    with ASYNC_JOBS_LOCK:
        ASYNC_JOBS[job["job_id"]] = job
    return build_async_job_public_record(job)


def get_async_job(job_id: str) -> dict | None:
    with ASYNC_JOBS_LOCK:
        job = ASYNC_JOBS.get(job_id)
        if not job:
            return None
        return build_async_job_public_record(job)


def update_async_job(job_id: str, **updates) -> None:
    with ASYNC_JOBS_LOCK:
        job = ASYNC_JOBS.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updated_at"] = utc_now_iso()


def compact_generation_result(result: dict) -> dict:
    params = clone_jsonable(result.get("params") or {}) or {}
    compact_images = []
    for img in result.get("images", []) or []:
        gen_date = str(img.get("gen_date") or params.get("gen_date") or "").strip()
        gen_filename = str(img.get("gen_filename") or "").strip()
        gen_relpath = str(img.get("gen_relpath") or params.get("gen_relpath") or "").strip()
        img_url = build_asset_public_url("generations", gen_relpath) if gen_relpath else (f"/generations/{gen_date}/{gen_filename}" if gen_date and gen_filename else "")
        compact_images.append({
            "gen_date": gen_date,
            "gen_filename": gen_filename,
            "gen_relpath": gen_relpath,
            "mime_type": str(img.get("mime_type") or "image/png"),
            "url": img_url,
        })
    first_image = compact_images[0] if compact_images else {}
    if first_image:
        params.setdefault("assetUrl", first_image.get("url", ""))
        params.setdefault("gen_filename", first_image.get("gen_filename", ""))
        params.setdefault("gen_relpath", first_image.get("gen_relpath", ""))
    return {
        "params": params,
        "images": compact_images,
        "url": first_image.get("url", ""),
        "filename": first_image.get("gen_filename", ""),
        "gen_filename": first_image.get("gen_filename", ""),
        "gen_relpath": first_image.get("gen_relpath", ""),
        "mime_type": first_image.get("mime_type", "image/png"),
        "text": str(result.get("text") or ""),
        "cost": round(float(result.get("cost") or 0.0), 6),
        "model_label": str(result.get("model_label") or ""),
    }


def compact_video_result(result: dict) -> dict:
    params = clone_jsonable(result.get("params") or {}) or {}
    compact_videos = []
    for item in result.get("videos", []) or []:
        gen_date = str(item.get("gen_date") or params.get("gen_date") or "").strip()
        gen_filename = str(item.get("gen_filename") or "").strip()
        gen_relpath = str(item.get("gen_relpath") or params.get("gen_relpath") or "").strip()
        video_url = build_asset_public_url("videos", gen_relpath) if gen_relpath else (f"/videos/{gen_date}/{gen_filename}" if gen_date and gen_filename else "")
        compact_videos.append({
            "gen_date": gen_date,
            "gen_filename": gen_filename,
            "gen_relpath": gen_relpath,
            "mime_type": str(item.get("mime_type") or "video/mp4"),
            "url": video_url,
            "poster_url": str(item.get("poster_url") or ""),
        })
    return {
        "params": params,
        "videos": compact_videos,
        "text": str(result.get("text") or ""),
        "cost": round(float(result.get("cost") or 0.0), 6),
        "model_label": str(result.get("model_label") or ""),
    }


def _run_generate_async_job(job_id: str, payload: dict) -> None:
    update_async_job(job_id, status="running", error="", debug=None)
    try:
        result = run_generation_job(clone_jsonable(payload) or {}, load_config())
        persist_generation_result(result)
        update_async_job(
            job_id,
            status="completed",
            completed_at=utc_now_iso(),
            result=compact_generation_result(result),
            error="",
            debug=None,
        )
    except GenerationDebugError as exc:
        update_async_job(
            job_id,
            status="failed",
            completed_at=utc_now_iso(),
            result=None,
            error=str(exc),
            debug=clone_jsonable(exc.debug),
        )
    except Exception as exc:
        update_async_job(
            job_id,
            status="failed",
            completed_at=utc_now_iso(),
            result=None,
            error=str(exc),
            debug=None,
        )


def _run_upscale_async_job(job_id: str, payload: dict) -> None:
    update_async_job(job_id, status="running", error="", debug=None)
    try:
        config = load_config()
        fal_key = (config.get("fal_api_key", "") or "").strip()
        if not fal_key:
            raise ValueError("Fal API key not configured. Go to Settings.")
        request_payload = clone_jsonable(payload) or {}
        is_video_upscale = bool(
            str(request_payload.get("assetType") or "").strip().lower() == "video"
            or str((request_payload.get("modelFamily") or "")).strip().lower() == "seedvr-video"
            or isinstance(request_payload.get("sourceVideo"), dict)
        )
        if is_video_upscale:
            result = run_fal_seedvr_video_job(request_payload, fal_key)
            persist_video_result(result)
            compact_result = compact_video_result(result)
        else:
            result = run_fal_seedvr_upscale_job(request_payload, fal_key)
            persist_generation_result(result)
            compact_result = compact_generation_result(result)
        update_async_job(
            job_id,
            status="completed",
            completed_at=utc_now_iso(),
            result=compact_result,
            error="",
            debug=None,
        )
    except Exception as exc:
        update_async_job(
            job_id,
            status="failed",
            completed_at=utc_now_iso(),
            result=None,
            error=str(exc),
            debug=None,
        )


def _run_video_async_job(job_id: str, payload: dict) -> None:
    update_async_job(job_id, status="running", error="", debug=None)
    try:
        result = run_video_job(clone_jsonable(payload) or {}, load_config())
        persist_video_result(result)
        update_async_job(
            job_id,
            status="completed",
            completed_at=utc_now_iso(),
            result=compact_video_result(result),
            error="",
            debug=None,
        )
    except Exception as exc:
        update_async_job(
            job_id,
            status="failed",
            completed_at=utc_now_iso(),
            result=None,
            error=str(exc),
            debug=None,
        )


def _run_edit_async_job(job_id: str, payload: dict) -> None:
    update_async_job(job_id, status="running", error="", debug=None)
    try:
        result = run_edit_job(clone_jsonable(payload) or {}, load_config())
        persist_generation_result(result)
        update_async_job(
            job_id,
            status="completed",
            completed_at=utc_now_iso(),
            result=compact_generation_result(result),
            error="",
            debug=None,
        )
    except GenerationDebugError as exc:
        update_async_job(
            job_id,
            status="failed",
            completed_at=utc_now_iso(),
            result=None,
            error=str(exc),
            debug=clone_jsonable(exc.debug),
        )
    except Exception as exc:
        update_async_job(
            job_id,
            status="failed",
            completed_at=utc_now_iso(),
            result=None,
            error=str(exc),
            debug=None,
        )


def start_async_generate_job(payload: dict) -> dict:
    job = create_async_job("generate")
    threading.Thread(
        target=_run_generate_async_job,
        args=(job["jobId"], clone_jsonable(payload) or {}),
        daemon=True,
    ).start()
    return job


def start_async_edit_job(payload: dict) -> dict:
    job = create_async_job("generate")
    threading.Thread(
        target=_run_edit_async_job,
        args=(job["jobId"], clone_jsonable(payload) or {}),
        daemon=True,
    ).start()
    return job


def start_async_upscale_job(payload: dict) -> dict:
    job = create_async_job("upscale")
    threading.Thread(
        target=_run_upscale_async_job,
        args=(job["jobId"], clone_jsonable(payload) or {}),
        daemon=True,
    ).start()
    return job


def start_async_video_job(payload: dict) -> dict:
    job = create_async_job("video")
    threading.Thread(
        target=_run_video_async_job,
        args=(job["jobId"], clone_jsonable(payload) or {}),
        daemon=True,
    ).start()
    return job

# ---------------------------------------------------------------------------
# Price per image in USD (verified March 2026)
# Gemini source: ai.google.dev/gemini-api/docs/pricing
# fal sources: fal.ai model pages
# BytePlus source: docs.byteplus.com ModelArk pricing
# ---------------------------------------------------------------------------
PRICING = {
    "gemini-2.5-flash-image": {
        "0.5K": 0.0,   "1K": 0.039, "2K": 0.039, "4K": 0.039
    },
    "gemini-3-pro-image-preview": {
        "0.5K": 0.0,   "1K": 0.134, "2K": 0.134, "4K": 0.240
    },
    "gemini-3.1-flash-image-preview": {
        "0.5K": 0.045, "1K": 0.067, "2K": 0.101, "4K": 0.151
    },
    "fal-ai/gemini-25-flash-image": {
        "0.5K": 0.0,   "1K": 0.039, "2K": 0.039, "4K": 0.039
    },
    "fal-ai/nano-banana-pro": {
        "0.5K": 0.0,   "1K": 0.150, "2K": 0.150, "4K": 0.300
    },
    "fal-ai/nano-banana-2": {
        "0.5K": 0.060, "1K": 0.080, "2K": 0.120, "4K": 0.160
    },
    "fal-ai/gpt-image-2": {},
    "fal-ai/bytedance/seedream/v4.5/text-to-image": {
        "0.5K": 0.0, "1K": 0.0, "2K": 0.040, "4K": 0.040
    },
    "seedream-4-5-251128": {
        "0.5K": 0.0, "1K": 0.0, "2K": 0.040, "4K": 0.040
    },
    "fal-ai/bytedance/seedream/v5/lite/text-to-image": {
        "0.5K": 0.0, "1K": 0.0, "2K": 0.035, "4K": 0.035
    },
}

# ---------------------------------------------------------------------------
# Models - includes max reference images supported
# ---------------------------------------------------------------------------
ASPECT_RATIOS_BASE      = ["1:1","16:9","9:16","4:3","3:4","3:2","2:3","21:9","5:4","4:5"]
ASPECT_RATIOS_NB2_EXTRA = ["4:1","1:4","8:1","1:8"]
ASPECT_RATIOS_FAL       = ["1:1","16:9","9:16","4:3","3:4","3:2","2:3","21:9","5:4","4:5"]
ASPECT_RATIOS_GPT_IMAGE_2 = ["1:1","16:9","9:16","4:3","3:4"]
ASPECT_RATIOS_BYTEPLUS  = ["1:1","16:9","9:16","4:3","3:4"]

PROVIDER_LABELS = {
    "gemini": "Gemini",
    "fal": "Fal",
    "byteplus": "BytePlus",
    "kling": "Kling",
    "runway": "Runway",
    "luma": "Luma",
    "openai": "OpenAI",
}

# Which Settings key each provider needs. The catalog is never filtered by this; the
# selectors only order and label models with it.
PROVIDER_KEY_FIELDS = {
    "gemini":   ("api_key",          "Gemini API key"),
    "fal":      ("fal_api_key",      "Fal key"),
    "byteplus": ("byteplus_api_key", "BytePlus key"),
    "kling":    ("kling_api_token",  "Kling token"),
    "luma":     ("luma_api_key",     "Luma key"),
    "runway":   ("runway_api_key",   "Runway key (no Settings card yet)"),
    "openai":   ("openai_api_key",   "OpenAI API key"),
}


def provider_key_state(config: dict | None = None) -> dict:
    """provider -> {set: bool, needs: label}. Read-only view of config."""
    config = config if config is not None else load_config()
    state = {}
    for provider, (field, needs) in PROVIDER_KEY_FIELDS.items():
        value = str(config.get(field) or "").strip()
        if provider == "byteplus" and not value:
            value = str(config.get("seedream_api_key") or "").strip()
        state[provider] = {"set": bool(value), "needs": needs}
    return state

GEMINI_BASE_URL                 = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
FAL_BASE_URL                    = "https://fal.run"
BYTEPLUS_BASE_URL               = "https://ark.ap-southeast.bytepluses.com/api/v3/images/generations"
KLING_BASE_URL                  = "https://api-singapore.klingai.com"
RUNWAY_BASE_URL                 = "https://api.dev.runwayml.com"
RUNWAY_API_VERSION              = "2024-11-06"
LUMA_BASE_URL                   = "https://api.lumalabs.ai/dream-machine/v1"
LUMA_GENERATIONS_URL            = f"{LUMA_BASE_URL}/generations"
FAL_NANO_BANANA_TEXT_ID         = "fal-ai/gemini-25-flash-image"
FAL_NANO_BANANA_EDIT_ID         = "fal-ai/gemini-25-flash-image/edit"
FAL_NANO_BANANA_PRO_TEXT_ID     = "fal-ai/nano-banana-pro"
FAL_NANO_BANANA_PRO_EDIT_ID     = "fal-ai/nano-banana-pro/edit"
FAL_NANO_BANANA_2_TEXT_ID       = "fal-ai/nano-banana-2"
FAL_NANO_BANANA_2_EDIT_ID       = "fal-ai/nano-banana-2/edit"
FAL_GPT_IMAGE_2_TEXT_ID         = "fal-ai/gpt-image-2"
FAL_GPT_IMAGE_2_EDIT_ID         = "fal-ai/gpt-image-2/edit"
# GPT Image 2 straight from OpenAI (Images API) with the OpenAI key; the same family as the Fal route
OPENAI_GPT_IMAGE_2_TEXT_ID      = "openai/gpt-image-2"
OPENAI_GPT_IMAGE_2_EDIT_ID      = "openai/gpt-image-2/edit"
OPENAI_GPT_IMAGE_2_API_MODEL    = "gpt-image-2"
# GPT Image 2.5 previews (OpenAI only). One model each: text-to-image, or an edit when references are attached.
OPENAI_GPT_IMAGE_25_MODELS = {
    "openai/gpt-image-2.5-flare":    {"api_model": "gpt-image-2.5-flare",    "family": "gpt-image-25-flare",    "label": "GPT Image 2.5 Flare",    "badge": "GPT25F"},
    "openai/gpt-image-2.5-sunburst": {"api_model": "gpt-image-2.5-sunburst", "family": "gpt-image-25-sunburst", "label": "GPT Image 2.5 Sunburst", "badge": "GPT25S"},
}
FAL_SEEDREAM_45_TEXT_ID         = "fal-ai/bytedance/seedream/v4.5/text-to-image"
FAL_SEEDREAM_45_EDIT_ID         = "fal-ai/bytedance/seedream/v4.5/edit"
FAL_SEEDREAM_5_TEXT_ID          = "fal-ai/bytedance/seedream/v5/lite/text-to-image"
FAL_SEEDREAM_5_EDIT_ID          = "fal-ai/bytedance/seedream/v5/lite/edit"
FAL_SEEDVR_UPSCALE_ID           = "fal-ai/seedvr/upscale/image"
FAL_SEEDVR_VIDEO_ID             = "fal-ai/seedvr/upscale/video"
FAL_SAM3_IMAGE_ID               = "fal-ai/sam-3/image"
FAL_SAM3_VIDEO_ID               = "fal-ai/sam-3/video"
FAL_KLING_V1_STD_T2V_ID         = "fal-ai/kling-video/v1/standard/text-to-video"
FAL_KLING_V1_STD_I2V_ID         = "fal-ai/kling-video/v1/standard/image-to-video"
FAL_KLING_V15_PRO_T2V_ID        = "fal-ai/kling-video/v1.5/pro/text-to-video"
FAL_KLING_V15_PRO_I2V_ID        = "fal-ai/kling-video/v1.5/pro/image-to-video"
FAL_KLING_V16_STD_T2V_ID        = "fal-ai/kling-video/v1.6/standard/text-to-video"
FAL_KLING_V16_STD_I2V_ID        = "fal-ai/kling-video/v1.6/standard/image-to-video"
FAL_KLING_V16_STD_ELEMENTS_ID   = "fal-ai/kling-video/v1.6/standard/elements"
FAL_KLING_V16_PRO_T2V_ID        = "fal-ai/kling-video/v1.6/pro/text-to-video"
FAL_KLING_V16_PRO_I2V_ID        = "fal-ai/kling-video/v1.6/pro/image-to-video"
FAL_KLING_V16_PRO_ELEMENTS_ID   = "fal-ai/kling-video/v1.6/pro/elements"
FAL_KLING_V2_MASTER_T2V_ID      = "fal-ai/kling-video/v2/master/text-to-video"
FAL_KLING_V2_MASTER_I2V_ID      = "fal-ai/kling-video/v2/master/image-to-video"
FAL_KLING_V21_STD_I2V_ID        = "fal-ai/kling-video/v2.1/standard/image-to-video"
FAL_KLING_V21_PRO_I2V_ID        = "fal-ai/kling-video/v2.1/pro/image-to-video"
FAL_KLING_V21_MASTER_T2V_ID     = "fal-ai/kling-video/v2.1/master/text-to-video"
FAL_KLING_V21_MASTER_I2V_ID     = "fal-ai/kling-video/v2.1/master/image-to-video"
FAL_KLING_V25_TURBO_PRO_T2V_ID  = "fal-ai/kling-video/v2.5-turbo/pro/text-to-video"
FAL_KLING_V25_TURBO_STD_I2V_ID  = "fal-ai/kling-video/v2.5-turbo/standard/image-to-video"
FAL_KLING_V25_TURBO_PRO_I2V_ID  = "fal-ai/kling-video/v2.5-turbo/pro/image-to-video"
FAL_KLING_V26_PRO_T2V_ID        = "fal-ai/kling-video/v2.6/pro/text-to-video"
FAL_KLING_V26_PRO_I2V_ID        = "fal-ai/kling-video/v2.6/pro/image-to-video"
FAL_KLING_V30_STD_T2V_ID        = "fal-ai/kling-video/v3/standard/text-to-video"
FAL_KLING_V30_STD_I2V_ID        = "fal-ai/kling-video/v3/standard/image-to-video"
FAL_KLING_V30_PRO_T2V_ID        = "fal-ai/kling-video/v3/pro/text-to-video"
FAL_KLING_V30_PRO_I2V_ID        = "fal-ai/kling-video/v3/pro/image-to-video"
FAL_KLING_V30_4K_T2V_ID         = "fal-ai/kling-video/v3/4k/text-to-video"
FAL_KLING_V30_4K_I2V_ID         = "fal-ai/kling-video/v3/4k/image-to-video"
FAL_KLING_O1_STD_I2V_ID         = "fal-ai/kling-video/o1/standard/image-to-video"
FAL_KLING_O1_REF_I2V_ID         = "fal-ai/kling-video/o1/standard/reference-to-video"
FAL_KLING_O1_PRO_I2V_ID         = "fal-ai/kling-video/o1/image-to-video"
FAL_KLING_O1_PRO_REF_I2V_ID     = "fal-ai/kling-video/o1/reference-to-video"
FAL_KLING_O3_STD_T2V_ID         = "fal-ai/kling-video/o3/standard/text-to-video"
FAL_KLING_O3_STD_I2V_ID         = "fal-ai/kling-video/o3/standard/image-to-video"
FAL_KLING_O3_REF_I2V_ID         = "fal-ai/kling-video/o3/standard/reference-to-video"
FAL_KLING_O3_STD_V2V_ID         = "fal-ai/kling-video/o3/standard/video-to-video/edit"
FAL_KLING_O3_PRO_T2V_ID         = "fal-ai/kling-video/o3/pro/text-to-video"
FAL_KLING_O3_PRO_I2V_ID         = "fal-ai/kling-video/o3/pro/image-to-video"
FAL_KLING_O3_PRO_REF_I2V_ID     = "fal-ai/kling-video/o3/pro/reference-to-video"
FAL_KLING_O3_PRO_V2V_ID         = "fal-ai/kling-video/o3/pro/video-to-video/edit"
FAL_KLING_O3_4K_T2V_ID          = "fal-ai/kling-video/o3/4k/text-to-video"
FAL_KLING_O3_4K_I2V_ID          = "fal-ai/kling-video/o3/4k/image-to-video"
FAL_KLING_O3_4K_REF_I2V_ID      = "fal-ai/kling-video/o3/4k/reference-to-video"
FAL_SEEDANCE_V1_LITE_T2V_ID     = "fal-ai/bytedance/seedance/v1/lite/text-to-video"
FAL_SEEDANCE_V1_LITE_I2V_ID     = "fal-ai/bytedance/seedance/v1/lite/image-to-video"
FAL_SEEDANCE_V1_LITE_REF_ID     = "fal-ai/bytedance/seedance/v1/lite/reference-to-video"
FAL_SEEDANCE_V1_PRO_T2V_ID      = "fal-ai/bytedance/seedance/v1/pro/text-to-video"
FAL_SEEDANCE_V1_PRO_I2V_ID      = "fal-ai/bytedance/seedance/v1/pro/image-to-video"
FAL_SEEDANCE_V1_PRO_FAST_T2V_ID = "fal-ai/bytedance/seedance/v1/pro/fast/text-to-video"
FAL_SEEDANCE_V1_PRO_FAST_I2V_ID = "fal-ai/bytedance/seedance/v1/pro/fast/image-to-video"
FAL_SEEDANCE_V15_PRO_T2V_ID     = "fal-ai/bytedance/seedance/v1.5/pro/text-to-video"
FAL_SEEDANCE_V15_PRO_I2V_ID     = "fal-ai/bytedance/seedance/v1.5/pro/image-to-video"
FAL_SEEDANCE_V20_T2V_ID         = "bytedance/seedance-2.0/text-to-video"
FAL_SEEDANCE_V20_I2V_ID         = "bytedance/seedance-2.0/image-to-video"
FAL_SEEDANCE_V20_REF_ID         = "bytedance/seedance-2.0/reference-to-video"
FAL_WAN_T2V_ID                  = "fal:wan2.7:text-to-video"
FAL_WAN_I2V_ID                  = "fal:wan2.7:image-to-video"
FAL_WAN_FIRST_LAST_ID           = "fal:wan2.7:first-last-frame"
FAL_WAN_CONTINUE_ID             = "fal:wan2.7:continue-video"
FAL_WAN_REF_ID                  = "fal:wan2.7:reference-to-video"
FAL_WAN_EDIT_ID                 = "fal:wan2.7:edit-video"
FAL_WAN_T2V_ENDPOINT            = "fal-ai/wan/v2.7/text-to-video"
FAL_WAN_I2V_ENDPOINT            = "fal-ai/wan/v2.7/image-to-video"
FAL_WAN_REF_ENDPOINT            = "fal-ai/wan/v2.7/reference-to-video"
FAL_WAN_EDIT_ENDPOINT           = "fal-ai/wan/v2.7/edit-video"
FAL_LTX_VIDEO_T2V_ID            = "fal-ai/ltx-video"
FAL_LTX_VIDEO_I2V_ID            = "fal-ai/ltx-video/image-to-video"
FAL_LTX_VIDEO_LORA_I2V_ID       = "fal-ai/ltx-video-lora/image-to-video"
FAL_LTX_23_22B_I2V_ID           = "fal-ai/ltx-2.3-22b/image-to-video"
KLING_DIRECT_TEXT_DEFAULT_ID    = "kling-v3-pro"
KLING_DIRECT_IMAGE_DEFAULT_ID   = "kling-v3-pro"
RUNWAY_GEN4_IMAGE_ID            = "runway:gen4_image"
RUNWAY_GEN4_IMAGE_TURBO_ID      = "runway:gen4_image_turbo"
RUNWAY_GEMINI_FLASH_IMAGE_ID    = "runway:gemini_2.5_flash"
RUNWAY_GEN45_T2V_ID             = "runway:gen4.5:text_to_video"
RUNWAY_VEO31_T2V_ID             = "runway:veo3.1:text_to_video"
RUNWAY_VEO31_FAST_T2V_ID        = "runway:veo3.1_fast:text_to_video"
RUNWAY_GEN45_I2V_ID             = "runway:gen4.5:image_to_video"
RUNWAY_GEN4_TURBO_I2V_ID        = "runway:gen4_turbo:image_to_video"
RUNWAY_GEN3A_TURBO_I2V_ID       = "runway:gen3a_turbo:image_to_video"
RUNWAY_VEO3_I2V_ID              = "runway:veo3:image_to_video"
RUNWAY_VEO31_I2V_ID             = "runway:veo3.1:image_to_video"
RUNWAY_VEO31_FAST_I2V_ID        = "runway:veo3.1_fast:image_to_video"
RUNWAY_GEN4_ALEPH_V2V_ID        = "runway:gen4_aleph:video_to_video"
LUMA_RAY2_T2V_ID                = "luma:ray-2:text_to_video"
LUMA_RAY2_I2V_ID                = "luma:ray-2:image_to_video"
LUMA_RAY_FLASH2_T2V_ID          = "luma:ray-flash-2:text_to_video"
LUMA_RAY_FLASH2_I2V_ID          = "luma:ray-flash-2:image_to_video"
UPSCALER_MODELS = {
    "seedvr2": {"id": FAL_SEEDVR_UPSCALE_ID, "label": "SeedVR2"},
}

BYTEPLUS_SEEDREAM_45_MODEL_ID   = "seedream-4-5-251128"
FAL_GPT_IMAGE_2_MAX_PIXELS      = 8_294_400
FAL_GPT_IMAGE_2_MAX_SIDE_BY_SIZE = {
    "1K": 1024,
    "2K": 2048,
    "4K": 3840,
}
FAL_GPT_IMAGE_2_HIGH_QUALITY_PRICING = {
    (1024, 768): 0.15,
    (768, 1024): 0.15,
    (1024, 1024): 0.22,
    (1024, 1536): 0.17,
    (1536, 1024): 0.17,
    (1920, 1080): 0.16,
    (1080, 1920): 0.16,
    (2560, 1440): 0.23,
    (1440, 2560): 0.23,
    (3840, 2160): 0.41,
    (2160, 3840): 0.41,
}

RUNWAY_IMAGE_RATIO_MAPS = {
    RUNWAY_GEN4_IMAGE_ID: {
        "1:1": "1024:1024",
        "16:9": "1360:768",
        "9:16": "720:1280",
        "4:3": "1440:1080",
        "3:4": "1080:1440",
    },
    RUNWAY_GEN4_IMAGE_TURBO_ID: {
        "1:1": "1024:1024",
        "16:9": "1360:768",
        "9:16": "720:1280",
        "4:3": "1440:1080",
        "3:4": "1080:1440",
    },
    RUNWAY_GEMINI_FLASH_IMAGE_ID: {
        "1:1": "1024:1024",
        "16:9": "1536:672",
        "9:16": "768:1344",
    },
}

RUNWAY_VIDEO_MODEL_SPECS = [
    {
        "id": RUNWAY_GEN45_T2V_ID,
        "runway_model": "gen4.5",
        "label": "Runway Gen-4.5",
        "input_modes": ["text"],
        "aspect_ratios": ["16:9", "9:16"],
        "duration_options": list(range(2, 11)),
        "sort_order": 410,
        "video_mode_kind": "text_to_video",
    },
    {
        "id": RUNWAY_VEO31_T2V_ID,
        "runway_model": "veo3.1",
        "label": "Runway Veo 3.1",
        "input_modes": ["text"],
        "aspect_ratios": ["16:9", "9:16"],
        "duration_options": [4, 6, 8],
        "sort_order": 411,
        "video_mode_kind": "text_to_video",
    },
    {
        "id": RUNWAY_VEO31_FAST_T2V_ID,
        "runway_model": "veo3.1_fast",
        "label": "Runway Veo 3.1 Fast",
        "input_modes": ["text"],
        "aspect_ratios": ["16:9", "9:16"],
        "duration_options": [4, 6, 8],
        "sort_order": 412,
        "video_mode_kind": "text_to_video",
    },
    {
        "id": RUNWAY_GEN45_I2V_ID,
        "runway_model": "gen4.5",
        "label": "Runway Gen-4.5",
        "input_modes": ["image"],
        "aspect_ratios": ["16:9", "9:16", "4:3", "1:1", "3:4", "21:9"],
        "duration_options": list(range(2, 11)),
        "supports_start_image": True,
        "start_image_required": True,
        "sort_order": 420,
        "video_mode_kind": "image_to_video",
    },
    {
        "id": RUNWAY_GEN4_TURBO_I2V_ID,
        "runway_model": "gen4_turbo",
        "label": "Runway Gen-4 Turbo",
        "input_modes": ["image"],
        "aspect_ratios": ["16:9", "9:16", "4:3", "1:1", "3:4", "21:9"],
        "duration_options": list(range(2, 11)),
        "supports_start_image": True,
        "start_image_required": True,
        "sort_order": 421,
        "video_mode_kind": "image_to_video",
    },
    {
        "id": RUNWAY_GEN3A_TURBO_I2V_ID,
        "runway_model": "gen3a_turbo",
        "label": "Runway Gen-3 Alpha Turbo",
        "input_modes": ["image"],
        "aspect_ratios": ["16:9", "9:16"],
        "duration_options": [5, 10],
        "supports_start_image": True,
        "start_image_required": True,
        "sort_order": 422,
        "video_mode_kind": "image_to_video",
    },
    {
        "id": RUNWAY_VEO3_I2V_ID,
        "runway_model": "veo3",
        "label": "Runway Veo 3",
        "input_modes": ["image"],
        "aspect_ratios": ["16:9", "9:16"],
        "duration_options": [8],
        "supports_start_image": True,
        "start_image_required": True,
        "sort_order": 423,
        "video_mode_kind": "image_to_video",
    },
    {
        "id": RUNWAY_VEO31_I2V_ID,
        "runway_model": "veo3.1",
        "label": "Runway Veo 3.1",
        "input_modes": ["image"],
        "aspect_ratios": ["16:9", "9:16"],
        "duration_options": [4, 6, 8],
        "supports_start_image": True,
        "start_image_required": True,
        "sort_order": 424,
        "video_mode_kind": "image_to_video",
    },
    {
        "id": RUNWAY_VEO31_FAST_I2V_ID,
        "runway_model": "veo3.1_fast",
        "label": "Runway Veo 3.1 Fast",
        "input_modes": ["image"],
        "aspect_ratios": ["16:9", "9:16"],
        "duration_options": [4, 6, 8],
        "supports_start_image": True,
        "start_image_required": True,
        "sort_order": 425,
        "video_mode_kind": "image_to_video",
    },
    {
        "id": RUNWAY_GEN4_ALEPH_V2V_ID,
        "runway_model": "gen4_aleph",
        "label": "Runway Gen-4 Aleph",
        "input_modes": ["video"],
        "aspect_ratios": [],
        "duration_options": [],
        "supports_start_image": False,
        "supports_source_video": True,
        "source_video_required": True,
        "supports_duration": False,
        "supports_aspect_ratio": False,
        "sort_order": 430,
        "video_mode_kind": "video_to_video",
    },
]

LUMA_VIDEO_MODEL_SPECS = [
    {
        "id": LUMA_RAY2_T2V_ID,
        "native_model_name": "ray-2",
        "label": "Luma Ray 2",
        "input_modes": ["text"],
        "aspect_ratios": ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "9:21"],
        "durations": [5, 9],
        "resolutions": ["720p", "1080p", "4k", "540p"],
        "sort_order": 510,
        "video_mode_kind": "text_to_video",
    },
    {
        "id": LUMA_RAY2_I2V_ID,
        "native_model_name": "ray-2",
        "label": "Luma Ray 2",
        "input_modes": ["image"],
        "aspect_ratios": ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "9:21"],
        "durations": [5, 9],
        "resolutions": ["720p", "1080p", "4k", "540p"],
        "supports_start_image": True,
        "start_image_required": True,
        "sort_order": 511,
        "video_mode_kind": "image_to_video",
    },
    {
        "id": LUMA_RAY_FLASH2_T2V_ID,
        "native_model_name": "ray-flash-2",
        "label": "Luma Ray Flash 2",
        "input_modes": ["text"],
        "aspect_ratios": ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "9:21"],
        "durations": [5, 9],
        "resolutions": ["720p", "1080p", "4k", "540p"],
        "sort_order": 512,
        "video_mode_kind": "text_to_video",
    },
    {
        "id": LUMA_RAY_FLASH2_I2V_ID,
        "native_model_name": "ray-flash-2",
        "label": "Luma Ray Flash 2",
        "input_modes": ["image"],
        "aspect_ratios": ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "9:21"],
        "durations": [5, 9],
        "resolutions": ["720p", "1080p", "4k", "540p"],
        "supports_start_image": True,
        "start_image_required": True,
        "sort_order": 513,
        "video_mode_kind": "image_to_video",
    },
]

MODELS_INFO = {
    "gemini-2.5-flash-image": {
        "provider":       "gemini",
        "provider_label": "Gemini",
        "family":         "nano-banana",
        "label":          "Nano Banana",
        "resolutions":    ["1K"],
        "thinking":       False,
        "supports_search": True,
        "aspect_ratios":  ASPECT_RATIOS_BASE,
        "max_images":     4,
        "max_ref_images": 0,
        "ref_note":       "Gemini API mode - does not support reference images"
    },
    "fal-ai/gemini-25-flash-image": {
        "provider":       "fal",
        "provider_label": "Fal",
        "family":         "nano-banana",
        "label":          "Nano Banana",
        "resolutions":    ["1K"],
        "thinking":       False,
        "supports_search": True,
        "aspect_ratios":  ASPECT_RATIOS_FAL,
        "max_images":     4,
        "max_ref_images": 14,
        "ref_note":       "Fal API mode - up to 14 reference images"
    },
    "gemini-3-pro-image-preview": {
        "provider":       "gemini",
        "provider_label": "Gemini",
        "family":         "nano-banana-pro",
        "label":          "Nano Banana Pro",
        "resolutions":    ["1K","2K","4K"],
        "thinking":       False,
        "supports_search": True,
        "aspect_ratios":  ASPECT_RATIOS_BASE,
        "max_images":     4,
        "max_ref_images": 8,
        "ref_note":       "Gemini API mode - up to 8 reference images"
    },
    "fal-ai/nano-banana-pro": {
        "provider":       "fal",
        "provider_label": "Fal",
        "family":         "nano-banana-pro",
        "label":          "Nano Banana Pro",
        "resolutions":    ["1K","2K","4K"],
        "thinking":       False,
        "supports_search": True,
        "aspect_ratios":  ASPECT_RATIOS_FAL,
        "max_images":     4,
        "max_ref_images": 14,
        "ref_note":       "Fal API mode - up to 14 reference images"
    },
    "gemini-3.1-flash-image-preview": {
        "provider":       "gemini",
        "provider_label": "Gemini",
        "family":         "nano-banana-2",
        "label":          "Nano Banana 2",
        "resolutions":    ["0.5K","1K","2K","4K"],
        "thinking":       True,
        "supports_search": True,
        "aspect_ratios":  ASPECT_RATIOS_BASE + ASPECT_RATIOS_NB2_EXTRA,
        "max_images":     4,
        "max_ref_images": 14,
        "ref_note":       "Gemini API mode - up to 14 reference images"
    },
    "fal-ai/nano-banana-2": {
        "provider":       "fal",
        "provider_label": "Fal",
        "family":         "nano-banana-2",
        "label":          "Nano Banana 2",
        "resolutions":    ["0.5K","1K","2K","4K"],
        "thinking":       False,
        "supports_search": True,
        "aspect_ratios":  ASPECT_RATIOS_FAL,
        "max_images":     4,
        "max_ref_images": 14,
        "ref_note":       "Fal API mode - up to 14 reference images"
    },
    "fal-ai/gpt-image-2": {
        "provider":       "fal",
        "provider_label": "Fal",
        "family":         "gpt-image-2",
        "label":          "GPT Image 2",
        "resolutions":    ["1K","2K","4K"],
        "thinking":       False,
        "aspect_ratios":  ASPECT_RATIOS_GPT_IMAGE_2,
        "max_images":     4,
        "max_ref_images": 0,
        "ref_note":       "Fal API mode - text-to-image only (no reference images)"
    },
    "fal-ai/gpt-image-2/edit": {
        "provider":       "fal",
        "provider_label": "Fal",
        "family":         "gpt-image-2-edit",
        "label":          "GPT Image 2 Edit",
        "resolutions":    ["1K","2K","4K"],
        "thinking":       False,
        "aspect_ratios":  ASPECT_RATIOS_GPT_IMAGE_2,
        "max_images":     4,
        "max_ref_images": 16,
        "ref_note":       "Fal API mode - edit model, requires at least 1 reference image and supports up to 16"
    },
    "openai/gpt-image-2": {
        "provider":       "openai",
        "provider_label": "OpenAI",
        "family":         "gpt-image-2",
        "label":          "GPT Image 2",
        "resolutions":    ["1K","2K","4K"],
        "thinking":       False,
        "aspect_ratios":  ASPECT_RATIOS_GPT_IMAGE_2,
        "max_images":     4,
        "max_ref_images": 0,
        "ref_note":       "OpenAI Images API - text-to-image only (no reference images, no seed)"
    },
    "openai/gpt-image-2/edit": {
        "provider":       "openai",
        "provider_label": "OpenAI",
        "family":         "gpt-image-2-edit",
        "label":          "GPT Image 2 Edit",
        "resolutions":    ["1K","2K","4K"],
        "thinking":       False,
        "aspect_ratios":  ASPECT_RATIOS_GPT_IMAGE_2,
        "max_images":     4,
        "max_ref_images": 16,
        "ref_note":       "OpenAI Images API - edit model, requires at least 1 reference image and supports up to 16"
    },
    **{
        model_id: {
            "provider":       "openai",
            "provider_label": "OpenAI",
            "family":         spec["family"],
            "label":          spec["label"],
            "resolutions":    ["1K","2K","4K"],
            "thinking":       False,
            "aspect_ratios":  ASPECT_RATIOS_GPT_IMAGE_2,
            "max_images":     4,
            "max_ref_images": 16,
            "ref_note":       "OpenAI Images API (preview) - text-to-image, or an edit when reference images are attached (up to 16)"
        }
        for model_id, spec in OPENAI_GPT_IMAGE_25_MODELS.items()
    },
    "fal-ai/bytedance/seedream/v4.5/text-to-image": {
        "provider":       "fal",
        "provider_label": "Fal",
        "family":         "seedream-45",
        "label":          "Seedream 4.5",
        "resolutions":    ["2K","4K"],
        "thinking":       False,
        "aspect_ratios":  ASPECT_RATIOS_FAL,
        "max_images":     4,
        "max_ref_images": 10,
        "ref_note":       "Fal API mode - up to 10 reference images"
    },
    "seedream-4-5-251128": {
        "provider":       "byteplus",
        "provider_label": "BytePlus",
        "family":         "seedream-45",
        "label":          "Seedream 4.5",
        "resolutions":    ["2K","4K"],
        "thinking":       False,
        "aspect_ratios":  ASPECT_RATIOS_BYTEPLUS,
        "max_images":     4,
        "max_ref_images": 10,
        "ref_note":       "BytePlus API mode - up to 10 reference images"
    },
    "fal-ai/bytedance/seedream/v5/lite/text-to-image": {
        "provider":       "fal",
        "provider_label": "Fal",
        "family":         "seedream-5-lite",
        "label":          "Seedream 5 Lite",
        "resolutions":    ["2K","4K"],
        "thinking":       False,
        "aspect_ratios":  ASPECT_RATIOS_FAL,
        "max_images":     4,
        "max_ref_images": 10,
        "ref_note":       "Fal API mode - up to 10 reference images"
    },
    RUNWAY_GEN4_IMAGE_ID: {
        "provider":       "runway",
        "provider_label": "Runway",
        "family":         "runway-gen4-image",
        "label":          "Runway Gen-4 Image",
        "runway_model":   "gen4_image",
        "resolutions":    ["1K"],
        "thinking":       False,
        "aspect_ratios":  list(RUNWAY_IMAGE_RATIO_MAPS[RUNWAY_GEN4_IMAGE_ID].keys()),
        "max_images":     1,
        "max_ref_images": 3,
        "ref_note":       "Runway API mode - up to 3 reference images"
    },
    RUNWAY_GEN4_IMAGE_TURBO_ID: {
        "provider":       "runway",
        "provider_label": "Runway",
        "family":         "runway-gen4-image-turbo",
        "label":          "Runway Gen-4 Image Turbo",
        "runway_model":   "gen4_image_turbo",
        "resolutions":    ["1K"],
        "thinking":       False,
        "aspect_ratios":  list(RUNWAY_IMAGE_RATIO_MAPS[RUNWAY_GEN4_IMAGE_TURBO_ID].keys()),
        "max_images":     1,
        "max_ref_images": 3,
        "ref_note":       "Runway API mode - up to 3 reference images"
    },
    RUNWAY_GEMINI_FLASH_IMAGE_ID: {
        "provider":       "runway",
        "provider_label": "Runway",
        "family":         "runway-gemini-25-flash",
        "label":          "Runway Gemini 2.5 Flash",
        "runway_model":   "gemini_2.5_flash",
        "resolutions":    ["1K"],
        "thinking":       False,
        "aspect_ratios":  list(RUNWAY_IMAGE_RATIO_MAPS[RUNWAY_GEMINI_FLASH_IMAGE_ID].keys()),
        "max_images":     1,
        "max_ref_images": 3,
        "ref_note":       "Runway API mode - up to 3 reference images"
    }
}

MODEL_FAMILIES = {
    "gpt-image-2-edit": {
        "label": "GPT Image 2 Edit",
        "badge": "GPT2E",
        "default_provider": "fal",
        "provider_order": ["fal", "openai"],
        "providers": {
            "fal": FAL_GPT_IMAGE_2_EDIT_ID,
            "openai": OPENAI_GPT_IMAGE_2_EDIT_ID,
        },
    },
    "gpt-image-2": {
        "label": "GPT Image 2",
        "badge": "GPT2",
        "default_provider": "fal",
        "provider_order": ["fal", "openai"],
        "providers": {
            "fal": FAL_GPT_IMAGE_2_TEXT_ID,
            "openai": OPENAI_GPT_IMAGE_2_TEXT_ID,
        },
    },
    **{
        spec["family"]: {
            "label": spec["label"],
            "badge": spec["badge"],
            "default_provider": "openai",
            "provider_order": ["openai"],
            "providers": {"openai": model_id},
        }
        for model_id, spec in OPENAI_GPT_IMAGE_25_MODELS.items()
    },
    "nano-banana-2": {
        "label": "Nano Banana 2",
        "badge": "NB2",
        "default_provider": "gemini",
        "provider_order": ["gemini", "fal"],
        "providers": {
            "gemini": "gemini-3.1-flash-image-preview",
            "fal": FAL_NANO_BANANA_2_TEXT_ID,
        },
    },
    "seedream-45": {
        "label": "Seedream 4.5",
        "badge": "SD45",
        "default_provider": "fal",
        "provider_order": ["fal", "byteplus"],
        "providers": {
            "fal": FAL_SEEDREAM_45_TEXT_ID,
            "byteplus": BYTEPLUS_SEEDREAM_45_MODEL_ID,
        },
    },
    "seedream-5-lite": {
        "label": "Seedream 5 Lite",
        "badge": "SD5",
        "default_provider": "fal",
        "provider_order": ["fal"],
        "providers": {
            "fal": FAL_SEEDREAM_5_TEXT_ID,
        },
    },
    "nano-banana-pro": {
        "label": "Nano Banana Pro",
        "badge": "PRO",
        "default_provider": "gemini",
        "provider_order": ["gemini", "fal"],
        "providers": {
            "gemini": "gemini-3-pro-image-preview",
            "fal": FAL_NANO_BANANA_PRO_TEXT_ID,
        },
    },
    "nano-banana": {
        "label": "Nano Banana",
        "badge": "NB",
        "default_provider": "gemini",
        "provider_order": ["gemini", "fal"],
        "providers": {
            "gemini": "gemini-2.5-flash-image",
            "fal": FAL_NANO_BANANA_TEXT_ID,
        },
    },
    "runway-gen4-image": {
        "label": "Runway Gen-4 Image",
        "badge": "RW4",
        "default_provider": "runway",
        "provider_order": ["runway"],
        "providers": {
            "runway": RUNWAY_GEN4_IMAGE_ID,
        },
    },
    "runway-gen4-image-turbo": {
        "label": "Runway Gen-4 Image Turbo",
        "badge": "RW4T",
        "default_provider": "runway",
        "provider_order": ["runway"],
        "providers": {
            "runway": RUNWAY_GEN4_IMAGE_TURBO_ID,
        },
    },
    "runway-gemini-25-flash": {
        "label": "Runway Gemini 2.5 Flash",
        "badge": "RWG",
        "default_provider": "runway",
        "provider_order": ["runway"],
        "providers": {
            "runway": RUNWAY_GEMINI_FLASH_IMAGE_ID,
        },
    },
}


def resolve_model_selection(requested_model: str = "", requested_family: str = "", requested_provider: str = "") -> tuple[str, str, str, dict]:
    model_id = str(requested_model or "").strip()
    family_key = str(requested_family or "").strip()
    provider_key = str(requested_provider or "").strip().lower()

    if family_key in MODEL_FAMILIES:
        family_info = MODEL_FAMILIES[family_key]
        available = family_info.get("providers", {})
        if provider_key not in available:
            provider_key = family_info.get("default_provider", "") or next(iter(available.keys()), "")
        model_id = available.get(provider_key, model_id)
        model_info = MODELS_INFO.get(model_id)
        if model_info:
            return model_id, family_key, provider_key, model_info

    if model_id in MODELS_INFO:
        model_info = MODELS_INFO[model_id]
        return model_id, model_info.get("family", family_key or "nano-banana-2"), model_info.get("provider", provider_key or "gemini"), model_info

    fallback_family = "nano-banana-2"
    fallback_provider = MODEL_FAMILIES[fallback_family]["default_provider"]
    fallback_model = MODEL_FAMILIES[fallback_family]["providers"][fallback_provider]
    return fallback_model, fallback_family, fallback_provider, MODELS_INFO[fallback_model]


def normalize_generation_request(body: dict | None) -> dict:
    payload = dict(body or {})
    model_id, family_key, provider_key, model_info = resolve_model_selection(
        payload.get("model", ""),
        payload.get("modelFamily", ""),
        payload.get("provider", ""),
    )
    payload["model"] = model_id
    payload["modelFamily"] = family_key
    payload["provider"] = provider_key
    payload["modelLabel"] = model_info.get("label", model_id)
    payload["providerLabel"] = model_info.get("provider_label", PROVIDER_LABELS.get(provider_key, provider_key.title()))
    apply_project_record_scope(payload)
    payload.update(normalize_asset_metadata(payload, require_filename=False))
    return payload


def resolve_video_model_selection(
    requested_model: str = "",
    requested_family: str = "",
    requested_provider: str = "",
    requested_input_mode: str = "text",
) -> tuple[str, str, str, dict]:
    model_id = str(requested_model or "").strip()
    family_key = str(requested_family or "").strip()
    provider_key = str(requested_provider or "").strip().lower()
    input_mode = str(requested_input_mode or "").strip().lower()

    if model_id in VIDEO_MODELS_INFO:
        model_info = VIDEO_MODELS_INFO[model_id]
        return (
            model_id,
            model_info.get("family", family_key or "kling"),
            model_info.get("provider", provider_key or "kling"),
            model_info,
        )

    if family_key in VIDEO_MODEL_FAMILIES:
        family_info = VIDEO_MODEL_FAMILIES[family_key]
        providers = list(family_info.get("provider_order", [])) or list((family_info.get("providers") or {}).keys())
        if provider_key and provider_key in providers:
            ordered_providers = [provider_key] + [prov for prov in providers if prov != provider_key]
        elif provider_key:
            ordered_providers = [provider_key] + providers
        else:
            default_provider = family_info.get("default_provider", "") or (providers[0] if providers else "")
            ordered_providers = [default_provider] + [prov for prov in providers if prov != default_provider]
        for provider_name in ordered_providers:
            candidates = get_video_model_candidates(family_key, provider_name, input_mode)
            if not candidates:
                candidates = get_video_model_candidates(family_key, provider_name, "")
            if candidates:
                resolved_model_id, resolved_model_info = candidates[0]
                return resolved_model_id, family_key, provider_name, resolved_model_info

    fallback_family = "kling"
    fallback_family_info = VIDEO_MODEL_FAMILIES[fallback_family]
    fallback_provider = fallback_family_info["default_provider"]
    fallback_candidates = get_video_model_candidates(fallback_family, fallback_provider, input_mode)
    if not fallback_candidates:
        fallback_candidates = get_video_model_candidates(fallback_family, fallback_provider, "")
    fallback_model, fallback_info = fallback_candidates[0]
    return fallback_model, fallback_family, fallback_provider, fallback_info


def normalize_video_duration(value, default: int = 5) -> int:
    try:
        duration = int(str(value or default).strip())
    except Exception:
        duration = default
    return duration if 1 <= duration <= 120 else default


def normalize_video_resolution(value: str) -> str:
    resolution = str(value or "720p").strip().lower()
    return resolution if resolution in {"480p", "540p", "580p", "720p", "1080p", "1440p", "2160p", "4k"} else "720p"


def normalize_optional_unit_float(value):
    """A float in 0..1, or None when blank/invalid (used for cfg_scale)."""
    if value is None or str(value).strip() == "":
        return None
    try:
        number = float(str(value).strip())
    except Exception:
        return None
    return max(0.0, min(1.0, number))


def parse_multi_prompt_shots(text: str, default_duration: int = 5) -> list[dict]:
    """
    One shot per line. An optional leading "<seconds>s:" sets that shot's length:
        3s: the cat wakes up
        the cat stretches
    """
    shots: list[dict] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        duration = default_duration
        match = re.match(r"^(\d{1,2})\s*s?\s*[:|\-]\s*(.+)$", line)
        if match:
            duration = int(match.group(1))
            line = match.group(2).strip()
        if line:
            shots.append({"prompt": line, "duration": str(max(1, min(15, duration)))})
    return shots


def normalize_video_elements(items, model_info: dict) -> list[dict]:
    """
    Kling v3 style elements: each one is a frontal image + 1..N angle references,
    or a single video. Empty elements are dropped.
    """
    schema = model_info.get("element_schema") or {}
    max_elements = max(0, int(model_info.get("max_elements", 0) or 0))
    refs_max = max(0, int(schema.get("references_max", 3) or 0))
    out: list[dict] = []
    for raw in (items or []):
        if not isinstance(raw, dict):
            continue
        frontal = normalize_video_image_payload(raw.get("frontal"), "element-frontal.png") if isinstance(raw.get("frontal"), dict) else {}
        refs = normalize_video_image_payloads(raw.get("references")) if isinstance(raw.get("references"), list) else []
        if refs_max:
            refs = refs[:refs_max]
        video = normalize_video_file_payload(raw.get("video"), "element-video.mp4") if isinstance(raw.get("video"), dict) else {}
        has_frontal = bool(str(frontal.get("data") or frontal.get("url") or "").strip())
        has_refs = any(str(r.get("data") or r.get("url") or "").strip() for r in refs)
        has_video = bool(str(video.get("data") or video.get("url") or "").strip())
        if not (has_frontal or has_refs or has_video):
            continue
        out.append({
            "name": str(raw.get("name") or "").strip()[:80],
            "frontal": frontal if has_frontal else {},
            "references": [r for r in refs if str(r.get("data") or r.get("url") or "").strip()],
            "video": video if has_video else {},
            "voice_id": str(raw.get("voice_id") or "").strip(),
        })
        if max_elements and len(out) >= max_elements:
            break
    return out


def normalize_video_input_mode(value: str) -> str:
    mode = str(value or "text").strip().lower()
    return mode if mode in {"text", "image", "reference", "video"} else "text"


def normalize_video_image_payload(image: dict | None, default_name: str) -> dict:
    if not isinstance(image, dict):
        image = {}
    payload = {
        "mime_type": str(image.get("mime_type") or "image/png"),
        "data": str(image.get("data") or ""),
        "name": str(image.get("name") or default_name),
        "original_data": str(image.get("original_data") or ""),
        "original_mime_type": str(image.get("original_mime_type") or ""),
        "mask_png_data": str(image.get("mask_png_data") or ""),
        "archive_date": str(image.get("archive_date") or ""),
        "archive_filename": os.path.basename(str(image.get("archive_filename") or "")),
        "original_url": str(image.get("original_url") or ""),
        "masked_url": str(image.get("masked_url") or ""),
        "mask_url": str(image.get("mask_url") or ""),
        "has_mask": bool(image.get("has_mask")),
    }
    if payload["data"]:
        try:
            clamped_b64, clamped_mime = clamp_image_b64_max_side(payload["data"], payload["mime_type"])
            payload["data"] = clamped_b64
            payload["mime_type"] = clamped_mime
            safe_b64, safe_mime = compress_video_input_image(payload["data"], payload["mime_type"])
            payload["data"] = safe_b64
            payload["mime_type"] = safe_mime
        except Exception:
            pass
    return payload


def normalize_video_image_payloads(items) -> list[dict]:
    if not isinstance(items, list):
        return []
    normalized = []
    for idx, item in enumerate(items, start=1):
        payload = normalize_video_image_payload(item, f"video-reference-{idx}.png")
        if payload.get("data"):
            normalized.append(payload)
    return normalized


def normalize_video_file_payload(video: dict | None, default_name: str) -> dict:
    if not isinstance(video, dict):
        video = {}
    mime_type = str(video.get("mime_type") or "video/mp4").split(";", 1)[0].strip().lower() or "video/mp4"
    payload = {
        "mime_type": mime_type,
        "data": str(video.get("data") or ""),
        "name": os.path.basename(str(video.get("name") or default_name)) or default_name,
        "url": str(video.get("url") or ""),
    }
    return payload


def normalize_video_file_payloads(items, default_prefix: str = "video-reference") -> list[dict]:
    if not isinstance(items, list):
        return []
    normalized = []
    for idx, item in enumerate(items, start=1):
        payload = normalize_video_file_payload(item, f"{default_prefix}-{idx}.mp4")
        if str(payload.get("data") or payload.get("url") or "").strip():
            normalized.append(payload)
    return normalized


def normalize_audio_file_payload(audio: dict | None, default_name: str = "driving-audio.mp3") -> dict:
    if not isinstance(audio, dict):
        audio = {}
    mime_type = str(audio.get("mime_type") or "audio/mpeg").split(";", 1)[0].strip().lower() or "audio/mpeg"
    payload = {
        "mime_type": mime_type,
        "data": str(audio.get("data") or ""),
        "name": os.path.basename(str(audio.get("name") or default_name)) or default_name,
        "url": str(audio.get("url") or ""),
    }
    return payload


def normalize_video_upscale_mode(value: str | None) -> str:
    mode = str(value or "factor").strip().lower()
    return mode if mode in {"factor", "target"} else "factor"


def normalize_video_upscale_factor(value, default: float = 2.0) -> float:
    try:
        factor = float(value)
    except Exception:
        factor = default
    return max(1.0, min(10.0, round(factor, 3)))


def normalize_video_upscale_target_resolution(value: str | None) -> str:
    resolution = str(value or "1080p").strip().lower()
    return resolution if resolution in {"720p", "1080p", "1440p", "2160p"} else "1080p"


def normalize_video_upscale_noise_scale(value, default: float = 0.1) -> float:
    try:
        noise = float(value)
    except Exception:
        noise = default
    return max(0.0, min(1.0, round(noise, 3)))


def normalize_video_upscale_write_mode(value: str | None) -> str:
    write_mode = str(value or "balanced").strip().lower()
    return write_mode if write_mode in {"fast", "balanced", "small"} else "balanced"


def normalize_video_upscale_output_quality(value: str | None) -> str:
    quality = str(value or "high").strip().lower()
    return quality if quality in {"low", "medium", "high", "maximum"} else "high"


def normalize_video_upscale_output_format(value: str | None) -> str:
    fmt = str(value or "X264 (.mp4)").strip()
    allowed = {"X264 (.mp4)", "VP9 (.webm)", "PRORES4444 (.mov)", "GIF (.gif)"}
    return fmt if fmt in allowed else "X264 (.mp4)"


def normalize_optional_int(value):
    if value in (None, "", False):
        return None
    try:
        return int(str(value).strip())
    except Exception:
        return None


def normalize_video_request(body: dict | None) -> dict:
    payload = dict(body or {})
    requested_input_mode = normalize_video_input_mode(payload.get("videoInputMode", "text"))
    model_id, family_key, provider_key, model_info = resolve_video_model_selection(
        payload.get("model", ""),
        payload.get("modelFamily", ""),
        payload.get("provider", ""),
        requested_input_mode,
    )
    payload["model"] = model_id
    payload["modelFamily"] = family_key
    payload["provider"] = provider_key
    payload["modelLabel"] = model_info.get("label", model_id)
    payload["providerLabel"] = model_info.get("provider_label", PROVIDER_LABELS.get(provider_key, provider_key.title()))
    supported_modes = [str(mode).strip().lower() for mode in model_info.get("input_modes", [])]
    payload["videoInputMode"] = requested_input_mode if requested_input_mode in supported_modes or not supported_modes else supported_modes[0]
    input_mode = payload["videoInputMode"]
    supports_start_image = bool(model_info.get("supports_start_image")) and input_mode != "text"
    supports_reference_images = bool(model_info.get("supports_reference_images")) and input_mode == "reference"
    supports_source_video = bool(model_info.get("supports_source_video")) and input_mode == "video"
    supports_driving_audio = bool(model_info.get("supports_driving_audio"))
    supports_reference_videos = bool(model_info.get("supports_reference_videos")) and input_mode == "reference"
    allowed_durations = [int(x) for x in (model_info.get("durations") or []) if str(x).strip()]
    allowed_ratios = [str(x).strip() for x in (model_info.get("aspect_ratios") or []) if str(x).strip()]
    allowed_resolutions = [str(x).strip() for x in (model_info.get("resolutions") or []) if str(x).strip()]
    payload["duration"] = normalize_video_duration(payload.get("duration", allowed_durations[0] if allowed_durations else 5))
    if allowed_durations and payload["duration"] not in allowed_durations:
        payload["duration"] = allowed_durations[0]

    requested_aspect_ratio = str(payload.get("aspectRatio", allowed_ratios[0] if allowed_ratios else "16:9") or "").strip()
    if allowed_ratios:
        allowed_ratio_lookup = {value.lower(): value for value in allowed_ratios}
        payload["aspectRatio"] = allowed_ratio_lookup.get(requested_aspect_ratio.lower(), allowed_ratios[0])
    else:
        payload["aspectRatio"] = requested_aspect_ratio if requested_aspect_ratio in VIDEO_ASPECT_RATIOS else "16:9"

    requested_resolution = str(payload.get("resolution", allowed_resolutions[0] if allowed_resolutions else "720p") or "").strip()
    if allowed_resolutions:
        allowed_resolution_lookup = {value.lower(): value for value in allowed_resolutions}
        payload["resolution"] = allowed_resolution_lookup.get(requested_resolution.lower(), allowed_resolutions[0])
    else:
        payload["resolution"] = normalize_video_resolution(requested_resolution)

    payload["negativePrompt"] = str(payload.get("negativePrompt", "") or "").strip()
    payload["videoSafetyChecker"] = bool(payload.get("videoSafetyChecker", True))
    payload["videoOutputSafetyChecker"] = bool(payload.get("videoOutputSafetyChecker", True))
    payload["videoGenerateAudio"] = bool(payload.get("videoGenerateAudio", False))
    payload["sourceImage"] = normalize_video_image_payload(payload.get("sourceImage"), "video-source.png") if supports_start_image else {}
    payload["sourceVideo"] = normalize_video_file_payload(payload.get("sourceVideo"), "video-source.mp4") if supports_source_video else {}
    payload["sourceAudio"] = normalize_audio_file_payload(payload.get("sourceAudio"), "driving-audio.mp3") if supports_driving_audio else {}
    payload["referenceImages"] = normalize_video_image_payloads(payload.get("referenceImages")) if supports_reference_images else []
    payload["referenceVideos"] = normalize_video_file_payloads(payload.get("referenceVideos"), "video-reference") if supports_reference_videos else []
    supports_end_image = bool(model_info.get("supports_end_image")) and input_mode != "text"
    payload["endImage"] = normalize_video_image_payload(payload.get("endImage"), "video-end-frame.png") if supports_end_image else {}
    payload["videoCfgScale"] = normalize_optional_unit_float(payload.get("videoCfgScale")) if model_info.get("supports_cfg_scale") else None
    payload["videoMultiPrompt"] = str(payload.get("videoMultiPrompt") or "").strip() if model_info.get("supports_multi_prompt") else ""
    payload["videoSeed"] = normalize_optional_int(payload.get("videoSeed")) if model_info.get("supports_seed") else None
    payload["videoPromptExpansion"] = bool(payload.get("videoPromptExpansion", True))
    shot_options = [str(x) for x in (model_info.get("shot_type_options") or [])]
    requested_shot = str(payload.get("videoShotType") or model_info.get("shot_type_default") or "").strip()
    payload["videoShotType"] = requested_shot if (model_info.get("supports_shot_type") and requested_shot in shot_options) else ""
    payload["videoElements"] = normalize_video_elements(payload.get("videoElements"), model_info) if model_info.get("supports_elements") else []
    if not supports_reference_images:
        payload["referenceImages"] = []
    else:
        max_refs = max(0, int(model_info.get("max_reference_images", 0) or 0))
        if max_refs:
            payload["referenceImages"] = payload["referenceImages"][:max_refs]
    if not supports_reference_videos:
        payload["referenceVideos"] = []
    else:
        max_ref_videos = max(0, int(model_info.get("max_reference_videos", 0) or 0))
        if max_ref_videos:
            payload["referenceVideos"] = payload["referenceVideos"][:max_ref_videos]
    payload["videoWanMultiShots"] = bool(payload.get("videoWanMultiShots", False)) if model_info.get("supports_multi_shots") else False
    payload["videoUpscaleMode"] = normalize_video_upscale_mode(payload.get("videoUpscaleMode", "factor"))
    payload["videoUpscaleFactor"] = normalize_video_upscale_factor(payload.get("videoUpscaleFactor", 2))
    payload["videoUpscaleTargetResolution"] = normalize_video_upscale_target_resolution(payload.get("videoUpscaleTargetResolution", payload.get("resolution", "1080p")))
    payload["videoUpscaleNoiseScale"] = normalize_video_upscale_noise_scale(payload.get("videoUpscaleNoiseScale", 0.1))
    payload["videoUpscaleOutputWriteMode"] = normalize_video_upscale_write_mode(payload.get("videoUpscaleOutputWriteMode", "balanced"))
    payload["videoUpscaleOutputFormat"] = normalize_video_upscale_output_format(payload.get("videoUpscaleOutputFormat", "X264 (.mp4)"))
    payload["videoUpscaleOutputQuality"] = normalize_video_upscale_output_quality(payload.get("videoUpscaleOutputQuality", "high"))
    payload["videoUpscaleSeed"] = normalize_optional_int(payload.get("videoUpscaleSeed"))
    apply_project_record_scope(payload)
    payload.update(normalize_asset_metadata(payload, require_filename=False))
    return payload

# ---------------------------------------------------------------------------
# Vocabolario canonico Ã¢â‚¬â€ caricato da talent_vocabulary.json se presente
# Used to constrain Gemini to return normalized values only
# ---------------------------------------------------------------------------
_VOCAB_PATH = os.path.join(BASE_DIR, "talent_vocabulary.json")
if os.path.exists(_VOCAB_PATH):
    with open(_VOCAB_PATH, encoding="utf-8") as _vf:
        TALENT_VOCABULARY = json.load(_vf)
else:
    # Minimal inline fallback (refresh by running normalize_talent_json.py)
    TALENT_VOCABULARY = {
        "gender":     ["female","male","non-binary","androgynous"],
        "ethnicity":  ["african","afro_caribbean","east_asian","south_asian","southeast_asian",
                       "middle_eastern","hispanic_latino","caucasian","mediterranean",
                       "eastern_european","northern_european","mixed","other"],
        "age_group":  ["teen","young_adult","adult","mature","senior"],
        "skin_tone":  ["very_fair","fair","fair_warm","light","light_olive","medium",
                       "medium_warm","medium_olive","tan","brown","deep_brown","deep"],
        "hair_color": ["black","dark_brown","medium_brown","light_brown","auburn","red",
                       "copper","blonde","dark_blonde","platinum_blonde","gray","white",
                       "silver","salt_and_pepper","bald","colored"],
        "hair_style": ["bald","buzz_cut","short_crop","short_pixie","short_bob","short_curly",
                       "short_afro","fade","medium_straight","medium_wavy","medium_curly",
                       "bob","lob","long_straight","long_wavy","long_curly","braids",
                       "cornrows","afro","bun","ponytail","updo","tied_back","fantasy_styled"],
        "eye_color":  ["dark_brown","brown","light_brown","hazel","green","gray","blue",
                       "light_blue","amber","black","other"],
        "body_type":  ["slim","slender","athletic","lean_athletic","fit","curvy",
                       "average","muscular","full","plus_size"],
    }


def _load_vocabulary_file(filename: str) -> dict:
    """Task 11: same loading as talent_vocabulary.json, same place on disk. Absent file -> {}."""
    path = os.path.join(BASE_DIR, filename)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


ENVIRONMENT_VOCABULARY = _load_vocabulary_file("environment_vocabulary.json")
PROP_VOCABULARY = _load_vocabulary_file("prop_vocabulary.json")


def _build_vocab_prompt_block(vocabulary: dict | None = None) -> str:
    """Costruisce il blocco testo del vocabolario da inserire nel prompt Gemini."""
    vocabulary = TALENT_VOCABULARY if vocabulary is None else vocabulary
    if not vocabulary:
        return ""
    lines = ["MANDATORY ALLOWED VALUES Ã¢â‚¬â€ use ONLY these exact strings, no variations:"]
    for field, values in vocabulary.items():
        lines.append(f'  "{field}": {" | ".join(values)}')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Element types (Task 11). Talent is the original; environment and prop reuse its intake.
# A record with no "type" is talent. Folder = the existing ELEMENTS_CATEGORIES folder.
# ---------------------------------------------------------------------------
ELEMENT_TYPES = {
    "talent": {
        "label": "Talent",
        "folder": "Model Managment",
        "fields": ["gender", "ethnicity", "age_group", "skin_tone", "hair_color", "hair_style", "eye_color", "body_type"],
    },
    "environment": {
        "label": "Environment",
        "folder": "Locations",
        "fields": ["location_type", "interior_exterior", "time_of_day", "materials", "light_behavior", "palette", "period", "condition"],
    },
    "prop": {
        "label": "Prop",
        "folder": "Props",
        "fields": ["category", "material", "scale", "period", "condition"],
    },
}
ELEMENT_FOLDER_TYPES = {cfg["folder"]: key for key, cfg in ELEMENT_TYPES.items()}


def normalize_element_type(value) -> str:
    text = str(value or "").strip().lower()
    return text if text in ELEMENT_TYPES else "talent"


def element_vocabulary(element_type: str) -> dict:
    return {"talent": TALENT_VOCABULARY, "environment": ENVIRONMENT_VOCABULARY, "prop": PROP_VOCABULARY}.get(element_type, {})


def build_element_analysis_prompt(element_type: str) -> str:
    """Extraction prompt for environment / prop. Talent keeps its original inline prompt."""
    vocab_block = _build_vocab_prompt_block(element_vocabulary(element_type))
    constraint = (
        "CRITICAL: For every field listed above you MUST use ONLY the exact string values given. "
        "Do NOT invent new values, do NOT use variations, plurals, or spaces instead of underscores.\n\n"
        if vocab_block else
        "Use short lowercase snake_case values for single-choice fields.\n\n"
    )
    if element_type == "environment":
        return (
            "You are a film location scout and production designer. Analyze this photo of a place and extract structured metadata "
            "for a location library.\n"
            "Your task: fill in EVERY field - never leave anything empty.\n\n"
            f"{vocab_block}\n\n"
            "Additional field rules:\n"
            "- name: a short, specific label for this place as a crew would call it (e.g. Sodium Bus Depot, Rooftop Water Tank, Grandmother's Kitchen)\n"
            "- materials: comma-separated list of the dominant surfaces and materials visible (e.g. wet asphalt, corrugated steel, cracked plaster)\n"
            "- description: 2 precise sentences for AI image generation - layout and depth, where the light comes from, textures, what makes the place recognisable\n"
            "- tags: JSON array of 4-6 lowercase, single-word or hyphenated tags useful for searching\n\n"
            f"{constraint}"
            "Return ONLY a valid JSON object - no markdown fences, no extra text, no comments:\n"
            "{\n"
            '  "name": "...",\n'
            '  "location_type": "...",\n'
            '  "interior_exterior": "...",\n'
            '  "time_of_day": "...",\n'
            '  "materials": "...",\n'
            '  "light_behavior": "...",\n'
            '  "palette": "...",\n'
            '  "period": "...",\n'
            '  "condition": "...",\n'
            '  "description": "...",\n'
            '  "tags": ["...", "..."]\n'
            "}"
        )
    return (
        "You are a film props master. Analyze this photo of an object and extract structured metadata for a props library.\n"
        "Your task: fill in EVERY field - never leave anything empty.\n\n"
        f"{vocab_block}\n\n"
        "Additional field rules:\n"
        "- name: a short, specific label for the object as a crew would call it (e.g. Brass Depot Key, Cracked Thermos, 1970s Rotary Phone)\n"
        "- description: 2 precise sentences for AI image generation - shape, proportions, surface finish, wear, distinguishing marks, what must stay consistent between shots\n"
        "- tags: JSON array of 4-6 lowercase, single-word or hyphenated tags useful for searching\n\n"
        f"{constraint}"
        "Return ONLY a valid JSON object - no markdown fences, no extra text, no comments:\n"
        "{\n"
        '  "name": "...",\n'
        '  "category": "...",\n'
        '  "material": "...",\n'
        '  "scale": "...",\n'
        '  "period": "...",\n'
        '  "condition": "...",\n'
        '  "description": "...",\n'
        '  "tags": ["...", "..."]\n'
        "}"
    )


STYLE_SCAN_PROMPT = (
    "You are a cinematographer and colourist. Look at this reference image and write the photographic treatment it "
    "represents, so the same look can be applied to other shots.\n"
    "Cover, in this order: grade and colour treatment; light quality and direction; palette; film stock or sensor "
    "character (grain, halation, dynamic range); overall photographic register.\n"
    "Write 3-5 plain sentences of prose. Describe the look, not the subject. No headings, no lists, no camera brands "
    "unless unmistakable.\n\n"
    "Return ONLY a valid JSON object - no markdown fences, no extra text:\n"
    '{\n  "text": "..."\n}'
)


# Model for talent visual analysis (text output only, not image generation)
# gemini-3-flash-preview = Gemini 3 Flash (preview) - more capable than lite, great for structured JSON
# gemini-3.1-flash-lite-preview = lite version (faster/cheaper but less precise)
TALENT_ANALYSIS_MODEL = "gemini-3-flash-preview"

# ---------------------------------------------------------------------------
# Vision / analysis pricing (per token, not per image)
# Fonte: Google AI pricing Marzo 2026
# ---------------------------------------------------------------------------
VISION_MODELS_INFO = {
    "gemini-3-flash-preview": {
        "label":         "Gemini 3 Flash",
        "badge":         "Vis",
        "input_per_1m":  0.15,    # USD per 1M input tokens
        "output_per_1m": 0.60,    # USD per 1M output tokens
        "free_tier":     "Preview Ã¢â‚¬â€ free *",
        "note":          "Recommended for talent analysis Ã¢â‚¬â€ best JSON quality"
    },
    "gemini-3.1-flash-lite-preview": {
        "label":         "Gemini 3.1 Flash-Lite",
        "badge":         "Vis",
        "input_per_1m":  0.075,
        "output_per_1m": 0.30,
        "free_tier":     "Preview Ã¢â‚¬â€ free *",
        "note":          "Lite version Ã¢â‚¬â€ faster but less accurate on JSON"
    },
    "gemini-2.0-flash-lite": {
        "label":         "Gemini 2.0 Flash-Lite",
        "badge":         "Vis",
        "input_per_1m":  0.075,
        "output_per_1m": 0.30,
        "free_tier":     "~1500 req/day",
        "note":          "Stable, cost-effective"
    },
}


# ---------------------------------------------------------------------------
# Helpers Ã¢â‚¬â€ Talent individual JSON
# Talent JSON files for "Model Managment" live in the json/ subfolder
# ---------------------------------------------------------------------------
TALENT_JSON_SUBDIR = "json"   # subfolder inside Model Managment/


# ---------------------------------------------------------------------------
# Task Workbench - task templates, prompt automation, routing, reporting
# ---------------------------------------------------------------------------
DEFAULT_TASK_TEMPLATES = [
    {
        "slug": "campaign_launch",
        "name": "Launch Campaign",
        "description": "Multi-channel campaign visuals with clear brand direction and commercial polish.",
        "default_provider": "gemini",
        "default_workflow": "campaign-launch-v1",
        "default_model": "gemini-3.1-flash-image-preview",
        "default_aspect_ratio": "4:5",
        "default_image_size": "1K",
        "default_temperature": 0.95,
        "prompt_scaffold": "Build a flagship campaign image that feels art-directed, premium, and ready for client review.",
    },
    {
        "slug": "ad_variation_batch",
        "name": "Ad Variation Batch",
        "description": "High-volume paid social concepts optimized for fast iteration and multiple hooks.",
        "default_provider": "comfyui",
        "default_workflow": "ad-variation-batch-v1",
        "default_model": "gemini-3.1-flash-image-preview",
        "default_aspect_ratio": "4:5",
        "default_image_size": "1K",
        "default_temperature": 0.9,
        "prompt_scaffold": "Create a conversion-minded ad visual with a strong focal point, clean hierarchy, and room for copy overlays.",
    },
    {
        "slug": "editorial_lookbook",
        "name": "Editorial Lookbook",
        "description": "Fashion-forward imagery with strong styling, mood, and visual consistency.",
        "default_provider": "gemini",
        "default_workflow": "editorial-lookbook-v1",
        "default_model": "gemini-3-pro-image-preview",
        "default_aspect_ratio": "4:5",
        "default_image_size": "2K",
        "default_temperature": 1.05,
        "prompt_scaffold": "Compose an editorial image with intentional styling, cinematic lighting, and magazine-grade composition.",
    },
    {
        "slug": "product_hero",
        "name": "Product Hero",
        "description": "Clean hero visuals for product launches, e-commerce, and ad landing pages.",
        "default_provider": "comfyui",
        "default_workflow": "product-hero-v1",
        "default_model": "gemini-3-pro-image-preview",
        "default_aspect_ratio": "1:1",
        "default_image_size": "2K",
        "default_temperature": 0.8,
        "prompt_scaffold": "Highlight the product with premium materials, controlled reflections, and strong commercial clarity.",
    },
    {
        "slug": "location_concept",
        "name": "Location Concepting",
        "description": "Fast visual exploration for sets, environments, and brand worlds.",
        "default_provider": "gemini",
        "default_workflow": "location-concept-v1",
        "default_model": "gemini-3.1-flash-image-preview",
        "default_aspect_ratio": "16:9",
        "default_image_size": "1K",
        "default_temperature": 1.1,
        "prompt_scaffold": "Explore a location concept with a clear point of view, strong atmosphere, and believable production detail.",
    },
]

# Film templates (Director Studio). Same shape as DEFAULT_TASK_TEMPLATES plus project_type.
DEFAULT_FILM_TASK_TEMPLATES = [
    {
        "slug": "film_story",
        "name": "Story",
        "description": "Turn a premise and register into structure and beats.",
        "default_provider": "gemini",
        "default_workflow": "film-story-v1",
        "default_model": "gemini-3.1-flash-image-preview",
        "default_aspect_ratio": "16:9",
        "default_image_size": "1K",
        "default_temperature": 1.0,
        "prompt_scaffold": "Work as a story editor. From the premise and register, lay out the structure and the key beats, then render one frame that captures the story's defining image.",
        "project_type": "film",
    },
    {
        "slug": "film_scene_breakdown",
        "name": "Scene Breakdown",
        "description": "Break one scene into the shots that make it.",
        "default_provider": "gemini",
        "default_workflow": "film-scene-breakdown-v1",
        "default_model": "gemini-3.1-flash-image-preview",
        "default_aspect_ratio": "16:9",
        "default_image_size": "1K",
        "default_temperature": 0.95,
        "prompt_scaffold": "Work as a first AD and DP. Break the scene into its shots in order, naming size, angle, and purpose for each, then render the establishing frame.",
        "project_type": "film",
    },
    {
        "slug": "film_casting",
        "name": "Casting",
        "description": "From a role described, propose talent candidates.",
        "default_provider": "gemini",
        "default_workflow": "film-casting-v1",
        "default_model": "gemini-3-pro-image-preview",
        "default_aspect_ratio": "4:5",
        "default_image_size": "2K",
        "default_temperature": 1.0,
        "prompt_scaffold": "Work as a casting director. From the role as described, render a candidate: a clean, natural-light headshot with an honest expression and no styling beyond what the role implies.",
        "project_type": "film",
    },
    {
        "slug": "film_wardrobe",
        "name": "Wardrobe",
        "description": "For a character in a scene, propose look candidates.",
        "default_provider": "gemini",
        "default_workflow": "film-wardrobe-v1",
        "default_model": "gemini-3-pro-image-preview",
        "default_aspect_ratio": "4:5",
        "default_image_size": "2K",
        "default_temperature": 1.0,
        "prompt_scaffold": "Work as a costume designer. For the character in this scene, render a full-length look candidate that reads at a glance and belongs to the film's world and period.",
        "project_type": "film",
    },
    {
        "slug": "film_location",
        "name": "Location",
        "description": "From a place described, propose environment candidates.",
        "default_provider": "gemini",
        "default_workflow": "film-location-v1",
        "default_model": "gemini-3-pro-image-preview",
        "default_aspect_ratio": "16:9",
        "default_image_size": "2K",
        "default_temperature": 1.05,
        "prompt_scaffold": "Work as a location scout. From the place as described, render an environment candidate with believable production detail, practical light sources, and room for blocking.",
        "project_type": "film",
    },
    {
        "slug": "film_shot_direction",
        "name": "Shot Direction",
        "description": "For one shot, define camera and movement.",
        "default_provider": "gemini",
        "default_workflow": "film-shot-direction-v1",
        "default_model": "gemini-3.1-flash-image-preview",
        "default_aspect_ratio": "16:9",
        "default_image_size": "1K",
        "default_temperature": 0.9,
        "prompt_scaffold": "Work as a director and DP. For this shot, specify lens, camera height, framing, and movement, then render the frame at the moment the movement lands.",
        "project_type": "film",
    },
]

WORKBENCH_CHANNEL_LABELS = {
    "instagram_feed": "Instagram Feed",
    "meta_ads": "Meta Ads",
    "stories_reels": "Stories / Reels",
    "website": "Website / Landing Page",
    "email": "Email / CRM",
    "print": "Print / OOH",
}


def get_db_connection():
    conn = sqlite3.connect(STUDIO_DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_task_runs_columns(conn):
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(task_runs)").fetchall()}
    desired = {
        "status":          "TEXT NOT NULL DEFAULT 'planned'",
        "run_count":       "INTEGER NOT NULL DEFAULT 0",
        "actual_images":   "INTEGER NOT NULL DEFAULT 0",
        "actual_cost_usd": "REAL NOT NULL DEFAULT 0",
        "actual_model":    "TEXT",
        "actual_provider": "TEXT",
        "last_run_at":     "TEXT",
        "last_error":      "TEXT",
        "plan_json":       "TEXT",
    }
    for name, ddl in desired.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE task_runs ADD COLUMN {name} {ddl}")


def ensure_projects_columns(conn):
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
    desired = {
        # Task 14: the film's constant, stored as written (markdown or plain text)
        "playbook": "TEXT NOT NULL DEFAULT ''",
        # Task 17: the film's short code, the first segment of every scene and shot ID
        "film_code": "TEXT NOT NULL DEFAULT ''",
    }
    for name, ddl in desired.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE projects ADD COLUMN {name} {ddl}")


def ensure_scenes_columns(conn):
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(scenes)").fetchall()}
    desired = {
        # Task 17: the scene number behind {FILM}-S{NN}; NULL on scenes from the old convention
        "number": "INTEGER",
    }
    for name, ddl in desired.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE scenes ADD COLUMN {name} {ddl}")


def ensure_shots_columns(conn):
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(shots)").fetchall()}
    desired = {
        # Task 05: what the shot sends
        "prompt":           "TEXT NOT NULL DEFAULT ''",
        "negative_prompt":  "TEXT NOT NULL DEFAULT ''",
        "elements":         "TEXT NOT NULL DEFAULT '[]'",
        "reference_assets": "TEXT NOT NULL DEFAULT '[]'",
        # Task 10: attached style record and whether it applies
        "style_id":         "INTEGER",
        "style_enabled":    "INTEGER NOT NULL DEFAULT 1",
        # Task 12: the scene a shot belongs to (the free-text `scene` stays as it is)
        "scene_id":         "INTEGER",
        # Task 15: the frame prompt; `prompt` stays the motion prompt the render sends
        "scene_prompt":     "TEXT NOT NULL DEFAULT ''",
        # Task 17: the parts of the assembled ID. slug holds the assembled ID and never changes
        "setup":            "INTEGER",
        "exposure_class":   "TEXT NOT NULL DEFAULT ''",
        "name_slug":        "TEXT NOT NULL DEFAULT ''",
    }
    for name, ddl in desired.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE shots ADD COLUMN {name} {ddl}")


def ensure_takes_columns(conn):
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(takes)").fetchall()}
    desired = {
        # Task 16: the take's original numbered path; asset_path carries the _approved name while approved
        "asset_path_numbered": "TEXT NOT NULL DEFAULT ''",
    }
    for name, ddl in desired.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE takes ADD COLUMN {name} {ddl}")


def ensure_task_templates_columns(conn):
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(task_templates)").fetchall()}
    desired = {
        "project_type": "TEXT NOT NULL DEFAULT 'campaign'",
    }
    for name, ddl in desired.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE task_templates ADD COLUMN {name} {ddl}")


def init_studio_db():
    conn = get_db_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS task_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            default_provider TEXT NOT NULL,
            default_workflow TEXT NOT NULL,
            default_model TEXT NOT NULL,
            default_aspect_ratio TEXT NOT NULL,
            default_image_size TEXT NOT NULL,
            default_temperature REAL NOT NULL,
            prompt_scaffold TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS task_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_uuid TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL,
            client_name TEXT,
            project_name TEXT,
            task_slug TEXT NOT NULL,
            task_name TEXT NOT NULL,
            objective TEXT NOT NULL,
            channels_json TEXT NOT NULL,
            vibe TEXT,
            subject_summary TEXT,
            constraints_summary TEXT,
            automation_level TEXT NOT NULL,
            recommended_provider TEXT NOT NULL,
            execution_provider TEXT NOT NULL,
            recommended_model TEXT,
            recommended_workflow TEXT NOT NULL,
            aspect_ratio TEXT,
            image_size TEXT,
            prompt_text TEXT NOT NULL,
            estimated_outputs INTEGER NOT NULL DEFAULT 0,
            estimated_cost_low REAL NOT NULL DEFAULT 0,
            estimated_cost_high REAL NOT NULL DEFAULT 0
        )
        """
    )
    ensure_task_runs_columns(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            client TEXT NOT NULL DEFAULT '',
            type TEXT NOT NULL DEFAULT 'campaign',
            settings TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            archived INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            slug TEXT NOT NULL,
            scene TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            duration_seconds REAL,
            beat_marker TEXT NOT NULL DEFAULT '',
            action_text TEXT NOT NULL DEFAULT '',
            dialogue TEXT NOT NULL DEFAULT '',
            audio_cue TEXT NOT NULL DEFAULT '',
            shot_size TEXT NOT NULL DEFAULT '',
            angle TEXT NOT NULL DEFAULT '',
            movement TEXT NOT NULL DEFAULT '[]',
            lens TEXT NOT NULL DEFAULT '',
            aperture TEXT NOT NULL DEFAULT '',
            speed_ramp TEXT NOT NULL DEFAULT '',
            first_frame TEXT NOT NULL DEFAULT '',
            last_frame TEXT NOT NULL DEFAULT '',
            chain_from_previous INTEGER NOT NULL DEFAULT 0,
            engine TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'empty',
            locked_fields TEXT NOT NULL DEFAULT '[]',
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE (project_id, slug)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS takes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shot_id INTEGER NOT NULL,
            asset_path TEXT NOT NULL DEFAULT '',
            approved INTEGER NOT NULL DEFAULT 0,
            cost REAL NOT NULL DEFAULT 0,
            engine TEXT NOT NULL DEFAULT '',
            prompt_sent TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'queued',
            error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            job_id TEXT NOT NULL DEFAULT '',
            poster_path TEXT NOT NULL DEFAULT '',
            completed_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS styles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            text TEXT NOT NULL DEFAULT '',
            images TEXT NOT NULL DEFAULT '[]',
            project_id INTEGER,
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scenes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            slug TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            brief TEXT NOT NULL DEFAULT '',
            script TEXT NOT NULL DEFAULT '',
            environment_id TEXT,
            note TEXT NOT NULL DEFAULT '',
            locked_fields TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            UNIQUE (project_id, slug)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            meta TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scene_cast (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scene_id INTEGER NOT NULL,
            element_id TEXT NOT NULL,
            character_name TEXT NOT NULL DEFAULT '',
            look_name TEXT NOT NULL DEFAULT '',
            handle TEXT NOT NULL DEFAULT '',
            images TEXT NOT NULL DEFAULT '[]',
            note TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            locked_fields TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scene_slug_counters (
            project_id INTEGER PRIMARY KEY,
            next_number INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shot_slug_counters (
            project_id INTEGER PRIMARY KEY,
            next_number INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS setup_counters (
            scene_id INTEGER PRIMARY KEY,
            next_number INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    ensure_projects_columns(conn)
    ensure_scenes_columns(conn)
    ensure_shots_columns(conn)
    ensure_takes_columns(conn)
    ensure_task_templates_columns(conn)
    now_ts = utc_now_iso()
    for template in DEFAULT_TASK_TEMPLATES + DEFAULT_FILM_TASK_TEMPLATES:
        conn.execute(
            """
            INSERT OR IGNORE INTO task_templates (
                slug, name, description, default_provider, default_workflow,
                default_model, default_aspect_ratio, default_image_size,
                default_temperature, prompt_scaffold, created_at, updated_at,
                project_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                template["slug"],
                template["name"],
                template["description"],
                template["default_provider"],
                template["default_workflow"],
                template["default_model"],
                template["default_aspect_ratio"],
                template["default_image_size"],
                template["default_temperature"],
                template["prompt_scaffold"],
                now_ts,
                now_ts,
                template.get("project_type", "campaign"),
            ),
        )
    conn.commit()
    conn.close()


def fetch_task_templates(project_type: str = ""):
    init_studio_db()
    conn = get_db_connection()
    query = (
        "SELECT slug, name, description, default_provider, default_workflow, "
        "default_model, default_aspect_ratio, default_image_size, "
        "default_temperature, prompt_scaffold, project_type "
        "FROM task_templates"
    )
    params = ()
    if project_type:
        query += " WHERE project_type = ?"
        params = (project_type,)
    rows = conn.execute(query + " ORDER BY id ASC", params).fetchall()
    conn.close()
    return [dict(row) for row in rows]


PROJECT_TYPES = ("film", "campaign")
FILM_PROJECT_SETTING_KEYS = ("format", "runtime", "aspect_ratio", "frame_rate", "register", "resolve_folder")


def normalize_project_type(value) -> str:
    text = str(value or "").strip().lower()
    return text if text in PROJECT_TYPES else ""


def normalize_project_settings(project_type: str, raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    if project_type == "film":
        settings = {}
        for key in FILM_PROJECT_SETTING_KEYS:
            value = raw.get(key)
            if value is None or str(value).strip() == "":
                settings[key] = None
            else:
                settings[key] = str(value).strip()
        return settings
    # campaign: no per-project settings defined yet
    return {}


def project_row_to_dict(row) -> dict:
    item = dict(row)
    try:
        settings = json.loads(item.get("settings") or "{}")
    except json.JSONDecodeError:
        settings = {}
    item["settings"] = settings if isinstance(settings, dict) else {}
    item["archived"] = bool(item.get("archived"))
    item["playbook"] = str(item.get("playbook") or "")
    item["film_code"] = str(item.get("film_code") or "")
    item["assetClient"] = normalize_asset_scope_text(item.get("client", "")) or ASSET_UNCATEGORIZED_VALUE
    item["assetProject"] = normalize_asset_scope_text(item.get("name", "")) or ASSET_UNCATEGORIZED_VALUE
    return item


def fetch_projects(include_archived: bool = False) -> list[dict]:
    init_studio_db()
    conn = get_db_connection()
    if include_archived:
        rows = conn.execute("SELECT * FROM projects ORDER BY archived ASC, name COLLATE NOCASE ASC, id ASC").fetchall()
    else:
        rows = conn.execute("SELECT * FROM projects WHERE archived = 0 ORDER BY name COLLATE NOCASE ASC, id ASC").fetchall()
    conn.close()
    return [project_row_to_dict(row) for row in rows]


def get_project(project_id) -> dict | None:
    try:
        project_id = int(project_id)
    except (TypeError, ValueError):
        return None
    init_studio_db()
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM projects WHERE id = ? LIMIT 1", (project_id,)).fetchone()
    conn.close()
    return project_row_to_dict(row) if row else None


# ---- Task 17: the ID spine. {FILM}-S{scene}-{setup}-{CLASS}-{slug}, assembled once, never renamed. ----
EXPOSURE_CLASSES = ("FLASH", "POP", "DETAIL")
DEFAULT_EXPOSURE_CLASS = "POP"
_FILM_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,23}$")
NO_FILM_CODE_MESSAGE = "This film project has no film code. Set one in the project settings (e.g. NEX01) before creating scenes or shots."


def normalize_film_code(value) -> str:
    """Short code, upper-cased as the convention writes it: letters, digits, hyphens. No spaces, nothing a filename rejects."""
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if not _FILM_CODE_RE.match(text):
        raise ValueError("Film code: letters, digits and hyphens only, no spaces (e.g. NEX01, RDOA-E01).")
    return text


def normalize_exposure_class(value, *, required: bool = False) -> str:
    text = str(value or "").strip().upper()
    if not text:
        if required:
            return DEFAULT_EXPOSURE_CLASS
        return ""
    if text not in EXPOSURE_CLASSES:
        raise ValueError("Exposure class must be one of " + ", ".join(EXPOSURE_CLASSES))
    return text


def normalize_name_slug(value) -> str:
    """Two or three human-readable words, hyphenated, no spaces; kept in the case typed."""
    words = [re.sub(r"[^A-Za-z0-9]+", "", w) for w in re.split(r"[\s_\-]+", str(value or "").strip())]
    words = [w for w in words if w]
    if not words:
        return ""
    if len(words) > 3:
        raise ValueError("Shot name: two or three words at most (it becomes part of the ID).")
    return "-".join(words)


def is_new_convention_slug(slug: str) -> bool:
    return bool(re.match(r"^[A-Z0-9][A-Z0-9-]*-S\d{2}(-\d{2}-(FLASH|POP|DETAIL)-[A-Za-z0-9-]+)?$", str(slug or "")))


def create_project(body: dict) -> dict:
    name = sanitize_asset_meta_text(body.get("name", ""))
    if not name or name.lower() == ASSET_UNCATEGORIZED_VALUE:
        raise ValueError("Project name is required")
    client = sanitize_asset_meta_text(body.get("client", ""))
    project_type = normalize_project_type(body.get("type"))
    if not project_type:
        raise ValueError("Project type must be 'film' or 'campaign'")
    settings = normalize_project_settings(project_type, body.get("settings"))
    film_code = normalize_film_code(body.get("film_code")) if project_type == "film" else ""
    if project_type == "film" and not film_code:
        raise ValueError("Film projects need a film code (e.g. NEX01). It is the first segment of every scene and shot ID.")
    init_studio_db()
    conn = get_db_connection()
    cursor = conn.execute(
        "INSERT INTO projects (name, client, type, settings, created_at, archived, playbook, film_code) VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
        (name, client, project_type, json.dumps(settings, ensure_ascii=False), utc_now_iso(), str(body.get("playbook") or ""), film_code),
    )
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return get_project(new_id)


def update_project(project_id, body: dict) -> dict:
    existing = get_project(project_id)
    if not existing:
        raise LookupError("Project not found")
    updates = {}
    if "name" in body:
        name = sanitize_asset_meta_text(body.get("name", ""))
        if not name or name.lower() == ASSET_UNCATEGORIZED_VALUE:
            raise ValueError("Project name is required")
        updates["name"] = name
    if "client" in body:
        updates["client"] = sanitize_asset_meta_text(body.get("client", ""))
    project_type = existing["type"]
    if "type" in body:
        project_type = normalize_project_type(body.get("type"))
        if not project_type:
            raise ValueError("Project type must be 'film' or 'campaign'")
        updates["type"] = project_type
    if "settings" in body or "type" in body:
        merged_settings = dict(existing.get("settings") or {})
        if isinstance(body.get("settings"), dict):
            merged_settings.update(body["settings"])
        updates["settings"] = json.dumps(normalize_project_settings(project_type, merged_settings), ensure_ascii=False)
    if "archived" in body:
        updates["archived"] = 1 if body.get("archived") else 0
    if "playbook" in body:
        updates["playbook"] = str(body.get("playbook") or "")   # as written: no strip, no parse
    if "film_code" in body:
        film_code = normalize_film_code(body.get("film_code"))
        if film_code != (existing.get("film_code") or ""):
            conn = get_db_connection()
            in_use = conn.execute("SELECT COUNT(*) AS n FROM scenes WHERE project_id = ? AND number IS NOT NULL", (existing["id"],)).fetchone()["n"]
            conn.close()
            if in_use:
                raise ValueError("The film code is fixed once scenes carry it: IDs are never renamed.")
            if project_type == "film" and not film_code:
                raise ValueError("Film projects need a film code (e.g. NEX01).")
            updates["film_code"] = film_code
    if updates:
        conn = get_db_connection()
        assignments = ", ".join(f"{column} = ?" for column in updates)
        conn.execute(f"UPDATE projects SET {assignments} WHERE id = ?", (*updates.values(), existing["id"]))
        conn.commit()
        conn.close()
    return get_project(existing["id"])


def apply_project_record_scope(payload: dict) -> dict:
    """If the request carries a project id, take Client/Project from the record.
    Without an id the free-text scope is used exactly as before."""
    raw_id = payload.get("assetProjectId")
    if raw_id is None or str(raw_id).strip() == "":
        payload.pop("assetProjectId", None)
        return payload
    record = get_project(raw_id)
    if not record:
        payload.pop("assetProjectId", None)
        return payload
    payload["assetProjectId"] = record["id"]
    payload["assetClient"] = record["assetClient"]
    payload["assetProject"] = record["assetProject"]
    return payload


# ---------------------------------------------------------------------------
# Shots (Director Studio, Task 04). Identity lives in slug, order in sort_order.
# ---------------------------------------------------------------------------
SHOT_STATUSES = ("empty", "queued", "rendering", "done", "rejected", "failed")
SHOT_TEXT_FIELDS = (
    "scene", "beat_marker", "action_text", "dialogue", "audio_cue",
    "shot_size", "angle", "lens", "aperture", "speed_ramp",
    "first_frame", "last_frame", "engine", "note",
    "prompt", "negative_prompt", "scene_prompt",
)
# Ordered JSON arrays of ids/paths. Order is the reference order providers see.
SHOT_LIST_FIELDS = ("elements", "reference_assets")
# Task 08: what each attached reference is for. Starting vocabulary; the user owns this list.
REFERENCE_ROLES = ("unassigned", "edit_target", "character", "garment", "environment", "prop", "style")
SHOT_EDITABLE_FIELDS = SHOT_TEXT_FIELDS + SHOT_LIST_FIELDS + ("duration_seconds", "movement", "chain_from_previous", "status", "locked_fields", "style_id", "style_enabled", "scene_id")
SHOT_SORT_STEP = 10


def shot_row_to_dict(row) -> dict:
    item = dict(row)
    for key, fallback in (("movement", []), ("locked_fields", []), ("elements", []), ("reference_assets", [])):
        try:
            value = json.loads(item.get(key) or "[]")
        except json.JSONDecodeError:
            value = fallback
        item[key] = value if isinstance(value, list) else fallback
    # Old rows hold plain strings; read them as unassigned. Nothing is written back here.
    for key in SHOT_LIST_FIELDS:
        item[key] = [_reference_entry(value) for value in item[key] if _reference_entry(value)]
    item["chain_from_previous"] = bool(item.get("chain_from_previous"))
    item["style_enabled"] = bool(item.get("style_enabled", 1)) if item.get("style_enabled") is not None else True
    return item


def attach_styles(conn, shots: list[dict]) -> list[dict]:
    """Adds `style` = the attached style record (or None) to each shot. Read-only helper."""
    ids = sorted({int(shot["style_id"]) for shot in shots if shot.get("style_id")})
    lookup = {}
    if ids:
        marks = ",".join("?" for _ in ids)
        for row in conn.execute(f"SELECT * FROM styles WHERE id IN ({marks})", ids).fetchall():
            style = style_row_to_dict(row)
            lookup[style["id"]] = style
    for shot in shots:
        shot["style"] = lookup.get(int(shot["style_id"])) if shot.get("style_id") else None
    return shots


def attach_take_summaries(conn, shots: list[dict]) -> list[dict]:
    """Adds `takes` = {approved, latest_done, rendering, count} per shot. Read-only helper."""
    if not shots:
        return shots
    ids = [shot["id"] for shot in shots]
    marks = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM takes WHERE shot_id IN ({marks}) ORDER BY id DESC", ids
    ).fetchall()
    by_shot: dict[int, dict] = {shot["id"]: {"approved": None, "latest_done": None, "rendering": False, "count": 0} for shot in shots}
    for row in rows:
        take = take_row_to_dict(row)
        summary = by_shot.get(take["shot_id"])
        if summary is None:
            continue
        summary["count"] += 1
        if take["approved"] and summary["approved"] is None:
            summary["approved"] = take
        if take["status"] == "done" and summary["latest_done"] is None:
            summary["latest_done"] = take
        if take["status"] in ("queued", "rendering"):
            summary["rendering"] = True
    for shot in shots:
        shot["takes"] = by_shot.get(shot["id"])
    return shots


def fetch_shots(project_id) -> list[dict]:
    try:
        project_id = int(project_id)
    except (TypeError, ValueError):
        return []
    init_studio_db()
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT * FROM shots WHERE project_id = ? ORDER BY sort_order ASC, id ASC", (project_id,)
    ).fetchall()
    shots = attach_styles(conn, attach_take_summaries(conn, [shot_row_to_dict(row) for row in rows]))
    conn.close()
    return shots


def get_shot(shot_id) -> dict | None:
    try:
        shot_id = int(shot_id)
    except (TypeError, ValueError):
        return None
    init_studio_db()
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM shots WHERE id = ? LIMIT 1", (shot_id,)).fetchone()
    shots = attach_styles(conn, attach_take_summaries(conn, [shot_row_to_dict(row)])) if row else []
    conn.close()
    return shots[0] if shots else None


def set_shot_status(shot_id, status: str) -> None:
    if status not in SHOT_STATUSES:
        return
    conn = get_db_connection()
    conn.execute("UPDATE shots SET status = ? WHERE id = ?", (status, int(shot_id)))
    conn.commit()
    conn.close()


def next_shot_slug(conn, project: dict) -> str:
    """<ProjectStem>_SH### from a per-project counter that only ever increments."""
    row = conn.execute("SELECT next_number FROM shot_slug_counters WHERE project_id = ?", (project["id"],)).fetchone()
    number = int(row["next_number"]) if row else 1
    conn.execute(
        "INSERT INTO shot_slug_counters (project_id, next_number) VALUES (?, ?) "
        "ON CONFLICT(project_id) DO UPDATE SET next_number = excluded.next_number",
        (project["id"], number + 1),
    )
    stem = sanitize_asset_filename_stem(project.get("name", ""), fallback="project")
    return f"{stem}_SH{number:03d}"


def next_setup_number(conn, scene_id: int) -> int:
    """Task 17: per-scene setup counter. Only increments; a deleted shot's number is never reused."""
    row = conn.execute("SELECT next_number FROM setup_counters WHERE scene_id = ?", (int(scene_id),)).fetchone()
    number = int(row["next_number"]) if row else 1
    conn.execute(
        "INSERT INTO setup_counters (scene_id, next_number) VALUES (?, ?) "
        "ON CONFLICT(scene_id) DO UPDATE SET next_number = excluded.next_number",
        (int(scene_id), number + 1),
    )
    return number


def assemble_shot_id(film_code: str, scene_number: int, setup: int, exposure_class: str, name_slug: str) -> str:
    return f"{film_code}-S{int(scene_number):02d}-{int(setup):02d}-{exposure_class}-{name_slug}"


def _normalize_shot_movement(raw) -> str:
    if isinstance(raw, str):
        values = [part for part in raw.split(",")]
    elif isinstance(raw, list):
        values = raw
    else:
        values = []
    cleaned = []
    for value in values:
        text = sanitize_asset_meta_text(value)
        if text and text not in cleaned:
            cleaned.append(text)
    return json.dumps(cleaned[:3], ensure_ascii=False)


def _reference_entry(value) -> dict | None:
    """One attachment as {ref, role}. Accepts the old plain-string shape (role unassigned)."""
    if isinstance(value, dict):
        ref = str(value.get("ref") or value.get("id") or value.get("path") or value.get("url") or "").strip()
        role = str(value.get("role") or "unassigned").strip().lower()
    else:
        ref = str(value or "").strip()
        role = "unassigned"
    if not ref:
        return None
    if role not in REFERENCE_ROLES:
        role = "unassigned"
    return {"ref": ref, "role": role}


def _normalize_shot_id_list(raw) -> str:
    values = raw if isinstance(raw, list) else []
    cleaned = []
    seen = set()
    for value in values:
        entry = _reference_entry(value)
        if entry and entry["ref"] not in seen:
            seen.add(entry["ref"])
            cleaned.append(entry)
    return json.dumps(cleaned, ensure_ascii=False)


def _enforce_single_edit_target(existing: dict, updates: dict) -> None:
    """At most one edit_target per shot across both arrays. The list being written wins:
    its last edit_target is kept; every other edit_target (same list or the other one) is
    cleared to unassigned. Order is never touched."""
    written = [key for key in SHOT_LIST_FIELDS if key in updates]
    if not written:
        return
    lists = {}
    for key in SHOT_LIST_FIELDS:
        raw = updates[key] if key in updates else json.dumps(existing.get(key) or [], ensure_ascii=False)
        try:
            lists[key] = json.loads(raw)
        except json.JSONDecodeError:
            lists[key] = []
    keeper = None
    for key in written:
        for index, entry in enumerate(lists[key]):
            if entry.get("role") == "edit_target":
                keeper = (key, index)
    if keeper is None:
        return
    changed = set()
    for key in SHOT_LIST_FIELDS:
        for index, entry in enumerate(lists[key]):
            if entry.get("role") == "edit_target" and (key, index) != keeper:
                entry["role"] = "unassigned"
                changed.add(key)
    for key in changed | set(written):
        updates[key] = json.dumps(lists[key], ensure_ascii=False)


def _normalize_shot_locked_fields(raw) -> str:
    values = raw if isinstance(raw, list) else []
    cleaned = []
    for value in values:
        text = str(value or "").strip()
        if text in SHOT_EDITABLE_FIELDS and text not in ("locked_fields",) and text not in cleaned:
            cleaned.append(text)
    return json.dumps(cleaned)


def _shot_updates_from_body(body: dict) -> dict:
    updates = {}
    for key in SHOT_TEXT_FIELDS:
        if key in body:
            updates[key] = str(body.get(key) or "").strip()
    if "duration_seconds" in body:
        raw = body.get("duration_seconds")
        if raw is None or str(raw).strip() == "":
            updates["duration_seconds"] = None
        else:
            try:
                updates["duration_seconds"] = float(raw)
            except (TypeError, ValueError):
                raise ValueError("duration_seconds must be a number")
    if "movement" in body:
        updates["movement"] = _normalize_shot_movement(body.get("movement"))
    for key in SHOT_LIST_FIELDS:
        if key in body:
            updates[key] = _normalize_shot_id_list(body.get(key))
    if "chain_from_previous" in body:
        updates["chain_from_previous"] = 1 if body.get("chain_from_previous") else 0
    if "status" in body:
        status = str(body.get("status") or "").strip().lower()
        if status not in SHOT_STATUSES:
            raise ValueError("status must be one of " + ", ".join(SHOT_STATUSES))
        updates["status"] = status
    if "locked_fields" in body:
        updates["locked_fields"] = _normalize_shot_locked_fields(body.get("locked_fields"))
    if "style_id" in body:
        raw_style = body.get("style_id")
        if raw_style in (None, "", 0, "0"):
            updates["style_id"] = None
        else:
            style = get_style(raw_style)
            if not style:
                raise ValueError("Style not found")
            updates["style_id"] = style["id"]
    if "style_enabled" in body:
        updates["style_enabled"] = 1 if body.get("style_enabled") else 0
    if "scene_id" in body:
        raw_scene = body.get("scene_id")
        if raw_scene in (None, "", 0, "0"):
            updates["scene_id"] = None
        else:
            scene = get_scene(raw_scene)
            if not scene:
                raise ValueError("Scene not found")
            updates["scene_id"] = scene["id"]
    # Task 17: editable labels; the stored slug is never reassembled from them
    if "exposure_class" in body:
        updates["exposure_class"] = normalize_exposure_class(body.get("exposure_class"))
    if "name_slug" in body:
        updates["name_slug"] = normalize_name_slug(body.get("name_slug"))
    return updates


def _compact_shot_sort_orders(conn, project_id: int) -> None:
    rows = conn.execute("SELECT id FROM shots WHERE project_id = ? ORDER BY sort_order ASC, id ASC", (project_id,)).fetchall()
    for index, row in enumerate(rows):
        conn.execute("UPDATE shots SET sort_order = ? WHERE id = ?", ((index + 1) * SHOT_SORT_STEP, row["id"]))


def create_shot(body: dict) -> dict:
    project = get_project(body.get("project_id"))
    if not project:
        raise LookupError("Project not found")
    if project.get("type") != "film":
        raise ValueError("Shots belong to film projects")
    updates = _shot_updates_from_body(body)
    _validate_cast_refs(updates.get("scene_id"), updates)
    # Task 17: the ID needs the film code and the scene number, so both are required at creation
    if not project.get("film_code"):
        raise ValueError(NO_FILM_CODE_MESSAGE)
    scene = get_scene(updates.get("scene_id")) if updates.get("scene_id") else None
    if not scene:
        raise ValueError("A shot needs a scene: its ID carries the scene number. Pick a scene first.")
    if scene.get("number") is None:
        raise ValueError(f"{scene['slug']} is on the old naming and has no scene number. Create a new scene for new shots.")
    exposure_class = normalize_exposure_class(body.get("exposure_class"), required=True)
    name_slug = normalize_name_slug(body.get("name_slug", body.get("name")))
    if not name_slug:
        raise ValueError("Give the shot a short name (two or three words): it is part of the ID.")
    updates["exposure_class"] = exposure_class
    updates["name_slug"] = name_slug
    init_studio_db()
    conn = get_db_connection()
    after_id = body.get("after_id")
    if after_id not in (None, ""):
        # Insert between: place directly after the given row, in the gap before its successor.
        prev_row = conn.execute("SELECT id, sort_order, chain_from_previous FROM shots WHERE id = ? AND project_id = ?", (int(after_id), project["id"])).fetchone()
        if not prev_row:
            conn.close()
            raise LookupError("Row to insert after was not found")
        next_row = conn.execute(
            "SELECT id, sort_order FROM shots WHERE project_id = ? AND (sort_order > ? OR (sort_order = ? AND id > ?)) ORDER BY sort_order ASC, id ASC LIMIT 1",
            (project["id"], prev_row["sort_order"], prev_row["sort_order"], prev_row["id"]),
        ).fetchone()
        if next_row is None:
            sort_order = int(prev_row["sort_order"]) + SHOT_SORT_STEP
        elif int(next_row["sort_order"]) - int(prev_row["sort_order"]) >= 2:
            sort_order = (int(prev_row["sort_order"]) + int(next_row["sort_order"])) // 2
        else:
            _compact_shot_sort_orders(conn, project["id"])
            prev_sort = int(conn.execute("SELECT sort_order FROM shots WHERE id = ?", (prev_row["id"],)).fetchone()["sort_order"])
            sort_order = prev_sort + SHOT_SORT_STEP // 2
        if "chain_from_previous" not in updates:
            updates["chain_from_previous"] = int(prev_row["chain_from_previous"] or 0)
    else:
        last = conn.execute("SELECT MAX(sort_order) AS m FROM shots WHERE project_id = ?", (project["id"],)).fetchone()
        sort_order = (int(last["m"]) if last and last["m"] is not None else 0) + SHOT_SORT_STEP
    updates["setup"] = next_setup_number(conn, scene["id"])
    slug = assemble_shot_id(project["film_code"], scene["number"], updates["setup"], exposure_class, name_slug)
    columns = ["project_id", "slug", "sort_order", "created_at"] + list(updates.keys())
    values = [project["id"], slug, sort_order, utc_now_iso()] + list(updates.values())
    cursor = conn.execute(
        f"INSERT INTO shots ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        values,
    )
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return get_shot(new_id)


def update_shot(shot_id, body: dict) -> dict:
    existing = get_shot(shot_id)
    if not existing:
        raise LookupError("Shot not found")
    updates = _shot_updates_from_body(body)
    _enforce_single_edit_target(existing, updates)
    _validate_cast_refs(updates.get("scene_id", existing.get("scene_id")) if "scene_id" in updates else existing.get("scene_id"), updates)
    # slug, sort_order, project_id are never writable here
    if updates:
        conn = get_db_connection()
        assignments = ", ".join(f"{column} = ?" for column in updates)
        conn.execute(f"UPDATE shots SET {assignments} WHERE id = ?", (*updates.values(), existing["id"]))
        conn.commit()
        conn.close()
    if updates.get("scene_id") and updates["scene_id"] != existing.get("scene_id"):
        _place_shot_in_scene(existing["project_id"], existing["id"], updates["scene_id"])   # Task 16
    return get_shot(existing["id"])


def _place_shot_in_scene(project_id: int, shot_id: int, scene_id: int) -> None:
    """Move the shot's sort_order to sit after the last shot of its new scene. Everything else keeps
    its relative order; slugs never change. An empty scene places the shot where the scene sits in
    scene order (after the last shot of any earlier scene)."""
    rows = fetch_shots(project_id)
    order = [row["id"] for row in rows if row["id"] != shot_id]
    scene_rank = {scene["id"]: index for index, scene in enumerate(fetch_scenes(project_id))}
    target_rank = scene_rank.get(scene_id)
    if target_rank is None:
        return
    others = [row for row in rows if row["id"] != shot_id]
    in_scene = [index for index, row in enumerate(others) if row.get("scene_id") == scene_id]
    if in_scene:
        insert_at = in_scene[-1] + 1                      # after the last shot of the new scene
    else:
        insert_at = 0                                     # empty scene: after the last shot of any earlier scene
        for index, row in enumerate(others):
            rank = scene_rank.get(row.get("scene_id"))
            if rank is not None and rank <= target_rank:
                insert_at = index + 1
    order.insert(insert_at, shot_id)
    if order != [row["id"] for row in rows]:
        reorder_shots(project_id, order)


def delete_shot(shot_id) -> bool:
    existing = get_shot(shot_id)
    if not existing:
        return False
    conn = get_db_connection()
    conn.execute("DELETE FROM shots WHERE id = ?", (existing["id"],))
    conn.commit()
    conn.close()
    return True


# ---------------------------------------------------------------------------
# Scenes (Director Studio, Task 12). Same rules as shots: identity in slug, order in
# sort_order, insertion free. Shots point at a scene through shots.scene_id.
# ---------------------------------------------------------------------------
SCENE_TEXT_FIELDS = ("name", "brief", "script", "note", "environment_id")
SCENE_EDITABLE_FIELDS = SCENE_TEXT_FIELDS + ("locked_fields",)


def scene_row_to_dict(row) -> dict:
    item = dict(row)
    try:
        locked = json.loads(item.get("locked_fields") or "[]")
    except json.JSONDecodeError:
        locked = []
    item["locked_fields"] = locked if isinstance(locked, list) else []
    item["environment_id"] = str(item.get("environment_id") or "") or None
    return item


def attach_scene_shot_counts(conn, scenes: list[dict]) -> list[dict]:
    if not scenes:
        return scenes
    ids = [scene["id"] for scene in scenes]
    marks = ",".join("?" for _ in ids)
    counts = {row["scene_id"]: row["n"] for row in conn.execute(
        f"SELECT scene_id, COUNT(*) AS n FROM shots WHERE scene_id IN ({marks}) GROUP BY scene_id", ids).fetchall()}
    for scene in scenes:
        scene["shot_count"] = int(counts.get(scene["id"], 0))
    return scenes


def fetch_scenes(project_id) -> list[dict]:
    try:
        project_id = int(project_id)
    except (TypeError, ValueError):
        return []
    init_studio_db()
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM scenes WHERE project_id = ? ORDER BY sort_order ASC, id ASC", (project_id,)).fetchall()
    scenes = attach_scene_shot_counts(conn, [scene_row_to_dict(row) for row in rows])
    conn.close()
    return scenes


def get_scene(scene_id) -> dict | None:
    try:
        scene_id = int(scene_id)
    except (TypeError, ValueError):
        return None
    init_studio_db()
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM scenes WHERE id = ? LIMIT 1", (scene_id,)).fetchone()
    scenes = attach_scene_shot_counts(conn, [scene_row_to_dict(row)]) if row else []
    conn.close()
    return scenes[0] if scenes else None


def next_scene_slug(conn, project: dict) -> str:
    """<ProjectStem>_SC### from a per-project counter that only ever increments."""
    row = conn.execute("SELECT next_number FROM scene_slug_counters WHERE project_id = ?", (project["id"],)).fetchone()
    number = int(row["next_number"]) if row else 1
    conn.execute(
        "INSERT INTO scene_slug_counters (project_id, next_number) VALUES (?, ?) "
        "ON CONFLICT(project_id) DO UPDATE SET next_number = excluded.next_number",
        (project["id"], number + 1),
    )
    stem = sanitize_asset_filename_stem(project.get("name", ""), fallback="project")
    return f"{stem}_SC{number:03d}"


def next_scene_id(conn, project: dict) -> tuple[str, int]:
    """Task 17: ({FILM}-S{NN}, NN) from the same per-project counter (it only increments; nothing is reused)."""
    row = conn.execute("SELECT next_number FROM scene_slug_counters WHERE project_id = ?", (project["id"],)).fetchone()
    number = int(row["next_number"]) if row else 1
    conn.execute(
        "INSERT INTO scene_slug_counters (project_id, next_number) VALUES (?, ?) "
        "ON CONFLICT(project_id) DO UPDATE SET next_number = excluded.next_number",
        (project["id"], number + 1),
    )
    return f"{project['film_code']}-S{number:02d}", number


def _scene_updates_from_body(body: dict) -> dict:
    updates = {}
    for key in SCENE_TEXT_FIELDS:
        if key in body:
            value = body.get(key)
            if key == "environment_id":
                updates[key] = str(value or "").strip() or None
            elif key == "script":
                updates[key] = str(value or "")            # multi-line, kept as typed
            else:
                updates[key] = str(value or "").strip()
    if "locked_fields" in body:
        values = body.get("locked_fields") if isinstance(body.get("locked_fields"), list) else []
        cleaned = []
        for value in values:
            text = str(value or "").strip()
            if text in SCENE_TEXT_FIELDS and text not in cleaned:
                cleaned.append(text)
        updates["locked_fields"] = json.dumps(cleaned)
    return updates


def _compact_scene_sort_orders(conn, project_id: int) -> None:
    rows = conn.execute("SELECT id FROM scenes WHERE project_id = ? ORDER BY sort_order ASC, id ASC", (project_id,)).fetchall()
    for index, row in enumerate(rows):
        conn.execute("UPDATE scenes SET sort_order = ? WHERE id = ?", ((index + 1) * SHOT_SORT_STEP, row["id"]))


def create_scene(body: dict) -> dict:
    project = get_project(body.get("project_id"))
    if not project:
        raise LookupError("Project not found")
    if project.get("type") != "film":
        raise ValueError("Scenes belong to film projects")
    if not project.get("film_code"):
        raise ValueError(NO_FILM_CODE_MESSAGE)
    updates = _scene_updates_from_body(body)
    init_studio_db()
    conn = get_db_connection()
    after_id = body.get("after_id")
    if after_id not in (None, ""):
        prev_row = conn.execute("SELECT id, sort_order FROM scenes WHERE id = ? AND project_id = ?", (int(after_id), project["id"])).fetchone()
        if not prev_row:
            conn.close()
            raise LookupError("Scene to insert after was not found")
        next_row = conn.execute(
            "SELECT id, sort_order FROM scenes WHERE project_id = ? AND (sort_order > ? OR (sort_order = ? AND id > ?)) ORDER BY sort_order ASC, id ASC LIMIT 1",
            (project["id"], prev_row["sort_order"], prev_row["sort_order"], prev_row["id"]),
        ).fetchone()
        if next_row is None:
            sort_order = int(prev_row["sort_order"]) + SHOT_SORT_STEP
        elif int(next_row["sort_order"]) - int(prev_row["sort_order"]) >= 2:
            sort_order = (int(prev_row["sort_order"]) + int(next_row["sort_order"])) // 2
        else:
            _compact_scene_sort_orders(conn, project["id"])
            prev_sort = int(conn.execute("SELECT sort_order FROM scenes WHERE id = ?", (prev_row["id"],)).fetchone()["sort_order"])
            sort_order = prev_sort + SHOT_SORT_STEP // 2
    else:
        last = conn.execute("SELECT MAX(sort_order) AS m FROM scenes WHERE project_id = ?", (project["id"],)).fetchone()
        sort_order = (int(last["m"]) if last and last["m"] is not None else 0) + SHOT_SORT_STEP
    slug, updates["number"] = next_scene_id(conn, project)
    if not updates.get("name"):
        updates["name"] = slug
    columns = ["project_id", "slug", "sort_order", "created_at"] + list(updates.keys())
    values = [project["id"], slug, sort_order, utc_now_iso()] + list(updates.values())
    cursor = conn.execute(f"INSERT INTO scenes ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})", values)
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return get_scene(new_id)


def update_scene(scene_id, body: dict) -> dict:
    existing = get_scene(scene_id)
    if not existing:
        raise LookupError("Scene not found")
    updates = _scene_updates_from_body(body)
    # slug, sort_order, project_id are never writable here
    if updates:
        conn = get_db_connection()
        assignments = ", ".join(f"{column} = ?" for column in updates)
        conn.execute(f"UPDATE scenes SET {assignments} WHERE id = ?", (*updates.values(), existing["id"]))
        conn.commit()
        conn.close()
    return get_scene(existing["id"])


def delete_scene(scene_id) -> dict | None:
    """Deletes the scene; its shots stay and are detached (scene_id -> NULL)."""
    existing = get_scene(scene_id)
    if not existing:
        return None
    conn = get_db_connection()
    detached = conn.execute("SELECT COUNT(*) AS n FROM shots WHERE scene_id = ?", (existing["id"],)).fetchone()["n"]
    conn.execute("UPDATE shots SET scene_id = NULL WHERE scene_id = ?", (existing["id"],))
    removed_cast = conn.execute("SELECT COUNT(*) AS n FROM scene_cast WHERE scene_id = ?", (existing["id"],)).fetchone()["n"]
    conn.execute("DELETE FROM scene_cast WHERE scene_id = ?", (existing["id"],))
    conn.execute("DELETE FROM scenes WHERE id = ?", (existing["id"],))
    conn.commit()
    conn.close()
    return {"deleted": existing["slug"], "detached_shots": int(detached), "removed_cast": int(removed_cast)}


# ---------------------------------------------------------------------------
# Scene cast (Director Studio, Task 13). One talent element, dressed for one scene.
# A shot attaches a cast member as an elements entry whose ref is "cast:<id>".
# ---------------------------------------------------------------------------
CAST_TEXT_FIELDS = ("character_name", "look_name", "handle", "note")
CAST_REF_PREFIX = "cast:"


def cast_row_to_dict(row) -> dict:
    item = dict(row)
    for key in ("images", "locked_fields"):
        try:
            value = json.loads(item.get(key) or "[]")
        except json.JSONDecodeError:
            value = []
        item[key] = [str(v).strip() for v in value if str(v or "").strip()] if isinstance(value, list) else []
    return item


def attach_cast_elements(cast: list[dict]) -> list[dict]:
    """Adds `element` = {id, name, img_url} for the talent element, or None when it is gone."""
    for member in cast:
        url = resolve_element_primary_image_url(member.get("element_id"))
        name = ""
        if url:
            for folder_name in ELEMENTS_CATEGORIES:
                folder_path = os.path.join(ELEMENTS_DIR, folder_name)
                if not os.path.isdir(folder_path):
                    continue
                for json_path in list_talent_jsons(folder_path):
                    talent = load_talent_json(json_path)
                    if talent and str(talent.get("id", "")) == str(member.get("element_id")):
                        name = str(talent.get("name") or "")
                        break
                if name:
                    break
        member["element"] = {"id": member.get("element_id"), "name": name or member.get("element_id"), "img_url": url} if url else None
    return cast


def fetch_cast(scene_id) -> list[dict]:
    try:
        scene_id = int(scene_id)
    except (TypeError, ValueError):
        return []
    init_studio_db()
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM scene_cast WHERE scene_id = ? ORDER BY sort_order ASC, id ASC", (scene_id,)).fetchall()
    conn.close()
    return attach_cast_elements([cast_row_to_dict(row) for row in rows])


def get_cast_member(cast_id) -> dict | None:
    try:
        cast_id = int(cast_id)
    except (TypeError, ValueError):
        return None
    init_studio_db()
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM scene_cast WHERE id = ? LIMIT 1", (cast_id,)).fetchone()
    conn.close()
    return attach_cast_elements([cast_row_to_dict(row)])[0] if row else None


def _project_cast_handles(conn, project_id: int, exclude_id=None) -> set[str]:
    rows = conn.execute(
        "SELECT c.id, c.handle FROM scene_cast c JOIN scenes s ON s.id = c.scene_id WHERE s.project_id = ?", (project_id,)
    ).fetchall()
    return {str(row["handle"]).lower() for row in rows if row["handle"] and row["id"] != exclude_id}


def make_cast_handle(conn, project_id: int, character_name: str, look_name: str, requested: str = "", exclude_id=None) -> tuple[str, str]:
    """@Character_Look through the existing sanitizer; unique within the project, suffixed on collision.
    Returns (handle, note) where note says when a suffix was needed."""
    if requested:
        base = sanitize_asset_filename_stem(requested.lstrip("@"), fallback="")
    else:
        parts = [sanitize_asset_filename_stem(character_name, fallback=""), sanitize_asset_filename_stem(look_name, fallback="")]
        base = "_".join(part for part in parts if part)
    base = base or "cast"
    taken = _project_cast_handles(conn, project_id, exclude_id)
    handle = f"@{base}"
    counter = 2
    while handle.lower() in taken:
        handle = f"@{base}_{counter}"
        counter += 1
    note = f"Handle {'@' + base} was taken in this project; using {handle}." if handle != f"@{base}" else ""
    return handle, note


def _cast_updates_from_body(body: dict) -> dict:
    updates = {}
    for key in CAST_TEXT_FIELDS:
        if key in body and key != "handle":
            updates[key] = str(body.get(key) or "").strip()
    if "images" in body:
        updates["images"] = _normalize_shot_id_list_plain(body.get("images"))
    if "locked_fields" in body:
        values = body.get("locked_fields") if isinstance(body.get("locked_fields"), list) else []
        cleaned = []
        for value in values:
            text = str(value or "").strip()
            if text in CAST_TEXT_FIELDS + ("images", "element_id") and text not in cleaned:
                cleaned.append(text)
        updates["locked_fields"] = json.dumps(cleaned)
    return updates


def _normalize_shot_id_list_plain(raw) -> str:
    values = raw if isinstance(raw, list) else []
    cleaned = []
    for value in values:
        text = str((value.get("ref") if isinstance(value, dict) else value) or "").strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return json.dumps(cleaned, ensure_ascii=False)


def create_cast_member(scene_id, body: dict) -> tuple[dict, str]:
    scene = get_scene(scene_id)
    if not scene:
        raise LookupError("Scene not found")
    element_id = str(body.get("element_id") or "").strip()
    if not element_id:
        raise ValueError("A talent element is required")
    if not resolve_element_primary_image_url(element_id):
        raise ValueError(f"Element '{element_id}' was not found in the Elements library")
    updates = _cast_updates_from_body(body)
    updates.setdefault("character_name", "")
    updates.setdefault("look_name", "")
    init_studio_db()
    conn = get_db_connection()
    handle, note = make_cast_handle(conn, scene["project_id"], updates["character_name"], updates["look_name"], str(body.get("handle") or ""))
    last = conn.execute("SELECT MAX(sort_order) AS m FROM scene_cast WHERE scene_id = ?", (scene["id"],)).fetchone()
    sort_order = (int(last["m"]) if last and last["m"] is not None else 0) + SHOT_SORT_STEP
    columns = ["scene_id", "element_id", "handle", "sort_order", "created_at"] + list(updates.keys())
    values = [scene["id"], element_id, handle, sort_order, utc_now_iso()] + list(updates.values())
    cursor = conn.execute(f"INSERT INTO scene_cast ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})", values)
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return get_cast_member(new_id), note


def update_cast_member(cast_id, body: dict) -> tuple[dict, str]:
    existing = get_cast_member(cast_id)
    if not existing:
        raise LookupError("Cast member not found")
    updates = _cast_updates_from_body(body)
    note = ""
    if "element_id" in body:
        element_id = str(body.get("element_id") or "").strip()
        if not element_id or not resolve_element_primary_image_url(element_id):
            raise ValueError("Element was not found in the Elements library")
        updates["element_id"] = element_id
    if "handle" in body:
        scene = get_scene(existing["scene_id"])
        conn = get_db_connection()
        handle, note = make_cast_handle(conn, scene["project_id"], existing["character_name"], existing["look_name"], str(body.get("handle") or ""), exclude_id=existing["id"])
        conn.close()
        updates["handle"] = handle
    if updates:
        conn = get_db_connection()
        assignments = ", ".join(f"{column} = ?" for column in updates)
        conn.execute(f"UPDATE scene_cast SET {assignments} WHERE id = ?", (*updates.values(), existing["id"]))
        conn.commit()
        conn.close()
    return get_cast_member(existing["id"]), note


def delete_cast_member(cast_id) -> bool:
    existing = get_cast_member(cast_id)
    if not existing:
        return False
    conn = get_db_connection()
    conn.execute("DELETE FROM scene_cast WHERE id = ?", (existing["id"],))
    conn.commit()
    conn.close()
    return True


def reorder_cast(scene_id, ordered_ids: list) -> list[dict]:
    scene = get_scene(scene_id)
    if not scene:
        raise LookupError("Scene not found")
    try:
        ordered_ids = [int(value) for value in ordered_ids]
    except (TypeError, ValueError):
        raise ValueError("ids must be integers")
    current = [member["id"] for member in fetch_cast(scene["id"])]
    if sorted(ordered_ids) != sorted(current):
        raise ValueError("ids must contain every cast member of the scene exactly once")
    conn = get_db_connection()
    for index, cast_id in enumerate(ordered_ids):
        conn.execute("UPDATE scene_cast SET sort_order = ? WHERE id = ?", ((index + 1) * SHOT_SORT_STEP, cast_id))
    conn.commit()
    conn.close()
    return fetch_cast(scene["id"])


def _validate_cast_refs(shot_scene_id, updates: dict) -> None:
    """A shot may only attach cast members from its own scene."""
    if "elements" not in updates:
        return
    try:
        entries = json.loads(updates["elements"])
    except json.JSONDecodeError:
        return
    for entry in entries:
        ref = str(entry.get("ref") or "")
        if not ref.startswith(CAST_REF_PREFIX):
            continue
        member = get_cast_member(ref[len(CAST_REF_PREFIX):])
        if not member:
            raise ValueError(f"Cast member '{ref}' was not found")
        if not shot_scene_id or int(member["scene_id"]) != int(shot_scene_id):
            raise ValueError(f"{member['handle'] or ref} belongs to another scene; only the shot's own scene's cast can be attached")


def reorder_scenes(project_id, ordered_ids: list) -> list[dict]:
    """Rewrite scene sort_order from the given id order. Slugs are untouched."""
    project = get_project(project_id)
    if not project:
        raise LookupError("Project not found")
    try:
        ordered_ids = [int(value) for value in ordered_ids]
    except (TypeError, ValueError):
        raise ValueError("ids must be integers")
    current_ids = [scene["id"] for scene in fetch_scenes(project["id"])]
    if sorted(ordered_ids) != sorted(current_ids):
        raise ValueError("ids must contain every scene of the project exactly once")
    conn = get_db_connection()
    for index, scene_id in enumerate(ordered_ids):
        conn.execute("UPDATE scenes SET sort_order = ? WHERE id = ?", ((index + 1) * SHOT_SORT_STEP, scene_id))
    conn.commit()
    conn.close()
    return fetch_scenes(project["id"])


def reorder_shots(project_id, ordered_ids: list) -> list[dict]:
    """Rewrite sort_order from the given id order. Slugs are untouched.
    A row whose predecessor changed loses chain_from_previous."""
    project = get_project(project_id)
    if not project:
        raise LookupError("Project not found")
    try:
        ordered_ids = [int(value) for value in ordered_ids]
    except (TypeError, ValueError):
        raise ValueError("ids must be integers")
    current = fetch_shots(project["id"])
    current_ids = [row["id"] for row in current]
    if sorted(ordered_ids) != sorted(current_ids):
        raise ValueError("ids must contain every shot of the project exactly once")
    old_prev = {row_id: (current_ids[index - 1] if index > 0 else None) for index, row_id in enumerate(current_ids)}
    conn = get_db_connection()
    for index, row_id in enumerate(ordered_ids):
        new_prev = ordered_ids[index - 1] if index > 0 else None
        if new_prev != old_prev.get(row_id):
            conn.execute("UPDATE shots SET sort_order = ?, chain_from_previous = 0 WHERE id = ?", ((index + 1) * SHOT_SORT_STEP, row_id))
        else:
            conn.execute("UPDATE shots SET sort_order = ? WHERE id = ?", ((index + 1) * SHOT_SORT_STEP, row_id))
    conn.commit()
    conn.close()
    return fetch_shots(project["id"])


# ---------------------------------------------------------------------------
# Styles (Director Studio, Task 10). A reusable look: text and/or a board of images.
# ---------------------------------------------------------------------------
def style_row_to_dict(row) -> dict:
    item = dict(row)
    try:
        images = json.loads(item.get("images") or "[]")
    except json.JSONDecodeError:
        images = []
    item["images"] = [str(value).strip() for value in images if str(value or "").strip()] if isinstance(images, list) else []
    item["archived"] = bool(item.get("archived"))
    return item


def _normalize_style_images(raw) -> str:
    values = raw if isinstance(raw, list) else []
    cleaned = []
    for value in values:
        text = str((value.get("ref") if isinstance(value, dict) else value) or "").strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return json.dumps(cleaned, ensure_ascii=False)


def fetch_styles(project_id=None, include_archived: bool = False) -> list[dict]:
    init_studio_db()
    conn = get_db_connection()
    clauses, params = [], []
    if project_id not in (None, ""):
        clauses.append("(project_id IS NULL OR project_id = ?)")
        params.append(int(project_id))
    if not include_archived:
        clauses.append("archived = 0")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(f"SELECT * FROM styles{where} ORDER BY name COLLATE NOCASE ASC, id ASC", params).fetchall()
    conn.close()
    return [style_row_to_dict(row) for row in rows]


def get_style(style_id) -> dict | None:
    try:
        style_id = int(style_id)
    except (TypeError, ValueError):
        return None
    init_studio_db()
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM styles WHERE id = ? LIMIT 1", (style_id,)).fetchone()
    conn.close()
    return style_row_to_dict(row) if row else None


def create_style(body: dict) -> dict:
    name = sanitize_asset_meta_text(body.get("name", ""))
    if not name:
        raise ValueError("Style name is required")
    project_id = body.get("project_id")
    if project_id not in (None, ""):
        if not get_project(project_id):
            raise ValueError("Project not found")
        project_id = int(project_id)
    else:
        project_id = None
    init_studio_db()
    conn = get_db_connection()
    cursor = conn.execute(
        "INSERT INTO styles (name, text, images, project_id, archived, created_at) VALUES (?, ?, ?, ?, 0, ?)",
        (name, str(body.get("text") or "").strip(), _normalize_style_images(body.get("images")), project_id, utc_now_iso()),
    )
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return get_style(new_id)


def update_style(style_id, body: dict) -> dict:
    existing = get_style(style_id)
    if not existing:
        raise LookupError("Style not found")
    updates = {}
    if "name" in body:
        name = sanitize_asset_meta_text(body.get("name", ""))
        if not name:
            raise ValueError("Style name is required")
        updates["name"] = name
    if "text" in body:
        updates["text"] = str(body.get("text") or "").strip()
    if "images" in body:
        updates["images"] = _normalize_style_images(body.get("images"))
    if "project_id" in body:
        raw = body.get("project_id")
        if raw in (None, ""):
            updates["project_id"] = None
        else:
            if not get_project(raw):
                raise ValueError("Project not found")
            updates["project_id"] = int(raw)
    if "archived" in body:
        updates["archived"] = 1 if body.get("archived") else 0
    if updates:
        conn = get_db_connection()
        assignments = ", ".join(f"{column} = ?" for column in updates)
        conn.execute(f"UPDATE styles SET {assignments} WHERE id = ?", (*updates.values(), existing["id"]))
        conn.commit()
        conn.close()
    return get_style(existing["id"])


def delete_style(style_id) -> bool:
    existing = get_style(style_id)
    if not existing:
        return False
    conn = get_db_connection()
    conn.execute("UPDATE shots SET style_id = NULL WHERE style_id = ?", (existing["id"],))
    conn.execute("DELETE FROM styles WHERE id = ?", (existing["id"],))
    conn.commit()
    conn.close()
    return True


# ---------------------------------------------------------------------------
# Disk and temp (Task 14b). Read-only sizes, and a cleaner scoped to app-created temp dirs.
# ---------------------------------------------------------------------------
def make_app_temp_dir(label: str = "") -> str:
    """The only sanctioned way to create a temp directory: prefixed and marked so the cleaner can find it."""
    path = tempfile.mkdtemp(prefix=APP_TEMP_PREFIX + (f"{label}_" if label else ""))
    with open(os.path.join(path, APP_TEMP_MARKER), "w", encoding="utf-8") as fh:
        fh.write(utc_now_iso())
    return path


def dir_size_bytes(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def _protected_roots() -> list[str]:
    return [os.path.realpath(BASE_DIR), os.path.realpath(IMAGE_ASSETS_DIR), os.path.realpath(CONFIG_FILE), os.path.realpath(STUDIO_DB_FILE)]


def is_app_temp_dir(path: str) -> tuple[bool, str]:
    """(is_ours, why). Ours = directly inside the system temp dir, named with our prefix AND holding
    the marker file, or named exactly like the legacy harness copies. Never anything under the app."""
    real = os.path.realpath(path)
    if not os.path.isdir(real):
        return False, "not a directory"
    for root in _protected_roots():
        if real == root or real.startswith(root + os.sep):
            return False, "inside the app folder"
    if os.path.dirname(real) != os.path.realpath(tempfile.gettempdir()):
        return False, "not directly inside the temp directory"
    name = os.path.basename(real)
    if name.startswith(APP_TEMP_PREFIX) and os.path.isfile(os.path.join(real, APP_TEMP_MARKER)):
        return True, "app prefix + marker"
    if LEGACY_HARNESS_TEMP_RE.match(name) and os.path.isfile(os.path.join(real, "nbs.py")):
        return True, "legacy test-harness copy"
    if name.startswith(APP_TEMP_PREFIX):
        # residue of an interrupted cleanup: our prefix, and nothing inside but a copied studio.db / the marker
        leftovers = set(os.listdir(real))
        if leftovers and leftovers <= {APP_TEMP_MARKER, "studio.db"}:
            return True, "app prefix, cleanup residue (studio.db only)"
    return False, "not app-created"


def scan_app_temp_dirs() -> list[dict]:
    temp_root = tempfile.gettempdir()
    found = []
    try:
        names = sorted(os.listdir(temp_root))
    except OSError:
        return found
    for name in names:
        path = os.path.join(temp_root, name)
        ours, why = is_app_temp_dir(path)
        if ours:
            found.append({"path": path, "bytes": dir_size_bytes(path), "why": why})
    return found


def disk_report() -> dict:
    assets = {}
    for label, path in (("generations", GENERATIONS_DIR), ("videos", VIDEOS_DIR), ("loved", LOVED_DIR),
                        ("reference_archive", REFERENCE_ARCHIVE_DIR), ("reference_masks", REFERENCE_MASKS_DIR),
                        ("reference_renders", REFERENCE_RENDERS_DIR), ("edit_sessions", EDIT_SESSIONS_DIR)):
        assets[label] = dir_size_bytes(path) if os.path.isdir(path) else 0
    usage = shutil.disk_usage(BASE_DIR)
    temp_dirs = scan_app_temp_dirs()
    return {
        "image_assets_bytes": dir_size_bytes(IMAGE_ASSETS_DIR) if os.path.isdir(IMAGE_ASSETS_DIR) else 0,
        "assets": assets,
        "temp_dirs": temp_dirs,
        "temp_bytes": sum(item["bytes"] for item in temp_dirs),
        "temp_root": tempfile.gettempdir(),
        "drive": os.path.splitdrive(os.path.realpath(BASE_DIR))[0] or os.path.realpath(BASE_DIR),
        "free_bytes": usage.free,
        "total_bytes": usage.total,
    }


def clean_app_temp_dirs(requested_paths: list[str]) -> dict:
    """Removes only paths that pass is_app_temp_dir at the moment of deletion."""
    removed, skipped, freed = [], [], 0
    for raw in requested_paths:
        path = os.path.realpath(str(raw or ""))
        ours, why = is_app_temp_dir(path)
        if not ours:
            skipped.append({"path": path, "why": why})
            continue
        size = dir_size_bytes(path)
        shutil.rmtree(path, ignore_errors=True)
        if os.path.exists(path):
            skipped.append({"path": path, "why": "could not remove (in use?)"})
            continue
        removed.append({"path": path, "bytes": size})
        freed += size
    return {"removed": removed, "skipped": skipped, "freed_bytes": freed}


# ---------------------------------------------------------------------------
# Workbench agent (Director Studio, Task 14). Reads everything, proposes anything,
# writes nothing: the only writes here are the conversation rows themselves.
# ---------------------------------------------------------------------------
OPENAI_API_BASE = "https://api.openai.com/v1"
AGENT_DIGEST_BUDGET_TOKENS = 60000       # what the digest may occupy before truncation
AGENT_HISTORY_WARN_TOKENS = 20000        # when the running exchange starts to cost real money
AGENT_FRAMING = (
    "You are the project's assistant inside Directors Studio. Below this framing is a digest of the whole "
    "project as it is right now: playbook, scenes, cast, shots, elements, styles. Treat it as the current truth.\n"
    "You read everything and may propose anything: answer questions from the playbook and the records, point out "
    "contradictions between a shot and what its scene established, suggest shots for a scene, draft a prompt for a "
    "shot, or explain your reasoning when asked.\n"
    "You cannot change anything. Nothing you write is applied to the project; the user acts on it by hand. "
    "Do not claim to have created, edited or deleted a record, and do not ask for permission to do so - write the "
    "proposal as text the user can act on, naming scenes and shots by their slugs."
)


def estimate_tokens(text) -> int:
    """No tokenizer dependency: ~4 characters per token is close enough for budgeting."""
    return max(0, (len(str(text or "")) + 3) // 4)


def _agent_shot_line(shot: dict) -> str:
    camera = ", ".join(f"{key} {shot.get(key)}" for key in ("shot_size", "angle", "lens", "aperture", "speed_ramp") if shot.get(key))
    movement = ", ".join(shot.get("movement") or [])
    takes = shot.get("takes") or {}
    bits = [
        f"{shot['slug']} [#{shot.get('_pos', '?')}]",
        f"scene {shot.get('_scene_slug') or (shot.get('scene') or '-')}",
        f"status {shot.get('status')}",
        f"takes {takes.get('count', 0)}" + (" (approved)" if takes.get("approved") else ""),
    ]
    if shot.get("duration_seconds") not in (None, ""):
        bits.append(f"{shot['duration_seconds']}s")
    if camera:
        bits.append(camera)
    if movement:
        bits.append(f"movement {movement}")
    line = " | ".join(bits)
    if shot.get("action_text"):
        line += f"\n    action: {shot['action_text']}"
    if shot.get("dialogue"):
        line += f"\n    dialogue: {shot['dialogue']}"
    if shot.get("audio_cue"):
        line += f"\n    audio: {shot['audio_cue']}"
    if shot.get("elements"):
        line += "\n    attached: " + ", ".join(f"{e['ref']} ({e['role']})" for e in shot["elements"])
    return line


def build_project_digest(project: dict, focus_scene_id=None, budget_tokens: int = AGENT_DIGEST_BUDGET_TOKENS) -> tuple[str, dict]:
    """The whole project as text for the model. Returns (digest, info) where info carries the token
    estimate and every truncation made, in order of least importance:
    1) other scenes' scripts, 2) element detail, 3) shot action/dialogue text."""
    scenes = fetch_scenes(project["id"])
    shots = fetch_shots(project["id"])
    scene_by_id = {scene["id"]: scene for scene in scenes}
    for index, shot in enumerate(shots):
        shot["_pos"] = index + 1
        shot["_scene_slug"] = scene_by_id[shot["scene_id"]]["slug"] if shot.get("scene_id") in scene_by_id else ""
    cast_by_scene = {scene["id"]: fetch_cast(scene["id"]) for scene in scenes}
    styles = fetch_styles(project_id=project["id"])
    elements = []
    for folder_name, cat_meta in ELEMENTS_CATEGORIES.items():
        folder_path = os.path.join(ELEMENTS_DIR, folder_name)
        if not os.path.isdir(folder_path):
            continue
        for json_path in list_talent_jsons(folder_path):
            talent = load_talent_json(json_path)
            if talent:
                elements.append((str(talent.get("id", "")), str(talent.get("name", "")), normalize_element_type(talent.get("type") or ELEMENT_FOLDER_TYPES.get(folder_name, "talent"))))

    try:
        focus_id = int(focus_scene_id) if focus_scene_id not in (None, "") else None
    except (TypeError, ValueError):
        focus_id = None

    settings = project.get("settings") or {}
    head = [f"# PROJECT: {project['name']} (client {project.get('client') or '-'}, type {project['type']})"]
    if settings:
        head.append("settings: " + ", ".join(f"{key} {value}" for key, value in settings.items() if value))
    playbook = str(project.get("playbook") or "").strip()
    head.append("\n## PLAYBOOK\n" + (playbook if playbook else "(no playbook yet)"))

    def scene_block(scene, with_script):
        member_lines = [
            f"    - {m.get('handle') or ''} {m.get('character_name') or ''} / look {m.get('look_name') or '-'} / element {m.get('element_id')}" + ("" if m.get("element") else " (element missing)")
            for m in cast_by_scene.get(scene["id"], [])
        ]
        lines = [f"### {scene['slug']} [#{scenes.index(scene) + 1}] {scene.get('name') or ''}".rstrip()]
        if scene.get("brief"):
            lines.append(f"  brief: {scene['brief']}")
        if scene.get("environment_id"):
            lines.append(f"  environment: {scene['environment_id']}")
        if member_lines:
            lines.append("  cast:\n" + "\n".join(member_lines))
        script = str(scene.get("script") or "").strip()
        if script:
            if with_script:
                lines.append("  script:\n" + "\n".join("    " + row for row in script.splitlines()))
            else:
                lines.append(f"  script: ({len(script.splitlines())} lines, omitted - ask to see it)")
        return "\n".join(lines)

    def assemble(script_scene_ids, include_elements, shot_text):
        parts = list(head)
        parts.append("\n## SCENES (in film order)")
        parts.extend(scene_block(scene, scene["id"] in script_scene_ids) for scene in scenes) if scenes else parts.append("(no scenes yet)")
        parts.append("\n## SHOTS (in film order)")
        if shots:
            for shot in shots:
                line = _agent_shot_line(shot)
                if not shot_text:
                    line = line.split("\n")[0]
                parts.append("- " + line)
        else:
            parts.append("(no shots yet)")
        parts.append("\n## ELEMENTS")
        if include_elements:
            parts.append("\n".join(f"- {eid}: {name} ({etype})" for eid, name, etype in elements) if elements else "(none)")
        else:
            parts.append(f"({len(elements)} elements - list omitted)")
        parts.append("\n## STYLES")
        parts.append("\n".join(f"- {st['name']}: {st.get('text') or '(no text)'}" for st in styles) if styles else "(none)")
        return "\n".join(parts)

    truncations = []
    scene_ids = {scene["id"] for scene in scenes}
    digest = assemble(scene_ids, True, True)
    if estimate_tokens(digest) > budget_tokens:
        kept = {focus_id} if focus_id in scene_ids else set()
        dropped = [scene["slug"] for scene in scenes if scene["id"] not in kept and str(scene.get("script") or "").strip()]
        digest = assemble(kept, True, True)
        if dropped:
            truncations.append("scripts omitted for " + ", ".join(dropped) + (" (the scene under discussion is kept in full)" if kept else ""))
    if estimate_tokens(digest) > budget_tokens:
        digest = assemble({focus_id} if focus_id in scene_ids else set(), False, True)
        truncations.append("element list omitted (names only were included)")
    if estimate_tokens(digest) > budget_tokens:
        digest = assemble({focus_id} if focus_id in scene_ids else set(), False, False)
        truncations.append("shot action, dialogue, audio and attachments omitted (slug, scene, camera and status kept)")
    if estimate_tokens(digest) > budget_tokens:
        limit = budget_tokens * 4
        digest = digest[:limit] + "\n\n[digest cut at the token budget]"
        truncations.append(f"digest hard-cut at ~{budget_tokens} tokens")
    info = {
        "tokens": estimate_tokens(digest),
        "budget_tokens": budget_tokens,
        "truncations": truncations,
        "counts": {"scenes": len(scenes), "shots": len(shots), "cast": sum(len(v) for v in cast_by_scene.values()), "elements": len(elements), "styles": len(styles)},
        "playbook_tokens": estimate_tokens(playbook),
        "focus_scene_id": focus_id,
    }
    return digest, info


def fetch_agent_messages(project_id) -> list[dict]:
    init_studio_db()
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM agent_messages WHERE project_id = ? ORDER BY id ASC", (int(project_id),)).fetchall()
    conn.close()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["meta"] = json.loads(item.get("meta") or "{}")
        except json.JSONDecodeError:
            item["meta"] = {}
        out.append(item)
    return out


def append_agent_message(project_id, role: str, content: str, meta: dict | None = None) -> dict:
    conn = get_db_connection()
    cursor = conn.execute(
        "INSERT INTO agent_messages (project_id, role, content, meta, created_at) VALUES (?, ?, ?, ?, ?)",
        (int(project_id), role, content, json.dumps(meta or {}, ensure_ascii=False), utc_now_iso()),
    )
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return {"id": new_id, "project_id": int(project_id), "role": role, "content": content, "meta": meta or {}}


def clear_agent_messages(project_id) -> int:
    conn = get_db_connection()
    n = conn.execute("SELECT COUNT(*) AS n FROM agent_messages WHERE project_id = ?", (int(project_id),)).fetchone()["n"]
    conn.execute("DELETE FROM agent_messages WHERE project_id = ?", (int(project_id),))
    conn.commit()
    conn.close()
    return int(n)


def build_agent_system_prompt(instructions: str, digest: str, truncations: list[str]) -> str:
    parts = []
    if instructions.strip():
        parts.append(instructions.strip())
    parts.append(AGENT_FRAMING)
    if truncations:
        parts.append("Note: to fit the context, the digest leaves out: " + "; ".join(truncations) + ". Say so if the user asks about something that was left out.")
    parts.append("=== PROJECT DIGEST ===\n" + digest)
    return "\n\n".join(parts)


def call_openai_chat(api_key: str, model: str, messages: list[dict], timeout: int = 120) -> dict:
    """The one OpenAI call: chat completions over the existing HTTP client. Returns {ok, text, usage} or {ok: False, error}."""
    try:
        response = requests.post(
            f"{OPENAI_API_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": messages, "temperature": 0.4},
            timeout=timeout,
        )
    except requests.exceptions.Timeout:
        return {"ok": False, "error": f"OpenAI timeout ({timeout}s)"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if response.status_code != 200:
        try:
            err = response.json().get("error", {}).get("message", f"HTTP {response.status_code}")
        except Exception:
            err = f"HTTP {response.status_code}: {response.text[:200]}"
        return {"ok": False, "error": err}
    data = response.json()
    text = ""
    for choice in data.get("choices", []):
        text = str((choice.get("message") or {}).get("content") or "")
        if text:
            break
    return {"ok": True, "text": text.strip(), "usage": data.get("usage") or {}}


# ---------------------------------------------------------------------------
# Takes (Director Studio, Task 07). Status = what the system did (on the shot);
# approved = what the user decided (on one take). Never derived from each other.
# ---------------------------------------------------------------------------
TAKE_STATUSES = ("queued", "rendering", "done", "failed")


def take_row_to_dict(row) -> dict:
    item = dict(row)
    item["approved"] = bool(item.get("approved"))
    item["cost"] = float(item.get("cost") or 0.0)
    return item


def fetch_takes(shot_id) -> list[dict]:
    try:
        shot_id = int(shot_id)
    except (TypeError, ValueError):
        return []
    init_studio_db()
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM takes WHERE shot_id = ? ORDER BY id DESC", (shot_id,)).fetchall()
    conn.close()
    return [take_row_to_dict(row) for row in rows]


def get_take(take_id) -> dict | None:
    try:
        take_id = int(take_id)
    except (TypeError, ValueError):
        return None
    init_studio_db()
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM takes WHERE id = ? LIMIT 1", (take_id,)).fetchone()
    conn.close()
    return take_row_to_dict(row) if row else None


def create_take(shot_id: int, *, engine: str, prompt_sent: str, job_id: str = "") -> dict:
    init_studio_db()
    conn = get_db_connection()
    cursor = conn.execute(
        "INSERT INTO takes (shot_id, engine, prompt_sent, status, job_id, created_at) VALUES (?, ?, ?, 'queued', ?, ?)",
        (int(shot_id), engine, prompt_sent, job_id, utc_now_iso()),
    )
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return get_take(new_id)


def update_take(take_id, **fields) -> dict | None:
    allowed = {"asset_path", "approved", "cost", "engine", "status", "error", "job_id", "poster_path", "completed_at", "asset_path_numbered"}
    updates = {key: value for key, value in fields.items() if key in allowed}
    if "status" in updates and updates["status"] not in TAKE_STATUSES:
        raise ValueError("status must be one of " + ", ".join(TAKE_STATUSES))
    existing = get_take(take_id)
    if not existing:
        raise LookupError("Take not found")
    conn = get_db_connection()
    if updates.get("approved"):
        # At most one approved take per shot: approving one clears the others.
        conn.execute("UPDATE takes SET approved = 0 WHERE shot_id = ? AND id != ?", (existing["shot_id"], existing["id"]))
        updates["approved"] = 1
    elif "approved" in updates:
        updates["approved"] = 0
    if updates:
        assignments = ", ".join(f"{column} = ?" for column in updates)
        conn.execute(f"UPDATE takes SET {assignments} WHERE id = ?", (*updates.values(), existing["id"]))
    conn.commit()
    conn.close()
    return get_take(existing["id"])


# ---- Task 16: approval state in the filename. The bridge matches *_approved.<ext> and needs no DB. ----
APPROVED_TAKE_SUFFIX = "_approved"
_TAKE_NUMBER_RE = re.compile(r"_take(?:_\d+)?$")


def _approved_take_stem(stem: str) -> str:
    """'<prefix>_take' / '<prefix>_take_3' -> '<prefix>_approved'; any other stem gets the suffix appended."""
    if _TAKE_NUMBER_RE.search(stem):
        return _TAKE_NUMBER_RE.sub(APPROVED_TAKE_SUFFIX, stem)
    return stem + APPROVED_TAKE_SUFFIX


def _take_file_set(video_url: str, poster_url: str = "") -> dict:
    """Local files that belong to one take: the video, its .json sidecar, its poster. Video must exist."""
    video_path = resolve_local_video_url_to_path(video_url)
    if not video_path:
        raise FileNotFoundError(f"The take's file is not on disk: {video_url or '(no path)'}")
    stem, ext = os.path.splitext(os.path.basename(video_path))
    folder = os.path.dirname(video_path)
    files = {"video": video_path, "stem": stem, "ext": ext, "dir": folder}
    json_path = os.path.join(folder, f"{stem}.json")
    if os.path.isfile(json_path):
        files["json"] = json_path
    poster_path = resolve_local_video_url_to_path(poster_url) if poster_url else ""
    if poster_path:
        files["poster"] = poster_path
    return files


def _rename_take_files(files: dict, new_stem: str) -> dict:
    """Rename video (+ sidecar, + poster) to new_stem, all or nothing. Returns the new URLs."""
    folder, old_stem, ext = files["dir"], files["stem"], files["ext"]
    plan = [(files["video"], os.path.join(folder, f"{new_stem}{ext}"))]
    if files.get("json"):
        plan.append((files["json"], os.path.join(folder, f"{new_stem}.json")))
    if files.get("poster"):
        poster_name = os.path.basename(files["poster"])
        plan.append((files["poster"], os.path.join(folder, poster_name.replace(old_stem, new_stem, 1) if poster_name.startswith(old_stem) else f"{new_stem}_poster.png")))
    for _src, dst in plan:
        if os.path.exists(dst):
            raise FileExistsError(f"Cannot rename: {os.path.basename(dst)} already exists")
    done = []
    try:
        for src, dst in plan:
            os.replace(src, dst)
            done.append((src, dst))
    except Exception:
        for src, dst in reversed(done):   # undo what was renamed so far
            try:
                os.replace(dst, src)
            except Exception:
                pass
        raise
    new_video = plan[0][1]
    relpath = os.path.relpath(new_video, VIDEOS_DIR).replace("\\", "/")
    result = {"asset_path": f"/videos/{relpath}", "poster_path": ""}
    if files.get("poster"):
        result["poster_path"] = f"/videos/{os.path.relpath(plan[-1][1], VIDEOS_DIR).replace(chr(92), '/')}"
    if files.get("json"):
        try:   # keep the sidecar pointing at the renamed files so the Assets browser still finds them
            json_path = plan[1][1]
            with open(json_path, "r", encoding="utf-8") as fh:
                meta = json.load(fh)
            meta["filename"] = os.path.basename(new_video)
            meta["assetRelpath"] = relpath
            if files.get("poster"):
                meta["poster_filename"] = os.path.basename(plan[-1][1])
                meta["poster_url"] = result["poster_path"]
            with open(json_path, "w", encoding="utf-8") as fh:
                json.dump(meta, fh, indent=2, ensure_ascii=False)
        except Exception:
            pass
    return result


def _free_numbered_stem(folder: str, stem: str, ext: str) -> str:
    """The take's numbered name, or the next free _take_N if that name was reused meanwhile."""
    if not os.path.exists(os.path.join(folder, f"{stem}{ext}")) and not os.path.exists(os.path.join(folder, f"{stem}.json")):
        return stem
    base = _TAKE_NUMBER_RE.sub("", stem) if _TAKE_NUMBER_RE.search(stem) else stem
    counter = 2
    while True:
        candidate = f"{base}_take_{counter}"
        if not os.path.exists(os.path.join(folder, f"{candidate}{ext}")) and not os.path.exists(os.path.join(folder, f"{candidate}.json")):
            return candidate
        counter += 1


def _revert_take_files(take: dict) -> dict:
    """approved name -> numbered name. Returns the DB fields to store."""
    files = _take_file_set(take.get("asset_path", ""), take.get("poster_path", ""))
    numbered_url = take.get("asset_path_numbered") or ""
    wanted_stem = os.path.splitext(os.path.basename(numbered_url))[0] if numbered_url else _TAKE_NUMBER_RE.sub("", files["stem"])
    if not wanted_stem or wanted_stem == files["stem"]:
        return {}
    new_stem = _free_numbered_stem(files["dir"], wanted_stem, files["ext"])
    return _rename_take_files(files, new_stem)


def set_take_approval(take_id, approved: bool) -> dict:
    """Approve/unapprove with the state mirrored in the filename. Files first; the database only
    changes once every rename succeeded, so disk and database never disagree."""
    take = get_take(take_id)
    if not take:
        raise LookupError("Take not found")
    if bool(take.get("approved")) == bool(approved):
        return take
    if approved and take.get("status") != "done":
        raise ValueError("Only a finished take can be approved.")
    if approved:
        previous = next((t for t in fetch_takes(take["shot_id"]) if t["approved"] and t["id"] != take["id"]), None)
        reverted = {}
        if previous:
            reverted = _revert_take_files(previous)   # may raise: nothing has changed yet
        files = _take_file_set(take.get("asset_path", ""), take.get("poster_path", ""))
        try:
            renamed = _rename_take_files(files, _approved_take_stem(files["stem"]))
        except Exception:
            if previous and reverted:   # put the previous approved file back so disk still matches the DB
                try:
                    _rename_take_files(_take_file_set(reverted["asset_path"], reverted.get("poster_path", "")), _approved_take_stem(os.path.splitext(os.path.basename(reverted["asset_path"]))[0]))
                except Exception:
                    pass
            raise
        if previous:
            update_take(previous["id"], approved=False, **{k: v for k, v in reverted.items() if k in ("asset_path", "poster_path")})
        return update_take(take["id"], approved=True, asset_path=renamed["asset_path"], poster_path=renamed.get("poster_path") or take.get("poster_path", ""),
                           asset_path_numbered=take.get("asset_path_numbered") or take.get("asset_path", ""))
    reverted = _revert_take_files(take)
    fields = {k: v for k, v in reverted.items() if k in ("asset_path", "poster_path")}
    return update_take(take["id"], approved=False, **fields)


def delete_take(take_id) -> bool:
    existing = get_take(take_id)
    if not existing:
        return False
    conn = get_db_connection()
    conn.execute("DELETE FROM takes WHERE id = ?", (existing["id"],))
    conn.commit()
    conn.close()
    return True


ASSET_URL_ROOTS = {
    "generations": lambda: GENERATIONS_DIR,
    "loved": lambda: LOVED_DIR,
    "videos": lambda: VIDEOS_DIR,
    "reference-archive": lambda: REFERENCE_ARCHIVE_DIR,
    "reference-render": lambda: REFERENCE_RENDERS_DIR,
    "reference-mask": lambda: REFERENCE_MASKS_DIR,
    "elements": lambda: ELEMENTS_DIR,
}


def resolve_asset_url_to_local_path(url: str) -> str:
    """'/generations/a/b/c.png' -> absolute file under the matching asset root, or ''."""
    text = str(url or "").strip()
    if text.startswith("http://") or text.startswith("https://"):
        text = urlparse(text).path
    text = unquote(text)
    parts = [part for part in text.replace("\\", "/").split("/") if part]
    if len(parts) < 2 or parts[0] not in ASSET_URL_ROOTS:
        return ""
    try:
        return safe_asset_path(ASSET_URL_ROOTS[parts[0]](), "/".join(parts[1:]))
    except Exception:
        return ""


def load_asset_image_payload(url: str, name: str) -> dict:
    local_path = resolve_asset_url_to_local_path(url)
    if not local_path:
        raise ValueError(f"Reference not found on disk: {url}")
    ext = os.path.splitext(local_path)[1].lower()
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(ext, "image/png")
    with open(local_path, "rb") as fh:
        data = base64.b64encode(fh.read()).decode("ascii")
    name = os.path.splitext(str(name or "image"))[0] + (ext if ext in (".png", ".jpg", ".jpeg", ".webp") else ".png")
    return {"mime_type": mime, "data": data, "name": name, "original_url": str(url or "")}


def resolve_element_primary_image_url(element_id: str) -> str:
    """Talent id -> '/elements/<folder>/<primary image>' using the per-talent JSON files."""
    wanted = str(element_id or "").strip()
    if not wanted:
        return ""
    for folder_name in ELEMENTS_CATEGORIES:
        folder_path = os.path.join(ELEMENTS_DIR, folder_name)
        if not os.path.isdir(folder_path):
            continue
        for json_path in list_talent_jsons(folder_path):
            talent = load_talent_json(json_path)
            if not talent or str(talent.get("id", "")) != wanted:
                continue
            images = talent.get("images") or []
            primary = next((img for img in images if img.get("is_primary")), images[0] if images else None)
            image_path = str((primary or {}).get("path") or talent.get("image_path") or "").strip()
            if image_path:
                return f"/elements/{folder_name}/{image_path}"
    return ""


# ---------------------------------------------------------------------------
# Prompt compiler (Director Studio, Task 15). Proposes text for a shot's prompt fields;
# writes nothing. The user accepts in the editor, which writes and locks the field.
# ---------------------------------------------------------------------------
# ===== COMPILER RULES - the only default instructions; edit here =====
COMPILER_RULES = (
    "Compiler rules:\n"
    "- Say what is different about this shot. Do not re-describe what the playbook, the scene or the references already establish.\n"
    "- Name what must stay unchanged from the references, explicitly.\n"
    "- Address references by position (reference 1, reference 2, ...), matching how they are sent.\n"
    "- Replace vague style words with visible, checkable instructions.\n"
    "- Subject first, then setting, then framing.\n"
    "- For a motion prompt: one clear action, what moves, what the camera does, what must not change. Not the whole scene.\n"
    "- Output prose, not JSON, not bullet points, not headings."
)
# ===== end compiler rules =====

COMPILE_FIELDS = {
    "scene_prompt": "Write the SCENE PROMPT for this shot: the single frame to generate as a still image before any motion. "
                    "It is what the first frame shows. Prose only.",
    "prompt": "Write the MOTION PROMPT for this shot: the text sent with the starting frame to the video model. "
              "One clear action, what moves, what the camera does, what must not change. Do not describe the whole scene - "
              "the starting image already shows it. Prose only.",
}


def list_shot_reference_slots(shot: dict) -> list[dict]:
    """The positional reference numbering the model will see, mirroring build_shot_render_payload:
    elements in array order (a cast entry expands to its dressed images), then reference assets,
    then an enabled style's images. Positions start at 1. Skipped items (no image) get no slot."""
    slots = []
    for entry in shot.get("elements") or []:
        ref = entry["ref"] if isinstance(entry, dict) else str(entry)
        role = entry.get("role", "unassigned") if isinstance(entry, dict) else "unassigned"
        if ref.startswith(CAST_REF_PREFIX):
            member = get_cast_member(ref[len(CAST_REF_PREFIX):])
            if not member:
                continue
            for index, url in enumerate(member.get("images") or []):
                slots.append({"position": len(slots) + 1, "kind": "cast", "role": role, "url": url,
                              "label": f"{member.get('handle') or ''} {member.get('character_name') or ''} / look {member.get('look_name') or '-'} (image {index + 1} of {len(member.get('images') or [])})".strip(),
                              "name": f"cast-{(member.get('handle') or ref).lstrip('@')}-{index + 1}"})
            continue
        url = resolve_element_primary_image_url(ref)
        if not url:
            continue
        name = ""
        for folder_name in ELEMENTS_CATEGORIES:
            folder_path = os.path.join(ELEMENTS_DIR, folder_name)
            if not os.path.isdir(folder_path):
                continue
            for json_path in list_talent_jsons(folder_path):
                talent = load_talent_json(json_path)
                if talent and str(talent.get("id", "")) == ref:
                    name = f"{talent.get('name') or ref} ({normalize_element_type(talent.get('type') or ELEMENT_FOLDER_TYPES.get(folder_name, 'talent'))})"
                    break
            if name:
                break
        slots.append({"position": len(slots) + 1, "kind": "element", "role": role, "url": url, "label": name or ref, "name": f"element-{ref}"})
    for index, entry in enumerate(shot.get("reference_assets") or []):
        ref = entry["ref"] if isinstance(entry, dict) else str(entry)
        role = entry.get("role", "unassigned") if isinstance(entry, dict) else "unassigned"
        slots.append({"position": len(slots) + 1, "kind": "reference", "role": role, "url": ref, "label": os.path.basename(ref), "name": f"reference-{index + 1}"})
    style = shot.get("style") if (shot.get("style_id") and shot.get("style_enabled", True)) else None
    if style:
        for index, url in enumerate(style.get("images") or []):
            slots.append({"position": len(slots) + 1, "kind": "style", "role": "style", "url": url, "label": f"style '{style.get('name')}' image {index + 1}", "name": f"style-{index + 1}"})
    return slots


def _script_excerpt(script: str, shot: dict, max_chars: int = 6000) -> str:
    text = str(script or "").strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    lines = text.splitlines()
    needles = [w.lower() for w in re.findall(r"[A-Za-z']{4,}", f"{shot.get('action_text', '')} {shot.get('dialogue', '')}")]
    best, best_score = 0, -1
    for index, line in enumerate(lines):
        low = line.lower()
        score = sum(1 for w in needles if w in low)
        if score > best_score:
            best, best_score = index, score
    start = max(0, best - 25)
    excerpt = "\n".join(lines[start:start + 50])
    return f"[excerpt: lines {start + 1}-{min(len(lines), start + 50)} of {len(lines)}; the full script was too long]\n{excerpt}"


def build_shot_compile_context(shot: dict, project: dict) -> tuple[str, list[dict]]:
    """Everything the compiler sees for one shot, as text, plus the reference slots."""
    scene = get_scene(shot["scene_id"]) if shot.get("scene_id") else None
    shots = fetch_shots(project["id"])
    index = next((i for i, item in enumerate(shots) if item["id"] == shot["id"]), -1)
    prev_shot = shots[index - 1] if index > 0 else None
    next_shot = shots[index + 1] if 0 <= index < len(shots) - 1 else None
    settings = project.get("settings") or {}
    slots = list_shot_reference_slots(shot)

    lines = [f"# PROJECT: {project['name']}"]
    for key in ("register", "aspect_ratio", "format", "runtime", "frame_rate"):
        if settings.get(key):
            lines.append(f"{key}: {settings[key]}")
    playbook = str(project.get("playbook") or "").strip()
    lines.append("## PLAYBOOK\n" + (playbook or "(no playbook)"))

    lines.append("\n## SCENE")
    if scene:
        lines.append(f"{scene['slug']} {scene.get('name') or ''}".strip())
        if scene.get("brief"):
            lines.append(f"brief: {scene['brief']}")
        if scene.get("environment_id"):
            env_name = scene["environment_id"]; env_desc = ""
            for json_path in list_talent_jsons(os.path.join(ELEMENTS_DIR, ELEMENT_TYPES["environment"]["folder"])) if os.path.isdir(os.path.join(ELEMENTS_DIR, ELEMENT_TYPES["environment"]["folder"])) else []:
                talent = load_talent_json(json_path)
                if talent and str(talent.get("id")) == str(scene["environment_id"]):
                    env_name = talent.get("name") or env_name; env_desc = talent.get("description") or ""
                    break
            lines.append(f"environment: {env_name}" + (f" - {env_desc}" if env_desc else ""))
        excerpt = _script_excerpt(scene.get("script") or "", shot)
        if excerpt:
            lines.append("script:\n" + excerpt)
    else:
        lines.append("(this shot has no scene)" + (f"; free-text scene label: {shot['scene']}" if shot.get("scene") else ""))

    lines.append(f"\n## SHOT {shot['slug']} (#{index + 1} of {len(shots)})")
    for key, label in (("action_text", "action"), ("dialogue", "dialogue"), ("audio_cue", "audio"), ("beat_marker", "beat")):
        if shot.get(key):
            lines.append(f"{label}: {shot[key]}")
    if shot.get("duration_seconds") not in (None, ""):
        lines.append(f"duration: {shot['duration_seconds']:g}s")
    lines.append("## CAMERA")
    camera = [f"{key}: {shot[key]}" for key in ("shot_size", "angle", "lens", "aperture", "speed_ramp") if shot.get(key)]
    if shot.get("movement"):
        camera.append("movement: " + ", ".join(shot["movement"]))
    lines.append("\n".join(camera) if camera else "(no camera fields set)")
    if shot.get("first_frame"):
        lines.append(f"first frame set: yes ({os.path.basename(str(shot['first_frame']))})")

    lines.append("\n## REFERENCES (numbered exactly as they are sent to the model)")
    if slots:
        for slot in slots:
            lines.append(f"reference {slot['position']}: [{slot['kind']}, role {slot['role']}] {slot['label']}")
    else:
        lines.append("(no references attached)")
    style = shot.get("style") if (shot.get("style_id") and shot.get("style_enabled", True)) else None
    lines.append("\n## STYLE")
    lines.append(f"{style.get('name')}: {style.get('text') or '(no text)'} - {len(style.get('images') or [])} image(s), appended after the other references" if style else "(no style applied)")

    lines.append("\n## NEIGHBORS (for continuity)")
    lines.append(f"previous {prev_shot['slug']}: {prev_shot.get('action_text') or '(no action text)'}" if prev_shot else "previous: (none - first shot)")
    lines.append(f"next {next_shot['slug']}: {next_shot.get('action_text') or '(no action text)'}" if next_shot else "next: (none - last shot)")

    lines.append("\n## CURRENT VALUES (drafts; you are writing a replacement, they need not be kept)")
    lines.append(f"scene_prompt: {shot.get('scene_prompt') or '(empty)'}")
    lines.append(f"prompt (motion): {shot.get('prompt') or '(empty)'}")
    return "\n".join(lines), slots


def compile_shot_prompt(shot_id, field: str) -> dict:
    """Returns a proposal. Never writes. Refuses a locked field outright."""
    if field not in COMPILE_FIELDS:
        raise ValueError("field must be prompt or scene_prompt")
    shot = get_shot(shot_id)
    if not shot:
        raise LookupError("Shot not found")
    if field in (shot.get("locked_fields") or []):
        raise PermissionError(f"{field} is locked. Unlock it in the editor to compile into it.")
    config = load_config()
    api_key = (config.get("openai_api_key") or "").strip()
    if not api_key:
        raise ValueError("No OpenAI API key is set. Add one in Settings.")
    project = get_project(shot["project_id"])
    context, slots = build_shot_compile_context(shot, project or {"id": shot["project_id"], "name": "", "settings": {}})
    instructions = str(config.get("agent_instructions") or "").strip()
    system_prompt = "\n\n".join(part for part in (instructions, COMPILER_RULES, "=== SHOT CONTEXT ===\n" + context) if part)
    user_prompt = f"{COMPILE_FIELDS[field]} Shot: {shot['slug']}."
    result = call_openai_chat(api_key, config.get("openai_model") or "gpt-4o-mini",
                              [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}])
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "OpenAI call failed")
    return {"field": field, "proposal": result["text"], "usage": result.get("usage") or {}, "context": context,
            "slots": slots, "system_tokens": estimate_tokens(system_prompt), "current": shot.get(field) or ""}


def previous_shot(shot: dict) -> dict | None:
    rows = fetch_shots(shot["project_id"])
    for index, row in enumerate(rows):
        if row["id"] == shot["id"]:
            return rows[index - 1] if index > 0 else None
    return None


def build_shot_render_payload(shot: dict, project: dict, body: dict | None = None) -> tuple[dict, list[str]]:
    """Assemble the same payload a Generator video run sends, from the shot row.
    Returns (payload, warnings). Raises ValueError when the shot cannot render."""
    body = body or {}
    warnings: list[str] = []
    engine = str(shot.get("engine") or "").strip()
    fallback = str(body.get("fallbackModel") or "").strip()
    if engine and engine not in VIDEO_MODELS_INFO:
        warnings.append(f"Engine '{engine}' is not a known video model; using the Generator's current model.")
        engine = ""
    model_id = engine or (fallback if fallback in VIDEO_MODELS_INFO else "")
    if not model_id:
        raise ValueError("No engine set on the shot and no video model selected in the Generator.")
    model_info = VIDEO_MODELS_INFO[model_id]
    modes = [str(mode).strip().lower() for mode in (model_info.get("input_modes") or [])]

    # References in array order: elements first, then reference assets.
    reference_urls: list[tuple[str, str]] = []
    for entry in shot.get("elements") or []:
        element_id = entry["ref"] if isinstance(entry, dict) else str(entry)
        if element_id.startswith(CAST_REF_PREFIX):
            # Task 13: a cast member contributes its dressed images, in order, at this position.
            member = get_cast_member(element_id[len(CAST_REF_PREFIX):])
            if not member:
                warnings.append(f"Cast member '{element_id}' no longer exists and was skipped.")
                continue
            tag = (member.get("handle") or element_id).lstrip("@")
            if not member.get("images"):
                warnings.append(f"{member.get('handle') or element_id} has no dressed images yet and was skipped.")
            for index, url in enumerate(member.get("images") or []):
                reference_urls.append((str(url), f"cast-{tag}-{index + 1}"))
            continue
        url = resolve_element_primary_image_url(element_id)
        if url:
            reference_urls.append((url, f"element-{element_id}"))
        else:
            warnings.append(f"Element '{element_id}' has no image and was skipped.")
    for index, entry in enumerate(shot.get("reference_assets") or []):
        url = entry["ref"] if isinstance(entry, dict) else str(entry)
        reference_urls.append((url, f"reference-{index + 1}"))
    # Task 10: an attached + enabled style appends after elements and reference assets.
    # A reference asset with role 'style' above is untouched; both may be present.
    style = shot.get("style") if (shot.get("style_id") and shot.get("style_enabled", True)) else None
    if style:
        for index, url in enumerate(style.get("images") or []):
            reference_urls.append((str(url), f"style-{index + 1}"))

    first_frame = str(shot.get("first_frame") or "").strip()
    last_frame = str(shot.get("last_frame") or "").strip()
    chain_image = body.get("chainStartImage") if isinstance(body.get("chainStartImage"), dict) else None
    if shot.get("chain_from_previous"):
        prev = previous_shot(shot)
        if not prev:
            raise ValueError("Chain from previous is set, but this is the first shot.")
        approved = (prev.get("takes") or {}).get("approved")
        if not approved:
            raise ValueError(f"Chain from previous: {prev['slug']} has no approved take. Approve one first, or turn chaining off.")
        if not chain_image or not str(chain_image.get("data") or "").strip():
            raise ValueError(f"Chain from previous: the last frame of {prev['slug']}'s approved take was not supplied.")
        if not model_info.get("supports_start_image"):
            raise ValueError(f"Chain from previous: {model_info.get('label', model_id)} cannot take a start image.")

    # One input mode per run (normalize_video_request): reference mode carries references
    # (+ start image where the model allows), image mode carries the start image only.
    wants_start = bool(chain_image) or bool(first_frame)
    if reference_urls and "reference" in modes:
        input_mode = "reference"
    elif wants_start and "image" in modes:
        input_mode = "image"
        if reference_urls:
            warnings.append(f"{model_info.get('label', model_id)} has no reference mode; {len(reference_urls)} reference(s) were not sent.")
    elif "text" in modes or not modes:
        input_mode = "text"
        if reference_urls:
            warnings.append(f"{model_info.get('label', model_id)} has no reference mode; {len(reference_urls)} reference(s) were not sent.")
        if wants_start:
            warnings.append(f"{model_info.get('label', model_id)} has no image mode; the start frame was not sent.")
    else:
        input_mode = modes[0]

    payload: dict = {
        "assetProjectId": project["id"],
        "assetClient": project.get("assetClient") or project.get("client") or "",
        "assetProject": project.get("assetProject") or project.get("name") or "",
        "assetShot": shot["slug"],
        "assetFilename": "take",
        "model": model_id,
        "modelFamily": model_info.get("family", ""),
        "provider": model_info.get("provider", ""),
        "videoInputMode": input_mode,
        "prompt": str(shot.get("prompt") or "").strip(),
        "negativePrompt": str(shot.get("negative_prompt") or "").strip(),
        "aspectRatio": _film_execution_aspect_ratio((project.get("settings") or {}).get("aspect_ratio"), {"default_aspect_ratio": "16:9"}),
        "resolution": str((model_info.get("resolutions") or ["720p"])[0]),
        "videoSafetyChecker": True,
        "videoOutputSafetyChecker": True,
        "videoGenerateAudio": False,
        "videoPromptExpansion": True,
        "sourceImage": {},
        "endImage": {},
        "referenceImages": [],
        "referenceVideos": [],
        "sourceVideo": {},
        "sourceAudio": {},
        "videoElements": [],
        "shotId": shot["id"],
        "shotSlug": shot["slug"],
    }
    # Task 16: a film shot with a scene files under client/project/<scene>/<shot>; no scene -> unchanged
    render_scene = get_scene(shot.get("scene_id")) if shot.get("scene_id") else None
    if render_scene:
        payload["assetScene"] = render_scene["slug"]
    if not payload["prompt"]:
        raise ValueError("The shot has no prompt. Write one in the Prompt section first.")
    if style and str(style.get("text") or "").strip():
        # Append, never replace: the shot's own prompt first, the style after it.
        payload["prompt"] = payload["prompt"] + "\n\n" + str(style["text"]).strip()

    durations = [int(x) for x in (model_info.get("durations") or []) if str(x).strip()]
    wanted = shot.get("duration_seconds")
    if wanted is not None and str(wanted).strip() != "":
        try:
            wanted_value = float(wanted)
        except (TypeError, ValueError):
            wanted_value = None
        if wanted_value is not None:
            payload["duration"] = min(durations, key=lambda d: abs(d - wanted_value)) if durations else int(round(wanted_value))
            if durations and payload["duration"] != wanted_value:
                warnings.append(f"Duration {wanted_value:g}s snapped to {payload['duration']}s for {model_info.get('label', model_id)}.")
    elif durations:
        payload["duration"] = durations[0]

    supports_start = bool(model_info.get("supports_start_image")) and input_mode != "text"
    if supports_start:
        if chain_image:
            payload["sourceImage"] = {"mime_type": str(chain_image.get("mime_type") or "image/png"), "data": str(chain_image.get("data") or ""), "name": "chain-start.png"}
        elif first_frame:
            payload["sourceImage"] = load_asset_image_payload(first_frame, "video-source.png")
    if bool(model_info.get("supports_end_image")) and input_mode != "text" and last_frame:
        payload["endImage"] = load_asset_image_payload(last_frame, "video-end-frame.png")
    if input_mode == "reference":
        max_refs = int(model_info.get("max_reference_images", 0) or 0)
        if max_refs and len(reference_urls) > max_refs:
            warnings.append(f"{model_info.get('label', model_id)} takes {max_refs} references; the last {len(reference_urls) - max_refs} were dropped.")
            reference_urls = reference_urls[:max_refs]
        payload["referenceImages"] = [load_asset_image_payload(url, f"{name}.png") for url, name in reference_urls]
    return payload, warnings


def summarize_render_payload(payload: dict) -> dict:
    """The payload with binary data replaced by sizes, for reports and the API response."""
    def strip(image):
        if not isinstance(image, dict) or not image.get("data"):
            return {} if isinstance(image, dict) else image
        return {k: v for k, v in image.items() if k != "data"} | {"data_bytes": len(image.get("data", "")) * 3 // 4}
    summary = dict(payload)
    summary["sourceImage"] = strip(payload.get("sourceImage"))
    summary["endImage"] = strip(payload.get("endImage"))
    summary["referenceImages"] = [strip(img) for img in payload.get("referenceImages") or []]
    return summary


def _run_shot_render_job(job_id: str, take_id: int, shot_id: int, payload: dict) -> None:
    """Caller of the existing video job runner; records the outcome on the take and the shot."""
    update_take(take_id, status="rendering")
    set_shot_status(shot_id, "rendering")
    try:
        _run_video_async_job(job_id, payload)
    except Exception as exc:  # the runner records its own failures; this is belt and braces
        update_async_job(job_id, status="failed", completed_at=utc_now_iso(), error=str(exc))
    job = get_async_job(job_id) or {}
    if job.get("status") == "completed":
        result = job.get("result") or {}
        videos = result.get("videos") or []
        first = videos[0] if videos else {}
        update_take(
            take_id,
            status="done",
            asset_path=str(first.get("url") or ""),
            poster_path=str(first.get("poster_url") or ""),
            cost=float(result.get("cost") or 0.0),
            engine=str((result.get("params") or {}).get("model") or payload.get("model") or ""),
            error="",
            completed_at=utc_now_iso(),
        )
        set_shot_status(shot_id, "done")
    else:
        update_take(take_id, status="failed", error=str(job.get("error") or "Render failed."), completed_at=utc_now_iso())
        set_shot_status(shot_id, "failed")


def start_shot_render(shot_id, body: dict | None = None) -> dict:
    shot = get_shot(shot_id)
    if not shot:
        raise LookupError("Shot not found")
    project = get_project(shot["project_id"])
    if not project or project.get("type") != "film":
        raise ValueError("Shots render only inside film projects")
    if (shot.get("takes") or {}).get("rendering"):
        raise ValueError("This shot is already rendering.")
    payload, warnings = build_shot_render_payload(shot, project, body)
    job = create_async_job("video")
    take = create_take(shot["id"], engine=payload["model"], prompt_sent=payload["prompt"], job_id=job["jobId"])
    set_shot_status(shot["id"], "queued")
    threading.Thread(
        target=_run_shot_render_job,
        args=(job["jobId"], take["id"], shot["id"], clone_jsonable(payload) or {}),
        daemon=True,
    ).start()
    return {"take": take, "job": job, "payload": summarize_render_payload(payload), "warnings": warnings, "shot": get_shot(shot["id"])}


def get_task_template(slug: str) -> dict | None:
    for template in fetch_task_templates():
        if template["slug"] == slug:
            return template
    return None


def _normalize_channels(raw_channels) -> list[str]:
    if isinstance(raw_channels, list):
        values = raw_channels
    else:
        values = str(raw_channels or "").split(",")
    cleaned = []
    for value in values:
        key = str(value).strip().lower()
        if key and key in WORKBENCH_CHANNEL_LABELS and key not in cleaned:
            cleaned.append(key)
    return cleaned


def _pick_aspect_ratio(channels: list[str], template: dict) -> str:
    if "stories_reels" in channels and len(channels) == 1:
        return "9:16"
    if any(ch in channels for ch in ("instagram_feed", "meta_ads")):
        return "4:5"
    if any(ch in channels for ch in ("website", "print")):
        return "16:9"
    return template.get("default_aspect_ratio", "1:1")


def _estimate_output_count(channels: list[str], automation_level: str) -> int:
    base = 4 + max(0, len(channels) - 1) * 2
    if automation_level == "aggressive":
        base += 4
    elif automation_level == "assisted":
        base -= 1
    return max(2, min(base, 16))


def _route_workbench_task(template: dict, channels: list[str], automation_level: str) -> dict:
    recommended_provider = template.get("default_provider", "gemini")
    execution_provider = "gemini"
    model = template.get("default_model", "gemini-3.1-flash-image-preview")
    workflow = template.get("default_workflow", "task-workbench-v1")
    notes = []

    if template["slug"] in ("ad_variation_batch", "product_hero"):
        notes.append("This task maps well to a hidden ComfyUI batch workflow once that adapter is added.")
    if template["slug"] == "editorial_lookbook":
        notes.append("Editorial work gets a higher-fidelity default model and larger output size.")
    if template["slug"] == "campaign_launch":
        notes.append("The route balances quality, speed, and reference-ready outputs for client review.")
    if automation_level == "aggressive" and template["slug"] in ("campaign_launch", "ad_variation_batch"):
        model = "gemini-3.1-flash-image-preview"
        notes.append("Automation is set high, so the route favors faster iteration over maximum fidelity.")
    if automation_level == "assisted" and template["slug"] in ("editorial_lookbook", "product_hero"):
        model = "gemini-3-pro-image-preview"
        notes.append("Assisted mode keeps the route closer to art-directed premium quality.")

    aspect_ratio = _pick_aspect_ratio(channels, template)
    image_size = template.get("default_image_size", "1K")
    temperature = float(template.get("default_temperature", 1.0))
    if automation_level == "aggressive":
        temperature = min(1.2, temperature + 0.05)
    elif automation_level == "assisted":
        temperature = max(0.75, temperature - 0.05)

    if aspect_ratio == "16:9" and image_size == "2K" and model == "gemini-3-pro-image-preview":
        notes.append("The current generator can execute this immediately, while a future workflow could expand into batch crops.")

    return {
        "recommended_provider": recommended_provider,
        "execution_provider": execution_provider,
        "model": model,
        "workflow": workflow,
        "aspect_ratio": aspect_ratio,
        "image_size": image_size,
        "temperature": round(temperature, 2),
        "top_p": 0.95,
        "notes": notes,
    }


def _build_prompt_from_brief(template: dict, route: dict, brief: dict) -> str:
    channels = brief.get("channels") or []
    channel_labels = [WORKBENCH_CHANNEL_LABELS.get(ch, ch) for ch in channels]
    sections = [
        template.get("prompt_scaffold", "Create a strong commercial visual."),
        f"Client: {brief.get('client_name') or 'Internal creative team'}.",
        f"Project: {brief.get('project_name') or 'Untitled project'}.",
        f"Objective: {brief.get('objective')}.",
    ]
    if brief.get("subject_summary"):
        sections.append(f"Subject and key assets: {brief['subject_summary']}.")
    if brief.get("vibe"):
        sections.append(f"Creative direction and vibe: {brief['vibe']}.")
    if channel_labels:
        sections.append(
            f"Design for these channels: {', '.join(channel_labels)}. Prefer an aspect ratio of {route['aspect_ratio']}."
        )
    sections.append(
        "The image should feel commercially usable, visually clean, and immediately understandable without extra explanation."
    )
    if brief.get("constraints_summary"):
        sections.append(f"Hard constraints: {brief['constraints_summary']}.")

    automation_level = brief.get("automation_level", "balanced")
    if automation_level == "aggressive":
        sections.append("Push for bold variation, strong hooks, and obvious first-read impact.")
    elif automation_level == "assisted":
        sections.append("Stay close to polished brand-safe art direction and avoid unnecessary stylistic risk.")
    else:
        sections.append("Balance originality with production practicality and brand readability.")

    sections.append("Use realistic lighting, coherent materials, and a production-ready sense of composition.")
    return "\n\n".join(sections)


# Cinema ratios the image models cannot take verbatim -> nearest ratio the generator offers.
FILM_ASPECT_RATIO_EXECUTION_MAP = {
    "2.39:1": "21:9", "2.35:1": "21:9", "2.40:1": "21:9", "2.4:1": "21:9", "2.37:1": "21:9",
    "1.85:1": "16:9", "1.78:1": "16:9", "1.77:1": "16:9", "1.66:1": "3:2", "1.5:1": "3:2",
    "1.33:1": "4:3", "1.37:1": "4:3", "1:1": "1:1", "9:16": "9:16", "4:5": "4:5",
}
GENERATOR_ASPECT_RATIOS = ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9", "5:4", "4:5", "4:1", "1:4", "8:1", "1:8")


def _film_execution_aspect_ratio(project_aspect: str, template: dict) -> str:
    text = str(project_aspect or "").strip()
    if text in GENERATOR_ASPECT_RATIOS:
        return text
    mapped = FILM_ASPECT_RATIO_EXECUTION_MAP.get(text)
    if mapped:
        return mapped
    return template.get("default_aspect_ratio", "16:9")


def _film_sentence(label: str, text: str) -> str:
    text = str(text or "").strip()
    return f"{label}: {text}" + ("" if text.endswith((".", "!", "?", "…")) else ".")


def _build_film_prompt_from_brief(template: dict, route: dict, brief: dict, project: dict) -> str:
    settings = project.get("settings") or {}
    sections = [
        template.get("prompt_scaffold", "Create a strong film frame."),
        f"Client: {brief.get('client_name') or 'Internal production'}.",
        f"Project: {brief.get('project_name') or project.get('name') or 'Untitled film'}.",
    ]
    film_facts = []
    if settings.get("format"):
        film_facts.append(f"format {settings['format']}")
    if settings.get("runtime"):
        film_facts.append(f"runtime {settings['runtime']}")
    if settings.get("frame_rate"):
        film_facts.append(f"{settings['frame_rate']} fps")
    if film_facts:
        sections.append(f"Film: {', '.join(film_facts)}.")
    if settings.get("register"):
        sections.append(f"Register: {settings['register']}.")
    sections.append(_film_sentence("Objective", brief.get("objective")))
    if brief.get("scene"):
        sections.append(_film_sentence("Scene", brief["scene"]))
    if brief.get("treatment"):
        sections.append(_film_sentence("Treatment", brief["treatment"]))
    if brief.get("subject_summary"):
        sections.append(_film_sentence("Subject and key assets", brief["subject_summary"]))
    if brief.get("vibe"):
        sections.append(_film_sentence("Creative direction and vibe", brief["vibe"]))
    if settings.get("aspect_ratio"):
        sections.append(f"Frame for {settings['aspect_ratio']}.")
    sections.append(
        "The frame should read as a still from the film: motivated light, coherent production design, and a clear dramatic beat."
    )
    if brief.get("constraints_summary"):
        sections.append(_film_sentence("Hard constraints", brief["constraints_summary"]))

    automation_level = brief.get("automation_level", "balanced")
    if automation_level == "aggressive":
        sections.append("Push the staging: bold blocking, decisive camera, strong contrast between beats.")
    elif automation_level == "assisted":
        sections.append("Stay close to the script and the register; avoid stylistic risk the director has not asked for.")
    else:
        sections.append("Balance authorship with what a crew can actually shoot.")

    sections.append("Use realistic lighting, coherent materials, and a production-ready sense of composition.")
    return "\n\n".join(sections)


def build_workbench_plan(body: dict) -> dict:
    task_slug = str(body.get("task_slug", "campaign_launch")).strip()
    template = get_task_template(task_slug)
    if not template:
        raise ValueError("Invalid task template")

    objective = str(body.get("objective", "")).strip()
    if not objective:
        raise ValueError("Objective is required")

    # Active project record (Task 02): its type decides campaign vs film planning.
    project = get_project(body.get("assetProjectId")) if body.get("assetProjectId") not in (None, "") else None
    is_film = bool(project and project.get("type") == "film")
    template_type = template.get("project_type") or "campaign"
    if is_film and template_type != "film":
        raise ValueError("Choose a film template for a film project")
    if template_type == "film" and not is_film:
        raise ValueError("Film templates need a film project selected in the scope bar")

    brief = {
        "client_name": str(body.get("client_name", "")).strip(),
        "project_name": str(body.get("project_name", "")).strip(),
        "objective": objective,
        "subject_summary": str(body.get("subject_summary", "")).strip(),
        "vibe": str(body.get("vibe", "")).strip(),
        "constraints_summary": str(body.get("constraints_summary", "")).strip(),
        "automation_level": str(body.get("automation_level", "balanced")).strip() or "balanced",
        "channels": [] if is_film else _normalize_channels(body.get("channels", [])),
    }
    if is_film:
        brief["scene"] = str(body.get("scene", "")).strip()
        brief["treatment"] = str(body.get("treatment", "")).strip()
        brief["project_id"] = project["id"]
        brief["project_type"] = "film"
    elif not brief["channels"]:
        brief["channels"] = ["instagram_feed"]

    route = _route_workbench_task(template, brief["channels"], brief["automation_level"])
    if is_film:
        route["aspect_ratio"] = _film_execution_aspect_ratio((project.get("settings") or {}).get("aspect_ratio"), template)
        prompt_text = _build_film_prompt_from_brief(template, route, brief, project)
    else:
        prompt_text = _build_prompt_from_brief(template, route, brief)
    estimated_outputs = _estimate_output_count(brief["channels"], brief["automation_level"])
    price_per_image = PRICING.get(route["model"], {}).get(route["image_size"], 0.0)
    est_low = round(price_per_image * max(2, estimated_outputs // 2), 4)
    est_high = round(price_per_image * estimated_outputs, 4)

    reasoning = [
        f"{template['name']} gives the user a task-first starting point instead of a blank prompt box.",
        f"Recommended workflow: {route['workflow']}.",
        f"Primary route: {route['recommended_provider']} with immediate execution through {route['execution_provider']}.",
    ] + route["notes"]

    return {
        "task_slug": template["slug"],
        "task_name": template["name"],
        "description": template["description"],
        "project_id": project["id"] if project else None,
        "project_type": project["type"] if project else "",
        "brief": brief,
        "recommended_provider": route["recommended_provider"],
        "recommended_workflow": route["workflow"],
        "execution_target": {
            "provider": route["execution_provider"],
            "model": route["model"],
            "model_label": MODELS_INFO.get(route["model"], {}).get("label", route["model"]),
            "aspectRatio": route["aspect_ratio"],
            "imageSize": route["image_size"],
            "temperature": route["temperature"],
            "topP": route["top_p"],
        },
        "estimated_outputs": estimated_outputs,
        "estimated_cost_range_usd": {
            "low": est_low,
            "high": est_high,
        },
        "reasoning": reasoning,
        "prompt": prompt_text,
    }


def save_task_run(plan: dict) -> dict:
    init_studio_db()
    brief = plan.get("brief", {})
    execution = plan.get("execution_target", {})
    run_uuid = plan.get("run_uuid") or str(uuid4())
    plan["run_uuid"] = run_uuid
    conn = get_db_connection()
    conn.execute(
        """
        INSERT OR REPLACE INTO task_runs (
            run_uuid, created_at, client_name, project_name, task_slug, task_name,
            objective, channels_json, vibe, subject_summary, constraints_summary,
            automation_level, recommended_provider, execution_provider,
            recommended_model, recommended_workflow, aspect_ratio, image_size,
            prompt_text, estimated_outputs, estimated_cost_low, estimated_cost_high,
            status, run_count, actual_images, actual_cost_usd, actual_model,
            actual_provider, last_run_at, last_error, plan_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_uuid,
            plan.get("created_at") or utc_now_iso(),
            brief.get("client_name", ""),
            brief.get("project_name", ""),
            plan.get("task_slug", ""),
            plan.get("task_name", ""),
            brief.get("objective", ""),
            json.dumps(brief.get("channels", [])),
            brief.get("vibe", ""),
            brief.get("subject_summary", ""),
            brief.get("constraints_summary", ""),
            brief.get("automation_level", "balanced"),
            plan.get("recommended_provider", ""),
            execution.get("provider", ""),
            execution.get("model", ""),
            plan.get("recommended_workflow", ""),
            execution.get("aspectRatio", ""),
            execution.get("imageSize", ""),
            plan.get("prompt", ""),
            int(plan.get("estimated_outputs", 0)),
            float(plan.get("estimated_cost_range_usd", {}).get("low", 0.0)),
            float(plan.get("estimated_cost_range_usd", {}).get("high", 0.0)),
            plan.get("status", "planned"),
            int(plan.get("run_count", 0)),
            int(plan.get("actual_images", 0)),
            float(plan.get("actual_cost_usd", 0.0)),
            plan.get("actual_model", execution.get("model", "")),
            plan.get("actual_provider", execution.get("provider", "")),
            plan.get("last_run_at", ""),
            plan.get("last_error", ""),
            json.dumps(plan, ensure_ascii=False),
        ),
    )
    conn.commit()
    conn.close()
    return plan


def get_task_run(run_uuid: str) -> dict | None:
    init_studio_db()
    conn = get_db_connection()
    row = conn.execute(
        "SELECT * FROM task_runs WHERE run_uuid = ? LIMIT 1",
        (run_uuid,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    item = dict(row)
    plan_json = item.get("plan_json") or ""
    if plan_json:
        try:
            item["plan"] = json.loads(plan_json)
        except json.JSONDecodeError:
            item["plan"] = None
    else:
        item["plan"] = None
    return item


def update_task_run_after_generation(run_uuid: str, generation_result: dict | None = None, error: str = ""):
    init_studio_db()
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM task_runs WHERE run_uuid = ? LIMIT 1", (run_uuid,)).fetchone()
    if not row:
        conn.close()
        return
    item = dict(row)
    run_count = int(item.get("run_count") or 0)
    actual_images = int(item.get("actual_images") or 0)
    actual_cost = float(item.get("actual_cost_usd") or 0.0)
    now_ts = utc_now_iso()
    if generation_result is not None:
        actual_images += len(generation_result.get("images", []))
        actual_cost += float(generation_result.get("cost", 0.0) or 0.0)
        status = "completed"
        last_error = ""
        actual_model = generation_result.get("params", {}).get("model", item.get("recommended_model", ""))
        actual_provider = item.get("execution_provider", "gemini")
    else:
        status = "failed"
        last_error = error[:500]
        actual_model = item.get("actual_model", item.get("recommended_model", ""))
        actual_provider = item.get("actual_provider", item.get("execution_provider", "gemini"))
    conn.execute(
        """
        UPDATE task_runs
        SET status = ?, run_count = ?, actual_images = ?, actual_cost_usd = ?,
            actual_model = ?, actual_provider = ?, last_run_at = ?, last_error = ?
        WHERE run_uuid = ?
        """,
        (
            status,
            run_count + 1,
            actual_images,
            round(actual_cost, 6),
            actual_model,
            actual_provider,
            now_ts,
            last_error,
            run_uuid,
        ),
    )
    conn.commit()
    conn.close()


def get_workbench_report() -> dict:
    init_studio_db()
    conn = get_db_connection()
    summary_row = conn.execute(
        """
        SELECT COUNT(*) AS total_runs,
               COUNT(DISTINCT NULLIF(client_name, '')) AS total_clients,
               COALESCE(SUM(estimated_cost_low), 0) AS total_cost_low,
               COALESCE(SUM(estimated_cost_high), 0) AS total_cost_high,
               COALESCE(SUM(estimated_outputs), 0) AS total_outputs,
               COALESCE(SUM(actual_images), 0) AS actual_images,
               COALESCE(SUM(actual_cost_usd), 0) AS actual_cost_usd,
               SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_runs,
               SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_runs,
               SUM(CASE WHEN run_count > 0 THEN 1 ELSE 0 END) AS executed_runs
        FROM task_runs
        """
    ).fetchone()
    task_breakdown = [
        dict(row) for row in conn.execute(
            "SELECT task_name, COUNT(*) AS runs FROM task_runs GROUP BY task_name ORDER BY runs DESC, task_name ASC LIMIT 6"
        ).fetchall()
    ]
    provider_breakdown = [
        dict(row) for row in conn.execute(
            "SELECT COALESCE(actual_provider, recommended_provider) AS provider, COUNT(*) AS runs "
            "FROM task_runs GROUP BY provider ORDER BY runs DESC, provider ASC LIMIT 6"
        ).fetchall()
    ]
    client_breakdown = [
        dict(row) for row in conn.execute(
            "SELECT CASE WHEN NULLIF(client_name, '') IS NULL THEN 'Unassigned' ELSE client_name END AS client_name, COUNT(*) AS runs "
            "FROM task_runs GROUP BY client_name ORDER BY runs DESC, client_name ASC LIMIT 6"
        ).fetchall()
    ]
    workflow_breakdown = [
        dict(row) for row in conn.execute(
            "SELECT recommended_workflow, COUNT(*) AS runs FROM task_runs GROUP BY recommended_workflow ORDER BY runs DESC, recommended_workflow ASC LIMIT 6"
        ).fetchall()
    ]
    recent_runs = []
    for row in conn.execute(
        """
        SELECT created_at, client_name, project_name, task_name, recommended_provider,
               recommended_workflow, recommended_model, estimated_outputs,
               estimated_cost_low, estimated_cost_high, status, run_count,
               actual_images, actual_cost_usd, last_run_at, last_error, run_uuid
        FROM task_runs
        ORDER BY id DESC
        LIMIT 12
        """
    ).fetchall():
        item = dict(row)
        created = item.get("created_at", "")
        item["created_at_label"] = created[:16].replace("T", " ") if created else ""
        last_run_at = item.get("last_run_at", "")
        item["last_run_at_label"] = last_run_at[:16].replace("T", " ") if last_run_at else ""
        recent_runs.append(item)
    conn.close()
    return {
        "summary": {
            "total_runs": int(summary_row["total_runs"] or 0),
            "total_clients": int(summary_row["total_clients"] or 0),
            "total_outputs": int(summary_row["total_outputs"] or 0),
            "total_cost_low": round(float(summary_row["total_cost_low"] or 0.0), 4),
            "total_cost_high": round(float(summary_row["total_cost_high"] or 0.0), 4),
            "executed_runs": int(summary_row["executed_runs"] or 0),
            "completed_runs": int(summary_row["completed_runs"] or 0),
            "failed_runs": int(summary_row["failed_runs"] or 0),
            "actual_images": int(summary_row["actual_images"] or 0),
            "actual_cost_usd": round(float(summary_row["actual_cost_usd"] or 0.0), 4),
        },
        "task_breakdown": task_breakdown,
        "provider_breakdown": provider_breakdown,
        "client_breakdown": client_breakdown,
        "workflow_breakdown": workflow_breakdown,
        "recent_runs": recent_runs,
    }
def talent_json_dir(folder_path: str) -> str:
    """Return the effective JSON directory for a talent folder.
    For 'Model Managment' use the json/ subfolder; for other folders use the root.
    """
    if os.path.basename(folder_path) == "Model Managment":
        return os.path.join(folder_path, TALENT_JSON_SUBDIR)
    return folder_path


def talent_json_path(folder_path: str, talent_id: str) -> str:
    """Path to the individual JSON file for a talent."""
    return os.path.join(talent_json_dir(folder_path), f"{talent_id}.json")


def load_talent_json(json_path: str) -> dict | None:
    """Load an individual talent JSON; returns None if missing/invalid."""
    try:
        with open(json_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_talent_json(json_path: str, data: dict):
    """Save talent data to the JSON file (creating parent dirs when needed)."""
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Helper - image normalization
#   Ã¢â‚¬Â¢ Se larghezza > MAX_IMG_WIDTH: ridimensiona mantenendo aspect ratio
#   - Always converts to JPG with JPEG_QUALITY quality
#   Ã¢â‚¬Â¢ Ritorna (b64_string, "image/jpeg", orig_w, orig_h, new_w, new_h)
# ---------------------------------------------------------------------------
MAX_IMG_WIDTH  = 4000   # px sulla dimensione orizzontale
JPEG_QUALITY   = 90     # JPG output quality %
SEEDREAM_MAX_INPUT_PIXELS = 36_000_000
SEEDREAM_MAX_INPUT_BYTES = 10 * 1024 * 1024
SEEDREAM_TARGET_INPUT_BYTES = int(SEEDREAM_MAX_INPUT_BYTES * 0.92)
VIDEO_MAX_INPUT_PIXELS = 36_000_000
VIDEO_MAX_INPUT_BYTES = 10 * 1024 * 1024
VIDEO_TARGET_INPUT_BYTES = int(VIDEO_MAX_INPUT_BYTES * 0.92)
REMOTE_REF_FETCH_MAX_BYTES = 30 * 1024 * 1024
MAX_REFERENCE_IMAGE_PIXELS = 5504 * 3072


def open_base64_image(image_b64: str) -> tuple[Image.Image, dict]:
    raw = base64.b64decode(image_b64)
    img = Image.open(io.BytesIO(raw))
    img.load()
    info = dict(getattr(img, "info", {}) or {})
    img = ImageOps.exif_transpose(img)
    return img, info


def flatten_image_for_jpeg(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(background, rgba).convert("RGB")
    if img.mode not in ("RGB", "L"):
        return img.convert("RGB")
    return img


def normalize_image_b64(image_b64: str, mime_type: str) -> tuple:
    """
    Process a base64 image:
      - se larghezza > MAX_IMG_WIDTH -> ridimensiona a MAX_IMG_WIDTH (mantiene ratio)
      - converte in JPG a JPEG_QUALITY
    Returns (processed_b64, "image/jpeg", orig_w, orig_h, final_w, final_h, resized: bool)
    """
    img, info = open_base64_image(image_b64)
    img = flatten_image_for_jpeg(img)

    orig_w, orig_h = img.size
    resized = False

    if orig_w > MAX_IMG_WIDTH:
        ratio = MAX_IMG_WIDTH / orig_w
        new_w = MAX_IMG_WIDTH
        new_h = int(orig_h * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        resized = True
    else:
        new_w, new_h = orig_w, orig_h

    buf = io.BytesIO()
    save_kwargs = {"format": "JPEG", "quality": JPEG_QUALITY, "optimize": True}
    icc_profile = info.get("icc_profile")
    if icc_profile:
        save_kwargs["icc_profile"] = icc_profile
    img.save(buf, **save_kwargs)
    buf.seek(0)
    b64_out = base64.b64encode(buf.read()).decode("utf-8")

    return b64_out, "image/jpeg", orig_w, orig_h, new_w, new_h, resized


def convert_image_b64_to_png(image_b64: str, mime_type: str) -> tuple[str, str]:
    """Convert a base64 image payload to PNG while preserving alpha when present."""
    img, info = open_base64_image(image_b64)

    has_alpha = ("A" in img.getbands()) or (img.mode == "P" and "transparency" in img.info)
    if has_alpha:
        if img.mode != "RGBA":
            img = img.convert("RGBA")
    elif img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    buf = io.BytesIO()
    save_kwargs = {"format": "PNG", "optimize": True}
    icc_profile = info.get("icc_profile")
    if icc_profile:
        save_kwargs["icc_profile"] = icc_profile
    img.save(buf, **save_kwargs)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8"), "image/png"


def name_to_slug(name: str) -> str:
    """Converte nome in slug (lowercase, underscore, senza accenti)."""
    slug = unicodedata.normalize("NFKD", name.lower())
    slug = slug.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^\w\s-]", "", slug).strip()
    slug = re.sub(r"[\s\-]+", "_", slug)
    return slug or "talent"



def get_next_image_number(folder_path: str, slug: str) -> int:
    """Return the next incremental number for slug_NNN.ext."""
    pattern = re.compile(
        r"^" + re.escape(slug) + r"_(\d+)\.(jpg|jpeg|png|webp)$",
        re.IGNORECASE
    )
    max_n = 0
    for fname in os.listdir(folder_path):
        m = pattern.match(fname)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return max_n + 1


def list_talent_jsons(folder_path: str) -> list[str]:
    """List all individual talent JSON files.
    For 'Model Managment' search in json/; otherwise search in the folder root.
    """
    excluded = {"catalog.json", "catalog.json.bak"}
    search_dir = talent_json_dir(folder_path)
    return [
        p for p in glob.glob(os.path.join(search_dir, "*.json"))
        if os.path.basename(p) not in excluded
    ]


MAX_SEED_VALUE = 2147483647


def normalize_seed_mode(value) -> str:
    mode = str(value or "random").strip().lower()
    return mode if mode in {"fixed", "random", "incremental"} else "random"


def coerce_seed_value(value) -> int:
    try:
        seed = int(str(value).strip())
    except Exception:
        seed = random.randint(1, MAX_SEED_VALUE)
    return max(1, min(seed, MAX_SEED_VALUE))


def load_reference_archive_index() -> dict[str, dict]:
    if not os.path.exists(REFERENCE_ARCHIVE_INDEX_FILE):
        return {}
    try:
        with open(REFERENCE_ARCHIVE_INDEX_FILE, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def save_reference_archive_index(index: dict[str, dict]) -> None:
    os.makedirs(REFERENCE_ARCHIVE_DIR, exist_ok=True)
    with open(REFERENCE_ARCHIVE_INDEX_FILE, "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2, ensure_ascii=False)


def compute_reference_archive_hash(source_png_b64: str, mask_png_b64: str = "") -> str:
    digest = hashlib.sha256()
    try:
        digest.update(base64.b64decode(source_png_b64))
    except Exception:
        digest.update(str(source_png_b64 or "").encode("utf-8"))
    digest.update(b"\n--mask--\n")
    if mask_png_b64:
        try:
            digest.update(base64.b64decode(mask_png_b64))
        except Exception:
            digest.update(str(mask_png_b64 or "").encode("utf-8"))
    return digest.hexdigest()


def get_reference_archive_index_entry(ref_hash: str, index: dict[str, dict] | None = None) -> dict | None:
    current_index = index if isinstance(index, dict) else load_reference_archive_index()
    raw_entry = current_index.get(str(ref_hash or "").strip())
    if not isinstance(raw_entry, dict):
        return None
    ref_date = str(raw_entry.get("date", "") or "").strip()
    filename = os.path.basename(str(raw_entry.get("filename", "") or "").strip())
    if not ref_date or not filename:
        return None
    archive_path = os.path.join(REFERENCE_ARCHIVE_DIR, ref_date, filename)
    if not os.path.exists(archive_path):
        current_index.pop(str(ref_hash or "").strip(), None)
        save_reference_archive_index(current_index)
        return None
    return {
        "hash": str(ref_hash or "").strip(),
        "date": ref_date,
        "filename": filename,
        "name": str(raw_entry.get("name", "") or filename),
        "mime_type": str(raw_entry.get("mime_type", "image/png") or "image/png"),
    }


def upsert_reference_archive_index_entry(ref_hash: str, entry: dict, index: dict[str, dict] | None = None) -> None:
    normalized_hash = str(ref_hash or "").strip()
    if not normalized_hash:
        return
    current_index = index if isinstance(index, dict) else load_reference_archive_index()
    current_index[normalized_hash] = {
        "date": str(entry.get("date", "") or "").strip(),
        "filename": os.path.basename(str(entry.get("filename", "") or "").strip()),
        "name": str(entry.get("name", "") or "").strip(),
        "mime_type": str(entry.get("mime_type", "image/png") or "image/png"),
        "updated_at": utc_now_iso(),
    }
    if current_index is not index:
        save_reference_archive_index(current_index)


def remove_reference_archive_index_entries(date_str: str, filename: str, index: dict[str, dict] | None = None) -> None:
    safe_date = str(date_str or "").strip()
    safe_filename = os.path.basename(str(filename or "").strip())
    if not safe_date or not safe_filename:
        return
    current_index = index if isinstance(index, dict) else load_reference_archive_index()
    stale_keys = []
    for key, value in current_index.items():
        if not isinstance(value, dict):
            stale_keys.append(key)
            continue
        value_date = str(value.get("date", "") or "").strip()
        value_filename = os.path.basename(str(value.get("filename", "") or "").strip())
        if value_date == safe_date and value_filename == safe_filename:
            stale_keys.append(key)
    for key in stale_keys:
        current_index.pop(key, None)
    if stale_keys and current_index is not index:
        save_reference_archive_index(current_index)


def meta_uses_reference_archive_entry(meta: dict, date_str: str, filename: str) -> bool:
    if not isinstance(meta, dict):
        return False
    safe_date = str(date_str or "").strip()
    safe_filename = os.path.basename(str(filename or "").strip())
    if not safe_date or not safe_filename:
        return False

    archive_groups = []
    ref_archive = meta.get("refArchive")
    if isinstance(ref_archive, list):
        archive_groups.append(ref_archive)
    video_ref_archive = meta.get("videoRefArchive")
    if isinstance(video_ref_archive, list):
        archive_groups.append(video_ref_archive)
    video_source_archive = meta.get("videoSourceArchive")
    if isinstance(video_source_archive, dict):
        archive_groups.append([video_source_archive])

    for group in archive_groups:
        for ref in group:
            if not isinstance(ref, dict):
                continue
            ref_date = str(ref.get("date", "") or "").strip()
            ref_name = os.path.basename(str(ref.get("filename", "") or "").strip())
            if ref_date == safe_date and ref_name == safe_filename:
                return True
    return False


def is_reference_archive_entry_still_used(date_str: str, filename: str) -> bool:
    safe_date = str(date_str or "").strip()
    safe_filename = os.path.basename(str(filename or "").strip())
    if not safe_date or not safe_filename:
        return False
    for root_dir in (GENERATIONS_DIR, VIDEOS_DIR):
        if not os.path.isdir(root_dir):
            continue
        for meta_file in list_meta_files_recursive(root_dir):
            try:
                with open(meta_file, encoding="utf-8") as fh:
                    meta = json.load(fh)
            except Exception:
                continue
            if meta_uses_reference_archive_entry(meta, safe_date, safe_filename):
                return True
    return False


def normalize_reference_recovery_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def iter_candidate_reference_source_files() -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    search_roots = (
        GENERATIONS_DIR,
        VIDEOS_DIR,
        os.path.join(ELEMENTS_DIR, "Model Managment"),
        REFERENCE_ARCHIVE_DIR,
    )
    allowed_exts = {".png", ".jpg", ".jpeg", ".webp"}
    for root_dir in search_roots:
        if not os.path.isdir(root_dir):
            continue
        for current_root, _, filenames in os.walk(root_dir):
            for name in filenames:
                ext = os.path.splitext(name)[1].lower()
                if ext not in allowed_exts:
                    continue
                path = os.path.join(current_root, name)
                if path in seen:
                    continue
                seen.add(path)
                candidates.append(path)
    return candidates


def find_reference_recovery_source(filename: str) -> tuple[str, str] | tuple[None, None]:
    safe_filename = os.path.basename(str(filename or "").strip())
    stem = os.path.splitext(safe_filename)[0]
    stem = re.sub(r"^\d+_ref_\d+_[0-9a-f]{10}_", "", stem)
    search_key = normalize_reference_recovery_key(stem)
    if not search_key:
        return None, None

    best_path = None
    best_name = None
    best_score = -1
    best_delta = 10**9
    for path in iter_candidate_reference_source_files():
        base_name = os.path.basename(path)
        base_stem = os.path.splitext(base_name)[0]
        candidate_key = normalize_reference_recovery_key(base_stem)
        if not candidate_key:
            continue
        score = -1
        if candidate_key == search_key:
            score = 100
        elif candidate_key.startswith(search_key) or search_key.startswith(candidate_key):
            score = 80
        elif search_key in candidate_key or candidate_key in search_key:
            score = 60
        if score < 0:
            continue
        delta = abs(len(candidate_key) - len(search_key))
        if score < best_score:
            continue
        if score == best_score and delta >= best_delta:
            continue
        best_score = score
        best_delta = delta
        best_path = path
        best_name = base_name
    if best_path and best_score >= 60:
        return best_path, best_name
    return None, None


def build_reference_payload_from_file(file_path: str, display_name: str = "") -> dict:
    with open(file_path, "rb") as fh:
        payload_b64 = base64.b64encode(fh.read()).decode("utf-8")
    mime_type = "image/png"
    ext = os.path.splitext(file_path)[1].lower()
    if ext in {".jpg", ".jpeg"}:
        mime_type = "image/jpeg"
    elif ext == ".webp":
        mime_type = "image/webp"
    return {
        "ok": True,
        "name": display_name or os.path.basename(file_path),
        "mime_type": mime_type,
        "data": payload_b64,
        "original_data": payload_b64,
        "original_mime_type": mime_type,
        "mask_png_data": "",
        "original_url": "",
        "masked_url": "",
        "mask_url": "",
        "has_mask": False,
        "recovered": True,
    }


def compute_reference_archive_file_hash(date_str: str, filename: str) -> str:
    paths = get_reference_mask_file_paths(date_str, filename)
    if not os.path.exists(paths["original_path"]):
        return ""
    try:
        digest = hashlib.sha256()
        with open(paths["original_path"], "rb") as fh:
            digest.update(fh.read())
        digest.update(b"\n--mask--\n")
        if os.path.exists(paths["mask_path"]):
            with open(paths["mask_path"], "rb") as fh:
                digest.update(fh.read())
        return digest.hexdigest()
    except Exception:
        return ""


def build_reference_archive_entries(ref_images: list[dict], date_str: str, time_prefix: str) -> list[dict]:
    if not ref_images:
        return []
    archive_day_dir = os.path.join(REFERENCE_ARCHIVE_DIR, date_str)
    os.makedirs(archive_day_dir, exist_ok=True)
    archived = []
    archive_index = load_reference_archive_index()
    index_changed = False
    for idx, img in enumerate(ref_images):
        if not isinstance(img, dict):
            continue
        img_b64 = str(img.get("data", "") or "").strip()
        if not img_b64:
            continue
        source_b64 = str(img.get("original_data", "") or "").strip()
        mime_type = str(img.get("original_mime_type", img.get("mime_type", "image/png")) or "image/png")
        existing_date = str(img.get("archive_date", "") or "").strip()
        existing_filename = os.path.basename(str(img.get("archive_filename", "") or "").strip())
        existing_path = os.path.join(REFERENCE_ARCHIVE_DIR, existing_date, existing_filename) if existing_date and existing_filename else ""
        mask_png_data = str(img.get("mask_png_data", "") or "").strip()

        # Reusing an already-archived reference should keep pointing at the same
        # archive entry unless the user explicitly edited the mask/source data.
        if existing_path and os.path.exists(existing_path) and not mask_png_data:
            existing_hash = compute_reference_archive_file_hash(existing_date, existing_filename)
            if existing_hash and not get_reference_archive_index_entry(existing_hash, archive_index):
                upsert_reference_archive_index_entry(existing_hash, {
                    "date": existing_date,
                    "filename": existing_filename,
                    "name": str(img.get("name", "") or existing_filename),
                    "mime_type": "image/png",
                }, archive_index)
                index_changed = True
            existing_entry = enrich_reference_archive_entry({
                "date": existing_date,
                "filename": existing_filename,
                "name": str(img.get("name", "") or existing_filename),
                "mime_type": "image/png",
            })
            archived.append(existing_entry)
            continue

        if not source_b64:
            source_date = existing_date
            source_filename = existing_filename
            source_path = os.path.join(REFERENCE_ARCHIVE_DIR, source_date, source_filename) if source_date and source_filename else ""
            if source_path and os.path.exists(source_path):
                try:
                    with open(source_path, "rb") as fh:
                        source_b64 = base64.b64encode(fh.read()).decode("utf-8")
                    mime_type = "image/png"
                except Exception:
                    source_b64 = ""
        if not source_b64:
            source_b64 = img_b64
        try:
            png_b64, png_mime = convert_image_b64_to_png(source_b64, mime_type)
        except Exception:
            continue
        ref_hash = compute_reference_archive_hash(png_b64, mask_png_data)
        indexed_entry = get_reference_archive_index_entry(ref_hash, archive_index)
        if indexed_entry:
            # Keep mask assets in sync if this run supplied a fresh mask payload.
            if mask_png_data:
                try:
                    save_reference_mask_assets(indexed_entry["date"], indexed_entry["filename"], mask_png_data)
                except Exception:
                    pass
            archived.append(enrich_reference_archive_entry(indexed_entry))
            continue

        original_name = os.path.basename(str(img.get("name", "") or f"reference-{idx + 1}.png"))
        stem = os.path.splitext(original_name)[0] or f"reference-{idx + 1}"
        safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._") or f"reference-{idx + 1}"
        filename = f"{time_prefix}_ref_{idx + 1}_{ref_hash[:10]}_{safe_stem}.png"
        archive_path = os.path.join(archive_day_dir, filename)
        with open(archive_path, "wb") as fh:
            fh.write(base64.b64decode(png_b64))
        if mask_png_data:
            try:
                save_reference_mask_assets(date_str, filename, mask_png_data)
            except Exception:
                pass
        archive_entry = {
            "date": date_str,
            "filename": filename,
            "name": original_name,
            "mime_type": png_mime,
        }
        upsert_reference_archive_index_entry(ref_hash, archive_entry, archive_index)
        index_changed = True
        archived.append(enrich_reference_archive_entry(archive_entry))
    if index_changed:
        save_reference_archive_index(archive_index)
    return archived


def delete_reference_archive_entries(entries: list[dict]):
    safe_root = os.path.realpath(REFERENCE_ARCHIVE_DIR)
    seen = set()
    for item in entries or []:
        if not isinstance(item, dict):
            continue
        date_str = str(item.get("date", "") or "").strip()
        filename = os.path.basename(str(item.get("filename", "") or "").strip())
        if not date_str or not filename:
            continue
        ref_key = f"{date_str}/{filename}"
        if ref_key in seen:
            continue
        seen.add(ref_key)
        archive_path = os.path.realpath(os.path.join(REFERENCE_ARCHIVE_DIR, date_str, filename))
        if not archive_path.startswith(safe_root + os.sep):
            continue
        if is_reference_archive_entry_still_used(date_str, filename):
            continue
        if os.path.exists(archive_path):
            try:
                os.remove(archive_path)
            except Exception:
                pass
        delete_reference_mask_assets(date_str, filename)
        remove_reference_archive_index_entries(date_str, filename)
        archive_day_dir = os.path.dirname(archive_path)
        if os.path.isdir(archive_day_dir) and not os.listdir(archive_day_dir):
            try:
                os.rmdir(archive_day_dir)
            except Exception:
                pass


def normalize_ref_image_payloads(ref_images: list[dict] | None, max_ref: int) -> list[dict]:
    normalized = []
    for img in (ref_images[:max_ref] if isinstance(ref_images, list) else []):
        if not isinstance(img, dict):
            continue
        data = str(img.get("data", "") or "").strip()
        if not data:
            continue
        item = {
            "mime_type": str(img.get("mime_type", "image/png") or "image/png"),
            "data": data,
            "name": str(img.get("name", "") or ""),
        }
        try:
            clamped_b64, clamped_mime = clamp_image_b64_max_side(item["data"], item["mime_type"])
            item["data"] = clamped_b64
            item["mime_type"] = clamped_mime
        except Exception:
            pass
        extra_fields = (
            "original_data",
            "original_mime_type",
            "mask_png_data",
            "archive_date",
            "archive_filename",
            "original_url",
            "masked_url",
            "mask_url",
        )
        for key in extra_fields:
            value = img.get(key)
            if value is None:
                continue
            item[key] = str(value or "")
        if img.get("has_mask") is not None:
            item["has_mask"] = bool(img.get("has_mask"))
        normalized.append(item)
    return normalized


class GenerationDebugError(RuntimeError):
    def __init__(self, message: str, debug: dict | None = None):
        super().__init__(message)
        self.debug = debug or {}


def build_gemini_failure_debug(result: dict, safety_preset: str = "default", safety_settings_sent: bool = False) -> dict:
    prompt_feedback = result.get("promptFeedback") or {}
    candidates = []
    for idx, candidate in enumerate(result.get("candidates", []) or []):
        finish_reason = candidate.get("finishReason")
        finish_message = candidate.get("finishMessage")
        parts = (candidate.get("content") or {}).get("parts") or []
        has_image = any("inlineData" in part for part in parts)
        safety_ratings = candidate.get("safetyRatings") or []
        candidates.append({
            "index": idx + 1,
            "finishReason": finish_reason or "",
            "finishMessage": finish_message or "",
            "hasImage": has_image,
            "safetyRatings": [
                {
                    "category": rating.get("category", ""),
                    "probability": rating.get("probability", ""),
                    "blocked": bool(rating.get("blocked", False)),
                }
                for rating in safety_ratings
                if isinstance(rating, dict)
            ],
        })

    summary_parts = []
    block_reason = prompt_feedback.get("blockReason")
    block_reason_message = prompt_feedback.get("blockReasonMessage")
    if block_reason:
        summary_parts.append(f"Prompt blocked: {block_reason}")
    if block_reason_message:
        summary_parts.append(str(block_reason_message))
    for item in candidates:
        finish_reason = item.get("finishReason")
        if finish_reason and finish_reason != "STOP":
            note = finish_reason
            if item.get("finishMessage"):
                note += f": {item['finishMessage']}"
            summary_parts.append(f"Candidate {item['index']}: {note}")
        elif finish_reason == "STOP" and not item.get("hasImage"):
            note = "STOP but no image was returned"
            if item.get("finishMessage"):
                note += f": {item['finishMessage']}"
            summary_parts.append(f"Candidate {item['index']}: {note}")

    if not summary_parts:
        summary_parts.append("Gemini returned no image for this request.")

    return {
        "provider": "gemini",
        "safetyPreset": safety_preset,
        "safetySettingsSent": bool(safety_settings_sent),
        "promptBlockReason": block_reason or "",
        "promptBlockReasonMessage": block_reason_message or "",
        "candidates": candidates,
        "summary": " | ".join(summary_parts),
    }


def summarize_generate_response_issue(result: dict, safety_preset: str = "default", safety_settings_sent: bool = False) -> tuple[str, dict]:
    debug = build_gemini_failure_debug(result, safety_preset=safety_preset, safety_settings_sent=safety_settings_sent)
    return debug["summary"], debug


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------
def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                if k not in data:
                    data[k] = v
            return data
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def ensure_flask_secret_key() -> str:
    env_secret = (_os.environ.get("FLASK_SECRET_KEY", "") or "").strip()
    if env_secret:
        return env_secret

    config = load_config()
    secret = str(config.get("flask_secret_key", "") or "").strip()
    if not secret:
        secret = secrets.token_hex(32)
        config["flask_secret_key"] = secret
        save_config(config)
    return secret


app.secret_key = ensure_flask_secret_key()


def mask_api_key(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    return value[:8] + "..." + value[-4:] if len(value) > 12 else "***"


def normalize_seedream_size(image_size: str) -> str:
    image_size = (image_size or "2K").strip().upper()
    if image_size not in {"2K", "4K"}:
        return "2K"
    return image_size


GEMINI_SAFETY_CATEGORIES = [
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
    "HARM_CATEGORY_CIVIC_INTEGRITY",
]


def build_gemini_safety_settings(profile: str) -> tuple[list[dict] | None, str]:
    normalized = (profile or "default").strip().lower()
    threshold = {
        "default": None,
        "relaxed": "BLOCK_ONLY_HIGH",
        "off": "OFF",
        "strict": "BLOCK_LOW_AND_ABOVE",
    }.get(normalized)
    if normalized not in {"default", "relaxed", "off", "strict"}:
        normalized = "default"
        threshold = None
    if threshold is None:
        return None, normalized
    return ([{"category": category, "threshold": threshold} for category in GEMINI_SAFETY_CATEGORIES], normalized)


def normalize_fal_safety_tolerance(value) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 4
    return max(1, min(parsed, 6))


def constrain_image_to_max_pixels(width: int, height: int, max_pixels: int) -> tuple[int, int]:
    width = max(1, int(width or 1))
    height = max(1, int(height or 1))
    max_pixels = max(1, int(max_pixels or 1))
    total_pixels = width * height
    if total_pixels <= max_pixels:
        return width, height

    scale = (max_pixels / float(total_pixels)) ** 0.5
    new_width = max(1, min(width, int(width * scale)))
    new_height = max(1, min(height, int(height * scale)))

    while new_width * new_height > max_pixels:
        if new_width >= new_height and new_width > 1:
            new_width -= 1
        elif new_height > 1:
            new_height -= 1
        else:
            break

    return new_width, new_height


def compress_seedream_ref_image(image_b64: str, mime_type: str) -> tuple[str, str]:
    """Clamp/compress a Seedream reference image to provider-safe pixel and byte limits."""
    raw = base64.b64decode(image_b64)
    img, info = open_base64_image(image_b64)
    orig_width, orig_height = img.size
    needs_pixel_clamp = (orig_width * orig_height) > SEEDREAM_MAX_INPUT_PIXELS
    if not needs_pixel_clamp and len(raw) <= SEEDREAM_TARGET_INPUT_BYTES:
        return image_b64, mime_type

    img = flatten_image_for_jpeg(img)
    clamped_width, clamped_height = constrain_image_to_max_pixels(orig_width, orig_height, SEEDREAM_MAX_INPUT_PIXELS)
    if (clamped_width, clamped_height) != img.size:
        img = img.resize((clamped_width, clamped_height), Image.LANCZOS)

    width, height = img.size
    quality = 88
    max_side = max(width, height)

    while True:
        working = img.copy()
        current_max = max(working.size)
        if current_max > max_side:
            scale = max_side / float(current_max)
            new_w = max(256, int(working.size[0] * scale))
            new_h = max(256, int(working.size[1] * scale))
            working = working.resize((new_w, new_h), Image.LANCZOS)

        buf = io.BytesIO()
        save_kwargs = {"format": "JPEG", "quality": quality, "optimize": True}
        icc_profile = info.get("icc_profile")
        if icc_profile:
            save_kwargs["icc_profile"] = icc_profile
        working.save(buf, **save_kwargs)
        payload = buf.getvalue()
        if len(payload) <= SEEDREAM_TARGET_INPUT_BYTES:
            return base64.b64encode(payload).decode("utf-8"), "image/jpeg"

        if quality > 50:
            quality -= 8
            continue
        if max_side > 1536:
            max_side = int(max_side * 0.82)
            quality = 82
            continue
        return base64.b64encode(payload).decode("utf-8"), "image/jpeg"


def compress_video_input_image(image_b64: str, mime_type: str) -> tuple[str, str]:
    """Clamp/compress video start/reference images to provider-safe pixel and byte limits."""
    raw = base64.b64decode(image_b64)
    img, info = open_base64_image(image_b64)
    orig_width, orig_height = img.size
    needs_pixel_clamp = (orig_width * orig_height) > VIDEO_MAX_INPUT_PIXELS
    if not needs_pixel_clamp and len(raw) <= VIDEO_TARGET_INPUT_BYTES:
        return image_b64, mime_type

    img = flatten_image_for_jpeg(img)
    clamped_width, clamped_height = constrain_image_to_max_pixels(orig_width, orig_height, VIDEO_MAX_INPUT_PIXELS)
    if (clamped_width, clamped_height) != img.size:
        img = img.resize((clamped_width, clamped_height), Image.LANCZOS)

    width, height = img.size
    quality = 88
    max_side = max(width, height)

    while True:
        working = img.copy()
        current_max = max(working.size)
        if current_max > max_side:
            scale = max_side / float(current_max)
            new_w = max(256, int(working.size[0] * scale))
            new_h = max(256, int(working.size[1] * scale))
            working = working.resize((new_w, new_h), Image.LANCZOS)

        buf = io.BytesIO()
        save_kwargs = {"format": "JPEG", "quality": quality, "optimize": True}
        icc_profile = info.get("icc_profile")
        if icc_profile:
            save_kwargs["icc_profile"] = icc_profile
        working.save(buf, **save_kwargs)
        payload = buf.getvalue()
        if len(payload) <= VIDEO_TARGET_INPUT_BYTES:
            return base64.b64encode(payload).decode("utf-8"), "image/jpeg"

        if quality > 50:
            quality -= 8
            continue
        if max_side > 1536:
            max_side = int(max_side * 0.82)
            quality = 82
            continue
        return base64.b64encode(payload).decode("utf-8"), "image/jpeg"


def build_data_uri_ref_inputs(ref_images: list[dict]) -> list[str]:
    items = []
    for img in ref_images:
        mime = img.get("mime_type", "image/png")
        data = img.get("data", "")
        if data:
            items.append(f"data:{mime};base64,{data}")
    return items


def build_seedream_data_uri_ref_inputs(ref_images: list[dict]) -> list[str]:
    items = []
    for img in ref_images:
        mime = img.get("mime_type", "image/png")
        data = img.get("data", "")
        if data:
            safe_b64, safe_mime = compress_seedream_ref_image(data, mime)
            items.append(f"data:{safe_mime};base64,{safe_b64}")
    return items


def fetch_remote_reference_image(url: str) -> tuple[str, str, str]:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http and https image URLs are supported.")

    try:
        response = requests.get(
            url,
            headers={"User-Agent": f"Directors Studio/{APP_VERSION}"},
            stream=True,
            timeout=20,
        )
    except requests.exceptions.Timeout as exc:
        raise TimeoutError("Timed out while downloading the dropped image.") from exc
    except Exception as exc:
        raise RuntimeError(f"Could not download the dropped image: {exc}") from exc

    with response:
        if response.status_code != 200:
            raise ValueError(f"Could not download the dropped image ({response.status_code}).")

        content_type = (response.headers.get("Content-Type", "") or "").split(";")[0].strip().lower()
        if not content_type.startswith("image/"):
            raise ValueError("The dropped URL did not return an image.")

        chunks = []
        total = 0
        for chunk in response.iter_content(64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > REMOTE_REF_FETCH_MAX_BYTES:
                raise ValueError("The dropped image is too large to import.")
            chunks.append(chunk)

    raw = b"".join(chunks)
    if not raw:
        raise ValueError("The dropped image was empty.")

    filename = os.path.basename(unquote(parsed.path or "")) or "reference-image"
    return base64.b64encode(raw).decode("utf-8"), content_type, filename


def build_byteplus_seedream_ref_inputs(ref_images: list[dict]) -> list[str]:
    items = []
    for img in ref_images:
        mime = img.get("mime_type", "image/png")
        data = img.get("data", "")
        if data:
            safe_b64, safe_mime = compress_seedream_ref_image(data, mime)
            items.append(f"data:{safe_mime};base64,{safe_b64}")
    return items


def extract_fal_error(resp: requests.Response) -> str:
    try:
        payload = resp.json()
    except Exception:
        return f"HTTP {resp.status_code}"
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, list) and detail:
            first = detail[0]
            if isinstance(first, dict):
                msg = first.get("msg") or first.get("message") or first.get("detail")
                loc = first.get("loc")
                if msg and loc:
                    return f"{'.'.join(str(x) for x in loc)}: {msg}"
                if msg:
                    return str(msg)
            elif isinstance(first, str):
                return first
        for key in ("error", "message", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return f"HTTP {resp.status_code}"


def extract_byteplus_error(resp: requests.Response) -> str:
    try:
        payload = resp.json()
    except Exception:
        raw = (resp.text or "").strip()
        return raw or f"HTTP {resp.status_code}"
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "").strip()
            message = str(error.get("message") or error.get("msg") or "").strip()
            if code and message:
                return f"{code} - {message}"
            if message:
                return message
        for key in ("message", "msg", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                code = str(payload.get("code") or "").strip()
                return f"{code} - {value.strip()}" if code else value.strip()
    return f"HTTP {resp.status_code}"


def extract_kling_error(resp: requests.Response) -> str:
    try:
        payload = resp.json()
    except Exception:
        raw = (resp.text or "").strip()
        return raw or f"HTTP {resp.status_code}"
    if isinstance(payload, dict):
        for key in ("message", "msg", "error_msg", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        if isinstance(payload.get("data"), dict):
            data = payload["data"]
            for key in ("task_status_msg", "message", "msg"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return f"HTTP {resp.status_code}"


def build_fal_seedream_image_size(model_id: str, image_size: str) -> str:
    normalized = (image_size or "2K").strip().upper()
    if model_id == FAL_SEEDREAM_5_TEXT_ID:
        return "auto_3K" if normalized == "4K" else "auto_2K"
    return "auto_4K" if normalized == "4K" else "auto_2K"


def build_fal_seedream_endpoint(model_id: str, has_refs: bool) -> str:
    if model_id == FAL_SEEDREAM_45_TEXT_ID:
        return FAL_SEEDREAM_45_EDIT_ID if has_refs else FAL_SEEDREAM_45_TEXT_ID
    if model_id == FAL_SEEDREAM_5_TEXT_ID:
        return FAL_SEEDREAM_5_EDIT_ID if has_refs else FAL_SEEDREAM_5_TEXT_ID
    raise ValueError("Unsupported Fal Seedream model")


def build_fal_nano_banana_endpoint(model_id: str, has_refs: bool) -> str:
    if model_id == FAL_NANO_BANANA_TEXT_ID:
        return FAL_NANO_BANANA_EDIT_ID if has_refs else FAL_NANO_BANANA_TEXT_ID
    if model_id == FAL_NANO_BANANA_PRO_TEXT_ID:
        return FAL_NANO_BANANA_PRO_EDIT_ID if has_refs else FAL_NANO_BANANA_PRO_TEXT_ID
    if model_id == FAL_NANO_BANANA_2_TEXT_ID:
        return FAL_NANO_BANANA_2_EDIT_ID if has_refs else FAL_NANO_BANANA_2_TEXT_ID
    raise ValueError("Unsupported Fal Nano Banana model")


def build_fal_nano_banana_resolution(model_id: str, image_size: str) -> str:
    normalized = (image_size or "1K").strip().upper()
    if model_id == FAL_NANO_BANANA_TEXT_ID:
        return "1K"
    if model_id == FAL_NANO_BANANA_PRO_TEXT_ID and normalized not in {"1K", "2K", "4K"}:
        return "1K"
    if model_id == FAL_NANO_BANANA_2_TEXT_ID and normalized not in {"0.5K", "1K", "2K", "4K"}:
        return "1K"
    return normalized


def compute_fal_gpt_image_2_dimensions(aspect_ratio: str, image_size: str) -> tuple[int, int]:
    ratio_text = str(aspect_ratio or "1:1").strip()
    try:
        width_ratio_raw, height_ratio_raw = ratio_text.split(":", 1)
        width_ratio = max(1, int(width_ratio_raw))
        height_ratio = max(1, int(height_ratio_raw))
    except Exception as exc:
        raise ValueError("Unsupported GPT Image 2 aspect ratio.") from exc

    normalized_size = str(image_size or "1K").strip().upper()
    max_side = FAL_GPT_IMAGE_2_MAX_SIDE_BY_SIZE.get(normalized_size, FAL_GPT_IMAGE_2_MAX_SIDE_BY_SIZE["1K"])
    ratio_value = width_ratio / height_ratio

    if ratio_value >= 1:
        width = max_side
        height = max(16, int((width / ratio_value) // 16 * 16))
    else:
        height = max_side
        width = max(16, int((height * ratio_value) // 16 * 16))

    if width * height > FAL_GPT_IMAGE_2_MAX_PIXELS:
        scale = (FAL_GPT_IMAGE_2_MAX_PIXELS / float(width * height)) ** 0.5
        width = max(16, int((width * scale) // 16 * 16))
        height = max(16, int((height * scale) // 16 * 16))

    while width > max_side or height > max_side or (width * height) > FAL_GPT_IMAGE_2_MAX_PIXELS:
        if width >= height:
            width = max(16, width - 16)
            height = max(16, int((width / ratio_value) // 16 * 16))
        else:
            height = max(16, height - 16)
            width = max(16, int((height * ratio_value) // 16 * 16))

    return width, height


def build_fal_gpt_image_2_image_size(image_size: str, aspect_ratio: str) -> str | dict:
    normalized_size = str(image_size or "1K").strip().upper()
    normalized_ratio = str(aspect_ratio or "1:1").strip()
    preset_map = {
        "1:1": "square_hd",
        "16:9": "landscape_16_9",
        "9:16": "portrait_16_9",
        "4:3": "landscape_4_3",
        "3:4": "portrait_4_3",
    }
    if normalized_size == "1K" and normalized_ratio in preset_map:
        return preset_map[normalized_ratio]
    width, height = compute_fal_gpt_image_2_dimensions(normalized_ratio, normalized_size)
    return {"width": width, "height": height}


def resolve_fal_gpt_image_2_dimensions(image_size_value: str | dict) -> tuple[int, int]:
    if isinstance(image_size_value, dict):
        width = int(image_size_value.get("width") or 0)
        height = int(image_size_value.get("height") or 0)
        if width > 0 and height > 0:
            return width, height
    preset_dimensions = {
        "square_hd": (1024, 1024),
        "square": (512, 512),
        "portrait_4_3": (768, 1024),
        "portrait_16_9": (576, 1024),
        "landscape_4_3": (1024, 768),
        "landscape_16_9": (1024, 576),
    }
    if isinstance(image_size_value, str):
        dims = preset_dimensions.get(str(image_size_value or "").strip().lower())
        if dims:
            return dims
    return 0, 0


def estimate_fal_gpt_image_2_price_per_image(image_size_value: str | dict) -> float:
    width, height = resolve_fal_gpt_image_2_dimensions(image_size_value)
    if width <= 0 or height <= 0:
        return 0.0

    exact = FAL_GPT_IMAGE_2_HIGH_QUALITY_PRICING.get((width, height))
    if exact is not None:
        return float(exact)

    target_area = float(width * height)
    target_aspect = float(max(width, height)) / float(max(1, min(width, height)))
    best_price = 0.0
    best_score = None
    for (candidate_width, candidate_height), candidate_price in FAL_GPT_IMAGE_2_HIGH_QUALITY_PRICING.items():
        candidate_area = float(candidate_width * candidate_height)
        candidate_aspect = float(max(candidate_width, candidate_height)) / float(max(1, min(candidate_width, candidate_height)))
        area_ratio = max(target_area, candidate_area) / max(1.0, min(target_area, candidate_area))
        aspect_ratio = max(target_aspect, candidate_aspect) / max(1.0, min(target_aspect, candidate_aspect))
        score = abs(area_ratio - 1.0) + (abs(aspect_ratio - 1.0) * 1.35)
        if best_score is None or score < best_score:
            best_score = score
            best_price = float(candidate_price)
    return best_price


def normalize_seedvr_preset(value: str | None) -> tuple[str, float | None, str | None, str]:
    presets = {
        "factor:2": ("factor", 2.0, None, "2X"),
        "factor:4": ("factor", 4.0, None, "4X"),
        "target:720p": ("target", None, "720p", "720P"),
        "target:1080p": ("target", None, "1080p", "1080P"),
        "target:1440p": ("target", None, "1440p", "1440P"),
        "target:2160p": ("target", None, "2160p", "4K"),
    }
    return presets.get(str(value or "factor:2").strip().lower(), presets["factor:2"])


def normalize_seedvr_custom_resolution(
    source_width: int,
    source_height: int,
    target_width: int | str | None,
    target_height: int | str | None,
    anchor: str | None,
) -> tuple[int, int, float, str]:
    safe_source_width = max(1, int(source_width or 0))
    safe_source_height = max(1, int(source_height or 0))
    if not safe_source_width or not safe_source_height:
        raise ValueError("Could not read the selected image size for custom upscaling.")

    safe_anchor = str(anchor or "width").strip().lower()
    if safe_anchor not in {"width", "height"}:
        safe_anchor = "width"

    raw_width = int(target_width or 0)
    raw_height = int(target_height or 0)
    if raw_width <= 0 and raw_height <= 0:
        raise ValueError("Enter a custom output width or height.")

    if safe_anchor == "height":
        locked_height = max(64, raw_height or int(round((raw_width * safe_source_height) / safe_source_width)))
        locked_width = max(64, int(round((locked_height * safe_source_width) / safe_source_height)))
    else:
        locked_width = max(64, raw_width or int(round((raw_height * safe_source_width) / safe_source_height)))
        locked_height = max(64, int(round((locked_width * safe_source_height) / safe_source_width)))

    upscale_factor = max(1.0, locked_width / safe_source_width, locked_height / safe_source_height)
    return locked_width, locked_height, round(upscale_factor, 6), safe_anchor


def approximate_image_size_label(width: int, height: int) -> str:
    max_dim = max(int(width or 0), int(height or 0))
    if max_dim >= 3840:
        return "4K"
    if max_dim >= 2048:
        return "2K"
    if max_dim >= 1024:
        return "1K"
    if max_dim >= 512:
        return "0.5K"
    return f"{max_dim}px" if max_dim else ""


def measure_image_dimensions(image_b64: str, mime_type: str = "image/png") -> tuple[int, int]:
    try:
        raw = base64.b64decode(image_b64)
        with Image.open(io.BytesIO(raw)) as img:
            return img.size
    except Exception:
        return 0, 0


def measure_image_file_dimensions(file_path: str) -> tuple[int, int]:
    try:
        with Image.open(file_path) as img:
            return img.size
    except Exception:
        return 0, 0


def resize_image_b64_to_exact_png(image_b64: str, mime_type: str, target_width: int, target_height: int) -> tuple[str, str]:
    """Resize an image payload to an exact PNG output while preserving ICC data."""
    safe_width = max(1, int(target_width or 0))
    safe_height = max(1, int(target_height or 0))
    if not safe_width or not safe_height:
        return image_b64, mime_type or "image/png"

    img, info = open_base64_image(image_b64)
    has_alpha = ("A" in img.getbands()) or (img.mode == "P" and "transparency" in img.info)
    if has_alpha:
        if img.mode != "RGBA":
            img = img.convert("RGBA")
    elif img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    if img.size != (safe_width, safe_height):
        img = img.resize((safe_width, safe_height), Image.LANCZOS)

    buf = io.BytesIO()
    save_kwargs = {"format": "PNG", "optimize": True}
    icc_profile = info.get("icc_profile")
    if icc_profile:
        save_kwargs["icc_profile"] = icc_profile
    img.save(buf, **save_kwargs)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8"), "image/png"


def clamp_image_b64_max_side(image_b64: str, mime_type: str, max_pixels: int = MAX_REFERENCE_IMAGE_PIXELS) -> tuple[str, str]:
    """Resize an image proportionally when it exceeds the app's 4K-equivalent pixel budget."""
    img, info = open_base64_image(image_b64)
    width, height = img.size
    if (width * height) <= max_pixels:
        return image_b64, mime_type

    new_width, new_height = constrain_image_to_max_pixels(width, height, max_pixels)
    img = img.resize((new_width, new_height), Image.LANCZOS)

    has_alpha = ("A" in img.getbands()) or (img.mode == "P" and "transparency" in img.info)
    buf = io.BytesIO()
    icc_profile = info.get("icc_profile")
    if has_alpha:
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        save_kwargs = {"format": "PNG", "optimize": True}
        if icc_profile:
            save_kwargs["icc_profile"] = icc_profile
        img.save(buf, **save_kwargs)
        out_mime = "image/png"
    else:
        img = flatten_image_for_jpeg(img)
        save_kwargs = {"format": "JPEG", "quality": JPEG_QUALITY, "optimize": True}
        if icc_profile:
            save_kwargs["icc_profile"] = icc_profile
        img.save(buf, **save_kwargs)
        out_mime = "image/jpeg"

    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8"), out_mime


def get_reference_mask_file_paths(date_str: str, filename: str) -> dict[str, str]:
    safe_date = str(date_str or "").strip()
    safe_filename = os.path.basename(str(filename or "").strip())
    base_stem = os.path.splitext(safe_filename)[0]
    return {
        "original_dir": os.path.join(REFERENCE_ARCHIVE_DIR, safe_date),
        "original_path": os.path.join(REFERENCE_ARCHIVE_DIR, safe_date, safe_filename),
        "mask_dir": os.path.join(REFERENCE_MASKS_DIR, safe_date),
        "mask_path": os.path.join(REFERENCE_MASKS_DIR, safe_date, safe_filename),
        "meta_path": os.path.join(REFERENCE_MASKS_DIR, safe_date, f"{base_stem}.json"),
        "render_dir": os.path.join(REFERENCE_RENDERS_DIR, safe_date),
        "render_path": os.path.join(REFERENCE_RENDERS_DIR, safe_date, safe_filename),
    }


def build_reference_mask_bundle(date_str: str, filename: str) -> dict:
    safe_date = str(date_str or "").strip()
    safe_filename = os.path.basename(str(filename or "").strip())
    paths = get_reference_mask_file_paths(safe_date, safe_filename)
    has_mask = os.path.exists(paths["mask_path"]) and os.path.exists(paths["render_path"])
    return {
        "date": safe_date,
        "filename": safe_filename,
        "has_mask": has_mask,
        "original_url": f"/reference-archive/{safe_date}/{safe_filename}",
        "masked_url": f"/reference-render/{safe_date}/{safe_filename}" if has_mask else "",
        "mask_url": f"/reference-mask/{safe_date}/{safe_filename}" if has_mask else "",
        "display_url": f"/reference-render/{safe_date}/{safe_filename}" if has_mask else f"/reference-archive/{safe_date}/{safe_filename}",
        "mask_edit_payload": {
            "kind": "references",
            "date": safe_date,
            "filename": safe_filename,
        },
    }


def load_reference_mask_metadata(date_str: str, filename: str) -> dict:
    paths = get_reference_mask_file_paths(date_str, filename)
    if not os.path.exists(paths["meta_path"]):
        return {}
    try:
        with open(paths["meta_path"], "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def delete_reference_mask_assets(date_str: str, filename: str) -> None:
    paths = get_reference_mask_file_paths(date_str, filename)
    for key in ("mask_path", "meta_path", "render_path"):
        path = paths[key]
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
    for dir_key in ("mask_dir", "render_dir"):
        folder = paths[dir_key]
        if os.path.isdir(folder) and not os.listdir(folder):
            try:
                os.rmdir(folder)
            except Exception:
                pass


def save_reference_mask_assets(date_str: str, filename: str, mask_png_b64: str) -> dict:
    paths = get_reference_mask_file_paths(date_str, filename)
    original_path = paths["original_path"]
    if not os.path.exists(original_path):
        raise FileNotFoundError("Reference image not found.")

    mask_img, _ = open_base64_image(mask_png_b64)
    original_img = Image.open(original_path)
    original_img.load()
    original_info = dict(getattr(original_img, "info", {}) or {})
    original_img = ImageOps.exif_transpose(original_img).convert("RGBA")

    mask_img = ImageOps.exif_transpose(mask_img).convert("L")
    if mask_img.size != original_img.size:
        mask_img = mask_img.resize(original_img.size, Image.Resampling.LANCZOS)

    transparent_img = Image.new("RGBA", original_img.size, (0, 0, 0, 0))
    render_img = Image.composite(original_img, transparent_img, mask_img)

    os.makedirs(paths["mask_dir"], exist_ok=True)
    os.makedirs(paths["render_dir"], exist_ok=True)

    mask_buf = io.BytesIO()
    mask_img.save(mask_buf, format="PNG", optimize=True)
    with open(paths["mask_path"], "wb") as fh:
        fh.write(mask_buf.getvalue())

    render_buf = io.BytesIO()
    save_kwargs = {"format": "PNG", "optimize": True}
    icc_profile = original_info.get("icc_profile")
    if icc_profile:
        save_kwargs["icc_profile"] = icc_profile
    render_img.save(render_buf, **save_kwargs)
    with open(paths["render_path"], "wb") as fh:
        fh.write(render_buf.getvalue())

    metadata = {
        "date": str(date_str or "").strip(),
        "filename": os.path.basename(str(filename or "").strip()),
        "updated_at": utc_now_iso(),
        "width": int(original_img.width),
        "height": int(original_img.height),
        "mode": "alpha_mask",
    }
    with open(paths["meta_path"], "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2, ensure_ascii=False)
    return metadata


def enrich_reference_archive_entry(entry: dict | None) -> dict:
    entry = dict(entry or {})
    ref_date = str(entry.get("date", "") or "").strip()
    filename = os.path.basename(str(entry.get("filename", "") or "").strip())
    if not ref_date or not filename:
        return entry
    bundle = build_reference_mask_bundle(ref_date, filename)
    enriched = {
        "date": ref_date,
        "filename": filename,
        "name": entry.get("name", "") or filename,
        "mime_type": entry.get("mime_type", "image/png"),
        "url": entry.get("url") or bundle["display_url"],
        "original_url": entry.get("original_url") or bundle["original_url"],
        "masked_url": entry.get("masked_url") or bundle["masked_url"],
        "mask_url": entry.get("mask_url") or bundle["mask_url"],
        "has_mask": bool(entry.get("has_mask")) or bundle["has_mask"],
        "mask_edit_payload": entry.get("mask_edit_payload") or bundle["mask_edit_payload"],
    }
    enriched["url"] = enriched["masked_url"] if enriched["has_mask"] and enriched["masked_url"] else (enriched["original_url"] or bundle["display_url"])
    return enriched


def enrich_reference_archive_entries(entries: list[dict] | None) -> list[dict]:
    result = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        result.append(enrich_reference_archive_entry(entry))
    return result


def decode_fal_image_result(item: dict) -> dict | None:
    image_url = str(item.get("url") or item.get("image_url") or item.get("data_uri") or "").strip()
    if image_url.startswith("data:"):
        header, _, b64_data = image_url.partition(",")
        mime_type = header.split(";", 1)[0][5:] or "image/png"
        return {"mime_type": mime_type, "data": b64_data}
    if image_url:
        try:
            image_resp = requests.get(image_url, timeout=120)
            image_resp.raise_for_status()
            raw = image_resp.content
            png_b64, png_mime = convert_image_b64_to_png(
                base64.b64encode(raw).decode("utf-8"),
                image_resp.headers.get("Content-Type", item.get("content_type", "image/png"))
            )
            return {"mime_type": png_mime, "data": png_b64}
        except Exception:
            return None
    return None


def download_remote_binary(url: str, *, headers: dict | None = None, timeout: int = 240) -> tuple[bytes, str]:
    response = requests.get(url, headers=headers or {}, timeout=timeout)
    response.raise_for_status()
    return response.content, str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()


def extract_fal_video_result(payload: dict) -> dict | None:
    candidates = []
    if isinstance(payload, dict):
        if isinstance(payload.get("video"), dict):
            candidates.append(payload.get("video"))
        if isinstance(payload.get("videos"), list):
            candidates.extend([item for item in payload.get("videos") if isinstance(item, dict)])
        if isinstance(payload.get("data"), dict):
            data_payload = payload.get("data")
            if isinstance(data_payload.get("video"), dict):
                candidates.append(data_payload.get("video"))
            if isinstance(data_payload.get("videos"), list):
                candidates.extend([item for item in data_payload.get("videos") if isinstance(item, dict)])
    for item in candidates:
        video_url = str(item.get("url") or item.get("video_url") or "").strip()
        if video_url:
            return {
                "url": video_url,
                "mime_type": str(item.get("content_type") or item.get("mime_type") or "video/mp4").strip() or "video/mp4",
                "poster_url": str(item.get("thumbnail_url") or item.get("poster_url") or item.get("preview_image_url") or "").strip(),
                "width": int(item.get("width") or 0),
                "height": int(item.get("height") or 0),
            }
    return None


def normalize_video_extension(mime_type: str, fallback_url: str = "") -> tuple[str, str]:
    mime = str(mime_type or "").split(";", 1)[0].strip().lower()
    if mime in {"video/mp4", "application/mp4"}:
        return "mp4", "video/mp4"
    if mime in {"video/webm"}:
        return "webm", "video/webm"
    if mime in {"video/quicktime"}:
        return "mov", "video/quicktime"
    if mime in {"image/gif", "video/gif"}:
        return "gif", "image/gif"
    parsed_path = os.path.basename(urlparse(str(fallback_url or "")).path or "")
    ext = os.path.splitext(parsed_path)[1].lower()
    if ext in {".mp4", ".webm", ".mov", ".gif"}:
        return ext.lstrip("."), mime or {
            ".mp4": "video/mp4",
            ".webm": "video/webm",
            ".mov": "video/quicktime",
            ".gif": "image/gif",
        }[ext]
    return "mp4", "video/mp4"


def get_video_extension_for_payload(mime_type: str = "", filename: str = "") -> str:
    _, normalized_mime = normalize_video_extension(mime_type, filename)
    ext, _ = normalize_video_extension(normalized_mime, filename)
    return ext or "mp4"


def resolve_local_video_url_to_path(video_url: str) -> str:
    parsed = urlparse(str(video_url or "").strip())
    path = unquote(parsed.path or "")
    if not path.startswith("/videos/"):
        return ""
    relpath = "/".join([part for part in path.split("/") if part][1:])
    if not relpath:
        return ""
    try:
        local_path = safe_asset_path(VIDEOS_DIR, relpath)
    except Exception:
        return ""
    return local_path if os.path.exists(local_path) else ""


def upload_video_payload_to_fal(client: fal_client.SyncClient, video_payload: dict) -> str:
    if not isinstance(video_payload, dict):
        raise ValueError("Choose or drop a source video for this model.")
    data_b64 = str(video_payload.get("data") or "").strip()
    direct_url = str(video_payload.get("url") or "").strip()
    mime_type = str(video_payload.get("mime_type") or "video/mp4").strip() or "video/mp4"
    name = os.path.basename(str(video_payload.get("name") or "video-source.mp4")) or "video-source.mp4"

    if direct_url.startswith("http://") or direct_url.startswith("https://"):
        return direct_url

    local_path = resolve_local_video_url_to_path(direct_url)
    if local_path:
        return str(client.upload_file(local_path))

    if not data_b64:
        raise ValueError("Choose or drop a source video for this model.")

    suffix = f".{get_video_extension_for_payload(mime_type, name)}"
    temp_path = ""
    try:
        raw_bytes = base64.b64decode(data_b64, validate=False)
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(raw_bytes)
            temp_path = temp_file.name
        return str(client.upload_file(temp_path))
    except Exception as exc:
        raise RuntimeError(f"Could not upload the selected source video: {exc}") from exc
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Routes Ã¢â‚¬â€ Auth
# ---------------------------------------------------------------------------
@app.route("/")
def root():
    if "user" in session:
        return redirect(url_for("index"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if username in USERS and USERS[username] == password:
            session["user"] = username
            return redirect(url_for("index"))
        error = "Invalid credentials. Please try again."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Routes Ã¢â‚¬â€ App
# ---------------------------------------------------------------------------
@app.route("/index")
@login_required
def index():
    config = load_config()
    has_key = bool(
        config.get("api_key", "").strip()
        or config.get("fal_api_key", "").strip()
        or config.get("byteplus_api_key", "").strip()
        or config.get("kling_api_token", "").strip()
    )
    return render_template("index.html",
                           models=MODELS_INFO,
                           model_families=MODEL_FAMILIES,
                           pricing=PRICING,
                           video_models=VIDEO_MODELS_INFO,
                           video_model_families=VIDEO_MODEL_FAMILIES,
                           video_pricing=VIDEO_PRICING,
                           gpt_image_2_dimension_pricing={f"{width}x{height}": price for (width, height), price in FAL_GPT_IMAGE_2_HIGH_QUALITY_PRICING.items()},
                           provider_labels=PROVIDER_LABELS,
                           provider_keys=provider_key_state(config),
                           initial_gallery_history_items=collect_generation_records(max_load=None),
                           has_key=has_key,
                           user=session["user"])


@app.route("/settings")
@login_required
def settings():
    config = load_config()
    stats = config.get("stats", DEFAULT_CONFIG["stats"])
    api_key = config.get("api_key", "")
    fal_api_key = config.get("fal_api_key", "")
    byteplus_api_key = config.get("byteplus_api_key", "") or config.get("seedream_api_key", "")
    kling_api_token = config.get("kling_api_token", "")
    luma_api_key = config.get("luma_api_key", "")
    openai_api_key = config.get("openai_api_key", "")
    topaz_api_key = config.get("topaz_api_key", ""); bfl_api_key = config.get("bfl_api_key", ""); clarity_api_key = config.get("clarity_api_key", "")
    return render_template("settings.html",
                           masked_topaz_key=mask_api_key(topaz_api_key), has_topaz_key=bool(topaz_api_key),
                           masked_bfl_key=mask_api_key(bfl_api_key), has_bfl_key=bool(bfl_api_key),
                           masked_clarity_key=mask_api_key(clarity_api_key), has_clarity_key=bool(clarity_api_key),
                           masked_key=mask_api_key(api_key),
                           has_key=bool(api_key),
                           masked_fal_key=mask_api_key(fal_api_key),
                           has_fal_key=bool(fal_api_key),
                           masked_byteplus_key=mask_api_key(byteplus_api_key),
                           has_byteplus_key=bool(byteplus_api_key),
                           masked_kling_token=mask_api_key(kling_api_token),
                           has_kling_token=bool(kling_api_token),
                           masked_luma_key=mask_api_key(luma_api_key),
                           masked_openai_key=mask_api_key(openai_api_key),
                           has_openai_key=bool(openai_api_key),
                           openai_model=config.get("openai_model", "gpt-4o-mini"),
                           agent_instructions=config.get("agent_instructions", ""),
                           has_luma_key=bool(luma_api_key),
                           stats=stats,
                           vision_models=VISION_MODELS_INFO,
                           analysis_model=TALENT_ANALYSIS_MODEL,
                           catalog_status=get_fal_catalog_status(),
                           video_families=VIDEO_MODEL_FAMILIES,
                           user=session["user"])


# ---------------------------------------------------------------------------
# Route - live fal model catalog
# ---------------------------------------------------------------------------
@app.route("/api/catalog/status")
@login_required
def api_catalog_status():
    return jsonify({"ok": True, "status": get_fal_catalog_status()})


@app.route("/api/catalog/refresh", methods=["POST"])
@login_required
def api_catalog_refresh():
    try:
        result = refresh_fal_catalog()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)})
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Catalog refresh failed: {exc}"})
    return jsonify(result)


@app.route("/api/catalog/probe", methods=["POST"])
@login_required
def api_catalog_probe():
    body = request.get_json(silent=True) or {}
    endpoint_id = str(body.get("endpoint") or "").strip().strip("/")
    if not endpoint_id:
        return jsonify({"ok": False, "error": "Paste a fal endpoint id, e.g. fal-ai/veo3.1/image-to-video"})
    try:
        entry = fal_catalog.probe_endpoint(endpoint_id, cfg={
            "label": body.get("label") or "",
            "family": body.get("family") or "",
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})
    known = [str(c.get("endpoint") or c.get("id") or "") for c in fal_catalog.load_endpoints(FAL_ENDPOINTS_PATH)["endpoints"]]
    return jsonify({"ok": True, "entry": entry, "already_listed": endpoint_id in known})


@app.route("/api/catalog/add", methods=["POST"])
@login_required
def api_catalog_add():
    body = request.get_json(silent=True) or {}
    try:
        result = add_fal_catalog_endpoint({
            "endpoint": str(body.get("endpoint") or "").strip().strip("/"),
            "label": str(body.get("label") or "").strip(),
            "family": str(body.get("family") or "").strip().lower(),
            "family_label": str(body.get("family_label") or "").strip(),
            "sort_order": body.get("sort_order"),
        })
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)})
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Could not add endpoint: {exc}"})
    return jsonify(result)


# ---------------------------------------------------------------------------
# Route Ã¢â‚¬â€ Credits
# ---------------------------------------------------------------------------
@app.route("/credits")
@login_required
def credits():
    return render_template("credits.html", user=session["user"])


# ---------------------------------------------------------------------------
# Route - Task Workbench
# ---------------------------------------------------------------------------
@app.route("/workbench")
@login_required
def workbench():
    return render_template(
        "workbench.html",
        task_templates=fetch_task_templates(project_type="campaign"),
        film_task_templates=fetch_task_templates(project_type="film"),
        report=get_workbench_report(),
        channel_labels=WORKBENCH_CHANNEL_LABELS,
        user=session["user"],
    )


# ---------------------------------------------------------------------------
# Route - Reports
# ---------------------------------------------------------------------------
@app.route("/reports")
@login_required
def reports_page():
    return render_template(
        "reports.html",
        report=get_workbench_report(),
        user=session["user"],
    )


# ---------------------------------------------------------------------------
# Routes - Asset galleries
# ---------------------------------------------------------------------------
ASSET_GALLERY_PAGE_CONFIG = {
    "loved": {
        "title": "Loved Images",
        "subtitle": "Favorites saved for reuse and reference.",
        "empty_title": "No loved images yet.",
        "empty_subtitle": "Generate images and press the heart button to save them here.",
    },
    "references": {
        "title": "References",
        "subtitle": "Previously used saved references from your archived generation inputs.",
        "empty_title": "No archived references yet.",
        "empty_subtitle": "Generate with reference images and they will appear here for reuse.",
    },
    "history": {
        "title": "History",
        "subtitle": "Saved generations with the same filters, selection, and scaling flow as the Generator sidebar.",
        "empty_title": "No history images yet.",
        "empty_subtitle": "Generate images and they will appear here automatically.",
    },
    "videos": {
        "title": "Videos",
        "subtitle": "Saved generated videos with the same filters, selection, and scaling flow as the Generator sidebar.",
        "empty_title": "No videos yet.",
        "empty_subtitle": "Generate videos and they will appear here automatically.",
    },
}

VIDEO_ASPECT_RATIOS = ["16:9", "9:16", "1:1"]
VIDEO_DURATION_OPTIONS = [5, 10]
VIDEO_DURATION_EXTENDED_OPTIONS = [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]
VIDEO_DURATION_OMNI_OPTIONS = [3, 4, 5, 6, 7, 8, 9, 10]
VIDEO_DURATION_ALL_OPTIONS = sorted({*VIDEO_DURATION_OPTIONS, *VIDEO_DURATION_EXTENDED_OPTIONS})
KLING_RESOLUTION_OPTIONS = ["1080p"]
KLING_4K_RESOLUTION_OPTIONS = ["2160p"]
WAN_RESOLUTION_OPTIONS = ["720p", "1080p"]
WAN_DURATION_OPTIONS = list(range(2, 16))
WAN_REFERENCE_DURATION_OPTIONS = list(range(2, 11))
WAN_EDIT_DURATION_OPTIONS = list(range(2, 11))
WAN_ASPECT_RATIOS = ["16:9", "9:16", "1:1", "4:3", "3:4"]
SEEDANCE_RESOLUTION_OPTIONS = ["480p", "720p", "1080p"]
SEEDANCE_20_RESOLUTION_OPTIONS = ["480p", "720p"]
SEEDANCE_20_ASPECT_RATIOS = ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"]
SEEDANCE_20_DURATION_OPTIONS = list(range(4, 16))
LTX_VIDEO_LORA_RESOLUTION_OPTIONS = ["480p", "720p"]
LTX_VIDEO_LORA_ASPECT_RATIOS = ["auto", "16:9", "1:1", "9:16"]
LTX_23_VIDEO_SIZE_OPTIONS = ["auto", "square_hd", "square", "portrait_4_3", "portrait_16_9", "landscape_4_3", "landscape_16_9"]

FAL_WAN_MODEL_SPECS = [
    {
        "id": FAL_WAN_T2V_ID,
        "fal_endpoint": FAL_WAN_T2V_ENDPOINT,
        "label": "Wan Video 2.7",
        "input_modes": ["text"],
        "durations": WAN_DURATION_OPTIONS,
        "aspect_ratios": WAN_ASPECT_RATIOS,
        "resolutions": WAN_RESOLUTION_OPTIONS,
        "supports_safety_checker": True,
        "supports_output_safety_checker": True,
        "supports_driving_audio": True,
        "sort_order": 210,
        "video_mode_kind": "text_to_video",
    },
    {
        "id": FAL_WAN_I2V_ID,
        "fal_endpoint": FAL_WAN_I2V_ENDPOINT,
        "label": "Wan Video 2.7",
        "input_modes": ["image"],
        "durations": WAN_DURATION_OPTIONS,
        "aspect_ratios": WAN_ASPECT_RATIOS,
        "resolutions": WAN_RESOLUTION_OPTIONS,
        "supports_safety_checker": True,
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "image_url",
        "supports_driving_audio": True,
        "sort_order": 211,
        "video_mode_kind": "image_to_video",
    },
    {
        "id": FAL_WAN_FIRST_LAST_ID,
        "fal_endpoint": FAL_WAN_I2V_ENDPOINT,
        "label": "Wan 2.7 First+Last",
        "input_modes": ["reference"],
        "durations": WAN_DURATION_OPTIONS,
        "aspect_ratios": WAN_ASPECT_RATIOS,
        "resolutions": WAN_RESOLUTION_OPTIONS,
        "supports_safety_checker": True,
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "image_url",
        "supports_reference_images": True,
        "reference_images_required": True,
        "reference_images_field": "end_image_url",
        "max_reference_images": 1,
        "supports_driving_audio": True,
        "sort_order": 212,
        "video_mode_kind": "first_last_frame_to_video",
    },
    {
        "id": FAL_WAN_CONTINUE_ID,
        "fal_endpoint": FAL_WAN_I2V_ENDPOINT,
        "label": "Wan 2.7 Continue",
        "input_modes": ["video"],
        "durations": WAN_DURATION_OPTIONS,
        "aspect_ratios": WAN_ASPECT_RATIOS,
        "resolutions": WAN_RESOLUTION_OPTIONS,
        "supports_safety_checker": True,
        "supports_source_video": True,
        "source_video_required": True,
        "source_video_field": "video_url",
        "supports_driving_audio": True,
        "sort_order": 213,
        "video_mode_kind": "video_continuation",
    },
    {
        "id": FAL_WAN_REF_ID,
        "fal_endpoint": FAL_WAN_REF_ENDPOINT,
        "label": "Wan 2.7 Reference",
        "input_modes": ["reference"],
        "durations": WAN_REFERENCE_DURATION_OPTIONS,
        "aspect_ratios": WAN_ASPECT_RATIOS,
        "resolutions": WAN_RESOLUTION_OPTIONS,
        "supports_safety_checker": True,
        "supports_reference_images": True,
        "reference_images_required": True,
        "reference_images_field": "reference_image_urls",
        "max_reference_images": 9,
        "supports_reference_videos": True,
        "max_reference_videos": 8,
        "supports_multi_shots": True,
        "sort_order": 214,
        "video_mode_kind": "reference_to_video",
    },
    {
        "id": FAL_WAN_EDIT_ID,
        "fal_endpoint": FAL_WAN_EDIT_ENDPOINT,
        "label": "Wan 2.7 Edit",
        "input_modes": ["video"],
        "durations": WAN_EDIT_DURATION_OPTIONS,
        "aspect_ratios": WAN_ASPECT_RATIOS,
        "resolutions": WAN_RESOLUTION_OPTIONS,
        "supports_safety_checker": True,
        "supports_source_video": True,
        "source_video_required": True,
        "source_video_field": "video_url",
        "sort_order": 215,
        "video_mode_kind": "video_edit",
    },
]

KLING_DIRECT_MODEL_SPECS = [
    {
        "id": "kling-v1-std",
        "native_model_name": "kling-v1",
        "label": "Kling 1.0 Standard",
        "input_modes": ["text", "image"],
        "durations": [5, 10],
        "sort_order": 10,
        "kling_mode": "std",
        "supports_start_image": True,
        "start_image_required": True,
    },
    {
        "id": "kling-v1-pro",
        "native_model_name": "kling-v1",
        "label": "Kling 1.0 Pro",
        "input_modes": ["text", "image"],
        "durations": [5, 10],
        "sort_order": 11,
        "kling_mode": "pro",
        "supports_start_image": True,
        "start_image_required": True,
    },
    {
        "id": "kling-v1-5-std",
        "native_model_name": "kling-v1-5",
        "label": "Kling 1.5 Standard",
        "input_modes": ["image"],
        "durations": [5, 10],
        "sort_order": 20,
        "kling_mode": "std",
        "supports_start_image": True,
        "start_image_required": True,
    },
    {
        "id": "kling-v1-5-pro",
        "native_model_name": "kling-v1-5",
        "label": "Kling 1.5 Pro",
        "input_modes": ["image"],
        "durations": [5, 10],
        "sort_order": 21,
        "kling_mode": "pro",
        "supports_start_image": True,
        "start_image_required": True,
    },
    {
        "id": "kling-v1-6-std",
        "native_model_name": "kling-v1-6",
        "label": "Kling 1.6 Standard",
        "input_modes": ["text", "image"],
        "durations": [5, 10],
        "sort_order": 30,
        "kling_mode": "std",
        "supports_start_image": True,
        "start_image_required": True,
    },
    {
        "id": "kling-v1-6-pro",
        "native_model_name": "kling-v1-6",
        "label": "Kling 1.6 Pro",
        "input_modes": ["text", "image"],
        "durations": [5, 10],
        "sort_order": 31,
        "kling_mode": "pro",
        "supports_start_image": True,
        "start_image_required": True,
    },
    {
        "id": "kling-v1-6-reference-std",
        "native_model_name": "kling-v1-6",
        "label": "Kling 1.6 Reference Standard",
        "input_modes": ["reference"],
        "durations": [5, 10],
        "sort_order": 32,
        "kling_mode": "std",
        "video_mode_kind": "reference_to_video",
        "native_video_endpoint": "/v1/videos/multi-image2video",
        "native_reference_item_key": "image",
        "supports_reference_images": True,
        "reference_images_required": True,
        "max_reference_images": 4,
    },
    {
        "id": "kling-v1-6-reference-pro",
        "native_model_name": "kling-v1-6",
        "label": "Kling 1.6 Reference Pro",
        "input_modes": ["reference"],
        "durations": [5, 10],
        "sort_order": 33,
        "kling_mode": "pro",
        "video_mode_kind": "reference_to_video",
        "native_video_endpoint": "/v1/videos/multi-image2video",
        "native_reference_item_key": "image",
        "supports_reference_images": True,
        "reference_images_required": True,
        "max_reference_images": 4,
    },
    {
        "id": "kling-v2-master",
        "native_model_name": "kling-v2-master",
        "label": "Kling 2.0 Master",
        "input_modes": ["text", "image"],
        "durations": [5, 10],
        "sort_order": 40,
        "kling_mode": "pro",
        "supports_start_image": True,
        "start_image_required": True,
    },
    {
        "id": "kling-v2-1-std",
        "native_model_name": "kling-v2-1",
        "label": "Kling 2.1 Standard",
        "input_modes": ["image"],
        "durations": [5, 10],
        "sort_order": 50,
        "kling_mode": "std",
        "supports_start_image": True,
        "start_image_required": True,
    },
    {
        "id": "kling-v2-1-pro",
        "native_model_name": "kling-v2-1",
        "label": "Kling 2.1 Pro",
        "input_modes": ["image"],
        "durations": [5, 10],
        "sort_order": 51,
        "kling_mode": "pro",
        "supports_start_image": True,
        "start_image_required": True,
    },
    {
        "id": "kling-v2-1-master",
        "native_model_name": "kling-v2-1-master",
        "label": "Kling 2.1 Master",
        "input_modes": ["text", "image"],
        "durations": [5, 10],
        "sort_order": 52,
        "kling_mode": "pro",
        "supports_start_image": True,
        "start_image_required": True,
    },
    {
        "id": "kling-v2-5-turbo-std",
        "native_model_name": "kling-v2-5-turbo",
        "label": "Kling 2.5 Turbo Standard",
        "input_modes": ["text", "image"],
        "durations": [5, 10],
        "sort_order": 60,
        "kling_mode": "std",
        "supports_start_image": True,
        "start_image_required": True,
    },
    {
        "id": "kling-v2-5-turbo-pro",
        "native_model_name": "kling-v2-5-turbo",
        "label": "Kling 2.5 Turbo Pro",
        "input_modes": ["text", "image"],
        "durations": [5, 10],
        "sort_order": 61,
        "kling_mode": "pro",
        "supports_start_image": True,
        "start_image_required": True,
    },
    {
        "id": "kling-v2-6-std",
        "native_model_name": "kling-v2-6",
        "label": "Kling 2.6 Standard",
        "input_modes": ["text", "image"],
        "durations": [5, 10],
        "sort_order": 70,
        "kling_mode": "std",
        "supports_start_image": True,
        "start_image_required": True,
    },
    {
        "id": "kling-v2-6-pro",
        "native_model_name": "kling-v2-6",
        "label": "Kling 2.6 Pro",
        "input_modes": ["text", "image"],
        "durations": [5, 10],
        "sort_order": 71,
        "kling_mode": "pro",
        "supports_start_image": True,
        "start_image_required": True,
    },
    {
        "id": "kling-v3-std",
        "native_model_name": "kling-v3",
        "label": "Kling 3.0 Standard",
        "input_modes": ["text", "image"],
        "durations": VIDEO_DURATION_EXTENDED_OPTIONS,
        "sort_order": 80,
        "kling_mode": "std",
        "supports_start_image": True,
        "start_image_required": True,
    },
    {
        "id": "kling-v3-pro",
        "native_model_name": "kling-v3",
        "label": "Kling 3.0 Pro",
        "input_modes": ["text", "image"],
        "durations": VIDEO_DURATION_EXTENDED_OPTIONS,
        "sort_order": 81,
        "kling_mode": "pro",
        "supports_start_image": True,
        "start_image_required": True,
    },
    {
        "id": "kling-v3-4k-std",
        "native_model_name": "kling-v3",
        "label": "Kling 3.0 Standard 4K",
        "input_modes": ["text", "image"],
        "durations": VIDEO_DURATION_EXTENDED_OPTIONS,
        "sort_order": 82,
        "kling_mode": "std",
        "supports_start_image": True,
        "start_image_required": True,
        "resolutions": KLING_4K_RESOLUTION_OPTIONS,
    },
    {
        "id": "kling-v3-4k-pro",
        "native_model_name": "kling-v3",
        "label": "Kling 3.0 Pro 4K",
        "input_modes": ["text", "image"],
        "durations": VIDEO_DURATION_EXTENDED_OPTIONS,
        "sort_order": 83,
        "kling_mode": "pro",
        "supports_start_image": True,
        "start_image_required": True,
        "resolutions": KLING_4K_RESOLUTION_OPTIONS,
    },
    {
        "id": "kling-video-o1-std",
        "native_model_name": "kling-video-o1",
        "label": "Kling O1 Standard",
        "input_modes": ["text", "reference"],
        "durations": VIDEO_DURATION_OMNI_OPTIONS,
        "sort_order": 90,
        "kling_mode": "std",
        "video_mode_kind": "omni_video",
        "native_video_endpoint": "/v1/videos/omni-video",
        "native_reference_item_key": "image_url",
        "supports_start_image": True,
        "start_image_required": False,
        "supports_reference_images": True,
        "reference_images_required": False,
        "max_reference_images": 7,
    },
    {
        "id": "kling-video-o1-pro",
        "native_model_name": "kling-video-o1",
        "label": "Kling O1 Pro",
        "input_modes": ["text", "reference"],
        "durations": VIDEO_DURATION_OMNI_OPTIONS,
        "sort_order": 91,
        "kling_mode": "pro",
        "video_mode_kind": "omni_video",
        "native_video_endpoint": "/v1/videos/omni-video",
        "native_reference_item_key": "image_url",
        "supports_start_image": True,
        "start_image_required": False,
        "supports_reference_images": True,
        "reference_images_required": False,
        "max_reference_images": 7,
    },
    {
        "id": "kling-v3-omni-std",
        "native_model_name": "kling-v3-omni",
        "label": "Kling 3.0 Omni Standard",
        "input_modes": ["text", "reference"],
        "durations": VIDEO_DURATION_EXTENDED_OPTIONS,
        "sort_order": 100,
        "kling_mode": "std",
        "video_mode_kind": "omni_video",
        "native_video_endpoint": "/v1/videos/omni-video",
        "native_reference_item_key": "image_url",
        "supports_start_image": True,
        "start_image_required": False,
        "supports_reference_images": True,
        "reference_images_required": False,
        "max_reference_images": 7,
    },
    {
        "id": "kling-v3-omni-pro",
        "native_model_name": "kling-v3-omni",
        "label": "Kling 3.0 Omni Pro",
        "input_modes": ["text", "reference"],
        "durations": VIDEO_DURATION_EXTENDED_OPTIONS,
        "sort_order": 101,
        "kling_mode": "pro",
        "video_mode_kind": "omni_video",
        "native_video_endpoint": "/v1/videos/omni-video",
        "native_reference_item_key": "image_url",
        "supports_start_image": True,
        "start_image_required": False,
        "supports_reference_images": True,
        "reference_images_required": False,
        "max_reference_images": 7,
    },
]

FAL_KLING_MODEL_SPECS = [
    {
        "id": FAL_KLING_V1_STD_T2V_ID,
        "label": "Kling 1.0 Standard",
        "input_modes": ["text"],
        "durations": [5, 10],
        "sort_order": 80,
        "video_mode_kind": "text_to_video",
    },
    {
        "id": FAL_KLING_V1_STD_I2V_ID,
        "label": "Kling 1.0 Standard",
        "input_modes": ["image"],
        "durations": [5, 10],
        "sort_order": 81,
        "video_mode_kind": "image_to_video",
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "image_url",
    },
    {
        "id": FAL_KLING_V15_PRO_T2V_ID,
        "label": "Kling 1.5 Pro",
        "durations": [5, 10],
        "input_modes": ["text"],
        "sort_order": 90,
        "video_mode_kind": "text_to_video",
    },
    {
        "id": FAL_KLING_V15_PRO_I2V_ID,
        "label": "Kling 1.5 Pro",
        "durations": [5, 10],
        "input_modes": ["image"],
        "sort_order": 91,
        "video_mode_kind": "image_to_video",
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "image_url",
    },
    {
        "id": FAL_KLING_V16_STD_T2V_ID,
        "label": "Kling 1.6 Standard",
        "durations": [5, 10],
        "input_modes": ["text"],
        "sort_order": 100,
        "video_mode_kind": "text_to_video",
    },
    {
        "id": FAL_KLING_V16_STD_I2V_ID,
        "label": "Kling 1.6 Standard",
        "durations": [5, 10],
        "input_modes": ["image"],
        "sort_order": 101,
        "video_mode_kind": "image_to_video",
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "image_url",
    },
    {
        "id": FAL_KLING_V16_STD_ELEMENTS_ID,
        "label": "Kling 1.6 Elements Standard",
        "durations": [5, 10],
        "input_modes": ["reference"],
        "sort_order": 102,
        "video_mode_kind": "reference_to_video",
        "supports_reference_images": True,
        "reference_images_required": True,
        "reference_images_field": "input_image_urls",
        "max_reference_images": 4,
    },
    {
        "id": FAL_KLING_V16_PRO_T2V_ID,
        "label": "Kling 1.6 Pro",
        "durations": [5, 10],
        "input_modes": ["text"],
        "sort_order": 110,
        "video_mode_kind": "text_to_video",
    },
    {
        "id": FAL_KLING_V16_PRO_I2V_ID,
        "label": "Kling 1.6 Pro",
        "durations": [5, 10],
        "input_modes": ["image"],
        "sort_order": 111,
        "video_mode_kind": "image_to_video",
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "image_url",
    },
    {
        "id": FAL_KLING_V16_PRO_ELEMENTS_ID,
        "label": "Kling 1.6 Elements Pro",
        "durations": [5, 10],
        "input_modes": ["reference"],
        "sort_order": 112,
        "video_mode_kind": "reference_to_video",
        "supports_reference_images": True,
        "reference_images_required": True,
        "reference_images_field": "input_image_urls",
        "max_reference_images": 4,
    },
    {
        "id": FAL_KLING_V2_MASTER_T2V_ID,
        "label": "Kling 2.0 Master",
        "durations": [5, 10],
        "input_modes": ["text"],
        "sort_order": 115,
        "video_mode_kind": "text_to_video",
    },
    {
        "id": FAL_KLING_V2_MASTER_I2V_ID,
        "label": "Kling 2.0 Master",
        "durations": [5, 10],
        "input_modes": ["image"],
        "sort_order": 116,
        "video_mode_kind": "image_to_video",
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "image_url",
    },
    {
        "id": FAL_KLING_V21_STD_I2V_ID,
        "label": "Kling 2.1 Standard",
        "durations": [5, 10],
        "input_modes": ["image"],
        "sort_order": 118,
        "video_mode_kind": "image_to_video",
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "image_url",
    },
    {
        "id": FAL_KLING_V21_PRO_I2V_ID,
        "label": "Kling 2.1 Pro",
        "durations": [5, 10],
        "input_modes": ["image"],
        "sort_order": 119,
        "video_mode_kind": "image_to_video",
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "image_url",
    },
    {
        "id": FAL_KLING_V21_MASTER_T2V_ID,
        "label": "Kling 2.1 Master",
        "durations": [5, 10],
        "input_modes": ["text"],
        "sort_order": 120,
        "video_mode_kind": "text_to_video",
    },
    {
        "id": FAL_KLING_V21_MASTER_I2V_ID,
        "label": "Kling 2.1 Master",
        "durations": [5, 10],
        "input_modes": ["image"],
        "sort_order": 121,
        "video_mode_kind": "image_to_video",
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "image_url",
    },
    {
        "id": FAL_KLING_V25_TURBO_PRO_T2V_ID,
        "label": "Kling 2.5 Turbo Pro",
        "durations": [5, 10],
        "input_modes": ["text"],
        "sort_order": 125,
        "video_mode_kind": "text_to_video",
    },
    {
        "id": FAL_KLING_V25_TURBO_STD_I2V_ID,
        "label": "Kling 2.5 Turbo Standard",
        "durations": [5, 10],
        "input_modes": ["image"],
        "sort_order": 126,
        "video_mode_kind": "image_to_video",
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "image_url",
    },
    {
        "id": FAL_KLING_V25_TURBO_PRO_I2V_ID,
        "label": "Kling 2.5 Turbo Pro",
        "durations": [5, 10],
        "input_modes": ["image"],
        "sort_order": 127,
        "video_mode_kind": "image_to_video",
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "image_url",
    },
    {
        "id": FAL_KLING_V26_PRO_T2V_ID,
        "label": "Kling 2.6 Pro",
        "durations": [5, 10],
        "input_modes": ["text"],
        "sort_order": 130,
        "video_mode_kind": "text_to_video",
    },
    {
        "id": FAL_KLING_V26_PRO_I2V_ID,
        "label": "Kling 2.6 Pro",
        "durations": [5, 10],
        "input_modes": ["image"],
        "sort_order": 131,
        "video_mode_kind": "image_to_video",
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "start_image_url",
    },
    {
        "id": FAL_KLING_V30_STD_T2V_ID,
        "label": "Kling 3.0 Standard",
        "durations": VIDEO_DURATION_EXTENDED_OPTIONS,
        "input_modes": ["text"],
        "sort_order": 140,
        "video_mode_kind": "text_to_video",
    },
    {
        "id": FAL_KLING_V30_STD_I2V_ID,
        "label": "Kling 3.0 Standard",
        "durations": VIDEO_DURATION_EXTENDED_OPTIONS,
        "input_modes": ["image"],
        "sort_order": 141,
        "video_mode_kind": "image_to_video",
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "start_image_url",
    },
    {
        "id": FAL_KLING_V30_PRO_T2V_ID,
        "label": "Kling 3.0 Pro",
        "durations": VIDEO_DURATION_EXTENDED_OPTIONS,
        "input_modes": ["text"],
        "sort_order": 150,
        "video_mode_kind": "text_to_video",
    },
    {
        "id": FAL_KLING_V30_PRO_I2V_ID,
        "label": "Kling 3.0 Pro",
        "durations": VIDEO_DURATION_EXTENDED_OPTIONS,
        "input_modes": ["image"],
        "sort_order": 151,
        "video_mode_kind": "image_to_video",
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "start_image_url",
    },
    {
        "id": FAL_KLING_V30_4K_T2V_ID,
        "label": "Kling 3.0 4K",
        "durations": VIDEO_DURATION_EXTENDED_OPTIONS,
        "input_modes": ["text"],
        "sort_order": 152,
        "video_mode_kind": "text_to_video",
        "resolutions": KLING_4K_RESOLUTION_OPTIONS,
        "supports_generate_audio": True,
    },
    {
        "id": FAL_KLING_V30_4K_I2V_ID,
        "label": "Kling 3.0 4K",
        "durations": VIDEO_DURATION_EXTENDED_OPTIONS,
        "input_modes": ["image"],
        "sort_order": 153,
        "video_mode_kind": "image_to_video",
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "image_url",
        "resolutions": KLING_4K_RESOLUTION_OPTIONS,
        "supports_generate_audio": True,
    },
    {
        "id": FAL_KLING_O1_STD_I2V_ID,
        "label": "Kling O1 Standard",
        "durations": [3, 4, 5, 6, 7, 8, 9, 10],
        "input_modes": ["image"],
        "sort_order": 158,
        "video_mode_kind": "image_to_video",
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "start_image_url",
    },
    {
        "id": FAL_KLING_O1_REF_I2V_ID,
        "label": "Kling O1 Standard Reference",
        "durations": [3, 4, 5, 6, 7, 8, 9, 10],
        "input_modes": ["reference"],
        "sort_order": 160,
        "video_mode_kind": "reference_to_video",
        "supports_reference_images": True,
        "reference_images_required": True,
        "reference_images_field": "image_urls",
        "max_reference_images": 7,
    },
    {
        "id": FAL_KLING_O1_PRO_I2V_ID,
        "label": "Kling O1 Pro",
        "durations": [3, 4, 5, 6, 7, 8, 9, 10],
        "input_modes": ["image"],
        "sort_order": 165,
        "video_mode_kind": "image_to_video",
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "start_image_url",
    },
    {
        "id": FAL_KLING_O1_PRO_REF_I2V_ID,
        "label": "Kling O1 Pro Reference",
        "durations": [3, 4, 5, 6, 7, 8, 9, 10],
        "input_modes": ["reference"],
        "sort_order": 166,
        "video_mode_kind": "reference_to_video",
        "supports_reference_images": True,
        "reference_images_required": True,
        "reference_images_field": "image_urls",
        "max_reference_images": 7,
    },
    {
        "id": FAL_KLING_O3_STD_T2V_ID,
        "label": "Kling O3 Standard",
        "durations": VIDEO_DURATION_EXTENDED_OPTIONS,
        "input_modes": ["text"],
        "sort_order": 170,
        "video_mode_kind": "text_to_video",
    },
    {
        "id": FAL_KLING_O3_STD_I2V_ID,
        "label": "Kling O3 Standard",
        "durations": VIDEO_DURATION_EXTENDED_OPTIONS,
        "input_modes": ["image"],
        "sort_order": 171,
        "video_mode_kind": "image_to_video",
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "image_url",
    },
    {
        "id": FAL_KLING_O3_STD_V2V_ID,
        "label": "Kling O3 Standard V2V",
        "durations": VIDEO_DURATION_EXTENDED_OPTIONS,
        "input_modes": ["video"],
        "sort_order": 171,
        "video_mode_kind": "video_to_video",
        "supports_source_video": True,
        "source_video_required": True,
        "source_video_field": "video_url",
    },
    {
        "id": FAL_KLING_O3_REF_I2V_ID,
        "label": "Kling O3 Reference",
        "durations": VIDEO_DURATION_EXTENDED_OPTIONS,
        "input_modes": ["reference"],
        "sort_order": 172,
        "video_mode_kind": "reference_to_video",
        "supports_start_image": True,
        "start_image_required": False,
        "start_image_field": "start_image_url",
        "supports_reference_images": True,
        "reference_images_required": True,
        "reference_images_field": "image_urls",
        "max_reference_images": 4,
    },
    {
        "id": FAL_KLING_O3_PRO_T2V_ID,
        "label": "Kling O3 Pro",
        "durations": VIDEO_DURATION_EXTENDED_OPTIONS,
        "input_modes": ["text"],
        "sort_order": 175,
        "video_mode_kind": "text_to_video",
    },
    {
        "id": FAL_KLING_O3_PRO_I2V_ID,
        "label": "Kling O3 Pro",
        "durations": VIDEO_DURATION_EXTENDED_OPTIONS,
        "input_modes": ["image"],
        "sort_order": 176,
        "video_mode_kind": "image_to_video",
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "image_url",
    },
    {
        "id": FAL_KLING_O3_PRO_V2V_ID,
        "label": "Kling O3 Pro V2V",
        "durations": VIDEO_DURATION_EXTENDED_OPTIONS,
        "input_modes": ["video"],
        "sort_order": 176,
        "video_mode_kind": "video_to_video",
        "supports_source_video": True,
        "source_video_required": True,
        "source_video_field": "video_url",
    },
    {
        "id": FAL_KLING_O3_PRO_REF_I2V_ID,
        "label": "Kling O3 Pro Reference",
        "durations": VIDEO_DURATION_EXTENDED_OPTIONS,
        "input_modes": ["reference"],
        "sort_order": 177,
        "video_mode_kind": "reference_to_video",
        "supports_start_image": True,
        "start_image_required": False,
        "start_image_field": "start_image_url",
        "supports_reference_images": True,
        "reference_images_required": True,
        "reference_images_field": "image_urls",
        "max_reference_images": 4,
    },
    {
        "id": FAL_KLING_O3_4K_T2V_ID,
        "label": "Kling O3 4K",
        "durations": VIDEO_DURATION_EXTENDED_OPTIONS,
        "input_modes": ["text"],
        "sort_order": 178,
        "video_mode_kind": "text_to_video",
        "resolutions": KLING_4K_RESOLUTION_OPTIONS,
        "supports_generate_audio": True,
    },
    {
        "id": FAL_KLING_O3_4K_I2V_ID,
        "label": "Kling O3 4K",
        "durations": VIDEO_DURATION_EXTENDED_OPTIONS,
        "input_modes": ["image"],
        "sort_order": 179,
        "video_mode_kind": "image_to_video",
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "image_url",
        "resolutions": KLING_4K_RESOLUTION_OPTIONS,
        "supports_generate_audio": True,
    },
    {
        "id": FAL_KLING_O3_4K_REF_I2V_ID,
        "label": "Kling O3 4K Reference",
        "durations": VIDEO_DURATION_EXTENDED_OPTIONS,
        "input_modes": ["reference"],
        "sort_order": 180,
        "video_mode_kind": "reference_to_video",
        "supports_start_image": True,
        "start_image_required": False,
        "start_image_field": "image_url",
        "supports_reference_images": True,
        "reference_images_required": True,
        "reference_images_field": "image_urls",
        "max_reference_images": 7,
        "resolutions": KLING_4K_RESOLUTION_OPTIONS,
        "supports_generate_audio": True,
    },
]

for _spec in KLING_DIRECT_MODEL_SPECS:
    _spec.setdefault("resolutions", list(KLING_RESOLUTION_OPTIONS))

for _spec in FAL_KLING_MODEL_SPECS:
    _spec.setdefault("resolutions", list(KLING_RESOLUTION_OPTIONS))

FAL_SEEDANCE_MODEL_SPECS = [
    {
        "id": FAL_SEEDANCE_V1_LITE_T2V_ID,
        "label": "Seedance 1.0 Lite",
        "input_modes": ["text"],
        "durations": [5, 10],
        "resolutions": SEEDANCE_RESOLUTION_OPTIONS,
        "sort_order": 220,
        "video_mode_kind": "text_to_video",
    },
    {
        "id": FAL_SEEDANCE_V1_LITE_I2V_ID,
        "label": "Seedance 1.0 Lite",
        "input_modes": ["image"],
        "durations": [5, 10],
        "resolutions": SEEDANCE_RESOLUTION_OPTIONS,
        "sort_order": 221,
        "video_mode_kind": "image_to_video",
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "image_url",
    },
    {
        "id": FAL_SEEDANCE_V1_LITE_REF_ID,
        "label": "Seedance 1.0 Lite Reference",
        "input_modes": ["reference"],
        "durations": [5, 10],
        "resolutions": SEEDANCE_RESOLUTION_OPTIONS,
        "sort_order": 222,
        "video_mode_kind": "reference_to_video",
        "supports_reference_images": True,
        "reference_images_required": True,
        "reference_images_field": "reference_image_urls",
        "max_reference_images": 4,
    },
    {
        "id": FAL_SEEDANCE_V1_PRO_T2V_ID,
        "label": "Seedance 1.0 Pro",
        "input_modes": ["text"],
        "durations": [5, 10],
        "resolutions": SEEDANCE_RESOLUTION_OPTIONS,
        "sort_order": 225,
        "video_mode_kind": "text_to_video",
    },
    {
        "id": FAL_SEEDANCE_V1_PRO_I2V_ID,
        "label": "Seedance 1.0 Pro",
        "input_modes": ["image"],
        "durations": [5, 10],
        "resolutions": SEEDANCE_RESOLUTION_OPTIONS,
        "sort_order": 226,
        "video_mode_kind": "image_to_video",
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "image_url",
    },
    {
        "id": FAL_SEEDANCE_V1_PRO_FAST_T2V_ID,
        "label": "Seedance 1.0 Pro Fast",
        "input_modes": ["text"],
        "durations": [5, 10],
        "resolutions": SEEDANCE_RESOLUTION_OPTIONS,
        "sort_order": 230,
        "video_mode_kind": "text_to_video",
    },
    {
        "id": FAL_SEEDANCE_V1_PRO_FAST_I2V_ID,
        "label": "Seedance 1.0 Pro Fast",
        "input_modes": ["image"],
        "durations": [5, 10],
        "resolutions": SEEDANCE_RESOLUTION_OPTIONS,
        "sort_order": 231,
        "video_mode_kind": "image_to_video",
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "image_url",
    },
    {
        "id": FAL_SEEDANCE_V15_PRO_T2V_ID,
        "label": "Seedance 1.5 Pro",
        "input_modes": ["text"],
        "durations": [5, 10],
        "resolutions": SEEDANCE_RESOLUTION_OPTIONS,
        "sort_order": 240,
        "video_mode_kind": "text_to_video",
    },
    {
        "id": FAL_SEEDANCE_V15_PRO_I2V_ID,
        "label": "Seedance 1.5 Pro",
        "input_modes": ["image"],
        "durations": [5, 10],
        "resolutions": SEEDANCE_RESOLUTION_OPTIONS,
        "sort_order": 241,
        "video_mode_kind": "image_to_video",
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "image_url",
    },
    {
        "id": FAL_SEEDANCE_V20_T2V_ID,
        "label": "Seedance 2.0",
        "input_modes": ["text"],
        "durations": SEEDANCE_20_DURATION_OPTIONS,
        "aspect_ratios": SEEDANCE_20_ASPECT_RATIOS,
        "resolutions": SEEDANCE_20_RESOLUTION_OPTIONS,
        "sort_order": 250,
        "video_mode_kind": "text_to_video",
        "supports_safety_checker": False,
        "supports_generate_audio": True,
    },
    {
        "id": FAL_SEEDANCE_V20_I2V_ID,
        "label": "Seedance 2.0",
        "input_modes": ["image"],
        "durations": SEEDANCE_20_DURATION_OPTIONS,
        "aspect_ratios": SEEDANCE_20_ASPECT_RATIOS,
        "resolutions": SEEDANCE_20_RESOLUTION_OPTIONS,
        "sort_order": 251,
        "video_mode_kind": "image_to_video",
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "image_url",
        "supports_safety_checker": False,
        "supports_generate_audio": True,
    },
    {
        "id": FAL_SEEDANCE_V20_REF_ID,
        "label": "Seedance 2.0 Reference",
        "input_modes": ["reference"],
        "durations": SEEDANCE_20_DURATION_OPTIONS,
        "aspect_ratios": SEEDANCE_20_ASPECT_RATIOS,
        "resolutions": SEEDANCE_20_RESOLUTION_OPTIONS,
        "sort_order": 252,
        "video_mode_kind": "reference_to_video",
        "supports_reference_images": True,
        "reference_images_required": True,
        "reference_images_field": "image_urls",
        "max_reference_images": 9,
        "supports_safety_checker": False,
        "supports_generate_audio": True,
    },
]


def _build_hardcoded_video_models_info() -> dict:
    info: dict[str, dict] = {}
    for spec in KLING_DIRECT_MODEL_SPECS:
        info[spec["id"]] = {
            "provider": "kling",
            "provider_label": "Kling",
            "family": "kling",
            "label": spec["label"],
            "input_modes": list(spec.get("input_modes", ["text"])),
            "durations": list(spec.get("durations", VIDEO_DURATION_OPTIONS)),
            "aspect_ratios": list(spec.get("aspect_ratios", VIDEO_ASPECT_RATIOS)),
            "supports_negative_prompt": True,
            "supports_safety_checker": False,
            "supports_resolution": True,
            "resolutions": list(spec.get("resolutions", KLING_RESOLUTION_OPTIONS)),
            "supports_start_image": bool(spec.get("supports_start_image", False)),
            "start_image_required": bool(spec.get("start_image_required", False)),
            "supports_reference_images": bool(spec.get("supports_reference_images", False)),
            "reference_images_required": bool(spec.get("reference_images_required", False)),
            "max_reference_images": int(spec.get("max_reference_images", 0) or 0),
            "supports_generate_audio": bool(spec.get("supports_generate_audio", False)),
            "direct_image_limit_mb": 10,
            "sort_order": int(spec.get("sort_order", 999)),
            "video_mode_kind": spec.get("video_mode_kind", "hybrid"),
            "native_model_name": str(spec.get("native_model_name") or spec["id"]),
            "native_video_endpoint": str(spec.get("native_video_endpoint") or ""),
            "kling_mode": str(spec.get("kling_mode") or "pro"),
            "native_reference_item_key": str(spec.get("native_reference_item_key") or "image"),
        }

    for spec in FAL_KLING_MODEL_SPECS:
        info[spec["id"]] = {
            "provider": "fal",
            "provider_label": "Fal",
            "family": "kling",
            "label": spec["label"],
            "input_modes": list(spec.get("input_modes", ["text"])),
            "durations": list(spec.get("durations", VIDEO_DURATION_OPTIONS)),
            "aspect_ratios": list(spec.get("aspect_ratios", VIDEO_ASPECT_RATIOS)),
            "supports_negative_prompt": True,
            "supports_safety_checker": False,
            "supports_resolution": True,
            "resolutions": list(spec.get("resolutions", KLING_RESOLUTION_OPTIONS)),
            "supports_start_image": bool(spec.get("supports_start_image", False)),
            "start_image_required": bool(spec.get("start_image_required", False)),
            "start_image_field": spec.get("start_image_field", "image_url"),
            "supports_reference_images": bool(spec.get("supports_reference_images", False)),
            "reference_images_required": bool(spec.get("reference_images_required", False)),
            "reference_images_field": spec.get("reference_images_field", "image_urls"),
            "max_reference_images": int(spec.get("max_reference_images", 0) or 0),
            "supports_generate_audio": bool(spec.get("supports_generate_audio", False)),
            "supports_source_video": bool(spec.get("supports_source_video", False)),
            "source_video_required": bool(spec.get("source_video_required", False)),
            "source_video_field": spec.get("source_video_field", "video_url"),
            "sort_order": int(spec.get("sort_order", 999)),
            "video_mode_kind": spec.get("video_mode_kind", "text_to_video"),
        }

    for spec in FAL_SEEDANCE_MODEL_SPECS:
        info[spec["id"]] = {
            "provider": "fal",
            "provider_label": "Fal",
            "family": "seedance",
            "label": spec["label"],
            "input_modes": list(spec.get("input_modes", ["text"])),
            "durations": list(spec.get("durations", VIDEO_DURATION_OPTIONS)),
            "aspect_ratios": list(spec.get("aspect_ratios", VIDEO_ASPECT_RATIOS)),
            "supports_negative_prompt": True,
            "supports_safety_checker": bool(spec.get("supports_safety_checker", True)),
            "supports_resolution": True,
            "resolutions": list(spec.get("resolutions", SEEDANCE_RESOLUTION_OPTIONS)),
            "supports_start_image": bool(spec.get("supports_start_image", False)),
            "start_image_required": bool(spec.get("start_image_required", False)),
            "start_image_field": spec.get("start_image_field", "image_url"),
            "supports_reference_images": bool(spec.get("supports_reference_images", False)),
            "reference_images_required": bool(spec.get("reference_images_required", False)),
            "reference_images_field": spec.get("reference_images_field", "reference_image_urls"),
            "max_reference_images": int(spec.get("max_reference_images", 0) or 0),
            "supports_generate_audio": bool(spec.get("supports_generate_audio", False)),
            "sort_order": int(spec.get("sort_order", 999)),
            "video_mode_kind": spec.get("video_mode_kind", "text_to_video"),
        }

    for spec in RUNWAY_VIDEO_MODEL_SPECS:
        info[spec["id"]] = {
            "provider": "runway",
            "provider_label": "Runway",
            "family": "runway-video",
            "label": spec["label"],
            "runway_model": spec.get("runway_model", ""),
            "input_modes": list(spec.get("input_modes", ["text"])),
            "durations": list(spec.get("duration_options", [])),
            "aspect_ratios": list(spec.get("aspect_ratios", [])),
            "supports_prompt": True,
            "supports_negative_prompt": False,
            "supports_safety_checker": False,
            "supports_resolution": False,
            "supports_duration": bool(spec.get("supports_duration", True)),
            "supports_aspect_ratio": bool(spec.get("supports_aspect_ratio", True)),
            "supports_start_image": bool(spec.get("supports_start_image", False)),
            "start_image_required": bool(spec.get("start_image_required", False)),
            "supports_source_video": bool(spec.get("supports_source_video", False)),
            "source_video_required": bool(spec.get("source_video_required", False)),
            "supports_reference_images": False,
            "reference_images_required": False,
            "max_reference_images": 0,
            "sort_order": int(spec.get("sort_order", 999)),
            "video_mode_kind": spec.get("video_mode_kind", "text_to_video"),
        }

    for spec in LUMA_VIDEO_MODEL_SPECS:
        info[spec["id"]] = {
            "provider": "luma",
            "provider_label": "Luma",
            "family": "luma-video",
            "label": spec["label"],
            "native_model_name": spec.get("native_model_name", ""),
            "input_modes": list(spec.get("input_modes", ["text"])),
            "durations": list(spec.get("durations", [5, 9])),
            "aspect_ratios": list(spec.get("aspect_ratios", ["16:9", "9:16", "1:1"])),
            "supports_prompt": True,
            "supports_negative_prompt": False,
            "supports_safety_checker": False,
            "supports_resolution": True,
            "resolutions": list(spec.get("resolutions", ["540p", "720p", "1080p", "4k"])),
            "supports_start_image": bool(spec.get("supports_start_image", False)),
            "start_image_required": bool(spec.get("start_image_required", False)),
            "supports_source_video": False,
            "source_video_required": False,
            "supports_reference_images": False,
            "reference_images_required": False,
            "max_reference_images": 0,
            "sort_order": int(spec.get("sort_order", 999)),
            "video_mode_kind": spec.get("video_mode_kind", "text_to_video"),
        }

    for spec in FAL_WAN_MODEL_SPECS:
        info[spec["id"]] = {
            "provider": "fal",
            "provider_label": "Fal",
            "family": "wan-video",
            "label": spec["label"],
            "fal_endpoint": spec.get("fal_endpoint", ""),
            "input_modes": list(spec.get("input_modes", ["text"])),
            "durations": list(spec.get("durations", WAN_DURATION_OPTIONS)),
            "aspect_ratios": list(spec.get("aspect_ratios", WAN_ASPECT_RATIOS)),
            "supports_negative_prompt": True,
            "supports_safety_checker": bool(spec.get("supports_safety_checker", True)),
            "supports_output_safety_checker": bool(spec.get("supports_output_safety_checker", False)),
            "supports_resolution": True,
            "resolutions": list(spec.get("resolutions", WAN_RESOLUTION_OPTIONS)),
            "supports_start_image": bool(spec.get("supports_start_image", False)),
            "start_image_required": bool(spec.get("start_image_required", False)),
            "start_image_field": spec.get("start_image_field", "image_url"),
            "supports_reference_images": bool(spec.get("supports_reference_images", False)),
            "reference_images_required": bool(spec.get("reference_images_required", False)),
            "reference_images_field": spec.get("reference_images_field", "reference_image_urls"),
            "max_reference_images": int(spec.get("max_reference_images", 0) or 0),
            "supports_source_video": bool(spec.get("supports_source_video", False)),
            "source_video_required": bool(spec.get("source_video_required", False)),
            "source_video_field": spec.get("source_video_field", "video_url"),
            "supports_driving_audio": bool(spec.get("supports_driving_audio", False)),
            "supports_reference_videos": bool(spec.get("supports_reference_videos", False)),
            "max_reference_videos": int(spec.get("max_reference_videos", 0) or 0),
            "supports_multi_shots": bool(spec.get("supports_multi_shots", False)),
            "sort_order": int(spec.get("sort_order", 999)),
            "video_mode_kind": spec.get("video_mode_kind", "text_to_video"),
        }
    info[FAL_LTX_VIDEO_T2V_ID] = {
        "provider": "fal",
        "provider_label": "Fal",
        "family": "ltx-video",
        "label": "LTX Video",
        "input_modes": ["text"],
        "durations": [5],
        "aspect_ratios": [],
        "supports_negative_prompt": True,
        "supports_safety_checker": False,
        "supports_output_safety_checker": False,
        "supports_resolution": False,
        "supports_duration": False,
        "supports_aspect_ratio": False,
        "supports_start_image": False,
        "start_image_required": False,
        "supports_reference_images": False,
        "reference_images_required": False,
        "max_reference_images": 0,
        "sort_order": 260,
        "video_mode_kind": "text_to_video",
    }
    info[FAL_LTX_VIDEO_I2V_ID] = {
        "provider": "fal",
        "provider_label": "Fal",
        "family": "ltx-video",
        "label": "LTX Video",
        "input_modes": ["image"],
        "durations": [5],
        "aspect_ratios": [],
        "supports_negative_prompt": True,
        "supports_safety_checker": False,
        "supports_output_safety_checker": False,
        "supports_resolution": False,
        "supports_duration": False,
        "supports_aspect_ratio": False,
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "image_url",
        "supports_reference_images": False,
        "reference_images_required": False,
        "max_reference_images": 0,
        "sort_order": 261,
        "video_mode_kind": "image_to_video",
    }
    info[FAL_LTX_VIDEO_LORA_I2V_ID] = {
        "provider": "fal",
        "provider_label": "Fal",
        "family": "ltx-video",
        "label": "LTX Video LoRA",
        "input_modes": ["image"],
        "durations": [5],
        "aspect_ratios": LTX_VIDEO_LORA_ASPECT_RATIOS,
        "supports_negative_prompt": True,
        "supports_safety_checker": True,
        "supports_output_safety_checker": False,
        "supports_resolution": True,
        "resolutions": LTX_VIDEO_LORA_RESOLUTION_OPTIONS,
        "supports_duration": False,
        "supports_aspect_ratio": True,
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "image_url",
        "supports_reference_images": False,
        "reference_images_required": False,
        "max_reference_images": 0,
        "sort_order": 262,
        "video_mode_kind": "image_to_video",
    }
    info[FAL_LTX_23_22B_I2V_ID] = {
        "provider": "fal",
        "provider_label": "Fal",
        "family": "ltx-video",
        "label": "LTX 2.3 22B",
        "input_modes": ["image"],
        "durations": [5],
        "aspect_ratios": [],
        "supports_negative_prompt": False,
        "supports_safety_checker": True,
        "supports_output_safety_checker": False,
        "supports_resolution": True,
        "resolutions": LTX_23_VIDEO_SIZE_OPTIONS,
        "supports_duration": False,
        "supports_aspect_ratio": False,
        "supports_start_image": True,
        "start_image_required": True,
        "start_image_field": "image_url",
        "supports_reference_images": False,
        "reference_images_required": False,
        "max_reference_images": 0,
        "supports_generate_audio": True,
        "sort_order": 263,
        "video_mode_kind": "image_to_video",
    }
    info[FAL_SEEDVR_VIDEO_ID] = {
        "provider": "fal",
        "provider_label": "Fal",
        "family": "seedvr-video",
        "label": "SeedVR2 Video",
        "input_modes": ["video"],
        "durations": [],
        "aspect_ratios": [],
        "supports_prompt": False,
        "supports_negative_prompt": False,
        "supports_safety_checker": False,
        "supports_resolution": False,
        "supports_duration": False,
        "supports_aspect_ratio": False,
        "supports_start_image": False,
        "start_image_required": False,
        "supports_source_video": True,
        "source_video_required": True,
        "supports_reference_images": False,
        "reference_images_required": False,
        "max_reference_images": 0,
        "supports_seedvr_video_settings": True,
        "video_target_resolutions": ["720p", "1080p", "1440p", "2160p"],
        "video_output_formats": ["X264 (.mp4)", "VP9 (.webm)", "PRORES4444 (.mov)", "GIF (.gif)"],
        "video_output_qualities": ["low", "medium", "high", "maximum"],
        "video_output_write_modes": ["fast", "balanced", "small"],
        "sort_order": 310,
        "video_mode_kind": "video_upscale",
    }
    return info


def _load_generated_video_catalog() -> dict | None:
    """The fetched fal catalog, or None when the file is missing or unreadable."""
    try:
        return fal_catalog.load_generated(FAL_GENERATED_PATH)
    except Exception:
        return None


def _build_video_models_info() -> dict:
    """
    Hardcoded specs are the base so the app never depends on a successful fetch.
    Entries from models_video.generated.json replace or extend them by id.
    """
    info = _build_hardcoded_video_models_info()
    generated = _load_generated_video_catalog()
    if generated:
        for model_id, entry in (generated.get("models") or {}).items():
            if isinstance(entry, dict) and entry.get("provider") == "fal":
                info[str(model_id)] = dict(entry)
    return info


VIDEO_MODELS_INFO = _build_video_models_info()

VIDEO_MODEL_FAMILIES_BASE = {
    "kling": {
        "label": "Kling",
        "default_provider": "kling",
        "provider_order": ["kling", "fal"],
        "providers": {
            "kling": KLING_DIRECT_TEXT_DEFAULT_ID,
            "fal": FAL_KLING_V30_PRO_T2V_ID,
        },
    },
    "seedance": {
        "label": "Seedance",
        "default_provider": "fal",
        "provider_order": ["fal"],
        "providers": {
            "fal": FAL_SEEDANCE_V20_T2V_ID,
        },
    },
    "wan-video": {
        "label": "Wan 2.7",
        "default_provider": "fal",
        "provider_order": ["fal"],
        "providers": {
            "fal": FAL_WAN_T2V_ID,
        },
    },
    "ltx-video": {
        "label": "LTX Video",
        "default_provider": "fal",
        "provider_order": ["fal"],
        "providers": {
            "fal": FAL_LTX_VIDEO_T2V_ID,
        },
    },
    "seedvr-video": {
        "label": "SeedVR2",
        "default_provider": "fal",
        "provider_order": ["fal"],
        "providers": {
            "fal": FAL_SEEDVR_VIDEO_ID,
        },
    },
    "runway-video": {
        "label": "Runway",
        "default_provider": "runway",
        "provider_order": ["runway"],
        "providers": {
            "runway": RUNWAY_GEN45_T2V_ID,
        },
    },
    "luma-video": {
        "label": "Luma",
        "default_provider": "luma",
        "provider_order": ["luma"],
        "providers": {
            "luma": LUMA_RAY2_T2V_ID,
        },
    },
}


def _build_video_model_families() -> dict:
    families = {key: dict(value) for key, value in VIDEO_MODEL_FAMILIES_BASE.items()}
    generated = _load_generated_video_catalog()
    if not generated:
        return families
    present = {str(info.get("family") or "") for info in VIDEO_MODELS_INFO.values()}
    for key, spec in (generated.get("families") or {}).items():
        if not isinstance(spec, dict) or key in families or key not in present:
            continue
        providers = dict(spec.get("providers") or {})
        providers.setdefault("fal", "")
        families[key] = {
            "label": str(spec.get("label") or key),
            "default_provider": str(spec.get("default_provider") or "fal"),
            "provider_order": list(spec.get("provider_order") or ["fal"]),
            "providers": providers,
        }
    return families


VIDEO_MODEL_FAMILIES = _build_video_model_families()


def get_video_model_candidates(family_key: str = "", provider_key: str = "", input_mode: str = "") -> list[tuple[str, dict]]:
    mode_key = str(input_mode or "").strip().lower()
    matches = []
    for model_id, model_info in VIDEO_MODELS_INFO.items():
        if family_key and model_info.get("family") != family_key:
            continue
        if provider_key and model_info.get("provider") != provider_key:
            continue
        supported_modes = [str(mode).strip().lower() for mode in model_info.get("input_modes", [])]
        if mode_key and supported_modes and mode_key not in supported_modes:
            continue
        matches.append((model_id, model_info))
    matches.sort(key=lambda item: (int(item[1].get("sort_order", 999)), str(item[1].get("label") or item[0]).lower()))
    return matches


def _build_video_pricing() -> dict:
    pricing = {
        model_id: {str(duration): 0.0 for duration in VIDEO_DURATION_ALL_OPTIONS}
        for model_id in VIDEO_MODELS_INFO
    }
    for known_id, known_prices in _VIDEO_PRICING_OVERRIDES.items():
        pricing[known_id] = dict(known_prices)
    return pricing


_VIDEO_PRICING_OVERRIDES: dict[str, dict] = {}
VIDEO_PRICING = _VIDEO_PRICING_OVERRIDES
VIDEO_PRICING["kling-v3-4k-std"] = {str(duration): round(duration * 0.05, 4) for duration in VIDEO_DURATION_ALL_OPTIONS}
VIDEO_PRICING["kling-v3-4k-pro"] = {str(duration): round(duration * 0.10, 4) for duration in VIDEO_DURATION_ALL_OPTIONS}
for model_id in (
    FAL_WAN_T2V_ID,
    FAL_WAN_I2V_ID,
    FAL_WAN_FIRST_LAST_ID,
    FAL_WAN_CONTINUE_ID,
    FAL_WAN_REF_ID,
    FAL_WAN_EDIT_ID,
):
    VIDEO_PRICING[model_id] = {str(duration): round(duration * 0.10, 4) for duration in VIDEO_DURATION_ALL_OPTIONS}
for model_id in (
    FAL_KLING_V30_4K_T2V_ID,
    FAL_KLING_V30_4K_I2V_ID,
    FAL_KLING_O3_4K_T2V_ID,
    FAL_KLING_O3_4K_I2V_ID,
    FAL_KLING_O3_4K_REF_I2V_ID,
):
    VIDEO_PRICING[model_id] = {str(duration): round(duration * 0.42, 4) for duration in VIDEO_DURATION_ALL_OPTIONS}
for model_id in (FAL_LTX_VIDEO_T2V_ID, FAL_LTX_VIDEO_I2V_ID):
    VIDEO_PRICING[model_id] = {str(duration): 0.02 for duration in VIDEO_DURATION_ALL_OPTIONS}
VIDEO_PRICING[FAL_LTX_VIDEO_LORA_I2V_ID] = {str(duration): 0.20 for duration in VIDEO_DURATION_ALL_OPTIONS}
VIDEO_PRICING = _build_video_pricing()


# ---------------------------------------------------------------------------
# Live fal catalog - refresh / add / reload
# ---------------------------------------------------------------------------
_CATALOG_LOCK = threading.Lock()


def reload_video_catalog() -> None:
    """Rebind the module-level video registries after the generated file changes."""
    global VIDEO_MODELS_INFO, VIDEO_MODEL_FAMILIES, VIDEO_PRICING
    VIDEO_MODELS_INFO = _build_video_models_info()
    VIDEO_MODEL_FAMILIES = _build_video_model_families()
    VIDEO_PRICING = _build_video_pricing()


def get_fal_catalog_status() -> dict:
    endpoints = fal_catalog.load_endpoints(FAL_ENDPOINTS_PATH)
    generated = _load_generated_video_catalog()
    return {
        "endpoints_path": os.path.basename(FAL_ENDPOINTS_PATH),
        "generated_path": os.path.basename(FAL_GENERATED_PATH),
        "endpoint_count": len(endpoints.get("endpoints") or []),
        "generated_at": (generated or {}).get("generated_at") or "",
        "generated_count": len((generated or {}).get("models") or {}),
        "live": bool(generated),
    }


def refresh_fal_catalog(endpoint_cfgs: list[dict] | None = None, max_workers: int = 4) -> dict:
    """
    Fetch every endpoint in fal_endpoints.json, write models_video.generated.json
    atomically, reload the registries. If any endpoint fails the generated file
    is left untouched (all or nothing) and the report says which ones failed.
    """
    with _CATALOG_LOCK:
        endpoints_doc = fal_catalog.load_endpoints(FAL_ENDPOINTS_PATH)
        cfgs = list(endpoint_cfgs if endpoint_cfgs is not None else endpoints_doc.get("endpoints") or [])
        if not cfgs:
            raise ValueError(f"No endpoints listed in {os.path.basename(FAL_ENDPOINTS_PATH)}.")
        previous_doc = _load_generated_video_catalog() or {}
        previous_models = dict(previous_doc.get("models") or {})
        base_specs = _build_hardcoded_video_models_info()
        models, report = fal_catalog.fetch_all(
            cfgs,
            base_specs=base_specs,
            previous=previous_models,
            max_workers=max_workers,
        )
        failed = [row for row in report if row.get("status") == "failed"]
        written = False
        if not failed:
            fal_catalog.write_json_atomic(
                FAL_GENERATED_PATH,
                fal_catalog.build_generated_document(models, endpoints_doc.get("families") or {}),
            )
            reload_video_catalog()
            written = True
        counts = {key: sum(1 for row in report if row.get("status") == key) for key in ("added", "unchanged", "changed", "failed")}
        return {
            "ok": not failed,
            "written": written,
            "counts": counts,
            "report": report,
            "status": get_fal_catalog_status(),
        }


def add_fal_catalog_endpoint(row: dict) -> dict:
    """Append one endpoint to fal_endpoints.json (atomic) and regenerate."""
    endpoint_id = str(row.get("endpoint") or "").strip()
    if not endpoint_id:
        raise ValueError("Endpoint id is required.")
    with _CATALOG_LOCK:
        endpoints_doc = fal_catalog.load_endpoints(FAL_ENDPOINTS_PATH)
        existing = [str(cfg.get("endpoint") or cfg.get("id") or "") for cfg in endpoints_doc["endpoints"]]
        if endpoint_id in existing:
            raise ValueError(f"{endpoint_id} is already in the catalog.")
        new_cfg = {"endpoint": endpoint_id}
        for key in ("id", "label", "family", "input_modes", "video_mode_kind"):
            if row.get(key):
                new_cfg[key] = row[key]
        try:
            new_cfg["sort_order"] = int(row.get("sort_order") or 900)
        except Exception:
            new_cfg["sort_order"] = 900
        family_key = str(new_cfg.get("family") or "fal-video")
        new_cfg["family"] = family_key
        families = endpoints_doc.setdefault("families", {})
        if family_key not in VIDEO_MODEL_FAMILIES_BASE and family_key not in families:
            families[family_key] = {
                "label": str(row.get("family_label") or family_key.replace("-", " ").title()),
                "default_provider": "fal",
                "provider_order": ["fal"],
                "providers": {"fal": endpoint_id},
            }
        endpoints_doc["endpoints"].append(new_cfg)
        fal_catalog.write_json_atomic(FAL_ENDPOINTS_PATH, endpoints_doc)
    return refresh_fal_catalog()


def build_asset_page_context(kind: str) -> dict:
    cfg = ASSET_GALLERY_PAGE_CONFIG.get(kind, ASSET_GALLERY_PAGE_CONFIG["history"])
    return {
        "asset_kind": kind,
        "page_title": "Assets",
        "page_subtitle": cfg.get("subtitle", "Browse history, references, loved images, and videos in one place."),
        "asset_gallery_config": ASSET_GALLERY_PAGE_CONFIG,
        "initial_asset_items": collect_asset_records(kind),
        "user": session["user"],
    }


@app.route("/images")
@login_required
def images_gallery():
    kind = str(request.args.get("kind") or "history").strip().lower()
    if kind not in ASSET_GALLERY_PAGE_CONFIG:
        kind = "history"
    return render_template("asset_gallery.html", **build_asset_page_context(kind))


@app.route("/loved")
@login_required
def loved_gallery():
    return redirect(url_for("images_gallery", kind="loved"))


@app.route("/references")
@login_required
def references_gallery():
    return redirect(url_for("images_gallery", kind="references"))


@app.route("/history")
@login_required
def history_gallery():
    return redirect(url_for("images_gallery", kind="history"))


def collect_video_asset_records(max_load: int | None = None) -> list[dict]:
    limit = max_load if max_load is not None else None
    result = []
    if not os.path.isdir(VIDEOS_DIR):
        return result

    for meta_file in list_meta_files_recursive(VIDEOS_DIR):
        if limit is not None and len(result) >= limit:
            break
        try:
            with open(meta_file, "r", encoding="utf-8") as fh:
                meta = json.load(fh)
            filename = str(meta.get("filename") or "").strip()
            if not filename:
                continue
            relpath = str(meta.get("assetRelpath") or os.path.relpath(os.path.join(os.path.dirname(meta_file), filename), VIDEOS_DIR)).replace("\\", "/")
            video_path = os.path.join(VIDEOS_DIR, relpath.replace("/", os.sep))
            if not os.path.exists(video_path):
                continue
            generated_at = str(meta.get("generated_at") or "")
            date_key = derive_asset_date_key(relpath, generated_at)
            meta = dict(meta)
            meta["assetRelpath"] = relpath
            result.append({
                "id": f"videos:{relpath}".replace("/", ":"),
                "kind": "videos",
                "url": build_asset_public_url("videos", relpath),
                "download_url": build_asset_public_url("videos", relpath),
                "date": date_key,
                "filename": filename,
                "relpath": relpath,
                "generated_at": generated_at,
                "sortTimestamp": generated_at or f"{date_key}T00:00:00",
                "prompt_preview": str(meta.get("prompt") or meta.get("text") or filename)[:120],
                "text": str(meta.get("text") or meta.get("prompt") or ""),
                "mime_type": str(meta.get("mime_type") or "video/mp4"),
                "poster_url": str(meta.get("poster_url") or ""),
                "delete_url": f"/api/videos/{relpath}",
                "folder_open_payload": {
                    "kind": "videos",
                    "date": date_key,
                    "filename": filename,
                    "relpath": relpath,
                },
                "params": meta,
            })
        except Exception:
            continue
    return result


@app.route("/loved/<path:asset_relpath>")
@login_required
def serve_loved(asset_relpath):
    safe_relpath = resolve_asset_relpath(relpath=asset_relpath)
    local_path = safe_asset_path(LOVED_DIR, safe_relpath)
    return send_from_directory(os.path.dirname(local_path), os.path.basename(local_path))


@app.route("/reference-archive/<date_str>/<filename>")
@login_required
def serve_reference_archive(date_str, filename):
    day_path = os.path.join(REFERENCE_ARCHIVE_DIR, date_str)
    return send_from_directory(day_path, filename)


@app.route("/reference-mask/<date_str>/<filename>")
@login_required
def serve_reference_mask(date_str, filename):
    day_path = os.path.join(REFERENCE_MASKS_DIR, date_str)
    return send_from_directory(day_path, filename)


@app.route("/reference-render/<date_str>/<filename>")
@login_required
def serve_reference_render(date_str, filename):
    day_path = os.path.join(REFERENCE_RENDERS_DIR, date_str)
    return send_from_directory(day_path, filename)


@app.route("/generations/<path:asset_relpath>")
@login_required
def serve_generation(asset_relpath):
    safe_relpath = resolve_asset_relpath(relpath=asset_relpath)
    local_path = safe_asset_path(GENERATIONS_DIR, safe_relpath)
    return send_from_directory(os.path.dirname(local_path), os.path.basename(local_path))


@app.route("/videos/<path:asset_relpath>")
@login_required
def serve_video(asset_relpath):
    safe_relpath = resolve_asset_relpath(relpath=asset_relpath)
    local_path = safe_asset_path(VIDEOS_DIR, safe_relpath)
    return send_from_directory(os.path.dirname(local_path), os.path.basename(local_path))


@app.route("/edit-sessions/<path:asset_relpath>")
@login_required
def serve_edit_session(asset_relpath):
    safe_relpath = resolve_asset_relpath(relpath=asset_relpath)
    local_path = safe_asset_path(EDIT_SESSIONS_DIR, safe_relpath)
    return send_from_directory(os.path.dirname(local_path), os.path.basename(local_path))


def open_file_in_folder(img_path: str):
    if sys.platform.startswith("win"):
        subprocess.Popen(["explorer", "/select,", img_path])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", img_path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(img_path)])


def resolve_asset_image_path(kind: str, date_str: str = "", filename: str = "", relpath: str = "") -> tuple[str, str, str]:
    kind = str(kind or "").strip()
    mapping = {
        "history": GENERATIONS_DIR,
        "generations": GENERATIONS_DIR,
        "videos": VIDEOS_DIR,
        "video": VIDEOS_DIR,
        "loved": LOVED_DIR,
        "references": REFERENCE_ARCHIVE_DIR,
        "reference_archive": REFERENCE_ARCHIVE_DIR,
    }
    root_dir = mapping.get(kind)
    if not root_dir:
        raise ValueError("Invalid asset kind.")
    safe_relpath = resolve_asset_relpath(relpath=relpath, date_str=date_str, filename=filename)
    img_path = safe_asset_path(root_dir, safe_relpath)
    return os.path.realpath(root_dir), img_path, safe_relpath


def build_edit_session_relpath(asset_relpath: str) -> str:
    safe_relpath = resolve_asset_relpath(relpath=asset_relpath)
    stem, _ = os.path.splitext(safe_relpath)
    return f"{stem}.json"


def normalize_edit_session_kind(kind: str) -> str:
    kind = str(kind or "").strip().lower()
    mapping = {
        "history": "history",
        "generations": "history",
        "videos": "videos",
        "video": "videos",
        "loved": "loved",
        "references": "references",
        "reference_archive": "references",
    }
    session_kind = mapping.get(kind)
    if not session_kind:
        raise ValueError("Invalid edit session kind.")
    return session_kind


def prefix_edit_session_asset_relpath(kind: str, relpath: str) -> str:
    session_kind = normalize_edit_session_kind(kind)
    safe_relpath = str(relpath or "").strip().replace("\\", "/").strip("/")
    if not safe_relpath:
        return ""
    if safe_relpath == session_kind or safe_relpath.startswith(f"{session_kind}/"):
        return safe_relpath
    known_prefixes = ("history/", "videos/", "loved/", "references/")
    if safe_relpath in {"history", "videos", "loved", "references"} or safe_relpath.startswith(known_prefixes):
        return safe_relpath
    return f"{session_kind}/{safe_relpath}"


def resolve_edit_session_path(kind: str, asset_relpath: str) -> tuple[str, str, str]:
    kind = str(kind or "").strip().lower()
    session_kind = normalize_edit_session_kind(kind)
    root_dir = os.path.realpath(EDIT_SESSIONS_DIR)
    session_relpath = prefix_edit_session_asset_relpath(session_kind, build_edit_session_relpath(asset_relpath))
    abs_path = os.path.realpath(os.path.join(root_dir, session_relpath.replace("/", os.sep)))
    if not abs_path.startswith(root_dir + os.sep):
        raise ValueError("Invalid edit session path.")
    return root_dir, abs_path, session_relpath


def build_edit_session_segments_relpath(asset_relpath: str) -> str:
    safe_relpath = resolve_asset_relpath(relpath=asset_relpath)
    stem, _ = os.path.splitext(safe_relpath)
    return f"{stem}__segments"


def resolve_edit_session_segments_dir(kind: str, asset_relpath: str) -> tuple[str, str, str]:
    session_kind = normalize_edit_session_kind(kind)
    root_dir = os.path.realpath(EDIT_SESSIONS_DIR)
    segment_relpath = prefix_edit_session_asset_relpath(session_kind, build_edit_session_segments_relpath(asset_relpath))
    abs_dir = os.path.realpath(os.path.join(root_dir, segment_relpath.replace("/", os.sep)))
    if not abs_dir.startswith(root_dir + os.sep):
        raise ValueError("Invalid edit segment path.")
    return root_dir, abs_dir, segment_relpath


def build_edit_session_mask_url(mask_relpath: str) -> str:
    return build_asset_public_url("edit-sessions", resolve_asset_relpath(relpath=mask_relpath))


def build_edit_session_asset_url(asset_relpath: str) -> str:
    return build_asset_public_url("edit-sessions", resolve_asset_relpath(relpath=asset_relpath))


def build_edit_session_preview_url(asset_relpath: str) -> str:
    return build_asset_public_url("edit-sessions", resolve_asset_relpath(relpath=asset_relpath))


def create_edit_selection_preview_image(source_asset_path: str, mask_path: str, preview_path: str, max_side: int = 768) -> None:
    with Image.open(source_asset_path) as base_image:
        base_rgba = ImageOps.exif_transpose(base_image).convert("RGBA")
    with Image.open(mask_path) as mask_image:
        mask_rgba = ImageOps.exif_transpose(mask_image).convert("RGBA")
    if mask_rgba.size != base_rgba.size:
        mask_rgba = mask_rgba.resize(base_rgba.size, Image.Resampling.LANCZOS)

    isolated = Image.new("RGBA", base_rgba.size, (0, 0, 0, 0))
    isolated.paste(base_rgba, (0, 0), mask_rgba.getchannel("A"))
    alpha = isolated.getchannel("A")
    bbox = alpha.getbbox()
    if bbox:
        isolated = isolated.crop(bbox)
    width, height = isolated.size
    if width <= 0 or height <= 0:
        isolated = base_rgba.copy()
        width, height = isolated.size
    scale = min(1.0, float(max_side) / float(max(width, height) or 1))
    if scale < 1.0:
        isolated = isolated.resize(
            (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
            Image.Resampling.LANCZOS,
        )
    os.makedirs(os.path.dirname(preview_path), exist_ok=True)
    isolated.save(preview_path, format="PNG")


def ensure_edit_selection_preview(kind: str, source_asset_path: str, selection: dict) -> dict:
    if not isinstance(selection, dict):
        return selection
    mask_relpath = str(selection.get("maskRelpath") or "").strip().replace("\\", "/").strip("/")
    if not mask_relpath:
        return selection

    preview_relpath = str(selection.get("previewRelpath") or "").strip().replace("\\", "/").strip("/")
    if preview_relpath:
        try:
            preview_relpath = prefix_edit_session_asset_relpath(kind, resolve_asset_relpath(relpath=preview_relpath))
            safe_root = os.path.realpath(EDIT_SESSIONS_DIR)
            preview_abs_path = os.path.realpath(os.path.join(EDIT_SESSIONS_DIR, preview_relpath.replace("/", os.sep)))
            if preview_abs_path.startswith(safe_root + os.sep) and os.path.isfile(preview_abs_path):
                selection["previewRelpath"] = preview_relpath
                selection["previewUrl"] = build_edit_session_preview_url(preview_relpath)
                return selection
            selection["previewRelpath"] = ""
            selection["previewUrl"] = ""
        except Exception:
            selection["previewRelpath"] = ""
            selection["previewUrl"] = ""

    try:
        safe_root = os.path.realpath(EDIT_SESSIONS_DIR)
        mask_abs_path = os.path.realpath(os.path.join(EDIT_SESSIONS_DIR, mask_relpath.replace("/", os.sep)))
        if not mask_abs_path.startswith(safe_root + os.sep) or not os.path.isfile(mask_abs_path):
            return selection
        stem, _ = os.path.splitext(mask_relpath)
        preview_relpath = f"{stem}__preview.png"
        preview_abs_path = os.path.realpath(os.path.join(EDIT_SESSIONS_DIR, preview_relpath.replace("/", os.sep)))
        if not preview_abs_path.startswith(safe_root + os.sep):
            return selection
        if not os.path.isfile(preview_abs_path):
            create_edit_selection_preview_image(source_asset_path, mask_abs_path, preview_abs_path)
        selection["previewRelpath"] = preview_relpath
        selection["previewUrl"] = build_edit_session_preview_url(preview_relpath)
    except Exception:
        selection["previewRelpath"] = ""
        selection["previewUrl"] = ""
    return selection


def normalize_edit_session_selection_payload(selection: dict | None, fallback_index: int = 0, kind: str = "") -> dict:
    source = selection if isinstance(selection, dict) else {}
    result = {
        "id": str(source.get("id") or f"selection-{fallback_index + 1}"),
        "name": str(source.get("name") or f"Selection {fallback_index + 1}"),
        "prompt": str(source.get("prompt") or ""),
        "describedPrompt": str(source.get("describedPrompt") or ""),
        "notes": str(source.get("notes") or ""),
        "visible": bool(source.get("visible", True)),
        "geometry": source.get("geometry") if isinstance(source.get("geometry"), dict) else None,
        "sourcePrompt": str(source.get("sourcePrompt") or ""),
        "score": source.get("score"),
        "box": source.get("box") if isinstance(source.get("box"), list) else None,
        "refImages": normalize_ref_image_payloads(source.get("refImages"), 16),
        "targetType": str(source.get("targetType") or "image").strip().lower() or "image",
        "previewRelpath": "",
        "previewUrl": "",
    }
    raw_mask_relpath = str(source.get("maskRelpath") or "").strip().replace("\\", "/").strip("/")
    if raw_mask_relpath:
        try:
            result["maskRelpath"] = prefix_edit_session_asset_relpath(kind, resolve_asset_relpath(relpath=raw_mask_relpath))
        except Exception:
            result["maskRelpath"] = ""
    else:
        result["maskRelpath"] = ""
    raw_mask_url = str(source.get("maskUrl") or "").strip()
    result["maskUrl"] = build_edit_session_mask_url(result["maskRelpath"]) if result["maskRelpath"] else raw_mask_url
    raw_track_relpath = str(source.get("trackVideoRelpath") or "").strip().replace("\\", "/").strip("/")
    if raw_track_relpath:
        try:
            result["trackVideoRelpath"] = prefix_edit_session_asset_relpath(kind, resolve_asset_relpath(relpath=raw_track_relpath))
        except Exception:
            result["trackVideoRelpath"] = ""
    else:
        result["trackVideoRelpath"] = ""
    raw_track_url = str(source.get("trackVideoUrl") or "").strip()
    result["trackVideoUrl"] = build_edit_session_asset_url(result["trackVideoRelpath"]) if result["trackVideoRelpath"] else raw_track_url
    raw_bbox_relpath = str(source.get("bboxZipRelpath") or "").strip().replace("\\", "/").strip("/")
    if raw_bbox_relpath:
        try:
            result["bboxZipRelpath"] = prefix_edit_session_asset_relpath(kind, resolve_asset_relpath(relpath=raw_bbox_relpath))
        except Exception:
            result["bboxZipRelpath"] = ""
    else:
        result["bboxZipRelpath"] = ""
    raw_bbox_url = str(source.get("bboxZipUrl") or "").strip()
    result["bboxZipUrl"] = build_edit_session_asset_url(result["bboxZipRelpath"]) if result["bboxZipRelpath"] else raw_bbox_url
    raw_preview_relpath = str(source.get("previewRelpath") or "").strip().replace("\\", "/").strip("/")
    if raw_preview_relpath:
        try:
            result["previewRelpath"] = prefix_edit_session_asset_relpath(kind, resolve_asset_relpath(relpath=raw_preview_relpath))
        except Exception:
            result["previewRelpath"] = ""
    raw_preview_url = str(source.get("previewUrl") or "").strip()
    result["previewUrl"] = build_edit_session_preview_url(result["previewRelpath"]) if result["previewRelpath"] else raw_preview_url
    return result


def collect_edit_session_mask_relpaths(selections: list[dict] | None, kind: str = "") -> set[str]:
    relpaths: set[str] = set()
    for index, selection in enumerate(selections or []):
        normalized = normalize_edit_session_selection_payload(selection, index, kind=kind)
        if normalized.get("maskRelpath"):
            relpaths.add(normalized["maskRelpath"])
    return relpaths


def delete_edit_session_mask_relpaths(mask_relpaths: set[str]) -> None:
    safe_root = os.path.realpath(EDIT_SESSIONS_DIR)
    for relpath in mask_relpaths:
        try:
            local_path = os.path.realpath(os.path.join(EDIT_SESSIONS_DIR, relpath.replace("/", os.sep)))
        except Exception:
            continue
        if not local_path.startswith(safe_root + os.sep):
            continue
        if os.path.isfile(local_path):
            try:
                os.remove(local_path)
            except Exception:
                pass


def resolve_local_image_url_to_path(image_url: str) -> str:
    parsed = urlparse(str(image_url or "").strip())
    path = unquote(parsed.path or "")
    if path.startswith("/generations/"):
        relpath = "/".join([part for part in path.split("/") if part][1:])
        root_dir = GENERATIONS_DIR
    elif path.startswith("/reference-archive/"):
        relpath = "/".join([part for part in path.split("/") if part][1:])
        root_dir = REFERENCE_ARCHIVE_DIR
    elif path.startswith("/reference-render/"):
        relpath = "/".join([part for part in path.split("/") if part][1:])
        root_dir = REFERENCE_RENDERS_DIR
    else:
        return ""
    if not relpath:
        return ""
    try:
        local_path = safe_asset_path(root_dir, relpath)
    except Exception:
        return ""
    return local_path if os.path.exists(local_path) else ""


def upload_image_payload_to_fal(client: fal_client.SyncClient, image_payload: dict) -> str:
    if not isinstance(image_payload, dict):
        raise ValueError("Choose or drop a source image for this model.")

    candidate_urls = [
        str(image_payload.get("url") or "").strip(),
        str(image_payload.get("masked_url") or "").strip(),
        str(image_payload.get("original_url") or "").strip(),
        str(image_payload.get("mask_url") or "").strip(),
    ]
    for direct_url in candidate_urls:
        if direct_url.startswith("http://") or direct_url.startswith("https://"):
            return direct_url
        local_path = resolve_local_image_url_to_path(direct_url)
        if local_path:
            return str(client.upload_file(local_path))

    data_b64 = str(image_payload.get("data") or "").strip()
    mime_type = str(image_payload.get("mime_type") or "image/png").split(";", 1)[0].strip() or "image/png"
    name = os.path.basename(str(image_payload.get("name") or "video-source.png")) or "video-source.png"
    if not data_b64:
        raise ValueError("Choose or drop a source image for this model.")

    ext = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }.get(mime_type, os.path.splitext(name)[1] or ".png")

    temp_path = ""
    try:
        raw_bytes = base64.b64decode(data_b64, validate=False)
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as temp_file:
            temp_file.write(raw_bytes)
            temp_path = temp_file.name
        return str(client.upload_file(temp_path))
    except Exception as exc:
        raise RuntimeError(f"Could not upload the selected source image: {exc}") from exc
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except Exception:
                pass


def build_luma_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def extract_luma_error(response: requests.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        return f"HTTP {response.status_code}: {response.text[:300]}"
    if isinstance(payload, dict):
        for key in ("error", "message", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                nested = str(value.get("message") or value.get("detail") or "").strip()
                if nested:
                    return nested
    return f"HTTP {response.status_code}: {str(payload)[:300]}"


LUMA_VIDEO_COST_PER_MEGAPIXEL = {
    "ray-2": 0.0064,
    "ray-flash-2": 0.0022,
}
LUMA_RESOLUTION_BASE_DIMENSIONS = {
    "540p": (960, 540),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "4k": (3840, 2160),
}
LUMA_ASPECT_RATIO_DIMENSIONS = {
    "16:9": {
        "540p": (960, 540),
        "720p": (1280, 720),
        "1080p": (1920, 1080),
        "4k": (3840, 2160),
    },
    "9:16": {
        "540p": (540, 960),
        "720p": (720, 1280),
        "1080p": (1080, 1920),
        "4k": (2160, 3840),
    },
    "1:1": {
        "540p": (540, 540),
        "720p": (720, 720),
        "1080p": (1080, 1080),
        "4k": (2160, 2160),
    },
    "4:3": {
        "540p": (720, 540),
        "720p": (960, 720),
        "1080p": (1440, 1080),
        "4k": (2880, 2160),
    },
    "3:4": {
        "540p": (540, 720),
        "720p": (720, 960),
        "1080p": (1080, 1440),
        "4k": (2160, 2880),
    },
    "21:9": {
        "540p": (1260, 540),
        "720p": (1680, 720),
        "1080p": (2520, 1080),
        "4k": (5040, 2160),
    },
    "9:21": {
        "540p": (540, 1260),
        "720p": (720, 1680),
        "1080p": (1080, 2520),
        "4k": (2160, 5040),
    },
}


def estimate_luma_video_cost(model_name: str, resolution: str, duration: int, aspect_ratio: str = "16:9") -> float:
    model_key = str(model_name or "ray-2").strip().lower() or "ray-2"
    resolution_key = str(resolution or "720p").strip().lower() or "720p"
    aspect_key = str(aspect_ratio or "16:9").strip() or "16:9"
    width, height = LUMA_ASPECT_RATIO_DIMENSIONS.get(aspect_key, {}).get(
        resolution_key,
        LUMA_RESOLUTION_BASE_DIMENSIONS.get(resolution_key, (1280, 720)),
    )
    pixels_million = (max(1, int(width)) * max(1, int(height)) * 24 * max(1, int(duration))) / 1_000_000
    rate = float(LUMA_VIDEO_COST_PER_MEGAPIXEL.get(model_key, 0.0))
    return round(pixels_million * rate, 4)


def poll_luma_generation(api_key: str, generation_id: str, *, timeout_seconds: int = 1800, poll_interval_seconds: float = 3.0) -> dict:
    headers = build_luma_headers(api_key)
    deadline = time.time() + max(30, int(timeout_seconds))
    last_payload = None
    while time.time() < deadline:
        try:
            response = requests.get(f"{LUMA_GENERATIONS_URL}/{generation_id}", headers=headers, timeout=90)
        except requests.exceptions.Timeout as exc:
            raise TimeoutError("Timeout: Luma status polling took too long.") from exc
        except Exception as exc:
            raise RuntimeError(f"Network error: {exc}") from exc
        if response.status_code != 200:
            raise RuntimeError(extract_luma_error(response))
        last_payload = response.json() if response.content else {}
        state = str((last_payload or {}).get("state") or "").strip().lower()
        if state == "completed":
            return last_payload
        if state == "failed":
            failure_reason = str((last_payload or {}).get("failure_reason") or (last_payload or {}).get("error") or "").strip()
            raise RuntimeError(failure_reason or "Luma generation failed.")
        time.sleep(max(0.5, float(poll_interval_seconds)))
    raise TimeoutError("Timeout: Luma generation did not finish in time.")


def build_default_edit_session(kind: str, asset_relpath: str, asset_url: str = "") -> dict:
    return {
        "version": 4,
        "kind": str(kind or "").strip().lower(),
        "assetRelpath": resolve_asset_relpath(relpath=asset_relpath),
        "assetUrl": str(asset_url or "").strip(),
        "selectionCounter": 0,
        "editModel": "fal-ai/nano-banana-pro",
        "editImageSize": "2K",
        "editAspectRatio": "16:9",
        "editPromptMode": "combined",
        "globalInstruction": "",
        "globalRefImages": [],
        "selections": [],
        "updatedAt": utc_now_iso(),
    }


@app.route("/api/edit-session", methods=["GET"])
@login_required
def api_get_edit_session():
    kind = str(request.args.get("kind") or "").strip()
    relpath = str(request.args.get("relpath") or "").strip()
    asset_url = str(request.args.get("asset_url") or "").strip()
    date_str = str(request.args.get("date") or "").strip()
    filename = os.path.basename(str(request.args.get("filename") or "").strip())

    try:
        _, asset_path, safe_relpath = resolve_asset_image_path(kind, date_str, filename, relpath)
        _, session_path, session_relpath = resolve_edit_session_path(kind, safe_relpath)
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    session = build_default_edit_session(kind, safe_relpath, asset_url)
    if os.path.exists(session_path):
        try:
            with open(session_path, "r", encoding="utf-8") as handle:
                stored = json.load(handle)
            if isinstance(stored, dict):
                session.update(stored)
        except Exception:
            pass
    session["kind"] = str(kind or "").strip().lower()
    session["assetRelpath"] = safe_relpath
    if asset_url:
        session["assetUrl"] = asset_url
    raw_selections = session.get("selections") if isinstance(session.get("selections"), list) else []
    session["selections"] = [
        normalize_edit_session_selection_payload(selection, index, kind=kind)
        for index, selection in enumerate(raw_selections)
    ]
    updated_previews = False
    if os.path.isfile(asset_path):
        refreshed_selections = []
        for selection in session["selections"]:
            previous_preview = str(selection.get("previewRelpath") or "")
            refreshed = ensure_edit_selection_preview(kind, asset_path, selection)
            if str(refreshed.get("previewRelpath") or "") != previous_preview:
                updated_previews = True
            refreshed_selections.append(refreshed)
        session["selections"] = refreshed_selections
    session["globalRefImages"] = normalize_ref_image_payloads(session.get("globalRefImages"), 16)
    session["selectionCounter"] = max(
        int(session.get("selectionCounter") or 0),
        len(session["selections"]),
    )
    session["version"] = max(int(session.get("version") or 1), 4)
    session["sessionRelpath"] = session_relpath
    if updated_previews:
        try:
            os.makedirs(os.path.dirname(session_path), exist_ok=True)
            with open(session_path, "w", encoding="utf-8") as handle:
                json.dump(session, handle, indent=2, ensure_ascii=False)
        except Exception:
            pass
    return jsonify({"ok": True, "session": session})


@app.route("/api/edit-session", methods=["POST"])
@login_required
def api_save_edit_session():
    body = request.get_json(silent=True) or {}
    kind = str(body.get("kind") or "").strip()
    relpath = str(body.get("relpath") or body.get("assetRelpath") or "").strip()
    asset_url = str(body.get("assetUrl") or "").strip()
    date_str = str(body.get("date") or "").strip()
    filename = os.path.basename(str(body.get("filename") or "").strip())
    session = body.get("session") if isinstance(body.get("session"), dict) else {}

    try:
        _, _, safe_relpath = resolve_asset_image_path(kind, date_str, filename, relpath)
        session_root, session_path, session_relpath = resolve_edit_session_path(kind, safe_relpath)
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    existing_mask_relpaths: set[str] = set()
    if os.path.exists(session_path):
        try:
            with open(session_path, "r", encoding="utf-8") as handle:
                previous_payload = json.load(handle)
            if isinstance(previous_payload, dict):
                existing_mask_relpaths = collect_edit_session_mask_relpaths(previous_payload.get("selections"), kind=kind)
        except Exception:
            existing_mask_relpaths = set()

    payload = build_default_edit_session(kind, safe_relpath, asset_url)
    payload.update(session)
    payload["kind"] = str(kind or "").strip().lower()
    payload["assetRelpath"] = safe_relpath
    if asset_url:
        payload["assetUrl"] = asset_url
    payload["selectionCounter"] = max(0, int(payload.get("selectionCounter") or 0))
    raw_selections = payload.get("selections") if isinstance(payload.get("selections"), list) else []
    payload["selections"] = [
        normalize_edit_session_selection_payload(selection, index, kind=kind)
        for index, selection in enumerate(raw_selections)
    ]
    payload["globalRefImages"] = normalize_ref_image_payloads(payload.get("globalRefImages"), 16)
    payload["updatedAt"] = utc_now_iso()
    payload["version"] = max(int(payload.get("version") or 1), 4)

    os.makedirs(os.path.dirname(session_path), exist_ok=True)
    with open(session_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)

    current_mask_relpaths = collect_edit_session_mask_relpaths(payload.get("selections"), kind=kind)
    orphaned_mask_relpaths = existing_mask_relpaths - current_mask_relpaths
    if orphaned_mask_relpaths:
        delete_edit_session_mask_relpaths(orphaned_mask_relpaths)

    return jsonify({
        "ok": True,
        "session": payload,
        "session_relpath": session_relpath,
        "session_url": build_asset_public_url("edit-sessions", session_relpath),
    })


@app.route("/api/edit-session/sam3-image", methods=["POST"])
@login_required
def api_run_edit_session_sam3_image():
    body = request.get_json(silent=True) or {}
    kind = str(body.get("kind") or "").strip()
    relpath = str(body.get("relpath") or body.get("assetRelpath") or "").strip()
    asset_url = str(body.get("assetUrl") or "").strip()
    date_str = str(body.get("date") or "").strip()
    filename = os.path.basename(str(body.get("filename") or "").strip())
    prompt = str(body.get("prompt") or "").strip()
    point_prompts = body.get("pointPrompts") if isinstance(body.get("pointPrompts"), list) else []
    box_prompts = body.get("boxPrompts") if isinstance(body.get("boxPrompts"), list) else []
    return_multiple_masks = bool(body.get("returnMultipleMasks", True))
    try:
        max_masks = max(1, min(int(body.get("maxMasks") or 3), 8))
    except Exception:
        max_masks = 3

    config = load_config()
    fal_api_key = str(config.get("fal_api_key", "") or "").strip()
    if not fal_api_key:
        return jsonify({"ok": False, "error": "Fal API key not configured."}), 400

    try:
        _, img_path, safe_relpath = resolve_asset_image_path(kind, date_str, filename, relpath)
        _, session_path, session_relpath = resolve_edit_session_path(kind, safe_relpath)
        _, segment_dir, segment_rel_dir = resolve_edit_session_segments_dir(kind, safe_relpath)
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    normalized_points: list[dict] = []
    for item in point_prompts:
        if not isinstance(item, dict):
            continue
        try:
            x = int(round(float(item.get("x"))))
            y = int(round(float(item.get("y"))))
            label = 1 if int(item.get("label", 1)) == 1 else 0
        except Exception:
            continue
        point_payload = {"x": x, "y": y, "label": label}
        object_id = item.get("object_id")
        if isinstance(object_id, int):
            point_payload["object_id"] = object_id
        normalized_points.append(point_payload)

    normalized_boxes: list[dict] = []
    for item in box_prompts:
        if not isinstance(item, dict):
            continue
        try:
            x_min = int(round(float(item.get("x_min"))))
            y_min = int(round(float(item.get("y_min"))))
            x_max = int(round(float(item.get("x_max"))))
            y_max = int(round(float(item.get("y_max"))))
        except Exception:
            continue
        if x_max <= x_min or y_max <= y_min:
            continue
        box_payload = {
            "x_min": x_min,
            "y_min": y_min,
            "x_max": x_max,
            "y_max": y_max,
        }
        object_id = item.get("object_id")
        if isinstance(object_id, int):
            box_payload["object_id"] = object_id
        normalized_boxes.append(box_payload)

    if not prompt and not normalized_points and not normalized_boxes:
        return jsonify({"ok": False, "error": "Add a text prompt, point prompt, or box prompt first."}), 400

    try:
        client = fal_client.SyncClient(key=fal_api_key, default_timeout=900.0)
        uploaded_image_url = str(client.upload_file(img_path))
        arguments = {
            "image_url": uploaded_image_url,
            "apply_mask": True,
            "sync_mode": False,
            "output_format": "png",
            "return_multiple_masks": return_multiple_masks,
            "max_masks": max_masks,
            "include_scores": True,
            "include_boxes": True,
        }
        if prompt:
            arguments["prompt"] = prompt
        if normalized_points:
            arguments["point_prompts"] = normalized_points
        if normalized_boxes:
            arguments["box_prompts"] = normalized_boxes
        result = client.run(FAL_SAM3_IMAGE_ID, arguments=arguments)
    except Exception as exc:
        return jsonify({"ok": False, "error": f"SAM 3 request failed: {exc}"}), 500

    masks = result.get("masks") if isinstance(result, dict) and isinstance(result.get("masks"), list) else []
    if not masks:
        return jsonify({"ok": False, "error": "SAM 3 returned no masks for this request."}), 500

    metadata_entries = result.get("metadata") if isinstance(result, dict) and isinstance(result.get("metadata"), list) else []
    score_entries = result.get("scores") if isinstance(result, dict) and isinstance(result.get("scores"), list) else []
    box_entries = result.get("boxes") if isinstance(result, dict) and isinstance(result.get("boxes"), list) else []

    session_payload = build_default_edit_session(kind, safe_relpath, asset_url)
    if os.path.exists(session_path):
        try:
            with open(session_path, "r", encoding="utf-8") as handle:
                previous_payload = json.load(handle)
            if isinstance(previous_payload, dict):
                session_payload.update(previous_payload)
        except Exception:
            pass
    session_payload["kind"] = str(kind or "").strip().lower()
    session_payload["assetRelpath"] = safe_relpath
    if asset_url:
        session_payload["assetUrl"] = asset_url
    session_payload["version"] = max(int(session_payload.get("version") or 1), 4)
    session_payload["selectionCounter"] = max(
        int(session_payload.get("selectionCounter") or 0),
        len(session_payload.get("selections") or []),
    )
    session_payload["selections"] = [
        normalize_edit_session_selection_payload(selection, index, kind=kind)
        for index, selection in enumerate(session_payload.get("selections") or [])
    ]

    os.makedirs(segment_dir, exist_ok=True)
    new_selections: list[dict] = []
    existing_count = len(session_payload["selections"])

    for index, mask_item in enumerate(masks):
        if not isinstance(mask_item, dict):
            continue
        mask_url = str(mask_item.get("url") or "").strip()
        if not mask_url:
            continue
        try:
            raw_bytes, detected_mime = download_remote_binary(mask_url, timeout=240)
            raw_b64 = base64.b64encode(raw_bytes).decode("utf-8")
            mask_png_b64, _ = convert_image_b64_to_png(raw_b64, detected_mime or str(mask_item.get("content_type") or "image/png"))
            mask_png_bytes = base64.b64decode(mask_png_b64)
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Could not download mask {index + 1}: {exc}"}), 500

        session_payload["selectionCounter"] += 1
        selection_number = existing_count + len(new_selections) + 1
        selection_id = f"selection-{session_payload['selectionCounter']}"
        mask_filename = f"{selection_id}.png"
        mask_abs_path = os.path.join(segment_dir, mask_filename)
        with open(mask_abs_path, "wb") as handle:
            handle.write(mask_png_bytes)

        mask_relpath = f"{segment_rel_dir}/{mask_filename}".replace("\\", "/")
        meta_entry = metadata_entries[index] if index < len(metadata_entries) and isinstance(metadata_entries[index], dict) else {}
        score = meta_entry.get("score")
        if score is None and index < len(score_entries):
            score = score_entries[index]
        box = meta_entry.get("box")
        if not isinstance(box, list) and index < len(box_entries):
            box = box_entries[index]
        selection = normalize_edit_session_selection_payload({
            "id": selection_id,
            "name": f"Segment {selection_number}",
            "prompt": "",
            "describedPrompt": "",
            "notes": "",
            "visible": True,
            "geometry": {
                "source": "sam3",
                "mode": "image",
                "prompt": prompt,
                "pointPrompts": normalized_points,
                "boxPrompts": normalized_boxes,
            },
            "sourcePrompt": prompt,
            "score": score,
            "box": box if isinstance(box, list) else None,
            "maskRelpath": mask_relpath,
            "maskUrl": build_edit_session_mask_url(mask_relpath),
        }, existing_count + len(new_selections), kind=kind)
        selection = ensure_edit_selection_preview(kind, img_path, selection)
        session_payload["selections"].append(selection)
        new_selections.append(selection)

    if not new_selections:
        return jsonify({"ok": False, "error": "SAM 3 returned no usable mask images."}), 500

    session_payload["updatedAt"] = utc_now_iso()
    os.makedirs(os.path.dirname(session_path), exist_ok=True)
    with open(session_path, "w", encoding="utf-8") as handle:
        json.dump(session_payload, handle, indent=2, ensure_ascii=False)

    return jsonify({
        "ok": True,
        "session": session_payload,
        "session_relpath": session_relpath,
        "session_url": build_asset_public_url("edit-sessions", session_relpath),
        "newSelections": new_selections,
        "maskCount": len(new_selections),
    })


@app.route("/api/edit-session/sam3-video", methods=["POST"])
@login_required
def api_run_edit_session_sam3_video():
    body = request.get_json(silent=True) or {}
    kind = str(body.get("kind") or "").strip()
    relpath = str(body.get("relpath") or body.get("assetRelpath") or "").strip()
    asset_url = str(body.get("assetUrl") or "").strip()
    date_str = str(body.get("date") or "").strip()
    filename = os.path.basename(str(body.get("filename") or "").strip())
    prompt = str(body.get("prompt") or "").strip()

    if not prompt:
        return jsonify({"ok": False, "error": "Add a text prompt before running SAM 3 video."}), 400

    config = load_config()
    fal_api_key = str(config.get("fal_api_key", "") or "").strip()
    if not fal_api_key:
        return jsonify({"ok": False, "error": "Fal API key not configured."}), 400

    try:
        _, video_path, safe_relpath = resolve_asset_image_path(kind, date_str, filename, relpath)
        _, session_path, session_relpath = resolve_edit_session_path(kind, safe_relpath)
        _, segment_dir, segment_rel_dir = resolve_edit_session_segments_dir(kind, safe_relpath)
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    try:
        client = fal_client.SyncClient(key=fal_api_key, default_timeout=1800.0)
        uploaded_video_url = str(client.upload_file(video_path))
        result = client.run(FAL_SAM3_VIDEO_ID, arguments={
            "video_url": uploaded_video_url,
            "prompt": prompt,
            "sync_mode": False,
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": f"SAM 3 video request failed: {exc}"}), 500

    segmented_video = result.get("video") if isinstance(result, dict) and isinstance(result.get("video"), dict) else {}
    segmented_video_url = str(segmented_video.get("url") or "").strip()
    bbox_zip = result.get("bounding_box_frames_zip") if isinstance(result, dict) and isinstance(result.get("bounding_box_frames_zip"), dict) else {}
    bbox_zip_url = str(bbox_zip.get("url") or "").strip()

    if not segmented_video_url:
        return jsonify({"ok": False, "error": "SAM 3 video returned no tracked video output."}), 500

    session_payload = build_default_edit_session(kind, safe_relpath, asset_url)
    if os.path.exists(session_path):
        try:
            with open(session_path, "r", encoding="utf-8") as handle:
                previous_payload = json.load(handle)
            if isinstance(previous_payload, dict):
                session_payload.update(previous_payload)
        except Exception:
            pass
    session_payload["kind"] = str(kind or "").strip().lower()
    session_payload["assetRelpath"] = safe_relpath
    if asset_url:
        session_payload["assetUrl"] = asset_url
    session_payload["version"] = max(int(session_payload.get("version") or 1), 4)
    session_payload["selectionCounter"] = max(
        int(session_payload.get("selectionCounter") or 0),
        len(session_payload.get("selections") or []),
    )
    session_payload["selections"] = [
        normalize_edit_session_selection_payload(selection, index, kind=kind)
        for index, selection in enumerate(session_payload.get("selections") or [])
    ]

    os.makedirs(segment_dir, exist_ok=True)

    try:
        segmented_bytes, segmented_mime = download_remote_binary(segmented_video_url, timeout=600)
        segmented_ext, segmented_mime_type = normalize_video_extension(
            segmented_video.get("mime_type") or segmented_mime,
            segmented_video_url,
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Could not download tracked SAM 3 video: {exc}"}), 500

    bbox_bytes = b""
    if bbox_zip_url:
        try:
            bbox_bytes, _bbox_mime = download_remote_binary(bbox_zip_url, timeout=600)
        except Exception:
            bbox_bytes = b""

    session_payload["selectionCounter"] += 1
    selection_id = f"selection-{session_payload['selectionCounter']}"
    selection_number = len(session_payload["selections"]) + 1
    track_filename = f"{selection_id}{segmented_ext}"
    track_abs_path = os.path.join(segment_dir, track_filename)
    with open(track_abs_path, "wb") as handle:
        handle.write(segmented_bytes)
    track_relpath = f"{segment_rel_dir}/{track_filename}".replace("\\", "/")

    bbox_relpath = ""
    bbox_url = ""
    if bbox_bytes:
        bbox_filename = f"{selection_id}_boxes.zip"
        bbox_abs_path = os.path.join(segment_dir, bbox_filename)
        with open(bbox_abs_path, "wb") as handle:
            handle.write(bbox_bytes)
        bbox_relpath = f"{segment_rel_dir}/{bbox_filename}".replace("\\", "/")
        bbox_url = build_edit_session_asset_url(bbox_relpath)

    selection = normalize_edit_session_selection_payload({
        "id": selection_id,
        "name": f"Track {selection_number}",
        "prompt": "",
        "describedPrompt": "",
        "notes": "",
        "visible": True,
        "geometry": {
            "source": "sam3",
            "mode": "video",
            "prompt": prompt,
        },
        "sourcePrompt": prompt,
        "targetType": "video",
        "trackVideoRelpath": track_relpath,
        "trackVideoUrl": build_edit_session_asset_url(track_relpath),
        "bboxZipRelpath": bbox_relpath,
        "bboxZipUrl": bbox_url,
        "notes": "Tracked SAM 3 video selection",
    }, len(session_payload["selections"]), kind=kind)
    session_payload["selections"].append(selection)
    session_payload["updatedAt"] = utc_now_iso()

    os.makedirs(os.path.dirname(session_path), exist_ok=True)
    with open(session_path, "w", encoding="utf-8") as handle:
        json.dump(session_payload, handle, indent=2, ensure_ascii=False)

    return jsonify({
        "ok": True,
        "session": session_payload,
        "session_relpath": session_relpath,
        "session_url": build_asset_public_url("edit-sessions", session_relpath),
        "newSelections": [selection],
        "trackCount": 1,
        "trackedVideoUrl": selection.get("trackVideoUrl", ""),
        "bboxZipUrl": selection.get("bboxZipUrl", ""),
    })


@app.route("/api/generations/open-folder", methods=["POST"])
@login_required
def api_open_generation_folder():
    body = request.get_json(silent=True) or {}
    date_str = str(body.get("date") or "").strip()
    filename = os.path.basename(str(body.get("filename") or "").strip())
    relpath = str(body.get("relpath") or "").strip()

    try:
        resolve_asset_image_path("history", date_str, filename, relpath)
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    try:
        _, img_path, _ = resolve_asset_image_path("history", date_str, filename, relpath)
        open_file_in_folder(img_path)
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/assets/open-folder", methods=["POST"])
@login_required
def api_open_asset_folder():
    body = request.get_json(silent=True) or {}
    kind = str(body.get("kind") or "").strip()
    date_str = str(body.get("date") or "").strip()
    filename = os.path.basename(str(body.get("filename") or "").strip())
    relpath = str(body.get("relpath") or "").strip()

    try:
        _, img_path, _ = resolve_asset_image_path(kind, date_str, filename, relpath)
        open_file_in_folder(img_path)
        return jsonify({"ok": True})
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ---------------------------------------------------------------------------
# API Ã¢â‚¬â€ Delete a loved image (image + JSON sidecar, never touches generations/)
# ---------------------------------------------------------------------------
@app.route("/api/loved/<path:asset_relpath>", methods=["DELETE"])
@login_required
def api_delete_loved(asset_relpath):
    try:
        _, img_path, safe_relpath = resolve_asset_image_path("loved", relpath=asset_relpath)
        filename = os.path.basename(img_path)
        if os.path.splitext(filename)[1].lower() not in (".jpeg", ".jpg", ".png", ".webp"):
            return jsonify({"ok": False, "error": "Invalid file type"}), 400
        if not os.path.exists(img_path):
            return jsonify({"ok": False, "error": "File not found"}), 404
        os.remove(img_path)
        json_path = os.path.splitext(img_path)[0] + ".json"
        if os.path.exists(json_path):
            os.remove(json_path)
        current_dir = os.path.dirname(img_path)
        safe_root = os.path.realpath(LOVED_DIR)
        while current_dir.startswith(safe_root + os.sep) and current_dir != safe_root:
            if os.path.isdir(current_dir) and not os.listdir(current_dir):
                os.rmdir(current_dir)
                current_dir = os.path.dirname(current_dir)
                continue
            break
        return jsonify({"ok": True, "relpath": safe_relpath})
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "File not found"}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/reference-archive/<date_str>/<filename>", methods=["DELETE"])
@login_required
def api_delete_reference_archive(date_str, filename):
    safe_root = os.path.realpath(REFERENCE_ARCHIVE_DIR)
    filename = os.path.basename(filename)
    img_path = os.path.realpath(os.path.join(REFERENCE_ARCHIVE_DIR, date_str, filename))

    if not img_path.startswith(safe_root + os.sep):
        return jsonify({"ok": False, "error": "Invalid path"}), 400
    if os.path.splitext(filename)[1].lower() not in (".jpeg", ".jpg", ".png", ".webp"):
        return jsonify({"ok": False, "error": "Invalid file type"}), 400
    if not os.path.exists(img_path):
        return jsonify({"ok": False, "error": "File not found"}), 404

    try:
        os.remove(img_path)
        delete_reference_mask_assets(date_str, filename)
        remove_reference_archive_index_entries(date_str, filename)
        archive_day_dir = os.path.dirname(img_path)
        if os.path.isdir(archive_day_dir) and not os.listdir(archive_day_dir):
            os.rmdir(archive_day_dir)

        if os.path.isdir(GENERATIONS_DIR):
            for gen_date in os.listdir(GENERATIONS_DIR):
                day_path = os.path.join(GENERATIONS_DIR, gen_date)
                if not os.path.isdir(day_path):
                    continue
                meta_files = glob.glob(os.path.join(day_path, "*.json"))
                for meta_file in meta_files:
                    try:
                        with open(meta_file, encoding="utf-8") as fh:
                            meta = json.load(fh)
                    except Exception:
                        continue
                    refs = meta.get("refArchive") or []
                    if not isinstance(refs, list) or not refs:
                        continue
                    new_refs = []
                    changed = False
                    for ref in refs:
                        if not isinstance(ref, dict):
                            continue
                        ref_date = str(ref.get("date", "") or "").strip()
                        ref_name = os.path.basename(str(ref.get("filename", "") or "").strip())
                        if ref_date == date_str and ref_name == filename:
                            changed = True
                            continue
                        new_refs.append(ref)
                    if changed:
                        meta["refArchive"] = new_refs
                        meta["ref_count"] = len(new_refs)
                        with open(meta_file, "w", encoding="utf-8") as fh:
                            json.dump(meta, fh, indent=2, ensure_ascii=False)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/reference-mask/<date_str>/<filename>", methods=["GET", "POST"])
@login_required
def api_reference_mask(date_str, filename):
    safe_date = str(date_str or "").strip()
    safe_filename = os.path.basename(str(filename or "").strip())
    paths = get_reference_mask_file_paths(safe_date, safe_filename)
    safe_root = os.path.realpath(REFERENCE_ARCHIVE_DIR)
    original_path = os.path.realpath(paths["original_path"])

    if not original_path.startswith(safe_root + os.sep):
        return jsonify({"ok": False, "error": "Invalid path"}), 400
    if not os.path.exists(original_path):
        return jsonify({"ok": False, "error": "Reference image not found"}), 404

    if request.method == "GET":
        return jsonify({
            "ok": True,
            "bundle": build_reference_mask_bundle(safe_date, safe_filename),
            "meta": load_reference_mask_metadata(safe_date, safe_filename),
        })

    body = request.get_json(silent=True) or {}
    mask_png_data = str(body.get("mask_data", "") or "").strip()
    clear_mask = bool(body.get("clear")) or not mask_png_data
    try:
        if clear_mask:
            delete_reference_mask_assets(safe_date, safe_filename)
            metadata = {}
        else:
            metadata = save_reference_mask_assets(safe_date, safe_filename, mask_png_data)
        return jsonify({
            "ok": True,
            "bundle": build_reference_mask_bundle(safe_date, safe_filename),
            "meta": metadata,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# API Ã¢â‚¬â€ Loved list (for reference image picker)
# ---------------------------------------------------------------------------
@app.route("/api/loved-list")
@login_required
def api_loved_list():
    """
    Returns list of loved images for the picker (no base64, only URL and meta).
    """
    result = []
    for item in collect_asset_records("loved"):
        params = item.get("params", {})
        result.append({
            "url": item.get("url", ""),
            "date": item.get("date", ""),
            "filename": item.get("filename", ""),
            "prompt": (params.get("prompt", "") or "")[:60],
            "model_label": params.get("model_label", ""),
            "imageSize": params.get("imageSize", ""),
            "aspectRatio": params.get("aspectRatio", ""),
        })
    return jsonify(result)


def build_common_asset_params(meta: dict | None, *, default_model: str = "", default_provider: str = "") -> dict:
    meta = meta or {}
    model_id = str(meta.get("model", default_model) or default_model or "")
    model_info = MODELS_INFO.get(model_id, {})
    provider = str(meta.get("provider", default_provider or model_info.get("provider", "")) or default_provider or model_info.get("provider", ""))
    provider_label = str(meta.get("provider_label", model_info.get("provider_label", PROVIDER_LABELS.get(provider, provider.title() if provider else ""))) or "")
    asset_meta = normalize_asset_metadata(meta, require_filename=False)
    asset_relpath = str(meta.get("assetRelpath", "") or "").strip()
    upscale_source_filename = str(meta.get("upscaleSourceFilename", "") or "").strip()
    upscale_source_date = str(meta.get("upscaleSourceDate", "") or "").strip()
    upscale_source_relpath = str(meta.get("upscaleSourceRelpath", "") or "").strip()
    if not upscale_source_relpath and upscale_source_filename:
        preferred_dir = os.path.dirname(asset_relpath).replace("\\", "/").strip("/")
        upscale_source_relpath = find_generation_relpath_by_filename(
            upscale_source_filename,
            preferred_dir=preferred_dir,
            preferred_date=upscale_source_date,
        )
    return {
        "model": model_id,
        "modelFamily": meta.get("modelFamily", model_info.get("family", model_id)),
        "model_label": meta.get("model_label", model_info.get("label", "")),
        "provider": provider,
        "provider_label": provider_label,
        "imageSize": meta.get("imageSize", ""),
        "deliveredImageSize": meta.get("deliveredImageSize", ""),
        "deliveredWidth": meta.get("deliveredWidth", 0),
        "deliveredHeight": meta.get("deliveredHeight", 0),
        "aspectRatio": meta.get("aspectRatio", ""),
        "prompt": meta.get("prompt", ""),
        "temperature": meta.get("temperature", 1.0),
        "topP": meta.get("topP", 0.95),
        "thinkingLevel": meta.get("thinkingLevel", "Minimal"),
        "useSearch": meta.get("useSearch", False),
        "ref_count": meta.get("ref_count", 0),
        "refArchive": enrich_reference_archive_entries(meta.get("refArchive", [])),
        "seedMode": meta.get("seedMode", "random"),
        "seedValue": meta.get("seedValue", 1),
        "falSafetyChecker": meta.get("falSafetyChecker", True),
        "falSafetyTolerance": meta.get("falSafetyTolerance", 4),
        "geminiSafetyPreset": meta.get("geminiSafetyPreset", "default"),
        "byteplusSafetyMode": meta.get("byteplusSafetyMode", "platform_default"),
        "upscaled": meta.get("upscaled", False),
        "upscalerType": meta.get("upscalerType", ""),
        "upscalerLabel": meta.get("upscalerLabel", ""),
        "upscaleModel": meta.get("upscaleModel", meta.get("upscalerType", "")),
        "upscalePreset": meta.get("upscalePreset", ""),
        "upscaleMode": meta.get("upscaleMode", ""),
        "upscaleFactor": meta.get("upscaleFactor"),
        "upscaleTargetResolution": meta.get("upscaleTargetResolution", ""),
        "upscaleTargetWidth": meta.get("upscaleTargetWidth", 0),
        "upscaleTargetHeight": meta.get("upscaleTargetHeight", 0),
        "upscaleTargetAnchor": meta.get("upscaleTargetAnchor", ""),
        "upscaleDisplaySize": meta.get("upscaleDisplaySize", ""),
        "upscaleSourceDate": meta.get("upscaleSourceDate", ""),
        "upscaleSourceFilename": upscale_source_filename,
        "upscaleSourceRelpath": upscale_source_relpath,
        "upscaleSourceUrl": build_asset_public_url("generations", upscale_source_relpath) if upscale_source_relpath else "",
        "upscaleOutputWidth": meta.get("upscaleOutputWidth", 0),
        "upscaleOutputHeight": meta.get("upscaleOutputHeight", 0),
        "mime_type": meta.get("mime_type", "image/png"),
        "assetClient": asset_meta["assetClient"],
        "assetProject": asset_meta["assetProject"],
        "assetShot": asset_meta["assetShot"],
        "assetFilename": asset_meta["assetFilename"],
        "assetRelpath": asset_relpath,
    }


def find_existing_image_for_meta_base(base_path: str) -> tuple[str | None, str | None]:
    for ext in (".jpeg", ".jpg", ".png", ".webp"):
        candidate = base_path + ext
        if os.path.exists(candidate):
            return candidate, os.path.basename(candidate)
    return None, None


def iso_sort_key(value: str, fallback: str) -> str:
    raw = str(value or "").strip()
    return raw or fallback


def collect_generation_records(max_load: int | None = None) -> list[dict]:
    result = []
    if not os.path.isdir(GENERATIONS_DIR):
        return result

    for mf in list_meta_files_recursive(GENERATIONS_DIR):
        if max_load is not None and len(result) >= max_load:
            break
        try:
            with open(mf, encoding="utf-8") as f:
                meta = json.load(f)
            img_path, filename = find_existing_binary_for_meta(mf, (".jpeg", ".jpg", ".png", ".webp"))
            if not img_path or not filename:
                continue
            relpath = os.path.relpath(img_path, GENERATIONS_DIR).replace("\\", "/")
            generated_at = str(meta.get("generated_at", "") or "")
            date_key = derive_asset_date_key(relpath, generated_at)
            params = build_common_asset_params(meta)
            if not params.get("deliveredImageSize"):
                delivered_width, delivered_height = measure_image_file_dimensions(img_path)
                if delivered_width and delivered_height:
                    params["deliveredWidth"] = delivered_width
                    params["deliveredHeight"] = delivered_height
                    params["deliveredImageSize"] = approximate_image_size_label(delivered_width, delivered_height)
            params["assetRelpath"] = relpath
            params["assetUrl"] = build_asset_public_url("generations", relpath)
            params["gen_date"] = date_key
            params["gen_filename"] = filename
            params["gen_relpath"] = relpath
            result.append({
                "id": f"history:{relpath}".replace("/", ":"),
                "kind": "history",
                "url": build_asset_public_url("generations", relpath),
                "download_url": build_asset_public_url("generations", relpath),
                "date": date_key,
                "filename": filename,
                "relpath": relpath,
                "generated_at": generated_at,
                "sortTimestamp": iso_sort_key(generated_at, f"{date_key}T00:00:00"),
                "prompt_preview": (params.get("prompt", "") or "")[:120],
                "text": meta.get("text", ""),
                "params": params,
                "delete_url": f"/api/generations/{relpath}",
                "folder_open_payload": {"kind": "history", "date": date_key, "filename": filename, "relpath": relpath},
            })
        except Exception:
            pass
    return result


def collect_loved_records() -> list[dict]:
    result = []
    if not os.path.isdir(LOVED_DIR):
        return result

    for mf in list_meta_files_recursive(LOVED_DIR):
        try:
            with open(mf, encoding="utf-8") as f:
                meta = json.load(f)
            img_path, filename = find_existing_binary_for_meta(mf, (".jpeg", ".jpg", ".png", ".webp"))
            if not img_path or not filename:
                continue
            relpath = os.path.relpath(img_path, LOVED_DIR).replace("\\", "/")
            published_at = str(meta.get("published_at", meta.get("generated_at", "")) or "")
            date_key = derive_asset_date_key(relpath, published_at)
            params = build_common_asset_params(meta)
            if not params.get("deliveredImageSize"):
                delivered_width, delivered_height = measure_image_file_dimensions(img_path)
                if delivered_width and delivered_height:
                    params["deliveredWidth"] = delivered_width
                    params["deliveredHeight"] = delivered_height
                    params["deliveredImageSize"] = approximate_image_size_label(delivered_width, delivered_height)
            params["assetRelpath"] = relpath
            params["assetUrl"] = build_asset_public_url("loved", relpath)
            result.append({
                "id": f"loved:{relpath}".replace("/", ":"),
                "kind": "loved",
                "url": build_asset_public_url("loved", relpath),
                "download_url": build_asset_public_url("loved", relpath),
                "date": date_key,
                "filename": filename,
                "relpath": relpath,
                "generated_at": published_at,
                "sortTimestamp": iso_sort_key(published_at, f"{date_key}T00:00:00"),
                "prompt_preview": (params.get("prompt", "") or "")[:120],
                "text": meta.get("text", ""),
                "params": params,
                "delete_url": f"/api/loved/{relpath}",
                "folder_open_payload": {"kind": "loved", "date": date_key, "filename": filename, "relpath": relpath},
            })
        except Exception:
            pass
    return result


def collect_reference_archive_records() -> list[dict]:
    result = []
    seen_refs = set()
    if not os.path.isdir(GENERATIONS_DIR):
        return result

    for mf in list_meta_files_recursive(GENERATIONS_DIR):
        try:
            with open(mf, encoding="utf-8") as f:
                meta = json.load(f)
            params = build_common_asset_params(meta)
            generated_at = str(meta.get("generated_at", "") or "")
            source_relpath = str(meta.get("assetRelpath") or "").strip()
            for ref_entry in meta.get("refArchive", []) or []:
                if not isinstance(ref_entry, dict):
                    continue
                ref_date = str(ref_entry.get("date", "") or "").strip()
                filename = os.path.basename(str(ref_entry.get("filename", "") or "").strip())
                if not ref_date or not filename:
                    continue
                ref_key = compute_reference_archive_file_hash(ref_date, filename) or f"{ref_date}/{filename}"
                if ref_key in seen_refs:
                    continue
                ref_path = os.path.join(REFERENCE_ARCHIVE_DIR, ref_date, filename)
                if not os.path.exists(ref_path):
                    continue
                seen_refs.add(ref_key)
                enriched_ref = enrich_reference_archive_entry(ref_entry)
                result.append({
                    "id": f"reference:{ref_date}:{filename}",
                    "kind": "references",
                    "url": str(enriched_ref.get("url") or f"/reference-archive/{ref_date}/{filename}"),
                    "download_url": str(enriched_ref.get("url") or f"/reference-archive/{ref_date}/{filename}"),
                    "date": ref_date,
                    "filename": filename,
                    "generated_at": generated_at,
                    "sortTimestamp": iso_sort_key(generated_at, f"{ref_date}T00:00:00"),
                    "prompt_preview": str(enriched_ref.get("name") or params.get("prompt", "") or filename)[:120],
                    "text": "",
                    "params": params,
                    "reference_name": str(enriched_ref.get("name", "") or ""),
                    "source_generation_date": derive_asset_date_key(source_relpath, generated_at),
                    "source_generation_filename": str(meta.get("filename", "") or ""),
                    "delete_url": f"/api/reference-archive/{ref_date}/{filename}",
                    "folder_open_payload": {"kind": "references", "date": ref_date, "filename": filename},
                    "original_url": str(enriched_ref.get("original_url") or ""),
                    "masked_url": str(enriched_ref.get("masked_url") or ""),
                    "mask_url": str(enriched_ref.get("mask_url") or ""),
                    "has_mask": bool(enriched_ref.get("has_mask")),
                    "mask_edit_payload": enriched_ref.get("mask_edit_payload") or {"kind": "references", "date": ref_date, "filename": filename},
                })
        except Exception:
            pass
    return result


def collect_asset_records(kind: str) -> list[dict]:
    if kind == "history":
        return collect_generation_records(max_load=None)
    if kind == "loved":
        return collect_loved_records()
    if kind == "references":
        return collect_reference_archive_records()
    if kind == "videos":
        return collect_video_asset_records(max_load=None)
    raise ValueError("Invalid asset kind")


def collect_asset_metadata_records() -> list[dict]:
    records: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()

    def append_record(client_value: str, project_value: str, shot_value: str, filename_value: str) -> None:
        client = normalize_asset_scope_text(client_value) or ASSET_UNCATEGORIZED_VALUE
        project = normalize_asset_scope_text(project_value) or ASSET_UNCATEGORIZED_VALUE
        shot = normalize_asset_scope_text(shot_value) or ASSET_UNCATEGORIZED_VALUE
        filename = sanitize_asset_filename_stem(filename_value, fallback="")
        if not filename:
            return
        key = (client, project, shot, filename)
        if key in seen:
            return
        seen.add(key)
        records.append({
            "assetClient": client,
            "assetProject": project,
            "assetShot": shot,
            "assetFilename": filename,
        })

    for root_dir in (GENERATIONS_DIR, VIDEOS_DIR):
        if not os.path.isdir(root_dir):
            continue
        for current_root, dirnames, _ in os.walk(root_dir):
            rel_dir = os.path.relpath(current_root, root_dir).replace("\\", "/")
            rel_parts = [part for part in rel_dir.split("/") if part and part != "."]
            entries = os.listdir(current_root)
            if len(rel_parts) >= 4 and not any(os.path.isfile(os.path.join(current_root, name)) for name in entries):
                append_record(rel_parts[0], rel_parts[1], rel_parts[2], os.path.splitext(rel_parts[3])[0])
            # a 4-deep directory holding files is client/project/scene/shot (Task 16): its files register it below
            for file_name in entries:
                file_path = os.path.join(current_root, file_name)
                if not os.path.isfile(file_path):
                    continue
                if file_name.lower().endswith(".json"):
                    continue
                if len(rel_parts) >= 4:
                    append_record(rel_parts[0], rel_parts[1], rel_parts[3], os.path.splitext(file_name)[0])   # scene between project and shot (Task 16)
                elif len(rel_parts) >= 3:
                    append_record(rel_parts[0], rel_parts[1], rel_parts[2], os.path.splitext(file_name)[0])

    for item in (
        collect_generation_records(max_load=None)
        + collect_video_asset_records(max_load=None)
        + collect_loved_records()
        + collect_reference_archive_records()
    ):
        params = item.get("params", {}) or {}
        append_record(
            params.get("assetClient", ""),
            params.get("assetProject", ""),
            params.get("assetShot", ""),
            params.get("assetFilename", ""),
        )
    return records


def collect_asset_metadata_options() -> dict:
    options = {
        "clients": [ASSET_UNCATEGORIZED_VALUE],
        "projects": [ASSET_UNCATEGORIZED_VALUE],
        "shots": [ASSET_UNCATEGORIZED_VALUE],
        "filenames": [],
    }
    seen = {
        "clients": {ASSET_UNCATEGORIZED_VALUE},
        "projects": {ASSET_UNCATEGORIZED_VALUE},
        "shots": {ASSET_UNCATEGORIZED_VALUE},
        "filenames": set(),
    }
    for record in collect_asset_metadata_records():
        client = normalize_asset_scope_text(record.get("assetClient", "")) or ASSET_UNCATEGORIZED_VALUE
        project = normalize_asset_scope_text(record.get("assetProject", "")) or ASSET_UNCATEGORIZED_VALUE
        shot = normalize_asset_scope_text(record.get("assetShot", "")) or ASSET_UNCATEGORIZED_VALUE
        filename = sanitize_asset_filename_stem(record.get("assetFilename", ""), fallback="")
        if client not in seen["clients"]:
            seen["clients"].add(client)
            options["clients"].append(client)
        if project not in seen["projects"]:
            seen["projects"].add(project)
            options["projects"].append(project)
        if shot not in seen["shots"]:
            seen["shots"].add(shot)
            options["shots"].append(shot)
        if filename and filename not in seen["filenames"]:
            seen["filenames"].add(filename)
            options["filenames"].append(filename)

    config = load_config()
    memory = ensure_asset_metadata_memory_shape(config.get("asset_metadata_memory"))
    for key in ("clients", "projects", "shots"):
        for value in memory.get(key, []):
            clean_value = normalize_asset_scope_text(value) or ASSET_UNCATEGORIZED_VALUE
            if clean_value not in seen[key]:
                seen[key].add(clean_value)
                options[key].append(clean_value)
    for value in memory.get("filenames", []):
        clean_value = sanitize_asset_filename_stem(value, fallback="")
        if clean_value and clean_value not in seen["filenames"]:
            seen["filenames"].add(clean_value)
            options["filenames"].append(clean_value)
    return options


@app.after_request
def no_store_html(response):
    # Pages carry the script cache-busters; a cached page would keep loading an old script.
    if (response.mimetype or "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.context_processor
def inject_asset_metadata_bootstrap():
    try:
        return {
            "asset_meta_bootstrap": {
                "options": collect_asset_metadata_options(),
                "records": collect_asset_metadata_records(),
                "projects": fetch_projects(),
            }
        }
    except Exception:
        return {
            "asset_meta_bootstrap": {
                "options": {
                    "clients": [ASSET_UNCATEGORIZED_VALUE],
                    "projects": [ASSET_UNCATEGORIZED_VALUE],
                    "shots": [ASSET_UNCATEGORIZED_VALUE],
                    "filenames": [],
                },
                "records": [],
                "projects": [],
            }
        }


@app.route("/api/asset-metadata-options")
@login_required
def api_asset_metadata_options():
    return jsonify({
        "ok": True,
        "options": collect_asset_metadata_options(),
        "records": collect_asset_metadata_records(),
    })


@app.route("/api/asset-metadata-memory", methods=["POST"])
@login_required
def api_asset_metadata_memory():
    body = request.get_json(silent=True) or {}
    asset_meta = normalize_asset_metadata(body, require_filename=False)
    config = load_config()
    memory = update_asset_metadata_memory(config, asset_meta)
    save_config(config)
    return jsonify({"ok": True, "options": collect_asset_metadata_options(), "memory": memory})


# ---------------------------------------------------------------------------
# API - Projects (Director Studio)
# ---------------------------------------------------------------------------
@app.route("/api/projects", methods=["GET"])
@login_required
def api_projects_list():
    include_archived = str(request.args.get("archived", "") or "").strip().lower() in ("1", "true", "yes")
    return jsonify({"ok": True, "projects": fetch_projects(include_archived=include_archived)})


@app.route("/api/projects", methods=["POST"])
@login_required
def api_projects_create():
    body = request.get_json(silent=True) or {}
    try:
        project = create_project(body)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "project": project})


@app.route("/api/projects/<int:project_id>", methods=["GET"])
@login_required
def api_projects_get(project_id):
    project = get_project(project_id)
    if not project:
        return jsonify({"ok": False, "error": "Project not found"}), 404
    return jsonify({"ok": True, "project": project})


@app.route("/api/projects/<int:project_id>", methods=["PATCH"])
@login_required
def api_projects_update(project_id):
    body = request.get_json(silent=True) or {}
    try:
        project = update_project(project_id, body)
    except LookupError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "project": project})


# ---------------------------------------------------------------------------
# API - Shots (Director Studio)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# API - Scenes (Director Studio)
# ---------------------------------------------------------------------------
@app.route("/api/scenes", methods=["GET"])
@login_required
def api_scenes_list():
    project = get_project(request.args.get("project_id"))
    if not project:
        return jsonify({"ok": False, "error": "Project not found"}), 404
    return jsonify({"ok": True, "scenes": fetch_scenes(project["id"])})


@app.route("/api/scenes", methods=["POST"])
@login_required
def api_scenes_create():
    body = request.get_json(silent=True) or {}
    try:
        scene = create_scene(body)
    except LookupError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "scene": scene})


@app.route("/api/scenes/reorder", methods=["POST"])
@login_required
def api_scenes_reorder():
    body = request.get_json(silent=True) or {}
    try:
        scenes = reorder_scenes(body.get("project_id"), body.get("ids") or [])
    except LookupError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "scenes": scenes})


@app.route("/api/scenes/<int:scene_id>", methods=["GET"])
@login_required
def api_scenes_get(scene_id):
    scene = get_scene(scene_id)
    if not scene:
        return jsonify({"ok": False, "error": "Scene not found"}), 404
    return jsonify({"ok": True, "scene": scene})


@app.route("/api/scenes/<int:scene_id>", methods=["PATCH"])
@login_required
def api_scenes_update(scene_id):
    body = request.get_json(silent=True) or {}
    try:
        scene = update_scene(scene_id, body)
    except LookupError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "scene": scene})


# ---------------------------------------------------------------------------
# API - Workbench agent (Director Studio). Read-only over the project.
# ---------------------------------------------------------------------------
@app.route("/api/system/disk")
@login_required
def api_system_disk():
    return jsonify({"ok": True, **disk_report()})


@app.route("/api/system/clean-temp", methods=["POST"])
@login_required
def api_system_clean_temp():
    body = request.get_json(silent=True) or {}
    if not body.get("confirm"):
        return jsonify({"ok": False, "error": "Confirmation required"}), 400
    paths = body.get("paths") if isinstance(body.get("paths"), list) else []
    if not paths:
        return jsonify({"ok": False, "error": "Nothing selected to clean"}), 400
    return jsonify({"ok": True, **clean_app_temp_dirs(paths)})


@app.route("/api/agent/status")
@login_required
def api_agent_status():
    config = load_config()
    return jsonify({
        "ok": True,
        "has_key": bool((config.get("openai_api_key") or "").strip()),
        "model": config.get("openai_model") or "gpt-4o-mini",
        "has_instructions": bool((config.get("agent_instructions") or "").strip()),
        "history_warn_tokens": AGENT_HISTORY_WARN_TOKENS,
    })


@app.route("/api/agent/digest")
@login_required
def api_agent_digest():
    project = get_project(request.args.get("project_id"))
    if not project:
        return jsonify({"ok": False, "error": "Project not found"}), 404
    digest, info = build_project_digest(project, request.args.get("scene_id"))
    return jsonify({"ok": True, "digest": digest, "info": info, "framing": AGENT_FRAMING})


@app.route("/api/agent/history")
@login_required
def api_agent_history():
    project = get_project(request.args.get("project_id"))
    if not project:
        return jsonify({"ok": False, "error": "Project not found"}), 404
    messages = fetch_agent_messages(project["id"])
    return jsonify({"ok": True, "messages": messages, "history_tokens": sum(estimate_tokens(m["content"]) for m in messages)})


@app.route("/api/agent/history", methods=["DELETE"])
@login_required
def api_agent_history_clear():
    project = get_project(request.args.get("project_id"))
    if not project:
        return jsonify({"ok": False, "error": "Project not found"}), 404
    return jsonify({"ok": True, "cleared": clear_agent_messages(project["id"])})


@app.route("/api/agent/message", methods=["POST"])
@login_required
def api_agent_message():
    body = request.get_json(silent=True) or {}
    project = get_project(body.get("project_id"))
    if not project:
        return jsonify({"ok": False, "error": "Project not found"}), 404
    text = str(body.get("message") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "Message is empty"}), 400
    config = load_config()
    api_key = (config.get("openai_api_key") or "").strip()
    if not api_key:
        return jsonify({"ok": False, "error": "No OpenAI API key is set. Add one in Settings."}), 400
    model = config.get("openai_model") or "gpt-4o-mini"
    instructions = str(config.get("agent_instructions") or "")

    history = fetch_agent_messages(project["id"])
    digest, info = build_project_digest(project, body.get("scene_id"))
    system_prompt = build_agent_system_prompt(instructions, digest, info["truncations"])
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend({"role": m["role"], "content": m["content"]} for m in history if m["role"] in ("user", "assistant"))
    messages.append({"role": "user", "content": text})

    user_row = append_agent_message(project["id"], "user", text, {"scene_id": info["focus_scene_id"]})
    result = call_openai_chat(api_key, model, messages)
    if not result.get("ok"):
        return jsonify({"ok": False, "error": result.get("error", "OpenAI call failed"), "user_message": user_row,
                        "digest_info": info}), 502
    reply_meta = {
        "model": model,
        "usage": result.get("usage") or {},
        "digest_tokens": info["tokens"],
        "truncations": info["truncations"],
        "system_tokens": estimate_tokens(system_prompt),
    }
    reply_row = append_agent_message(project["id"], "assistant", result["text"], reply_meta)
    history_tokens = sum(estimate_tokens(m["content"]) for m in history) + estimate_tokens(text) + estimate_tokens(result["text"])
    return jsonify({"ok": True, "user_message": user_row, "reply": reply_row, "digest_info": info,
                    "history_tokens": history_tokens, "history_warn_tokens": AGENT_HISTORY_WARN_TOKENS})


@app.route("/api/scenes/<int:scene_id>/cast", methods=["GET"])
@login_required
def api_scene_cast_list(scene_id):
    if not get_scene(scene_id):
        return jsonify({"ok": False, "error": "Scene not found"}), 404
    return jsonify({"ok": True, "cast": fetch_cast(scene_id)})


@app.route("/api/scenes/<int:scene_id>/cast", methods=["POST"])
@login_required
def api_scene_cast_create(scene_id):
    body = request.get_json(silent=True) or {}
    try:
        member, note = create_cast_member(scene_id, body)
    except LookupError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "cast_member": member, "handle_note": note})


@app.route("/api/scenes/<int:scene_id>/cast/reorder", methods=["POST"])
@login_required
def api_scene_cast_reorder(scene_id):
    body = request.get_json(silent=True) or {}
    try:
        cast = reorder_cast(scene_id, body.get("ids") or [])
    except LookupError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "cast": cast})


@app.route("/api/cast/<int:cast_id>", methods=["PATCH"])
@login_required
def api_cast_update(cast_id):
    body = request.get_json(silent=True) or {}
    try:
        member, note = update_cast_member(cast_id, body)
    except LookupError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "cast_member": member, "handle_note": note})


@app.route("/api/cast/<int:cast_id>", methods=["DELETE"])
@login_required
def api_cast_delete(cast_id):
    if not delete_cast_member(cast_id):
        return jsonify({"ok": False, "error": "Cast member not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/scenes/<int:scene_id>", methods=["DELETE"])
@login_required
def api_scenes_delete(scene_id):
    result = delete_scene(scene_id)
    if result is None:
        return jsonify({"ok": False, "error": "Scene not found"}), 404
    return jsonify({"ok": True, **result})


@app.route("/api/shots", methods=["GET"])
@login_required
def api_shots_list():
    project = get_project(request.args.get("project_id"))
    if not project:
        return jsonify({"ok": False, "error": "Project not found"}), 404
    return jsonify({"ok": True, "shots": fetch_shots(project["id"])})


@app.route("/api/shots", methods=["POST"])
@login_required
def api_shots_create():
    body = request.get_json(silent=True) or {}
    try:
        shot = create_shot(body)
    except LookupError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "shot": shot})


@app.route("/api/shots/reorder", methods=["POST"])
@login_required
def api_shots_reorder():
    body = request.get_json(silent=True) or {}
    try:
        shots = reorder_shots(body.get("project_id"), body.get("ids") or [])
    except LookupError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "shots": shots})


@app.route("/api/shots/<int:shot_id>", methods=["GET"])
@login_required
def api_shots_get(shot_id):
    shot = get_shot(shot_id)
    if not shot:
        return jsonify({"ok": False, "error": "Shot not found"}), 404
    return jsonify({"ok": True, "shot": shot})


@app.route("/api/shots/<int:shot_id>", methods=["PATCH"])
@login_required
def api_shots_update(shot_id):
    body = request.get_json(silent=True) or {}
    try:
        shot = update_shot(shot_id, body)
    except LookupError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "shot": shot})


@app.route("/api/shots/<int:shot_id>", methods=["DELETE"])
@login_required
def api_shots_delete(shot_id):
    if not delete_shot(shot_id):
        return jsonify({"ok": False, "error": "Shot not found"}), 404
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# API - Takes + render (Director Studio)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# API - Styles (Director Studio)
# ---------------------------------------------------------------------------
@app.route("/api/styles", methods=["GET"])
@login_required
def api_styles_list():
    include_archived = str(request.args.get("archived", "") or "").strip().lower() in ("1", "true", "yes")
    project_id = request.args.get("project_id")
    return jsonify({"ok": True, "styles": fetch_styles(project_id=project_id or None, include_archived=include_archived)})


@app.route("/api/styles", methods=["POST"])
@login_required
def api_styles_create():
    body = request.get_json(silent=True) or {}
    try:
        style = create_style(body)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "style": style})


@app.route("/api/styles/scan", methods=["POST"])
@login_required
def api_styles_scan():
    """Style scan (Task 11): image in, treatment prose out, image archived as an asset path.
    Body: {data, mime_type} for a dropped file, or {asset_url} for an existing asset."""
    config  = load_config()
    api_key = config.get("api_key", "").strip()
    if not api_key:
        return jsonify({"ok": False, "error": "API key not configured"})
    body = request.get_json(silent=True) or {}
    image_url = str(body.get("asset_url") or "").strip()
    image_b64 = str(body.get("data") or "")
    mime_type = str(body.get("mime_type") or "image/png")
    if image_url and not image_b64:
        try:
            loaded = load_asset_image_payload(image_url, "style-scan.png")
            image_b64, mime_type = loaded["data"], loaded["mime_type"]
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)})
    if not image_b64:
        return jsonify({"ok": False, "error": "Image data missing"})
    if not image_url:
        # A dropped file becomes a reference-archive asset so the style's images array can point at it.
        now = datetime.now()
        entries = build_reference_archive_entries(
            [{"data": image_b64, "mime_type": mime_type, "name": str(body.get("name") or "style-reference.png")}],
            now.strftime("%Y-%m-%d"), now.strftime("%H%M%S%f")[:12],
        )
        if entries:
            image_url = f"/reference-archive/{entries[0]['date']}/{entries[0]['filename']}"
    try:
        image_b64, mime_type, *_ = normalize_image_b64(image_b64, mime_type)
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Image pre-processing error: {exc}"})
    extraction = run_vision_extraction(api_key, image_b64, mime_type, STYLE_SCAN_PROMPT)
    if not extraction.get("ok"):
        return jsonify(extraction)
    text = str((extraction.get("metadata") or {}).get("text") or "").strip()
    return jsonify({"ok": True, "text": text, "image_url": image_url, "raw_text": extraction["raw_text"], "usage": extraction["usage"]})


@app.route("/api/styles/<int:style_id>", methods=["GET"])
@login_required
def api_styles_get(style_id):
    style = get_style(style_id)
    if not style:
        return jsonify({"ok": False, "error": "Style not found"}), 404
    return jsonify({"ok": True, "style": style})


@app.route("/api/styles/<int:style_id>", methods=["PATCH"])
@login_required
def api_styles_update(style_id):
    body = request.get_json(silent=True) or {}
    try:
        style = update_style(style_id, body)
    except LookupError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "style": style})


@app.route("/api/styles/<int:style_id>", methods=["DELETE"])
@login_required
def api_styles_delete(style_id):
    if not delete_style(style_id):
        return jsonify({"ok": False, "error": "Style not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/shots/<int:shot_id>/takes", methods=["GET"])
@login_required
def api_shot_takes(shot_id):
    if not get_shot(shot_id):
        return jsonify({"ok": False, "error": "Shot not found"}), 404
    return jsonify({"ok": True, "takes": fetch_takes(shot_id)})


@app.route("/api/shots/<int:shot_id>/render", methods=["POST"])
@login_required
def api_shot_render(shot_id):
    body = request.get_json(silent=True) or {}
    try:
        result = start_shot_render(shot_id, body)
    except LookupError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, **result}), 202


@app.route("/api/shots/<int:shot_id>/compile", methods=["POST"])
@login_required
def api_shot_compile(shot_id):
    """Proposal only. The editor writes the field when the user accepts."""
    body = request.get_json(silent=True) or {}
    try:
        result = compile_shot_prompt(shot_id, str(body.get("field") or "prompt"))
    except LookupError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except PermissionError as exc:
        return jsonify({"ok": False, "error": str(exc), "locked": True}), 409
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    return jsonify({"ok": True, **result})


@app.route("/api/shots/<int:shot_id>/compile-context", methods=["GET"])
@login_required
def api_shot_compile_context(shot_id):
    shot = get_shot(shot_id)
    if not shot:
        return jsonify({"ok": False, "error": "Shot not found"}), 404
    project = get_project(shot["project_id"])
    context, slots = build_shot_compile_context(shot, project or {"id": shot["project_id"], "name": "", "settings": {}})
    return jsonify({"ok": True, "context": context, "slots": slots, "tokens": estimate_tokens(context), "rules": COMPILER_RULES})


@app.route("/api/shots/<int:shot_id>/render-preview", methods=["POST"])
@login_required
def api_shot_render_preview(shot_id):
    """Dry run: the payload that a render would send, without rendering."""
    shot = get_shot(shot_id)
    if not shot:
        return jsonify({"ok": False, "error": "Shot not found"}), 404
    project = get_project(shot["project_id"])
    body = request.get_json(silent=True) or {}
    try:
        payload, warnings = build_shot_render_payload(shot, project or {}, body)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "payload": summarize_render_payload(payload), "warnings": warnings})


@app.route("/api/takes/<int:take_id>", methods=["PATCH"])
@login_required
def api_take_update(take_id):
    body = request.get_json(silent=True) or {}
    try:
        if "approved" in body:
            take = set_take_approval(take_id, bool(body.get("approved")))   # Task 16: renames on disk, then the DB
        else:
            take = update_take(take_id)
    except LookupError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except (OSError, FileExistsError, FileNotFoundError) as exc:
        return jsonify({"ok": False, "error": f"Approval not changed: {exc}"}), 409
    return jsonify({"ok": True, "take": take, "shot": get_shot(take["shot_id"]) if take else None})


@app.route("/api/takes/<int:take_id>", methods=["DELETE"])
@login_required
def api_take_delete(take_id):
    take = get_take(take_id)
    if not take or not delete_take(take_id):
        return jsonify({"ok": False, "error": "Take not found"}), 404
    return jsonify({"ok": True, "shot": get_shot(take["shot_id"])})


@app.route("/api/reference-archive-list")
@login_required
def api_reference_archive_list():
    result = []
    for item in collect_reference_archive_records():
        params = item.get("params", {})
        result.append({
            "url": item.get("url", ""),
            "original_url": item.get("original_url", ""),
            "masked_url": item.get("masked_url", ""),
            "mask_url": item.get("mask_url", ""),
            "has_mask": item.get("has_mask", False),
            "mask_edit_payload": item.get("mask_edit_payload", {}),
            "date": item.get("date", ""),
            "filename": item.get("filename", ""),
            "prompt": (item.get("reference_name") or params.get("prompt", "") or "")[:60],
            "model_label": params.get("model_label", ""),
            "imageSize": params.get("imageSize", ""),
            "aspectRatio": params.get("aspectRatio", ""),
        })
    return jsonify(result)


@app.route("/api/reference-archive-payload/<date_str>/<filename>")
@login_required
def api_reference_archive_payload(date_str, filename):
    safe_date = str(date_str or "").strip()
    safe_filename = os.path.basename(str(filename or "").strip())
    if not safe_date or not safe_filename:
        return jsonify({"ok": False, "error": "Invalid archive reference"}), 400

    paths = get_reference_mask_file_paths(safe_date, safe_filename)
    original_path = paths["original_path"]
    if not os.path.exists(original_path):
        recovered_path, recovered_name = find_reference_recovery_source(safe_filename)
        if recovered_path:
            try:
                payload = build_reference_payload_from_file(recovered_path, recovered_name or safe_filename)
                payload.update({
                    "date": safe_date,
                    "filename": safe_filename,
                })
                return jsonify(payload)
            except Exception as exc:
                return jsonify({"ok": False, "error": str(exc)}), 500
        return jsonify({"ok": False, "error": "Reference archive entry not found"}), 404

    bundle = build_reference_mask_bundle(safe_date, safe_filename)
    display_path = paths["render_path"] if bundle["has_mask"] and os.path.exists(paths["render_path"]) else original_path

    try:
        with open(original_path, "rb") as fh:
            original_b64 = base64.b64encode(fh.read()).decode("utf-8")
        with open(display_path, "rb") as fh:
            display_b64 = base64.b64encode(fh.read()).decode("utf-8")
        mask_b64 = ""
        if bundle["has_mask"] and os.path.exists(paths["mask_path"]):
            with open(paths["mask_path"], "rb") as fh:
                mask_b64 = base64.b64encode(fh.read()).decode("utf-8")
        return jsonify({
            "ok": True,
            "date": safe_date,
            "filename": safe_filename,
            "name": safe_filename,
            "mime_type": "image/png",
            "data": display_b64,
            "original_data": original_b64,
            "original_mime_type": "image/png",
            "mask_png_data": mask_b64,
            "original_url": bundle["original_url"],
            "masked_url": bundle["masked_url"],
            "mask_url": bundle["mask_url"],
            "has_mask": bundle["has_mask"],
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/asset-gallery/<kind>")
@login_required
def api_asset_gallery(kind):
    if kind not in ASSET_GALLERY_PAGE_CONFIG:
        return jsonify({"ok": False, "error": "Invalid gallery kind"}), 404
    return jsonify({"ok": True, "items": collect_asset_records(kind)})


@app.route("/api/import-ref-image", methods=["POST"])
@login_required
def api_import_ref_image():
    body = request.get_json(silent=True) or {}
    url = str(body.get("url", "") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "Image URL is empty"})

    try:
        image_b64, mime_type, filename = fetch_remote_reference_image(url)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})

    return jsonify({
        "ok": True,
        "data": image_b64,
        "mime_type": mime_type,
        "name": filename,
    })

# ---------------------------------------------------------------------------
# Routes Ã¢â‚¬â€ Elements (asset library)
# ---------------------------------------------------------------------------

@app.route("/elements/<path:filepath>")
@login_required
def serve_element_file(filepath):
    """Serve element images (model, location, prop)."""
    full_path = os.path.join(ELEMENTS_DIR, filepath)
    directory = os.path.dirname(full_path)
    filename  = os.path.basename(full_path)
    return send_from_directory(directory, filename)


@app.route("/api/elements")
@login_required
def api_elements_catalog():
    """
    Return all categories and their assets from catalog.json.
    Supporta ?category=slug e ?q=search per filtrare.
    """
    category_filter = request.args.get("category", "all")
    search_query    = request.args.get("q", "").strip().lower()
    page            = int(request.args.get("page", 1))
    per_page        = int(request.args.get("per_page", 60))
    # Filtri metadati (solo characters) Ã¢â‚¬â€ tutti exact-match con vocabolario canonico
    def _fset(param): return {v.strip().lower() for v in request.args.get(param,"").split(",") if v.strip()}
    f_gender     = _fset("gender")
    f_age        = _fset("age_group")
    f_ethnicity  = _fset("ethnicity")
    f_skin       = _fset("skin_tone")
    f_hair       = _fset("hair_color")
    f_hair_style = _fset("hair_style")
    f_body       = _fset("body_type")

    result_categories = []
    all_items         = []

    if not os.path.isdir(ELEMENTS_DIR):
        return jsonify({"categories": [], "items": [], "total": 0})

    for folder_name, cat_meta in ELEMENTS_CATEGORIES.items():
        folder_path  = os.path.join(ELEMENTS_DIR, folder_name)
        catalog_path = os.path.join(folder_path, "catalog.json")

        if not os.path.isdir(folder_path):
            continue

        result_categories.append({
            "slug":  cat_meta["slug"],
            "label": cat_meta["label"],
            "icon":  cat_meta["icon"],
        })

        if category_filter not in ("all", cat_meta["slug"]):
            continue

        # Priority 1: individual talent JSON files
        individual_jsons = list_talent_jsons(folder_path)
        if individual_jsons:
            raw_items = []
            for jpath in sorted(individual_jsons):
                talent = load_talent_json(jpath)
                if not talent:
                    continue
                # Derive image_path from the primary image (or the first available one)
                images = talent.get("images", [])
                primary = next((i for i in images if i.get("is_primary")), None)
                if not primary and images:
                    primary = images[0]
                if primary:
                    talent["image_path"] = primary.get("path", "")
                raw_items.append(talent)
        # Priority 2: legacy catalog.json
        elif os.path.exists(catalog_path):
            try:
                with open(catalog_path, encoding="utf-8") as f:
                    data = json.load(f)
                raw_items = data.get("talents", data.get("items", []))
            except Exception:
                raw_items = []
        else:
            # Fallback: scan images in the images/ folder
            img_dir   = os.path.join(folder_path, "images")
            raw_items = []
            if os.path.isdir(img_dir):
                for fname in sorted(os.listdir(img_dir)):
                    if fname.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                        name = os.path.splitext(fname)[0].replace("_", " ").title()
                        raw_items.append({"id": fname, "name": name, "image_path": f"images/{fname}"})

        for item in raw_items:
            img_path = item.get("image_path", "")
            # Verify that the image exists on disk
            full_img = os.path.join(folder_path, img_path)
            if not os.path.exists(full_img):
                continue

            # Costruisci URL di servizio
            img_url = f"/elements/{folder_name}/{img_path}"

            asset = {
                "id":          item.get("id", img_path),
                "name":        item.get("name", ""),
                "category":    cat_meta["slug"],
                "cat_label":   cat_meta["label"],
                "img_url":     img_url,
                "folder":      folder_name,
                "img_path":    img_path,
                # All physical metadata for the @mention prompt
                "gender":      item.get("gender", ""),
                "ethnicity":   item.get("ethnicity", ""),
                "age_group":   item.get("age_group", ""),
                "skin_tone":   item.get("skin_tone", ""),
                "hair_color":  item.get("hair_color", ""),
                "hair_style":  item.get("hair_style", ""),
                "eye_color":   item.get("eye_color", ""),
                "body_type":   item.get("body_type", ""),
                "tags":        item.get("tags", []),
                "description": item.get("description", ""),
                "is_favorite": item.get("is_favorite", False),
                "profile":     item.get("profile", {}),
            }
            # Task 11: type from the record, else from the folder; no "type" means talent.
            asset["type"] = normalize_element_type(item.get("type") or ELEMENT_FOLDER_TYPES.get(folder_name, "talent"))
            for key in ELEMENT_TYPES[asset["type"]]["fields"]:
                asset.setdefault(key, item.get(key, ""))
            all_items.append(asset)

    # Deduplicate by id (same person with multiple images in the catalog)
    seen_ids = set()
    unique_items = []
    for it in all_items:
        if it["id"] not in seen_ids:
            seen_ids.add(it["id"])
            unique_items.append(it)

    # Filter by text search
    if search_query:
        def matches(item):
            return (search_query in item["name"].lower() or
                    search_query in item.get("description", "").lower() or
                    any(search_query in t.lower() for t in item.get("tags", [])) or
                    search_query in item.get("gender", "").lower() or
                    search_query in item.get("ethnicity", "").lower())
        unique_items = [i for i in unique_items if matches(i)]

    # Filtri metadati Ã¢â‚¬â€ exact match (vocabolario canonico, tutti underscored)
    def _exact(items, field, fset):
        return [i for i in items if i.get(field, "").lower() in fset] if fset else items
    unique_items = _exact(unique_items, "gender",     f_gender)
    unique_items = _exact(unique_items, "age_group",  f_age)
    unique_items = _exact(unique_items, "ethnicity",  f_ethnicity)
    unique_items = _exact(unique_items, "skin_tone",  f_skin)
    unique_items = _exact(unique_items, "hair_color", f_hair)
    unique_items = _exact(unique_items, "hair_style", f_hair_style)
    unique_items = _exact(unique_items, "body_type",  f_body)

    # Newest first, then favorites at the top
    unique_items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    unique_items.sort(key=lambda x: not x.get("is_favorite", False))

    total = len(unique_items)
    start = (page - 1) * per_page
    paged = unique_items[start:start + per_page]

    return jsonify({
        "categories": result_categories,
        "items":      paged,
        "total":      total,
        "page":       page,
        "per_page":   per_page,
        "pages":      (total + per_page - 1) // per_page
    })


@app.route("/api/elements/toggle-favorite", methods=["POST"])
@login_required
def api_elements_toggle_favorite():
    """Update is_favorite for an asset (individual JSON or catalog.json)."""
    body        = request.get_json()
    asset_id    = body.get("id")
    folder_name = body.get("folder")
    favorite    = bool(body.get("favorite", False))

    folder_path = os.path.join(ELEMENTS_DIR, folder_name)

    # Prova prima il JSON individuale
    jpath = talent_json_path(folder_path, asset_id)
    if os.path.exists(jpath):
        try:
            talent = load_talent_json(jpath)
            if talent:
                talent["is_favorite"] = favorite
                talent["updated_at"]  = datetime.now().isoformat()
                save_talent_json(jpath, talent)
                return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)})

    # Fallback: catalog.json
    catalog_path = os.path.join(folder_path, "catalog.json")
    if not os.path.exists(catalog_path):
        return jsonify({"ok": False, "error": "Talent not found"})
    try:
        with open(catalog_path, encoding="utf-8") as f:
            data = json.load(f)
        for item in data.get("talents", []):
            if item.get("id") == asset_id:
                item["is_favorite"] = favorite
                break
        with open(catalog_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# API - Elements: catalog migration -> individual JSON files
# ---------------------------------------------------------------------------
@app.route("/api/elements/migrate-catalog", methods=["POST"])
@login_required
def api_migrate_catalog():
    """
    Migrate catalog.json into individual JSON files (one file per talent).
    Safe: does not overwrite existing JSON files. Creates an automatic backup of catalog.json.
    """
    body        = request.get_json(silent=True) or {}
    folder_name = body.get("folder", "Model Managment")
    folder_path = os.path.join(ELEMENTS_DIR, folder_name)
    catalog_path = os.path.join(folder_path, "catalog.json")

    if not os.path.exists(catalog_path):
        return jsonify({"ok": False, "error": "catalog.json not found"})

    try:
        with open(catalog_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Error reading catalog: {e}"})

    raw_items = data.get("talents", data.get("items", []))
    now_ts    = datetime.now().isoformat()

    # Deduplica per id Ã¢â‚¬â€ per ogni id teniamo il record con image_path valido
    seen: dict[str, dict] = {}
    for item in raw_items:
        tid = item.get("id")
        if not tid:
            continue
        img_path = item.get("image_path", "")
        full_img = os.path.join(folder_path, img_path) if img_path else ""
        if tid not in seen:
            seen[tid] = item
        elif img_path and os.path.exists(full_img) and not seen[tid].get("image_path"):
            seen[tid] = item

    created = skipped = 0
    for talent_id, item in seen.items():
        jpath = talent_json_path(folder_path, talent_id)
        if os.path.exists(jpath):
            skipped += 1
            continue

        img_path = item.get("image_path", "")
        images   = []
        if img_path and os.path.exists(os.path.join(folder_path, img_path)):
            images.append({
                "filename":   os.path.basename(img_path),
                "path":       img_path,
                "added_at":   now_ts,
                "is_primary": True,
                "analyzed":   False,
            })

        talent_data = {
            "id":          talent_id,
            "name":        item.get("name", ""),
            "gender":      item.get("gender", ""),
            "ethnicity":   item.get("ethnicity", ""),
            "age_group":   item.get("age_group", ""),
            "skin_tone":   item.get("skin_tone", ""),
            "hair_color":  item.get("hair_color", ""),
            "hair_style":  item.get("hair_style", ""),
            "eye_color":   item.get("eye_color", ""),
            "body_type":   item.get("body_type", ""),
            "description": item.get("description", ""),
            "tags":        item.get("tags", []),
            "profile":     item.get("profile", {}),
            "is_favorite": item.get("is_favorite", False),
            "images":      images,
            "created_at":  now_ts,
            "updated_at":  now_ts,
        }
        save_talent_json(jpath, talent_data)
        created += 1

    # Backup catalog.json (one time only)
    bak_path = catalog_path + ".bak"
    if not os.path.exists(bak_path):
        shutil.copy2(catalog_path, bak_path)

    return jsonify({
        "ok":      True,
        "created": created,
        "skipped": skipped,
        "total":   len(seen),
        "message": f"Migration completed: {created} created, {skipped} already existed across {len(seen)} talents."
    })


# ---------------------------------------------------------------------------
# API - Elements: image analysis with Gemini Vision
# ---------------------------------------------------------------------------
def run_vision_extraction(api_key: str, image_b64: str, mime_type: str, analysis_prompt: str, timeout: int = 60) -> dict:
    """The one Gemini Vision extraction path (Task 11 lifted it out of the talent route unchanged).
    Returns {"ok", "metadata", "raw_text", "usage"} or {"ok": False, "error", ...}."""
    payload = {
        "contents": [{
            "role": "user",
            "parts": [
                {"inlineData": {"mimeType": mime_type, "data": image_b64}},
                {"text": analysis_prompt}
            ]
        }],
        "generationConfig": {
            "temperature":     0.2,
            "maxOutputTokens": 2048,
        }
    }

    try:
        resp = requests.post(
            GEMINI_BASE_URL.format(model=TALENT_ANALYSIS_MODEL),
            params={"key": api_key},
            json=payload,
            timeout=timeout
        )
    except requests.exceptions.Timeout:
        return {"ok": False, "error": f"Image analysis timeout ({timeout}s)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    if resp.status_code != 200:
        try:
            err_body = resp.json()
            err_msg  = err_body.get("error", {}).get("message", f"HTTP {resp.status_code}")
        except Exception:
            err_msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
        return {"ok": False, "error": err_msg}

    result   = resp.json()
    raw_text = ""
    # Estrae testo dal primo candidato con parti testo
    for candidate in result.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            if "text" in part:
                raw_text = part["text"].strip()
                break
        if raw_text:
            break

    # Calcola costo Vision da usageMetadata (token-based)
    usage         = result.get("usageMetadata", {})
    input_tokens  = usage.get("promptTokenCount", 0)
    output_tokens = usage.get("candidatesTokenCount", 0)
    vp            = VISION_MODELS_INFO.get(TALENT_ANALYSIS_MODEL, {})
    vision_cost   = round(
        input_tokens  * vp.get("input_per_1m",  0.075) / 1_000_000 +
        output_tokens * vp.get("output_per_1m", 0.30)  / 1_000_000,
        8
    )
    # Registra la chiamata nelle statistiche
    cfg_v  = load_config()
    sv     = cfg_v.setdefault("stats", DEFAULT_CONFIG["stats"].copy())
    sv["vision_calls"]    = sv.get("vision_calls",    0) + 1
    sv["vision_cost_usd"] = round(sv.get("vision_cost_usd", 0.0) + vision_cost, 8)
    vlog   = sv.setdefault("vision_log", [])
    vlog.insert(0, {
        "ts":           utc_now_iso(),
        "model":        TALENT_ANALYSIS_MODEL,
        "input_tok":    input_tokens,
        "output_tok":   output_tokens,
        "cost":         vision_cost,
    })
    if len(vlog) > 200:
        sv["vision_log"] = vlog[:200]
    save_config(cfg_v)

    if not raw_text:
        return {"ok": False, "error": "Empty response from Gemini", "raw": str(result)[:300]}

    # Parsing JSON robusto: prova diretta, poi estrai il primo { ... } block
    metadata = None
    # 1) Remove any markdown code fences ```json ... ```
    cleaned = re.sub(r"```(?:json)?\s*", "", raw_text).replace("```", "").strip()
    for candidate_text in [cleaned, raw_text]:
        try:
            metadata = json.loads(candidate_text)
            break
        except json.JSONDecodeError:
            pass
        # Look for the largest JSON block
        match = re.search(r"\{[\s\S]*\}", candidate_text)
        if match:
            try:
                metadata = json.loads(match.group())
                break
            except json.JSONDecodeError:
                pass

    if metadata is None:
        return {"ok": False, "error": "Could not extract JSON from response", "raw": raw_text[:500]}
    return {
        "ok": True,
        "metadata": metadata,
        "raw_text": raw_text,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": vision_cost},
    }


@app.route("/api/elements/vocabularies")
@login_required
def api_element_vocabularies():
    """Task 11: the per-type vocabularies as loaded from disk (empty where the file is absent)."""
    return jsonify({"ok": True, "vocabularies": {"talent": TALENT_VOCABULARY, "environment": ENVIRONMENT_VOCABULARY, "prop": PROP_VOCABULARY}})


@app.route("/api/elements/analyze-image", methods=["POST"])
@login_required
def api_analyze_talent_image():
    """
    Analyze a talent image with Gemini Vision and return JSON metadata.
    Uses TALENT_ANALYSIS_MODEL (gemini-3-flash-preview) - more capable for structured JSON extraction.
    Task 11: element_type = talent (default) | environment | prop | style, same path, different prompt.
    """
    config  = load_config()
    api_key = config.get("api_key", "").strip()
    if not api_key:
        return jsonify({"ok": False, "error": "API key not configured"})

    body      = request.get_json(silent=True) or {}
    image_b64 = body.get("data", "")
    mime_type = body.get("mime_type", "image/jpeg")
    element_type = str(body.get("element_type") or "talent").strip().lower()
    if element_type != "style":
        element_type = normalize_element_type(element_type)
    if not image_b64 and body.get("asset_url"):
        # An existing asset can be scanned by path (style scan from the reference picker).
        try:
            loaded = load_asset_image_payload(str(body.get("asset_url")), "scan.png")
            image_b64, mime_type = loaded["data"], loaded["mime_type"]
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)})

    if not image_b64:
        return jsonify({"ok": False, "error": "Image data missing"})

    # Normalize the image before sending it to Gemini
    try:
        image_b64, mime_type, orig_w, orig_h, proc_w, proc_h, was_resized = \
            normalize_image_b64(image_b64, mime_type)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Image pre-processing error: {e}"})

    vocab_block = _build_vocab_prompt_block()
    analysis_prompt = (
        "You are a professional talent catalog specialist. Analyze this portrait photo carefully and extract structured metadata.\n"
        "Your task: fill in EVERY field Ã¢â‚¬â€ never leave anything empty or use values outside the allowed lists.\n\n"
        f"{vocab_block}\n\n"
        "Additional field rules:\n"
        "- name: INVENT a realistic first+last name that fits the person's apparent ethnicity and vibe "
        "(e.g. Sofia Esposito, Kai Nakamura, Amara Diallo, Luca Ferretti, Yuki Tanaka, Zara Osei)\n"
        "- description: 2 precise sentences for AI image generation Ã¢â‚¬â€ describe face shape, skin quality, "
        "distinctive features (nose, lips, jawline, cheekbones), eye shape, expression, overall aesthetic vibe\n"
        "- tags: JSON array of 4Ã¢â‚¬â€œ6 lowercase, single-word or hyphenated tags useful for searching "
        "(e.g. [\"editorial\", \"beauty\", \"runway\", \"high-fashion\", \"dark-skin\", \"versatile\"])\n\n"
        "CRITICAL: You MUST use ONLY the exact string values listed above. "
        "Do NOT invent new values, do NOT use variations, plurals, or spaces instead of underscores.\n\n"
        "Return ONLY a valid JSON object Ã¢â‚¬â€ no markdown fences, no extra text, no comments:\n"
        "{\n"
        '  "name": "...",\n'
        '  "gender": "...",\n'
        '  "ethnicity": "...",\n'
        '  "age_group": "...",\n'
        '  "skin_tone": "...",\n'
        '  "hair_color": "...",\n'
        '  "hair_style": "...",\n'
        '  "eye_color": "...",\n'
        '  "body_type": "...",\n'
        '  "description": "...",\n'
        '  "tags": ["...", "..."]\n'
        "}"
    )
    if element_type == "style":
        analysis_prompt = STYLE_SCAN_PROMPT
    elif element_type != "talent":
        analysis_prompt = build_element_analysis_prompt(element_type)

    extraction = run_vision_extraction(api_key, image_b64, mime_type, analysis_prompt)
    if not extraction.get("ok"):
        return jsonify(extraction)
    metadata = extraction["metadata"]

    # Normalize missing keys with empty values
    if element_type == "style":
        defaults = {"text": ""}
    elif element_type == "talent":
        defaults = {"name":"","gender":"","ethnicity":"","age_group":"","skin_tone":"",
                    "hair_color":"","hair_style":"","eye_color":"","body_type":"","description":"","tags":[]}
    else:
        defaults = {"name": "", **{key: "" for key in ELEMENT_TYPES[element_type]["fields"]}, "description": "", "tags": []}
    for k, v in defaults.items():
        if k not in metadata:
            metadata[k] = v

    return jsonify({
        "ok":         True,
        "element_type": element_type,
        "metadata":   metadata,
        "raw_text":   extraction["raw_text"],
        "usage":      extraction["usage"],
        "image_info": {"orig_w": orig_w, "orig_h": orig_h,
                       "proc_w": proc_w, "proc_h": proc_h, "resized": was_resized},
    })


@app.route("/api/references/describe", methods=["POST"])
@login_required
def api_describe_reference_images():
    config = load_config()
    api_key = (config.get("api_key", "") or "").strip()
    if not api_key:
        return jsonify({"ok": False, "error": "Gemini API key not configured."}), 400

    body = request.get_json(silent=True) or {}
    ref_images = normalize_ref_image_payloads(body.get("refImages"), 32)
    if not ref_images:
        return jsonify({"ok": False, "error": "No reference images provided."}), 400

    describe_prompt = (
        "You are a high-end visual prompt writer for image generation.\n"
        "Analyze this reference image and write one detailed, production-ready descriptive prompt.\n"
        "Focus on visible subject matter, wardrobe, materials, textures, colors, styling, pose, composition, "
        "camera perspective, lighting, environment, mood, and any distinctive visual cues.\n"
        "Write a single dense paragraph in clean English.\n"
        "Do not use bullet points, markdown, labels, or filenames.\n"
        "Do not speculate about things that are not visible.\n"
        "Return only the prompt text."
    )

    descriptions: list[dict] = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_cost = 0.0

    for idx, item in enumerate(ref_images, start=1):
        image_b64 = str(item.get("data", "") or "").strip()
        mime_type = str(item.get("mime_type", "image/jpeg") or "image/jpeg")
        filename = (
            str(item.get("name") or "").strip()
            or os.path.basename(str(item.get("archive_filename") or "").strip())
            or f"reference_{idx}.png"
        )
        if not image_b64:
            continue

        try:
            image_b64, mime_type, _, _, _, _, _ = normalize_image_b64(image_b64, mime_type)
        except Exception as e:
            return jsonify({"ok": False, "error": f"Image pre-processing error for {filename}: {e}"}), 400

        payload = {
            "contents": [{
                "role": "user",
                "parts": [
                    {"inlineData": {"mimeType": mime_type, "data": image_b64}},
                    {"text": describe_prompt}
                ]
            }],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 1024,
            }
        }

        try:
            resp = requests.post(
                GEMINI_BASE_URL.format(model=TALENT_ANALYSIS_MODEL),
                params={"key": api_key},
                json=payload,
                timeout=60
            )
        except requests.exceptions.Timeout:
            return jsonify({"ok": False, "error": f"Description timeout for {filename} (60s)."}), 504
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

        if resp.status_code != 200:
            try:
                err_body = resp.json()
                err_msg = err_body.get("error", {}).get("message", f"HTTP {resp.status_code}")
            except Exception:
                err_msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
            return jsonify({"ok": False, "error": f"{filename}: {err_msg}"}), 400

        result = resp.json()
        raw_text = ""
        for candidate in result.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                if "text" in part and str(part["text"]).strip():
                    raw_text = str(part["text"]).strip()
                    break
            if raw_text:
                break

        if not raw_text:
            return jsonify({"ok": False, "error": f"Gemini returned no description for {filename}."}), 400

        usage = result.get("usageMetadata", {})
        input_tokens = int(usage.get("promptTokenCount", 0) or 0)
        output_tokens = int(usage.get("candidatesTokenCount", 0) or 0)
        vp = VISION_MODELS_INFO.get(TALENT_ANALYSIS_MODEL, {})
        vision_cost = round(
            input_tokens * vp.get("input_per_1m", 0.075) / 1_000_000 +
            output_tokens * vp.get("output_per_1m", 0.30) / 1_000_000,
            8
        )
        total_input_tokens += input_tokens
        total_output_tokens += output_tokens
        total_cost += vision_cost

        descriptions.append({
            "filename": filename,
            "description": raw_text,
        })

    cfg_v = load_config()
    sv = cfg_v.setdefault("stats", DEFAULT_CONFIG["stats"].copy())
    sv["vision_calls"] = sv.get("vision_calls", 0) + len(descriptions)
    sv["vision_cost_usd"] = round(sv.get("vision_cost_usd", 0.0) + total_cost, 8)
    vlog = sv.setdefault("vision_log", [])
    vlog.insert(0, {
        "ts": utc_now_iso(),
        "model": TALENT_ANALYSIS_MODEL,
        "input_tok": total_input_tokens,
        "output_tok": total_output_tokens,
        "cost": round(total_cost, 8),
        "refs_described": len(descriptions),
    })
    if len(vlog) > 200:
        sv["vision_log"] = vlog[:200]
    save_config(cfg_v)

    return jsonify({
        "ok": True,
        "descriptions": descriptions,
        "usage": {
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "cost_usd": round(total_cost, 8),
        }
    })


# ---------------------------------------------------------------------------
# API - Elements: save new talent
# ---------------------------------------------------------------------------
@app.route("/api/elements/save-talent", methods=["POST"])
@login_required
def api_save_talent():
    """
    Save a new talent: image with an incremental filename + JSON metadata.
    If the talent (slug) already exists, append the new image to images[].
    """
    body        = request.get_json(silent=True) or {}
    image_b64   = body.get("image_data", "")
    mime_type   = body.get("mime_type", "image/jpeg")
    element_type = normalize_element_type(body.get("element_type") or ELEMENT_FOLDER_TYPES.get(str(body.get("folder") or ""), "talent"))
    folder_name = body.get("folder") or ELEMENT_TYPES[element_type]["folder"]
    metadata    = body.get("metadata", {})

    if not image_b64:
        return jsonify({"ok": False, "error": "Image data missing"})

    folder_path = os.path.join(ELEMENTS_DIR, folder_name)
    if not os.path.isdir(folder_path):
        if element_type == "talent":
            return jsonify({"ok": False, "error": f"Folder '{folder_name}' not found"})
        os.makedirs(folder_path, exist_ok=True)  # Locations / Props are created on first save

    name = metadata.get("name", "talent").strip() or "talent"
    slug = name_to_slug(name)

    # Normalize image (resize if >4000px, convert to JPG 90%)
    try:
        image_b64, mime_type, _, _, _, _, _ = normalize_image_b64(image_b64, mime_type)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Image pre-processing error: {e}"})

    # Numero progressivo immagine Ã¢â‚¬â€ sempre .jpg dopo normalizzazione
    num      = get_next_image_number(folder_path, slug)
    img_file = f"{slug}_{num:03d}.jpg"
    img_full = os.path.join(folder_path, img_file)

    # Save image to disk
    try:
        img_bytes = base64.b64decode(image_b64)
        with open(img_full, "wb") as f:
            f.write(img_bytes)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Image save error: {e}"})

    now_ts     = datetime.now().isoformat()
    jpath      = talent_json_path(folder_path, slug)
    new_img    = {
        "filename":   img_file,
        "path":       img_file,
        "added_at":   now_ts,
        "is_primary": not os.path.exists(jpath),   # first image = primary
        "analyzed":   True,
    }

    if os.path.exists(jpath):
        # Talent esistente Ã¢â€ â€™ aggiungi immagine
        talent = load_talent_json(jpath)
        if talent:
            talent["images"].append(new_img)
            talent["updated_at"] = now_ts
            save_talent_json(jpath, talent)
    else:
        # New talent
        talent = {
            "id":          slug,
            "name":        metadata.get("name", name),
            "gender":      metadata.get("gender", ""),
            "ethnicity":   metadata.get("ethnicity", ""),
            "age_group":   metadata.get("age_group", ""),
            "skin_tone":   metadata.get("skin_tone", ""),
            "hair_color":  metadata.get("hair_color", ""),
            "hair_style":  metadata.get("hair_style", ""),
            "eye_color":   metadata.get("eye_color", ""),
            "body_type":   metadata.get("body_type", ""),
            "description": metadata.get("description", ""),
            "tags":        metadata.get("tags", []),
            "profile":     {},
            "is_favorite": False,
            "images":      [new_img],
            "created_at":  now_ts,
            "updated_at":  now_ts,
        }
        if element_type != "talent":
            # Environment / prop: same record shape, that type's attribute set instead of the talent one.
            talent = {"id": slug, "name": metadata.get("name", name)}
            for key in ELEMENT_TYPES[element_type]["fields"]:
                talent[key] = metadata.get(key, "")
            talent.update({
                "description": metadata.get("description", ""),
                "tags":        metadata.get("tags", []),
                "profile":     {},
                "is_favorite": False,
                "images":      [new_img],
                "created_at":  now_ts,
                "updated_at":  now_ts,
            })
        talent["type"] = element_type
        save_talent_json(jpath, talent)

    return jsonify({
        "ok":        True,
        "talent_id": slug,
        "image_url": f"/elements/{folder_name}/{img_file}",
        "is_new":    len(talent.get("images", [])) == 1,
    })


# ---------------------------------------------------------------------------
# API Ã¢â‚¬â€ Config / Key
# ---------------------------------------------------------------------------
@app.route("/api/save-config", methods=["POST"])
@login_required
def api_save_config():
    data = request.get_json() or {}
    config = load_config()
    if "api_key" in data:
        config["api_key"] = data["api_key"].strip()
    if "fal_api_key" in data:
        config["fal_api_key"] = data["fal_api_key"].strip()
    if "byteplus_api_key" in data:
        config["byteplus_api_key"] = data["byteplus_api_key"].strip()
        config["seedream_api_key"] = data["byteplus_api_key"].strip()
    if "seedream_api_key" in data:
        config["byteplus_api_key"] = data["seedream_api_key"].strip()
        config["seedream_api_key"] = data["seedream_api_key"].strip()
    if "kling_api_token" in data:
        config["kling_api_token"] = data["kling_api_token"].strip()
    if "luma_api_key" in data:
        config["luma_api_key"] = data["luma_api_key"].strip()
    if "openai_api_key" in data:
        config["openai_api_key"] = data["openai_api_key"].strip()
    for dormant_key in ("topaz_api_key", "bfl_api_key", "clarity_api_key"):   # Task 16: stored only
        if dormant_key in data:
            config[dormant_key] = str(data[dormant_key] or "").strip()
    if "openai_model" in data:
        config["openai_model"] = str(data["openai_model"] or "").strip() or "gpt-4o-mini"
    if "agent_instructions" in data:
        config["agent_instructions"] = str(data["agent_instructions"] or "")   # the user's rules, as written
    save_config(config)
    return jsonify({"ok": True})


@app.route("/api/verify-openai-key", methods=["POST"])
@login_required
def api_verify_openai_key():
    data = request.get_json() or {}
    key = (data.get("openai_api_key") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "OpenAI API key is empty"})
    try:
        response = requests.get(f"{OPENAI_API_BASE}/models", headers={"Authorization": f"Bearer {key}"}, timeout=45)
    except requests.exceptions.Timeout:
        return jsonify({"ok": False, "error": "Connection timeout"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})
    if response.status_code == 200:
        return jsonify({"ok": True, "message": "Valid OpenAI key and API access confirmed."})
    try:
        err = response.json().get("error", {}).get("message", f"HTTP {response.status_code}")
    except Exception:
        err = f"HTTP {response.status_code}"
    return jsonify({"ok": False, "error": err})


TOPAZ_API_BASE = "https://api.topazlabs.com"


@app.route("/api/verify-topaz-key", methods=["POST"])
@login_required
def api_verify_topaz_key():
    """Topaz has no cheap list endpoint; a status probe answers 401/403 for a bad key and
    404/400 for a good one (the id does not exist). Nothing is generated."""
    data = request.get_json() or {}
    key = (data.get("topaz_api_key") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "Topaz API key is empty"})
    try:
        response = requests.get(f"{TOPAZ_API_BASE}/image/v1/status/verify-probe", headers={"X-API-Key": key, "Accept": "application/json"}, timeout=45)
    except requests.exceptions.Timeout:
        return jsonify({"ok": False, "error": "Connection timeout"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})
    if response.status_code in (401, 403):
        return jsonify({"ok": False, "error": "Topaz rejected the key (HTTP %d)" % response.status_code})
    if response.status_code in (200, 400, 404, 422):
        return jsonify({"ok": True, "message": "Topaz accepted the key (HTTP %d on the probe)." % response.status_code})
    return jsonify({"ok": False, "error": f"Unexpected reply from Topaz: HTTP {response.status_code}"})


@app.route("/api/verify-fal-key", methods=["POST"])
@app.route("/api/verify-seedream-key", methods=["POST"])
@login_required
def api_verify_fal_key():
    data = request.get_json() or {}
    key = (data.get("fal_api_key") or data.get("seedream_api_key") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "Fal API key is empty"})
    payload = {
        "prompt": "Fal provider verification image",
        "image_size": "auto_2K",
        "num_images": 1,
        "max_images": 1,
        "sync_mode": True,
        "enable_safety_checker": True,
    }
    headers = {
        "Authorization": f"Key {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        resp = requests.post(f"{FAL_BASE_URL}/{FAL_SEEDREAM_45_TEXT_ID}", headers=headers, json=payload, timeout=45)
        if resp.status_code == 200:
            return jsonify({"ok": True, "message": "Valid Fal key and model access confirmed."})
        return jsonify({"ok": False, "error": extract_fal_error(resp)})
    except requests.exceptions.Timeout:
        return jsonify({"ok": False, "error": "Connection timeout"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/verify-byteplus-key", methods=["POST"])
@login_required
def api_verify_byteplus_key():
    data = request.get_json() or {}
    key = (data.get("byteplus_api_key") or data.get("seedream_api_key") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "BytePlus API key is empty"})
    payload = {
        "model": BYTEPLUS_SEEDREAM_45_MODEL_ID,
        "prompt": "BytePlus provider verification image",
        "sequential_image_generation": "disabled",
        "response_format": "url",
        "size": "2K",
        "stream": False,
        "watermark": False,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        resp = requests.post(BYTEPLUS_BASE_URL, headers=headers, json=payload, timeout=45)
        if resp.status_code == 200:
            return jsonify({"ok": True, "message": "Valid BytePlus key and Seedream access confirmed."})
        return jsonify({"ok": False, "error": extract_byteplus_error(resp)})
    except requests.exceptions.Timeout:
        return jsonify({"ok": False, "error": "Connection timeout"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/verify-luma-key", methods=["POST"])
@login_required
def api_verify_luma_key():
    data = request.get_json() or {}
    key = (data.get("luma_api_key") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "Luma API key is empty"})
    try:
        response = requests.get(
            f"{LUMA_GENERATIONS_URL}?limit=1&offset=0",
            headers=build_luma_headers(key),
            timeout=45,
        )
    except requests.exceptions.Timeout:
        return jsonify({"ok": False, "error": "Connection timeout"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})
    if response.status_code == 200:
        return jsonify({"ok": True, "message": "Valid Luma key and API access confirmed."})
    return jsonify({"ok": False, "error": extract_luma_error(response)})


@app.route("/api/verify-kling-token", methods=["POST"])
@login_required
def api_verify_kling_token():
    data = request.get_json() or {}
    token = str(data.get("kling_api_token") or "").strip()
    if not token:
        return jsonify({"ok": False, "error": "Kling API token is empty"})
    try:
        headers = build_kling_headers(token)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)})
    minted = token.count(".") != 2
    try:
        response = requests.get(f"{KLING_BASE_URL}/v1/videos/text2video?pageNum=1&pageSize=1", headers=headers, timeout=45)
        if response.status_code == 200:
            return jsonify({"ok": True, "message": "Valid Kling credentials confirmed." + (" The app will sign a fresh token for each request." if minted else " Note: a pasted JWT expires; ACCESS_KEY:SECRET_KEY is more reliable.")})
        return jsonify({"ok": False, "error": extract_kling_error(response)})
    except requests.exceptions.Timeout:
        return jsonify({"ok": False, "error": "Connection timeout"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@app.route("/api/verify-key", methods=["POST"])
@login_required
def api_verify_key():
    data = request.get_json()
    key  = data.get("api_key", "").strip()
    if not key:
        return jsonify({"ok": False, "error": "API key is empty"})
    try:
        resp = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": key}, timeout=10
        )
        if resp.status_code == 200:
            models_list = resp.json().get("models", [])
            return jsonify({"ok": True, "message": f"Valid key. {len(models_list)} models available."})
        elif resp.status_code == 400:
            return jsonify({"ok": False, "error": "Invalid API key (400)"})
        elif resp.status_code == 403:
            return jsonify({"ok": False, "error": "Access denied Ã¢â‚¬â€ check billing is active (403)"})
        else:
            return jsonify({"ok": False, "error": f"HTTP error {resp.status_code}"})
    except requests.exceptions.Timeout:
        return jsonify({"ok": False, "error": "Connection timeout"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# API Ã¢â‚¬â€ Generate (con supporto immagini di riferimento)
# ---------------------------------------------------------------------------
def run_gemini_generation_job(body: dict, api_key: str) -> dict:
    body = normalize_generation_request(body)
    model_id     = body.get("model", "gemini-3.1-flash-image-preview")
    image_size   = body.get("imageSize", "1K")
    num_images   = max(1, min(int(body.get("numberOfImages", 1)), 4))
    temperature  = float(body.get("temperature", 1.0))
    top_p        = float(body.get("topP", 0.95))
    thinking_lvl = body.get("thinkingLevel", "Minimal")
    use_search   = bool(body.get("useSearch", False))
    gemini_safety_settings, gemini_safety_preset = build_gemini_safety_settings(body.get("geminiSafetyPreset", "default"))
    output_mode  = body.get("outputMode", "images_text")
    ref_images   = body.get("refImages", [])
    aspect_ratio = body.get("aspectRatio", "1:1")
    seed_mode    = normalize_seed_mode(body.get("seedMode", "random"))
    seed_value   = coerce_seed_value(body.get("seedValue", 1))
    actual_seed  = seed_value if seed_mode != "random" else random.randint(1, MAX_SEED_VALUE)

    raw_prompt = body.get("prompt", "")
    if isinstance(raw_prompt, dict):
        prompt = json.dumps(raw_prompt, ensure_ascii=False, indent=2)
    else:
        prompt = str(raw_prompt).strip()

    if prompt.startswith("{"):
        try:
            prompt_obj = json.loads(prompt)
            ar_from_prompt = (prompt_obj.get("composition", {}) or {}).get("aspect_ratio", "")
            if ar_from_prompt and aspect_ratio == "1:1":
                aspect_ratio = ar_from_prompt
        except (json.JSONDecodeError, AttributeError):
            pass

    if not prompt:
        raise ValueError("Please enter a prompt")
    if model_id not in MODELS_INFO:
        raise ValueError("Invalid model")

    model_info = MODELS_INFO[model_id]
    max_ref = model_info["max_ref_images"]
    if ref_images and max_ref == 0:
        raise ValueError(f"{model_info['label']} does not support reference images.")
    ref_images = normalize_ref_image_payloads(ref_images, max_ref)

    parts = []
    for img in ref_images:
        parts.append({
            "inlineData": {
                "mimeType": img.get("mime_type", "image/png"),
                "data": img.get("data", "")
            }
        })
    parts.append({"text": prompt})

    modalities = ["IMAGE"] if output_mode == "images_only" else ["TEXT", "IMAGE"]
    image_config = {"aspectRatio": aspect_ratio}
    if model_id in {"gemini-3.1-flash-image-preview", "gemini-3-pro-image-preview"}:
        image_config["imageSize"] = image_size

    gen_config = {
        "responseModalities": modalities,
        "imageConfig": image_config,
        "temperature": temperature,
        "topP": top_p,
        "maxOutputTokens": 65536,
    }
    if model_id == "gemini-3.1-flash-image-preview":
        thinking_budget_map = {"Minimal": 0, "High": 8192, "Dynamic": -1}
        gen_config["thinkingConfig"] = {
            "thinkingBudget": thinking_budget_map.get(thinking_lvl, 0)
        }

    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": gen_config,
    }
    if gemini_safety_settings:
        payload["safetySettings"] = gemini_safety_settings
    if use_search:
        payload["tools"] = [{"googleSearch": {}}]

    all_responses = []
    for _ in range(num_images):
        try:
            response = requests.post(
                GEMINI_BASE_URL.format(model=model_id),
                params={"key": api_key},
                json=payload,
                timeout=120,
            )
            all_responses.append(response)
        except requests.exceptions.Timeout as exc:
            raise TimeoutError("Timeout: generation took too long.") from exc
        except Exception as exc:
            raise RuntimeError(f"Network error: {exc}") from exc

    for response in all_responses:
        if response.status_code != 200:
            try:
                err_msg = response.json().get("error", {}).get("message", f"HTTP {response.status_code}")
            except Exception:
                err_msg = f"HTTP {response.status_code}"
            raise RuntimeError(err_msg)

    images = []
    text_parts = []
    response_issues = []
    for response in all_responses:
        result = response.json()
        response_images_before = len(images)
        for candidate in result.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                if "inlineData" in part:
                    img_data = part["inlineData"]
                    images.append({
                        "mime_type": img_data.get("mimeType", "image/png"),
                        "data": img_data.get("data", "")
                    })
                elif "text" in part:
                    text_parts.append(part["text"])
        if len(images) == response_images_before:
            response_issues.append(
                summarize_generate_response_issue(
                    result,
                    safety_preset=gemini_safety_preset,
                    safety_settings_sent=bool(gemini_safety_settings),
                )
            )

    if not images:
        issue_entry = next((item for item in response_issues if item and item[0]), None)
        if issue_entry:
            issue_message, issue_debug = issue_entry
        else:
            issue_message, issue_debug = (
                "Gemini returned no image for this request.",
                {
                    "provider": "gemini",
                    "safetyPreset": gemini_safety_preset,
                    "safetySettingsSent": bool(gemini_safety_settings),
                    "summary": "Gemini returned no image for this request.",
                    "candidates": [],
                },
            )
        raise GenerationDebugError(issue_message, issue_debug)

    png_images = []
    for img in images:
        try:
            png_b64, png_mime = convert_image_b64_to_png(
                img.get("data", ""),
                img.get("mime_type", "image/png")
            )
            png_images.append({
                "mime_type": png_mime,
                "data": png_b64,
            })
        except Exception:
            png_images.append(img)
    images = png_images

    price_per_image = PRICING.get(model_id, {}).get(image_size, 0.0)
    cost = price_per_image * len(images)
    params_meta = merge_request_settings(merge_asset_metadata({
        "model": model_id,
        "modelFamily": body.get("modelFamily", model_info.get("family", "")),
        "model_label": model_info["label"],
        "provider": body.get("provider", model_info.get("provider", "gemini")),
        "provider_label": model_info.get("provider_label", PROVIDER_LABELS.get(body.get("provider", "gemini"), "Gemini")),
        "imageSize": image_size,
        "aspectRatio": aspect_ratio,
        "temperature": temperature,
        "topP": top_p,
        "thinkingLevel": thinking_lvl,
        "useSearch": use_search,
        "geminiSafetyPreset": gemini_safety_preset,
        "geminiSafetySettingsSent": bool(gemini_safety_settings),
        "outputMode": output_mode,
        "prompt": prompt,
        "ref_count": len(ref_images),
        "seedMode": seed_mode,
        "seedValue": actual_seed,
    }, body), body)
    return {
        "ok": True,
        "images": images,
        "text": "\n".join(text_parts),
        "cost": round(cost, 4),
        "model_label": model_info["label"],
        "params": params_meta,
        "_input_ref_images": ref_images,
    }


def persist_generation_result(result: dict):
    config = load_config()
    stats  = config.setdefault("stats", DEFAULT_CONFIG["stats"].copy())
    stats["total_requests"] = stats.get("total_requests", 0) + 1
    stats["total_images"]   = stats.get("total_images", 0) + len(result.get("images", []))
    stats["total_cost_usd"] = round(stats.get("total_cost_usd", 0.0) + result.get("cost", 0.0), 6)
    params = result.get("params", {})
    raw_ref_images = result.pop("_input_ref_images", None)
    log_entry = {
        "ts": utc_now_iso(),
        "model": params.get("model", ""),
        "provider": params.get("provider", ""),
        "size": params.get("imageSize", ""),
        "aspect": params.get("aspectRatio", ""),
        "upscaler": params.get("upscalerType", ""),
        "n": len(result.get("images", [])),
        "ref_n": params.get("ref_count", 0),
        "cost": round(result.get("cost", 0.0), 4),
        "prompt_preview": params.get("prompt", "")[:60],
    }
    log = stats.setdefault("requests_log", [])
    log.insert(0, log_entry)
    stats["requests_log"] = log[:50]
    save_config(config)

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H%M%S%f")[:12]
    fallback_filename = sanitize_asset_filename_stem(params.get("prompt", ""), fallback=time_str)
    asset_meta = normalize_asset_metadata(params, require_filename=True, fallback_filename=fallback_filename)
    params.update(asset_meta)
    update_asset_metadata_memory(config, asset_meta)
    if raw_ref_images is not None:
        archived_refs = build_reference_archive_entries(raw_ref_images, date_str, time_str)
        params["refArchive"] = archived_refs
        params["ref_count"] = len(archived_refs)
    else:
        params["refArchive"] = params.get("refArchive", []) or []
        params["ref_count"] = int(params.get("ref_count", len(params["refArchive"])))
    result["params"] = params

    for g_idx, img in enumerate(result.get("images", [])):
        try:
            png_b64, png_mime = convert_image_b64_to_png(
                img.get("data", ""),
                img.get("mime_type", "image/png")
            )
            img["data"] = png_b64
            img["mime_type"] = png_mime
            delivered_width, delivered_height = measure_image_dimensions(png_b64, png_mime)
            delivered_size = approximate_image_size_label(delivered_width, delivered_height)
            img["deliveredWidth"] = delivered_width
            img["deliveredHeight"] = delivered_height
            img["deliveredImageSize"] = delivered_size
            abs_dir, relpath, basename = build_asset_storage_paths(
                GENERATIONS_DIR,
                asset_meta,
                "png",
                variant_suffix=str(g_idx + 1) if len(result.get("images", [])) > 1 else "",
            )
            img_path = os.path.join(GENERATIONS_DIR, relpath.replace("/", os.sep))
            meta_path = os.path.join(abs_dir, f"{basename}.json")
            img["gen_date"] = derive_asset_date_key(relpath, now.isoformat())
            img["gen_filename"] = os.path.basename(img_path)
            img["gen_relpath"] = relpath
            with open(img_path, "wb") as fh:
                fh.write(base64.b64decode(png_b64))
            gen_meta = dict(params)
            gen_meta.update({
                "generated_at": now.isoformat(),
                "mime_type": png_mime,
                "gen_date": img["gen_date"],
                "gen_filename": img["gen_filename"],
                "gen_relpath": img["gen_relpath"],
                "deliveredWidth": delivered_width,
                "deliveredHeight": delivered_height,
                "deliveredImageSize": delivered_size,
                "filename": os.path.basename(img_path),
                "assetRelpath": relpath,
                "text": result.get("text", "") if g_idx == 0 else "",
            })
            if g_idx == 0:
                params["deliveredWidth"] = delivered_width
                params["deliveredHeight"] = delivered_height
                params["deliveredImageSize"] = delivered_size
                result["params"] = params
            with open(meta_path, "w", encoding="utf-8") as fh:
                json.dump(gen_meta, fh, indent=2, ensure_ascii=False)
        except Exception:
            pass
    save_config(config)
    return result


def persist_video_result(result: dict):
    params = result.get("params", {}) or {}
    raw_source_image = result.pop("_input_source_image", None)
    raw_source_video = result.pop("_input_source_video", None)
    raw_reference_images = result.pop("_input_reference_images", None)
    config = load_config()
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H%M%S%f")[:12]
    fallback_filename = sanitize_asset_filename_stem(
        params.get("prompt", "") or params.get("modelLabel", "") or "video",
        fallback=time_str,
    )
    asset_meta = normalize_asset_metadata(params, require_filename=True, fallback_filename=fallback_filename)
    params.update(asset_meta)
    update_asset_metadata_memory(config, asset_meta)

    if isinstance(raw_source_image, dict) and str(raw_source_image.get("data") or "").strip():
        archived_source = build_reference_archive_entries([raw_source_image], date_str, f"{time_str}_video_src")
        params["videoSourceArchive"] = archived_source[0] if archived_source else {}
    else:
        params["videoSourceArchive"] = params.get("videoSourceArchive", {}) or {}

    if isinstance(raw_reference_images, list):
        archived_refs = build_reference_archive_entries(raw_reference_images, date_str, f"{time_str}_video_ref")
        params["videoRefArchive"] = archived_refs
        params["videoRefCount"] = len(archived_refs)
    else:
        params["videoRefArchive"] = params.get("videoRefArchive", []) or []
        params["videoRefCount"] = int(params.get("videoRefCount", len(params["videoRefArchive"])))

    source_video_archive = params.get("videoSourceVideoArchive", {}) or {}
    if isinstance(raw_source_video, dict):
        raw_video_url = str(raw_source_video.get("url") or "").strip()
        raw_video_name = os.path.basename(str(raw_source_video.get("name") or "")) or "video-source.mp4"
        raw_video_mime = str(raw_source_video.get("mime_type") or "video/mp4").strip() or "video/mp4"
        local_source_path = resolve_local_video_url_to_path(raw_video_url)
        if local_source_path:
            source_video_archive = {
                "date": os.path.basename(os.path.dirname(local_source_path)),
                "filename": os.path.basename(local_source_path),
                "name": raw_video_name,
                "mime_type": raw_video_mime,
                "url": raw_video_url or f"/videos/{os.path.basename(os.path.dirname(local_source_path))}/{os.path.basename(local_source_path)}",
            }
        elif str(raw_source_video.get("data") or "").strip():
            ext = get_video_extension_for_payload(raw_video_mime, raw_video_name)
            source_meta = dict(asset_meta)
            source_meta["assetFilename"] = sanitize_asset_filename_stem(
                f"{asset_meta.get('assetFilename', fallback_filename)}_source",
                fallback=f"{time_str}_source",
            )
            try:
                archive_dir, archive_relpath, _archive_basename = build_asset_storage_paths(
                    VIDEOS_DIR,
                    source_meta,
                    ext,
                    variant_suffix="source",
                )
                archive_path = os.path.join(VIDEOS_DIR, archive_relpath.replace("/", os.sep))
                with open(archive_path, "wb") as fh:
                    fh.write(base64.b64decode(str(raw_source_video.get("data") or ""), validate=False))
                source_video_archive = {
                    "date": derive_asset_date_key(archive_relpath, now.isoformat()),
                    "filename": os.path.basename(archive_path),
                    "name": raw_video_name,
                    "mime_type": raw_video_mime,
                    "url": f"/videos/{archive_relpath}",
                    "relpath": archive_relpath,
                }
            except Exception:
                source_video_archive = {}
        elif raw_video_url:
            source_video_archive = {
                "date": "",
                "filename": raw_video_name,
                "name": raw_video_name,
                "mime_type": raw_video_mime,
                "url": raw_video_url,
            }
    params["videoSourceVideoArchive"] = source_video_archive

    result["params"] = params

    for idx, video in enumerate(result.get("videos", []) or []):
        video_url = str(video.get("url") or "").strip()
        if not video_url:
            continue
        try:
            raw_bytes, detected_mime = download_remote_binary(video_url, timeout=300)
            ext, mime_type = normalize_video_extension(video.get("mime_type") or detected_mime, video_url)
            abs_dir, relpath, basename = build_asset_storage_paths(
                VIDEOS_DIR,
                asset_meta,
                ext,
                variant_suffix=str(idx + 1) if len(result.get("videos", []) or []) > 1 else "",
            )
            video_path = os.path.join(VIDEOS_DIR, relpath.replace("/", os.sep))
            video_filename = os.path.basename(video_path)
            with open(video_path, "wb") as fh:
                fh.write(raw_bytes)

            poster_url = str(video.get("poster_url") or "").strip()
            poster_filename = ""
            if poster_url:
                try:
                    poster_raw, poster_mime = download_remote_binary(poster_url, timeout=120)
                    poster_b64 = base64.b64encode(poster_raw).decode("utf-8")
                    poster_png_b64, poster_png_mime = convert_image_b64_to_png(poster_b64, poster_mime or "image/png")
                    poster_filename = f"{basename}_poster.png"
                    poster_path = os.path.join(abs_dir, poster_filename)
                    with open(poster_path, "wb") as pfh:
                        pfh.write(base64.b64decode(poster_png_b64))
                    poster_relpath = os.path.join(os.path.dirname(relpath), poster_filename).replace("\\", "/")
                    video["poster_url"] = f"/videos/{poster_relpath}"
                    video["poster_mime_type"] = poster_png_mime
                except Exception:
                    poster_filename = ""

            video["gen_date"] = derive_asset_date_key(relpath, now.isoformat())
            video["gen_filename"] = video_filename
            video["gen_relpath"] = relpath
            video["url"] = f"/videos/{relpath}"
            video["mime_type"] = mime_type

            meta_path = os.path.join(abs_dir, f"{basename}.json")
            video_meta = dict(params)
            video_meta.update({
                "generated_at": now.isoformat(),
                "mime_type": mime_type,
                "filename": video_filename,
                "poster_filename": poster_filename,
                "poster_url": video.get("poster_url", ""),
                "assetRelpath": relpath,
                "text": result.get("text", "") if idx == 0 else "",
            })
            with open(meta_path, "w", encoding="utf-8") as fh:
                json.dump(video_meta, fh, indent=2, ensure_ascii=False)
        except Exception:
            continue
    save_config(config)
    return result


def _jwt_b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def mint_kling_jwt(access_key: str, secret_key: str, ttl_seconds: int = 1800) -> str:
    """
    Kling's API authenticates with a short-lived HS256 JWT signed by the account's
    secret key: {"iss": <access key>, "exp": now+30min, "nbf": now-5s}.
    """
    import hmac as _hmac
    now = int(time.time())
    header = _jwt_b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8"))
    claims = _jwt_b64url(json.dumps({"iss": access_key, "exp": now + int(ttl_seconds), "nbf": now - 5}, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header}.{claims}"
    signature = _hmac.new(secret_key.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_jwt_b64url(signature)}"


def resolve_kling_bearer(token_setting: str) -> str:
    """
    Accepts either a ready-made JWT or "ACCESS_KEY:SECRET_KEY" (also | or whitespace
    separated) and returns a bearer token that is valid right now. Minting per
    request means a long poll never outlives the token.
    """
    token = str(token_setting or "").strip()
    if not token:
        raise ValueError("Kling API token not configured. Go to Settings.")
    if token.count(".") == 2 and not any(sep in token for sep in (":", "|")):
        return token  # a pre-minted JWT
    for sep in (":", "|", None):
        parts = token.split(sep, 1) if sep else token.split(None, 1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            return mint_kling_jwt(parts[0].strip(), parts[1].strip())
    raise ValueError(
        "Kling credentials look wrong: enter ACCESS_KEY:SECRET_KEY from the Kling console "
        "(the app signs a fresh token for every request), or paste a complete JWT."
    )


def build_kling_headers(api_token: str) -> dict:
    return {
        "Authorization": f"Bearer {resolve_kling_bearer(api_token)}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


KLING_POLL_TIMEOUT_SECONDS = 2400  # Omni / v3 jobs on Kling's own queue can sit well past 15 minutes


def poll_kling_task(api_token: str, endpoint: str, task_id: str, *, timeout_seconds: int = KLING_POLL_TIMEOUT_SECONDS) -> dict:
    headers = build_kling_headers(api_token)
    deadline = time.monotonic() + max(60, int(timeout_seconds))
    transient_errors = 0
    while time.monotonic() < deadline:
        try:
            response = requests.get(f"{KLING_BASE_URL}{endpoint}/{task_id}", headers=headers, timeout=45)
        except requests.exceptions.RequestException as exc:
            # A dropped poll is not a failed job - the task is still running on Kling's side.
            transient_errors += 1
            if transient_errors > 6:
                raise RuntimeError(f"Lost contact with Kling while polling task {task_id}: {exc}") from exc
            threading.Event().wait(10)
            continue
        if response.status_code >= 500 or response.status_code == 429:
            transient_errors += 1
            if transient_errors > 6:
                raise RuntimeError(extract_kling_error(response))
            threading.Event().wait(10)
            continue
        if response.status_code != 200:
            raise RuntimeError(extract_kling_error(response))
        transient_errors = 0
        payload = response.json()
        task_status = str(payload.get("data", {}).get("task_status") or payload.get("task_status") or "").strip().lower()
        if task_status in {"succeed", "success"}:
            return payload
        if task_status in {"failed", "error"}:
            raise RuntimeError(
                str(
                    payload.get("data", {}).get("task_status_msg")
                    or payload.get("message")
                    or payload.get("msg")
                    or "Kling video generation failed."
                )
            )
        threading.Event().wait(5)
    raise TimeoutError(
        f"Timeout: Kling has not finished task {task_id} after {int(timeout_seconds // 60)} minutes. "
        "The job is still queued on Kling's side and will be billed if it completes; check your Kling console for the result."
    )


def build_kling_video_from_payload(result_payload: dict) -> dict:
    data = result_payload.get("data") if isinstance(result_payload, dict) else {}
    videos = []
    if isinstance(data, dict):
        task_result = data.get("task_result")
        if isinstance(task_result, dict) and isinstance(task_result.get("videos"), list):
            videos = [item for item in task_result.get("videos") if isinstance(item, dict)]
        elif isinstance(data.get("videos"), list):
            videos = [item for item in data.get("videos") if isinstance(item, dict)]
    if not videos:
        raise RuntimeError("Kling returned no video for this request.")
    first = videos[0]
    video_url = str(first.get("url") or "").strip()
    if not video_url:
        raise RuntimeError("Kling returned no video URL for this request.")
    return {
        "url": video_url,
        "mime_type": "video/mp4",
        "poster_url": str(first.get("cover_url") or first.get("poster_url") or first.get("thumbnail_url") or "").strip(),
        "width": int(first.get("width") or 0),
        "height": int(first.get("height") or 0),
    }


def run_native_kling_video_job(body: dict, api_token: str) -> dict:
    body = normalize_video_request(body)
    input_mode = body.get("videoInputMode", "text")
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("Please enter a video prompt.")

    selected_model_id = str(body.get("model") or KLING_DIRECT_TEXT_DEFAULT_ID).strip() or KLING_DIRECT_TEXT_DEFAULT_ID
    model_info = VIDEO_MODELS_INFO.get(selected_model_id, {})
    model_name = str(model_info.get("native_model_name") or selected_model_id or KLING_DIRECT_TEXT_DEFAULT_ID).strip() or KLING_DIRECT_TEXT_DEFAULT_ID
    duration = normalize_video_duration(body.get("duration", 5))
    aspect_ratio = body.get("aspectRatio", "16:9")
    negative_prompt = str(body.get("negativePrompt") or "").strip()
    source_image = body.get("sourceImage") or {}
    reference_images = list(body.get("referenceImages") or [])
    kling_mode = str(model_info.get("kling_mode") or "pro").strip().lower() or "pro"
    endpoint = str(model_info.get("native_video_endpoint") or "").strip()
    if not endpoint:
        endpoint = "/v1/videos/text2video" if input_mode == "text" else "/v1/videos/image2video"
    native_ref_item_key = str(model_info.get("native_reference_item_key") or "image").strip() or "image"

    def build_native_ref_item(image_payload: dict, item_type: str = "") -> dict:
        raw_value = str(image_payload.get("data") or "").strip()
        item = {native_ref_item_key: raw_value}
        if item_type:
            item["type"] = item_type
        return item

    payload = {
        "model_name": model_name,
        "prompt": prompt,
        "duration": str(duration),
        "mode": kling_mode,
        "sound": "off",
        "aspect_ratio": aspect_ratio,
    }
    if negative_prompt:
        payload["negative_prompt"] = negative_prompt
    if input_mode == "image":
        if not source_image.get("data"):
            raise ValueError("Choose a source image for image-to-video.")
        if endpoint == "/v1/videos/omni-video":
            payload["image_list"] = [build_native_ref_item(source_image, "first_frame")]
        else:
            payload["image"] = str(source_image.get("data") or "").strip()
    elif input_mode == "reference":
        reference_payloads = [
            build_native_ref_item(img)
            for img in reference_images
            if str(img.get("data") or "").strip()
        ]
        if endpoint == "/v1/videos/omni-video" and source_image.get("data"):
            reference_payloads.insert(0, build_native_ref_item(source_image, "first_frame"))
        if model_name == "kling-video-o1" and source_image.get("data") and len(reference_payloads) > 2:
            raise ValueError("Kling O1 supports at most one extra reference image when a start image is also used.")
        if model_info.get("reference_images_required") and not reference_payloads:
            raise ValueError("Add at least one reference image for this Kling reference-to-video model.")
        if reference_payloads:
            payload["image_list"] = reference_payloads
    headers = build_kling_headers(api_token)
    try:
        create_response = requests.post(f"{KLING_BASE_URL}{endpoint}", headers=headers, json=payload, timeout=90)
    except requests.exceptions.Timeout as exc:
        raise TimeoutError("Timeout: Kling request took too long to start.") from exc
    except Exception as exc:
        raise RuntimeError(f"Network error: {exc}") from exc
    if create_response.status_code != 200:
        raise RuntimeError(extract_kling_error(create_response))
    create_payload = create_response.json()
    task_id = str(create_payload.get("data", {}).get("task_id") or create_payload.get("task_id") or "").strip()
    if not task_id:
        raise RuntimeError("Kling did not return a task id.")
    result_payload = poll_kling_task(api_token, endpoint, task_id)
    video_item = build_kling_video_from_payload(result_payload)
    params_meta = merge_request_settings(merge_asset_metadata({
        "model": selected_model_id,
        "modelFamily": body.get("modelFamily", "kling"),
        "model_label": model_info.get("label", "Kling"),
        "provider": "kling",
        "provider_label": "Kling",
        "videoInputMode": input_mode,
        "duration": duration,
        "aspectRatio": aspect_ratio,
        "negativePrompt": negative_prompt,
        "prompt": prompt,
        "resolution": body.get("resolution", "720p"),
    }, body), body)
    return {
        "ok": True,
        "videos": [video_item],
        "text": "",
        "cost": round(float(VIDEO_PRICING.get(selected_model_id, {}).get(str(duration), 0.0)), 4),
        "model_label": model_info.get("label", "Kling"),
        "params": params_meta,
        "_input_source_image": source_image if str(source_image.get("data") or "").strip() else None,
        "_input_reference_images": reference_images,
    }


def run_fal_kling_video_job(body: dict, api_key: str) -> dict:
    body = normalize_video_request(body)
    input_mode = body.get("videoInputMode", "text")
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("Please enter a video prompt.")
    duration = normalize_video_duration(body.get("duration", 5))
    aspect_ratio = body.get("aspectRatio", "16:9")
    negative_prompt = str(body.get("negativePrompt") or "").strip()
    generate_audio = bool(body.get("videoGenerateAudio", False))
    source_image = body.get("sourceImage") or {}
    source_video = body.get("sourceVideo") or {}
    reference_images = list(body.get("referenceImages") or [])
    endpoint = str(body.get("model") or "").strip()
    model_info = VIDEO_MODELS_INFO.get(endpoint, {})
    if not endpoint or model_info.get("provider") != "fal" or model_info.get("family") != "kling":
        raise ValueError("Choose a valid Fal Kling model.")
    max_reference_images = max(0, int(model_info.get("max_reference_images", 0) or 0))
    if not model_info.get("supports_reference_images"):
        reference_images = []
    elif max_reference_images:
        reference_images = reference_images[:max_reference_images]
    payload = {
        "prompt": prompt,
        "duration": str(duration),
        "aspect_ratio": aspect_ratio,
        "sync_mode": True,
    }
    if negative_prompt:
        payload["negative_prompt"] = negative_prompt
    if model_info.get("supports_generate_audio"):
        payload["generate_audio"] = generate_audio
    if input_mode != "text" and model_info.get("supports_start_image"):
        if model_info.get("start_image_required") and not source_image.get("data"):
            raise ValueError("Choose a start image for this Kling video model.")
        if source_image.get("data"):
            start_field = str(model_info.get("start_image_field") or "image_url")
            payload[start_field] = f"data:{source_image.get('mime_type', 'image/png')};base64,{source_image.get('data', '')}"
    if input_mode == "video" and model_info.get("supports_source_video"):
        if model_info.get("source_video_required") and not str(source_video.get("data") or source_video.get("url") or "").strip():
            raise ValueError("Choose a source video for this Kling video-to-video model.")
        if str(source_video.get("data") or source_video.get("url") or "").strip():
            client = fal_client.SyncClient(key=api_key)
            source_field = str(model_info.get("source_video_field") or "video_url")
            payload[source_field] = upload_video_payload_to_fal(client, source_video)
    if input_mode == "reference" and model_info.get("supports_reference_images"):
        if model_info.get("reference_images_required") and not reference_images:
            raise ValueError("Add at least one reference image for this Kling reference-to-video model.")
        if reference_images:
            ref_field = str(model_info.get("reference_images_field") or "image_urls")
            reference_urls = [
                f"data:{img.get('mime_type', 'image/png')};base64,{img.get('data', '')}"
                for img in reference_images
                if str(img.get("data") or "").strip()
            ]
            if model_info.get("reference_images_required") and not reference_urls:
                raise ValueError("Add at least one valid reference image for this Kling reference-to-video model.")
            if reference_urls:
                payload[ref_field] = reference_urls
    headers = {
        "Authorization": f"Key {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        response = requests.post(f"{FAL_BASE_URL}/{endpoint}", headers=headers, json=payload, timeout=600)
    except requests.exceptions.Timeout as exc:
        raise TimeoutError("Timeout: Fal Kling generation took too long.") from exc
    except Exception as exc:
        raise RuntimeError(f"Network error: {exc}") from exc
    if response.status_code != 200:
        raise RuntimeError(extract_fal_error(response))
    result = response.json()
    video_item = extract_fal_video_result(result)
    if not video_item:
        raise RuntimeError("Fal Kling returned no video for this request.")
    params_meta = merge_request_settings(merge_asset_metadata({
        "model": endpoint,
        "modelFamily": body.get("modelFamily", "kling"),
        "model_label": model_info.get("label", "Kling"),
        "provider": "fal",
        "provider_label": "Fal",
        "videoInputMode": input_mode,
        "duration": duration,
        "aspectRatio": aspect_ratio,
        "negativePrompt": negative_prompt,
        "prompt": prompt,
        "resolution": body.get("resolution", "720p"),
        "videoGenerateAudio": generate_audio if model_info.get("supports_generate_audio") else None,
    }, body), body)
    return {
        "ok": True,
        "videos": [video_item],
        "text": "",
        "cost": round(float(VIDEO_PRICING.get(endpoint, {}).get(str(duration), 0.0)), 4),
        "model_label": model_info.get("label", "Kling"),
        "params": params_meta,
        "_input_source_image": source_image if str(source_image.get("data") or "").strip() else None,
        "_input_source_video": source_video if str(source_video.get("data") or source_video.get("url") or "").strip() else None,
        "_input_reference_images": reference_images,
    }


def run_luma_video_job(body: dict, api_key: str, fal_api_key: str = "") -> dict:
    body = normalize_video_request(body)
    input_mode = body.get("videoInputMode", "text")
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("Please enter a video prompt.")

    selected_model_id = str(body.get("model") or LUMA_RAY2_T2V_ID).strip() or LUMA_RAY2_T2V_ID
    model_info = VIDEO_MODELS_INFO.get(selected_model_id, {})
    if model_info.get("provider") != "luma":
        raise ValueError("Choose a valid Luma video model.")

    native_model_name = str(model_info.get("native_model_name") or "ray-2").strip() or "ray-2"
    duration = normalize_video_duration(body.get("duration", 5))
    aspect_ratio = str(body.get("aspectRatio") or "16:9").strip() or "16:9"
    resolution = str(body.get("resolution") or "720p").strip().lower() or "720p"
    source_image = body.get("sourceImage") or {}

    payload = {
        "prompt": prompt,
        "model": native_model_name,
        "aspect_ratio": aspect_ratio,
        "duration": f"{duration}s",
        "resolution": resolution,
    }
    if input_mode == "image":
        if not str(source_image.get("data") or source_image.get("original_url") or source_image.get("masked_url") or "").strip():
            raise ValueError("Choose a source image for image-to-video.")
        public_image_url = ""
        candidate_urls = [
            str(source_image.get("url") or "").strip(),
            str(source_image.get("masked_url") or "").strip(),
            str(source_image.get("original_url") or "").strip(),
        ]
        for candidate_url in candidate_urls:
            if candidate_url.startswith("http://") or candidate_url.startswith("https://"):
                public_image_url = candidate_url
                break
        if not public_image_url:
            if not fal_api_key:
                raise ValueError("Luma image-to-video needs a public source image URL. Add a Fal API key in Settings so Studio can upload the image temporarily.")
            try:
                client = fal_client.SyncClient(key=fal_api_key)
                public_image_url = upload_image_payload_to_fal(client, source_image)
            except Exception as exc:
                raise RuntimeError(str(exc)) from exc
        payload["keyframes"] = {
            "frame0": {
                "type": "image",
                "url": public_image_url,
            }
        }

    headers = build_luma_headers(api_key)
    try:
        create_response = requests.post(LUMA_GENERATIONS_URL, headers=headers, json=payload, timeout=120)
    except requests.exceptions.Timeout as exc:
        raise TimeoutError("Timeout: Luma request took too long to start.") from exc
    except Exception as exc:
        raise RuntimeError(f"Network error: {exc}") from exc
    if create_response.status_code not in {200, 201}:
        raise RuntimeError(extract_luma_error(create_response))

    created_payload = create_response.json() if create_response.content else {}
    generation_id = str(created_payload.get("id") or created_payload.get("generation_id") or "").strip()
    if not generation_id:
        raise RuntimeError("Luma did not return a generation id.")

    result_payload = poll_luma_generation(api_key, generation_id)
    assets = result_payload.get("assets") or {}
    raw_video_asset = assets.get("video")
    video_url = ""
    if isinstance(raw_video_asset, dict):
        video_url = str(raw_video_asset.get("url") or raw_video_asset.get("video_url") or "").strip()
    else:
        video_url = str(raw_video_asset or assets.get("video_url") or "").strip()
    if not video_url:
        raise RuntimeError("Luma returned no video for this request.")

    video_item = {
        "url": video_url,
        "mime_type": "video/mp4",
        "poster_url": str(assets.get("image") or assets.get("thumbnail") or assets.get("preview_image") or "").strip(),
        "width": 0,
        "height": 0,
    }
    cost = estimate_luma_video_cost(native_model_name, resolution, duration, aspect_ratio)
    params_meta = merge_request_settings(merge_asset_metadata({
        "model": selected_model_id,
        "modelFamily": body.get("modelFamily", "luma-video"),
        "model_label": model_info.get("label", "Luma"),
        "provider": "luma",
        "provider_label": "Luma",
        "videoInputMode": input_mode,
        "duration": duration,
        "aspectRatio": aspect_ratio,
        "prompt": prompt,
        "resolution": resolution,
        "lumaGenerationId": generation_id,
    }, body), body)
    return {
        "ok": True,
        "videos": [video_item],
        "text": "",
        "cost": cost,
        "model_label": model_info.get("label", "Luma"),
        "params": params_meta,
        "_input_source_image": source_image if str(source_image.get("data") or "").strip() else None,
        "_input_reference_images": [],
    }


def run_fal_wan_video_job(body: dict, api_key: str) -> dict:
    body = normalize_video_request(body)
    input_mode = body.get("videoInputMode", "text")
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("Please enter a video prompt.")
    selected_model_id = str(body.get("model") or FAL_WAN_T2V_ID).strip() or FAL_WAN_T2V_ID
    model_info = VIDEO_MODELS_INFO.get(selected_model_id, {})
    if not model_info or model_info.get("provider") != "fal" or model_info.get("family") != "wan-video":
        raise ValueError("Choose a valid Fal Wan model.")
    endpoint = str(model_info.get("fal_endpoint") or "").strip()
    if not endpoint:
        raise ValueError("Wan endpoint is not configured for this model.")
    duration = normalize_video_duration(body.get("duration", 5))
    aspect_ratio = body.get("aspectRatio", "16:9")
    negative_prompt = str(body.get("negativePrompt") or "").strip()
    resolution = normalize_video_resolution(body.get("resolution", "720p"))
    safety_checker = bool(body.get("videoSafetyChecker", True))
    output_safety_checker = bool(body.get("videoOutputSafetyChecker", True))
    source_image = body.get("sourceImage") or {}
    source_video = body.get("sourceVideo") or {}
    source_audio = body.get("sourceAudio") or {}
    reference_images = list(body.get("referenceImages") or [])
    reference_videos = list(body.get("referenceVideos") or [])
    multi_shots = bool(body.get("videoWanMultiShots", False))
    payload = {
        "prompt": prompt,
        "duration": duration,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "enable_safety_checker": safety_checker,
        "enable_prompt_expansion": bool(body.get("videoPromptExpansion", True)),
        "sync_mode": True,
    }
    if negative_prompt:
        payload["negative_prompt"] = negative_prompt
    if model_info.get("supports_output_safety_checker"):
        payload["enable_output_safety_checker"] = output_safety_checker
    if model_info.get("supports_driving_audio"):
        audio_url = str(source_audio.get("url") or "").strip()
        if audio_url.startswith("http://") or audio_url.startswith("https://"):
            payload["audio_url"] = audio_url
        elif str(source_audio.get("data") or "").strip():
            payload["audio_url"] = f"data:{source_audio.get('mime_type', 'audio/mpeg')};base64,{source_audio.get('data', '')}"
    if input_mode == "image":
        if not source_image.get("data"):
            raise ValueError("Choose a source image for image-to-video.")
        payload["image_url"] = f"data:{source_image.get('mime_type', 'image/png')};base64,{source_image.get('data', '')}"
    elif input_mode == "reference":
        if model_info.get("video_mode_kind") == "first_last_frame_to_video":
            if not source_image.get("data"):
                raise ValueError("Choose a start image for this Wan first+last model.")
            end_image = next((img for img in reference_images if str(img.get("data") or "").strip()), {})
            if not end_image.get("data"):
                raise ValueError("Add one end frame image for this Wan first+last model.")
            payload["image_url"] = f"data:{source_image.get('mime_type', 'image/png')};base64,{source_image.get('data', '')}"
            payload["end_image_url"] = f"data:{end_image.get('mime_type', 'image/png')};base64,{end_image.get('data', '')}"
        else:
            reference_urls = [
                f"data:{img.get('mime_type', 'image/png')};base64,{img.get('data', '')}"
                for img in reference_images
                if str(img.get("data") or "").strip()
            ]
            if model_info.get("reference_images_required") and not reference_urls:
                raise ValueError("Add at least one reference image for this Wan reference model.")
            if reference_urls:
                payload["reference_image_urls"] = reference_urls
            if model_info.get("supports_reference_videos"):
                client = fal_client.SyncClient(key=api_key, default_timeout=1800.0)
                reference_video_urls = []
                for video_item in reference_videos:
                    if not str(video_item.get("data") or video_item.get("url") or "").strip():
                        continue
                    reference_video_urls.append(upload_video_payload_to_fal(client, video_item))
                if reference_video_urls:
                    payload["reference_video_urls"] = reference_video_urls
            if model_info.get("supports_multi_shots"):
                payload["multi_shots"] = multi_shots
    elif input_mode == "video":
        if not str(source_video.get("data") or source_video.get("url") or "").strip():
            raise ValueError("Choose a source video for this Wan model.")
        client = fal_client.SyncClient(key=api_key, default_timeout=1800.0)
        payload["video_url"] = upload_video_payload_to_fal(client, source_video)
    headers = {
        "Authorization": f"Key {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        response = requests.post(f"{FAL_BASE_URL}/{endpoint}", headers=headers, json=payload, timeout=900)
    except requests.exceptions.Timeout as exc:
        raise TimeoutError("Timeout: Fal Wan generation took too long.") from exc
    except Exception as exc:
        raise RuntimeError(f"Network error: {exc}") from exc
    if response.status_code != 200:
        raise RuntimeError(extract_fal_error(response))
    result = response.json()
    video_item = extract_fal_video_result(result)
    if not video_item:
        raise RuntimeError("Fal Wan returned no video for this request.")
    params_meta = merge_request_settings(merge_asset_metadata({
        "model": selected_model_id,
        "modelFamily": body.get("modelFamily", "wan-video"),
        "model_label": model_info.get("label", "Wan 2.7"),
        "provider": "fal",
        "provider_label": "Fal",
        "videoInputMode": input_mode,
        "duration": duration,
        "aspectRatio": aspect_ratio,
        "negativePrompt": negative_prompt,
        "prompt": prompt,
        "resolution": resolution,
        "videoSafetyChecker": safety_checker,
        "videoOutputSafetyChecker": output_safety_checker if model_info.get("supports_output_safety_checker") else None,
        "videoWanMultiShots": multi_shots if model_info.get("supports_multi_shots") else None,
        "videoWanHasDrivingAudio": bool(payload.get("audio_url")),
        "videoWanReferenceVideoCount": len(payload.get("reference_video_urls") or []),
    }, body), body)
    return {
        "ok": True,
        "videos": [video_item],
        "text": "",
        "cost": round(float(VIDEO_PRICING.get(selected_model_id, {}).get(str(duration), 0.0)), 4),
        "model_label": model_info.get("label", "Wan 2.7"),
        "params": params_meta,
        "_input_source_image": source_image if str(source_image.get("data") or "").strip() else None,
        "_input_source_video": source_video if str(source_video.get("data") or source_video.get("url") or "").strip() else None,
        "_input_reference_images": reference_images,
    }


def run_fal_seedance_video_job(body: dict, api_key: str) -> dict:
    body = normalize_video_request(body)
    input_mode = body.get("videoInputMode", "text")
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("Please enter a video prompt.")
    duration = normalize_video_duration(body.get("duration", 5))
    aspect_ratio = body.get("aspectRatio", "16:9")
    negative_prompt = str(body.get("negativePrompt") or "").strip()
    resolution = normalize_video_resolution(body.get("resolution", "720p"))
    safety_checker = bool(body.get("videoSafetyChecker", True))
    generate_audio = bool(body.get("videoGenerateAudio", False))
    source_image = body.get("sourceImage") or {}
    reference_images = list(body.get("referenceImages") or [])
    endpoint = str(body.get("model") or "").strip()
    model_info = VIDEO_MODELS_INFO.get(endpoint, {})
    if not endpoint or model_info.get("provider") != "fal" or model_info.get("family") != "seedance":
        raise ValueError("Choose a valid Fal Seedance model.")
    max_reference_images = max(0, int(model_info.get("max_reference_images", 0) or 0))
    if not model_info.get("supports_reference_images"):
        reference_images = []
    elif max_reference_images:
        reference_images = reference_images[:max_reference_images]
    payload = {
        "prompt": prompt,
        "duration": duration,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "sync_mode": True,
    }
    if model_info.get("supports_safety_checker"):
        payload["enable_safety_checker"] = safety_checker
    if model_info.get("supports_generate_audio"):
        payload["generate_audio"] = generate_audio
    if negative_prompt:
        payload["negative_prompt"] = negative_prompt
    if input_mode != "text" and model_info.get("supports_start_image"):
        if model_info.get("start_image_required") and not source_image.get("data"):
            raise ValueError("Choose a start image for this Seedance video model.")
        if source_image.get("data"):
            start_field = str(model_info.get("start_image_field") or "image_url")
            payload[start_field] = f"data:{source_image.get('mime_type', 'image/png')};base64,{source_image.get('data', '')}"
    if input_mode == "reference" and model_info.get("supports_reference_images"):
        if model_info.get("reference_images_required") and not reference_images:
            raise ValueError("Add at least one reference image for this Seedance reference-to-video model.")
        if reference_images:
            ref_field = str(model_info.get("reference_images_field") or "reference_image_urls")
            reference_urls = [
                f"data:{img.get('mime_type', 'image/png')};base64,{img.get('data', '')}"
                for img in reference_images
                if str(img.get("data") or "").strip()
            ]
            if model_info.get("reference_images_required") and not reference_urls:
                raise ValueError("Add at least one valid reference image for this Seedance reference-to-video model.")
            if reference_urls:
                payload[ref_field] = reference_urls
    headers = {
        "Authorization": f"Key {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        response = requests.post(f"{FAL_BASE_URL}/{endpoint}", headers=headers, json=payload, timeout=900)
    except requests.exceptions.Timeout as exc:
        raise TimeoutError("Timeout: Fal Seedance generation took too long.") from exc
    except Exception as exc:
        raise RuntimeError(f"Network error: {exc}") from exc
    if response.status_code != 200:
        raise RuntimeError(extract_fal_error(response))
    result = response.json()
    video_item = extract_fal_video_result(result)
    if not video_item:
        raise RuntimeError("Fal Seedance returned no video for this request.")
    params_meta = merge_request_settings(merge_asset_metadata({
        "model": endpoint,
        "modelFamily": body.get("modelFamily", "seedance"),
        "model_label": model_info.get("label", "Seedance"),
        "provider": "fal",
        "provider_label": "Fal",
        "videoInputMode": input_mode,
        "duration": duration,
        "aspectRatio": aspect_ratio,
        "negativePrompt": negative_prompt,
        "prompt": prompt,
        "resolution": resolution,
        "videoSafetyChecker": safety_checker,
        "videoGenerateAudio": generate_audio,
    }, body), body)
    return {
        "ok": True,
        "videos": [video_item],
        "text": "",
        "cost": round(float(VIDEO_PRICING.get(endpoint, {}).get(str(duration), 0.0)), 4),
        "model_label": model_info.get("label", "Seedance"),
        "params": params_meta,
        "_input_source_image": source_image if str(source_image.get("data") or "").strip() else None,
        "_input_reference_images": reference_images,
    }


_FAL_ERROR_TYPE_HINTS = {
    "content_policy_violation": "fal's content moderation rejected this input - it usually means a realistic human likeness, a minor, or restricted content in the image or prompt. Try a different image (stylized, cropped, or generated) or another model.",
    "value_error": "the value was not accepted by the model's schema.",
    "missing": "a required field was not sent.",
}


def format_fal_client_error(exc: Exception) -> str:
    """
    fal_client raises with the validation payload stringified as a Python repr:
        [{'loc': ['body', 'image_url'], 'msg': '...', 'type': 'content_policy_violation', 'input': {...}}]
    Turn that into "image_url: <msg> (content_policy_violation)" and drop the echoed input.
    """
    detail = None
    for candidate in (getattr(exc, "detail", None), *(getattr(exc, "args", None) or [])):
        if isinstance(candidate, (list, dict)):
            detail = candidate
            break
    if detail is None:
        text = str(exc).strip()
        if text.startswith(("[", "{")):
            try:
                import ast as _ast
                detail = _ast.literal_eval(text)
            except Exception:
                detail = None
        if detail is None:
            return text or "request failed"
    items = detail if isinstance(detail, list) else [detail]
    parts = []
    hints = []
    for item in items:
        if not isinstance(item, dict):
            parts.append(str(item))
            continue
        loc = [str(x) for x in (item.get("loc") or []) if str(x) != "body"]
        msg = str(item.get("msg") or item.get("message") or item.get("detail") or "").strip()
        kind = str(item.get("type") or "").strip()
        part = f"{'.'.join(loc)}: {msg}" if loc and msg else (msg or ".".join(loc) or "validation error")
        if kind:
            part += f" ({kind})"
        parts.append(part)
        if kind in _FAL_ERROR_TYPE_HINTS and _FAL_ERROR_TYPE_HINTS[kind] not in hints:
            hints.append(_FAL_ERROR_TYPE_HINTS[kind])
    text = "; ".join(parts) or "request failed"
    if hints:
        text += " - " + " ".join(hints)
    return text


def format_fal_schema_duration(duration: int, model_info: dict):
    """Match the type fal's schema declares: 5, "5" or "5s"."""
    fmt = str(model_info.get("duration_format") or "string")
    if fmt == "int":
        return int(duration)
    if fmt == "seconds_suffix":
        return f"{int(duration)}s"
    return str(int(duration))


def _fal_schema_has(model_info: dict, field: str) -> bool:
    fields = model_info.get("schema_fields") or []
    return field in fields if fields else True


def upload_audio_payload_to_fal(client: fal_client.SyncClient, audio_payload: dict) -> str:
    if not isinstance(audio_payload, dict):
        raise ValueError("Choose an audio file for this model.")
    direct_url = str(audio_payload.get("url") or "").strip()
    if direct_url.startswith("http://") or direct_url.startswith("https://"):
        return direct_url
    data_b64 = str(audio_payload.get("data") or "").strip()
    if not data_b64:
        raise ValueError("Choose an audio file for this model.")
    mime_type = str(audio_payload.get("mime_type") or "audio/mpeg").split(";", 1)[0].strip() or "audio/mpeg"
    name = os.path.basename(str(audio_payload.get("name") or "audio.mp3")) or "audio.mp3"
    try:
        return str(client.upload(base64.b64decode(data_b64, validate=False), mime_type, file_name=name))
    except Exception as exc:
        raise RuntimeError(f"Could not upload the audio file: {exc}") from exc


def run_fal_schema_video_job(body: dict, api_key: str) -> dict:
    """
    Generic fal video runner for catalog-sourced models. The payload is built
    from the fetched schema: only fields the endpoint declares are sent, with
    the field names and value types the schema uses. Runs through fal's queue.
    """
    body = normalize_video_request(body)
    input_mode = body.get("videoInputMode", "text")
    model_id = str(body.get("model") or "").strip()
    model_info = VIDEO_MODELS_INFO.get(model_id, {})
    if not model_id or model_info.get("provider") != "fal":
        raise ValueError("Choose a valid Fal video model.")
    endpoint = str(model_info.get("fal_endpoint") or model_id).strip()
    has = lambda field: _fal_schema_has(model_info, field)  # noqa: E731

    prompt = str(body.get("prompt") or "").strip()
    negative_prompt = str(body.get("negativePrompt") or "").strip()
    duration = normalize_video_duration(body.get("duration", 5))
    aspect_ratio = str(body.get("aspectRatio") or "16:9")
    resolution = str(body.get("resolution") or "720p")
    generate_audio = bool(body.get("videoGenerateAudio", False))
    safety_checker = bool(body.get("videoSafetyChecker", True))
    source_image = body.get("sourceImage") or {}
    end_image = body.get("endImage") or {}
    source_video = body.get("sourceVideo") or {}
    source_audio = body.get("sourceAudio") or {}
    reference_images = list(body.get("referenceImages") or [])
    reference_videos = list(body.get("referenceVideos") or [])
    multi_prompt_shots = parse_multi_prompt_shots(body.get("videoMultiPrompt"), duration) if has("multi_prompt") else []

    client = fal_client.SyncClient(key=api_key)
    payload: dict = {}

    if multi_prompt_shots:
        payload["multi_prompt"] = multi_prompt_shots  # fal: prompt OR multi_prompt, never both
    elif has("prompt"):
        if not prompt and model_info.get("prompt_required", True):
            raise ValueError("Please enter a video prompt.")
        if prompt:
            payload["prompt"] = prompt
    if has("negative_prompt") and negative_prompt:
        payload["negative_prompt"] = negative_prompt
    if has("duration") and model_info.get("supports_duration", True):
        payload["duration"] = format_fal_schema_duration(duration, model_info)
    if has("aspect_ratio") and model_info.get("supports_aspect_ratio", True):
        payload["aspect_ratio"] = aspect_ratio
    if has("resolution") and model_info.get("supports_resolution"):
        payload["resolution"] = resolution
    if has("generate_audio") and model_info.get("supports_generate_audio"):
        payload["generate_audio"] = generate_audio
    if has("enable_safety_checker") and model_info.get("supports_safety_checker"):
        payload["enable_safety_checker"] = safety_checker
    if has("enable_prompt_expansion"):
        payload["enable_prompt_expansion"] = bool(body.get("videoPromptExpansion", True))
    if has("cfg_scale") and body.get("videoCfgScale") is not None:
        payload["cfg_scale"] = float(body["videoCfgScale"])
    if has("seed") and body.get("videoSeed") is not None:
        payload["seed"] = int(body["videoSeed"])
    if has("shot_type") and body.get("videoShotType"):
        payload["shot_type"] = str(body["videoShotType"])

    elements_field = str(model_info.get("elements_field") or "")
    if elements_field and has(elements_field) and model_info.get("supports_elements"):
        schema = model_info.get("element_schema") or {}
        frontal_field = str(schema.get("frontal_field") or "frontal_image_url")
        refs_field = str(schema.get("references_field") or "reference_image_urls")
        video_field = str(schema.get("video_field") or "video_url")
        voice_field = str(schema.get("voice_field") or "voice_id")
        refs_min = max(0, int(schema.get("references_min", 1) or 0))
        built = []
        for index, element in enumerate(body.get("videoElements") or [], start=1):
            label = element.get("name") or f"Element {index}"
            item: dict = {}
            video = element.get("video") or {}
            if str(video.get("data") or video.get("url") or "").strip():
                item[video_field] = upload_video_payload_to_fal(client, video)
            else:
                frontal = element.get("frontal") or {}
                refs = [r for r in (element.get("references") or []) if str(r.get("data") or r.get("url") or "").strip()]
                if not str(frontal.get("data") or frontal.get("url") or "").strip():
                    raise ValueError(f"{label}: add a frontal image (or a video) for this element.")
                if len(refs) < refs_min:
                    raise ValueError(f"{label}: add at least {refs_min} angle reference image{'s' if refs_min != 1 else ''}.")
                item[frontal_field] = upload_image_payload_to_fal(client, frontal)
                if refs:
                    item[refs_field] = [upload_image_payload_to_fal(client, r) for r in refs]
            if element.get("voice_id"):
                item[voice_field] = element["voice_id"]
            built.append(item)
        if built:
            payload[elements_field] = built

    start_field = str(model_info.get("start_image_field") or "image_url")
    end_field = str(model_info.get("end_image_field") or "")
    ref_field = str(model_info.get("reference_images_field") or "image_urls")

    if input_mode != "text" and model_info.get("supports_start_image"):
        if str(source_image.get("data") or source_image.get("url") or "").strip():
            payload[start_field] = upload_image_payload_to_fal(client, source_image)
        elif model_info.get("start_image_required"):
            raise ValueError(f"Choose a start image for {model_info.get('label', 'this model')}.")

    if end_field and model_info.get("supports_end_image") and ref_field != end_field:
        if str(end_image.get("data") or end_image.get("url") or "").strip():
            payload[end_field] = upload_image_payload_to_fal(client, end_image)

    if input_mode == "reference" and model_info.get("supports_reference_images"):
        max_refs = max(0, int(model_info.get("max_reference_images", 0) or 0))
        if max_refs:
            reference_images = reference_images[:max_refs]
        usable = [img for img in reference_images if str(img.get("data") or img.get("url") or "").strip()]
        if ref_field == end_field:
            # Single end-frame slot exposed through the reference tray (Wan First+Last style).
            if usable:
                payload[end_field] = upload_image_payload_to_fal(client, usable[0])
        elif usable:
            payload[ref_field] = [upload_image_payload_to_fal(client, img) for img in usable]
        if not usable and model_info.get("reference_images_required"):
            raise ValueError(f"Add at least one reference image for {model_info.get('label', 'this model')}.")

    ref_video_field = str(model_info.get("reference_videos_field") or "")
    if input_mode == "reference" and ref_video_field and model_info.get("supports_reference_videos") and reference_videos:
        max_ref_videos = max(0, int(model_info.get("max_reference_videos", 0) or 0))
        if max_ref_videos:
            reference_videos = reference_videos[:max_ref_videos]
        urls = [upload_video_payload_to_fal(client, item) for item in reference_videos]
        if urls:
            payload[ref_video_field] = urls

    if input_mode == "video" and model_info.get("supports_source_video"):
        source_field = str(model_info.get("source_video_field") or "video_url")
        if str(source_video.get("data") or source_video.get("url") or "").strip():
            payload[source_field] = upload_video_payload_to_fal(client, source_video)
        elif model_info.get("source_video_required"):
            raise ValueError(f"Choose a source video for {model_info.get('label', 'this model')}.")

    audio_field = str(model_info.get("driving_audio_field") or "")
    if audio_field and model_info.get("supports_driving_audio") and str(source_audio.get("data") or source_audio.get("url") or "").strip():
        payload[audio_field] = upload_audio_payload_to_fal(client, source_audio)

    try:
        result = client.subscribe(endpoint, arguments=payload, with_logs=False)
    except Exception as exc:
        raise RuntimeError(f"Fal {model_info.get('label', endpoint)}: {format_fal_client_error(exc)}") from exc
    video_item = extract_fal_video_result(result if isinstance(result, dict) else {})
    if not video_item:
        raise RuntimeError(f"Fal {model_info.get('label', endpoint)} returned no video for this request.")

    params_meta = merge_request_settings(merge_asset_metadata({
        "model": model_id,
        "modelFamily": body.get("modelFamily", model_info.get("family", "")),
        "model_label": model_info.get("label", model_id),
        "provider": "fal",
        "provider_label": "Fal",
        "fal_endpoint": endpoint,
        "videoInputMode": input_mode,
        "duration": duration,
        "aspectRatio": aspect_ratio if "aspect_ratio" in payload else None,
        "negativePrompt": negative_prompt,
        "prompt": prompt,
        "videoMultiPrompt": body.get("videoMultiPrompt") or "",
        "resolution": resolution if "resolution" in payload else None,
        "videoSafetyChecker": safety_checker if "enable_safety_checker" in payload else None,
        "videoGenerateAudio": generate_audio if "generate_audio" in payload else None,
        "videoCfgScale": payload.get("cfg_scale"),
        "videoSeed": payload.get("seed"),
        "videoHasEndImage": bool(end_field and end_field in payload),
        "videoShotType": payload.get("shot_type"),
        "videoElementCount": len(payload.get(str(model_info.get("elements_field") or "elements"), []) or []),
        "ref_count": len(payload.get(ref_field, []) if isinstance(payload.get(ref_field), list) else []),
    }, body), body)
    return {
        "ok": True,
        "videos": [video_item],
        "text": "",
        "cost": round(float(VIDEO_PRICING.get(model_id, {}).get(str(duration), 0.0)), 4),
        "model_label": model_info.get("label", model_id),
        "params": params_meta,
        "_input_source_image": source_image if str(source_image.get("data") or "").strip() else None,
        "_input_source_video": source_video if str(source_video.get("data") or source_video.get("url") or "").strip() else None,
        "_input_reference_images": reference_images,
    }


def run_fal_ltx_video_job(body: dict, api_key: str) -> dict:
    body = normalize_video_request(body)
    input_mode = body.get("videoInputMode", "text")
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("Please enter a video prompt.")
    negative_prompt = str(body.get("negativePrompt") or "").strip()
    resolution = str(body.get("resolution") or "").strip()
    aspect_ratio = str(body.get("aspectRatio") or "").strip()
    safety_checker = bool(body.get("videoSafetyChecker", True))
    generate_audio = bool(body.get("videoGenerateAudio", False))
    source_image = body.get("sourceImage") or {}
    endpoint = str(body.get("model") or (FAL_LTX_VIDEO_I2V_ID if input_mode == "image" else FAL_LTX_VIDEO_T2V_ID)).strip()
    model_info = VIDEO_MODELS_INFO.get(endpoint, {})
    if not endpoint or model_info.get("provider") != "fal" or model_info.get("family") != "ltx-video":
        raise ValueError("Choose a valid Fal LTX Video model.")
    payload = {
        "prompt": prompt,
        "sync_mode": True,
    }
    if negative_prompt:
        payload["negative_prompt"] = negative_prompt
    if input_mode == "image":
        if not source_image.get("data"):
            raise ValueError("Choose a start image for LTX Video image-to-video.")
        payload["image_url"] = f"data:{source_image.get('mime_type', 'image/png')};base64,{source_image.get('data', '')}"
    if endpoint == FAL_LTX_VIDEO_LORA_I2V_ID:
        if resolution:
            payload["resolution"] = resolution
        if aspect_ratio:
            payload["aspect_ratio"] = aspect_ratio
        payload["enable_safety_checker"] = safety_checker
    elif endpoint == FAL_LTX_23_22B_I2V_ID:
        if resolution:
            payload["video_size"] = resolution
        payload["enable_safety_checker"] = safety_checker
        payload["generate_audio"] = generate_audio
    headers = {
        "Authorization": f"Key {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        response = requests.post(f"{FAL_BASE_URL}/{endpoint}", headers=headers, json=payload, timeout=900)
    except requests.exceptions.Timeout as exc:
        raise TimeoutError("Timeout: Fal LTX Video generation took too long.") from exc
    except Exception as exc:
        raise RuntimeError(f"Network error: {exc}") from exc
    if response.status_code != 200:
        raise RuntimeError(extract_fal_error(response))
    result = response.json()
    video_item = extract_fal_video_result(result)
    if not video_item:
        raise RuntimeError("Fal LTX Video returned no video for this request.")
    params_meta = merge_request_settings(merge_asset_metadata({
        "model": endpoint,
        "modelFamily": body.get("modelFamily", "ltx-video"),
        "model_label": model_info.get("label", "LTX Video"),
        "provider": "fal",
        "provider_label": "Fal",
        "videoInputMode": input_mode,
        "negativePrompt": negative_prompt,
        "prompt": prompt,
        "resolution": resolution,
        "aspectRatio": aspect_ratio,
        "videoSafetyChecker": safety_checker if model_info.get("supports_safety_checker") else None,
        "videoGenerateAudio": generate_audio if model_info.get("supports_generate_audio") else None,
    }, body), body)
    return {
        "ok": True,
        "videos": [video_item],
        "text": "",
        "cost": round(float(VIDEO_PRICING.get(endpoint, {}).get("5", 0.0)), 4),
        "model_label": model_info.get("label", "LTX Video"),
        "params": params_meta,
        "_input_source_image": source_image if str(source_image.get("data") or "").strip() else None,
        "_input_reference_images": [],
    }


def run_fal_seedvr_video_job(body: dict, api_key: str) -> dict:
    body = normalize_video_request(body)
    input_mode = body.get("videoInputMode", "video")
    endpoint = str(body.get("model") or FAL_SEEDVR_VIDEO_ID).strip() or FAL_SEEDVR_VIDEO_ID
    model_info = VIDEO_MODELS_INFO.get(endpoint, {})
    if not endpoint or model_info.get("provider") != "fal" or model_info.get("family") != "seedvr-video":
        raise ValueError("Choose a valid Fal SeedVR2 video model.")

    source_video = body.get("sourceVideo") or {}
    if not str(source_video.get("data") or source_video.get("url") or "").strip():
        raise ValueError("Choose or drop a source video for SeedVR2.")

    upscale_mode = normalize_video_upscale_mode(body.get("videoUpscaleMode", "factor"))
    upscale_factor = normalize_video_upscale_factor(body.get("videoUpscaleFactor", 2))
    target_resolution = normalize_video_upscale_target_resolution(
        body.get("videoUpscaleTargetResolution", "1080p")
    )
    noise_scale = normalize_video_upscale_noise_scale(body.get("videoUpscaleNoiseScale", 0.1))
    output_format = normalize_video_upscale_output_format(body.get("videoUpscaleOutputFormat", "X264 (.mp4)"))
    output_quality = normalize_video_upscale_output_quality(body.get("videoUpscaleOutputQuality", "high"))
    output_write_mode = normalize_video_upscale_write_mode(body.get("videoUpscaleOutputWriteMode", "balanced"))
    sync_mode = bool(body.get("videoUpscaleSyncMode", True))
    seed_value = normalize_optional_int(body.get("videoUpscaleSeed"))

    try:
        client = fal_client.SyncClient(key=api_key, default_timeout=1800.0)
        uploaded_video_url = upload_video_payload_to_fal(client, source_video)
        payload = {
            "video_url": uploaded_video_url,
            "upscale_mode": upscale_mode,
            "noise_scale": noise_scale,
            "output_format": output_format,
            "output_quality": output_quality,
            "output_write_mode": output_write_mode,
            "sync_mode": sync_mode,
        }
        if upscale_mode == "target":
            payload["target_resolution"] = target_resolution
        else:
            payload["upscale_factor"] = upscale_factor
        if seed_value is not None:
            payload["seed"] = seed_value
        result = client.run(endpoint, arguments=payload)
    except TimeoutError as exc:
        raise TimeoutError("Timeout: SeedVR2 video upscale took too long.") from exc
    except Exception as exc:
        message = str(exc)
        if "Timeout" in message:
            raise TimeoutError("Timeout: SeedVR2 video upscale took too long.") from exc
        raise RuntimeError(f"SeedVR2 video upscale failed: {message}") from exc

    video_item = extract_fal_video_result(result if isinstance(result, dict) else {})
    if not video_item:
        raise RuntimeError("Fal SeedVR2 returned no video for this request.")

    params_meta = merge_request_settings(merge_asset_metadata({
        "model": endpoint,
        "modelFamily": body.get("modelFamily", "seedvr-video"),
        "model_label": model_info.get("label", "SeedVR2 Video"),
        "provider": "fal",
        "provider_label": "Fal",
        "videoInputMode": input_mode,
        "upscaled": True,
        "upscalerType": "seedvr2-video",
        "upscalerLabel": "SeedVR2 Video",
        "upscaleModel": "seedvr2-video",
        "upscalePreset": "video",
        "prompt": "",
        "negativePrompt": "",
        "duration": body.get("duration", 0),
        "aspectRatio": body.get("aspectRatio", ""),
        "resolution": body.get("resolution", ""),
        "videoUpscaleMode": upscale_mode,
        "videoUpscaleFactor": float(upscale_factor),
        "videoUpscaleTargetResolution": target_resolution,
        "videoUpscaleNoiseScale": float(noise_scale),
        "videoUpscaleOutputFormat": output_format,
        "videoUpscaleOutputQuality": output_quality,
        "videoUpscaleOutputWriteMode": output_write_mode,
        "videoUpscaleSeed": seed_value,
        "videoUpscaleSyncMode": sync_mode,
    }, body), body)
    return {
        "ok": True,
        "videos": [video_item],
        "text": "",
        "cost": 0.0,
        "model_label": model_info.get("label", "SeedVR2 Video"),
        "params": params_meta,
        "_input_source_image": None,
        "_input_source_video": source_video,
        "_input_reference_images": [],
    }


def run_fal_seedream_generation_job(body: dict, api_key: str) -> dict:
    body = normalize_generation_request(body)
    model_id = body.get("model", FAL_SEEDREAM_45_TEXT_ID)
    image_size = body.get("imageSize", "2K")
    num_images = max(1, min(int(body.get("numberOfImages", 1)), 4))
    aspect_ratio = body.get("aspectRatio", "1:1")
    ref_images = body.get("refImages", [])
    enable_safety_checker = bool(body.get("falSafetyChecker", True))
    seed_mode = normalize_seed_mode(body.get("seedMode", "random"))
    seed_value = coerce_seed_value(body.get("seedValue", 1))

    raw_prompt = body.get("prompt", "")
    if isinstance(raw_prompt, dict):
        prompt = json.dumps(raw_prompt, ensure_ascii=False, indent=2)
    else:
        prompt = str(raw_prompt).strip()

    if not prompt:
        raise ValueError("Please enter a prompt")
    if model_id not in MODELS_INFO:
        raise ValueError("Invalid model")

    model_info = MODELS_INFO[model_id]
    max_ref = model_info["max_ref_images"]
    if ref_images and max_ref == 0:
        raise ValueError(f"{model_info['label']} does not support reference images.")
    ref_images = normalize_ref_image_payloads(ref_images, max_ref)

    endpoint = build_fal_seedream_endpoint(model_id, bool(ref_images))
    payload = {
        "prompt": prompt,
        "image_size": build_fal_seedream_image_size(model_id, image_size),
        "num_images": num_images,
        "max_images": 1,
        "sync_mode": True,
        "enable_safety_checker": enable_safety_checker,
    }
    if ref_images:
        payload["image_urls"] = build_seedream_data_uri_ref_inputs(ref_images)

    actual_seed = None
    if seed_mode != "random":
        actual_seed = seed_value
        payload["seed"] = actual_seed

    headers = {
        "Authorization": f"Key {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        response = requests.post(f"{FAL_BASE_URL}/{endpoint}", headers=headers, json=payload, timeout=240)
    except requests.exceptions.Timeout as exc:
        raise TimeoutError("Timeout: Fal Seedream generation took too long.") from exc
    except Exception as exc:
        raise RuntimeError(f"Network error: {exc}") from exc

    if response.status_code != 200:
        raise RuntimeError(extract_fal_error(response))

    result = response.json()
    returned_seed = result.get("seed") if isinstance(result, dict) else None
    items = result.get("images") or result.get("data") or []
    images = []
    for item in items:
        if not isinstance(item, dict):
            continue
        decoded = decode_fal_image_result(item)
        if decoded:
            images.append(decoded)

    if not images:
        raise RuntimeError("Fal Seedream returned no image for this request.")

    png_images = []
    for img in images:
        try:
            png_b64, png_mime = convert_image_b64_to_png(img.get("data", ""), img.get("mime_type", "image/png"))
            png_images.append({"mime_type": png_mime, "data": png_b64})
        except Exception:
            png_images.append(img)

    price_per_image = PRICING.get(model_id, {}).get(image_size, 0.0)
    cost = price_per_image * len(png_images)
    params_meta = merge_request_settings(merge_asset_metadata({
        "model": model_id,
        "modelFamily": body.get("modelFamily", model_info.get("family", "")),
        "model_label": model_info["label"],
        "provider": body.get("provider", model_info.get("provider", "fal")),
        "provider_label": model_info.get("provider_label", PROVIDER_LABELS.get(body.get("provider", "fal"), "Fal")),
        "imageSize": image_size,
        "aspectRatio": aspect_ratio,
        "temperature": float(body.get("temperature", 1.0)),
        "topP": float(body.get("topP", 0.95)),
        "thinkingLevel": body.get("thinkingLevel", "Minimal"),
        "useSearch": bool(body.get("useSearch", False)),
        "outputMode": body.get("outputMode", "images_only"),
        "prompt": prompt,
        "ref_count": len(ref_images),
        "seedMode": seed_mode,
        "seedValue": int(returned_seed if returned_seed is not None else actual_seed if actual_seed is not None else seed_value),
        "falSafetyChecker": enable_safety_checker,
    }, body), body)
    return {
        "ok": True,
        "images": png_images,
        "text": "",
        "cost": round(cost, 4),
        "model_label": model_info["label"],
        "params": params_meta,
        "_input_ref_images": ref_images,
    }


def run_fal_nano_banana_generation_job(body: dict, api_key: str) -> dict:
    body = normalize_generation_request(body)
    model_id = body.get("model", FAL_NANO_BANANA_2_TEXT_ID)
    image_size = body.get("imageSize", "1K")
    num_images = max(1, min(int(body.get("numberOfImages", 1)), 4))
    aspect_ratio = body.get("aspectRatio", "1:1")
    ref_images = body.get("refImages", [])
    use_search = bool(body.get("useSearch", False))
    fal_safety_tolerance = normalize_fal_safety_tolerance(body.get("falSafetyTolerance", 4))
    seed_mode = normalize_seed_mode(body.get("seedMode", "random"))
    seed_value = coerce_seed_value(body.get("seedValue", 1))

    raw_prompt = body.get("prompt", "")
    if isinstance(raw_prompt, dict):
        prompt = json.dumps(raw_prompt, ensure_ascii=False, indent=2)
    else:
        prompt = str(raw_prompt).strip()

    if not prompt:
        raise ValueError("Please enter a prompt")
    if model_id not in MODELS_INFO:
        raise ValueError("Invalid model")

    model_info = MODELS_INFO[model_id]
    max_ref = model_info["max_ref_images"]
    if ref_images and max_ref == 0:
        raise ValueError(f"{model_info['label']} does not support reference images.")
    ref_images = normalize_ref_image_payloads(ref_images, max_ref)

    endpoint = build_fal_nano_banana_endpoint(model_id, bool(ref_images))
    payload = {
        "prompt": prompt,
        "num_images": num_images,
        "resolution": build_fal_nano_banana_resolution(model_id, image_size),
        "aspect_ratio": aspect_ratio,
        "output_format": "png",
        "sync_mode": True,
        "safety_tolerance": fal_safety_tolerance,
    }
    if ref_images:
        payload["image_urls"] = build_data_uri_ref_inputs(ref_images)
    if use_search:
        payload["enable_web_search"] = True
    actual_seed = None
    if seed_mode != "random":
        actual_seed = seed_value
        payload["seed"] = actual_seed

    headers = {
        "Authorization": f"Key {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        response = requests.post(f"{FAL_BASE_URL}/{endpoint}", headers=headers, json=payload, timeout=240)
    except requests.exceptions.Timeout as exc:
        raise TimeoutError("Timeout: Fal Nano Banana generation took too long.") from exc
    except Exception as exc:
        raise RuntimeError(f"Network error: {exc}") from exc

    if response.status_code != 200:
        raise RuntimeError(extract_fal_error(response))

    result = response.json()
    returned_seed = result.get("seed") if isinstance(result, dict) else None
    items = result.get("images") or result.get("data") or []
    images = []
    for item in items:
        if not isinstance(item, dict):
            continue
        decoded = decode_fal_image_result(item)
        if decoded:
            images.append(decoded)

    if not images:
        raise RuntimeError("Fal Nano Banana returned no image for this request.")

    png_images = []
    for img in images:
        try:
            png_b64, png_mime = convert_image_b64_to_png(img.get("data", ""), img.get("mime_type", "image/png"))
            png_images.append({"mime_type": png_mime, "data": png_b64})
        except Exception:
            png_images.append(img)

    price_per_image = PRICING.get(model_id, {}).get(image_size, 0.0)
    cost = price_per_image * len(png_images)
    params_meta = merge_request_settings(merge_asset_metadata({
        "model": model_id,
        "modelFamily": body.get("modelFamily", model_info.get("family", "")),
        "model_label": model_info["label"],
        "provider": body.get("provider", model_info.get("provider", "fal")),
        "provider_label": model_info.get("provider_label", PROVIDER_LABELS.get(body.get("provider", "fal"), "Fal")),
        "imageSize": image_size,
        "aspectRatio": aspect_ratio,
        "temperature": float(body.get("temperature", 1.0)),
        "topP": float(body.get("topP", 0.95)),
        "thinkingLevel": body.get("thinkingLevel", "Minimal"),
        "useSearch": use_search,
        "outputMode": body.get("outputMode", "images_only"),
        "prompt": prompt,
        "ref_count": len(ref_images),
        "seedMode": seed_mode,
        "seedValue": int(returned_seed if returned_seed is not None else actual_seed if actual_seed is not None else seed_value),
        "falSafetyTolerance": fal_safety_tolerance,
    }, body), body)
    text_value = ""
    if isinstance(result, dict):
        text_value = str(result.get("description") or result.get("text") or "")
    return {
        "ok": True,
        "images": png_images,
        "text": text_value,
        "cost": round(cost, 4),
        "model_label": model_info["label"],
        "params": params_meta,
        "_input_ref_images": ref_images,
    }


def run_fal_gpt_image_2_generation_job(body: dict, api_key: str) -> dict:
    body = normalize_generation_request(body)
    model_id = body.get("model", FAL_GPT_IMAGE_2_TEXT_ID)
    image_size = body.get("imageSize", "1K")
    num_images = max(1, min(int(body.get("numberOfImages", 1)), 4))
    aspect_ratio = body.get("aspectRatio", "1:1")
    ref_images = body.get("refImages", [])
    seed_mode = normalize_seed_mode(body.get("seedMode", "random"))
    seed_value = coerce_seed_value(body.get("seedValue", 1))

    raw_prompt = body.get("prompt", "")
    if isinstance(raw_prompt, dict):
        prompt = json.dumps(raw_prompt, ensure_ascii=False, indent=2)
    else:
        prompt = str(raw_prompt).strip()

    if not prompt:
        raise ValueError("Please enter a prompt")
    if model_id != FAL_GPT_IMAGE_2_TEXT_ID or model_id not in MODELS_INFO:
        raise ValueError("Invalid model")
    if ref_images:
        raise ValueError("GPT Image 2 does not support reference images in the generation tab.")

    requested_image_size = build_fal_gpt_image_2_image_size(image_size, aspect_ratio)
    payload = {
        "prompt": prompt,
        "image_size": requested_image_size,
        "quality": "high",
        "num_images": num_images,
        "output_format": "png",
        "sync_mode": True,
    }

    actual_seed = None
    if seed_mode != "random":
        actual_seed = seed_value
        payload["seed"] = actual_seed

    headers = {
        "Authorization": f"Key {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        response = requests.post(f"{FAL_BASE_URL}/{model_id}", headers=headers, json=payload, timeout=240)
    except requests.exceptions.Timeout as exc:
        raise TimeoutError("Timeout: Fal GPT Image 2 generation took too long.") from exc
    except Exception as exc:
        raise RuntimeError(f"Network error: {exc}") from exc

    if response.status_code != 200:
        raise RuntimeError(extract_fal_error(response))

    result = response.json()
    returned_seed = result.get("seed") if isinstance(result, dict) else None
    items = result.get("images") or result.get("data") or []
    images = []
    for item in items:
        if not isinstance(item, dict):
            continue
        decoded = decode_fal_image_result(item)
        if decoded:
            images.append(decoded)

    if not images:
        raise RuntimeError("Fal GPT Image 2 returned no image for this request.")

    png_images = []
    for img in images:
        try:
            png_b64, png_mime = convert_image_b64_to_png(img.get("data", ""), img.get("mime_type", "image/png"))
            png_images.append({"mime_type": png_mime, "data": png_b64})
        except Exception:
            png_images.append(img)

    delivered_image_size = requested_image_size
    if items and isinstance(items[0], dict):
        try:
            delivered_width = int(items[0].get("width") or 0)
            delivered_height = int(items[0].get("height") or 0)
        except Exception:
            delivered_width = 0
            delivered_height = 0
        if delivered_width > 0 and delivered_height > 0:
            delivered_image_size = {"width": delivered_width, "height": delivered_height}

    model_info = MODELS_INFO[model_id]
    price_per_image = estimate_fal_gpt_image_2_price_per_image(delivered_image_size)
    cost = price_per_image * len(png_images)
    params_meta = merge_request_settings(merge_asset_metadata({
        "model": model_id,
        "modelFamily": body.get("modelFamily", model_info.get("family", "")),
        "model_label": model_info["label"],
        "provider": body.get("provider", model_info.get("provider", "fal")),
        "provider_label": model_info.get("provider_label", PROVIDER_LABELS.get(body.get("provider", "fal"), "Fal")),
        "imageSize": image_size,
        "aspectRatio": aspect_ratio,
        "temperature": float(body.get("temperature", 1.0)),
        "topP": float(body.get("topP", 0.95)),
        "thinkingLevel": body.get("thinkingLevel", "Minimal"),
        "useSearch": False,
        "outputMode": body.get("outputMode", "images_only"),
        "prompt": prompt,
        "ref_count": 0,
        "seedMode": seed_mode,
        "seedValue": int(returned_seed if returned_seed is not None else actual_seed if actual_seed is not None else seed_value),
    }, body), body)
    return {
        "ok": True,
        "images": png_images,
        "text": "",
        "cost": round(cost, 4),
        "model_label": model_info["label"],
        "params": params_meta,
        "_input_ref_images": [],
    }


OPENAI_GPT_IMAGE_2_MIN_PIXELS = 786_432   # 1024x768 accepted, 800x800 refused (probed 2026-09-19)


def _openai_gpt_image_2_size(image_size: str, aspect_ratio: str) -> tuple[int, int]:
    """Same pixel budget as the Fal route, adjusted to OpenAI's rules for gpt-image-2 (probed):
    both sides divisible by 16, and at least ~0.79 MP. Only 1K 16:9 / 9:16 fall short (1024x576 -> 1280x720)."""
    width, height = resolve_fal_gpt_image_2_dimensions(build_fal_gpt_image_2_image_size(image_size, aspect_ratio))
    if (width, height) in ((1024, 576), (576, 1024)):          # the one short case: use the standard HD frame
        return (1280, 720) if width > height else (720, 1280)
    if width * height < OPENAI_GPT_IMAGE_2_MIN_PIXELS:
        scale = (OPENAI_GPT_IMAGE_2_MIN_PIXELS / float(width * height)) ** 0.5
        width, height = int(round(width * scale)), int(round(height * scale))
    width, height = max(16, (width + 8) // 16 * 16), max(16, (height + 8) // 16 * 16)
    while width * height < OPENAI_GPT_IMAGE_2_MIN_PIXELS:
        width, height = width + 16, height + 16
    return width, height


def _openai_api_model_for(model_id: str) -> str:
    return OPENAI_GPT_IMAGE_25_MODELS.get(model_id, {}).get("api_model") or OPENAI_GPT_IMAGE_2_API_MODEL


def _openai_gpt_image_2_result(body: dict, model_id: str, data: dict, width: int, height: int, prompt: str, ref_images: list) -> dict:
    items = data.get("data") or []
    png_images = []
    for item in items:
        b64_data = str((item or {}).get("b64_json") or "").strip()
        if b64_data:
            png_images.append({"mime_type": "image/png", "data": b64_data})
    if not png_images:
        raise RuntimeError("OpenAI returned no image for this request.")
    model_info = MODELS_INFO[model_id]
    usage = data.get("usage") or {}
    price_per_image = estimate_fal_gpt_image_2_price_per_image({"width": width, "height": height})   # OpenAI list price, same table
    params_meta = merge_request_settings(merge_asset_metadata({
        "model": model_id,
        "modelFamily": body.get("modelFamily", model_info.get("family", "")),
        "model_label": model_info["label"],
        "provider": "openai",
        "provider_label": "OpenAI",
        "imageSize": body.get("imageSize", "1K"),
        "aspectRatio": body.get("aspectRatio", "1:1"),
        "temperature": float(body.get("temperature", 1.0)),
        "topP": float(body.get("topP", 0.95)),
        "thinkingLevel": body.get("thinkingLevel", "Minimal"),
        "useSearch": False,
        "outputMode": body.get("outputMode", "images_only"),
        "prompt": prompt,
        "ref_count": len(ref_images),
        "seedMode": "random",   # the Images API has no seed
        "seedValue": 0,
        "openaiUsage": {"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens")},
        "openaiApiModel": _openai_api_model_for(model_id),
        "deliveredSize": f"{width}x{height}",
    }, body), body)
    return {
        "ok": True,
        "images": png_images,
        "text": "",
        "cost": round(price_per_image * len(png_images), 4),
        "model_label": model_info["label"],
        "params": params_meta,
        "_input_ref_images": ref_images,
    }


def _openai_images_error(response) -> str:
    try:
        return str(response.json().get("error", {}).get("message") or f"HTTP {response.status_code}")
    except Exception:
        return f"HTTP {response.status_code}"


def run_openai_gpt_image_2_generation_job(body: dict, api_key: str) -> dict:
    """GPT Image 2 via OpenAI's Images API (POST /images/generations). Same options as the Fal route."""
    body = normalize_generation_request(body)
    model_id = body.get("model", OPENAI_GPT_IMAGE_2_TEXT_ID)
    if model_id != OPENAI_GPT_IMAGE_2_TEXT_ID and model_id not in OPENAI_GPT_IMAGE_25_MODELS:
        raise ValueError("Invalid model")
    raw_prompt = body.get("prompt", "")
    prompt = json.dumps(raw_prompt, ensure_ascii=False, indent=2) if isinstance(raw_prompt, dict) else str(raw_prompt).strip()
    if not prompt:
        raise ValueError("Please enter a prompt")
    if body.get("refImages"):
        raise ValueError("GPT Image 2 does not support reference images in the generation tab. Use GPT Image 2 Edit.")
    num_images = max(1, min(int(body.get("numberOfImages", 1)), 4))
    width, height = _openai_gpt_image_2_size(body.get("imageSize", "1K"), body.get("aspectRatio", "1:1"))
    payload = {"model": _openai_api_model_for(model_id), "prompt": prompt, "n": num_images, "size": f"{width}x{height}",
               "quality": "high", "output_format": "png"}
    try:
        response = requests.post(f"{OPENAI_API_BASE}/images/generations", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                                 json=payload, timeout=300)
    except requests.exceptions.Timeout as exc:
        raise TimeoutError("Timeout: OpenAI GPT Image 2 generation took too long.") from exc
    except Exception as exc:
        raise RuntimeError(f"Network error: {exc}") from exc
    if response.status_code != 200:
        raise RuntimeError(_openai_images_error(response))
    return _openai_gpt_image_2_result(body, model_id, response.json(), width, height, prompt, [])


def run_openai_gpt_image_2_edit_job(body: dict, api_key: str) -> dict:
    """GPT Image 2 Edit via OpenAI's Images API (POST /images/edits, multipart, image[] up to 16)."""
    body = normalize_generation_request(body)
    model_id = body.get("model", OPENAI_GPT_IMAGE_2_EDIT_ID)
    if model_id != OPENAI_GPT_IMAGE_2_EDIT_ID and model_id not in OPENAI_GPT_IMAGE_25_MODELS:
        raise ValueError("Invalid model")
    raw_prompt = body.get("prompt", "")
    prompt = json.dumps(raw_prompt, ensure_ascii=False, indent=2) if isinstance(raw_prompt, dict) else str(raw_prompt).strip()
    if not prompt:
        raise ValueError("Please enter a prompt")
    ref_images = normalize_ref_image_payloads(body.get("refImages", []), MODELS_INFO[model_id]["max_ref_images"])
    if not ref_images:
        raise ValueError("GPT Image 2 Edit needs at least one reference image.")
    num_images = max(1, min(int(body.get("numberOfImages", 1)), 4))
    width, height = _openai_gpt_image_2_size(body.get("imageSize", "1K"), body.get("aspectRatio", "1:1"))
    files = []
    for index, img in enumerate(ref_images):
        mime = str(img.get("mime_type") or "image/png")
        ext = "jpg" if "jpeg" in mime or "jpg" in mime else ("webp" if "webp" in mime else "png")
        files.append(("image[]", (f"reference-{index + 1}.{ext}", base64.b64decode(img["data"]), mime)))
    fields = {"model": _openai_api_model_for(model_id), "prompt": prompt, "n": str(num_images), "size": f"{width}x{height}",
              "quality": "high", "output_format": "png"}
    try:
        response = requests.post(f"{OPENAI_API_BASE}/images/edits", headers={"Authorization": f"Bearer {api_key}"},
                                 data=fields, files=files, timeout=300)
    except requests.exceptions.Timeout as exc:
        raise TimeoutError("Timeout: OpenAI GPT Image 2 edit took too long.") from exc
    except Exception as exc:
        raise RuntimeError(f"Network error: {exc}") from exc
    if response.status_code != 200:
        raise RuntimeError(_openai_images_error(response))
    return _openai_gpt_image_2_result(body, model_id, response.json(), width, height, prompt, ref_images)


def run_fal_gpt_image_2_edit_job(body: dict, api_key: str) -> dict:
    body = normalize_generation_request(body)
    model_id = body.get("model", FAL_GPT_IMAGE_2_EDIT_ID)
    image_size = body.get("imageSize", "1K")
    num_images = max(1, min(int(body.get("numberOfImages", 1)), 4))
    aspect_ratio = body.get("aspectRatio", "1:1")
    ref_images = body.get("refImages", [])
    seed_mode = normalize_seed_mode(body.get("seedMode", "random"))
    seed_value = coerce_seed_value(body.get("seedValue", 1))

    raw_prompt = body.get("prompt", "")
    if isinstance(raw_prompt, dict):
        prompt = json.dumps(raw_prompt, ensure_ascii=False, indent=2)
    else:
        prompt = str(raw_prompt).strip()

    if not prompt:
        raise ValueError("Please enter a prompt")
    if model_id != FAL_GPT_IMAGE_2_EDIT_ID or model_id not in MODELS_INFO:
        raise ValueError("Invalid model")

    model_info = MODELS_INFO[model_id]
    max_ref = model_info["max_ref_images"]
    ref_images = normalize_ref_image_payloads(ref_images, max_ref)
    if not ref_images:
        raise ValueError("GPT Image 2 Edit requires at least 1 reference image.")

    requested_image_size = build_fal_gpt_image_2_image_size(image_size, aspect_ratio)
    payload = {
        "prompt": prompt,
        "image_urls": build_data_uri_ref_inputs(ref_images),
        "image_size": requested_image_size,
        "quality": "high",
        "num_images": num_images,
        "output_format": "png",
        "sync_mode": True,
    }

    actual_seed = None
    if seed_mode != "random":
        actual_seed = seed_value
        payload["seed"] = actual_seed

    headers = {
        "Authorization": f"Key {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        response = requests.post(f"{FAL_BASE_URL}/{model_id}", headers=headers, json=payload, timeout=240)
    except requests.exceptions.Timeout as exc:
        raise TimeoutError("Timeout: Fal GPT Image 2 Edit took too long.") from exc
    except Exception as exc:
        raise RuntimeError(f"Network error: {exc}") from exc

    if response.status_code != 200:
        raise RuntimeError(extract_fal_error(response))

    result = response.json()
    returned_seed = result.get("seed") if isinstance(result, dict) else None
    items = result.get("images") or result.get("data") or []
    images = []
    for item in items:
        if not isinstance(item, dict):
            continue
        decoded = decode_fal_image_result(item)
        if decoded:
            images.append(decoded)

    if not images:
        raise RuntimeError("Fal GPT Image 2 Edit returned no image for this request.")

    png_images = []
    for img in images:
        try:
            png_b64, png_mime = convert_image_b64_to_png(img.get("data", ""), img.get("mime_type", "image/png"))
            png_images.append({"mime_type": png_mime, "data": png_b64})
        except Exception:
            png_images.append(img)

    delivered_image_size = requested_image_size
    if items and isinstance(items[0], dict):
        try:
            delivered_width = int(items[0].get("width") or 0)
            delivered_height = int(items[0].get("height") or 0)
        except Exception:
            delivered_width = 0
            delivered_height = 0
        if delivered_width > 0 and delivered_height > 0:
            delivered_image_size = {"width": delivered_width, "height": delivered_height}

    price_per_image = estimate_fal_gpt_image_2_price_per_image(delivered_image_size)
    cost = price_per_image * len(png_images)
    params_meta = merge_request_settings(merge_asset_metadata({
        "model": model_id,
        "modelFamily": body.get("modelFamily", model_info.get("family", "")),
        "model_label": model_info["label"],
        "provider": body.get("provider", model_info.get("provider", "fal")),
        "provider_label": model_info.get("provider_label", PROVIDER_LABELS.get(body.get("provider", "fal"), "Fal")),
        "imageSize": image_size,
        "aspectRatio": aspect_ratio,
        "temperature": float(body.get("temperature", 1.0)),
        "topP": float(body.get("topP", 0.95)),
        "thinkingLevel": body.get("thinkingLevel", "Minimal"),
        "useSearch": False,
        "outputMode": body.get("outputMode", "images_only"),
        "prompt": prompt,
        "ref_count": len(ref_images),
        "seedMode": seed_mode,
        "seedValue": int(returned_seed if returned_seed is not None else actual_seed if actual_seed is not None else seed_value),
    }, body), body)
    return {
        "ok": True,
        "images": png_images,
        "text": "",
        "cost": round(cost, 4),
        "model_label": model_info["label"],
        "params": params_meta,
        "_input_ref_images": ref_images,
    }


def run_byteplus_seedream_generation_job(body: dict, api_key: str) -> dict:
    body = normalize_generation_request(body)
    model_id = body.get("model", BYTEPLUS_SEEDREAM_45_MODEL_ID)
    image_size = normalize_seedream_size(body.get("imageSize", "2K"))
    num_images = max(1, min(int(body.get("numberOfImages", 1)), 4))
    aspect_ratio = body.get("aspectRatio", "1:1")
    ref_images = body.get("refImages", [])
    seed_mode = normalize_seed_mode(body.get("seedMode", "random"))
    seed_value = coerce_seed_value(body.get("seedValue", 1))

    raw_prompt = body.get("prompt", "")
    if isinstance(raw_prompt, dict):
        prompt = json.dumps(raw_prompt, ensure_ascii=False, indent=2)
    else:
        prompt = str(raw_prompt).strip()

    if not prompt:
        raise ValueError("Please enter a prompt")
    if model_id not in MODELS_INFO:
        raise ValueError("Invalid model")

    model_info = MODELS_INFO[model_id]
    max_ref = model_info["max_ref_images"]
    ref_images = normalize_ref_image_payloads(ref_images, max_ref)

    payload = {
        "model": BYTEPLUS_SEEDREAM_45_MODEL_ID,
        "prompt": prompt,
        "sequential_image_generation": "auto" if num_images > 1 else "disabled",
        "response_format": "url",
        "size": image_size,
        "stream": False,
        "watermark": False,
    }
    if num_images > 1:
        payload["sequential_image_generation_options"] = {"max_images": num_images}
    if ref_images:
        ref_inputs = build_byteplus_seedream_ref_inputs(ref_images)
        payload["image"] = ref_inputs[0] if len(ref_inputs) == 1 else ref_inputs
    if seed_mode != "random":
        payload["seed"] = seed_value

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        response = requests.post(BYTEPLUS_BASE_URL, headers=headers, json=payload, timeout=240)
    except requests.exceptions.Timeout as exc:
        raise TimeoutError("Timeout: BytePlus Seedream generation took too long.") from exc
    except Exception as exc:
        raise RuntimeError(f"Network error: {exc}") from exc

    if response.status_code != 200:
        raise RuntimeError(extract_byteplus_error(response))

    result = response.json()
    items = result.get("data") or result.get("images") or []
    images = []
    for item in items:
        if isinstance(item, dict):
            decoded = decode_fal_image_result(item)
            if decoded:
                images.append(decoded)
        elif isinstance(item, str):
            decoded = decode_fal_image_result({"url": item})
            if decoded:
                images.append(decoded)

    if not images:
        raise RuntimeError("BytePlus Seedream returned no image for this request.")

    price_per_image = PRICING.get(model_id, {}).get(image_size, 0.0)
    cost = price_per_image * len(images)
    params_meta = merge_request_settings(merge_asset_metadata({
        "model": model_id,
        "modelFamily": body.get("modelFamily", model_info.get("family", "")),
        "model_label": model_info["label"],
        "provider": body.get("provider", model_info.get("provider", "byteplus")),
        "provider_label": model_info.get("provider_label", PROVIDER_LABELS.get(body.get("provider", "byteplus"), "BytePlus")),
        "byteplusSafetyMode": str(body.get("byteplusSafetyMode", "platform_default") or "platform_default"),
        "imageSize": image_size,
        "aspectRatio": aspect_ratio,
        "temperature": float(body.get("temperature", 1.0)),
        "topP": float(body.get("topP", 0.95)),
        "thinkingLevel": body.get("thinkingLevel", "Minimal"),
        "useSearch": bool(body.get("useSearch", False)),
        "outputMode": body.get("outputMode", "images_only"),
        "prompt": prompt,
        "ref_count": len(ref_images),
        "seedMode": seed_mode,
        "seedValue": seed_value,
    }, body), body)
    return {
        "ok": True,
        "images": images,
        "text": "",
        "cost": round(cost, 4),
        "model_label": model_info["label"],
        "params": params_meta,
        "_input_ref_images": ref_images,
    }


def run_fal_seedvr_upscale_job(body: dict, api_key: str) -> dict:
    upscale_model_key = str(body.get("upscaleModel") or "seedvr2").strip().lower()
    upscaler_info = UPSCALER_MODELS.get(upscale_model_key)
    if not upscaler_info:
        raise ValueError("Invalid upscale model.")

    source_image = body.get("image") if isinstance(body.get("image"), dict) else {}
    image_b64 = str(source_image.get("data") or "").strip()
    mime_type = str(source_image.get("mime_type") or "image/png").strip() or "image/png"
    if not image_b64:
        raise ValueError("Select an image from the gallery to upscale.")

    source_params = dict(body.get("sourceParams") or {})
    source_width, source_height = measure_image_dimensions(image_b64, mime_type)
    preset_value = str(body.get("upscalePreset") or "factor:2").strip().lower()
    target_width = 0
    target_height = 0
    target_anchor = ""
    if preset_value == "custom":
        target_width, target_height, upscale_factor, target_anchor = normalize_seedvr_custom_resolution(
            source_width,
            source_height,
            body.get("upscaleTargetWidth"),
            body.get("upscaleTargetHeight"),
            body.get("upscaleTargetAnchor"),
        )
        upscale_mode = "factor"
        target_resolution = ""
        preset_label = f"{target_width}x{target_height}"
    else:
        upscale_mode, upscale_factor, target_resolution, preset_label = normalize_seedvr_preset(preset_value)

    payload = {
        "image_url": f"data:{mime_type};base64,{image_b64}",
        "upscale_mode": upscale_mode,
        "noise_scale": 0.1,
        "output_format": "png",
        "sync_mode": True,
    }
    if upscale_mode == "factor" and upscale_factor is not None:
        payload["upscale_factor"] = upscale_factor
    if upscale_mode == "target" and target_resolution:
        payload["target_resolution"] = target_resolution

    headers = {
        "Authorization": f"Key {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        response = requests.post(f"{FAL_BASE_URL}/{upscaler_info['id']}", headers=headers, json=payload, timeout=240)
    except requests.exceptions.Timeout as exc:
        raise TimeoutError("Timeout: SeedVR upscaling took too long.") from exc
    except Exception as exc:
        raise RuntimeError(f"Network error: {exc}") from exc

    if response.status_code != 200:
        raise RuntimeError(extract_fal_error(response))

    result = response.json()
    image_item = result.get("image") if isinstance(result, dict) else None
    if not isinstance(image_item, dict):
        items = result.get("images") or result.get("data") or []
        image_item = items[0] if items and isinstance(items[0], dict) else None
    if not isinstance(image_item, dict):
        raise RuntimeError("SeedVR returned no image for this request.")

    decoded = decode_fal_image_result(image_item)
    if not decoded:
        raise RuntimeError("SeedVR returned no image for this request.")

    output_width = int(image_item.get("width") or 0)
    output_height = int(image_item.get("height") or 0)
    if not output_width or not output_height:
        output_width, output_height = measure_image_dimensions(decoded.get("data", ""), decoded.get("mime_type", "image/png"))

    if target_width and target_height and (output_width != target_width or output_height != target_height):
        resized_b64, resized_mime = resize_image_b64_to_exact_png(
            decoded.get("data", ""),
            decoded.get("mime_type", "image/png"),
            target_width,
            target_height,
        )
        decoded = {"mime_type": resized_mime, "data": resized_b64}
        output_width = target_width
        output_height = target_height

    image_size_label = approximate_image_size_label(output_width, output_height) or str(source_params.get("imageSize") or "")
    display_size_label = preset_label or image_size_label
    megapixels = max(0.0, (output_width * output_height) / 1_000_000) if output_width and output_height else 0.0
    cost = round(megapixels * 0.001, 4)

    params_meta = dict(source_params)
    params_meta.update({
        "imageSize": image_size_label,
        "upscaled": True,
        "upscalerType": upscale_model_key,
        "upscalerLabel": upscaler_info["label"],
        "upscaleModel": upscale_model_key,
        "upscalePreset": preset_value,
        "upscaleMode": upscale_mode,
        "upscaleFactor": upscale_factor,
        "upscaleTargetResolution": target_resolution or (f"{target_width}x{target_height}" if target_width and target_height else ""),
        "upscaleTargetWidth": target_width,
        "upscaleTargetHeight": target_height,
        "upscaleTargetAnchor": target_anchor,
        "upscaleDisplaySize": f"Upscaled {str(display_size_label).upper()}" if display_size_label else "Upscaled",
        "upscaleSourceDate": str(source_params.get("gen_date") or ""),
        "upscaleOutputWidth": output_width,
        "upscaleOutputHeight": output_height,
        "upscaleSourceFilename": str(body.get("sourceFilename") or ""),
        "upscaleSourceRelpath": str(source_params.get("gen_relpath") or source_params.get("assetRelpath") or ""),
    })
    params_meta = merge_request_settings(merge_asset_metadata(params_meta, body, fallback_source=source_params), body)

    return {
        "ok": True,
        "images": [decoded],
        "text": "",
        "cost": cost,
        "model_label": params_meta.get("model_label", ""),
        "params": params_meta,
    }


def run_generation_job(body: dict, config: dict) -> dict:
    payload = normalize_generation_request(body)
    model_id = payload.get("model", "gemini-3.1-flash-image-preview")
    if model_id not in MODELS_INFO:
        raise ValueError("Invalid model")
    provider = payload.get("provider") or MODELS_INFO[model_id].get("provider", "gemini")
    family = payload.get("modelFamily") or MODELS_INFO[model_id].get("family", "")

    if provider == "gemini":
        gemini_key = (config.get("api_key", "") or "").strip()
        if not gemini_key:
            raise ValueError("Gemini API key not configured. Go to Settings.")
        return run_gemini_generation_job(payload, gemini_key)

    if provider == "fal":
        fal_key = (config.get("fal_api_key", "") or "").strip()
        if not fal_key:
            raise ValueError("Fal API key not configured. Go to Settings.")
        if family == "gpt-image-2":
            return run_fal_gpt_image_2_generation_job(payload, fal_key)
        if family == "gpt-image-2-edit":
            return run_fal_gpt_image_2_edit_job(payload, fal_key)
        if family.startswith("seedream"):
            return run_fal_seedream_generation_job(payload, fal_key)
        return run_fal_nano_banana_generation_job(payload, fal_key)

    if provider == "byteplus":
        byteplus_key = (config.get("byteplus_api_key", "") or config.get("seedream_api_key", "") or "").strip()
        if not byteplus_key:
            raise ValueError("BytePlus API key not configured. Go to Settings.")
        return run_byteplus_seedream_generation_job(payload, byteplus_key)

    if provider == "openai":
        openai_key = (config.get("openai_api_key", "") or "").strip()
        if not openai_key:
            raise ValueError("OpenAI API key not configured. Go to Settings.")
        if family == "gpt-image-2-edit":
            return run_openai_gpt_image_2_edit_job(payload, openai_key)
        if family == "gpt-image-2":
            return run_openai_gpt_image_2_generation_job(payload, openai_key)
        if model_id in OPENAI_GPT_IMAGE_25_MODELS:   # 2.5 previews: one model, edit when references are attached
            if payload.get("refImages"):
                return run_openai_gpt_image_2_edit_job(payload, openai_key)
            return run_openai_gpt_image_2_generation_job(payload, openai_key)
        raise ValueError("OpenAI serves the GPT Image models only.")

    raise ValueError("Unsupported provider")


def run_edit_job(body: dict, config: dict) -> dict:
    payload = normalize_generation_request(body)
    model_id = payload.get("model", FAL_NANO_BANANA_PRO_TEXT_ID)
    if model_id not in MODELS_INFO:
        raise ValueError("Invalid edit model")

    model_info = MODELS_INFO[model_id]
    provider = payload.get("provider") or model_info.get("provider", "fal")
    family = payload.get("modelFamily") or model_info.get("family", "")
    if provider != "fal":
        raise ValueError("Selective edit currently supports Fal image edit models only.")
    if family not in {"nano-banana", "nano-banana-pro", "nano-banana-2", "seedream-45", "seedream-5-lite", "gpt-image-2-edit"}:
        raise ValueError("Selective edit currently supports Nano Banana, Seedream, and GPT Image 2 Edit models only.")

    fal_key = (config.get("fal_api_key", "") or "").strip()
    if not fal_key:
        raise ValueError("Fal API key not configured. Go to Settings.")

    base_image_raw = body.get("baseImage") if isinstance(body.get("baseImage"), dict) else {}
    selection_image_raw = body.get("selectionImage") if isinstance(body.get("selectionImage"), dict) else {}
    global_ref_images_raw = body.get("globalRefImages") if isinstance(body.get("globalRefImages"), list) else []
    segment_ref_images_raw = body.get("segmentRefImages") if isinstance(body.get("segmentRefImages"), list) else []

    base_images = normalize_ref_image_payloads([base_image_raw], 1) if base_image_raw else []
    selection_images = normalize_ref_image_payloads([selection_image_raw], 1) if selection_image_raw else []
    if not base_images:
        raise ValueError("Edit source image is missing.")
    if not selection_images:
        raise ValueError("Segment selection image is missing.")

    global_ref_images = normalize_ref_image_payloads(global_ref_images_raw, 16)
    segment_ref_images = normalize_ref_image_payloads(segment_ref_images_raw, 16)

    max_ref = max(2, int(model_info.get("max_ref_images", 0) or 0))
    available_ref_slots = max(0, max_ref - 2)
    kept_segment_ref_images = segment_ref_images[:available_ref_slots]
    remaining_slots = max(0, available_ref_slots - len(kept_segment_ref_images))
    kept_global_ref_images = global_ref_images[:remaining_slots]

    edit_payload = dict(payload)
    edit_payload["refImages"] = (
        base_images
        + selection_images
        + kept_segment_ref_images
        + kept_global_ref_images
    )[:max_ref]
    edit_payload["numberOfImages"] = 1
    edit_payload["outputMode"] = "images_only"

    if family == "gpt-image-2-edit":
        result = run_fal_gpt_image_2_edit_job(edit_payload, fal_key)
    elif family.startswith("seedream"):
        result = run_fal_seedream_generation_job(edit_payload, fal_key)
    else:
        result = run_fal_nano_banana_generation_job(edit_payload, fal_key)

    result["_input_ref_images"] = kept_segment_ref_images + kept_global_ref_images
    params_meta = dict(result.get("params") or {})
    params_meta.update({
        "editMode": True,
        "editSourceKind": str(body.get("editSourceKind") or ""),
        "editSourceRelpath": str(body.get("editSourceRelpath") or ""),
        "editSelectionId": str(body.get("editSelectionId") or ""),
        "editSelectionName": str(body.get("editSelectionName") or ""),
        "editSelectionPrompt": str(body.get("editSelectionPrompt") or ""),
        "editSelectionDescribedPrompt": str(body.get("editSelectionDescribedPrompt") or ""),
        "editSelectionNotes": str(body.get("editSelectionNotes") or ""),
        "editPromptMode": str(body.get("editPromptMode") or ""),
        "editSelectionMaskRelpath": str(body.get("editSelectionMaskRelpath") or ""),
        "editSegmentRefCount": len(kept_segment_ref_images),
        "editGlobalRefCount": len(kept_global_ref_images),
        "editTotalRefCount": len(edit_payload["refImages"]),
        "ref_count": len(kept_segment_ref_images) + len(kept_global_ref_images),
    })
    result["params"] = params_meta
    return result


def run_video_job(body: dict, config: dict) -> dict:
    payload = normalize_video_request(body)
    provider = payload.get("provider", "kling")
    family = payload.get("modelFamily", "kling")

    if provider == "kling":
        kling_token = (config.get("kling_api_token", "") or "").strip()
        if not kling_token:
            raise ValueError("Kling API token not configured. Go to Settings.")
        return run_native_kling_video_job(payload, kling_token)

    if provider == "fal":
        fal_key = (config.get("fal_api_key", "") or "").strip()
        if not fal_key:
            raise ValueError("Fal API key not configured. Go to Settings.")
        if family == "seedvr-video":
            return run_fal_seedvr_video_job(payload, fal_key)
        if family == "wan-video":
            return run_fal_wan_video_job(payload, fal_key)
        if family == "ltx-video":
            return run_fal_ltx_video_job(payload, fal_key)
        model_info = VIDEO_MODELS_INFO.get(str(payload.get("model") or ""), {})
        if model_info.get("catalog_source") == "fal_schema":
            return run_fal_schema_video_job(payload, fal_key)
        if family == "seedance":
            return run_fal_seedance_video_job(payload, fal_key)
        if family == "kling":
            return run_fal_kling_video_job(payload, fal_key)
        return run_fal_schema_video_job(payload, fal_key)

    if provider == "luma":
        luma_key = (config.get("luma_api_key", "") or "").strip()
        if not luma_key:
            raise ValueError("Luma API key not configured. Go to Settings.")
        fal_key = (config.get("fal_api_key", "") or "").strip()
        return run_luma_video_job(payload, luma_key, fal_key)

    raise ValueError("Unsupported video provider")


@app.route("/api/generate", methods=["POST"])
@login_required
def api_generate():
    config = load_config()
    body = request.get_json(silent=True) or {}
    try:
        result = run_generation_job(body, config)
        persist_generation_result(result)
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)})
    except TimeoutError as exc:
        return jsonify({"ok": False, "error": str(exc)})
    except GenerationDebugError as exc:
        return jsonify({"ok": False, "error": str(exc), "debug": exc.debug})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@app.route("/api/jobs/generate", methods=["POST"])
@login_required
def api_generate_async():
    body = request.get_json(silent=True) or {}
    try:
        job = start_async_generate_job(body)
        return jsonify(job), 202
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/jobs/edit", methods=["POST"])
@login_required
def api_edit_async():
    body = request.get_json(silent=True) or {}
    try:
        job = start_async_edit_job(body)
        return jsonify(job), 202
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/jobs/video", methods=["POST"])
@login_required
def api_video_async():
    body = request.get_json(silent=True) or {}
    try:
        job = start_async_video_job(body)
        return jsonify(job), 202
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/upscale", methods=["POST"])
@login_required
def api_upscale():
    config = load_config()
    fal_key = (config.get("fal_api_key", "") or "").strip()
    if not fal_key:
        return jsonify({"ok": False, "error": "Fal API key not configured. Go to Settings."})

    body = request.get_json(silent=True) or {}
    try:
        result = run_fal_seedvr_upscale_job(body, fal_key)
        persist_generation_result(result)
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)})
    except TimeoutError as exc:
        return jsonify({"ok": False, "error": str(exc)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@app.route("/api/jobs/upscale", methods=["POST"])
@login_required
def api_upscale_async():
    body = request.get_json(silent=True) or {}
    try:
        job = start_async_upscale_job(body)
        return jsonify(job), 202
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/jobs/<job_id>", methods=["GET"])
@login_required
def api_async_job_status(job_id):
    job = get_async_job(str(job_id or "").strip())
    if not job:
        return jsonify({"ok": False, "error": "Job not found."}), 404
    return jsonify(job)


@app.route("/api/workbench/run", methods=["POST"])
@login_required
def api_workbench_run():
    config = load_config()

    body = request.get_json(silent=True) or {}
    run_uuid = str(body.get("run_uuid", "")).strip()
    task_run = get_task_run(run_uuid)
    if not task_run:
        return jsonify({"ok": False, "error": "Task run not found."}), 404

    plan = task_run.get("plan") or {}
    execution = dict(plan.get("execution_target") or {})
    payload = {
        "model": execution.get("model", task_run.get("recommended_model", "gemini-3.1-flash-image-preview")),
        "prompt": plan.get("prompt", task_run.get("prompt_text", "")),
        "aspectRatio": execution.get("aspectRatio", task_run.get("aspect_ratio", "1:1")),
        "imageSize": execution.get("imageSize", task_run.get("image_size", "1K")),
        "numberOfImages": int(body.get("numberOfImages", 1) or 1),
        "temperature": float(execution.get("temperature", 1.0)),
        "topP": float(execution.get("topP", 0.95)),
        "thinkingLevel": body.get("thinkingLevel", "Minimal"),
        "useSearch": bool(body.get("useSearch", False)),
        "outputMode": body.get("outputMode", "images_text"),
        "refImages": body.get("refImages", []),
    }
    for scope_key in ("assetClient", "assetProject", "assetShot", "assetFilename", "assetProjectId"):
        if scope_key in body:
            payload[scope_key] = body.get(scope_key)
    try:
        result = run_generation_job(payload, config)
        persist_generation_result(result)
        update_task_run_after_generation(run_uuid, generation_result=result)
        fresh_report = get_workbench_report()
        task_state = get_task_run(run_uuid)
        return jsonify({
            "ok": True,
            "result": result,
            "report": fresh_report,
            "task_run": task_state,
        })
    except Exception as exc:
        update_task_run_after_generation(run_uuid, generation_result=None, error=str(exc))
        return jsonify({"ok": False, "error": str(exc), "report": get_workbench_report()}), 500


@app.route("/api/generations")
@login_required
def api_generations():
    """
    Returns the last N saved generations for gallery reload on restart.
    Each item includes a local image URL plus prompt metadata.
    """
    MAX_LOAD = 100
    result = []
    for item in collect_generation_records(max_load=MAX_LOAD):
        params = item.get("params", {})
        result.append({
            "mime_type": params.get("mime_type", "image/png"),
            "url": item.get("url", ""),
            "generated_at": item.get("generated_at", ""),
            "text": item.get("text", ""),
            "gen_date": item.get("date", ""),
            "gen_filename": item.get("filename", ""),
            "gen_relpath": item.get("relpath", ""),
            "delete_url": item.get("delete_url", ""),
            "folder_open_payload": item.get("folder_open_payload", {}),
            "params": params,
        })
    return jsonify(result)


@app.route("/api/videos")
@login_required
def api_videos():
    max_load = 40
    result = []
    for item in collect_video_asset_records(max_load=max_load):
        result.append({
            "url": item.get("url", ""),
            "generated_at": item.get("generated_at", ""),
            "gen_date": item.get("date", ""),
            "gen_filename": item.get("filename", ""),
            "gen_relpath": item.get("relpath", ""),
            "mime_type": item.get("mime_type", "video/mp4"),
            "poster_url": item.get("poster_url", ""),
            "text": item.get("text", ""),
            "delete_url": item.get("delete_url", ""),
            "folder_open_payload": item.get("folder_open_payload", {}),
            "params": item.get("params", {}),
        })
    return jsonify(result)


@app.route("/api/videos/<path:asset_relpath>", methods=["DELETE"])
@login_required
def api_delete_video(asset_relpath):
    try:
        safe_relpath = resolve_asset_relpath(relpath=asset_relpath)
        safe_root, video_path, _ = resolve_asset_image_path("videos", relpath=safe_relpath)
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    filename = os.path.basename(video_path)
    if os.path.splitext(filename)[1].lower() not in (".mp4", ".webm", ".mov"):
        return jsonify({"ok": False, "error": "Invalid file type"}), 400

    try:
        base_path = os.path.splitext(video_path)[0]
        json_path = base_path + ".json"
        poster_path = base_path + "_poster.png"
        archived_refs = []

        if os.path.exists(json_path):
            try:
                with open(json_path, encoding="utf-8") as fh:
                    meta = json.load(fh)
                if isinstance(meta.get("videoSourceArchive"), dict):
                    archived_refs.append(meta.get("videoSourceArchive"))
                if isinstance(meta.get("videoRefArchive"), list):
                    archived_refs.extend([item for item in meta.get("videoRefArchive", []) if isinstance(item, dict)])
            except Exception:
                archived_refs = []

        if os.path.exists(video_path):
            os.remove(video_path)
        if os.path.exists(json_path):
            os.remove(json_path)
        if os.path.exists(poster_path):
            os.remove(poster_path)

        delete_reference_archive_entries(archived_refs)

        day_dir = os.path.dirname(video_path)
        while os.path.isdir(day_dir) and day_dir.startswith(safe_root + os.sep) and not os.listdir(day_dir):
            os.rmdir(day_dir)
            day_dir = os.path.dirname(day_dir)
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ---------------------------------------------------------------------------
# API Ã¢â‚¬â€ Delete a generation (image + sidecar JSON only, never loved/)
# ---------------------------------------------------------------------------
@app.route("/api/generations/<path:asset_relpath>", methods=["DELETE"])
@login_required
def api_delete_generation(asset_relpath):
    try:
        safe_relpath = resolve_asset_relpath(relpath=asset_relpath)
        safe_root, img_path, _ = resolve_asset_image_path("history", relpath=safe_relpath)
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    filename = os.path.basename(img_path)
    if os.path.splitext(filename)[1].lower() not in (".jpeg", ".jpg", ".png", ".webp"):
        return jsonify({"ok": False, "error": "Invalid file type"}), 400

    try:
        json_path = os.path.splitext(img_path)[0] + ".json"
        archived_refs = []
        if os.path.exists(json_path):
            try:
                with open(json_path, encoding="utf-8") as fh:
                    meta = json.load(fh)
                archived_refs = meta.get("refArchive", [])
            except Exception:
                archived_refs = []

        os.remove(img_path)
        if os.path.exists(json_path):
            os.remove(json_path)

        delete_reference_archive_entries(archived_refs)

        day_dir = os.path.dirname(img_path)
        while os.path.isdir(day_dir) and day_dir.startswith(safe_root + os.sep) and not os.listdir(day_dir):
            os.rmdir(day_dir)
            day_dir = os.path.dirname(day_dir)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# API Ã¢â‚¬â€ Publish (save to loved/)
# ---------------------------------------------------------------------------
@app.route("/api/publish", methods=["POST"])
@login_required
def api_publish():
    body      = request.get_json()
    img_b64   = body.get("data", "")
    mime_type = body.get("mime_type", "image/png")
    meta      = body.get("meta", {})

    if not img_b64:
        return jsonify({"ok": False, "error": "No image data provided"})

    now = datetime.now()

    try:
        img_b64, mime_type = convert_image_b64_to_png(img_b64, mime_type)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Image conversion error: {e}"})

    fallback_filename = sanitize_asset_filename_stem(
        meta.get("assetFilename", "") or meta.get("prompt", "") or meta.get("model_label", "") or now.strftime("%H%M%S"),
        fallback=now.strftime("%H%M%S"),
    )
    asset_meta = normalize_asset_metadata(meta, require_filename=True, fallback_filename=fallback_filename)
    meta.update(asset_meta)
    config = load_config()
    update_asset_metadata_memory(config, asset_meta)
    save_config(config)

    abs_dir, relpath, basename = build_asset_storage_paths(LOVED_DIR, asset_meta, "png")
    img_path = os.path.join(LOVED_DIR, relpath.replace("/", os.sep))
    meta_path = os.path.join(abs_dir, f"{basename}.json")

    img_bytes = base64.b64decode(img_b64)
    with open(img_path, "wb") as f:
        f.write(img_bytes)

    meta["mime_type"]    = mime_type
    meta["published_at"] = now.isoformat()
    meta["filename"]     = os.path.basename(img_path)
    meta["assetRelpath"] = relpath
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    return jsonify({
        "ok":      True,
        "date":    derive_asset_date_key(relpath, now.isoformat()),
        "file":    os.path.basename(img_path),
        "url":     f"/loved/{relpath}",
        "gallery": url_for("loved_gallery")
    })


# ---------------------------------------------------------------------------
# API - Task Workbench
# ---------------------------------------------------------------------------
@app.route("/api/workbench/templates")
@login_required
def api_workbench_templates():
    return jsonify(fetch_task_templates(project_type=str(request.args.get("project_type", "") or "").strip().lower()))


@app.route("/api/workbench/report")
@login_required
def api_workbench_report():
    return jsonify(get_workbench_report())




@app.route("/api/reports/overview")
@login_required
def api_reports_overview():
    return jsonify(get_workbench_report())
@app.route("/api/workbench/plan", methods=["POST"])
@login_required
def api_workbench_plan():
    body = request.get_json(silent=True) or {}
    try:
        plan = build_workbench_plan(body)
        save_task_run(plan)
        return jsonify({"ok": True, "plan": plan, "report": get_workbench_report()})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# API Ã¢â‚¬â€ Stats
# ---------------------------------------------------------------------------
@app.route("/api/stats")
@login_required
def api_stats():
    config = load_config()
    return jsonify(config.get("stats", DEFAULT_CONFIG["stats"]))


@app.route("/api/reset-stats", methods=["POST"])
@login_required
def api_reset_stats():
    config = load_config()
    config["stats"] = {
        "total_requests":  0,
        "total_images":    0,
        "total_cost_usd":  0.0,
        "requests_log":    [],
        "vision_calls":    0,
        "vision_cost_usd": 0.0,
        "vision_log":      [],
    }
    save_config(config)
    return jsonify({"ok": True})


@app.route("/api/models-info")
@login_required
def api_models_info():
    return jsonify(MODELS_INFO)


@app.route("/api/video-models-info")
@login_required
def api_video_models_info():
    return jsonify(VIDEO_MODELS_INFO)


@app.route("/api/provider-keys")
@login_required
def api_provider_keys():
    """Which provider keys are set (booleans only; never the keys)."""
    return jsonify({"ok": True, "providers": provider_key_state()})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    migrate_image_assets_layout()
    os.makedirs(LOVED_DIR, exist_ok=True)
    os.makedirs(GENERATIONS_DIR, exist_ok=True)
    os.makedirs(VIDEOS_DIR, exist_ok=True)
    os.makedirs(EDIT_SESSIONS_DIR, exist_ok=True)
    for _cfg in ELEMENT_TYPES.values():
        os.makedirs(os.path.join(ELEMENTS_DIR, _cfg["folder"]), exist_ok=True)
    os.makedirs(REFERENCE_ARCHIVE_DIR, exist_ok=True)
    os.makedirs(REFERENCE_MASKS_DIR, exist_ok=True)
    os.makedirs(REFERENCE_RENDERS_DIR, exist_ok=True)
    init_studio_db()
    # Migrate old published/ folder to loved/ if it exists and loved/ is empty
    _old_pub = os.path.join(BASE_DIR, "published")
    if os.path.isdir(_old_pub) and not os.listdir(LOVED_DIR):
        for item in os.listdir(_old_pub):
            src = os.path.join(_old_pub, item)
            dst = os.path.join(LOVED_DIR, item)
            if not os.path.exists(dst):
                shutil.copytree(src, dst) if os.path.isdir(src) else shutil.copy2(src, dst)
        print("  Migrated published/ -> loved/")
    print("\n" + "="*52)
    print(f"  Directors Studio {APP_VERSION}")
    print("  http://localhost:8000")
    print("  Login: admin / banana2024")
    print("  Max ref images: NB=0, Pro=8, NB2=14")
    print("="*52 + "\n")
    app.run(debug=True, port=8000)




