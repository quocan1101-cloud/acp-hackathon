# acp-hackathon
# Yield Finder — Buyer/Seller Agent Demo

This project implements a **buyer–seller agent system** using the [Virtuals ACP](https://virtuals.io) framework.  
The goal is to demonstrate autonomous **yield-finding services**: a buyer agent requests the best yield opportunities, and a seller agent fulfills the request by aggregating providers (e.g., Beefy, Yearn).

---

## 📂 Project Structure

### `buyer.py`
- **Role:** Serves as buyer agent.
- **How it works:**
  1. Connects to ACP network (using env variables).
  2. Prompts you for a keyword → displays relevant agents.
  3. Select offerings provided by the agent chosen
  4. Initiate job

### `seller.py`
- **Role:** Serves as seller agent.
- **How it works:**
  1. Connects to ACP and listens for jobs..
  2. In the REQUEST phase, prompts operator to Accept/Reject/Skip.
  3. At TRANSACTION, aggregates yield quotes via broker.py.
  4. Picks the best provider, builds a JSON deliverable, and delivers it to the buyer.

- **Deliverable format:**
```
{
  "service": "Find Yields",
  "inputs": { "asset": "USDC", "amount": 10000, "duration_days": 30, "risk level": "medium", "notes": "" },
  "quotes": ["ProviderA: 5.20% — Note", "ProviderB: 4.90% — Note"],
  "best": { "provider": "ProviderA", "apr": 0.052, "notes": "Best available" },
  "summary": "Best APR: ProviderA at 5.20%"
}
```
 
### `broker.py`
- **Role:** Browse through external yield providers.
- **How it works:**
  1. Queries providers (e.g., Beefy, Yearn) and returns tuples
  2. Chooses the optimal provider (e.g., highest APR).
 
### `renderers.py`
- **Role:** Console/UI helpers for seller output.
- **How it works:**
  1. Prints a formatted table of quotes, highlighting the best option.

 ### `services.py`
- **Role:** Handles deliverables per service.
- **How it works:**
  1. Standardizes the JSON deliverable format.


  

