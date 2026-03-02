"""
Indian mobile number validation utilities.
Validates 10-digit numbers starting with 6, 7, 8, or 9.
Rejects invalid patterns like 00000, 11111, 1234567890.
"""
import re


def normalize_indian_mobile(value: str) -> str:
    """Normalize phone: remove spaces, dashes, optional +91 prefix. Returns digits only."""
    v = re.sub(r'[\s\-]', '', str(value).strip())
    # Strip optional +91 or 91 prefix for Indian numbers
    if v.startswith('+91'):
        v = v[3:].lstrip()
    elif v.startswith('91') and len(v) > 10:
        v = v[2:].lstrip()
    return v


def validate_indian_mobile(value: str) -> str:
    """
    Validate Indian mobile number.
    - Must be 10 digits
    - Must start with 6, 7, 8, or 9
    - Rejects: all same digit (00000, 11111), sequential (1234567890), etc.

    Returns normalized 10-digit string.
    Raises ValueError with descriptive message if invalid.
    """
    v = normalize_indian_mobile(value)
    if not v.isdigit():
        raise ValueError('Please enter a valid number')
    if len(v) != 10:
        raise ValueError('Please enter a valid number')
    first = v[0]
    if first not in ('6', '7', '8', '9'):
        raise ValueError('Please enter a valid number')
    # Reject all same digit (0000000000, 1111111111, etc.)
    if len(set(v)) == 1:
        raise ValueError('Please enter a valid number')
    # Reject sequential ascending (1234567890, 0123456789)
    if v in ('1234567890', '0123456789'):
        raise ValueError('Please enter a valid number')
    return v
