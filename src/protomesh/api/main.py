import os
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from protomesh.core.lock_manager import LockManager
from protomesh.core.policy_engine import PolicyCheck, PolicyEngine
from protomesh.storage.database import Database

load_dotenv()

app = FastAPI(title="ProtoMesh", version="0.1.0")

# Init components
lock_manager = LockManager(redis_url=os.getenv("REDIS_URL", "redis://localhost:6379"))
policy_engine = PolicyEngine()
database = Database(database_url=os.getenv("DATABASE_URL", "sqlite:///./protomesh.db"))


# Create tables on startup
@app.on_event("startup")
async def startup():
    database.create_tables()

    try:
        await lock_manager.redis.ping()
        print("✓ Redis connection successful")
    except Exception as e:
        print(f"✗ Redis connection failed: {e}")
        print("Make sure Redis is running.")
        raise


@app.on_event("shutdown")
async def shutdown():
    print("Shutting down ProtoMesh...")

    result = await lock_manager.cleanup_all_locks()
    print(f"✓ Cleaned up {result['locks_released']} locks")

    await lock_manager.redis.aclose()
    print("✓ Redis connection closed")


# Request models
class AcquireLockRequest(BaseModel):
    resource_type: str
    resource_id: str
    agent_id: str
    priority: int = 5
    ttl: Optional[int] = None


class ReleaseLockRequest(BaseModel):
    lock_id: str
    agent_id: Optional[str] = None


class CancelLockRequest(BaseModel):
    resource_type: str
    resource_id: str
    agent_id: str


class PolicyCheckRequest(BaseModel):
    agent_id: str
    action: str
    metadata: Dict[str, Any]


# Endpoints
@app.post("/v1/locks/acquire")
async def acquire_lock(request: AcquireLockRequest):
    try:
        result = await lock_manager.acquire_lock(
            resource_type=request.resource_type,
            resource_id=request.resource_id,
            agent_id=request.agent_id,
            priority=request.priority,
            ttl=request.ttl,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/locks/release")
async def release_lock(request: ReleaseLockRequest):
    try:
        result = await lock_manager.release_lock(lock_id=request.lock_id, agent_id=request.agent_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/locks/cancel")
async def cancel_lock(request: CancelLockRequest):
    try:
        result = await lock_manager.cancel_lock_request(
            resource_type=request.resource_type,
            resource_id=request.resource_id,
            agent_id=request.agent_id,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/v1/locks/{lock_id}/status")
async def check_lock(lock_id: str):
    result = await lock_manager.check_lock_status(lock_id)
    return result


@app.post("/v1/policies/check")
async def check_policy(request: PolicyCheckRequest):
    try:
        check = PolicyCheck(**request.dict())
        result = await policy_engine.check_policy(check)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    return {"status": "healthy"}
