FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn
COPY backend/ .
RUN useradd -r -u 10001 prime && chown -R prime /app
USER prime
EXPOSE 8008
# Migrate on start (optionally seed the DEMO login admin/admin123), then serve. The standalone app requires a login.
CMD ["sh", "-c", "python manage.py migrate --noinput && if [ \"$PRIME_SEED_DEMO_USER\" = \"1\" ]; then python manage.py seed_demo_users; fi && exec gunicorn config.wsgi:application --bind 0.0.0.0:8008 --workers 3 --timeout 120"]
