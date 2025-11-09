import asyncio
import json
import subprocess
import sys
from pathlib import Path


async def run_agent_blocking(script_name):
    print(f"\n{'=' * 60}")
    print(f"Starting {script_name}")
    print(f"{'=' * 60}\n")

    process = await asyncio.create_subprocess_exec(
        sys.executable, script_name, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )

    while True:
        line = await process.stdout.readline()
        if not line:
            break
        print(line.decode().rstrip())

    await process.wait()
    return process.returncode


async def main():
    print("\n" + "=" * 60)
    print("ProtoMesh Phase 1 Demo: Sequential Execution")
    print("=" * 60)
    print("\nScenario:")
    print("- Run Agent A first, then Agent B")
    print("- Both modify the same customer record")
    print("- Demonstrate clean handoff via locks")
    print("=" * 60 + "\n")

    # Reset shared resource
    resource_path = Path(__file__).parent / "shared_resource.json"
    initial_data = {
        "customer_123": {
            "name": "Alice Johnson",
            "balance": 1000,
            "status": "active",
            "last_contact": None,
        }
    }

    with open(resource_path, "w") as f:
        json.dump(initial_data, f, indent=2)

    print("✓ Reset shared_resource.json to initial state\n")

    # Agent A
    print(" Running Agent A (Gemini)...")
    result_a = await run_agent_blocking("demo_agents/agent_a_gemini.py")

    if result_a != 0:
        print("\n Agent A failed!")
        return 1

    # Agent B
    print("\n Running Agent B (Groq)...")
    result_b = await run_agent_blocking("demo_agents/agent_b_groq.py")

    if result_b != 0:
        print("\n Agent B failed!")
        return 1

    print("\n" + "=" * 60)
    print("Demo Complete!")
    print("=" * 60)

    with open(resource_path, "r") as f:
        final_data = json.load(f)

    print("\n Final Customer State:")
    print(json.dumps(final_data["customer_123"], indent=2))

    print("\n Verification:")
    customer = final_data["customer_123"]

    checks = [
        ("Balance updated by Agent A", customer.get("balance") == 1100),
        ("Last contact recorded", customer.get("last_contact") is not None),
        ("Fraud check completed", customer.get("fraud_risk") == "LOW"),
        ("Both agents executed", customer.get("last_fraud_check") is not None),
    ]

    for check_name, passed in checks:
        status = "✓" if passed else "✗"
        print(f"  {status} {check_name}")

    all_passed = all(passed for _, passed in checks)

    if all_passed:
        print("\n SUCCESS: Both agents executed cleanly!")
        print("   ProtoMesh locks prevented conflicts.")
    else:
        print("\n  Some checks failed.")

    return 0 if all_passed else 1


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n  Demo interrupted")
        sys.exit(1)