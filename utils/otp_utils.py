import random


OTP_STORE = {}




def generate_otp(email: str) -> int:
otp = random.randint(100000, 999999)
OTP_STORE[email] = otp
# integrate email/SMS provider here
return otp




def verify_otp(email: str, otp: int) -> bool:
return OTP_STORE.get(email) == otp
