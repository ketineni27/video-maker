"""
Upload photos + an audio file, get an MP4 back.

Run:
    python3 app.py

Opens a local web UI (Gradio) at http://127.0.0.1:7860
"""

import os
import tempfile

import gradio as gr

import config
import engine


def on_generate(photos, audio_path, photo_order, per_photo_dur, title_text,
                 closing_text, opening_dur, closing_dur, sparkle, preview):
    if not photos:
        raise gr.Error("Please upload at least one photo.")
    if not audio_path:
        raise gr.Error("Please upload an audio file.")

    photo_paths = [p if isinstance(p, str) else p.name for p in photos]

    logs = []
    def cb(msg):
        logs.append(msg)

    out_dir = tempfile.mkdtemp(prefix="video-maker-")
    output_path = os.path.join(out_dir, "preview.mp4" if preview else "output.mp4")
    duration = per_photo_dur if per_photo_dur and per_photo_dur > 0 else None

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
            preview=preview,
            progress_cb=cb,
        )
    except Exception as e:
        raise gr.Error(f"Failed to generate video: {e}")

    return output_path, "\n".join(logs)


with gr.Blocks(title="Photo + Audio -> Video") as demo:
    gr.Markdown("## 🎬 Photo + Audio → Video\nUpload your photos and a song, then hit Generate.")

    with gr.Row():
        with gr.Column():
            photos_in = gr.Files(label="Photos", file_count="multiple", file_types=["image"])
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
                preview_in = gr.Checkbox(
                    value=config.DEFAULT_QUICK_PREVIEW,
                    label="Quick preview (fast draft, lower quality — uncheck for the final export)",
                )

            generate_btn = gr.Button("Generate Video", variant="primary")

        with gr.Column():
            video_out = gr.Video(label="Generated Video")
            log_out = gr.Textbox(label="Log", lines=14, interactive=False)

    generate_btn.click(
        on_generate,
        inputs=[photos_in, audio_in, photo_order_in, per_photo_dur_in,
                title_text_in, closing_text_in, opening_dur_in, closing_dur_in,
                sparkle_in, preview_in],
        outputs=[video_out, log_out],
    )

if __name__ == "__main__":
    # Locally this defaults to 127.0.0.1:7860 (matches what launching this
    # app's self-check expects on macOS). In the Docker container used for
    # deployment, GRADIO_SERVER_NAME=0.0.0.0 and PORT are set so the host's
    # reverse proxy (e.g. Render) can actually reach the server.
    server_name = os.environ.get("GRADIO_SERVER_NAME", "127.0.0.1")
    server_port = int(os.environ.get("PORT", 7860))
    demo.queue().launch(server_name=server_name, server_port=server_port)
