"""
Reusable photo + audio -> MP4 video engine.

Same rendering techniques as the baby-birthday-video project (Ken Burns
pans/zooms, sparkle overlay, pastel colour grade, cross-dissolve
transitions, portrait/landscape-aware fitting, custom overlay video clips),
simplified down to the core use case: give it a list of photos and one
audio file, get an MP4 back. No lyric/caption-sync map — that didn't pan
out well before, so it's left out here (worth revisiting later as its own
feature).

Import generate_video() and call it directly, or drive it from app.py.
"""

import math
import random
import time

import os

import numpy as np
import pillow_heif
import proglog
from PIL import Image, ImageFilter, ImageDraw, ImageFont, ImageOps
from moviepy import VideoClip, VideoFileClip, AudioFileClip, concatenate_videoclips, ImageClip
from moviepy.video.fx import CrossFadeIn, CrossFadeOut
from moviepy.audio.fx import AudioFadeOut, AudioNormalize

import config

# iPhone photos are commonly HEIC — Pillow can't open that format on its own,
# so photos exported straight from the Photos app would otherwise fail here.
pillow_heif.register_heif_opener()

FULL_FPS = config.FPS
PREVIEW_FPS = config.PREVIEW_FPS

TRANSITION_DUR = config.TRANSITION_DURATION
DIRECTIONS = ["zoom_in", "zoom_out", "pan_left", "pan_right"]


def _smoothstep(x):
    """Classic ease-in-out curve: slow-fast-slow instead of constant speed."""
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


# ═══════════════════════════════════════════════════════════════
#  PORTRAIT / LANDSCAPE DETECTION + FITTING
# ═══════════════════════════════════════════════════════════════

def is_portrait(img):
    return img.height > img.width


def fit_landscape(img, size):
    W, H = size
    scale = max(W / img.width, H / img.height) * 1.15
    img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    return np.array(img)


def fit_portrait(img, size):
    W, H = size
    bg_scale = max(W / img.width, H / img.height)
    bg = img.resize((int(img.width * bg_scale), int(img.height * bg_scale)), Image.LANCZOS)
    bx = (bg.width - W) // 2
    by = (bg.height - H) // 2
    bg = bg.crop((bx, by, bx + W, by + H))
    bg = bg.filter(ImageFilter.GaussianBlur(radius=35))
    dim = Image.new("RGB", (W, H), (20, 10, 20))
    bg = Image.blend(bg, dim, alpha=0.45)

    scale = min(W / img.width, H / img.height) * 0.97
    new_w = int(img.width * scale)
    new_h = int(img.height * scale)
    fg = img.resize((new_w, new_h), Image.LANCZOS)

    px = (W - new_w) // 2
    py = (H - new_h) // 2
    bg.paste(fg, (px, py))
    return np.array(bg)


def load_photo(path, size, auto_color=True):
    img = Image.open(path).convert("RGB")
    if auto_color:
        # Stretches each photo's own histogram so its darkest/brightest
        # pixels reach near-black/near-white — evens out photos shot on
        # different cameras/lighting instead of leaving some looking flat
        # or washed out next to others.
        try:
            img = ImageOps.autocontrast(img, cutoff=config.AUTO_COLOR_CUTOFF)
        except Exception:
            pass
    if is_portrait(img):
        return fit_portrait(img, size), True
    else:
        return fit_landscape(img, size), False


# ═══════════════════════════════════════════════════════════════
#  COLOUR GRADE
# ═══════════════════════════════════════════════════════════════

def apply_pastel_grade(frame):
    img = Image.fromarray(frame).convert("RGB")
    img = img.filter(ImageFilter.GaussianBlur(radius=0.5))
    arr = np.array(img, dtype=np.float32)
    arr = arr * 0.88 + 28
    arr[:, :, 0] = np.clip(arr[:, :, 0] * 1.08, 0, 255)
    arr[:, :, 2] = np.clip(arr[:, :, 2] * 1.06, 0, 255)
    arr[:, :, 1] = np.clip(arr[:, :, 1] * 0.97, 0, 255)
    return np.clip(arr, 0, 255).astype(np.uint8)


