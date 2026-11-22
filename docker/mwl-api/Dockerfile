FROM python:3.11.18-slim

WORKDIR /app

RUN apt-get update && apt-get install -y gcc && rm -rf /var/lib/apt/lists/*

COPY docker/mwl-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY docker/mwl-api/ .
COPY config.py .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
