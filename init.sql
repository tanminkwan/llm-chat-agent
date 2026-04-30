-- Phase 4 RAG Console Database Initialization Script (DB & User Setup Only)
-- 이 스크립트는 PostgreSQL 데이터베이스와 사용자 계정을 생성할 때 사용합니다.
-- 테이블은 서비스 기동 시 애플리케이션(SQLAlchemy)에서 자동으로 생성됩니다.

-- 1. 데이터베이스 생성
-- CREATE DATABASE llm_agent;

-- 2. 사용자 생성 및 권한 부여
-- 'password' 부분은 .env 파일의 DB_PASSWORD와 일치하도록 수정하여 실행하십시오.
-- CREATE USER admin WITH PASSWORD 'password';
-- GRANT ALL PRIVILEGES ON DATABASE llm_agent TO admin;

-- 3. 스키마 권한 (PostgreSQL 15 이상 버전 대응)
-- GRANT ALL ON SCHEMA public TO admin;
