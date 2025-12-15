from sqlalchemy import Column, Integer, String, Boolean, DateTime
from datetime import datetime
from database import Base


class User(Base):
__tablename__ = "users"


id = Column(Integer, primary_key=True, index=True)
email = Column(String, unique=True, index=True)
password = Column(String)
gender = Column(String)
country = Column(String)
is_subscribed = Column(Boolean, default=False)
created_at = Column(DateTime, default=datetime.utcnow)




class ChatHistory(Base):
__tablename__ = "chat_history"


id = Column(Integer, primary_key=True)
sender_id = Column(Integer)
receiver_id = Column(Integer)
message = Column(String)
created_at = Column(DateTime, default=datetime.utcnow)
