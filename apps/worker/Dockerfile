FROM python:3.12-slim

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-hin \
    tesseract-ocr-mar \
    tesseract-ocr-eng \
    libpoppler-cpp-dev \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml poetry.lock* ./
RUN pip install --no-cache-dir poetry && \
    poetry config virtualenvs.create false && \
    poetry install --no-dev

COPY app/ ./app/
COPY /libs/ /libs/

ENV PYTHONPATH=/app:/libs

CMD ["celery", "-A", "app.worker", "worker", "--loglevel=info", "-Q", "default,ocr,embed,graph", "-c", "4"]
