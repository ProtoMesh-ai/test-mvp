import asyncio
from typing import Any, Dict, Optional

import httpx


class ProtoMeshClient:
    def __init__(self, api_url: str, agent_id: str):
        self.api_url = api_url.rstrip("/")
        self.agent_id = agent_id
        self.client = httpx.AsyncClient()
        self._pubsub_client: Optional[Any] = None

    async def acquire_lock(
        self,
        resource_type: str,
        resource_id: str,
        priority: int = 5,
        wait: bool = False,
        max_wait_seconds: int = 60,
    ) -> dict:
        response = await self.client.post(
            f"{self.api_url}/v1/locks/acquire",
            json={
                "resource_type": resource_type,
                "resource_id": resource_id,
                "agent_id": self.agent_id,
                "priority": priority,
            },
        )
        response.raise_for_status()
        result = response.json()

        # If already acquired, return immediately
        if result["status"] == "acquired":
            return result

        # If queued and wait=True, poll until acquired
        if result["status"] == "queued" and wait:
            print(
                f"  [{self.agent_id}] Queued at position {result['position']}, waiting for lock grant..."
            )

            # Usin redis Pub/Sub to wait for lock grant notification
            lock_granted = await self._wait_for_lock_grant(
                resource_type, resource_id, max_wait_seconds
            )

            if lock_granted:
                # Lock was granted, retrieve the new lock_id from the result
                return lock_granted
            else:
                # Timeout cancel queue entry
                await self._cancel_lock_request(resource_type, resource_id)
                raise TimeoutError(f"Failed to acquire lock after {max_wait_seconds}s")

        return result

    async def _wait_for_lock_grant(
        self, resource_type: str, resource_id: str, timeout: int
    ) -> Optional[dict]:
        import redis.asyncio as redis

        # Create dedicated pubsub connection
        redis_client = redis.from_url("redis://localhost:6379", decode_responses=True)
        pubsub = redis_client.pubsub()

        channel = f"lock_granted:lock:{resource_type}:{resource_id}"
        await pubsub.subscribe(channel)

        try:
            async with asyncio.timeout(timeout):
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        data = message["data"]

                        # Message format: "agent_id:lock_id"
                        if data.startswith(f"{self.agent_id}:"):
                            lock_id = data.split(":", 1)[1]
                            print(f"  [{self.agent_id}] ✓ Lock granted! (lock_id={lock_id[:8]}...)")

                            return {"status": "acquired", "lock_id": lock_id, "method": "pubsub"}
        except asyncio.TimeoutError:
            return None
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
            await redis_client.close()

    async def _cancel_lock_request(self, resource_type: str, resource_id: str):
        """Cancel a queued lock request after timeout."""
        try:
            await self.client.post(
                f"{self.api_url}/v1/locks/cancel",
                json={
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "agent_id": self.agent_id,
                },
            )
        except Exception:
            pass

    async def release_lock(self, lock_id: str) -> dict:
        response = await self.client.post(
            f"{self.api_url}/v1/locks/release",
            json={
                "lock_id": lock_id,
                "agent_id": self.agent_id,  # For ownership verification
            },
        )
        response.raise_for_status()
        return response.json()

    async def check_lock(self, lock_id: str) -> dict:
        response = await self.client.get(f"{self.api_url}/v1/locks/{lock_id}/status")
        response.raise_for_status()
        return response.json()

    async def check_policy(self, action: str, metadata: Dict[str, Any]) -> dict:
        response = await self.client.post(
            f"{self.api_url}/v1/policies/check",
            json={"agent_id": self.agent_id, "action": action, "metadata": metadata},
        )
        response.raise_for_status()
        return response.json()

    async def close(self):
        await self.client.aclose()
