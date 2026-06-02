# Upgraded to Python 3.11 for better performance (Reviewer Suggestion)
FROM python:3.11-slim

# Install FFmpeg (Crucial for yt-dlp merging)
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Run Uvicorn using the shell format so Render can dynamically inject its $PORT variable
# If $PORT isn't found (like testing locally), it automatically falls back to 8000
CMD uvicorn backend:app --host 0.0.0.0 --port ${PORT:-8000}
