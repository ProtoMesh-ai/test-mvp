from sqlalchemy import Column, String, Integer, DateTime, Float
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()

class Agent(Base):
    __tablename__ = "agents"
    
    id = Column(String, primary_key=True)
    framework = Column(String, nullable=False)
    priority = Column(Integer, default=5)
    role = Column(String, default="user")
    team = Column(String, default="default")
    created_at = Column(DateTime, default=datetime.utcnow)

class LockEvent(Base):
    __tablename__ = "lock_events"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    lock_id = Column(String, nullable=False, index=True)
    agent_id = Column(String, nullable=False, index=True)
    resource_type = Column(String, nullable=False)
    resource_id = Column(String, nullable=False)
    action = Column(String)
    acquired_at = Column(DateTime, default=datetime.utcnow)
    released_at = Column(DateTime, nullable=True)
    duration_ms = Column(Float, nullable=True)