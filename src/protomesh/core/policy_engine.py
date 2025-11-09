from typing import Callable, Dict, Any
from pydantic import BaseModel

class PolicyCheck(BaseModel):
    agent_id: str
    action: str
    metadata: Dict[str, Any]

class PolicyResult(BaseModel):
    allowed: bool
    reason: str

class PolicyEngine:
    def __init__(self):
        # @todo Hard-coded policies for MVP
        self.policies: Dict[str, Callable] = {
            "spend_limit": self._check_spend_limit,
            "resource_access": self._check_resource_access,
        }
    
    async def check_policy(self, check: PolicyCheck) -> PolicyResult:
        for policy_name, policy_func in self.policies.items():
            result = await policy_func(check)
            if not result.allowed:
                return result
        
        return PolicyResult(allowed=True, reason="All policies passed")
    
    async def _check_spend_limit(self, check: PolicyCheck) -> PolicyResult:
        estimated_cost = check.metadata.get("estimated_cost", 0)
        agent_role = check.metadata.get("agent_role", "user")
        
        limits = {
            "junior": 100,
            "senior": 1000,
            "admin": 10000
        }
        
        limit = limits.get(agent_role, 100)
        
        if estimated_cost > limit:
            return PolicyResult(
                allowed=False,
                reason=f"Cost ${estimated_cost} exceeds limit ${limit} for role {agent_role}"
            )
        
        return PolicyResult(allowed=True, reason="Within spend limit")
    
    async def _check_resource_access(self, check: PolicyCheck) -> PolicyResult:
        _resource_type = check.metadata.get("resource_type")
        agent_team = check.metadata.get("agent_team", "default")
        resource_team = check.metadata.get("resource_team", "default")
        
        # @todo Simple Rule: agents can only access resources from their team
        if agent_team != resource_team and agent_team != "admin":
            return PolicyResult(
                allowed=False,
                reason=f"Agent team '{agent_team}' cannot access resource from team '{resource_team}'"
            )
        
        return PolicyResult(allowed=True, reason="Resource access permitted")