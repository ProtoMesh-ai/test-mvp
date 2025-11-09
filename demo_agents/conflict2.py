import asyncio
import sys
import json
from pathlib import Path
from protomesh.sdk.client import ProtoMeshClient

class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

async def concurrent_agent(agent_id, priority, work_duration=3):
    pm = ProtoMeshClient("http://localhost:8000", agent_id)

    start_time = asyncio.get_event_loop().time()

    print(f"{Colors.OKCYAN}[{agent_id}] Starting (priority={priority}){Colors.ENDC}")

    try:
        # Try acquiring lock
        print(f"{Colors.OKBLUE}[{agent_id}] Requesting lock...{Colors.ENDC}")

        lock_result = await pm.acquire_lock(
            resource_type="customer",
            resource_id="customer_123",
            priority=priority,
            wait=True,
            max_wait_seconds=30
        )

        wait_time = asyncio.get_event_loop().time() - start_time

        if lock_result["status"] == "acquired":
            print(f"{Colors.OKGREEN}[{agent_id}] ✓ Lock acquired after {wait_time:.1f}s{Colors.ENDC}")

        # Read shared resource
        resource_path = Path(__file__).parent / "shared_resource.json"
        with open(resource_path, "r") as f:
            data = json.load(f)

        customer = data["customer_123"]
        print(f"{Colors.BOLD}[{agent_id}] Working on customer: balance=${customer['balance']}{Colors.ENDC}")

        # Simulate work
        await asyncio.sleep(work_duration)

        # Modify resource
        customer[f"accessed_by_{agent_id}"] = True
        customer["balance"] += 50
        data["customer_123"] = customer

        with open(resource_path, "w") as f:
            json.dump(data, f, indent=2)

        total_time = asyncio.get_event_loop().time() - start_time
        print(f"{Colors.OKGREEN}[{agent_id}] ✓ Work complete! (total time: {total_time:.1f}s){Colors.ENDC}")

        # Release lock
        await pm.release_lock(lock_result["lock_id"])
        print(f"{Colors.OKGREEN}[{agent_id}] ✓ Lock released{Colors.ENDC}")

        return {"agent": agent_id, "success": True, "total_time": total_time}

    except Exception as e:
        print(f"{Colors.FAIL}[{agent_id}] ✗ Failed: {e}{Colors.ENDC}")
        return {"agent": agent_id, "success": False, "error": str(e)}

    finally:
        await pm.close()

async def main():
    print(f"\n{Colors.HEADER}{'='*70}")
    print("ProtoMesh: Concurrent Conflict Resolution Demo")
    print(f"{'='*70}{Colors.ENDC}\n")

    print("Scenario:")
    print("  • 3 agents launch simultaneously")
    print("  • All try to lock customer_123 at the same time")
    print("  • Priority: Agent_A=10, Agent_B=5, Agent_C=8")
    print("  • Expected order: A (pri 10) → C (pri 8) → B (pri 5)")
    print(f"\n{'='*70}\n")

    # Reset shared resource
    resource_path = Path(__file__).parent / "shared_resource.json"
    initial_data = {
        "customer_123": {
            "name": "Alice Johnson",
            "balance": 1000,
            "status": "active"
        }
    }

    with open(resource_path, "w") as f:
        json.dump(initial_data, f, indent=2)

    print(f"{Colors.OKGREEN}✓ Reset shared_resource.json{Colors.ENDC}\n")
    print(f"{Colors.WARNING} Launching 3 agents simultaneously...{Colors.ENDC}\n")

    # Launch all agents at once
    start_time = asyncio.get_event_loop().time()

    results = await asyncio.gather(
        concurrent_agent("Agent_A_Priority10", priority=10, work_duration=2),
        concurrent_agent("Agent_B_Priority5", priority=5, work_duration=2),
        concurrent_agent("Agent_C_Priority8", priority=8, work_duration=2),
        return_exceptions=True
    )

    total_duration = asyncio.get_event_loop().time() - start_time

    # Show results
    print(f"\n{Colors.HEADER}{'='*70}")
    print("Demo Complete!")
    print(f"{'='*70}{Colors.ENDC}\n")

    print(f"  Total execution time: {total_duration:.1f}s\n")

    # Check results
    failures = []
    for result in results:
        if isinstance(result, Exception):
            failures.append(str(result))
        elif isinstance(result, dict) and not result.get("success"):
            failures.append(result.get("error", "Unknown error"))

    if failures:
        print(f"{Colors.FAIL} Some agents failed:{Colors.ENDC}")
        for failure in failures:
            print(f"  • {failure}")
        return 1

    # Show final state
    with open(resource_path, "r") as f:
        final_data = json.load(f)

    print(f"{Colors.BOLD} Final Customer State:{Colors.ENDC}")
    print(json.dumps(final_data["customer_123"], indent=2))
    
    # Verify correctness
    print(f"\n{Colors.BOLD} Verification:{Colors.ENDC}")
    customer = final_data["customer_123"]
    
    checks = [
        ("All 3 agents accessed resource", 
         all([
             customer.get("accessed_by_Agent_A_Priority10"),
             customer.get("accessed_by_Agent_B_Priority5"),
             customer.get("accessed_by_Agent_C_Priority8")
         ])),
        ("Balance updated correctly", customer.get("balance") == 1150),  # 1000 + 50*3
        ("No data corruption", customer.get("name") == "Alice Johnson"),
    ]
    
    all_passed = True
    for check_name, passed in checks:
        status = f"{Colors.OKGREEN}✓{Colors.ENDC}" if passed else f"{Colors.FAIL}✗{Colors.ENDC}"
        print(f"  {status} {check_name}")
        if not passed:
            all_passed = False

    if all_passed:
        print(f"\n{Colors.OKGREEN}{Colors.BOLD} SUCCESS!{Colors.ENDC}")
        print(f"{Colors.OKGREEN}   ProtoMesh prevented data corruption!")
        print(f"   All 3 agents executed in priority order without conflicts.{Colors.ENDC}")
    else:
        print(f"\n{Colors.FAIL}  Some verification checks failed.{Colors.ENDC}")
    
    return 0 if all_passed else 1

if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print(f"\n{Colors.WARNING}  Demo interrupted{Colors.ENDC}")
        sys.exit(1)