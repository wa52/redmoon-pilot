#!/usr/bin/env python3
"""Package the current RM-009..RM-018 local draft into versioned project folders."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STYLE_SPEC = ROOT / "production" / "styleframe_shots.json"
ANIMATIC_SPEC = ROOT / "production" / "animatic_shots.json"
WORKFLOW = ROOT / "workflows" / "z_image_styleframe.api.json"
GENERATED = ROOT / "assets" / "generated"
STYLEFRAME_DIR = GENERATED / "styleframes"
LOCAL_MANIFEST = GENERATED / "RM-009-RM-018_manifest_v01.json"
DESTINATIONS = [ROOT / "shots", ROOT / "animatic", ROOT / "audio" / "RM-009-RM-018-v01", ROOT / "reviews"]


class PackageError(RuntimeError):
    """Raised when the local prototype is incomplete or inconsistent."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    if not path.is_file():
        raise PackageError(f"Missing required input: {path.relative_to(ROOT)}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PackageError(f"Invalid JSON in {path.relative_to(ROOT)}: {exc}") from exc


def image_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise PackageError(f"Invalid PNG: {path.relative_to(ROOT)}")
    return struct.unpack(">II", data[16:24])


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Validate inputs and print the package plan without copying files.")
    parser.add_argument("--overwrite", action="store_true", help="Replace a previously packaged draft revision.")
    args = parser.parse_args()

    style_spec = read_json(STYLE_SPEC)
    animatic_spec = read_json(ANIMATIC_SPEC)
    local_manifest = read_json(LOCAL_MANIFEST)
    workflow = read_json(WORKFLOW)

    if local_manifest.get("status") not in {"ANIMATIC_DRAFT_NEEDS_REVIEW", "ANIMATIC_DRAFT_UNREVIEWED"}:
        raise PackageError("Unexpected manifest state; package is only for the current draft prototype.")
    if len(style_spec.get("shots", [])) != 10 or len(animatic_spec.get("shots", [])) != 10:
        raise PackageError("Expected exactly ten styleframes and ten animatic shots.")
    duration_total = sum(float(shot.get("duration_seconds", 0)) for shot in animatic_spec["shots"])
    if abs(duration_total - 36.0) > 0.001:
        raise PackageError(f"Expected a 36-second RM-009..RM-018 sequence; got {duration_total} seconds.")

    workflow_hash = sha256(WORKFLOW)
    models = {
        "unet": workflow["1"]["inputs"]["unet_name"],
        "text_encoder": workflow["2"]["inputs"]["clip_name"],
        "vae": workflow["3"]["inputs"]["vae_name"],
    }
    style_by_id = {shot["shot_id"]: shot for shot in style_spec["shots"]}
    animatic_by_id = {shot["shot_id"]: shot for shot in animatic_spec["shots"]}
    manifest_by_id = {shot["shot_id"]: shot for shot in local_manifest["inputs"]}
    if set(style_by_id) != set(animatic_by_id) or set(style_by_id) != set(manifest_by_id):
        raise PackageError("Shot IDs do not match between style prompts, animatic and render manifest.")

    planned_files: list[tuple[Path, Path]] = []
    shot_specs: list[tuple[Path, dict[str, Any]]] = []
    for shot_id, style_shot in style_by_id.items():
        generated_png = STYLEFRAME_DIR / f"{shot_id}.png"
        generated_sidecar = STYLEFRAME_DIR / f"{shot_id}.json"
        sidecar = read_json(generated_sidecar)
        manifest_item = manifest_by_id[shot_id]
        if not generated_png.is_file() or sha256(generated_png) != manifest_item["sha256"]:
            raise PackageError(f"Styleframe hash mismatch or missing image: {shot_id}")
        dims = image_dimensions(generated_png)
        if dims != (1024, 576):
            raise PackageError(f"Unexpected styleframe dimensions for {shot_id}: {dims}")
        if sidecar.get("prompt_id") is None or sidecar.get("workflow_sha256") != workflow_hash:
            raise PackageError(f"Missing ComfyUI provenance for {shot_id}")

        destination_dir = ROOT / "shots" / shot_id
        planned_files.append((generated_png, destination_dir / "styleframe_v01.png"))
        spec_record = {
            "shot_id": shot_id,
            "storyboard_revision": animatic_spec["storyboard_revision"],
            "source_anchor": style_shot["source_anchor"],
            "text_status": "TEXT_CONFIRMED_IN_COPY_SCENE; VISUAL_CREATIVE_PROPOSAL",
            "duration_seconds": animatic_by_id[shot_id]["duration_seconds"],
            "camera_motion": animatic_by_id[shot_id]["camera"],
            "audio_spec": animatic_by_id[shot_id],
            "styleframe": "styleframe_v01.png",
            "styleframe_sha256": manifest_item["sha256"],
            "production_status": "STYLEFRAME_ONLY; PER_SHOT_VIDEO_NOT_GENERATED",
            "review_status": "NEEDS_REVIEW",
        }
        generation_record = {
            "status": sidecar.get("comfy_status", {}).get("status_str", "unknown"),
            "prompt_id": sidecar["prompt_id"],
            "seed": sidecar["seed"],
            "prompt": sidecar["prompt"],
            "models": models,
            "dimensions": sidecar.get("dimensions"),
            "workflow": "../../workflows/z_image_styleframe.api.json",
            "workflow_sha256": workflow_hash,
            "output_sha256": manifest_item["sha256"],
            "local_endpoint_omitted_from_public_record": True,
        }
        shot_specs.append((destination_dir / "shot_spec.json", spec_record))
        shot_specs.append((destination_dir / "generation_v01.json", generation_record))

    generated_files = {
        "animatic": GENERATED / "RM-009-RM-018_animatic_v01.mp4",
        "contact_sheet": GENERATED / "RM-009-RM-018_contact_sheet_v01.png",
        "review_manifest": LOCAL_MANIFEST,
        "ambience": GENERATED / "animatic_ambience_v01.wav",
        "music": GENERATED / "animatic_music_v01.wav",
        "foley": GENERATED / "animatic_foley_v01.wav",
        "mix": GENERATED / "animatic_mix_v01.wav",
    }
    if not all(path.is_file() for path in generated_files.values()):
        missing = [str(path.relative_to(ROOT)) for path in generated_files.values() if not path.is_file()]
        raise PackageError("Missing animatic/audio deliverables:\n- " + "\n- ".join(missing))

    animatic_dest = ROOT / "animatic" / "RM-009-RM-018_v01.mp4"
    contact_dest = ROOT / "animatic" / "RM-009-RM-018_contact_sheet_v01.png"
    audio_dir = ROOT / "audio" / "RM-009-RM-018-v01"
    review_dest = ROOT / "reviews" / "RM-009-RM-018_v01.json"
    planned_files.extend([(generated_files["animatic"], animatic_dest), (generated_files["contact_sheet"], contact_dest)])
    for name in ("ambience", "music", "foley", "mix"):
        planned_files.append((generated_files[name], audio_dir / f"{name}_v01.wav"))

    review_payload = {
        "sequence_id": "RM-009-RM-018",
        "status": local_manifest["status"],
        "visual_review": local_manifest["visual_review"],
        "audio_review": local_manifest["audio_review"],
        "video": local_manifest["video"],
        "audio": local_manifest["audio"],
        "input_frame_hashes": [item["sha256"] for item in local_manifest["inputs"]],
        "source_archive_included": False,
        "generated_media_rights": "Procedural/locally-generated draft assets; adaptation and publication rights remain unverified.",
    }
    planned_paths = [str(destination.relative_to(ROOT)).replace("\\", "/") for _, destination in planned_files]
    planned_paths.extend(str(path.relative_to(ROOT)).replace("\\", "/") for path, _ in shot_specs)
    planned_paths.append(str(review_dest.relative_to(ROOT)).replace("\\", "/"))
    if not args.dry_run and not args.overwrite:
        existing = [path for path in planned_paths if (ROOT / path).exists()]
        if existing:
            raise PackageError("Packaged output already exists; pass --overwrite to replace:\n- " + "\n- ".join(existing))

    if args.dry_run:
        print(json.dumps({"files_to_copy": planned_paths, "shot_count": len(style_by_id), "model_files": models, "status": local_manifest["status"]}, ensure_ascii=False, indent=2))
        return 0

    for source, destination in planned_files:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    for path, payload in shot_specs:
        write_json(path, payload)
    write_json(review_dest, review_payload)
    print(json.dumps({"packaged_files": planned_paths, "shot_count": len(style_by_id), "review_status": local_manifest["status"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PackageError as exc:
        print(f"PACKAGE_PROTOTYPE_ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
