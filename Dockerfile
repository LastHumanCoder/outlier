# ffmpeg is the only real system dependency. Everything else is pip packages.
FROM python:3.12-slim

# fonts-dejavu-core is not optional: generating the sample dataset burns text
# into the video with ffmpeg's drawtext filter, which needs a font file on disk.
# The slim image ships none, so without this the fixture step below fails the
# build with "No usable font found".
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Sample data so a fresh deploy has something to show immediately rather than
# an empty dashboard. Cheap: it is ffmpeg drawing coloured cards.
RUN python -m outlier.cli fixtures

ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
