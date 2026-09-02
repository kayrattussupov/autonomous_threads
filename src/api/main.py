from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.api.routers import posts
from src.db.repo import InvalidStateTransition, RetirementBlocked

app = FastAPI()
app.include_router(posts.router)


@app.exception_handler(InvalidStateTransition)
def handle_invalid_state_transition(request: Request, exc: InvalidStateTransition):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(RetirementBlocked)
def handle_retirement_blocked(request: Request, exc: RetirementBlocked):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/health")
def health():
    return {"status": "ok"}
