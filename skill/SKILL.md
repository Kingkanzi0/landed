---
name: landed
description: Escrow an agent's fee for a cross-chain transfer and let GenLayer validators settle it from LI.FI's own record. Use when an agent is hired to bridge or swap funds across chains through LI.FI and should be paid only if the funds really landed on the agreed terms, or when a client wants a refund because they did not. An Internet Court layer-6 (verification and disputes) flow on top of LI.FI (layer 5, execution).
---

# Landed: a court for cross-chain jobs

Landed is a GenLayer Intelligent Contract. A **client** escrows an **agent's** fee with
plain-language delivery terms. The agent does the transfer through LI.FI and files the
source transaction hash. Every validator reads `https://li.quest/v1/status?txHash=<hash>`,
reduces the reply to a fixed record with plain code, and judges each term against it.
Validators must agree on the record and on every single term. Plain code pays the agent
only if LI.FI says `DONE`, the substatus is not `REFUNDED`, the transfer was sent after the
job's `not_before` time, and every term is `MET`.

```text
Client: open_job(terms, agent, fee)  ->  Agent: bridges via LI.FI  ->  Agent: file_proof(tx)
  -> validators read LI.FI, rule term by term  ->  PAY (fee to agent) or REJECT (3 tries, then client reclaims)
```

## When to use it

- An agent is paid to move value across chains (LI.FI bridge or swap) and the payer wants
  "no delivery, no fee" without trusting the agent's word.
- A delivery is disputed ("you didn't send the full amount", "it went to the wrong chain").
- Another contract needs a verdict on a cross-chain job: read `get_verdict(job_id)`.

Do **not** use it for same-chain payments with no LI.FI record, or for subjective work
(use Signoff or the Internet Court GenLayer review skill for that).

## Contract

| Item | Value |
| --- | --- |
| Source | `contracts/landed.py` in https://github.com/Kingkanzi0/landed |
| Network | see the repo README "Deployment" table (Studionet now, Bradbury next) |
| Web app | https://kingkanzi0.github.io/landed/frontend/ |

### Methods

| Method | Who | Arguments |
| --- | --- | --- |
| `open_job` (payable) | client | `title: str`, `terms: str` (1 to 5 lines, 8 to 200 chars each), `agent: str` (0x address, not the client), `days: int` (1 to 30), `not_before: int` (unix seconds; `0` = now). Send the fee as the call value. Returns `{"id", "fee"}`. |
| `file_proof` | agent | `job_id: int`, `tx_hash: str` (0x + 64 hex, the LI.FI **source** transaction). Up to 3 filings. Returns the ruling. |
| `reclaim` | client | `job_id: int`. After the deadline or 3 rejected filings. |
| `get_job`, `get_jobs(offset, limit)` | anyone | Full job, terms, last ruling with the LI.FI record, per-term statuses and reasons. |
| `get_verdict` | anyone | `{"status": "OPEN" or "PAID" or "REFUNDED", "decision": "PAY" or "REJECT" or ""}` |

## Agent workflow

1. **Discover first, commit nothing.** Read the job with `get_job` and restate the terms.
   You hold no funds or authority the user has not granted.
2. **Quote and execute through LI.FI** (load `vendored/lifi/lifi/SKILL.md` in the Internet
   Court package): `GET /quote`, sign the `transactionRequest`, then poll `GET /status`
   until `DONE`. Make sure `toAddress`, destination chain, token and amount satisfy the terms.
3. **Preview the evidence** exactly as validators will see it:
   `curl "https://li.quest/v1/status?txHash=<source tx>"`. Check `status == DONE`,
   `substatus` is `COMPLETED` (or `PARTIAL` only if the terms allow another token),
   `receiving.chainId`, `receiving.amount` (divide by `10^token.decimals`) and `toAddress`.
4. **File proof** with `file_proof(job_id, tx_hash)`. Do not file a transfer sent before the
   job's `not_before`; it is rejected by plain code and costs a filing.
5. **Report faithfully.** A `REJECT` lists which term failed and why. Never claim payment
   before `get_verdict` shows `PAID`.

### Calling it

With the GenLayer CLI (reads and the agent's filing):

```bash
genlayer network set studionet          # or testnet-bradbury, per the README
genlayer call  <LANDED_ADDRESS> get_job --args 0
genlayer write <LANDED_ADDRESS> file_proof --args 0 0x<source-tx-hash>
genlayer call  <LANDED_ADDRESS> get_verdict --args 0
```

With genlayer-js (needed for `open_job`, which carries the fee as value):

```ts
import { createClient, createAccount } from "genlayer-js";
import { studionet } from "genlayer-js/chains";
const client = createClient({ chain: studionet, account: createAccount(process.env.KEY) });
const hash = await client.writeContract({
  address: LANDED, functionName: "open_job",
  args: ["Bridge 50 USDC to Base", "Move USDC from Arbitrum to Base\nAt least 49 USDC must arrive on Base\nFunds must arrive at 0x…", AGENT, 3, 0],
  value: 10n ** 17n,            // 0.1 GEN fee
});
await client.waitForTransactionReceipt({ hash, status: "ACCEPTED" });
```

## Writing good terms

Terms are judged only against LI.FI's record, so write facts the record can prove:
source and destination chain, token symbol, a minimum received amount, the receiving
address. "Fast" or "cheap" cannot be proven; write "within 30 minutes of sending" instead
(the record has both timestamps).

## Guarantees and limits

- The PAY decision is recomputed by plain code from the agreed record; a model saying
  "MET" can never pay out a transfer LI.FI reports as pending, failed, not found or refunded.
- One transfer can pay only one job, and only if sent after that job's `not_before`.
- If LI.FI is unreachable the call fails without using a filing; try again later.
- Testnet-first. This is protocol design, not legal or financial advice.
