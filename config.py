"""
Video Maker settings — change the values below to change the app's
defaults. No code knowledge needed: edit the number/text/True-False after
the `=` sign and save. Restart the app (stop it and run `python3 app.py`
again) for changes to take effect.
"""

# ── Video quality ─────────────────────────────────────────────────
VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
FPS = 30

# "Quick preview" mode renders at this much smaller size/fps instead, so
# you can check pacing/order/captions in seconds instead of minutes.
PREVIEW_WIDTH = 640
PREVIEW_HEIGHT = 360
PREVIEW_FPS = 12

# Final-render encode quality. CRF is the main quality knob for H.264:
# lower = higher quality + bigger file + slower encode (18 is already
# visually near-lossless; go to 16 or 14 for a further bump). PRESET
# trades encode time for compression efficiency at the SAME crf/quality
# ("slow" is a good balance; "slower"/"veryslow" squeeze out a bit more
# quality-per-megabyte but take noticeably longer to render).
FINAL_CRF = "16"
FINAL_PRESET = "slow"
FINAL_AUDIO_BITRATE = "192k"

# ── Effects ───────────────────────────────────────────────────────
SPARKLE_ENABLED_DEFAULT = True   # glitter/sparkle particles floating over every photo
SPARKLE_COUNT = 25               # how many sparkle particles (lower = subtler, higher = busier)
TRANSITION_DURATION = 1.0        # seconds of cross-dissolve fade between photos
KEN_BURNS_ENABLED_DEFAULT = True    # slow pan/zoom motion; off = still photos
PASTEL_GRADE_ENABLED_DEFAULT = True  # warm pink/lavender colour grade
BRIGHTNESS_DEFAULT = 1.0            # 1.0 = unchanged, >1.0 = brighter, <1.0 = darker

# Ease the Ken Burns pan/zoom in and out instead of moving at constant
# speed — a classic, essentially free "makes it feel less like a
# slideshow" fix (real camera moves rarely hold one constant speed).
EASE_KEN_BURNS_DEFAULT = True

# Auto-levels each photo's own contrast/exposure (via Pillow's autocontrast)
# before anything else — evens out photos shot on different cameras/in
# different lighting so the whole video feels visually consistent.
AUTO_COLOR_ENABLED_DEFAULT = True
AUTO_COLOR_CUTOFF = 1  # % of extreme pixels ignored at each end of the histogram

# Peak-normalizes the audio track to 0dB before the fade-out — avoids a
# quiet source track making the whole video feel underwhelming.
AUDIO_NORMALIZE_DEFAULT = True

# Subtle darkening toward the frame edges — classic, cheap cinematic polish.
VIGNETTE_ENABLED_DEFAULT = True
VIGNETTE_STRENGTH = 0.35  # 0 = none, 1 = strong

# Adds a bit of flickering grain/texture instead of perfectly flat digital
# footage. Off by default — a stylistic choice, not universally wanted.
GRAIN_ENABLED_DEFAULT = False
GRAIN_STRENGTH = 10          # additive noise amplitude (0-255ish)
GRAIN_PATTERN_COUNT = 12     # precomputed noise tiles cycled through, so we
                              # aren't generating fresh random noise every frame

# Captions slide up into place (and back down on the way out) instead of
# just fading — a "lower third" broadcast-graphics feel.
CAPTION_SLIDE_ENABLED_DEFAULT = True
CAPTION_SLIDE_DISTANCE = 40  # pixels

# Overlay video clips (e.g. hearts/confetti/sparkle animations) uploaded
# through the UI, composited on top of every photo. A dark/black background
# in the clip is treated as transparent (via a luminance-based mask) unless
# the file already has real alpha, so most "effect on black" stock clips
# work without any pre-editing.
OVERLAY_OPACITY = 0.70
OVERLAY_SUPPORTED_EXTS = {".mov", ".mp4", ".webm", ".avi", ".gif"}

# ── Defaults shown when the web UI opens ───────────────────────────
DEFAULT_PHOTO_ORDER = "as-given"   # "as-given" (upload order) | "alphabetical" | "random"
DEFAULT_PHOTO_DURATION = 0         # seconds per middle photo; 0 = auto-fit evenly to the time left
DEFAULT_TITLE_TEXT = ""           # caption shown on the opening photo
DEFAULT_CLOSING_TEXT = ""         # caption shown on the closing photo
DEFAULT_QUICK_PREVIEW = True      # start with the fast/low-quality draft checkbox on

# The first photo (opening) and last photo (closing) get their own fixed
# duration instead of sharing the auto-split with the middle photos —
# handy for a steady title-card-style open/close on every video.
DEFAULT_OPENING_DURATION = 3.5
DEFAULT_CLOSING_DURATION = 3.5

# ── Export aspect ratio ──────────────────────────────────────────────
# All derived from VIDEO_HEIGHT/PREVIEW_HEIGHT above so the "quality"
# dimension stays consistent across ratios (only the framing changes).
ASPECT_RATIOS = {
    "16:9 (landscape)": {
        "full": (VIDEO_WIDTH, VIDEO_HEIGHT),
        "preview": (PREVIEW_WIDTH, PREVIEW_HEIGHT),
    },
    "9:16 (vertical / Reels)": {
        "full": (VIDEO_HEIGHT, VIDEO_WIDTH),
        "preview": (PREVIEW_HEIGHT, PREVIEW_WIDTH),
    },
    "1:1 (square)": {
        "full": (VIDEO_HEIGHT, VIDEO_HEIGHT),
        "preview": (PREVIEW_HEIGHT, PREVIEW_HEIGHT),
    },
}
DEFAULT_ASPECT_RATIO = "16:9 (landscape)"
