app = FastAPI(title="Chat App Backend")

app.include_router(match_router, prefix="/api")

@app.get("/")
def root():
    return {"status": "Backend running"}

