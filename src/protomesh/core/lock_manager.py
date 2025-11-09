import uuid
from datetime import datetime
from typing import Optional

import redis.asyncio as redis


class LockManager:
    def __init__(self, redis_url: str, default_ttl: int = 300):
        self.redis = redis.from_url(redis_url, decode_responses=True)
        self.default_ttl = default_ttl

    async def acquire_lock(
        self,
        resource_type: str,
        resource_id: str,
        agent_id: str,
        priority: int = 5,
        ttl: Optional[int] = None,
        allow_reentrant: bool = False,
    ) -> dict:
        """
        Acquire a lock on a resource atomically.
        Returns:
            {
                "status": "acquired" | "queued" | "cancelled" | "already_owned",
                "lock_id": str,
                "position": int (if queued),
                "expires_in": int (if acquired),
            }
        """
        lock_key = f"lock:{resource_type}:{resource_id}"
        queue_key = f"queue:{resource_type}:{resource_id}"
        lock_id = str(uuid.uuid4())
        ttl = ttl or self.default_ttl

        agent_lock_key = f"agent_lock:{resource_type}:{resource_id}:{agent_id}"
        cancel_key = f"cancel:{resource_type}:{resource_id}:{agent_id}"

        acquire_script = """
            -- KEYS[1] = lock_key
            -- KEYS[2] = lock_meta_key  
            -- KEYS[3] = queue_key
            -- KEYS[4] = agent_lock_key
            -- KEYS[5] = cancel_key
            -- ARGV[1] = agent_id
            -- ARGV[2] = ttl
            -- ARGV[3] = lock_id
            -- ARGV[4] = acquired_at (ISO timestamp)
            -- ARGV[5] = resource_type
            -- ARGV[6] = resource_id
            -- ARGV[7] = priority_score (negative for descending)
            -- ARGV[8] = allow_reentrant ("1" or "0")
            
            -- Check if this agent has a pending cancellation flag
            local cancel_flag = redis.call('GET', KEYS[5])
            if cancel_flag then
                redis.call('DEL', KEYS[5])
                return {-1, 0}  -- Status: cancelled
            end
            
            -- Check if lock is already held by this agent (re-entrancy check)
            local current_owner = redis.call('GET', KEYS[1])
            if current_owner == ARGV[1] then
                if ARGV[8] == "1" then
                    -- Re-entrant acquisition allowed: extend TTL and return existing lock_id
                    local existing_lock_id = redis.call('GET', KEYS[4])
                    if existing_lock_id then
                        local ttl = tonumber(ARGV[2])
                        redis.call('EXPIRE', KEYS[1], ttl)
                        
                        local existing_meta_key = "lock_meta:" .. existing_lock_id
                        redis.call('EXPIRE', existing_meta_key, ttl)
                        redis.call('EXPIRE', KEYS[4], ttl)
                        
                        return {2, ttl, existing_lock_id}  -- Status: already_owned (extended)
                    end
                else
                    -- Re-entrancy not allowed
                    return {-2, 0}  -- Status: already_owned_error
                end
            end
            
            -- Try to acquire lock atomically
            local acquired = redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', tonumber(ARGV[2]))
            
            if acquired then
                -- Lock acquired! Set metadata with same TTL
                redis.call('HSET', KEYS[2],
                    'lock_key', KEYS[1],
                    'agent_id', ARGV[1],
                    'lock_id', ARGV[3],
                    'acquired_at', ARGV[4],
                    'resource_type', ARGV[5],
                    'resource_id', ARGV[6]
                )
                redis.call('EXPIRE', KEYS[2], tonumber(ARGV[2]))
                
                -- Store lock_id mapping for this agent
                redis.call('SET', KEYS[4], ARGV[3], 'EX', tonumber(ARGV[2]))
                
                return {1, tonumber(ARGV[2])}  -- Status: acquired, TTL
            else
                -- Lock held by someone else, join queue
                
                -- Check if this agent is already queued
                local agent_score = redis.call('ZSCORE', KEYS[3], ARGV[1])
                
                if agent_score then
                    -- Already in queue, return existing position
                    local position = redis.call('ZRANK', KEYS[3], ARGV[1])
                    
                    -- Update the pending lock_id mapping (in case of retry with new lock_id)
                    redis.call('SET', KEYS[4], ARGV[3], 'EX', 3600)
                    
                    return {0, position}  -- Status: queued (already), position
                else
                    -- Add to priority queue
                    redis.call('ZADD', KEYS[3], tonumber(ARGV[7]), ARGV[1])
                    
                    -- Store pending lock_id with longer TTL (for when granted)
                    redis.call('SET', KEYS[4], ARGV[3], 'EX', 3600)
                    
                    -- Get queue position
                    local position = redis.call('ZRANK', KEYS[3], ARGV[1])
                    
                    return {0, position}  -- Status: queued, position
                end
            end
        """

        result = await self.redis.eval(
            acquire_script,
            5,
            lock_key,
            f"lock_meta:{lock_id}",
            queue_key,
            agent_lock_key,
            cancel_key,
            # ARGV values
            agent_id,
            str(ttl),
            lock_id,
            datetime.utcnow().isoformat(),
            resource_type,
            resource_id,
            str(-priority),  # -ve for descending sort
            "1" if allow_reentrant else "0",
        )

        status_code = result[0]
        value = result[1]

        if status_code == -1:
            return {"status": "cancelled", "message": "Lock request was cancelled"}
        elif status_code == -2:
            return {
                "status": "error",
                "message": "Agent already holds this lock (re-entrancy not allowed)",
            }
        elif status_code == 1:
            # Newly acquired
            return {"status": "acquired", "lock_id": lock_id, "expires_in": value}
        elif status_code == 2:
            # Already owned, TTL extended
            existing_lock_id = result[2] if len(result) > 2 else lock_id
            return {
                "status": "already_owned",
                "lock_id": existing_lock_id,
                "expires_in": value,
                "extended": True,
            }
        else:
            # Queued
            position = value + 1 if value is not None else 1
            return {"status": "queued", "lock_id": lock_id, "position": position}

    async def release_lock(
        self, lock_id: str, agent_id: Optional[str] = None, idempotent: bool = True
    ) -> dict:
        """
        Returns:
            {
                "status": "released" | "error",
                "next_agent": str (if granted to next agent),
                "next_lock_id": str (if granted),
            }
        """
        release_script = """
            -- KEYS[1] = lock_meta:{lock_id}
            -- ARGV[1] = agent_id (for ownership verification, "" if not provided)
            -- ARGV[2] = default_ttl
            -- ARGV[3] = idempotent ("1" or "0")
            
            -- Check lock metadata exists
            local meta = redis.call('HGETALL', KEYS[1])
            if #meta == 0 then
                if ARGV[3] == "1" then
                    -- Idempotent mode: already released is success
                    return {0, "", "", ""}
                else
                    return {-1, "Lock not found or already expired", "", ""}
                end
            end
            
            -- Parse metadata
            local lock_key = nil
            local owner_agent_id = nil
            local resource_type = nil
            local resource_id = nil
            
            for i = 1, #meta, 2 do
                if meta[i] == "lock_key" then
                    lock_key = meta[i + 1]
                elseif meta[i] == "agent_id" then
                    owner_agent_id = meta[i + 1]
                elseif meta[i] == "resource_type" then
                    resource_type = meta[i + 1]
                elseif meta[i] == "resource_id" then
                    resource_id = meta[i + 1]
                end
            end
            
            if not lock_key then
                return {-2, "Invalid lock metadata", "", ""}
            end
            
            -- Verify ownership if agent_id provided
            if ARGV[1] ~= "" and ARGV[1] ~= owner_agent_id then
                return {-3, "Permission denied: not lock owner", "", ""}
            end
            
            -- Verify lock still exists and is owned by this agent
            -- Prevents race where lock expired and was acquired by someone else
            local current_owner = redis.call('GET', lock_key)
            if not current_owner then
                -- Lock expired between metadata check and now
                -- Clean up orphaned metadata
                redis.call('DEL', KEYS[1])
                local owner_agent_lock_key = "agent_lock:" .. resource_type .. ":" .. resource_id .. ":" .. owner_agent_id
                redis.call('DEL', owner_agent_lock_key)
                
                if ARGV[3] == "1" then
                    return {0, "", "", ""}  -- Idempotent: success
                else
                    return {-4, "Lock expired before release", "", ""}
                end
            end
            
            if current_owner ~= owner_agent_id then
                -- Lock was taken by someone else (should be impossible, but safety check)
                return {-5, "Lock ownership changed during release", "", ""}
            end
            
            local queue_key = string.gsub(lock_key, "lock:", "queue:")
            local owner_agent_lock_key = "agent_lock:" .. resource_type .. ":" .. resource_id .. ":" .. owner_agent_id
            
            -- Loop to find first non-cancelled agent in queue
            local max_retries = 10
            local next_agent_id = nil
            local next_lock_id = nil
            
            for retry = 1, max_retries do
                local next_in_queue = redis.call('ZPOPMIN', queue_key, 1)
                
                if #next_in_queue == 0 then
                    -- Queue is empty
                    break
                end
                
                next_agent_id = next_in_queue[1]
                
                -- Check if this agent cancelled while queued
                local cancel_key = "cancel:" .. resource_type .. ":" .. resource_id .. ":" .. next_agent_id
                local was_cancelled = redis.call('GET', cancel_key)
                
                if was_cancelled then
                    -- Agent cancelled, clean up and try next agent
                    redis.call('DEL', cancel_key)
                    local cancelled_agent_lock_key = "agent_lock:" .. resource_type .. ":" .. resource_id .. ":" .. next_agent_id
                    redis.call('DEL', cancelled_agent_lock_key)
                    next_agent_id = nil  -- Mark as invalid, continue loop
                else
                    -- Found valid (non-cancelled) agent
                    local next_agent_lock_key = "agent_lock:" .. resource_type .. ":" .. resource_id .. ":" .. next_agent_id
                    next_lock_id = redis.call('GET', next_agent_lock_key)
                    
                    if not next_lock_id then
                        -- Mapping missing (edge case), generate new lock_id
                        local counter = redis.call('INCR', 'lock_id_counter')
                        next_lock_id = "fallback_" .. tostring(counter)
                    end
                    
                    break  -- Found valid agent, exit loop
                end
            end
            
            if next_agent_id then
                -- Grant lock to next valid agent
                local ttl = tonumber(ARGV[2])
                
                -- Clean up old owner's data
                redis.call('DEL', KEYS[1])
                redis.call('DEL', owner_agent_lock_key)
                
                -- Atomically grant lock to next agent
                redis.call('SET', lock_key, next_agent_id, 'XX', 'EX', ttl)
                
                -- Verify the SET succeeded (lock still existed)
                local verify = redis.call('GET', lock_key)
                if not verify or verify ~= next_agent_id then
                    -- Lock disappeared during grant (expired), re-queue agent
                    redis.call('ZADD', queue_key, next_in_queue[2], next_agent_id)
                    return {0, "", "", ""}
                end
                
                -- Create new metadata for next agent
                local next_meta_key = "lock_meta:" .. next_lock_id
                redis.call('HSET', next_meta_key,
                    'lock_key', lock_key,
                    'agent_id', next_agent_id,
                    'lock_id', next_lock_id,
                    'acquired_at', redis.call('TIME')[1],
                    'resource_type', resource_type,
                    'resource_id', resource_id
                )
                redis.call('EXPIRE', next_meta_key, ttl)
                
                -- Update agent lock mapping
                local next_agent_lock_key = "agent_lock:" .. resource_type .. ":" .. resource_id .. ":" .. next_agent_id
                redis.call('SET', next_agent_lock_key, next_lock_id, 'EX', ttl)
                
                -- Publish notification for pub/sub listeners
                redis.call('PUBLISH', 'lock_granted:' .. lock_key, next_agent_id .. ':' .. next_lock_id)
                
                return {1, next_agent_id, next_lock_id, ""}
            else
                -- No valid agents in queue, release completely
                redis.call('DEL', lock_key)
                redis.call('DEL', KEYS[1])
                redis.call('DEL', owner_agent_lock_key)
                
                return {0, "", "", ""}
            end
        """

        result = await self.redis.eval(
            release_script,
            1,
            f"lock_meta:{lock_id}",
            agent_id or "",
            str(self.default_ttl),
            "1" if idempotent else "0",
        )

        status_code = result[0]

        if status_code < 0:
            message = result[1] if len(result) > 1 else "Unknown error"
            return {"status": "error", "message": message}
        elif status_code == 1:
            next_agent_id = result[1] if len(result) > 1 else None
            next_lock_id = result[2] if len(result) > 2 else None
            return {
                "status": "released",
                "next_agent": next_agent_id,
                "next_lock_id": next_lock_id,
            }
        else:
            return {"status": "released", "next_agent": None}

    # @TODO: This is a non-atomic read. The lock could expire between reading metadata and returning the result. Callers should handle this race condition.
    async def check_lock_status(self, lock_id: str) -> dict:
        meta = await self.redis.hgetall(f"lock_meta:{lock_id}")
        if not meta:
            return {"status": "expired"}

        # Double-check that the actual lock still exists
        lock_key = meta.get("lock_key")
        if lock_key:
            owner = await self.redis.get(lock_key)
            if not owner:
                return {"status": "expired"}

        return {
            "status": "active",
            "agent_id": meta.get("agent_id"),
            "acquired_at": meta.get("acquired_at"),
            "resource_type": meta.get("resource_type"),
            "resource_id": meta.get("resource_id"),
        }

    async def get_queue_position(
        self, resource_type: str, resource_id: str, agent_id: str
    ) -> Optional[int]:
        queue_key = f"queue:{resource_type}:{resource_id}"
        position = await self.redis.zrank(queue_key, agent_id)
        return position + 1 if position is not None else None

    async def cancel_lock_request(
        self, resource_type: str, resource_id: str, agent_id: str
    ) -> dict:
        queue_key = f"queue:{resource_type}:{resource_id}"
        agent_lock_key = f"agent_lock:{resource_type}:{resource_id}:{agent_id}"
        cancel_key = f"cancel:{resource_type}:{resource_id}:{agent_id}"

        cancel_script = """
            local queue_key = KEYS[1]
            local agent_lock_key = KEYS[2]
            local cancel_key = KEYS[3]
            local agent_id = ARGV[1]
            
            -- Try to remove from queue
            local removed = redis.call('ZREM', queue_key, agent_id)
            
            if removed > 0 then
                -- Successfully removed from queue
                redis.call('DEL', agent_lock_key)
                return {1, "removed_from_queue"}
            else
                -- Not in queue - might have just been granted lock
                -- Set cancellation flag (60s TTL) so release_lock will skip this agent
                redis.call('SET', cancel_key, '1', 'EX', 60)
                return {0, "not_in_queue_flag_set"}
            end
        """

        result = await self.redis.eval(
            cancel_script, 3, queue_key, agent_lock_key, cancel_key, agent_id
        )

        _status_code = result[0]
        detail = result[1] if len(result) > 1 else ""

        return {"status": "cancelled", "detail": detail}

    async def extend_lock(
        self, lock_id: str, additional_ttl: int, agent_id: Optional[str] = None
    ) -> dict:
        # Get metadata first (needed for resource info)
        meta = await self.redis.hgetall(f"lock_meta:{lock_id}")
        if not meta:
            return {"status": "error", "message": "Lock not found"}

        resource_type = meta.get("resource_type", "")
        resource_id = meta.get("resource_id", "")

        extend_script = """
            -- KEYS[1] = lock_meta_key
            -- ARGV[1] = agent_id (for verification)
            -- ARGV[2] = additional_ttl
            -- ARGV[3] = resource_type
            -- ARGV[4] = resource_id
            
            local meta = redis.call('HGETALL', KEYS[1])
            if #meta == 0 then
                return {-1, "Lock metadata not found"}
            end
            
            local lock_key = nil
            local owner = nil
            
            for i = 1, #meta, 2 do
                if meta[i] == "lock_key" then
                    lock_key = meta[i + 1]
                elseif meta[i] == "agent_id" then
                    owner = meta[i + 1]
                end
            end
            
            -- Verify ownership
            if ARGV[1] ~= "" and ARGV[1] ~= owner then
                return {-2, "Not lock owner"}
            end
            
            -- Verify lock still exists and is owned by this agent
            -- Prevents extending a lock that expired and was acquired by someone else
            local current_owner = redis.call('GET', lock_key)
            if not current_owner then
                return {-3, "Lock expired"}
            end
            
            if current_owner ~= owner then
                return {-4, "Lock ownership changed"}
            end
            
            -- Extend all related keys atomically
            local ttl_added = tonumber(ARGV[2])
            local expire_result_1 = redis.call('EXPIRE', lock_key, ttl_added)
            local expire_result_2 = redis.call('EXPIRE', KEYS[1], ttl_added)
            
            -- Extend agent lock mapping
            local agent_lock_key = "agent_lock:" .. ARGV[3] .. ":" .. ARGV[4] .. ":" .. owner
            local expire_result_3 = redis.call('EXPIRE', agent_lock_key, ttl_added)
            
            -- Verify all EXPIREs succeeded (paranoid check)
            if expire_result_1 == 0 or expire_result_2 == 0 then
                return {-5, "Lock expired during extend"}
            end
            
            return {1, ttl_added}
        """

        result = await self.redis.eval(
            extend_script,
            1,
            f"lock_meta:{lock_id}",
            agent_id or "",
            str(additional_ttl),
            resource_type,
            resource_id,
        )

        status_code = result[0]

        if status_code < 0:
            message = result[1] if len(result) > 1 else "Unknown error"
            return {"status": "error", "message": message}
        else:
            return {"status": "extended", "new_ttl": result[1]}

    async def cleanup_all_locks(self) -> dict:
        cleanup_script = """
            local cursor = "0"
            local cleaned = 0
            
            -- Clean all lock_meta keys and associated data
            repeat
                local result = redis.call('SCAN', cursor, 'MATCH', 'lock_meta:*', 'COUNT', 100)
                cursor = result[1]
                local keys = result[2]
                
                for _, meta_key in ipairs(keys) do
                    local meta = redis.call('HGETALL', meta_key)
                    
                    if #meta > 0 then
                        local lock_key = nil
                        
                        for i = 1, #meta, 2 do
                            if meta[i] == "lock_key" then
                                lock_key = meta[i + 1]
                                break
                            end
                        end
                        
                        if lock_key then
                            local queue_key = string.gsub(lock_key, "lock:", "queue:")
                            
                            -- Delete lock, metadata, and queue
                            redis.call('DEL', lock_key)
                            redis.call('DEL', meta_key)
                            redis.call('DEL', queue_key)
                            
                            cleaned = cleaned + 1
                        end
                    end
                end
            until cursor == "0"
            
            -- Clean all agent_lock:* mappings
            cursor = "0"
            repeat
                local result = redis.call('SCAN', cursor, 'MATCH', 'agent_lock:*', 'COUNT', 100)
                cursor = result[1]
                local keys = result[2]
                
                for _, key in ipairs(keys) do
                    redis.call('DEL', key)
                end
            until cursor == "0"
            
            -- Clean all cancel:* flags
            cursor = "0"
            repeat
                local result = redis.call('SCAN', cursor, 'MATCH', 'cancel:*', 'COUNT', 100)
                cursor = result[1]
                local keys = result[2]
                
                for _, key in ipairs(keys) do
                    redis.call('DEL', key)
                end
            until cursor == "0"
            
            return cleaned
        """

        cleaned = await self.redis.eval(cleanup_script, 0)
        return {"status": "cleaned", "locks_released": cleaned}
