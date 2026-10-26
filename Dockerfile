# Playwright Chromium runtime for Render Docker deployments
# Image tag MUST match the playwright package version in requirements.txt
FROM mcr.microsoft.com/playwright/python:v1.64.1-noble

ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV BROWSER_WORKER_ENABLED=false
ENV BROWSER_ALLOW_SUBMIT=false

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 10000

# PORT is injected by Render
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"]
