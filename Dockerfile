FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

WORKDIR /app

# Non-root user. UID/GID 10001 matches the external_source_code user_id/group_id
# attribute used with the pod_security feature in this stack.
RUN groupadd -g 10001 app && useradd -u 10001 -g 10001 -m -s /usr/sbin/nologin app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x entrypoint.sh && chown -R 10001:10001 /app

USER 10001
EXPOSE 8000

CMD ["./entrypoint.sh"]
