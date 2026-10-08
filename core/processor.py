#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import os
import random
import json
import logging
import time
from pathlib import Path
from datetime import datetime, timedelta
from config.config import TEMP_DIR

logger = logging.getLogger("processor")

# ---------- ФИКС ДЛЯ FFMPEG ----------
FFMPEG_PATH = r"C:\Program Files\ffmpeg\bin"
if Path(FFMPEG_PATH).exists():
    os.environ["PATH"] = FFMPEG_PATH + os.pathsep + os.environ.get("PATH", "")

CONFIG = {
    "output_dir": TEMP_DIR,
    "watermark_path": Path(__file__).resolve().parent.parent / "watermark.mov",
    "scale_width": 1080,
    "scale_height": 1920,
    "overlay_scale": 0.9,
    "boxblur_radius": 7,
    "crop_min": 4,
    "crop_max": 6,
    "speed_min": 0.98,
    "speed_max": 1.02,
    "brightness_range": (-0.1, 0.1),
    "contrast_range": (0.9, 1.1),
    "saturation_range": (0.8, 1.2),
    "colorbalance_range": (-0.1, 0.1),
    "volume_rand_range": (0.9, 1.1),
    "volume_duration": 0.5,
    "apply_negate_chance": 0.5,
    "apply_volume_chance": 0.5,
    "apply_black_chance": 0.5,
    "apply_noise_chance": 0.0,
    "black_duration": 0.04,
    "negate_duration": 0.1,
    "bottom_limit": 1400,
    "video_codec": "libx264",
    "crf": 23,
    "preset": "fast",
    "audio_codec": "aac",
    "audio_bitrate": "128k",
    "threads": 2,
    "pixel_format": "yuv420p",
    "profile": "high",
    "level": "4.0",
    "tag": "avc1",
    "phone_models": ["SM-G998B", "SM-G991B", "iPhone13,2", "iPhone14,5", "Pixel 6 Pro", "Pixel 7", "SM-S908B", "SM-A536B"],
    "android_version": "10",
    "watermark_scale_factor": 2,
    "watermark_opacity": 1,
    "safe_zone": {
        "left": 110,
        "top": 200,
        "right": 970,
        "bottom": 1328   # было 1120 → 400 px отступ от низа оверлея (1728)
    }
}

def check_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True, timeout=5)
        subprocess.run(["ffprobe", "-version"], capture_output=True, check=True, timeout=5)
        return True
    except:
        logger.warning("FFmpeg/FFprobe not found")
        return False

def get_media_size(path):
    ext = path.suffix.lower()
    if ext == '.png':
        try:
            from PIL import Image
            with Image.open(path) as img:
                return img.size
        except:
            logger.warning("PIL not available, fallback to 200x200")
            return 200, 200
    else:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "json",
            str(path)
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                logger.error(f"ffprobe failed: {result.stderr}")
                return 200, 200
            data = json.loads(result.stdout)
            streams = data.get("streams", [])
            if streams:
                w = int(streams[0].get("width", 200))
                h = int(streams[0].get("height", 200))
                return w, h
            else:
                return 200, 200
        except Exception as e:
            logger.error(f"ffprobe error: {e}")
            return 200, 200

def is_video_file(path):
    return path.suffix.lower() != '.png'

def get_duration(path):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])

