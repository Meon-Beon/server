from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import auth, dna, health, report

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(dna.router)
app.include_router(health.router)
app.include_router(report.router)


@app.get("/api/hello")
def hello():
    return {"message": "연결오케"}
