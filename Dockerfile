FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

# Непривилегированный пользователь; /data принадлежит ему, поэтому новый
# именованный том наследует эти права
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
 && mkdir -p /data/storage \
 && chown -R app:app /data

# Зависимости отдельным слоем: пересобираются только при смене requirements.txt
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app
COPY docker/entrypoint.sh /entrypoint.sh
# sed убирает окончания строк Windows (CRLF), из-за них скрипт не запускается в Linux
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

USER app
EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
# forwarded-allow-ips="*" безопасно только пока порт приложения недоступен снаружи
# (в compose он не публикуется, до него достаёт один Nginx)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]