# OffsAid API image for Render (or any container host).
# The scam-language model is NOT in this image: set SCAM_MODEL_API_URL / SCAM_MODEL_API_TOKEN to use
# the Modal endpoint (modal_scam_model.py). Without them the wording check runs in rules_only mode.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Tesseract: OCR for scanned letters when the vision model cannot be used.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN chmod +x scripts/render_start.sh \
    && useradd --system --home /app offsaid \
    && chown -R offsaid /app
USER offsaid

CMD ["scripts/render_start.sh"]
