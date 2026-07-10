"""Relational schema.

Design notes for scale (documented, since we run on SQLite here):
- price_observations is the high-volume table. In Postgres it should be
  declaratively partitioned BY RANGE (observed_at) monthly, with a BRIN index
  on observed_at and a btree on (product_id, observed_at). Old partitions can
  be rolled to cheaper storage or dropped per retention policy.
- products carries a natural key (retailer, external_id) with a unique index so
  connectors can upsert idempotently.
"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, ForeignKey, Boolean, Text,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship
from .db import Base


class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    retailer = Column(String(64), nullable=False, index=True)
    external_id = Column(String(128), nullable=False)  # retailer's SKU/ASIN/etc
    title = Column(String(512), nullable=False)
    brand = Column(String(128), index=True)
    category = Column(String(128), index=True)
    url = Column(String(1024))
    image_url = Column(String(1024))
    msrp = Column(Float)
    rating = Column(Float)            # 0-5
    review_count = Column(Integer, default=0)
    seller_reputation = Column(Float)  # 0-1, connector-supplied
    created_at = Column(DateTime, default=datetime.utcnow)

    observations = relationship("PriceObservation", back_populates="product",
                                cascade="all, delete-orphan")
    __table_args__ = (
        UniqueConstraint("retailer", "external_id", name="uq_product_natural_key"),
    )


class PriceObservation(Base):
    __tablename__ = "price_observations"
    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"),
                        nullable=False)
    price = Column(Float, nullable=False)
    in_stock = Column(Boolean, default=True)
    inventory_level = Column(Integer)   # nullable; some retailers expose it
    coupon = Column(Float, default=0.0)  # additional discount amount if any
    observed_at = Column(DateTime, default=datetime.utcnow, index=True)

    product = relationship("Product", back_populates="observations")
    __table_args__ = (
        Index("ix_price_obs_product_time", "product_id", "observed_at"),
    )


class Alert(Base):
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True)
    user_email = Column(String(256), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"),
                        nullable=False)
    # rule types: price_below, percent_off, lowest_ever, back_in_stock,
    #             coupon_appears, low_inventory
    rule_type = Column(String(32), nullable=False)
    threshold = Column(Float)  # meaning depends on rule_type
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_triggered_at = Column(DateTime)

    product = relationship("Product")


class AlertEvent(Base):
    __tablename__ = "alert_events"
    id = Column(Integer, primary_key=True)
    alert_id = Column(Integer, ForeignKey("alerts.id", ondelete="CASCADE"),
                      nullable=False)
    message = Column(Text, nullable=False)
    triggered_at = Column(DateTime, default=datetime.utcnow, index=True)
