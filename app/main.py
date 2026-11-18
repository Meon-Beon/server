from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import CORS_ORIGINS

from app.routers import auth, dna, health, insurance, kakao, me, report

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,  # .env 의 CORS_ORIGINS (기본: 로컬 프론트)
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(dna.router)
app.include_router(health.router)
app.include_router(insurance.router)
app.include_router(kakao.router)
app.include_router(me.router)
app.include_router(report.router)


@app.get("/api/hello")
def hello():
    return {"message": "연결오케"}
