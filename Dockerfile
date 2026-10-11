# Shared image for every Python service in this project (serving API,
# automation loop). All of them need the same code and dependencies —
# only the command differs per service, set in docker-compose.yml.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# No CMD here on purpose — docker-compose.yml sets the command per service
# (uvicorn for serving, python -m automation.loop for automation).
