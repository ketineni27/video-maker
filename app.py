"""
Upload photos + an audio file, get an MP4 back.

Run:
    python3 app.py

Opens a local web UI (Gradio) at http://127.0.0.1:7860
"""

import json
import os
import tempfile
import threading
import time

import gradio as gr
from PIL import Image

import config
import engine  # registers the HEIC/HEIF opener with Pillow as a side effect

HEIC_EXTS = {".heic", ".heif"}
_heic_preview_cache = {}  # original path -> converted JPEG preview path

# Trying to trap the browser's Back/Forward buttons in JS turned out not to
# be reliable enough — a reload can still slip through and wipe gr.State
# (which only lives in server memory per browser session). This sidesteps
# the whole problem: the accumulated photo/overlay lists are mirrored to a
# small file on disk on every change, and restored from there on every page
# load, regardless of *why* the page reloaded (back button, refresh, closed
# tab). The underlying uploaded files themselves aren't touched — they stay
# wherever Gradio put them as long as this app process keeps running.
SESSION_FILE = os.path.join(tempfile.gettempdir(), "video-maker-session.json")


def save_session(photos=None, overlays=None):
    data = {}
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE) as f:
                data = json.load(f)
        except Exception:
            data = {}
    if photos is not None:
        data["photos"] = photos
    if overlays is not None:
        data["overlays"] = overlays
    try:
        with open(SESSION_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def load_session():
    try:
        with open(SESSION_FILE) as f:
            data = json.load(f)
    except Exception:
        data = {}
    photos = [p for p in data.get("photos", []) if os.path.exists(p)]
    overlays = [p for p in data.get("overlays", []) if os.path.exists(p)]
    return photos, overlays


def restore_session():
    photos, overlays = load_session()
    return photos, gallery_value(photos), overlays, format_file_list(overlays)


def preview_path(path):
    """Browsers can't render HEIC/HEIF inline, so the gallery needs a JPEG
    stand-in for those — the original file is untouched and still what
    actually gets used to generate the video."""
    if os.path.splitext(path)[1].lower() not in HEIC_EXTS:
        return path
    cached = _heic_preview_cache.get(path)
    if cached and os.path.exists(cached):
        return cached
    out_path = os.path.join(tempfile.gettempdir(), f"heic-preview-{abs(hash(path))}.jpg")
    Image.open(path).convert("RGB").save(out_path, "JPEG", quality=85)
    _heic_preview_cache[path] = out_path
    return out_path


def gallery_value(paths):
    """(path, caption) tuples so the gallery shows each photo's filename —
    makes it easy to see what to type in the per-photo captions box."""
    return [(preview_path(p), os.path.basename(p)) for p in paths]


def format_file_list(paths):
    if not paths:
        return "_Nothing added yet._"
    lines = "\n".join(f"- {os.path.basename(p)}" for p in paths)
    return f"**{len(paths)} file(s) added so far:**\n{lines}"


def add_uploads(new_files, accumulated_paths):
    """gr.Files replaces its selection on every new upload AND hides its own
    drop zone once it has a value (only offering remove/clear, no way to add
    more) — so this merges each new batch into a running list kept in State,
    and resets the upload widget back to empty/None so it's always ready to
    accept another batch. The running list is shown separately below it."""
    new_paths = [f if isinstance(f, str) else f.name for f in (new_files or [])]
    merged = (accumulated_paths or []) + new_paths
    save_session(overlays=merged)
    return merged, None, format_file_list(merged)


def clear_uploads():
    save_session(overlays=[])
    return [], None, format_file_list([])


def add_photos(new_files, accumulated_paths):
    new_paths = [f if isinstance(f, str) else f.name for f in (new_files or [])]
    merged = (accumulated_paths or []) + new_paths
    save_session(photos=merged)
    return merged, None, gallery_value(merged), None  # state, reset upload box, gallery, reset selection


def clear_photos():
    save_session(photos=[])
    return [], None, [], None


def on_photo_select(evt: gr.SelectData):
    return evt.index


def remove_selected_photo(accumulated_paths, selected_idx):
    if selected_idx is None:
        raise gr.Error("Click a photo below first, then Remove Selected Photo.")
    if not accumulated_paths or not (0 <= selected_idx < len(accumulated_paths)):
        raise gr.Error("That photo isn't in the list anymore — click another one.")
    remaining = accumulated_paths[:selected_idx] + accumulated_paths[selected_idx + 1:]
    save_session(photos=remaining)
    return remaining, gallery_value(remaining), None


def parse_photo_captions(text):
    """Parse lines like 'filename.jpg: caption text' into {filename: caption}.
    Type \\n inside a caption for a line break, e.g. 'Scotland At A Glance\\n2026'."""
    captions = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        filename, caption = line.split(":", 1)
        filename = filename.strip().lower()
        caption = caption.strip().replace("\\n", "\n")
        if filename and caption:
            captions[filename] = caption
    return captions


def on_generate(photos, audio_path, photo_order, per_photo_dur, title_text,
                 closing_text, opening_dur, closing_dur, sparkle, ken_burns,
                 pastel_grade, brightness, ease_motion, auto_color, audio_normalize,
                 vignette, grain, caption_slide, aspect_ratio,
                 overlays, photo_captions_text, preview,
                 progress=gr.Progress()):
    if not photos:
        raise gr.Error("Please upload at least one photo.")
    if not audio_path:
        raise gr.Error("Please upload an audio file.")

    photo_paths = [p if isinstance(p, str) else p.name for p in photos]
    overlay_paths = [p if isinstance(p, str) else p.name for p in (overlays or [])]
    photo_captions = parse_photo_captions(photo_captions_text)

    out_dir = tempfile.mkdtemp(prefix="video-maker-")
    output_path = os.path.join(out_dir, "preview.mp4" if preview else "output.mp4")
    duration = per_photo_dur if per_photo_dur and per_photo_dur > 0 else None

    # The actual render runs on a background thread while this generator
    # polls its shared state and yields every second or so. Long renders
    # (30+ minutes at full quality) were finishing successfully but never
    # reaching the browser — a plain blocking return after that long with
    # no data sent in between looks exactly like a dead connection to
    # whatever's in the middle (browser/proxy), and it gives up on the
    # request. Yielding regularly keeps it visibly alive the whole time,
    # on top of actually giving you a progress bar.
    state = {"fraction": 0.0, "message": "Starting…", "done": False, "error": None}
    logs = []
    lock = threading.Lock()

    def log_cb(msg):
        with lock:
            logs.append(msg)

    def progress_cb(fraction, message):
        with lock:
            state["fraction"] = fraction
            state["message"] = message

    def run():
        try:
            engine.generate_video(
                photo_paths,
                audio_path,
                output_path,
                photo_duration=duration,
                photo_order=photo_order,
                title_text=title_text or "",
                closing_text=closing_text or "",
                opening_duration=opening_dur,
                closing_duration=closing_dur,
                sparkle_enabled=sparkle,
                ken_burns_enabled=ken_burns,
                pastel_grade_enabled=pastel_grade,
                brightness=brightness,
                ease_motion=ease_motion,
                auto_color_enabled=auto_color,
                audio_normalize_enabled=audio_normalize,
                vignette_enabled=vignette,
                grain_enabled=grain,
                caption_slide_enabled=caption_slide,
                aspect_ratio=aspect_ratio,
                overlay_paths=overlay_paths,
                photo_captions=photo_captions,
                preview=preview,
                progress_cb=log_cb,
                on_progress=progress_cb,
            )
        except Exception as e:
            with lock:
                state["error"] = str(e)
        finally:
            with lock:
                state["done"] = True

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    while True:
        with lock:
            fraction, message, done, error = state["fraction"], state["message"], state["done"], state["error"]
            log_text = "\n".join(logs)
        progress(fraction, desc=message)
        # gr.skip() leaves video_out/output_path_out untouched instead of
        # re-setting them to the same empty value every second — repeatedly
        # writing None/"" to them was what caused the visible flashing.
        yield gr.skip(), gr.skip(), log_text
        if done:
            break
        time.sleep(1)

    if error:
        raise gr.Error(f"Failed to generate video: {error}")

    with lock:
        log_text = "\n".join(logs)
    # Send the plain file path first, as its own update — it doesn't depend
    # on Gradio copying/hashing the (often 100-200MB+) video file the way
    # the preview player does, so it gets through reliably even in the
    # cases where the video player itself has struggled to load a big file.
    yield None, output_path, log_text
    yield output_path, output_path, log_text


LOCK_NAVIGATION_JS = """
<script>
// Photos/overlays are tracked in per-session state, which resets if the
// browser's Back/Forward buttons cause a fresh page load — trap them so
// they never navigate away from the app instead of losing your uploads.
history.pushState(null, '', location.href);
window.addEventListener('popstate', function() {
    history.pushState(null, '', location.href);
});
</script>
"""

GALLERY_CSS = """
#photos_gallery img { transition: transform 0.2s ease; }
#photos_gallery img:hover { transform: scale(1.8); z-index: 10; position: relative; }
"""

with gr.Blocks(
    title="Photo + Audio -> Video", head=LOCK_NAVIGATION_JS, css=GALLERY_CSS,
    # Gradio's own upload/output cache is cleaned up safely in the
    # background while the server keeps running (checks every hour,
    # deletes anything older than a day) — unlike a manual rm -rf, this is
    # Gradio's own mechanism and won't crash a live request.
    delete_cache=(3600, 86400),
) as demo:
    gr.Markdown("## 🎬 Photo + Audio → Video\nUpload your photos and a song, then hit Generate.")

    photo_state = gr.State([])
    overlay_state = gr.State([])

    with gr.Row():
        with gr.Column():
            photos_in = gr.Files(
                label="Add photos (upload, then repeat to add more — it resets after each add)",
                file_count="multiple", file_types=["image"],
            )
            photos_gallery = gr.Gallery(
                label="Photos added so far — click one, then Remove Selected Photo (hover to zoom in)",
                elem_id="photos_gallery",
                columns=8, height=90, object_fit="cover",
            )
            selected_photo_idx = gr.State(None)
            with gr.Row():
                clear_photos_btn = gr.Button("Clear all photos", size="sm")
                remove_photo_btn = gr.Button("Remove selected photo", size="sm")
            audio_in = gr.Audio(label="Audio / Song", type="filepath")

            with gr.Accordion("Options", open=False):
                photo_order_in = gr.Radio(
                    ["as-given", "alphabetical", "random"], value=config.DEFAULT_PHOTO_ORDER,
                    label="Photo order",
                )
                per_photo_dur_in = gr.Slider(
                    minimum=0, maximum=15, step=0.5, value=config.DEFAULT_PHOTO_DURATION,
                    label="Seconds per middle photo (0 = auto-fit to remaining time)",
                )
                title_text_in = gr.Textbox(value=config.DEFAULT_TITLE_TEXT, label="Opening caption (optional)")
                closing_text_in = gr.Textbox(value=config.DEFAULT_CLOSING_TEXT, label="Closing caption (optional)")
                opening_dur_in = gr.Slider(
                    minimum=0.5, maximum=15, step=0.5, value=config.DEFAULT_OPENING_DURATION,
                    label="Opening photo duration (seconds)",
                )
                closing_dur_in = gr.Slider(
                    minimum=0.5, maximum=15, step=0.5, value=config.DEFAULT_CLOSING_DURATION,
                    label="Closing photo duration (seconds)",
                )
                sparkle_in = gr.Checkbox(value=config.SPARKLE_ENABLED_DEFAULT, label="Sparkle overlay")
                ken_burns_in = gr.Checkbox(value=config.KEN_BURNS_ENABLED_DEFAULT, label="Ken Burns pan/zoom motion")
                pastel_grade_in = gr.Checkbox(value=config.PASTEL_GRADE_ENABLED_DEFAULT, label="Pastel colour grade")
                brightness_in = gr.Slider(
                    minimum=0.5, maximum=2.0, step=0.05, value=config.BRIGHTNESS_DEFAULT,
                    label="Brightness (1.0 = unchanged)",
                )
                gr.Markdown("**Professional-grade polish**")
                ease_motion_in = gr.Checkbox(
                    value=config.EASE_KEN_BURNS_DEFAULT,
                    label="Ease Ken Burns motion (slow-fast-slow instead of constant speed)",
                )
                auto_color_in = gr.Checkbox(
                    value=config.AUTO_COLOR_ENABLED_DEFAULT,
                    label="Auto color/exposure correction per photo",
                )
                audio_normalize_in = gr.Checkbox(
                    value=config.AUDIO_NORMALIZE_DEFAULT,
                    label="Normalize audio loudness",
                )
                vignette_in = gr.Checkbox(
                    value=config.VIGNETTE_ENABLED_DEFAULT,
                    label="Vignette (subtle darkened edges)",
                )
                grain_in = gr.Checkbox(
                    value=config.GRAIN_ENABLED_DEFAULT,
                    label="Film grain texture",
                )
                caption_slide_in = gr.Checkbox(
                    value=config.CAPTION_SLIDE_ENABLED_DEFAULT,
                    label="Captions slide into place (instead of just fading)",
                )
                aspect_ratio_in = gr.Radio(
                    list(config.ASPECT_RATIOS.keys()), value=config.DEFAULT_ASPECT_RATIO,
                    label="Aspect ratio",
                )
                overlays_in = gr.Files(
                    label="Add overlay video(s) (optional — e.g. hearts, confetti, sparkle animation; "
                          "upload, then repeat to add more)",
                    file_count="multiple",
                    file_types=[".mp4", ".mov", ".webm", ".avi", ".gif"],
                )
                overlays_list_md = gr.Markdown("_Nothing added yet._")
                clear_overlays_btn = gr.Button("Clear overlay videos", size="sm")
                photo_captions_in = gr.Textbox(
                    label="Captions for specific photos (optional)",
                    placeholder="one per line: filename.jpg: your caption text",
                    lines=3,
                    info="Matches by filename — overrides the opening/closing caption above if it's that photo. "
                         "Type \\n for a line break, e.g. 01.jpeg: Scotland At A Glance\\n2026",
                )
                preview_in = gr.Checkbox(
                    value=config.DEFAULT_QUICK_PREVIEW,
                    label="Quick preview (fast draft, lower quality — uncheck for the final export)",
                )

            generate_btn = gr.Button("Generate Video", variant="primary")

        with gr.Column():
            video_out = gr.Video(label="Generated Video")
            output_path_out = gr.Textbox(
                label="Output file path (use this — e.g. via Finder's Go > Go to Folder — "
                      "if the preview above doesn't load for a large file)",
                interactive=False,
            )
            log_out = gr.Textbox(label="Log", lines=14, interactive=False)

    photos_in.upload(add_photos, inputs=[photos_in, photo_state],
                      outputs=[photo_state, photos_in, photos_gallery, selected_photo_idx])
    clear_photos_btn.click(clear_photos,
                            outputs=[photo_state, photos_in, photos_gallery, selected_photo_idx])
    photos_gallery.select(on_photo_select, outputs=[selected_photo_idx])
    remove_photo_btn.click(remove_selected_photo, inputs=[photo_state, selected_photo_idx],
                            outputs=[photo_state, photos_gallery, selected_photo_idx])

    overlays_in.upload(add_uploads, inputs=[overlays_in, overlay_state],
                        outputs=[overlay_state, overlays_in, overlays_list_md])
    clear_overlays_btn.click(clear_uploads, outputs=[overlay_state, overlays_in, overlays_list_md])

    generate_btn.click(
        on_generate,
        inputs=[photo_state, audio_in, photo_order_in, per_photo_dur_in,
                title_text_in, closing_text_in, opening_dur_in, closing_dur_in,
                sparkle_in, ken_burns_in, pastel_grade_in, brightness_in,
                ease_motion_in, auto_color_in, audio_normalize_in,
                vignette_in, grain_in, caption_slide_in, aspect_ratio_in,
                overlay_state, photo_captions_in, preview_in],
        outputs=[video_out, output_path_out, log_out],
        show_progress="minimal",
    )

    demo.load(
        restore_session, inputs=None,
        outputs=[photo_state, photos_gallery, overlay_state, overlays_list_md],
    )

def cleanup_old_outputs():
    """Delete leftover render output folders from previous runs of this app.
    Only called once at startup, before the server accepts any requests —
    safe because this process hasn't created any output folders of its own
    yet, so anything matching the pattern is guaranteed to be stale (from a
    prior run that's already exited). Never call this while the server is
    live: deleting a folder a running request still has open can crash it —
    that's exactly what happened when this was done manually via Bash."""
    import glob
    import shutil
    for path in glob.glob(os.path.join(tempfile.gettempdir(), "video-maker-*")):
        if os.path.isdir(path):
            try:
                shutil.rmtree(path)
            except Exception:
                pass


if __name__ == "__main__":
    cleanup_old_outputs()

    # Locally this defaults to 127.0.0.1:7860 (matches what launching this
    # app's self-check expects on macOS). In Docker, GRADIO_SERVER_NAME=0.0.0.0
    # and PORT are set so the container is reachable from outside it.
    server_name = os.environ.get("GRADIO_SERVER_NAME", "127.0.0.1")
    server_port = int(os.environ.get("PORT", 7860))
    demo.queue().launch(server_name=server_name, server_port=server_port)
