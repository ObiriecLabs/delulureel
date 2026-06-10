FROM python:3.11-slim

# Dipendenze: wget per download, runpod SDK
RUN apt-get update && apt-get install -y --no-install-recommends wget curl && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir runpod

# Script di download (copiato dal progetto)
COPY download_models.sh /download_models.sh
RUN chmod +x /download_models.sh

# Handler serverless
COPY handler.py /handler.py

CMD ["python", "-u", "/handler.py"]
