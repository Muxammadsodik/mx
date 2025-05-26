# Dockerfile for IELTS Bot with Tesseract OCR support
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libtesseract-dev \
    libleptonica-dev \
    poppler-utils \
    build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set environment variable for tesseract data path
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/4.00/tessdata

# Set working directory
WORKDIR /app

# Copy all files to working directory
COPY . /app

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Expose port for health check
EXPOSE 8080

# Run the bot
CMD ["python", "main.py"]
