# Use an official Python image
FROM python:3.13-slim

# The published image only ever runs in production on the NAS. config.py keys
# its required-variable check off this, and .env.production doesn't set it.
# Compose sets it too; if that line is ever lost the app still fails loudly on
# a missing SECRET_KEY rather than running with broken sessions.
ENV APP_ENV=production

# Don't build bytecode
ENV PYTHONDONTWRITEBYTECODE=1
# Flush logs immediately
ENV PYTHONUNBUFFERED=1
# Entry point for Flask app
ENV FLASK_APP=app.py


# Set working directory
WORKDIR /app

# Copy just the requirements.txt file first (to leverage caching)
COPY requirements.txt .

# Install dependencies
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Now, copy the rest of your application code
COPY . .

# Build provenance, injected by CI (see .github/workflows/docker-publish.yml).
# Declared after the COPY so the pip layer above stays cached across builds.
# APP_COMMIT is what busts the static asset cache: APP_VERSION only moves at a
# release tag, but any build off main can change scripts.js.
ARG VERSION=dev
ARG COMMIT=unknown
ARG BUILD_DATE=
ENV APP_VERSION=$VERSION \
    APP_COMMIT=$COMMIT \
    APP_BUILD_DATE=$BUILD_DATE

# The port gunicorn binds below
EXPOSE 80

# Run the app.
# gthread rather than the default sync worker: sync workers can't do keep-alive,
# so every static asset needed a fresh TCP connection. Stay on a single process
# so the in-memory Bookings singleton remains a single copy.
CMD ["gunicorn", "-b", "0.0.0.0:80", "app:app", \
    "--workers", "1", "--worker-class", "gthread", "--threads", "4", \
    "--keep-alive", "5", "--access-logfile", "-", "--error-logfile", "-"]
