# Use an official Python image
FROM python:3.13-slim

ARG APP_VERSION
ENV APP_VERSION=$APP_VERSION

ARG APP_ENV
ENV APP_ENV=$APP_ENV

# Don't build bytecode
ENV PYTHONDONTWRITEBYTECODE=1
# Flush logs immediately
ENV PYTHONUNBUFFERED=1
# Entry point for Flask app
ENV FLASK_APP=app.py


# Set working directory
WORKDIR /app

# Copy just the requirements.txt file first (to leverage caching)
COPY requirements.txt .

# Install dependencies
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Now, copy the rest of your application code
COPY . .

# Expose port for Flask (default 5000)
EXPOSE 80

# Run the app
CMD ["gunicorn", "-b", "0.0.0.0:80", "app:app"]
