"""
Seed script to populate thyrocare_pincodes table with test pincodes.
Run: python seed_thyrocare_pincodes.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from database import SessionLocal
from Thyrocare_module.Thyrocare_model import ThyrocarePincode
from Login_module.Utils.datetime_utils import now_ist

PINCODES = [
    "100065", "106108", "110001", "110003", "110006",
]

def seed():
    db = SessionLocal()
    try:
        added = 0
        for pc in PINCODES:
            existing = db.query(ThyrocarePincode).filter(ThyrocarePincode.pincode == pc).first()
            if not existing:
                db.add(ThyrocarePincode(pincode=pc, is_active=True, synced_at=now_ist()))
                added += 1
        db.commit()
        print(f"Seeded {added} pincodes. Skipped {len(PINCODES) - added} already present.")
    finally:
        db.close()

if __name__ == "__main__":
    seed()
