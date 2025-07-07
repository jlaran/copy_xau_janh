# models.py
from sqlalchemy import Column, String
from db import Base

class License(Base):
    __tablename__ = "licenses"

    account_number = Column(String, primary_key=True, index=True)
    license_key = Column(String)
    enabled = Column(String, default="")


class AccountStatus(Base):
    __tablename__ = "account_status"

    account_number = Column(String, primary_key=True, index=True)
    account_balance = Column(String, default="")
    last_trade = Column(String, default="")
    account_mode = Column(String, default="")
    broker_server = Column(String, default="")
    broker_company = Column(String, default="")
    risk_per_group = Column(String, default="")
    ea_status = Column(String, default="")
    last_sync = Column(String, default="")