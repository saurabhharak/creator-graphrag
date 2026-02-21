"""SQLAlchemy declarative base shared by all Creator GraphRAG ORM models."""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all Creator GraphRAG ORM models."""
    pass
