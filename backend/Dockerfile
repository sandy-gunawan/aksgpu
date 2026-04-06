FROM pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime

WORKDIR /app

# Install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

EXPOSE 8000

# Default: run the FastAPI server
# CronJob overrides with: python -m scripts.train
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
