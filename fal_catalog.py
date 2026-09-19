"""
fal model catalog - fetch fal's public OpenAPI schema per endpoint and derive
the capability dict the app's video UI already understands.

No API key, no third-party dependencies. The public schema lives at:

    https://fal.ai/api/openapi/queue/openapi.json?endpoint_id=<url-encoded id>

Files (all in the app folder):
    fal_endpoints.json           - the maintained list. The only file a human edits.
    models_video.generated.json  - the fetched result. Regenerated on refresh.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

SCHEMA_URL = "https://fal.ai/api/openapi/queue/openapi.json?endpoint_id={endpoint}"
ENDPOINTS_FILENAME = "fal_endpoints.json"
GENERATED_FILENAME = "models_video.generated.json"
GENERATED_VERSION = 1

# Field-name candidates, in priority order. fal is not consistent across models.
START_IMAGE_FIELDS = ("start_image_url", "image_url", "first_frame_url")
END_IMAGE_FIELDS = ("end_image_url", "last_frame_url", "tail_image_url")
REFERENCE_IMAGE_FIELDS = ("image_urls", "reference_image_urls", "images_list")
REFERENCE_VIDEO_FIELDS = ("video_urls", "reference_video_urls")
REFERENCE_AUDIO_FIELDS = ("audio_urls",)
SOURCE_VIDEO_FIELDS = ("video_url",)
DRIVING_AUDIO_FIELDS = ("audio_url",)

# Keys ignored when diffing old vs new generated entries.
DIFF_IGNORE_KEYS = {"schema_fetched_at"}


class CatalogFetchError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------
def schema_url(endpoint_id: str) -> str:
    return SCHEMA_URL.format(endpoint=urllib.parse.quote(endpoint_id.strip(), safe=""))


def fetch_openapi(endpoint_id: str, timeout: float = 25.0) -> dict:
    endpoint_id = str(endpoint_id or "").strip()
    if not endpoint_id:
        raise CatalogFetchError("Empty endpoint id.")
    req = urllib.request.Request(
        schema_url(endpoint_id),
        headers={"User-Agent": "AI-API-Studio-catalog/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise CatalogFetchError(f"HTTP {exc.code} fetching schema for {endpoint_id}") from exc
    except Exception as exc:
        raise CatalogFetchError(f"Network error fetching schema for {endpoint_id}: {exc}") from exc
    try:
        doc = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise CatalogFetchError(f"Schema for {endpoint_id} is not valid JSON") from exc
    if not isinstance(doc, dict) or "components" not in doc:
        raise CatalogFetchError(f"Schema for {endpoint_id} has no components (unknown endpoint?)")
    return doc


# ---------------------------------------------------------------------------
# Schema parsing
# ---------------------------------------------------------------------------
def _resolve_ref(components: dict, ref: str) -> dict:
    # "#/components/schemas/Name"
    name = str(ref or "").rsplit("/", 1)[-1]
    return dict((components.get("schemas") or {}).get(name) or {})


def _unwrap(prop: dict) -> tuple[dict, bool]:
    """Collapse anyOf/oneOf [X, null] into (X, nullable)."""
    if not isinstance(prop, dict):
        return {}, False
    variants = prop.get("anyOf") or prop.get("oneOf")
    if not isinstance(variants, list):
        return prop, False
    nullable = any(isinstance(v, dict) and v.get("type") == "null" for v in variants)
    real = [v for v in variants if isinstance(v, dict) and v.get("type") != "null"]
    if not real:
        return prop, nullable
    merged = dict(real[0])
    # keep outer description/default/title
    for key in ("description", "default", "title"):
        if key in prop and key not in merged:
            merged[key] = prop[key]
    return merged, nullable


def extract_input_schema(doc: dict) -> tuple[str, dict, dict]:
    components = doc.get("components") or {}
    schemas = components.get("schemas") or {}
    # Prefer the schema referenced by the POST request body; fall back to *Input.
    for _path, methods in (doc.get("paths") or {}).items():
        post = (methods or {}).get("post") or {}
        content = ((post.get("requestBody") or {}).get("content") or {}).get("application/json") or {}
        ref = (content.get("schema") or {}).get("$ref")
        if ref:
            name = ref.rsplit("/", 1)[-1]
            if name in schemas:
                return name, schemas[name], components
    for name, schema in schemas.items():
        if name.endswith("Input"):
            return name, schema, components
    raise CatalogFetchError("Could not locate the input schema in the OpenAPI document.")


def analyze_fields(schema: dict, components: dict) -> dict[str, dict]:
    """Return {field_name: descriptor} for the top-level input properties."""
    required = set(schema.get("required") or [])
    fields: dict[str, dict] = {}
    for name, raw in (schema.get("properties") or {}).items():
        prop, nullable = _unwrap(raw)
        if "$ref" in prop:
            prop = {**_resolve_ref(components, prop["$ref"]), **{k: v for k, v in prop.items() if k != "$ref"}}
        items = prop.get("items") if isinstance(prop.get("items"), dict) else {}
        items_ref = items.get("$ref") if items else None
        item_ui = (items.get("_fal_ui_field") or (items.get("ui") or {}).get("field")) if items else None
        xfal = prop.get("x-fal") if isinstance(prop.get("x-fal"), dict) else (items.get("x-fal") if items and isinstance(items.get("x-fal"), dict) else None)
        fields[name] = {
            "constraints": {k: v for k, v in (xfal or {}).items() if k != "timeout"} or None,
            "max_length": prop.get("maxLength"),
            "type": prop.get("type"),
            "enum": list(prop.get("enum") or []),
            "default": prop.get("default"),
            "required": name in required,
            "nullable": nullable,
            "max_items": prop.get("maxItems"),
            "min_items": prop.get("minItems"),
            "minimum": prop.get("minimum"),
            "maximum": prop.get("maximum"),
            "items_type": items.get("type") if items else None,
            "items_ref": items_ref.rsplit("/", 1)[-1] if items_ref else None,
            "ui_field": prop.get("_fal_ui_field") or (prop.get("ui") or {}).get("field") or item_ui,
            "description": str(prop.get("description") or ""),
        }
    return fields


# "up to 30 images" / "Maximum 4 total" - but not "Max 20 MB each" or "up to 30 seconds"
_UP_TO_RE = re.compile(r"(?:up to|maximum(?: of)?|max(?:imum)?\.?|at most)\s+(\d{1,3})(?!\s*(?:MB|GB|KB|px|MP|seconds?|secs?|s\b|fps|%))\b", re.I)
_TOKEN_RE = re.compile(r"(@|\[)(Image|Element|Video|Audio)1(\]?)")


def _max_items(desc: dict, default: int) -> int:
    if isinstance(desc.get("max_items"), int) and desc["max_items"] > 0:
        return int(desc["max_items"])
    m = _UP_TO_RE.search(desc.get("description") or "")
    if m:
        return int(m.group(1))
    return default


def _prompt_token(desc: dict, fallback_kind: str) -> str:
    m = _TOKEN_RE.search(desc.get("description") or "")
    if m:
        open_char, kind, close_char = m.groups()
        return f"{open_char}{kind}{{n}}{close_char}"
    return f"@{fallback_kind}{{n}}"


def _duration_info(desc: dict | None) -> tuple[list[int], str, bool]:
    """Return (durations, format, allows_auto). format: int | string | seconds_suffix."""
    if not desc:
        return [], "int", False
    values = desc.get("enum") or []
    allows_auto = any(str(v).strip().lower() == "auto" for v in values)
    durations: list[int] = []
    fmt = "int" if desc.get("type") == "integer" else "string"
    for v in values:
        s = str(v).strip().lower()
        if s == "auto":
            continue
        if s.endswith("s") and s[:-1].isdigit():
            fmt = "seconds_suffix"
            durations.append(int(s[:-1]))
        elif s.isdigit():
            durations.append(int(s))
    if not values:
        # Free integer duration: use min/max if present, else a sensible default.
        lo = desc.get("minimum")
        hi = desc.get("maximum")
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and hi - lo <= 60:
            durations = list(range(int(lo), int(hi) + 1))
        else:
            durations = [5, 10]
    return sorted(set(durations)), fmt, allows_auto


def _first_present(fields: dict, names: tuple[str, ...]) -> str:
    for n in names:
        if n in fields:
            return n
    return ""


def infer_video_mode_kind(endpoint_id: str) -> str:
    e = endpoint_id.lower()
    if "first-last-frame" in e or "first_last" in e:
        return "first_last_frame_to_video"
    if "reference-to-video" in e or e.endswith("/elements"):
        return "reference_to_video"
    if "video-to-video" in e or "edit-video" in e or e.endswith("/edit"):
        return "video_edit"
    if "extend" in e or "continue" in e:
        return "video_continuation"
    if "image-to-video" in e:
        return "image_to_video"
    if "upscale" in e:
        return "video_upscale"
    return "text_to_video"


def infer_input_modes(endpoint_id: str) -> list[str]:
    kind = infer_video_mode_kind(endpoint_id)
    if kind == "reference_to_video":
        return ["reference"]
    if kind in ("video_edit", "video_continuation", "video_upscale"):
        return ["video"]
    if kind in ("image_to_video", "first_last_frame_to_video"):
        return ["image"]
    # A text route that also happens to accept a start image stays text-only:
    # fal publishes separate i2v routes and the UI keys off the route.
    return ["text"]


def _image_constraints(fields: dict, *names: str) -> dict:
    out = {}
    for n in names:
        if n and n in fields and fields[n].get("constraints"):
            out[n] = dict(fields[n]["constraints"])
    return out


def derive_element_schema(fields: dict, components: dict | None) -> dict | None:
    """
    Nested element arrays (Kling v3 `elements`): each item is an object with a
    frontal image, 1-3 angle references, or a video. Returns the shape the UI needs.
    """
    desc = fields.get("elements")
    if not desc or not components:
        return None
    ref_name = desc.get("items_ref")
    if not ref_name:
        return None
    sub = (components.get("schemas") or {}).get(ref_name) or {}
    sub_fields = analyze_fields(sub, components)
    frontal = _first_present(sub_fields, ("frontal_image_url", "image_url"))
    refs = _first_present(sub_fields, ("reference_image_urls", "image_urls"))
    video = _first_present(sub_fields, ("video_url",))
    voice = _first_present(sub_fields, ("voice_id",))
    refs_desc = sub_fields.get(refs, {}) if refs else {}
    m = re.search(r"(\d)\s*-\s*(\d)\s+images", refs_desc.get("description") or "")
    refs_min, refs_max = (int(m.group(1)), int(m.group(2))) if m else (1, _max_items(refs_desc, 3) if refs else 0)
    return {
        "schema_name": ref_name,
        "frontal_field": frontal,
        "references_field": refs,
        "references_min": refs_min,
        "references_max": refs_max,
        "video_field": video,
        "voice_field": voice,
        "image_constraints": _image_constraints(sub_fields, frontal, refs),
        "video_constraints": dict(sub_fields[video]["constraints"]) if video and sub_fields[video].get("constraints") else None,
    }


def derive_capabilities(endpoint_id: str, fields: dict[str, dict], components: dict | None = None) -> dict:
    """Map analyzed schema fields onto the app's model_info keys."""
    kind = infer_video_mode_kind(endpoint_id)
    caps: dict = {
        "fal_endpoint": endpoint_id,
        "schema_fields": sorted(fields.keys()),
        "schema_required": sorted(n for n, d in fields.items() if d["required"]),
    }

    prompt = fields.get("prompt")
    caps["supports_prompt"] = prompt is not None
    caps["prompt_required"] = bool(prompt and prompt["required"])
    caps["prompt_max_length"] = int(prompt["max_length"]) if prompt and prompt.get("max_length") else 0
    neg = fields.get("negative_prompt")
    caps["supports_negative_prompt"] = neg is not None
    caps["negative_prompt_default"] = str(neg.get("default") or "") if neg else ""
    caps["negative_prompt_max_length"] = int(neg["max_length"]) if neg and neg.get("max_length") else 0

    durations, fmt, allows_auto = _duration_info(fields.get("duration"))
    caps["supports_duration"] = "duration" in fields
    caps["durations"] = durations
    caps["duration_format"] = fmt
    caps["duration_allows_auto"] = allows_auto

    ar = fields.get("aspect_ratio")
    caps["supports_aspect_ratio"] = ar is not None
    caps["aspect_ratios"] = [str(v) for v in (ar or {}).get("enum") or []] if ar else []

    res = fields.get("resolution")
    caps["supports_resolution"] = res is not None
    caps["resolutions"] = [str(v) for v in (res or {}).get("enum") or []] if res else []

    start_field = _first_present(fields, START_IMAGE_FIELDS)
    # A bare text-to-video route never takes a start image even if the schema
    # happens to expose one; the UI keys the slot off input mode anyway.
    caps["supports_start_image"] = bool(start_field) and kind != "text_to_video"
    caps["start_image_field"] = start_field or "image_url"
    caps["start_image_required"] = bool(start_field and fields[start_field]["required"])

    end_field = _first_present(fields, END_IMAGE_FIELDS)
    caps["supports_end_image"] = bool(end_field)
    caps["end_image_field"] = end_field

    ref_field = _first_present(fields, REFERENCE_IMAGE_FIELDS)
    caps["supports_reference_images"] = bool(ref_field)
    caps["reference_images_field"] = ref_field or "image_urls"
    caps["reference_images_required"] = bool(ref_field and fields[ref_field]["required"])
    caps["max_reference_images"] = _max_items(fields[ref_field], 9) if ref_field else 0
    caps["reference_prompt_token"] = _prompt_token(fields[ref_field], "Image") if ref_field else ""

    ref_video_field = _first_present(fields, REFERENCE_VIDEO_FIELDS)
    caps["supports_reference_videos"] = bool(ref_video_field)
    caps["reference_videos_field"] = ref_video_field
    caps["max_reference_videos"] = _max_items(fields[ref_video_field], 3) if ref_video_field else 0

    ref_audio_field = _first_present(fields, REFERENCE_AUDIO_FIELDS)
    caps["supports_reference_audio"] = bool(ref_audio_field)
    caps["reference_audio_field"] = ref_audio_field
    caps["max_reference_audio"] = _max_items(fields[ref_audio_field], 3) if ref_audio_field else 0

    src_video_field = _first_present(fields, SOURCE_VIDEO_FIELDS)
    caps["supports_source_video"] = bool(src_video_field) and kind in ("video_edit", "video_continuation", "video_upscale")
    caps["source_video_field"] = src_video_field or "video_url"
    caps["source_video_required"] = bool(caps["supports_source_video"] and fields[src_video_field]["required"])

    audio_field = _first_present(fields, DRIVING_AUDIO_FIELDS)
    caps["supports_driving_audio"] = bool(audio_field)
    caps["driving_audio_field"] = audio_field

    ga = fields.get("generate_audio")
    caps["supports_generate_audio"] = ga is not None
    caps["generate_audio_default"] = bool(ga["default"]) if ga and ga.get("default") is not None else False

    caps["supports_safety_checker"] = "enable_safety_checker" in fields
    caps["supports_multi_prompt"] = "multi_prompt" in fields
    caps["supports_seed"] = "seed" in fields

    cfg = fields.get("cfg_scale")
    caps["supports_cfg_scale"] = cfg is not None
    caps["cfg_scale_default"] = float(cfg["default"]) if cfg and cfg.get("default") is not None else 0.5
    caps["cfg_scale_min"] = float(cfg["minimum"]) if cfg and cfg.get("minimum") is not None else 0.0
    caps["cfg_scale_max"] = float(cfg["maximum"]) if cfg and cfg.get("maximum") is not None else 1.0

    shot = fields.get("shot_type")
    caps["supports_shot_type"] = shot is not None
    caps["shot_type_options"] = [str(v) for v in (shot or {}).get("enum") or []] if shot else []
    caps["shot_type_default"] = str(shot.get("default") or "") if shot else ""

    # Nested element arrays (characters / objects built from several images or a video).
    elements = fields.get("elements")
    caps["supports_elements"] = elements is not None
    caps["elements_field"] = "elements" if elements else ""
    caps["elements_prompt_token"] = _prompt_token(elements, "Element") if elements else ""
    caps["max_elements"] = _max_items(elements, 4) if elements else 0
    caps["element_schema"] = derive_element_schema(fields, components) if elements else None

    # Per-image limits fal declares (min size, max bytes, aspect range) so the UI can reject early.
    caps["image_constraints"] = _image_constraints(fields, start_field, end_field, ref_field)
    return caps


