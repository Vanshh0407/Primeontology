FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt backend/requirements-optional.txt ./
# INSTALL_OPTIONAL=1 adds OCR for scanned PDFs and the semantic embedding model support (larger image). 0 = lean image.
ARG INSTALL_OPTIONAL=1
RUN pip install --no-cache-dir -r requirements.txt gunicorn
# OCR pulls in OpenCV, which needs these two system libraries on the slim base image
RUN if [ "$INSTALL_OPTIONAL" = "1" ]; then       apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*       && pip install --no-cache-dir -r requirements-optional.txt;     fi
COPY backend/ .
ENV PRIME_ONTOLOGY_MODEL_DIR=/app/.model_cache HF_HUB_DISABLE_SYMLINKS_WARNING=1
RUN useradd -r -u 10001 prime && mkdir -p /app/.model_cache && chown -R prime /app
USER prime
EXPOSE 8008
# Migrate on start (optionally seed the DEMO login admin/admin123), then serve. The standalone app requires a login.
CMD ["sh", "-c", "python manage.py migrate --noinput && if [ \"$PRIME_SEED_DEMO_USER\" = \"1\" ]; then python manage.py seed_demo_users; fi && exec gunicorn config.wsgi:application --bind 0.0.0.0:8008 --workers 3 --timeout 120"]
