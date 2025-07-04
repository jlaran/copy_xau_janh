# models.py
from sqlalchemy import Column, String, Boolean, Float
from db import Base

class License(Base):
    __tablename__ = "licenses"

    account_number = Column(String, primary_key=True, index=True)
    license_key = Column(String)
    enabled = Column(Boolean, default=True)


class AccountStatus(Base):
    __tablename__ = "account_status"

    account_number = Column(String, primary_key=True, index=True)
    account_balance = Column(Float, default=0.0)
    last_trade = Column(String, default="")
    account_mode = Column(String, default="")
    broker_server = Column(String, default="")
    broker_company = Column(String, default="")
    risk_per_group = Column(String, default="")
    ea_status = Column(String, default="")