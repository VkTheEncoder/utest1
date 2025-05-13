# /root/utest1/Dockerfile
FROM python:3.10-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy your bot code
COPY . .

# Ensure your bot reads ANIWATCH_API_BASE and Telegram creds from env
CMD ["python3", "main.py"]
