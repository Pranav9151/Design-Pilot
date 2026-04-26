-- Runs on first Postgres container boot. Creates the test database and
-- installs the pgvector + pgcrypto extensions in both DBs so Alembic
-- migrations do not need superuser privileges.

CREATE DATABASE designpilot_test;

\c designpilot_dev
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

\c designpilot_test
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