# ---------------------------------------------------------------------------
# Entry building
# ---------------------------------------------------------------------------
def build_entry(cfg: dict, doc: dict, base: dict | None = None) -> dict:
    """
    cfg  - one row of fal_endpoints.json
    doc  - the fetched OpenAPI document
    base - the hardcoded spec for the same id, if any (keeps hand-tuned keys)

    Precedence, lowest to highest: base -> schema-derived -> cfg overrides.
    """
    endpoint_id = str(cfg.get("endpoint") or cfg.get("id") or "").strip()
    _, schema, components = extract_input_schema(doc)
    fields = analyze_fields(schema, components)
    derived = derive_capabilities(endpoint_id, fields, components)

    entry: dict = dict(base or {})
    entry.update({
        "id": str(cfg.get("id") or endpoint_id),
        "provider": "fal",
        "provider_label": "Fal",
        "family": str(cfg.get("family") or entry.get("family") or "fal-video"),
        "label": str(cfg.get("label") or entry.get("label") or endpoint_id),
        "sort_order": int(cfg.get("sort_order", entry.get("sort_order", 999))),
        "video_mode_kind": str(cfg.get("video_mode_kind") or infer_video_mode_kind(endpoint_id)),
        "input_modes": list(cfg.get("input_modes") or entry.get("input_modes") or infer_input_modes(endpoint_id)),
    })
    entry.update(derived)
    overrides = cfg.get("overrides") or {}
    if isinstance(overrides, dict):
        entry.update(overrides)
    entry["catalog_source"] = "fal_schema"
    entry["schema_fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return entry


def probe_endpoint(endpoint_id: str, cfg: dict | None = None, base: dict | None = None, timeout: float = 25.0) -> dict:
    """Fetch one endpoint and return its derived entry (no files touched)."""
    row = dict(cfg or {})
    row.setdefault("endpoint", endpoint_id)
    doc = fetch_openapi(endpoint_id, timeout=timeout)
    return build_entry(row, doc, base=base)


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------
def load_json(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def load_endpoints(path: str) -> dict:
    data = load_json(path) or {}
    data.setdefault("families", {})
    data.setdefault("endpoints", [])
    if not isinstance(data["endpoints"], list):
        data["endpoints"] = []
    if not isinstance(data["families"], dict):
        data["families"] = {}
    return data


def load_generated(path: str) -> dict | None:
    data = load_json(path)
    if not data or not isinstance(data.get("models"), dict):
        return None
    return data


def write_json_atomic(path: str, data: dict) -> None:
    """Write to a temp file in the same folder, then os.replace - never a partial file."""
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------
def diff_entries(old: dict | None, new: dict) -> list[str]:
    if not old:
        return []
    changed = []
    for key in sorted(set(old.keys()) | set(new.keys())):
        if key in DIFF_IGNORE_KEYS:
            continue
        if old.get(key) != new.get(key):
            changed.append(key)
    return changed


def fetch_all(
    endpoint_cfgs: list[dict],
    base_specs: dict | None = None,
    previous: dict | None = None,
    max_workers: int = 4,
    timeout: float = 25.0,
) -> tuple[dict, list[dict]]:
    """
    Fetch every endpoint concurrently (bounded). Returns (models, report).
    models: {id: entry} for every successful fetch.
    report: one row per endpoint - {id, endpoint, label, status, changed_fields?, error?}
    """
    base_specs = base_specs or {}
    previous = previous or {}
    models: dict = {}
    report: list[dict] = []

    def _one(cfg: dict) -> dict:
        endpoint_id = str(cfg.get("endpoint") or cfg.get("id") or "").strip()
        model_id = str(cfg.get("id") or endpoint_id)
        doc = fetch_openapi(endpoint_id, timeout=timeout)
        return build_entry(cfg, doc, base=base_specs.get(model_id))

    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as pool:
        futures = {pool.submit(_one, cfg): cfg for cfg in endpoint_cfgs}
        for fut in as_completed(futures):
            cfg = futures[fut]
            endpoint_id = str(cfg.get("endpoint") or cfg.get("id") or "").strip()
            model_id = str(cfg.get("id") or endpoint_id)
            row = {"id": model_id, "endpoint": endpoint_id, "label": str(cfg.get("label") or model_id)}
            try:
                entry = fut.result()
            except Exception as exc:
                row.update({"status": "failed", "error": str(exc)})
                report.append(row)
                continue
            models[model_id] = entry
            old = previous.get(model_id)
            if old is None:
                row["status"] = "added"
            else:
                changed = diff_entries(old, entry)
                row["status"] = "changed" if changed else "unchanged"
                row["changed_fields"] = changed
            report.append(row)

    order = {str(c.get("id") or c.get("endpoint")): i for i, c in enumerate(endpoint_cfgs)}
    report.sort(key=lambda r: order.get(r["id"], 10**6))
    return models, report


def build_generated_document(models: dict, families: dict | None = None) -> dict:
    return {
        "version": GENERATED_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "https://fal.ai/api/openapi/queue/openapi.json",
        "families": dict(families or {}),
        "models": dict(models),
    }


if __name__ == "__main__":  # quick manual probe: python fal_catalog.py <endpoint id>
    import sys
    for arg in sys.argv[1:]:
        print(json.dumps(probe_endpoint(arg), indent=2))
