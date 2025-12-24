import os
import sys
from datetime import datetime

import yaml
from sqlalchemy.orm import Session

# Add repository root to path so we can import models/db
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database import SessionLocal, engine, Base  # noqa: E402
from models import User  # noqa: E402


def set_dummy_users_online(*, also_clear_on_call: bool = True) -> int:
    """
    Mark dummy users as "online" by refreshing last_active_at to now.

    Backend rules:
    - online if last_active_at within 2 minutes
    - matchable if is_on_call == False
    """
    Base.metadata.create_all(bind=engine)
    db: Session = SessionLocal()

    yml_path = os.path.join(os.path.dirname(__file__), "dummy_users.yml")
    with open(yml_path, "r") as f:
        data = yaml.safe_load(f) or {}

    usernames = []
    for u in (data.get("users", []) or []):
        username = (u or {}).get("username")
        if username:
            usernames.append(str(username))

    if not usernames:
        print("No dummy usernames found in dummy_users.yml")
        db.close()
        return 0

    now = datetime.utcnow()
    updated = 0
    for username in usernames:
        user = db.query(User).filter(User.username == username).first()
        if not user:
            continue
        user.last_active_at = now
        if also_clear_on_call:
            user.is_on_call = False
        db.add(user)
        updated += 1

    db.commit()
    db.close()
    print(f"Marked {updated} dummy users online at {now.isoformat()}Z")
    return updated


if __name__ == "__main__":
    set_dummy_users_online()

