"""
Singleton counter for internal Order.order_number (sequential).
"""

from sqlalchemy import BigInteger, Column, Integer

from database import Base

# First issued order sequence will be ORDER_NUMBER_SEED_LAST + 1.
ORDER_NUMBER_SEED_LAST = 0


class OrderNumberCounter(Base):
    __tablename__ = "order_number_counter"

    id = Column(Integer, primary_key=True)
    last_value = Column(BigInteger, nullable=False)

