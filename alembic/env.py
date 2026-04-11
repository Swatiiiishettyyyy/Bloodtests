from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Import database configuration
import sys
import os
from pathlib import Path

# Add project root to path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# Import database and Base
from database import Base, engine, DATABASE_URL

# Import all models so Alembic can detect them
# This ensures all tables are included in autogenerate
from Login_module.User.user_model import User
from Product_module.Product_model import Category, Product
from Member_module.Member_model import Member
from Address_module.Address_model import Address, ServiceableLocation
from Cart_module.Cart_model import CartItem, Cart
from Cart_module.Coupon_model import Coupon, CartCoupon
from Orders_module.Order_model import Order, OrderItem, OrderSnapshot, OrderStatusHistory
from Cart_module.Coupon_model import Coupon, CartCoupon
from Login_module.Device.Device_session_model import DeviceSession
from Member_module.Member_audit_model import MemberAuditLog
from Address_module.Address_audit_model import AddressAudit
from Cart_module.Cart_audit_model import AuditLog
from Audit_module.Profile_audit_crud import ProfileAuditLog
from Login_module.Device.Device_session_audit_model import SessionAuditLog
from Thyrocare_module.Thyrocare_model import ThyrocareProduct, ThyrocarePincode
from Thyrocare_module.thyrocare_webhook_model import ThyrocareOrderTracking, ThyrocarePatientTracking, ThyrocareOrderStatusHistory
from Thyrocare_module.thyrocare_lab_result_model import ThyrocareLabResult
from Login_module.OTP.OTP_Log_Model import OTPAuditLog
from Login_module.Token.Refresh_token_model import RefreshToken  # Dual-token strategy
from Consent_module.Consent_model import UserConsent, ConsentProduct, PartnerConsent
from GeneticTest_module.GeneticTest_model import GeneticTestParticipant
from Tracking_module.Tracking_model import TrackingRecord  # Location & Analytics Tracking
from Enquiry_module.Enquiry_model import EnquiryRequest

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set the sqlalchemy.url from our database configuration
config.set_main_option("sqlalchemy.url", DATABASE_URL)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

