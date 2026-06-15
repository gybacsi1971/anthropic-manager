FROM python:3.12-slim

WORKDIR /app

# Függőségek (psycopg2-binary és cryptography wheel-ekkel jönnek — nincs build-dep)
COPY backend/requirements.txt ./backend/
RUN pip install --no-cache-dir -r backend/requirements.txt

# Alkalmazás
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY VERSION ./VERSION
COPY VERSIONINFO/ ./VERSIONINFO/

ENV TZ=Europe/Budapest
# A konténer non-root UID-vel fut (compose `user:` mező). A python:slim image-ben
# ehhez nincs /etc/passwd rekord, ezért a HOME-ot explicit beállítjuk.
ENV HOME=/tmp

EXPOSE 8000

# A /api/version végpont auth- és DB-mentes → megbízható healthcheck.
HEALTHCHECK --interval=15s --timeout=5s --retries=3 --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/version')"

# 1 worker: egyetlen háttér-ütemező példány (l. scheduler.py). A collector
# Postgres advisory lock-ja akkor is véd, ha a jövőben több workert indítanánk.
# --proxy-headers + --forwarded-allow-ips=*: a Traefik X-Forwarded-* fejléceit
# Uvicorn natívan kezelje (eredeti kliens IP, https-séma a Secure cookie-hoz).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--app-dir", "backend", "--proxy-headers", "--forwarded-allow-ips=*"]
