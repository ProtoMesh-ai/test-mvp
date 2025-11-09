import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from groq import AsyncGroq

from protomesh.sdk.client import ProtoMeshClient

load_dotenv()

api_key = os.getenv("GROQ_API_KEY")
if not api_key:
    print("[Agent B - Groq] ERROR: GROQ_API_KEY not found in .env file")
    sys.exit(1)

groq_client = AsyncGroq(api_key=api_key)


async def main():
    pm = ProtoMeshClient(api_url="http://localhost:8000", agent_id="agent_groq_002")

    customer_id = "customer_123"
    lock_result = None

    try:
        print(f"[Agent B - Groq] Attempting to acquire lock on customer {customer_id}")

        lock_result = await pm.acquire_lock(
            resource_type="customer",
            resource_id=customer_id,
            priority=5,  # Lower priority than Agent A
            wait=True,
        )

        print(
            f"[Agent B - Groq] ✓ Lock acquired: {lock_result['status']}, lock_id={lock_result['lock_id'][:8]}..."
        )

        resource_path = Path(__file__).parent / "shared_resource.json"
        with open(resource_path, "r") as f:
            data = json.load(f)

        customer = data.get(customer_id, {})
        print(
            f"[Agent B - Groq] Read customer data: balance=${customer.get('balance')}, status={customer.get('status')}"
        )

        prompt = f"""Analyze this customer for fraud risk:
Name: {customer.get("name")}
Balance: ${customer.get("balance")}
Status: {customer.get("status")}
Last Contact: {customer.get("last_contact")}

Respond with ONLY one word: LOW, MEDIUM, or HIGH"""

        print("[Agent B - Groq] Calling Groq API (llama-3.3-70b-versatile)...")

        response = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": "You are a fraud detection expert. Respond with only: LOW, MEDIUM, or HIGH.",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=10,
            temperature=0,
        )

        risk_level = response.choices[0].message.content.strip().upper()

        if risk_level not in ["LOW", "MEDIUM", "HIGH"]:
            risk_level = "MEDIUM"

        print(f"[Agent B - Groq] ✓ Groq fraud assessment: {risk_level} risk")

        # Update data
        customer["fraud_risk"] = risk_level
        customer["last_fraud_check"] = "Agent B via Groq"

        if risk_level == "HIGH":
            customer["status"] = "flagged"
            print("[Agent B - Groq]   HIGH RISK - Customer flagged!")

        data[customer_id] = customer

        print("[Agent B - Groq] Processing fraud check...")
        await asyncio.sleep(1.5)

        # Write back
        with open(resource_path, "w") as f:
            json.dump(data, f, indent=2)

        print(
            f"[Agent B - Groq] ✓ Updated customer: status={customer['status']}, risk={risk_level}"
        )

        return 0

    except Exception as e:
        print(f"[Agent B - Groq] ✗ ERROR: {type(e).__name__}: {e}")
        import traceback

        traceback.print_exc()
        return 1

    finally:
        if lock_result and "lock_id" in lock_result:
            try:
                await pm.release_lock(lock_result["lock_id"])
                print("[Agent B - Groq] ✓ Lock released")
            except Exception as _e:
                print("[Agent B - Groq] ✗ Error releasing lock: {_e}")

        await pm.close()
        print("[Agent B - Groq] ✓ Connection closed\n")


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)