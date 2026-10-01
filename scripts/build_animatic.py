#!/usr/bin/env python3
"""Build a CPU Ken-Burns animatic and procedural audio sketch from approved stills."""

from __future__ import annotations

import argparse
import array
import hashlib
import json
import math
import os
import random
import struct
import subprocess
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageDraw, ImageOps
except ImportError as exc:  # Clear startup dependency check for the CLI.
    raise SystemExit("Missing dependency: Pillow. Install with `python -m pip install Pillow`.") from exc


ROOT = Path(__file__).resolve().parents[1]
SHOT_SPEC = ROOT / "production" / "animatic_shots.json"
FRAME_DIR = ROOT / "assets" / "generated" / "styleframes"
OUTPUT_DIR = ROOT / "assets" / "generated"
SAMPLE_RATE = 48000


class BuildError(RuntimeError):
    """Raised when local animatic inputs or the encoder are unavailable."""


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BuildError(f"Missing project file: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise BuildError(f"Invalid JSON in {path.relative_to(ROOT)}: {exc}") from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_inputs(spec: dict[str, Any]) -> list[tuple[dict[str, Any], Path, Image.Image]]:
    loaded = []
    missing = []
    for shot in spec.get("shots", []):
        image_path = FRAME_DIR / f"{shot['shot_id']}.png"
        if not image_path.is_file():
            missing.append(image_path.relative_to(ROOT).as_posix())
            continue
        try:
            with Image.open(image_path) as source:
                image = source.convert("RGB")
        except OSError as exc:
            raise BuildError(f"Cannot read {image_path.relative_to(ROOT)}: {exc}") from exc
        loaded.append((shot, image_path, image))
    if missing:
        raise BuildError("Missing style-frame input(s):\n- " + "\n- ".join(missing))
    if len(loaded) != len(spec.get("shots", [])):
        raise BuildError("Shot specification and loaded style-frame count do not match.")
    return loaded


def contact_sheet(loaded: list[tuple[dict[str, Any], Path, Image.Image]], target: Path) -> None:
    columns = 4
    rows = math.ceil(len(loaded) / columns)
    tile_w, tile_h, label_h = 384, 216, 30
    sheet = Image.new("RGB", (columns * tile_w, rows * (tile_h + label_h)), (12, 16, 24))
    draw = ImageDraw.Draw(sheet)
    for index, (shot, _, image) in enumerate(loaded):
        x = (index % columns) * tile_w
        y = (index // columns) * (tile_h + label_h)
        tile = ImageOps.fit(image, (tile_w, tile_h), method=Image.Resampling.LANCZOS)
        sheet.paste(tile, (x, y))
        draw.text((x + 8, y + tile_h + 7), f"{shot['shot_id']}  {shot['duration_seconds']}s", fill=(235, 238, 245))
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, format="PNG", optimize=True)


def _pan_gains(pan: float) -> tuple[float, float]:
    bounded = max(-1.0, min(1.0, pan))
    angle = (bounded + 1.0) * math.pi / 4.0
    return math.cos(angle), math.sin(angle)


def add_tone(
    left: array.array,
    right: array.array,
    start_s: float,
    duration_s: float,
    frequency: float,
    gain: float,
    pan: float = 0.0,
    harmonics: float = 0.22,
    decay: float = 1.4,
) -> None:
    start = max(0, int(start_s * SAMPLE_RATE))
    count = max(1, int(duration_s * SAMPLE_RATE))
    left_gain, right_gain = _pan_gains(pan)
    limit = min(len(left), start + count)
    for sample_index in range(start, limit):
        t = (sample_index - start) / SAMPLE_RATE
        progress = t / duration_s
        attack = min(1.0, t / 0.025)
        release = min(1.0, max(0.0, (1.0 - progress) / 0.2))
        envelope = attack * release * math.exp(-decay * progress)
        phase = 2.0 * math.pi * frequency * t
        value = gain * envelope * (math.sin(phase) + harmonics * math.sin(2.0 * phase))
        left[sample_index] += value * left_gain
        right[sample_index] += value * right_gain


def add_noise(
    left: array.array,
    right: array.array,
    start_s: float,
    duration_s: float,
    gain: float,
    pan: float,
    seed: int,
    lowpass_alpha: float = 0.16,
    highpass_mix: float = 0.0,
    modulation_hz: float = 0.0,
) -> None:
    start = max(0, int(start_s * SAMPLE_RATE))
    count = max(1, int(duration_s * SAMPLE_RATE))
    end = min(len(left), start + count)
    left_gain, right_gain = _pan_gains(pan)
    rng = random.Random(seed)
    low_state = 0.0
    previous_white = 0.0
    for sample_index in range(start, end):
        t = (sample_index - start) / SAMPLE_RATE
        progress = t / duration_s
        attack = min(1.0, t / 0.035)
        release = min(1.0, max(0.0, (1.0 - progress) / 0.12))
        white = rng.random() * 2.0 - 1.0
        low_state += lowpass_alpha * (white - low_state)
        high_state = white - previous_white
        previous_white = white
        value = low_state * (1.0 - highpass_mix) + high_state * highpass_mix
        if modulation_hz > 0:
            value *= 0.62 + 0.38 * math.sin(2.0 * math.pi * modulation_hz * t)
        value *= gain * attack * release
        left[sample_index] += value * left_gain
        right[sample_index] += value * right_gain


def synthesize_audio(spec: dict[str, Any], total_samples: int) -> tuple[array.array, ...]:
    ambience_l = array.array("f", [0.0]) * total_samples
    ambience_r = array.array("f", [0.0]) * total_samples
    music_l = array.array("f", [0.0]) * total_samples
    music_r = array.array("f", [0.0]) * total_samples
    foley_l = array.array("f", [0.0]) * total_samples
    foley_r = array.array("f", [0.0]) * total_samples

    shot_starts: dict[str, float] = {}
    shot_end = 0.0
    for shot in spec["shots"]:
        shot_starts[shot["shot_id"]] = shot_end
        shot_end += float(shot["duration_seconds"])

    # One continuous low-level room-tone bed; the slow transition between
    # rooms follows the scene sequence rather than restarting at every cut.
    noise_state_l = 0.0
    noise_state_r = 0.0
    previous_white_l = 0.0
    previous_white_r = 0.0
    rng = random.Random(20261018)
    shot_index = 0
    accumulated = 0.0
    for index in range(total_samples):
        t = index / SAMPLE_RATE
        while shot_index + 1 < len(spec["shots"]) and t >= accumulated + float(spec["shots"][shot_index]["duration_seconds"]):
            accumulated += float(spec["shots"][shot_index]["duration_seconds"])
            shot_index += 1
        shot = spec["shots"][shot_index]
        gain = float(shot.get("ambience_gain", 0.008))
        # A short ramp at shot edges avoids clicks but keeps the room bed continuous.
        local_t = t - accumulated
        shot_duration = float(shot["duration_seconds"])
        edge = min(1.0, local_t / 0.12, max(0.0, (shot_duration - local_t) / 0.16))
        white_l = rng.random() * 2.0 - 1.0
        white_r = rng.random() * 2.0 - 1.0
        noise_state_l += 0.045 * (white_l - noise_state_l)
        noise_state_r += 0.045 * (white_r - noise_state_r)
        high_l = white_l - previous_white_l
        high_r = white_r - previous_white_r
        previous_white_l, previous_white_r = white_l, white_r
        texture = 0.82 if shot.get("bed") in {"living_room", "kitchen", "dinner_room"} else 0.62
        ambience_l[index] = edge * gain * (noise_state_l * texture + high_l * 0.018)
        ambience_r[index] = edge * gain * (noise_state_r * texture + high_r * 0.018)

    # All motifs are sparse events on one continuous music stem, not one track per shot.
    for cue_index, cue in enumerate(spec.get("music_cues", [])):
        at = float(cue["at_seconds"])
        notes = cue["notes_hz"]
        spacing = float(cue.get("note_seconds", 0.38)) + 0.08
        for note_index, frequency in enumerate(notes):
            add_tone(
                music_l,
                music_r,
                at + note_index * spacing,
                float(cue.get("note_seconds", 0.38)),
                float(frequency),
                float(cue.get("gain", 0.03)),
                float(cue.get("pan", 0.0)),
                decay=1.8,
            )

    for shot_index, shot in enumerate(spec["shots"]):
        start = shot_starts[shot["shot_id"]]
        for event_index, event in enumerate(shot.get("foley", [])):
            event_start = start + float(event.get("offset_seconds", 0.0))
            duration = float(event.get("duration_seconds", 0.25))
            level = float(event.get("level", 0.08))
            pan = float(event.get("pan", 0.0))
            kind = event["kind"]
            if kind in {"steam_hiss", "muffled_tv", "phone_texture", "fabric_rustle"}:
                high_mix = 0.18 if kind == "fabric_rustle" else 0.0
                alpha = 0.22 if kind in {"steam_hiss", "phone_texture"} else 0.1
                modulation = 4.8 if kind == "phone_texture" else (2.0 if kind == "muffled_tv" else 0.0)
                add_noise(foley_l, foley_r, event_start, duration, level, pan, 3000 + shot_index * 31 + event_index, alpha, high_mix, modulation)
            else:
                frequency, decay = {
                    "door_creak": (185.0, 1.1),
                    "wood_tap": (142.0, 4.0),
                    "metal_click": (1480.0, 5.5),
                    "door_thud": (78.0, 4.5),
                    "tableware": (920.0, 5.0),
                }.get(kind, (250.0, 3.0))
                add_tone(foley_l, foley_r, event_start, duration, frequency, level, pan, harmonics=0.16, decay=decay)

    return ambience_l, ambience_r, music_l, music_r, foley_l, foley_r


def _write_stereo_wav(path: Path, left: array.array, right: array.array) -> tuple[float, float, float]:
    interleaved = array.array("h")
    peak = 0.0
    sum_squares = 0.0
    for l_value, r_value in zip(left, right):
        l_value = max(-0.92, min(0.92, float(l_value)))
        r_value = max(-0.92, min(0.92, float(r_value)))
        peak = max(peak, abs(l_value), abs(r_value))
        sum_squares += l_value * l_value + r_value * r_value
        interleaved.append(int(l_value * 32767))
        interleaved.append(int(r_value * 32767))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(interleaved.tobytes())
    rms = math.sqrt(sum_squares / max(1, 2 * len(left)))
    return peak, rms, 20.0 * math.log10(max(rms, 1e-9))


def write_audio_stems(spec: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    total_seconds = sum(float(shot["duration_seconds"]) for shot in spec["shots"])
    total_samples = round(total_seconds * SAMPLE_RATE)
    amb_l, amb_r, mus_l, mus_r, foley_l, foley_r = synthesize_audio(spec, total_samples)
    stems = {
        "ambience": (amb_l, amb_r),
        "music": (mus_l, mus_r),
        "foley": (foley_l, foley_r),
    }
    metrics: dict[str, Any] = {"sample_rate": SAMPLE_RATE, "channels": 2, "duration_seconds": total_seconds, "stems": {}}
    for name, (left, right) in stems.items():
        path = output_dir / f"animatic_{name}_v01.wav"
        peak, rms, rms_dbfs = _write_stereo_wav(path, left, right)
        metrics["stems"][name] = {"path": path.relative_to(ROOT).as_posix(), "sha256": sha256_file(path), "peak": peak, "rms": rms, "rms_dbfs": rms_dbfs}

    mix_l = array.array("f", amb_l)
    mix_r = array.array("f", amb_r)
    prototype_master_gain = float(spec["audio"].get("prototype_master_gain", 1.0))
    for index in range(total_samples):
        mix_l[index] += mus_l[index] + foley_l[index]
        mix_r[index] += mus_r[index] + foley_r[index]
        fade_in = min(1.0, index / int(0.3 * SAMPLE_RATE))
        fade_out = min(1.0, max(0.0, (total_samples - index) / int(0.5 * SAMPLE_RATE)))
        mix_l[index] *= fade_in * fade_out * prototype_master_gain
        mix_r[index] *= fade_in * fade_out * prototype_master_gain
    mix_path = output_dir / "animatic_mix_v01.wav"
    peak, rms, rms_dbfs = _write_stereo_wav(mix_path, mix_l, mix_r)
    metrics["mix"] = {"path": mix_path.relative_to(ROOT).as_posix(), "sha256": sha256_file(mix_path), "peak": peak, "rms": rms, "rms_dbfs": rms_dbfs}
    metrics["mix"]["prototype_master_gain"] = prototype_master_gain
    metrics["mix_status"] = "PROCEDURAL_DRAFT_UNREVIEWED"
    return metrics


def frame_for_time(image: Image.Image, size: tuple[int, int], move: dict[str, Any], progress: float) -> Image.Image:
    width, height = size
    zoom = float(move.get("zoom_start", 1.0)) + (float(move.get("zoom_end", 1.0)) - float(move.get("zoom_start", 1.0))) * progress
    start = move.get("center_start", [0.5, 0.5])
    end = move.get("center_end", start)
    center_x = float(start[0]) + (float(end[0]) - float(start[0])) * progress
    center_y = float(start[1]) + (float(end[1]) - float(start[1])) * progress
    base = ImageOps.fit(image, size, method=Image.Resampling.LANCZOS)
    scaled_size = (max(width, round(width * zoom)), max(height, round(height * zoom)))
    scaled = base.resize(scaled_size, Image.Resampling.LANCZOS)
    max_left = scaled.width - width
    max_top = scaled.height - height
    left = max(0, min(max_left, round(max_left * center_x)))
    top = max(0, min(max_top, round(max_top * center_y)))
    return scaled.crop((left, top, left + width, top + height))


def build_video(spec: dict[str, Any], loaded: list[tuple[dict[str, Any], Path, Image.Image]], audio_path: Path, video_path: Path) -> dict[str, Any]:
    ffmpeg = os.environ.get("FFMPEG_EXE", "").strip()
    if not ffmpeg:
        raise BuildError("Set FFMPEG_EXE to a local FFmpeg executable before encoding the animatic.")
    ffmpeg_path = Path(ffmpeg)
    if not ffmpeg_path.is_file():
        raise BuildError("FFMPEG_EXE does not point to an existing executable.")

    fps = int(spec["fps"])
    width, height = (int(v) for v in spec["frame_size"])
    frame_count = sum(round(float(shot["duration_seconds"]) * fps) for shot in spec["shots"])
    if frame_count != round(sum(float(shot["duration_seconds"]) for shot in spec["shots"]) * fps):
        raise BuildError("Shot durations do not align to the target frame rate.")

    video_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(ffmpeg_path), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s:v", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0",
        "-i", str(audio_path), "-frames:v", str(frame_count), "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(video_path),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    assert process.stdin is not None
    produced_frames = 0
    try:
        for (shot, _, image) in loaded:
            shot_frames = round(float(shot["duration_seconds"]) * fps)
            motion = shot["camera"]
            for frame_index in range(shot_frames):
                progress = 0.0 if shot_frames <= 1 else frame_index / (shot_frames - 1)
                frame = frame_for_time(image, (width, height), motion, progress)
                process.stdin.write(frame.tobytes())
                produced_frames += 1
        process.stdin.close()
    except (BrokenPipeError, OSError) as exc:
        process.kill()
        _, stderr = process.communicate()
        detail = stderr.decode("utf-8", errors="replace")
        raise BuildError(f"FFmpeg stopped while receiving frames ({produced_frames} sent): {detail or exc}") from exc
    stderr = process.stderr.read() if process.stderr else b""
    return_code = process.wait()
    if return_code != 0:
        raise BuildError(f"FFmpeg failed with exit code {return_code}: {stderr.decode('utf-8', errors='replace')}")
    if produced_frames != frame_count:
        raise BuildError(f"Expected {frame_count} frames, wrote {produced_frames}.")
    return {"path": video_path.relative_to(ROOT).as_posix(), "sha256": sha256_file(video_path), "fps": fps, "frames": produced_frames, "duration_seconds": produced_frames / fps}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contact-sheet-only", action="store_true", help="Build only the still-frame review sheet; no FFmpeg needed.")
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        raise BuildError("Python 3.10 or newer is required.")

    spec = read_json(SHOT_SPEC)
    global SAMPLE_RATE
    SAMPLE_RATE = int(spec["audio"]["sample_rate"])
    if SAMPLE_RATE < 32000 or SAMPLE_RATE > 96000:
        raise BuildError(f"Unsupported project sample rate: {SAMPLE_RATE}")
    loaded = load_inputs(spec)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    sheet_path = OUTPUT_DIR / "RM-009-RM-018_contact_sheet_v01.png"
    contact_sheet(loaded, sheet_path)
    if args.contact_sheet_only:
        print(f"Contact sheet: {sheet_path.relative_to(ROOT)}")
        return 0

    duration = sum(float(shot["duration_seconds"]) for shot in spec["shots"])
    if duration != 36.0:
        raise BuildError(f"RM-009 through RM-018 should total 36 seconds; current specs total {duration}.")
    audio_metrics = write_audio_stems(spec, OUTPUT_DIR)
    audio_path = ROOT / audio_metrics["mix"]["path"]
    video = build_video(spec, loaded, audio_path, OUTPUT_DIR / "RM-009-RM-018_animatic_v01.mp4")
    inputs = [{"shot_id": shot["shot_id"], "path": path.relative_to(ROOT).as_posix(), "sha256": sha256_file(path)} for shot, path, _ in loaded]
    manifest = {
        "sequence_id": spec["sequence_id"],
        "storyboard_revision": spec["storyboard_revision"],
        "status": "ANIMATIC_DRAFT_UNREVIEWED",
        "source": "CREATIVE_PROPOSAL built from draft still-styleframes; not generated video diffusion",
        "shot_count": len(loaded),
        "duration_seconds": video["duration_seconds"],
        "inputs": inputs,
        "contact_sheet": {"path": sheet_path.relative_to(ROOT).as_posix(), "sha256": sha256_file(sheet_path)},
        "audio": audio_metrics,
        "video": video,
        "visual_review": "UNREVIEWED",
        "audio_review": "UNREVIEWED",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = OUTPUT_DIR / "RM-009-RM-018_manifest_v01.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "video": video, "audio": audio_metrics["mix"], "manifest": manifest_path.relative_to(ROOT).as_posix()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"ANIMATIC_BUILD_ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
