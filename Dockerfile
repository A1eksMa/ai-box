FROM node:22-slim

RUN apt-get update && apt-get install -y \
    tmux python3 python3-pip python3-venv \
    bash curl git vim \
 && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

WORKDIR /app

COPY requirements.txt .
RUN python3 -m venv /app/venv \
 && /app/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

COPY app.py .
COPY templates/ templates/
COPY static/ static/

ENV HOME=/root/ai-box

EXPOSE 8080

ENTRYPOINT ["/app/entrypoint.sh"]
