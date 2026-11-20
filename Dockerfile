FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

# For local ASR inside the image, use:
#   docker build --build-arg EXTRAS=".[mcp,rumble]" -t clipkit .
ARG EXTRAS=".[mcp]"
RUN pip install "${EXTRAS}"

ENTRYPOINT ["clipkit"]
CMD ["--help"]
