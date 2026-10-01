#!/usr/bin/env python3
"""Render storyboard style frames through the locally configured ComfyUI API.

The script is intentionally limited to still images. It uses only Python's
standard library; the selected ComfyUI install supplies the model/runtime.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = PROJECT_ROOT / "production" / "styleframe_shots.json"
WORKFLOW_PATH = PROJECT_ROOT / "workflows" / "z_image_styleframe.api.json"
OUTPUT_DIR = PROJECT_ROOT / "assets" / "generated" / "styleframes"
MIN_FREE_VRAM_BYTES = 4 * 1024**3
DEFAULT_TIMEOUT_SECONDS = 1200
POLL_INTERVAL_SECONDS = 2


class RenderError(RuntimeError):
    """Raised when a render preflight or ComfyUI job fails."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RenderError(f"Required project file is missing: {path.relative_to(PROJECT_ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise RenderError(f"Invalid JSON in {path.relative_to(PROJECT_ROOT)}: {exc}") from exc


def api_request(base_url: str, route: str, body: dict[str, Any] | None = None, timeout: int = 30) -> Any:
    url = f"{base_url}{route}"
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if data is None else "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RenderError(f"ComfyUI returned HTTP {exc.code} for {route}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RenderError(f"Cannot reach ComfyUI at {base_url}: {exc}") from exc
    if route == "/view" or route.startswith("/view?"):
        return payload
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RenderError(f"ComfyUI returned non-JSON data for {route}") from exc


def _option_list(node_info: dict[str, Any], field: str) -> set[str]:
    required = node_info.get("input", {}).get("required", {})
    value = required.get(field)
    if not isinstance(value, list) or not value:
        return set()
    options = value[0]
    if isinstance(options, list):
        return {str(option) for option in options}
    return set()


def check_queue_empty(base_url: str) -> None:
    queue = api_request(base_url, "/queue")
    running = queue.get("queue_running", [])
    pending = queue.get("queue_pending", [])
    if running or pending:
        raise RenderError(
            f"ComfyUI queue is busy ({len(running)} running, {len(pending)} pending); "
            "wait for the existing job instead of inserting this render."
        )


def check_comfy(base_url: str, workflow: dict[str, Any]) -> dict[str, Any]:
    system = api_request(base_url, "/system_stats")
    check_queue_empty(base_url)

    devices = system.get("devices", [])
    gpu_devices = [device for device in devices if device.get("type") == "cuda" or "nvidia" in device.get("name", "").lower()]
    if not gpu_devices:
        raise RenderError("ComfyUI did not report an available NVIDIA/CUDA device; refusing to guess local render capacity.")
    free_bytes = max(int(device.get("vram_free", 0)) for device in gpu_devices)
    if free_bytes < MIN_FREE_VRAM_BYTES:
        raise RenderError(
            f"Only {free_bytes / 1024**3:.2f} GiB VRAM is free; this still-image preset requires at least "
            f"{MIN_FREE_VRAM_BYTES / 1024**3:.0f} GiB free. Free memory and retry."
        )

    object_info = api_request(base_url, "/object_info")
    required_classes = {node.get("class_type") for node in workflow.values()}
    missing_nodes = sorted(node for node in required_classes if node not in object_info)
    if missing_nodes:
        raise RenderError("Missing workflow node classes: " + ", ".join(missing_nodes))

    model_bindings = (("1", "unet_name"), ("2", "clip_name"), ("3", "vae_name"))
    for node_id, field in model_bindings:
        node = workflow[node_id]
        model_name = node["inputs"][field]
        options = _option_list(object_info[node["class_type"]], field)
        if options and model_name not in options:
            raise RenderError(
                f"Workflow model {model_name!r} is not listed by {node['class_type']}.{field}. "
                "Install it on the ComfyUI host or choose an installed compatible model."
            )

    return {"vram_free_bytes": free_bytes, "device_count": len(gpu_devices), "queue_empty": True}


def png_dimensions(image: bytes) -> tuple[int, int]:
    if len(image) < 24 or image[:8] != b"\x89PNG\r\n\x1a\n" or image[12:16] != b"IHDR":
        raise RenderError("ComfyUI output is not a valid PNG image.")
    return struct.unpack(">II", image[16:24])


def image_from_history(base_url: str, prompt_id: str, timeout_seconds: int) -> tuple[bytes, dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    last_history: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        response = api_request(base_url, f"/history/{urllib.parse.quote(prompt_id)}")
        history = response.get(prompt_id)
        if history:
            last_history = history
            status = history.get("status", {})
            status_text = status.get("status_str", "")
            if status_text == "error" or status.get("completed") is False:
                if status_text == "error":
                    raise RenderError(f"ComfyUI job {prompt_id} failed: {json.dumps(status, ensure_ascii=False)}")
            outputs = history.get("outputs", {}).get("10", {}).get("images", [])
            if outputs:
                image_info = outputs[0]
                query = urllib.parse.urlencode(image_info)
                image_data = api_request(base_url, f"/view?{query}")
                return image_data, history
        time.sleep(POLL_INTERVAL_SECONDS)

    status = None if last_history is None else last_history.get("status")
    raise TimeoutError(f"Timed out waiting for ComfyUI job {prompt_id}; last status: {status}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shot", action="append", dest="shots", help="Render one shot ID; repeat to select multiple. Default: all listed shots.")
    parser.add_argument("--dry-run", action="store_true", help="Print resolved prompts and model/workflow settings without contacting ComfyUI.")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing local style frame for a selected shot.")
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args()

    if sys.version_info < (3, 10):
        raise RenderError("Python 3.10 or newer is required.")

    spec = read_json(SPEC_PATH)
    workflow_template = read_json(WORKFLOW_PATH)
    workflow_hash = sha256(WORKFLOW_PATH.read_bytes())
    all_shots = spec.get("shots", [])
    if not all_shots:
        raise RenderError("The style-frame shot specification is empty.")

    by_id = {shot["shot_id"]: shot for shot in all_shots}
    selected_ids = args.shots or list(by_id)
    unknown = sorted(set(selected_ids) - set(by_id))
    if unknown:
        raise RenderError("Unknown shot IDs: " + ", ".join(unknown))
    bindings = spec["workflow_bindings"]
    context = {"style": spec["shared_style"], **spec["shared_character_drafts"]}

    if args.dry_run:
        print(json.dumps({
            "workflow": WORKFLOW_PATH.relative_to(PROJECT_ROOT).as_posix(),
            "workflow_sha256": workflow_hash,
            "visual_status": spec["visual_status"],
            "shots": [{"shot_id": shot_id, "seed": by_id[shot_id]["seed"], "prompt": f"{spec['shared_style']}. {by_id[shot_id]['prompt'].format_map(context)}"} for shot_id in selected_ids],
        }, ensure_ascii=False, indent=2))
        return 0

    api_url = os.environ.get("COMFYUI_API_URL", "").strip().rstrip("/")
    if not api_url:
        raise RenderError("Set COMFYUI_API_URL to the locally managed ComfyUI API, e.g. http://127.0.0.1:8190.")
    parsed_url = urllib.parse.urlparse(api_url)
    if parsed_url.scheme not in {"http", "https"} or parsed_url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RenderError("This prototype only accepts a loopback ComfyUI API URL; remote rendering is not enabled here.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    pending_ids = [shot_id for shot_id in selected_ids if args.overwrite or not (OUTPUT_DIR / f"{shot_id}.png").exists()]
    if not pending_ids:
        print("All selected style frames already exist; nothing to render.", flush=True)
        return 0

    preflight = check_comfy(api_url, workflow_template)
    print(f"ComfyUI ready; {preflight['vram_free_bytes'] / 1024**3:.2f} GiB VRAM free; queue empty; rendering {len(pending_ids)} still-image frame(s).", flush=True)

    for shot_id in pending_ids:
        scene = by_id[shot_id]
        output_path = OUTPUT_DIR / f"{shot_id}.png"
        metadata_path = OUTPUT_DIR / f"{shot_id}.json"
        check_queue_empty(api_url)
        graph = copy.deepcopy(workflow_template)
        prompt = f"{spec['shared_style']}. {scene['prompt'].format_map(context)}"
        graph[bindings["prompt"]["node"]]["inputs"][bindings["prompt"]["input"]] = prompt
        graph[bindings["width"]["node"]]["inputs"][bindings["width"]["input"]] = int(spec.get("width", 1024))
        graph[bindings["height"]["node"]]["inputs"][bindings["height"]["input"]] = int(spec.get("height", 576))
        graph[bindings["seed"]["node"]]["inputs"][bindings["seed"]["input"]] = int(scene["seed"])
        graph[bindings["filename_prefix"]["node"]]["inputs"][bindings["filename_prefix"]["input"]] = f"redmoon_pilot/styleframes/{shot_id}"

        result = api_request(api_url, "/prompt", {"prompt": graph, "client_id": "redmoon-pilot-styleframes"}, timeout=60)
        prompt_id = result.get("prompt_id")
        if not prompt_id:
            raise RenderError(f"ComfyUI did not return a prompt_id for {shot_id}: {json.dumps(result, ensure_ascii=False)}")
        print(f"Submitted {shot_id}: {prompt_id}", flush=True)

        image_data, history = image_from_history(api_url, prompt_id, args.timeout_seconds)
        width, height = png_dimensions(image_data)
        expected_size = (int(spec.get("width", 1024)), int(spec.get("height", 576)))
        if (width, height) != expected_size:
            raise RenderError(f"{shot_id} expected {expected_size}, received {(width, height)}")

        output_path.write_bytes(image_data)
        metadata = {
            "shot_id": shot_id,
            "storyboard_revision": spec["storyboard_revision"],
            "visual_status": spec["visual_status"],
            "source_anchor": scene["source_anchor"],
            "prompt": prompt,
            "seed": scene["seed"],
            "workflow": WORKFLOW_PATH.relative_to(PROJECT_ROOT).as_posix(),
            "workflow_sha256": workflow_hash,
            "comfyui_api_url": api_url,
            "prompt_id": prompt_id,
            "output": output_path.relative_to(PROJECT_ROOT).as_posix(),
            "output_sha256": sha256(image_data),
            "dimensions": [width, height],
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "comfy_status": history.get("status", {}),
            "review_status": "UNREVIEWED",
        }
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Saved {output_path.relative_to(PROJECT_ROOT)} ({width}x{height}, sha256 {metadata['output_sha256']})", flush=True)

    print(f"Style-frame batch complete. Review files under {OUTPUT_DIR.relative_to(PROJECT_ROOT).as_posix()} before using them as references.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RenderError, TimeoutError) as exc:
        print(f"STYLEFRAME_RENDER_ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
