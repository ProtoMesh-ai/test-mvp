import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google import genai

from protomesh.sdk.client import ProtoMeshClient

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    print("[Agent A - Gemini] ERROR: GEMINI_API_KEY not found in .env file")
    sys.exit(1)

print("[Agent A - Gemini] Initializing Gemini client...")
gemini_client = genai.Client(api_key=api_key)


async def main():
    pm = ProtoMeshClient(api_url="http://localhost:8000", agent_id="agent_gemini_001")

    customer_id = "customer_123"
    lock_result = None

    try:
        print(f"[Agent A - Gemini] Attempting to acquire lock on customer {customer_id}")

        lock_result = await pm.acquire_lock(
            resource_type="customer", resource_id=customer_id, priority=8, wait=True
        )

        print(
            f"[Agent A - Gemini] ✓ Lock acquired: {lock_result['status']}, lock_id={lock_result['lock_id'][:8]}..."
        )

        resource_path = Path(__file__).parent / "shared_resource.json"
        with open(resource_path, "r") as f:
            data = json.load(f)

        customer = data.get(customer_id, {})
        print(
            f"[Agent A - Gemini] Read customer data: balance=${customer.get('balance')}, status={customer.get('status')}"
        )

        print("[Agent A - Gemini] Calling Gemini API (gemini-2.0-flash-exp)...")

        prompt = f"Generate a short friendly greeting (max 15 words) for a customer named {customer.get('name', 'Customer')}."

        response = gemini_client.models.generate_content(
            model="gemini-2.0-flash-exp", contents=prompt
        )

        greeting = response.text.strip()
        print(f'[Agent A - Gemini] ✓ Gemini response: "{greeting}"')

        # Update data
        customer["last_contact"] = f"Agent A: {greeting[:50]}"
        customer["balance"] = customer.get("balance", 0) + 100
        data[customer_id] = customer

        print("[Agent A - Gemini] Processing transaction...")
        await asyncio.sleep(2)  # Sim processing

        # Write back
        with open(resource_path, "w") as f:
            json.dump(data, f, indent=2)

        print(f"[Agent A - Gemini] ✓ Updated customer: balance=${customer['balance']}")

        return 0

    except Exception as e:
        print(f"[Agent A - Gemini] ✗ ERROR: {type(e).__name__}: {e}")
        import traceback

        traceback.print_exc()
        return 1

    finally:
        if lock_result and "lock_id" in lock_result:
            try:
                await pm.release_lock(lock_result["lock_id"])
                print("[Agent A - Gemini] ✓ Lock released")
            except Exception as _e:
                print("[Agent A - Gemini] ✗ Error releasing lock: {_e}")

        await pm.close()
        print("[Agent A - Gemini] ✓ Connection closed\n")


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)