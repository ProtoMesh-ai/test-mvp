import asyncio

import pytest

from protomesh.core.lock_manager import LockManager


@pytest.fixture
async def lock_manager():
    lm = LockManager("redis://localhost:6379")
    yield lm
    # Cleanup
    await lm.redis.flushdb()
    await lm.redis.aclose()

# ===== Basic Fun Test =====

@pytest.mark.asyncio
async def test_acquire_lock(lock_manager):
    result = await lock_manager.acquire_lock(
        resource_type="customer",
        resource_id="123",
        agent_id="agent_a",
        priority=5
    )
    
    assert result["status"] == "acquired"
    assert "lock_id" in result
    assert result["expires_in"] == 300

@pytest.mark.asyncio
async def test_release_lock(lock_manager):
    # Acquire lock
    result = await lock_manager.acquire_lock(
        resource_type="customer",
        resource_id="123",
        agent_id="agent_a",
        priority=5
    )
    
    # Release lock
    release_result = await lock_manager.release_lock(result["lock_id"])
    assert release_result["status"] == "released"

@pytest.mark.asyncio
async def test_lock_status_check(lock_manager):
    # Acquire lock
    result = await lock_manager.acquire_lock(
        resource_type="customer",
        resource_id="123",
        agent_id="agent_a",
        priority=5
    )
    
    # Check status
    status = await lock_manager.check_lock_status(result["lock_id"])
    assert status["status"] == "active"
    assert status["agent_id"] == "agent_a"
    
    # Release and check again
    await lock_manager.release_lock(result["lock_id"])
    status_after = await lock_manager.check_lock_status(result["lock_id"])
    assert status_after["status"] == "expired"


# ===== Conflict Resolution Test =====

@pytest.mark.asyncio
async def test_conflict_queuing(lock_manager):
    """Testing that second agent gets queued when lock's held."""
    # Agent A acquires lock
    result_a = await lock_manager.acquire_lock(
        resource_type="customer",
        resource_id="123",
        agent_id="agent_a",
        priority=5
    )
    assert result_a["status"] == "acquired"
    
    # Agent B tries to acquire same lock (should be queued)
    result_b = await lock_manager.acquire_lock(
        resource_type="customer",
        resource_id="123",
        agent_id="agent_b",
        priority=3
    )
    assert result_b["status"] == "queued"
    assert result_b["position"] == 1

@pytest.mark.asyncio
async def test_priority_ordering(lock_manager):
    """Testing locks are granted by priority order."""
    # Agent A holds lock
    result_a = await lock_manager.acquire_lock(
        resource_type="customer",
        resource_id="123",
        agent_id="agent_a",
        priority=5
    )
    assert result_a["status"] == "acquired"
    
    # Queue agents with different priorities
    result_low = await lock_manager.acquire_lock(
        "customer", "123", "agent_low", priority=1
    )
    result_high = await lock_manager.acquire_lock(
        "customer", "123", "agent_high", priority=10
    )
    result_mid = await lock_manager.acquire_lock(
        "customer", "123", "agent_mid", priority=5
    )
    
    assert result_low["status"] == "queued"
    assert result_high["status"] == "queued"
    assert result_mid["status"] == "queued"
    
    # Release lock highest priority should be notified
    release_result = await lock_manager.release_lock(result_a["lock_id"])
    
    # The next_agent should be agent_high (priority 10)
    assert "agent_high" in release_result.get("next_agent", "")

@pytest.mark.asyncio
async def test_multiple_resources_no_conflict(lock_manager):
    """Testing different resources don't conflict."""
    # Lock customer 123
    result_a = await lock_manager.acquire_lock(
        resource_type="customer",
        resource_id="123",
        agent_id="agent_a",
        priority=5
    )
    
    # Lock customer 456 (different resource)
    result_b = await lock_manager.acquire_lock(
        resource_type="customer",
        resource_id="456",
        agent_id="agent_b",
        priority=5
    )
    
    # Both should acquire immediately (no conflict)
    assert result_a["status"] == "acquired"
    assert result_b["status"] == "acquired"

@pytest.mark.asyncio
async def test_concurrent_lock_attempts(lock_manager):
    """Testing truly concurrent lock attempts."""
    async def try_lock(agent_id, priority):
        return await lock_manager.acquire_lock(
            resource_type="customer",
            resource_id="123",
            agent_id=agent_id,
            priority=priority
        )
    
    # Launch 3 agents simultaneously
    results = await asyncio.gather(
        try_lock("agent_a", 8),
        try_lock("agent_b", 5),
        try_lock("agent_c", 10),
    )
    
    # Exactly one should acquire others queued
    acquired = [r for r in results if r["status"] == "acquired"]
    queued = [r for r in results if r["status"] == "queued"]
    
    assert len(acquired) == 1
    assert len(queued) == 2


# ===== Lock Expiry Tests =====

@pytest.mark.asyncio
async def test_lock_expiry(lock_manager):
    """Testing locks expire after TTL."""
    # Acquire lock with short TTL
    result = await lock_manager.acquire_lock(
        resource_type="customer",
        resource_id="123",
        agent_id="agent_a",
        priority=5,
        ttl=2  # 2sec
    )
    assert result["status"] == "acquired"
    
    # Wait for expiry
    await asyncio.sleep(3)
    
    # Check status should be expired
    status = await lock_manager.check_lock_status(result["lock_id"])
    assert status["status"] == "expired"
    
    # Another agent should be able to acquire now
    result_b = await lock_manager.acquire_lock(
        resource_type="customer",
        resource_id="123",
        agent_id="agent_b",
        priority=5
    )
    assert result_b["status"] == "acquired"

# ===== Edge Cases =====

@pytest.mark.asyncio
async def test_release_nonexistent_lock(lock_manager):
    """Testing releasing lock that doesn't exist."""
    result = await lock_manager.release_lock("fake-lock-id")
    assert result["status"] == "error"
    assert "not found" in result["message"].lower()

@pytest.mark.asyncio
async def test_same_agent_multiple_locks(lock_manager):
    """Testing same agent can hold multiple locks on different resources."""
    result_a = await lock_manager.acquire_lock(
        resource_type="customer",
        resource_id="123",
        agent_id="agent_a",
        priority=5
    )
    
    result_b = await lock_manager.acquire_lock(
        resource_type="customer",
        resource_id="456",
        agent_id="agent_a",  # Same agent
        priority=5
    )
    
    assert result_a["status"] == "acquired"
    assert result_b["status"] == "acquired"