FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    FACEAUTH_DATA_DIR=/data FACEAUTH_MODEL_DIR=/app/models

COPY requirements.txt .
# headless OpenCV: no GUI libraries needed in a container
RUN pip install --no-cache-dir -r requirements.txt \
 && pip uninstall -y opencv-python \
 && pip install --no-cache-dir opencv-python-headless

COPY faceauth ./faceauth
COPY web ./web
COPY scripts ./scripts
RUN python scripts/download_models.py /app/models

RUN useradd -r -u 10001 faceauth && mkdir -p /data && chown faceauth /data
USER faceauth
VOLUME ["/data"]
EXPOSE 8100
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8100/health')"
CMD ["python", "-m", "faceauth", "--host", "0.0.0.0", "--port", "8100", "--no-browser"]
