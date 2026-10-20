# Versão web pronta para publicar: docker build -t cipher-web . && docker run -p 8000:8000 cipher-web
FROM node:22-slim AS frontend
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
# Deno: o yt-dlp o usa para resolver o desafio JavaScript do YouTube (sem ele os fluxos são recusados).
COPY --from=denoland/deno:bin-2.5.6 /deno /usr/local/bin/deno
WORKDIR /app
COPY requirements-web.txt ./
RUN pip install --no-cache-dir -r requirements-web.txt
COPY cipher_studio/ cipher_studio/
COPY --from=frontend /web/dist web/dist
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
# Defina CIPHER_WEB_PASSWORD para exigir login e publique atrás de HTTPS (a senha do vídeo vai ao servidor).
CMD ["sh", "-c", "python -m cipher_studio web --host 0.0.0.0 --port ${PORT:-8000}"]
