import os
import sys
import yaml
import bcrypt
from sqlalchemy.orm import Session

# Add repository root to path so we can import models/db
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database import SessionLocal, engine, Base
from models import User

def load_data():
    Base.metadata.create_all(bind=engine)
    db: Session = SessionLocal()
    
    with open(os.path.join(os.path.dirname(__file__), "dummy_users.yml"), "r") as f:
        data = yaml.safe_load(f)
        
    for u_data in data.get("users", []):
        username = u_data.get("username")
        existing = db.query(User).filter(User.username == username).first()
        if existing:
            print(f"User {username} already exists. Skipping.")
            continue
            
        password = u_data.pop("password")
        # Multi-byte safe password truncation for bcrypt (max 72 bytes)
        safe_password = password.strip().encode("utf-8")[:72].decode("utf-8", errors="ignore")
        pwd_bytes = safe_password.encode('utf-8')
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(pwd_bytes, salt)
        password_hash = hashed.decode('utf-8')
        
        user = User(
            **u_data,
            password_hash=password_hash
        )
        db.add(user)
        print(f"Adding user {username}...")
        
    db.commit()
    db.close()
    print("Dummy data loaded.")

if __name__ == "__main__":
    load_data()
