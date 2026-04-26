"""
Mint a local development JWT for testing API endpoints without Supabase.

Usage:
    python -m scripts.mint_dev_token
    python -m scripts.mint_dev_token --email dev@local --hours 8

The token uses the same secret as SUPABASE_JWT_SECRET in .env, so the
app's JWT validator accepts it exactly like a real Supabase token.

NEVER use this in production. For local testing only.
"""
from __future__ import annotations

import argparse
import uuid
from datetime import datetime, timedelta, timezone


def mint(
    email: str = "dev@local.test",
    hours: int = 24,
    secret: str | None = None,
) -> str:
    from jose import jwt as jose_jwt
    from app.core.config import get_settings

    settings = get_settings()
    key = secret or settings.SUPABASE_JWT_SECRET
    now = datetime.now(timezone.utc)

    payload = {
        "sub": str(uuid.uuid4()),
        "email": email,
        "aud": settings.SUPABASE_JWT_AUDIENCE,
        "role": "authenticated",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=hours)).timestamp()),
    }

    return jose_jwt.encode(
        payload,
        key,
        algorithm=settings.SUPABASE_JWT_ALGORITHM,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Mint a local dev JWT")
    parser.add_argument("--email", default="dev@local.test")
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args()

    token = mint(email=args.email, hours=args.hours)
    print(token)
    print()
    print(f"# Expires in {args.hours}h  ·  Email: {args.email}")
    print()
    print("# Test with curl:")
    print(f'curl -H "Authorization: Bearer {token[:40]}..." http://localhost:8000/api/v1/materials')
    print()
    print("# Or set as shell variable:")
    print(f"TOKEN={token}")


if __name__ == "__main__":
    main()
