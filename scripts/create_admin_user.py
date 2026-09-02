"""Create the first real admin user interactively.

This is a one-time setup script for a fresh (or rebuilt) database.  The
``system`` user seeded by ``bootstrap_service`` is NOT a login account
(its ``password_hash`` is a placeholder that always fails verification).
This script creates a real, login-able admin account using the same
``auth_service.create_user()`` the API uses, with proper
PBKDF2-HMAC-SHA256 hashing via ``security.hash_password()``.

Credentials are **never** hard-coded.  Username and email are read from
CLI arguments or an interactive prompt; the password is read via
``getpass`` (no terminal echo) and is never stored in source code,
logs, or any file.

Idempotent -- safe to run more than once.  If the username already
exists the script prints a clear message and exits without creating a
duplicate.

Usage (from the project root, with ``DATABASE_URL`` configured and
migrations already applied via ``alembic upgrade head``)::

    # Interactive (prompts for everything):
    python -m scripts.create_admin_user

    # Supply username / email as arguments (password is always prompted):
    python -m scripts.create_admin_user --username admin --email admin@example.com
"""

from __future__ import annotations

import argparse
import getpass
import sys

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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create the first real admin user for the ERP system.",
    )
    parser.add_argument(
        "--username",
        type=str,
        default=None,
        help="Admin username (interactive prompt if omitted).",
    )
    parser.add_argument(
        "--email",
        type=str,
        default=None,
        help="Admin email address (interactive prompt if omitted).",
    )
    return parser.parse_args()


def _prompt_username() -> str:
    while True:
        username = input("Username: ").strip()
        if username:
            return username
        print("  Username cannot be empty.")


def _prompt_email() -> str:
    while True:
        email = input("Email: ").strip()
        if email and "@" in email:
            return email
        print("  Please enter a valid email address.")


def _prompt_password() -> str:
    while True:
        password = getpass.getpass("Password: ")
        if not password:
            print("  Password cannot be empty.")
            continue
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("  Passwords do not match. Try again.")
            continue
        return password


def main() -> None:
    args = _parse_args()

    username = args.username or _prompt_username()
    email = args.email or _prompt_email()
    password = _prompt_password()

    with get_session() as session:
        # 1. Ensure the system user and RBAC bootstrap exist (idempotent).
        system_user = bootstrap_service.ensure_system_user(session)
        bootstrap_service.ensure_rbac_bootstrap(session)

        # 2. Check if the admin user already exists.
        existing = session.execute(
            select(AppUser).where(
                (AppUser.username == username) | (AppUser.email == email)
            )
        ).scalar_one_or_none()

        if existing is not None:
            print(f"User already exists: {existing.username} <{existing.email}>")
            print(f"  id:     {existing.id}")
            print(f"  status: {existing.status}")
            print()
            print("To reset the password, use the API or update the database directly.")
            sys.exit(1)

        # 3. Create the admin user.
        user = auth_service.create_user(
            session,
            username=username,
            email=email,
            password=password,
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
        print("Admin user created successfully.")
        print("You can now log in through the application.")


if __name__ == "__main__":
    main()
