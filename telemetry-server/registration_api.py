#!/usr/bin/env python3
"""
OIG Telemetry Registration API

Simple FastAPI service that:
1. Validates requests from OIG Proxy (via CLIENT_SECRET)
2. Issues JWT tokens for MQTT authentication
3. Tracks registered devices in SQLite

Deployment:
  docker-compose service on NAS alongside mosquitto/influxdb
"""

import hashlib
import hmac
import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import jwt
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# =============================================================================
# Configuration
# =============================================================================

# Secret shared with proxy (compile-time in proxy, env var here)
CLIENT_SECRET = os.getenv("OIG_CLIENT_SECRET", "oig-proxy-2026-telemetry-Kj8mN2xP4qR7vW9z")

# JWT signing key (keep secret!)
JWT_SECRET = os.getenv("OIG_JWT_SECRET", "oig-jwt-secret-2026-mQ5nR8sT2vX4yZ7a")

# Token validity
TOKEN_EXPIRY_DAYS = int(os.getenv("OIG_TOKEN_EXPIRY_DAYS", "30"))

# Database path
DB_PATH = Path(os.getenv("OIG_DB_PATH", "/data/devices.db"))

# =============================================================================
# Database
# =============================================================================

def init_db():
    """Initialize SQLite database."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            device_id TEXT PRIMARY KEY,
            instance_hash TEXT NOT NULL,
            version TEXT,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            token_issued TEXT,
            request_count INTEGER DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            device_id TEXT,
            action TEXT NOT NULL,
            details TEXT,
            ip_address TEXT
        )
    """)
    conn.commit()
    conn.close()


def get_device(device_id: str) -> Optional[dict]:
    """Get device from database."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def register_device(device_id: str, instance_hash: str, version: str) -> dict:
    """Register or update device in database."""
    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(str(DB_PATH))
    
    existing = get_device(device_id)
    if existing:
        # Update existing device
        conn.execute("""
            UPDATE devices 
            SET last_seen = ?, version = ?, request_count = request_count + 1
            WHERE device_id = ?
        """, (now, version, device_id))
    else:
        # New device
        conn.execute("""
            INSERT INTO devices (device_id, instance_hash, version, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?)
        """, (device_id, instance_hash, version, now, now))
    
    conn.commit()
    conn.close()
    return get_device(device_id)


def update_token_issued(device_id: str):
    """Update token_issued timestamp."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "UPDATE devices SET token_issued = ? WHERE device_id = ?",
        (datetime.utcnow().isoformat(), device_id)
    )
    conn.commit()
    conn.close()


def log_audit(device_id: str, action: str, details: str, ip_address: str):
    """Log audit entry."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        INSERT INTO audit_log (timestamp, device_id, action, details, ip_address)
        VALUES (?, ?, ?, ?, ?)
    """, (datetime.utcnow().isoformat(), device_id, action, details, ip_address))
    conn.commit()
    conn.close()


# =============================================================================
# JWT Token
# =============================================================================

