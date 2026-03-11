FROM python:3.12-slim

# Sistema base
RUN apt-get update && apt-get install -y \
    curl git wget gnupg2 ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código del agente
COPY . .

# Directorio de trabajo para el agente
RUN mkdir -p /workspaces /app/skills

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