def apply_brightness(frame, factor):
    """factor: 1.0 = unchanged, >1.0 = brighter, <1.0 = darker."""
    if factor == 1.0:
        return frame
    arr = frame.astype(np.float32) * factor
    return np.clip(arr, 0, 255).astype(np.uint8)


_vignette_cache = {}


def _get_vignette_mask(video_size, strength):
    key = (video_size, strength)
    cached = _vignette_cache.get(key)
    if cached is not None:
        return cached
    W, H = video_size
    y, x = np.ogrid[:H, :W]
    cx, cy = W / 2, H / 2
    dist = np.sqrt(((x - cx) / cx) ** 2 + ((y - cy) / cy) ** 2)
    # Starts darkening beyond ~40% of the way to the corner, reaching
    # (1 - strength) brightness right at the corners.
    mask = 1.0 - strength * np.clip((dist - 0.4) / 0.6, 0, 1)
    mask = mask.astype(np.float32)
    _vignette_cache[key] = mask
    return mask


def apply_vignette(frame, video_size, strength):
    if strength <= 0:
        return frame
    mask = _get_vignette_mask(video_size, strength)
    arr = frame.astype(np.float32) * mask[:, :, None]
    return np.clip(arr, 0, 255).astype(np.uint8)


_grain_cache = {}


def _get_grain_tiles(video_size, strength, count):
    key = (video_size, strength, count)
    cached = _grain_cache.get(key)
    if cached is not None:
        return cached
    W, H = video_size
    rng = np.random.default_rng(123)
    tiles = [rng.normal(0, strength, (H, W, 1)).astype(np.float32) for _ in range(count)]
    _grain_cache[key] = tiles
    return tiles


def apply_grain(frame, video_size, strength, count, t):
    if strength <= 0:
        return frame
    tiles = _get_grain_tiles(video_size, strength, count)
    idx = int(t * 12) % len(tiles)
    arr = frame.astype(np.float32) + tiles[idx]
    return np.clip(arr, 0, 255).astype(np.uint8)


# ═══════════════════════════════════════════════════════════════
#  KEN BURNS
# ═══════════════════════════════════════════════════════════════

