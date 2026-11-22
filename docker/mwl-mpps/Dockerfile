FROM python:3.11.18-slim

WORKDIR /app

RUN apt-get update && apt-get install -y gcc && rm -rf /var/lib/apt/lists/*

COPY docker/mwl-mpps/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY docker/mwl-mpps/app.py .
COPY config.py .

CMD ["python", "app.py"]
