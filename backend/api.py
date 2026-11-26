
from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from brain import ask_ai, get_last_error
from backend.accounts import router as accounts_router

app = FastAPI(
    title="JARVIS Web API",
    version="0.2.0"
)

app.include_router(accounts_router)

# Allow the React frontend to communicate with Python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, error: RequestValidationError):
    if request.url.path.startswith("/api/account/"):
        return JSONResponse({"detail": "Check your email and password."}, status_code=422)
    return await request_validation_exception_handler(request, error)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


@app.get("/")
def home():
    return {"assistant": "JARVIS", "status": "online"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat")
def chat(request: ChatRequest):
    message = request.message.strip()

    if not message:
        raise HTTPException(
            status_code=400,
            detail="Message cannot be empty"
        )

    try:
        reply = ask_ai(message)
        error = get_last_error()

        if error:
            raise HTTPException(
                status_code=503,
                detail=error
            )

        return {
            "assistant": "JARVIS",
            "reply": reply
        }

    except HTTPException:
        raise

    except Exception:
        raise HTTPException(
            status_code=503,
            detail="AI service unavailable"
        )
