import os
import random
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from auth import (
    create_device_api_token,
    get_current_user,
    hash_api_token,
)
from database import get_connection
from rate_limit import limiter

router = APIRouter()

PAIRING_TTL_MINUTES = int(os.getenv("DEVICE_PAIRING_CODE_TTL_MINUTES", "15"))


class PairingCodeResponse(BaseModel):
    code: str
    expires_at: str


class ClaimPairingCodeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=8)
    device_name: str | None = None
    serial_number: str | None = None


def _dict_row_factory(c, r):
    return {col[0]: r[i] for i, col in enumerate(c.description)}


def _generate_pairing_code() -> str:
    return f"{random.randint(0, 999999):06d}"


def _issue_unique_pairing_code(conn) -> str:
    cur = conn.cursor()
    for _ in range(20):
        code = _generate_pairing_code()
        cur.execute("SELECT 1 FROM device_pairing_codes WHERE code = ?", (code,))
        if not cur.fetchone():
            return code
    raise HTTPException(status_code=500, detail="Could not generate a unique pairing code.")


@router.get("/devices")
async def list_devices(current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    conn.row_factory = _dict_row_factory
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, device_name, serial_number, status, paired_at, unpaired_at, last_seen_at, created_at
            FROM devices
            WHERE user_id = ?
              AND (status IS NULL OR status = 'active')
            ORDER BY created_at DESC
            """,
            (current_user["id"],),
        )
        return cur.fetchall()
    finally:
        conn.close()


@router.post("/devices/pairing-codes", response_model=PairingCodeResponse)
@limiter.limit("20/minute")
async def create_pairing_code(request: Request, current_user: dict = Depends(get_current_user)):
    now = datetime.utcnow()
    expires_at = now + timedelta(minutes=PAIRING_TTL_MINUTES)

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM device_pairing_codes WHERE expires_at <= ?", (now.isoformat(),))
        code = _issue_unique_pairing_code(conn)
        cur.execute(
            """
            INSERT INTO device_pairing_codes (code, user_id, expires_at, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (code, current_user["id"], expires_at.isoformat(), now.isoformat()),
        )
        conn.commit()
        return {"code": code, "expires_at": expires_at.isoformat()}
    finally:
        conn.close()


@router.post("/devices/claim")
@limiter.limit("20/minute")
async def claim_pairing_code(request: Request, body: ClaimPairingCodeRequest):
    code = body.code.strip()
    now = datetime.utcnow().isoformat()
    device_id = str(uuid.uuid4())
    device_token = create_device_api_token()
    token_hash = hash_api_token(device_token)

    conn = get_connection()
    conn.row_factory = _dict_row_factory
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.cursor()
        cur.execute(
            """
            SELECT pc.*, u.email AS owner_email
            FROM device_pairing_codes pc
            JOIN users u ON u.id = pc.user_id
            WHERE pc.code = ?
            """,
            (code,),
        )
        pairing = cur.fetchone()
        if not pairing:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Pairing code not found.")
        if pairing.get("claimed_at"):
            conn.rollback()
            raise HTTPException(status_code=409, detail="Pairing code has already been used.")
        if pairing["expires_at"] <= now:
            conn.rollback()
            raise HTTPException(status_code=410, detail="Pairing code has expired.")

        cur.execute(
            """
            INSERT INTO devices
              (id, user_id, device_name, serial_number, auth_token_hash, status, paired_at, last_seen_at, created_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (
                device_id,
                pairing["user_id"],
                (body.device_name or "MeetingBox").strip() or "MeetingBox",
                (body.serial_number or "").strip() or None,
                token_hash,
                now,
                now,
                now,
            ),
        )
        cur.execute(
            "UPDATE device_pairing_codes SET claimed_at = ?, device_id = ? WHERE code = ?",
            (now, device_id, code),
        )
        conn.commit()
        return {
            "device": {
                "id": device_id,
                "device_name": (body.device_name or "MeetingBox").strip() or "MeetingBox",
                "serial_number": (body.serial_number or "").strip() or None,
                "status": "active",
                "paired_at": now,
            },
            "access_token": device_token,
            "owner_user_id": pairing["user_id"],
            "owner_email": pairing.get("owner_email"),
        }
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@router.post("/devices/{device_id}/unpair")
async def unpair_device(device_id: str, current_user: dict = Depends(get_current_user)):
    now = datetime.utcnow().isoformat()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE devices
            SET status = 'unpaired', auth_token_hash = NULL, unpaired_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (now, device_id, current_user["id"]),
        )
        if cur.rowcount == 0:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Device not found.")
        conn.commit()
        return {"status": "unpaired", "device_id": device_id}
    finally:
        conn.close()
