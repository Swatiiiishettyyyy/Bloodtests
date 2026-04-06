"""
Seed thyrocare_products from the live Thyrocare sandbox/production catalogue API.
This ensures thyrocare_id matches what the API actually expects.

Usage:
    python seed_thyrocare_from_api.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from database import SessionLocal
from Login_module.User.user_model import User  # noqa: F401
from Address_module.Address_model import Address  # noqa: F401
from Member_module.Member_model import Member  # noqa: F401
from Product_module.Product_model import Product  # noqa: F401
from Thyrocare_module.Thyrocare_model import ThyrocareProduct, ThyrocareTestParameter
from Thyrocare_module.thyrocare_service import ThyrocareService


def seed():
    service = ThyrocareService()
    db = SessionLocal()
    created = 0
    updated = 0
    page = 1
    page_size = 400

    try:
        while True:
            print(f"Fetching page {page}...")
            result = service.get_catalogue(min_price=0, max_price=100000, page=page, page_size=page_size)
            sku_list = result.get("skuList", [])

            if not sku_list:
                break

            for sku in sku_list:
                thyrocare_id = sku.get("id", "")
                if not thyrocare_id:
                    continue

                rate = sku.get("rate", {})
                beneficiaries = sku.get("beneficiaries", {})
                flags = sku.get("flags", {})

                existing = db.query(ThyrocareProduct).filter(
                    ThyrocareProduct.thyrocare_id == thyrocare_id
                ).first()

                if existing:
                    existing.name = sku.get("name", existing.name)
                    existing.type = sku.get("type", existing.type)
                    existing.no_of_tests_included = int(sku.get("noOfTestsIncluded", 0) or 0)
                    existing.listing_price = float(rate.get("listingPrice", 0) or 0)
                    existing.selling_price = float(rate.get("sellingPrice", 0) or 0)
                    existing.discount_percentage = float(rate.get("discountPercentage", 0) or 0)
                    existing.notational_incentive = float(rate.get("notationalIncentive", 0) or 0)
                    existing.beneficiaries_min = int(beneficiaries.get("min", 1) or 1)
                    existing.beneficiaries_max = int(beneficiaries.get("max", 1) or 1)
                    existing.beneficiaries_multiple = int(beneficiaries.get("multiple", 1) or 1)
                    existing.is_fasting_required = flags.get("isFastingRequired")
                    existing.is_home_collectible = flags.get("isHomeCollectible")
                    product = existing
                    updated += 1
                else:
                    product = ThyrocareProduct(
                        thyrocare_id=thyrocare_id,
                        name=sku.get("name", ""),
                        type=sku.get("type", "SSKU"),
                        no_of_tests_included=int(sku.get("noOfTestsIncluded", 0) or 0),
                        listing_price=float(rate.get("listingPrice", 0) or 0),
                        selling_price=float(rate.get("sellingPrice", 0) or 0),
                        discount_percentage=float(rate.get("discountPercentage", 0) or 0),
                        notational_incentive=float(rate.get("notationalIncentive", 0) or 0),
                        beneficiaries_min=int(beneficiaries.get("min", 1) or 1),
                        beneficiaries_max=int(beneficiaries.get("max", 1) or 1),
                        beneficiaries_multiple=int(beneficiaries.get("multiple", 1) or 1),
                        is_fasting_required=flags.get("isFastingRequired"),
                        is_home_collectible=flags.get("isHomeCollectible"),
                    )
                    db.add(product)
                    db.flush()
                    created += 1

                # Sync test parameters
                db.query(ThyrocareTestParameter).filter(
                    ThyrocareTestParameter.thyrocare_product_id == product.id
                ).delete()

                for test in sku.get("testsIncluded", []):
                    param = ThyrocareTestParameter(
                        thyrocare_product_id=product.id,
                        name=test.get("name", ""),
                        group_name=test.get("groupName"),
                    )
                    db.add(param)

            db.commit()
            print(f"Page {page} done. Created: {created}, Updated: {updated}")

            if result.get("isLastPage", True):
                break
            page += 1

        print(f"\nDone. Total Created: {created}, Total Updated: {updated}")
    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
