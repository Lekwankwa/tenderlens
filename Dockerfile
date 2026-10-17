# TenderLens, packaged for Google Cloud Run (see README "Hosting on Google Cloud Run").
# Also runs anywhere else Docker does: docker build -t tenderlens . && docker run -p 8080:8080 ... tenderlens
FROM python:3.12-slim

# tesseract-ocr: OCR. poppler-utils: pdftoppm/pdftotext/pdfinfo (page rendering and text extraction).
# Same two packages install.sh installs locally and packages.txt installs on Streamlit Community Cloud.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so `docker build` only reinstalls them when requirements.txt actually changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# pilot/pilot.db ships inside the image on purpose (see README "Pilot mode" - Cloud Run's disk is wiped on
# restart/scale-to-zero, so the committed file is what a fresh instance starts from, not a cache of it).
# Everything else under samples/, documents/, out/, .cache/ is excluded by .dockerignore and was never here.

RUN useradd --create-home --uid 1000 tenderlens \
 && chown -R tenderlens:tenderlens /app
USER tenderlens

ENV PYTHONUNBUFFERED=1 \
    TENDERLENS_MAX_UPLOAD_MB=30

EXPOSE 8080

# Shell form so $PORT (Cloud Run sets this; 8080 is its own default) is substituted at container start, not
# baked in at build time. server.maxUploadSize is a hard server-side limit (so an oversized file is never
# even received); TENDERLENS_MAX_UPLOAD_MB above drives the friendlier in-app message pointed at the same
# number - keep the two in sync if this is ever changed.
CMD streamlit run app.py \
    --server.port=${PORT:-8080} \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.maxUploadSize=30
