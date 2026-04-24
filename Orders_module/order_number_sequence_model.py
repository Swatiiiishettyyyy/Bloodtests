"""
Order number sequence table.

We rely on an autoincrement primary key insert for concurrency-safe, sequential IDs.
"""

from sqlalchemy import Column, Integer

from database import Base


class OrderNumberSequence(Base):
    __tablename__ = "order_number_sequence"

    id = Column(Integer, primary_key=True, autoincrement=True)

