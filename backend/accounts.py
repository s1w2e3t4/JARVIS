import hashlib
import os
from pathlib import Path
import secrets
import sqlite3
import time
from contextlib import closing

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field

router = APIRouter(prefix="/api/account")
passwords = PasswordHasher()
dummy_hash = passwords.hash(secrets.token_hex(32))
cookie_name = "jarvis_session"
session_lifetime = 7 * 24 * 60 * 60


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


def database():
    path = Path(os.environ.get(
        "JARVIS_ACCOUNT_DB", Path(__file__).resolve().parent / "data" / "accounts.sqlite3"
    ))
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, check_same_thread=False)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL
            );
        """)
        yield connection


def require_account_header(request: Request):
    # Cross-origin browsers need CORS permission to send this custom header.
    if request.headers.get("X-Jarvis-Account") != "1":
        raise HTTPException(403, "Account request rejected.")


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def cookie_options():
    return {
        "path": "/api/account",
        "httponly": True,
        "samesite": "strict",
        "secure": os.environ.get("JARVIS_SECURE_COOKIES") == "1",
    }


def start_session(connection, request, response, user_id):
    now = int(time.time())
    token = secrets.token_urlsafe(32)
    previous = request.cookies.get(cookie_name)
    if previous:
        connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(previous),))
    connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
    connection.execute(
        "INSERT INTO sessions VALUES (?, ?, ?)",
        (token_hash(token), user_id, now + session_lifetime),
    )
    connection.commit()
    response.set_cookie(cookie_name, token, max_age=session_lifetime, **cookie_options())


@router.post("/register", status_code=201, dependencies=[Depends(require_account_header)])
def register(credentials: Credentials, request: Request, response: Response, connection=Depends(database)):
    if len(credentials.password) < 15:
        raise HTTPException(422, "Use at least 15 characters for your password.")
    email = credentials.email.casefold()
    password_hash = passwords.hash(credentials.password)
    try:
        user = connection.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)", (email, password_hash)
        )
    except sqlite3.IntegrityError as error:
        raise HTTPException(409, "Could not create this account. Try signing in.") from error
    start_session(connection, request, response, user.lastrowid)
    return {"email": email}


@router.post("/login", dependencies=[Depends(require_account_header)])
def login(credentials: Credentials, request: Request, response: Response, connection=Depends(database)):
    user = connection.execute(
        "SELECT id, email, password_hash FROM users WHERE email = ?", (credentials.email.casefold(),)
    ).fetchone()
    try:
        passwords.verify(user[2] if user else dummy_hash, credentials.password)
    except VerifyMismatchError:
        raise HTTPException(401, "Email or password is incorrect.") from None
    if user is None:
        raise HTTPException(401, "Email or password is incorrect.")
    start_session(connection, request, response, user[0])
    return {"email": user[1]}


@router.get("/session", dependencies=[Depends(require_account_header)])
def session(request: Request, response: Response, connection=Depends(database)):
    response.headers["Cache-Control"] = "no-store"
    token = request.cookies.get(cookie_name)
    user = None
    if token:
        user = connection.execute(
            """SELECT users.email FROM sessions JOIN users ON users.id = sessions.user_id
               WHERE token_hash = ? AND expires_at > ?""",
            (token_hash(token), int(time.time())),
        ).fetchone()
    return {"email": user[0] if user else None}


@router.post("/logout", status_code=204, dependencies=[Depends(require_account_header)])
def logout(request: Request, response: Response, connection=Depends(database)):
    token = request.cookies.get(cookie_name)
    if token:
        connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))
        connection.commit()
    response.delete_cookie(cookie_name, **cookie_options())
