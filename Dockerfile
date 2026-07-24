# Photo + Audio -> Video web app (Gradio UI + moviepy/ffmpeg renderer).
FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY config.py engine.py app.py /app/

ENV GRADIO_SERVER_NAME=0.0.0.0
# Render (and most PaaS hosts) inject PORT at runtime; 7860 is the local fallback.
ENV PORT=7860
EXPOSE 7860

CMD ["python3", "app.py"]
