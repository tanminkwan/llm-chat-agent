# 1단계: 빌드 환경
FROM python:3.12-slim AS builder

# uv 설치
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# 의존성 파일 복사 및 설치
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-cache

# 2단계: 실행 환경
FROM python:3.12-slim

WORKDIR /app

# 빌드된 가상환경 복사
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=/app

# 소스 코드 복사
COPY . .

# 권한 설정 (Non-root 권한 권장하나 우선 실행을 위해 기본 설정)
# RUN chmod +x apps/api/main.py

# 포트 개방 (HTTP)
EXPOSE 8000

# 서버 실행 (모듈 방식 실행으로 경로 문제 해결)
CMD ["python", "-m", "apps.api.main"]
