#!/usr/bin/env bash
# exit on error
set -o errexit

# Install dependencies
pip install -r requirements.txt

# Convert static assets (CSS/JS) for WhiteNoise
python manage.py collectstatic --no-input

# Apply database migrations
python manage.py migrate