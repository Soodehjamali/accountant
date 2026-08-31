"""Seed the first admin user with known credentials.

Idempotent -- safe to run more than once.  On first run, creates the admin
user and assigns the ADMIN role.  On subsequent runs, detects the existing
user and skips creation.

Usage (from the project root, with DATABASE_URL configured and migrations
already applied via ``alembic upgrade head``)::

    python -m scripts.create_admin_user

Credentials (dev-only -- change before production):

    email:    admin@local.invalid
    username: admin
    password: Admin123!

These are deliberately obviously-fake dev credentials.  The bootstrap
service's system user (username ``system``, email ``system@local.invalid``)
is NOT a login account -- its password_hash is a placeholder string that
always fails verification.  This script creates a real, login-able admin
account using the same ``auth_service.create_user()`` function the API
uses, with proper PBKDF2-HMAC-SHA256 hashing via ``security.hash_password()``.
"""

from __future__ import annotations

from dotenv import load_dotenv

# Load .env BEFORE importing database.session, so DATABASE_URL (if only
# defined in a local .env file) is in os.environ by the time
# database.session.get_engine() first reads it.  Same BOM-tolerant
# encoding as seed_and_list_products.py.
load_dotenv(encoding="utf-8-sig")

from sqlalchemy import select  # noqa: E402

from database.models.app_user import AppUser  # noqa: E402
from database.session import get_session  # noqa: E402
from services import auth_service, bootstrap_service, rbac_service  # noqa: E402

#: Credentials for the first admin user.
ADMIN_USERNAME = "admin"
ADMIN_EMAIL = "admin@local.invalid"
ADMIN_PASSWORD = "Admin123!"


def main() -> None:
    with get_session() as session:
        # 1. Ensure the system user and RBAC bootstrap exist (idempotent).
        system_user = bootstrap_service.ensure_system_user(session)
        bootstrap_service.ensure_rbac_bootstrap(session)

        # 2. Check if the admin user already exists.
        existing = session.execute(
            select(AppUser).where(
                (AppUser.username == ADMIN_USERNAME) | (AppUser.email == ADMIN_EMAIL)
            )
        ).scalar_one_or_none()

        if existing is not None:
            print(f"Admin user already exists: {existing.username} <{existing.email}>")
            print(f"  id:     {existing.id}")
            print(f"  status: {existing.status}")
            print()
            print("To reset the password, update the password_hash column directly")
            print("or delete this user and re-run this script.")
            return

        # 3. Create the admin user.
        user = auth_service.create_user(
            session,
            username=ADMIN_USERNAME,
            email=ADMIN_EMAIL,
            password=ADMIN_PASSWORD,
            created_by=system_user.id,
        )
        print(f"Created admin user: {user.username} <{user.email}>")
        print(f"  id:     {user.id}")

        # 4. Assign the ADMIN role (grants all default permissions).
        rbac_service.assign_role(
            session,
            user_id=user.id,
            role_code=bootstrap_service.ADMIN_ROLE_CODE,
            assigned_by=system_user.id,
        )
        print(f"  role:   {bootstrap_service.ADMIN_ROLE_CODE}")

        # 5. Commit everything atomically.
        session.commit()

        print()
        print("=== Login Credentials ===")
        print(f"  Email:    {ADMIN_EMAIL}")
        print(f"  Username: {ADMIN_USERNAME}")
        print(f"  Password: {ADMIN_PASSWORD}")
        print()
        print("POST /api/v1/auth/login with:")
        print(f'  {{"username_or_email": "{ADMIN_EMAIL}", "password": "{ADMIN_PASSWORD}"}}')


if __name__ == "__main__":
    main()
