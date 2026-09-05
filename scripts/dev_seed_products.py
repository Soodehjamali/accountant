"""DEVELOPMENT-ONLY: Create sample products for local testing.

Usage (from the project root, with DATABASE_URL configured and migrations
already applied via ``alembic upgrade head``)::

    ERP_ALLOW_DEV_SEED=1 python -m scripts.dev_seed_products

IMPORTANT: This script is for DEVELOPMENT/TESTING only. It should NOT be
run in production or as part of a fresh installation. A fresh installation
starts with an empty business database.

Guard: the script refuses to run unless ``ERP_ALLOW_DEV_SEED=1`` is set in
the environment, so it can never be invoked by accident against an
unexpected environment. Any rows it creates are TEST/DEMO data that must be
cleaned up afterwards (``python -m scripts.cleanup_test_data``) per the
project's Testing/Development Data Hygiene rule (see CLAUDE.md).

Safe to run more than once: every step here is get-or-create / checks for
an existing SKU first, so re-running just reprints the same products
instead of erroring or duplicating rows.
"""

from __future__ import annotations

from dotenv import load_dotenv

# Load .env BEFORE importing database.session, so DATABASE_URL (if only
# defined in a local .env file) is in os.environ by the time
# database.session.get_engine() first reads it. This script is a standalone
# entry point -- it does not go through backend/app/core/config.py (which
# does its own load_dotenv() for the FastAPI app), so it must load .env
# itself.
#
# encoding="utf-8-sig": tolerates a UTF-8 byte-order-mark (BOM) at the start
# of the file. PowerShell's `Out-File -Encoding utf8` (and Notepad's default
# "UTF-8" save option on Windows) write a BOM that is invisible in most
# viewers/`Get-Content`, but a plain "utf-8" read leaves it attached to the
# first key, silently turning `DATABASE_URL=...` into an unrecognized
# `\\ufeffDATABASE_URL=...` line that python-dotenv skips. "utf-8-sig" strips
# a leading BOM if present and behaves identically to "utf-8" if absent, so
# this is safe regardless of how the .env file was created.
load_dotenv(encoding="utf-8-sig")

from database.session import get_session  # noqa: E402
from services import bootstrap_service, product_service  # noqa: E402

#: A handful of sample products for development/testing.
SAMPLE_PRODUCTS = [
    {"sku": "SKU-0001", "name": "Steel Bolt 10mm", "description": "Standard 10mm steel bolt."},
    {"sku": "SKU-0002", "name": "Steel Nut 10mm", "description": "Matching 10mm steel nut."},
    {"sku": "SKU-0003", "name": "Packing Box (Small)", "description": "Corrugated box, small size."},
]


def _guard() -> None:
    """Refuse to run outside an explicitly-allowed dev/test seed session."""
    import os

    if os.getenv("ERP_ALLOW_DEV_SEED") != "1":
        print(
            "Refusing to run: this script creates TEST/DEMO data. "
            "Re-run with ERP_ALLOW_DEV_SEED=1 (development/test only) and "
            "clean up afterwards via `python -m scripts.cleanup_test_data`."
        )
        raise SystemExit(1)


def main() -> None:
    _guard()
    with get_session() as session:
        system_user = bootstrap_service.ensure_system_user(session)
        default_uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)

        for item in SAMPLE_PRODUCTS:
            try:
                product_service.create_product(
                    session,
                    sku=item["sku"],
                    name=item["name"],
                    description=item["description"],
                    base_uom_id=default_uom.id,
                    created_by=system_user.id,
                )
                print(f"created:  {item['sku']}")
            except product_service.DuplicateSkuError:
                print(f"skipped:  {item['sku']} (already exists)")

        print()
        print("Products currently in the database:")
        print("-" * 60)
        for product in product_service.list_products(session):
            print(f"{product.sku:<12} {product.name:<28} {product.status}")
        print("-" * 60)


if __name__ == "__main__":
    main()
