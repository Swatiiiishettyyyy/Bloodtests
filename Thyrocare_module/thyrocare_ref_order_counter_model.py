"""
Singleton counter for Thyrocare partner refOrderNo (numeric sequence).
"""
from sqlalchemy import Column, Integer, BigInteger

from database import Base

# First value issued is THYROCARE_REF_ORDER_SEED_LAST + 1 (e.g. 2627001001).
THYROCARE_REF_ORDER_SEED_LAST = 2627001000


class ThyrocareRefOrderCounter(Base):
    __tablename__ = "thyrocare_ref_order_counter"

    id = Column(Integer, primary_key=True)
    last_value = Column(BigInteger, nullable=False)
