FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY src /app/src

# Read a request from standard input and write the selected JSON (or
# UNRESOLVABLE) to standard output.
ENTRYPOINT ["python", "-m", "resolver"]
