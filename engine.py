"""
Reusable photo + audio -> MP4 video engine.

Same rendering techniques as the baby-birthday-video project (Ken Burns
pans/zooms, sparkle overlay, pastel colour grade, cross-dissolve
transitions, portrait/landscape-aware fitting), simplified down to the
core use case: give it a list of photos and one audio file, get an MP4
back. No lyric/caption-sync map — that didn't pan out well before, so
it's left out here (worth revisiting later as its own feature).

Import generate_video() and call it directly, or drive it from app.py.
"""

import math
import random

import numpy as np
from PIL import Image, ImageFilter, ImageDraw, ImageFont
from moviepy import VideoClip, AudioFileClip, concatenate_videoclips, ImageClip
from moviepy.video.fx import CrossFadeIn, CrossFadeOut
from moviepy.audio.fx import AudioFadeOut

import config

FULL_VIDEO_SIZE = (config.VIDEO_WIDTH, config.VIDEO_HEIGHT)
FULL_FPS = config.FPS
PREVIEW_VIDEO_SIZE = (config.PREVIEW_WIDTH, config.PREVIEW_HEIGHT)
PREVIEW_FPS = config.PREVIEW_FPS

TRANSITION_DUR = config.TRANSITION_DURATION
DIRECTIONS = ["zoom_in", "zoom_out", "pan_left", "pan_right"]


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


def load_photo(path, size):
    img = Image.open(path).convert("RGB")
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


# ═══════════════════════════════════════════════════════════════
#  KEN BURNS
# ═══════════════════════════════════════════════════════════════

def ken_burns_frame(base_img, t, duration, direction, video_size, portrait=False):
    W, H = video_size
    img_h, img_w = base_img.shape[:2]
    progress = t / max(duration, 0.001)

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
    return apply_pastel_grade(
        np.array(Image.fromarray(cropped).resize((W, H), Image.LANCZOS))
    )


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
#  FONT / TEXT
# ═══════════════════════════════════════════════════════════════

def get_font(size):
    import os
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
                      lyric="", sparkle_enabled=True):
    base_img, portrait = load_photo(photo_path, video_size)
    W, H = video_size

    lyric_layer = None
    if lyric and lyric.strip():
        font = get_font(52)
        txt_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(txt_layer)
        bbox = d.textbbox((0, 0), lyric, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        tx, ty = (W - tw) // 2, H - 130
        pad = 20
        d.rounded_rectangle([tx - pad, ty - pad // 2, tx + tw + pad, ty + th + pad // 2], radius=16, fill=(0, 0, 0, 100))
        d.text((tx + 2, ty + 2), lyric, font=font, fill=(255, 210, 230, 200))
        d.text((tx, ty), lyric, font=font, fill=(255, 255, 255, 245))
        lyric_layer = np.array(txt_layer)

    def make_frame(t):
        frame = ken_burns_frame(base_img, t, duration, direction, video_size, portrait)
        out = Image.fromarray(frame).convert("RGBA")

        if sparkle_enabled:
            sparks = sparkle_sys.render(t)
            out = Image.alpha_composite(out, Image.fromarray(sparks))

        if lyric_layer is not None:
            fade = min(t / 0.6, 1.0, (duration - t) / 0.6)
            l_arr = lyric_layer.copy()
            l_arr[:, :, 3] = (l_arr[:, :, 3] * max(fade, 0)).astype(np.uint8)
            out = Image.alpha_composite(out, Image.fromarray(l_arr))

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


def _build_schedule(photos, content_dur, photo_duration=None):
    n = len(photos)
    per = photo_duration if photo_duration else max(content_dur / n, 1.0)
    clips = [{"photo": photos[i], "duration": round(per, 3), "effect": DIRECTIONS[i % 4]} for i in range(n)]
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
    preview=False,
    progress_cb=None,
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
    preview: render a fast, low-res/low-fps draft instead of the final quality
    progress_cb: optional callable(str) for status updates (e.g. for a UI)
    """
    if not photo_paths:
        raise ValueError("No photos given.")

    def report(msg):
        if progress_cb:
            progress_cb(msg)

    video_size = PREVIEW_VIDEO_SIZE if preview else FULL_VIDEO_SIZE
    fps = PREVIEW_FPS if preview else FULL_FPS

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
                     "lyric": title_text or closing_text}]
    elif len(photos) == 2:
        schedule = [
            {"photo": photos[0], "duration": opening_duration, "effect": "zoom_in", "lyric": title_text},
            {"photo": photos[1], "duration": closing_duration, "effect": "zoom_out", "lyric": closing_text},
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

        raw_clips = _build_schedule(middle_photos, content_dur, photo_duration)
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
            [{"photo": opening_photo, "duration": opening_duration, "effect": "zoom_in", "lyric": title_text}]
            + middle_schedule
            + [{"photo": closing_photo, "duration": closing_duration, "effect": "zoom_out", "lyric": closing_text}]
        )

    report(f"Building {len(schedule)} photo clip(s)…")
    sparkle = SparkleSystem(video_size)
    video_clips = []
    for i, entry in enumerate(schedule):
        clip = build_photo_clip(
            entry["photo"], entry["duration"], entry.get("effect", "zoom_in"), sparkle,
            video_size, fps, lyric=entry.get("lyric", ""), sparkle_enabled=sparkle_enabled,
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
    audio_full = AudioFileClip(audio_path).with_effects([AudioFadeOut(2.0)])
    final_video = final_video.with_audio(audio_full)

    if preview:
        preset, crf, audio_bitrate = "ultrafast", "28", "128k"
    else:
        preset, crf, audio_bitrate = config.FINAL_PRESET, config.FINAL_CRF, config.FINAL_AUDIO_BITRATE
    report(f"Rendering {final_video.duration:.0f}s of video at {video_size[0]}x{video_size[1]}@{fps}fps…")
    final_video.write_videofile(
        output_path, fps=fps,
        codec="libx264", audio_codec="aac", audio_bitrate=audio_bitrate,
        preset=preset, ffmpeg_params=["-crf", crf],
        threads=4, logger=None,
    )
    report(f"Done -> {output_path}")
    return output_path
