from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)

    email = Column(String, unique=True, index=True, nullable=True)
    username = Column(String, unique=True, index=True, nullable=True)

    # Store password hash (bcrypt). Never store plaintext.
    password_hash = Column(String, nullable=False)

    name = Column(String, nullable=False, default="User")
    gender = Column(String, nullable=False)  # "male" | "female" | "cross"
    country = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    image_url = Column(String, nullable=True)

    # Entitlement flag (for opposite/cross chat). Real Google Play IAP should update this.
    is_subscribed = Column(Boolean, default=False, nullable=False)

    # Presence tracking: updated on authenticated requests.
    last_active_at = Column(DateTime, nullable=True)

    # Video matching counters for "free" bias rules.
    free_video_total_count = Column(Integer, default=0, nullable=False)
    free_video_opposite_count = Column(Integer, default=0, nullable=False)

    # Busy status
    is_on_call = Column(Boolean, default=False, nullable=False)

    # Video matchmaking state:
    # - idle: not searching, not on a call
    # - searching: user is actively requesting /video/match (polling)
    # - in_call: matched and should be in a video session
    #
    # This prevents matching random "online" users who didn't request a call.
    video_state = Column(String, default="idle", nullable=False)
    video_state_updated_at = Column(DateTime, nullable=True)
    video_session_id = Column(Integer, nullable=True)
    video_partner_id = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True)
    mode = Column(String, nullable=False)  # "text" | "video"

    user_a_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    user_b_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Video sessions can be ended explicitly.
    ended_at = Column(DateTime, nullable=True)
    ended_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    user_a = relationship("User", foreign_keys=[user_a_id])
    user_b = relationship("User", foreign_keys=[user_b_id])
    ended_by = relationship("User", foreign_keys=[ended_by_id])


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), nullable=False, index=True)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    session = relationship("ChatSession")
    sender = relationship("User")


class PublicMessage(Base):
    __tablename__ = "public_messages"

    id = Column(Integer, primary_key=True)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    message = Column(Text, nullable=False)
    # Optional: store image URL if we support images in public chat
    image_url = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    sender = relationship("User")


class Swipe(Base):
    __tablename__ = "swipes"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    target_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    direction = Column(String, nullable=False)  # "left" | "right"
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class Report(Base):
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True)
    reporter_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    reported_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True) # Optional if reporting generic content or system
    reason = Column(String, nullable=False)
    details = Column(Text, nullable=True)
    context = Column(String, nullable=True) # "video", "chat", "public_chat"
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    reporter = relationship("User", foreign_keys=[reporter_id])
    reported_user = relationship("User", foreign_keys=[reported_user_id])


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    product_id = Column(String, nullable=False)
    purchase_token = Column(String, nullable=False, unique=True)
    expiry_time_millis = Column(String, nullable=True) # Storing as string to handle large longs safely or BigInteger
    status = Column(String, default="active", nullable=False) # active, expired, canceled
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", backref="subscriptions")