def ken_burns_frame(base_img, t, duration, direction, video_size, portrait=False,
                     ken_burns_enabled=True, apply_grade=True, brightness=1.0,
                     ease_motion=True, vignette_strength=0.0, grain_strength=0.0,
                     grain_count=config.GRAIN_PATTERN_COUNT):
    W, H = video_size
    img_h, img_w = base_img.shape[:2]
    # A static (no pan/zoom) photo is just the animation's t=0 frame held
    # for the whole clip — same centred crop, no motion over time.
    progress = (t / max(duration, 0.001)) if ken_burns_enabled else 0.0
    if ken_burns_enabled and ease_motion:
        # Slow-fast-slow instead of constant speed — real camera moves
        # rarely hold one constant speed, so this alone reads as noticeably
        # less "slideshow-ish".
        progress = _smoothstep(progress)

    if portrait:
        scale = 1.0 + 0.04 * (progress if direction == "zoom_in" else -progress + 0.04)
        cx, cy = 0.5, 0.5
    else:
        if direction == "zoom_in":
            scale, cx, cy = 1.0 + 0.08 * progress, 0.5, 0.5
        elif direction == "zoom_out":
            scale, cx, cy = 1.08 - 0.08 * progress, 0.5, 0.5
        elif direction == "pan_right":
            scale = 1.08; cx = 0.38 + 0.24 * progress; cy = 0.5
        else:
            scale = 1.08; cx = 0.62 - 0.24 * progress; cy = 0.5

    crop_w = int(W / scale)
    crop_h = int(H / scale)
    x1 = max(0, min(int(cx * img_w) - crop_w // 2, img_w - crop_w))
    y1 = max(0, min(int(cy * img_h) - crop_h // 2, img_h - crop_h))

    cropped = base_img[y1:y1 + crop_h, x1:x1 + crop_w]
    resized = np.array(Image.fromarray(cropped).resize((W, H), Image.LANCZOS))
    graded = apply_pastel_grade(resized) if apply_grade else resized
    bright = apply_brightness(graded, brightness)
    vignetted = apply_vignette(bright, video_size, vignette_strength)
    return apply_grain(vignetted, video_size, grain_strength, grain_count, t)


# ═══════════════════════════════════════════════════════════════
#  SPARKLE SYSTEM
# ═══════════════════════════════════════════════════════════════

class SparkleSystem:
    def __init__(self, video_size, n=config.SPARKLE_COUNT, seed=42):
        rng = np.random.default_rng(seed)
        self.n = n
        self.W, self.H = video_size
        self.x = rng.uniform(0, self.W, n)
        self.y = rng.uniform(0, self.H, n)
        self.sizes = rng.uniform(3, 10, n).astype(int)
        self.speeds = rng.uniform(0.3, 1.2, n)
        self.phases = rng.uniform(0, 2 * math.pi, n)
        palette = [(255, 255, 255), (255, 220, 240), (255, 245, 180), (230, 210, 255), (255, 200, 220)]
        self.colours = [palette[i % len(palette)] for i in range(n)]

    def render(self, t):
        overlay = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        for i in range(self.n):
            alpha = int(128 + 127 * math.sin(self.speeds[i] * t * 2 * math.pi + self.phases[i]))
            alpha = max(0, min(255, alpha))
            if alpha < 20:
                continue
            drift_y = (self.y[i] - self.speeds[i] * 18 * t) % self.H
            cx, cy, r = int(self.x[i]), int(drift_y), self.sizes[i]
            cr, cg, cb = self.colours[i]
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(cr, cg, cb, alpha))
            arm = r + 3
            draw.line([cx - arm, cy, cx + arm, cy], fill=(cr, cg, cb, alpha // 2), width=1)
            draw.line([cx, cy - arm, cx, cy + arm], fill=(cr, cg, cb, alpha // 2), width=1)
        return np.array(overlay)


# ═══════════════════════════════════════════════════════════════
#  OVERLAY CLIPS (e.g. hearts/confetti animations layered on every photo)
# ═══════════════════════════════════════════════════════════════

def load_overlay_clips(paths):
    """Load uploaded overlay video/gif files once, reused across every photo clip."""
    clips = []
    for path in paths or []:
        ext = os.path.splitext(path)[1].lower()
        if ext not in config.OVERLAY_SUPPORTED_EXTS:
            continue
        clips.append(VideoFileClip(path, has_mask=True))
    return clips


def close_overlay_clips(clips):
    for clip in clips:
        try:
            clip.close()
        except Exception:
            pass


def _overlay_frame(clip, t, video_size):
    W, H = video_size
    clip_t = t % clip.duration
    raw = clip.get_frame(clip_t)
    frame_img = Image.fromarray(raw.astype(np.uint8)).convert("RGBA")
    frame_img = frame_img.resize((W, H), Image.LANCZOS)

    if clip.mask is not None:
        mask_raw = clip.mask.get_frame(clip_t)
        mask_img = Image.fromarray((mask_raw * 255).astype(np.uint8), mode="L")
        mask_img = mask_img.resize((W, H), Image.LANCZOS)
        frame_img.putalpha(mask_img)
    else:
        # No real alpha channel (most stock "effect on black" clips) — treat
        # brightness as opacity so the dark background disappears and only
        # the bright effect (hearts, sparkles, confetti...) shows through.
        arr = np.array(frame_img, dtype=np.float32)
        luminance = arr[:, :, 0] * 0.299 + arr[:, :, 1] * 0.587 + arr[:, :, 2] * 0.114
        alpha = np.clip(luminance / 255.0 * 2.0, 0, 1)
        arr[:, :, 3] = (alpha * 255).astype(np.uint8)
        frame_img = Image.fromarray(arr.astype(np.uint8), mode="RGBA")

    arr = np.array(frame_img, dtype=np.float32)
    arr[:, :, 3] = np.clip(arr[:, :, 3] * config.OVERLAY_OPACITY, 0, 255)
    return arr.astype(np.uint8)


# ═══════════════════════════════════════════════════════════════
#  FONT / TEXT
# ═══════════════════════════════════════════════════════════════

def get_font(size):
    for fp in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                pass
    return ImageFont.load_default()


# ═══════════════════════════════════════════════════════════════
#  PHOTO CLIP BUILDER
# ═══════════════════════════════════════════════════════════════

def build_photo_clip(photo_path, duration, direction, sparkle_sys, video_size, fps,
                      lyric="", sparkle_enabled=True, ken_burns_enabled=True,
                      pastel_grade_enabled=True, overlay_clips=None, brightness=1.0,
                      auto_color_enabled=True, ease_motion=True,
                      vignette_strength=0.0, grain_strength=0.0,
                      caption_slide_enabled=True):
    base_img, portrait = load_photo(photo_path, video_size, auto_color=auto_color_enabled)
    W, H = video_size

    # The caption is built as its own small (text + padding) image rather
    # than a full-frame layer, so it can be pasted at a position that
    # slides per-frame — a full-frame layer would only support fading
    # in place, not moving.
    lyric_img = None
    lyric_final_xy = None
    if lyric and lyric.strip():
        font = get_font(52)
        probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        bbox = probe.textbbox((0, 0), lyric, font=font, align="center")
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad = 20
        box_w, box_h = tw + pad * 2, th + pad
        lyric_img = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
        d = ImageDraw.Draw(lyric_img)
        d.rounded_rectangle([0, 0, box_w, box_h], radius=16, fill=(0, 0, 0, 100))
        text_x, text_y = pad - bbox[0], pad // 2 - bbox[1]
        d.text((text_x + 2, text_y + 2), lyric, font=font, fill=(255, 210, 230, 200), align="center")
        d.text((text_x, text_y), lyric, font=font, fill=(255, 255, 255, 245), align="center")
        # Position proportional to frame height, not a fixed pixel offset —
        # keeps captions sitting in a sensible spot across aspect ratios
        # (a fixed 130px offset would sit very differently on a 1920-tall
        # vertical export than on a 1080-tall landscape one).
        final_x = (W - box_w) // 2
        final_y = H - int(H * 0.12) - box_h
        lyric_final_xy = (final_x, final_y)

    def make_frame(t):
        frame = ken_burns_frame(base_img, t, duration, direction, video_size, portrait,
                                 ken_burns_enabled=ken_burns_enabled, apply_grade=pastel_grade_enabled,
                                 brightness=brightness, ease_motion=ease_motion,
                                 vignette_strength=vignette_strength, grain_strength=grain_strength)
        out = Image.fromarray(frame).convert("RGBA")

        if sparkle_enabled:
            sparks = sparkle_sys.render(t)
            out = Image.alpha_composite(out, Image.fromarray(sparks))

        for clip in (overlay_clips or []):
            ov_frame = _overlay_frame(clip, t, video_size)
            out = Image.alpha_composite(out, Image.fromarray(ov_frame, mode="RGBA"))

        if lyric_img is not None:
            fade_in = min(t / 0.6, 1.0)
            fade_out = min((duration - t) / 0.6, 1.0)
            fade = max(min(fade_in, fade_out), 0.0)
            if fade > 0:
                final_x, final_y = lyric_final_xy
                if caption_slide_enabled:
                    slide = config.CAPTION_SLIDE_DISTANCE
                    y_offset = int((1 - _smoothstep(fade_in)) * slide + (1 - _smoothstep(fade_out)) * slide)
                else:
                    y_offset = 0
                layer = lyric_img.copy()
                alpha = layer.getchannel("A").point(lambda a, f=fade: int(a * f))
                layer.putalpha(alpha)
                out.paste(layer, (final_x, final_y + y_offset), layer)

        return np.array(out.convert("RGB"))

    return VideoClip(make_frame, duration=duration).with_fps(fps)


# ═══════════════════════════════════════════════════════════════
#  SCHEDULING
# ═══════════════════════════════════════════════════════════════

def _order_photos(photos, photo_order):
    photos = list(photos)
    if photo_order == "alphabetical":
        photos.sort()
    elif photo_order == "random":
        random.shuffle(photos)
    # "as-given" (upload order) -> leave untouched
    return photos


def _caption_for(path, captions, fallback=""):
    if not captions:
        return fallback
    return captions.get(os.path.basename(path).lower(), fallback)


def _build_schedule(photos, content_dur, photo_duration=None, photo_captions=None):
    n = len(photos)
    per = photo_duration if photo_duration else max(content_dur / n, 1.0)
    clips = [
        {"photo": photos[i], "duration": round(per, 3), "effect": DIRECTIONS[i % 4],
         "lyric": _caption_for(photos[i], photo_captions)}
        for i in range(n)
    ]
    return clips


def _loop_and_trim(clips, content_dur):
    one_pass_dur = sum(c["duration"] for c in clips)
    passes_needed = math.ceil(content_dur / one_pass_dur) if one_pass_dur < content_dur - 1.0 else 1

    effect_rotations = [
        ["zoom_in", "zoom_out", "pan_right", "pan_left"],
        ["pan_left", "zoom_in", "zoom_out", "pan_right"],
        ["zoom_out", "pan_left", "zoom_in", "pan_right"],
        ["pan_right", "zoom_out", "pan_left", "zoom_in"],
    ]

    schedule = []
    for pass_num in range(passes_needed):
        for entry in clips:
            new_entry = entry.copy()
            if pass_num > 0:
                orig = effect_rotations[0]
                rot = effect_rotations[pass_num % len(effect_rotations)]
                eff = entry.get("effect", "zoom_in")
                if eff in orig:
                    new_entry["effect"] = rot[orig.index(eff)]
                new_entry["lyric"] = ""  # captions only shown on the first pass
            schedule.append(new_entry)

    trimmed, accum = [], 0.0
    for entry in schedule:
        dur = entry["duration"]
        remaining = content_dur - accum
        if remaining <= 0.5:
            break
        if accum + dur >= content_dur:
            entry = entry.copy()
            entry["duration"] = max(remaining, 1.0)
            trimmed.append(entry)
            accum += entry["duration"]
            break
        trimmed.append(entry)
        accum += dur
    return trimmed


# ═══════════════════════════════════════════════════════════════
#  ENCODE PROGRESS (real frame-by-frame percentage + ETA)
# ═══════════════════════════════════════════════════════════════

class _EncodeProgressBridge(proglog.ProgressBarLogger):
    """Feeds moviepy/ffmpeg's own per-frame progress bar into on_progress(
    fraction, message) — this is the actual dominant cost of a render (the
    per-frame Python compositing plus the ffmpeg pipe), so it gives an
    honest percentage/ETA rather than a guess."""

    def __init__(self, on_progress, label="Rendering video"):
        super().__init__()
        self.on_progress = on_progress
        self.label = label
        self._start_time = time.time()

    def bars_callback(self, bar, attr, value, old_value=None):
        if attr != "index":
            return
        total = self.bars[bar].get("total")
        if not total:
            return
        fraction = min(max(value / total, 0.0), 1.0)
        elapsed = time.time() - self._start_time
        message = self.label
        if fraction > 0.02:
            eta = elapsed / fraction - elapsed
            message = f"{self.label} — about {int(eta // 60)}m {int(eta % 60)}s left"
        self.on_progress(fraction, message)


# ═══════════════════════════════════════════════════════════════
#  MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════

def generate_video(
    photo_paths,
    audio_path,
    output_path,
    photo_duration=None,
    photo_order=config.DEFAULT_PHOTO_ORDER,
    title_text=config.DEFAULT_TITLE_TEXT,
    closing_text=config.DEFAULT_CLOSING_TEXT,
    opening_duration=config.DEFAULT_OPENING_DURATION,
    closing_duration=config.DEFAULT_CLOSING_DURATION,
    sparkle_enabled=config.SPARKLE_ENABLED_DEFAULT,
    ken_burns_enabled=config.KEN_BURNS_ENABLED_DEFAULT,
    pastel_grade_enabled=config.PASTEL_GRADE_ENABLED_DEFAULT,
    brightness=config.BRIGHTNESS_DEFAULT,
    ease_motion=config.EASE_KEN_BURNS_DEFAULT,
    auto_color_enabled=config.AUTO_COLOR_ENABLED_DEFAULT,
    audio_normalize_enabled=config.AUDIO_NORMALIZE_DEFAULT,
    vignette_enabled=config.VIGNETTE_ENABLED_DEFAULT,
    grain_enabled=config.GRAIN_ENABLED_DEFAULT,
    caption_slide_enabled=config.CAPTION_SLIDE_ENABLED_DEFAULT,
    aspect_ratio=config.DEFAULT_ASPECT_RATIO,
    overlay_paths=None,
    photo_captions=None,
    preview=False,
    progress_cb=None,
    on_progress=None,
):
    """
    photo_paths: list of image file paths (at least 1)
    audio_path: path to the music/audio file
    output_path: where to write the .mp4
    photo_duration: seconds per MIDDLE photo (None = auto-split evenly to fill
        whatever time is left after the opening/closing photos)
    photo_order: "as-given" | "alphabetical" | "random"
    title_text / closing_text: optional captions on the opening / closing photo
    opening_duration / closing_duration: fixed seconds for the first and last
        photo (only used when there are 3+ photos — see below)
    sparkle_enabled: overlay the built-in glitter/sparkle particles
    ken_burns_enabled: slow pan/zoom motion; False = still photos
    pastel_grade_enabled: warm pink/lavender colour grade
    brightness: 1.0 = unchanged, >1.0 = brighter, <1.0 = darker
    ease_motion: ease the Ken Burns pan/zoom in/out instead of constant speed
    auto_color_enabled: auto-levels each photo's own contrast/exposure
    audio_normalize_enabled: peak-normalize the audio track to 0dB
    vignette_enabled: subtle darkening toward the frame edges
    grain_enabled: adds a bit of flickering film-grain texture
    caption_slide_enabled: captions slide up into place instead of just fading
    aspect_ratio: one of config.ASPECT_RATIOS' keys, e.g. "16:9 (landscape)",
        "9:16 (vertical / Reels)", "1:1 (square)"
    overlay_paths: optional list of video/gif file paths (e.g. hearts,
        confetti) composited on top of every photo
    photo_captions: optional dict of {filename (lowercase, no path): caption
        text} to caption one or more specific photos (any position, not just
        opening/closing) — overrides title_text/closing_text for that photo
        if it happens to be the opening or closing one.
    preview: render a fast, low-res/low-fps draft instead of the final quality
    progress_cb: optional callable(str) for status updates (e.g. for a UI)
    on_progress: optional callable(fraction: float 0-1, message: str) fed by
        the actual frame-by-frame encode progress (the dominant cost of a
        render) — use this for a real percentage/ETA, not just log lines.
    """
    if not photo_paths:
        raise ValueError("No photos given.")

    def report(msg):
        if progress_cb:
            progress_cb(msg)

    ratio_key = aspect_ratio if aspect_ratio in config.ASPECT_RATIOS else config.DEFAULT_ASPECT_RATIO
    video_size = tuple(config.ASPECT_RATIOS[ratio_key]["preview" if preview else "full"])
    fps = PREVIEW_FPS if preview else FULL_FPS
    vignette_strength = config.VIGNETTE_STRENGTH if vignette_enabled else 0.0
    grain_strength = config.GRAIN_STRENGTH if grain_enabled else 0.0

    report("Reading audio duration…")
    audio = AudioFileClip(audio_path)
    song_dur = audio.duration
    audio.close()
    report(f"Audio length: {song_dur:.1f}s")

    photos = _order_photos(photo_paths, photo_order)

    # With only 1-2 photos there's no separate "middle" group to auto-fit —
    # just show what we have and let the opening/closing durations (or a
    # frozen last frame, via the length-reconciliation step below) cover
    # the rest of the audio.
    if len(photos) == 1:
        schedule = [{"photo": photos[0], "duration": song_dur, "effect": "zoom_in",
                     "lyric": _caption_for(photos[0], photo_captions, title_text or closing_text)}]
    elif len(photos) == 2:
        schedule = [
            {"photo": photos[0], "duration": opening_duration, "effect": "zoom_in",
             "lyric": _caption_for(photos[0], photo_captions, title_text)},
            {"photo": photos[1], "duration": closing_duration, "effect": "zoom_out",
             "lyric": _caption_for(photos[1], photo_captions, closing_text)},
        ]
    else:
        opening_photo, closing_photo = photos[0], photos[-1]
        middle_photos = photos[1:-1]

        content_dur = song_dur - opening_duration - closing_duration
        if content_dur <= 0:
            raise ValueError(
                f"Opening + closing durations ({opening_duration + closing_duration:.1f}s) "
                f"exceed the audio length ({song_dur:.1f}s) — lower them or use a longer audio file."
            )

        raw_clips = _build_schedule(middle_photos, content_dur, photo_duration, photo_captions)
        middle_schedule = _loop_and_trim(raw_clips, content_dur)
        if not middle_schedule:
            raise ValueError("Could not build a photo schedule long enough to cover the audio.")

        # Compensate for cross-dissolve overlap: concatenate_videoclips(...,
        # padding=-TRANSITION_DUR) overlaps every adjacent pair of clips by
        # TRANSITION_DUR seconds, so the rendered video is shorter than the
        # sum of clip durations. Spread that lost time across the middle
        # photos only (opening/closing keep their exact requested duration)
        # so real (moving) photo content fills the whole song instead of a
        # frozen tail at the end.
        total_clips = len(middle_schedule) + 2  # + opening + closing
        overlap_loss = (total_clips - 1) * TRANSITION_DUR
        if overlap_loss > 0.05:
            extra_per_clip = overlap_loss / len(middle_schedule)
            for e in middle_schedule:
                e["duration"] = round(e["duration"] + extra_per_clip, 3)

        schedule = (
            [{"photo": opening_photo, "duration": opening_duration, "effect": "zoom_in",
              "lyric": _caption_for(opening_photo, photo_captions, title_text)}]
            + middle_schedule
            + [{"photo": closing_photo, "duration": closing_duration, "effect": "zoom_out",
                "lyric": _caption_for(closing_photo, photo_captions, closing_text)}]
        )

    report(f"Building {len(schedule)} photo clip(s)…")
    sparkle = SparkleSystem(video_size)
    overlay_clips = load_overlay_clips(overlay_paths)
    if overlay_clips:
        report(f"Loaded {len(overlay_clips)} overlay clip(s).")

    try:
        video_clips = []
        for i, entry in enumerate(schedule):
            clip = build_photo_clip(
                entry["photo"], entry["duration"], entry.get("effect", "zoom_in"), sparkle,
                video_size, fps, lyric=entry.get("lyric", ""), sparkle_enabled=sparkle_enabled,
                ken_burns_enabled=ken_burns_enabled, pastel_grade_enabled=pastel_grade_enabled,
                overlay_clips=overlay_clips, brightness=brightness,
                auto_color_enabled=auto_color_enabled, ease_motion=ease_motion,
                vignette_strength=vignette_strength, grain_strength=grain_strength,
                caption_slide_enabled=caption_slide_enabled,
            )
            effects = []
            if i > 0:
                effects.append(CrossFadeIn(TRANSITION_DUR))
            if i < len(schedule) - 1:
                effects.append(CrossFadeOut(TRANSITION_DUR))
            if effects:
                clip = clip.with_effects(effects)
            video_clips.append(clip)
            report(f"  [{i + 1}/{len(schedule)}] {entry['photo']} ({entry['duration']:.1f}s, {entry.get('effect')})")

        report("Concatenating clips…")
        final_video = concatenate_videoclips(video_clips, padding=-TRANSITION_DUR, method="compose")

        video_dur = final_video.duration
        diff = song_dur - video_dur
        if diff > 0.5:
            last_frame = ImageClip(final_video.get_frame(video_dur - 0.1), duration=diff + 0.5).with_fps(fps)
            final_video = concatenate_videoclips([final_video, last_frame], method="compose")
        elif diff < -0.5:
            final_video = final_video.subclipped(0, song_dur)

        report("Attaching audio…")
        audio_effects = [AudioNormalize()] if audio_normalize_enabled else []
        audio_effects.append(AudioFadeOut(2.0))
        audio_full = AudioFileClip(audio_path).with_effects(audio_effects)
        final_video = final_video.with_audio(audio_full)

        if preview:
            preset, crf, audio_bitrate = "ultrafast", "28", "128k"
        else:
            preset, crf, audio_bitrate = config.FINAL_PRESET, config.FINAL_CRF, config.FINAL_AUDIO_BITRATE
        report(f"Rendering {final_video.duration:.0f}s of video at {video_size[0]}x{video_size[1]}@{fps}fps…")
        video_logger = _EncodeProgressBridge(on_progress) if on_progress else None
        final_video.write_videofile(
            output_path, fps=fps,
            codec="libx264", audio_codec="aac", audio_bitrate=audio_bitrate,
            preset=preset, ffmpeg_params=["-crf", crf],
            threads=4, logger=video_logger,
        )
    finally:
        close_overlay_clips(overlay_clips)

    report(f"Done -> {output_path}")
    return output_path
