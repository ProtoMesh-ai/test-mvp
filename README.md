# ⚠️ This repository is no longer maintained
Please visit the active repository here: https://github.com/ProtoMesh-ai/protomesh

ProtoMesh v0.1 (with CrewAI and LangGraph support) is available in the new repo.

------
------


## Architecture Overview
todo!

ProtoMesh sits between agents as a coordination layer. Agents call ProtoMesh for:
- Lock acquisition before modifying shared resources
- Policy checks before taking risky actions
- Priority based queuing when conflicts occur

Agents continue calling their LLMs directly, ProtoMesh adds no latency to AI inference.

---

## Design Choices
### 1. Coordination-Only Architecture
**Decision:** ProtoMesh does NOT proxy LLM calls \
**Rationale:** Most LLM calls don't need coordination. Only resource access and high risk actions require orchestration. Proxying all LLM traffic would add latency and become a single point of failure.

### 2. Redis for Distributed Locking
**Decision:** Use Redis for active locks and queues \
**Rationale:** Atomic operations (SETNX), sub-millisecond latency, built-in TTL for automatic lock expiry, battle-tested at scale.

### 3. Priority Queues Over FIFO
**Decision:** Locks are granted by priority, not fcfs \
**Rationale:** Critical agents (fraud detection, compliance) need to jump the queue. FIFO would block high priority work behind low priority batch jobs.

### 4. SQLite → PostgreSQL Migration Path
**Decision:** Start with SQLite, migrate to Postgres for production \
**Rationale:** SQLite enables rapid local development with zero configuration. PostgreSQL provides multi-tenancy, ACID guarantees, and horizontal scaling for production.

### 5. Hard Coded Policies for MVP
**Decision:** MVP has 3 hard coded policies, not a full DSL \
**Rationale:** Policy DSL is complex and can be added later. Hard coded rules prove the concept without over engineering.

### 6. Vendor Agnostic SDK
**Decision:** SDK is framework agnostic, works with any agent architecture \
**Rationale:** Enterprises use multiple frameworks. Lock-in to LangGraph or CrewAI defeats the purpose of cross framework coordination.

---

## Quick Start
### Prerequisites
- Python 3.11+
- Redis (running on localhost:6379)
- uv package manager

### Installation
```bash
# Clone repository
git clone https://github.com/ProtoMesh-Labs/protomesh-ai
cd protomesh

# Install dependencies
uv sync

# Copy environment template
cp .env.example .env

# Edit .env with your API keys

# Start Redis (in separate terminal)
docker run -p 6379:6379 redis:latest

# Start ProtoMesh API
uv run uvicorn protomesh.api.main:app --reload
```
Server runs at `http://localhost:8000`. API docs at `http://localhost:8000/docs`.

### ProtoMesh API Demo
```bash
# Execute the following after starting redis and ProtoMesh API
uv run python demo_agents/conflict1.py # (using real agents)

uv run python demo_agents/conflict2.py # (checking lock: simulation of conflict)
```

### Tests
```bash
# Run all tests
uv run pytest tests/test_locks.py -v

# Run with coverage
uv run pytest tests/test_locks.py -v --cov=protomesh --cov-report=term-missing

# Run specific test
uv run pytest tests/test_locks.py::test_concurrent_lock_attempts -v
```

**Expected behavior:** Both agents attempt to modify [shared_resource.json](./demo_agents/shared_resource.json). ProtoMesh coordinates access Agent A acquires lock, Agent B waits, both complete successfully without data corruption.

---

## Current Limitations (MVP)

- Single instance (no HA/replication yet)
- Hard-coded policies 
- SQLite storage (currently not being used)
- No observability dashboard
- Basic cost tracking (no automatic verification)

These limitations are intentional. Aim is to prove core coordination mechanics work.

---

## Roadmap
### Phase 1:
- [x] Distributed locking with Redis
- [x] Priority-based queuing
- [x] Basic policy engine
- [x] Python SDK
- [x] Demo agents (Gemini, Groq)

### Phase 2:
- [ ] PostgreSQL migration
- [ ] Configurable policy DSL
- [ ] Observability dashboard
- [ ] Support for 3+ frameworks
- [ ] Cost tracking with verification
- [ ] Multi-tenancy

### Phase 3:
- [ ] High availability (multi-region)
- [ ] Horizontal scaling
- [ ] Advanced policy engine (approval workflows)
- [ ] Enterprise auth (SSO, RBAC)
- [ ] Compliance features (SOC2, audit logs)
