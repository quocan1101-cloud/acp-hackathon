# acp-hackathon
# Yield Finder — Buyer/Seller Agent Demo

This project implements a **buyer–seller agent system** using the [Virtuals ACP](https://virtuals.io) framework.  
The goal is to demonstrate autonomous **yield-finding services**: a buyer agent requests the best yield opportunities, and a seller agent fulfills the request by aggregating providers (e.g., Beefy, Yearn).

---

## 📂 Project Structure

### `run_all.py`
- **Role:** Convenience launcher for both buyer and seller agents.
- **How it works:** Spawns two subprocesses (`seller.py` then `buyer.py`) and streams their output to the same terminal.
- **Usage:** 
  ```bash
  python run_all.py
