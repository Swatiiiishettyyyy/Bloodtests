"""
Backwards-compatible alias for legacy imports.

Some parts of the app import `OrderNumberCounter`, but the current implementation
uses `OrderNumberSequence` as the concurrency-safe counter table.
"""

from .order_number_sequence_model import OrderNumberSequence as OrderNumberCounter