def process_video(input_path, output_dir=None):
    if not check_ffmpeg():
        logger.error("FFmpeg not available")
        return None

    output_dir = Path(output_dir) if output_dir else TEMP_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / f"{input_path.stem}_uniq.mp4"
    if output_path.exists():
        logger.info(f"Unique file already exists: {output_path}")
        return output_path

    tmp_path = TEMP_DIR / f"temp_{random.randint(100000, 999999)}.mp4"
    while tmp_path.exists():
        tmp_path = TEMP_DIR / f"temp_{random.randint(100000, 999999)}.mp4"

    try:
        duration = get_duration(input_path)
        logger.info(f"Video duration: {duration:.2f}s")
    except Exception as e:
        logger.error(f"Failed to get duration: {e}")
        return None

    if duration < 1:
        logger.warning(f"Video too short ({duration}s), skipping uniqueization")
        return None

    crop = random.randint(CONFIG["crop_min"], CONFIG["crop_max"])
    speed = f"{random.uniform(CONFIG['speed_min'], CONFIG['speed_max']):.3f}"
    br = random.uniform(*CONFIG["brightness_range"])
    co = random.uniform(*CONFIG["contrast_range"])
    sa = random.uniform(*CONFIG["saturation_range"])
    eq_filter = f"eq=brightness={br:.2f}:contrast={co:.2f}:saturation={sa:.2f}"
    rs = random.uniform(*CONFIG["colorbalance_range"])
    bs = random.uniform(*CONFIG["colorbalance_range"])
    cb_filter = f"colorbalance=rs={rs:.2f}:bs={bs:.2f}"

    logger.info(f"Uniqueization params: crop={crop}, speed={speed}, brightness={br:.2f}, contrast={co:.2f}, saturation={sa:.2f}")

    blur_w = CONFIG["scale_width"] // 2
    blur_h = CONFIG["scale_height"] // 2
    filters = [
        f"[0:v]crop=iw-{crop*2}:ih-{crop*2}:{crop}:{crop},scale={CONFIG['scale_width']}:{CONFIG['scale_height']}:force_original_aspect_ratio=disable,fps=30,setpts=PTS*{speed}[scaled]",
        "[scaled]split=2[base][blurred]",
        f"[blurred]scale={blur_w}:{blur_h},boxblur={CONFIG['boxblur_radius']}:1,hue=s=0,colorchannelmixer=0.2:0.2:0.2:0:0.2:0.2:0.2:0:0.2:0.2:0.2:0,scale={CONFIG['scale_width']}:{CONFIG['scale_height']}[blurred_bw]",
        f"[base]{eq_filter},{cb_filter},scale=iw*{CONFIG['overlay_scale']}:ih*{CONFIG['overlay_scale']}[small]"
    ]

    cmd = ["ffmpeg", "-i", str(input_path)]
    wm_path = CONFIG["watermark_path"]
    use_wm = wm_path.exists()

    if use_wm:
        logger.info(f"Watermark file found: {wm_path.name} (type: {wm_path.suffix})")
        ow, oh = int(CONFIG["scale_width"] * CONFIG["overlay_scale"]), int(CONFIG["scale_height"] * CONFIG["overlay_scale"])
        max_w, max_h = ow, oh - CONFIG["bottom_limit"]
        if max_w > 0 and max_h > 0:
            wm_w, wm_h = get_media_size(wm_path)
            scale = min(1.0, max_w / wm_w, max_h / wm_h) * CONFIG.get("watermark_scale_factor", 1.0)
            scale = min(scale, max_w / wm_w, max_h / wm_h)
            nw, nh = int(wm_w * scale), int(wm_h * scale)
            if nw < 10 or nh < 10:
                nw, nh = 100, 50

            safe = CONFIG["safe_zone"]
            min_x = safe["left"]
            max_x = safe["right"] - nw
            if max_x < min_x:
                max_x = min_x
            x = random.randint(min_x, max_x)

            min_y = safe["top"]
            max_y = safe["bottom"] - nh
            if max_y < min_y:
                max_y = min_y
            # ПАТЧ: верхний кап расширен с 300 до 500 → даёт шанс уйти ниже на 200 px
            y = random.randint(min_y, min(max_y, safe["top"] + 500))

            logger.info(f"Watermark scaled to {nw}x{nh}, placed at ({x},{y}) within safe zone")

            is_video = is_video_file(wm_path)
            wm_duration = None
            if is_video:
                try:
                    wm_duration = get_duration(wm_path)
                    logger.info(f"Watermark video duration: {wm_duration:.2f}s")
                except Exception as e:
                    logger.error(f"Failed to get watermark duration: {e}")
                    wm_duration = 3.0

            cmd.extend(["-i", str(wm_path)])

            opacity = CONFIG.get("watermark_opacity", 1.0)
            filters.append(f"[1:v]scale={nw}:{nh},format=rgba,colorchannelmixer=aa={opacity}[wm]")

            # ПРАВИЛЬНЫЙ СИНТАКСИС overlay
            overlay_filter = f"[small][wm]overlay={x}:{y}"
            if is_video and wm_duration is not None:
                overlay_filter += f":enable='lt(t,{wm_duration})'"
            overlay_filter += "[outv]"
            filters.append(overlay_filter)

            filters.append(f"[blurred_bw][outv]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2[vfinal]")
        else:
            use_wm = False
            logger.info("Watermark too large, skipping")

    if not use_wm:
        filters.append(f"[blurred_bw][small]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2[vfinal]")

    apply_negate = random.random() < CONFIG["apply_negate_chance"]
    apply_black = random.random() < CONFIG["apply_black_chance"]
    apply_volume = random.random() < CONFIG["apply_volume_chance"]

    if apply_negate:
        negate_start = random.uniform(0.5, duration - CONFIG["negate_duration"] - 0.5) if duration > 1 else 0
        filters.append(f"[vfinal]negate=enable='between(t,{negate_start},{negate_start+CONFIG['negate_duration']})'[vfinal]")
        logger.info(f"Apply negate at {negate_start:.2f}s")
    else:
        logger.info("Negate skipped")

    if apply_black:
        black_start = random.uniform(0.5, duration - CONFIG["black_duration"] - 0.5) if duration > 1 else 0
        color = random.choice(["black", "white"])
        filters.append(f"color={color}:{CONFIG['scale_width']}x{CONFIG['scale_height']}:rate=1,format=yuv420p,loop=1:1:0,scale={CONFIG['scale_width']}:{CONFIG['scale_height']}[bg]")
        filters.append(f"[vfinal][bg]overlay=0:0:enable='between(t,{black_start},{black_start+CONFIG['black_duration']})'[vfinal]")
        logger.info(f"Apply {color} screen at {black_start:.2f}s")
    else:
        logger.info("Black/white screen skipped")

    audio_filters = [f"atempo={speed}"]
    if apply_volume:
        vol = random.choice([CONFIG["volume_rand_range"][0], CONFIG["volume_rand_range"][1]])
        vol_start = random.uniform(0.5, duration - CONFIG["volume_duration"] - 0.5) if duration > 1 else 0
        audio_filters.append(f"volume={vol}:enable='between(t,{vol_start},{vol_start+CONFIG['volume_duration']})'")
        logger.info(f"Apply volume change {vol} at {vol_start:.2f}s")
    else:
        logger.info("Volume change skipped")
    audio_filter = ",".join(audio_filters)

    creation_time = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    model = random.choice(CONFIG["phone_models"])

    cmd.extend([
        "-map_metadata", "-1",
        "-metadata", f"creation_time={creation_time}",
        "-metadata", f"model={model}",
        "-metadata", f"com.android.version={CONFIG['android_version']}",
        "-tag:v", CONFIG["tag"],
        "-profile:v", CONFIG["profile"],
        "-level", CONFIG["level"],
        "-pix_fmt", CONFIG["pixel_format"],
        "-filter_complex", ";".join(filters),
        "-map", "[vfinal]",
        "-map", "0:a?",
        "-c:v", CONFIG["video_codec"],
        "-crf", str(CONFIG["crf"]),
        "-preset", CONFIG["preset"],
        "-threads", str(CONFIG["threads"]),
        "-c:a", CONFIG["audio_codec"],
        "-b:a", CONFIG["audio_bitrate"],
        "-af", audio_filter,
        "-t", str(duration),
        "-movflags", "+faststart",
        "-y", str(tmp_path)
    ])

    logger.info(f"FFmpeg command length: {len(cmd)} args, output: {tmp_path}")
    logger.info(f"Running ffmpeg... (timeout 900s)")

    start_time = time.time()
    process = None
    stderr_lines = []
    last_log_time = time.time()

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            universal_newlines=True
        )

        while True:
            if process.poll() is not None:
                remaining_stderr = process.stderr.read()
                if remaining_stderr:
                    stderr_lines.extend(remaining_stderr.splitlines())
                break

            try:
                line = process.stderr.readline()
                if line:
                    stderr_lines.append(line.strip())
                    logger.debug(f"FFmpeg stderr: {line.strip()}")
                else:
                    time.sleep(0.1)
            except:
                pass

            if time.time() - last_log_time > 10 and stderr_lines:
                last_line = stderr_lines[-1]
                logger.info(f"FFmpeg last stderr: {last_line[:200]}")
                last_log_time = time.time()

            if time.time() - start_time > 900:
                logger.error("FFmpeg timeout (900s), killing process")
                process.kill()
                process.wait()
                if stderr_lines:
                    logger.error("FFmpeg stderr output (last 20 lines):")
                    for line in stderr_lines[-20:]:
                        logger.error(f"  {line}")
                if tmp_path.exists():
                    tmp_path.unlink()
                return None

        elapsed = time.time() - start_time
        logger.info(f"FFmpeg finished in {elapsed:.1f}s")

        if process.returncode != 0:
            logger.error(f"FFmpeg error (code {process.returncode})")
            if stderr_lines:
                logger.error("FFmpeg stderr output (last 20 lines):")
                for line in stderr_lines[-20:]:
                    logger.error(f"  {line}")
            if tmp_path.exists():
                tmp_path.unlink()
            return None

        if not tmp_path.exists():
            logger.error("Temporary file not created")
            return None

        os.replace(str(tmp_path), str(output_path))
        logger.info(f"Unique video saved: {output_path}")
        return output_path

    except Exception as e:
        logger.error(f"FFmpeg exception: {e}")
        if process and process.poll() is None:
            process.kill()
            process.wait()
        if tmp_path.exists():
            tmp_path.unlink()
        return None