def create_jwt_token(device_id: str, instance_hash: str) -> str:
    """Create JWT token for MQTT authentication."""
    payload = {
        "sub": device_id,
        "instance": instance_hash,
        "iat": int(time.time()),
        "exp": int(time.time()) + (TOKEN_EXPIRY_DAYS * 24 * 3600),
        "iss": "oig-telemetry",
        # ACL: allow publish to oig/telemetry/{device_id} and oig/events/{device_id}
        "acl": [
            f"oig/telemetry/{device_id}",
            f"oig/events/{device_id}"
        ]
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def verify_jwt_token(token: str) -> Optional[dict]:
    """Verify JWT token. Returns payload or None."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# =============================================================================
# API
# =============================================================================

app = FastAPI(
    title="OIG Telemetry Registration API",
    version="1.0.0",
    description="Device registration and JWT token issuance for OIG Proxy telemetry"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class ProvisionRequest(BaseModel):
    """Request model for device provisioning."""
    device_id: str = Field(..., pattern=r"^\d{10}$", description="10-digit device ID")
    instance_hash: str = Field(..., min_length=8, max_length=32, description="Instance hash")
    version: str = Field(..., description="Proxy version")


class ProvisionResponse(BaseModel):
    """Response model for device provisioning."""
    status: str
    device_id: str
    token: str
    expires_in: int  # seconds


class VerifyRequest(BaseModel):
    """Request model for token verification (used by Mosquitto auth plugin)."""
    username: str
    password: str  # JWT token


class VerifyResponse(BaseModel):
    """Response model for token verification."""
    ok: bool
    acl: list[str] = []


@app.on_event("startup")
async def startup():
    """Initialize database on startup."""
    init_db()


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.post("/api/provision", response_model=ProvisionResponse)
async def provision(
    request: Request,
    body: ProvisionRequest,
    x_client_secret: str = Header(None, alias="X-Client-Secret")
):
    """
    Provision a device and issue JWT token.
    
    Requires X-Client-Secret header for authentication.
    """
    client_ip = request.client.host if request.client else "unknown"
    
    # Verify client secret
    if not x_client_secret or not hmac.compare_digest(x_client_secret, CLIENT_SECRET):
        log_audit(body.device_id, "provision_failed", "Invalid client secret", client_ip)
        raise HTTPException(status_code=401, detail="Invalid client secret")
    
    # Validate device_id format (10 digits, starts with 2)
    if not body.device_id.isdigit() or len(body.device_id) != 10:
        log_audit(body.device_id, "provision_failed", "Invalid device_id format", client_ip)
        raise HTTPException(status_code=400, detail="Invalid device_id format")
    
    # Check instance_hash binding
    existing = get_device(body.device_id)
    if existing and existing["instance_hash"] != body.instance_hash:
        # Device already registered with different instance
        log_audit(body.device_id, "provision_rejected", 
                  f"Instance mismatch: {existing['instance_hash']} != {body.instance_hash}", client_ip)
        raise HTTPException(
            status_code=403, 
            detail="Device already registered with different instance"
        )
    
    # Register device
    device = register_device(body.device_id, body.instance_hash, body.version)
    
    # Issue token
    token = create_jwt_token(body.device_id, body.instance_hash)
    update_token_issued(body.device_id)
    
    log_audit(body.device_id, "provision_success", f"Token issued, version={body.version}", client_ip)
    
    return ProvisionResponse(
        status="ok",
        device_id=body.device_id,
        token=token,
        expires_in=TOKEN_EXPIRY_DAYS * 24 * 3600
    )


@app.post("/api/verify", response_model=VerifyResponse)
async def verify(body: VerifyRequest):
    """
    Verify JWT token (called by Mosquitto auth plugin).
    
    Returns ACL list if token is valid.
    """
    payload = verify_jwt_token(body.password)
    if not payload:
        return VerifyResponse(ok=False)
    
    # Verify username matches token subject
    if body.username != payload.get("sub"):
        return VerifyResponse(ok=False)
    
    return VerifyResponse(ok=True, acl=payload.get("acl", []))


# =============================================================================
# Mosquitto go-auth HTTP backend endpoints
# =============================================================================

class MqttAuthRequest(BaseModel):
    """Request model for Mosquitto HTTP auth."""
    username: str
    password: str
    clientid: str = ""
    topic: str = ""
    acc: int = 0  # 1=subscribe, 2=publish


@app.post("/mqtt/auth")
async def mqtt_auth(body: MqttAuthRequest):
    """
    Mosquitto HTTP auth - verify username/password (JWT token).
    Returns 200 OK if valid, 403 otherwise.
    """
    # Verify JWT token
    payload = verify_jwt_token(body.password)
    if not payload:
        raise HTTPException(status_code=403, detail="Invalid token")
    
    # Verify username matches token subject
    if body.username != payload.get("sub"):
        raise HTTPException(status_code=403, detail="Username mismatch")
    
    return {"ok": True}


@app.post("/mqtt/superuser")
async def mqtt_superuser(body: MqttAuthRequest):
    """
    Mosquitto HTTP auth - check if user is superuser.
    We don't have superusers, always return 403.
    """
    raise HTTPException(status_code=403, detail="Not a superuser")


@app.post("/mqtt/acl")
async def mqtt_acl(body: MqttAuthRequest):
    """
    Mosquitto HTTP auth - check ACL for topic.
    Returns 200 OK if allowed, 403 otherwise.
    """
    # Verify JWT token first
    payload = verify_jwt_token(body.password)
    if not payload:
        raise HTTPException(status_code=403, detail="Invalid token")
    
    # Check if topic is in ACL
    acl = payload.get("acl", [])
    if body.topic in acl:
        return {"ok": True}
    
    # Allow if topic matches pattern oig/telemetry/{username} or oig/events/{username}
    if body.topic == f"oig/telemetry/{body.username}" or body.topic == f"oig/events/{body.username}":
        return {"ok": True}
    
    raise HTTPException(status_code=403, detail="ACL denied")


@app.get("/api/devices")
async def list_devices():
    """List all registered devices (admin endpoint)."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT * FROM devices ORDER BY last_seen DESC")
    devices = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return {"devices": devices, "count": len(devices)}


@app.get("/api/devices/{device_id}")
async def get_device_info(device_id: str):
    """Get device info."""
    device = get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081)
