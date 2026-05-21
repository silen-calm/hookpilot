# Python 3.14 slim (백엔드 + yt-dlp + crawler)
FROM python:3.14-slim

# 시스템 의존성 — yt-dlp 가 ffmpeg 필요. instaloader 별도 X.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# yt-dlp 시스템 설치 (Python 모듈 X, CLI)
RUN curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp \
    && chmod +x /usr/local/bin/yt-dlp

WORKDIR /app

# Python 패키지 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 코드 + 풀 데이터
COPY . .

# Render 가 $PORT 환경변수 박음
ENV PORT=10000
EXPOSE 10000

# 백엔드 시작 — listen 0.0.0.0 (cloud 외부 접근 가능)
CMD ["python3", "-u", "download_server.py"]
