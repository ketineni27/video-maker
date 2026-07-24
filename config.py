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
