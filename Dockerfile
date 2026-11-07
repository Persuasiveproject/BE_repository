FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --create-home app

COPY . .

USER 1000:1000

CMD ["gunicorn", "--config", "gunicorn.conf.py", "config.wsgi:application"]
