# Landed: pay the agent only when the money landed

**An Internet Court partner demo: GenLayer × LI.FI.**

Agents already move money across chains for people. What they lack is a way to be trusted
by a client they've never met: *"I bridged your 50 USDC, pay me."* Landed puts the agent's
fee in escrow and lets GenLayer validators settle the job from **LI.FI's own record** of
the transfer.

```text
Client ── open_job(terms, agent, fee) ──▶ Landed (GenLayer)        fee held in escrow
Agent  ── bridges through LI.FI ────────▶ Arbitrum → Base
Agent  ── file_proof(source tx) ────────▶ Landed
                                           │ every validator: GET li.quest/v1/status?txHash=…
                                           │ plain code: reduce reply to a fixed record
                                           │ LLM: judge each term against the record
                                           │ validators must agree on the record AND every term
                                           ▼
                         DONE + all terms MET ─▶ fee paid to the agent
                         otherwise ────────────▶ REJECT (3 filings), then the client reclaims
```

- **Web app:** https://kingkanzi0.github.io/landed/frontend/
- **Agent skill:** [`skill/SKILL.md`](skill/SKILL.md), so any agent can reproduce the flow.
- **Contract:** [`contracts/landed.py`](contracts/landed.py)

## Why GenLayer is essential here

LI.FI already tells you *what happened* to a transfer. It can't tell you whether that
satisfies **a deal written in plain language** between two parties, and nobody can pay out
on its word without trusting whoever reads it. Landed makes that reading a decentralised
verdict:

- **Independent reads:** every validator fetches LI.FI itself; a leader showing a fake
  amount is outvoted (`test_validators_reject_a_leader_reading_a_different_transfer`).
- **Term-by-term judgment:** terms like "at least 45 USDC must arrive on Base" or "within
  30 minutes" are judged against the record, and validators must agree on **every** term,
  not just the final PAY/REJECT (`test_validators_bind_every_term_verdict`).
- **Code owns the money:** the payout is recomputed by plain code. Nothing pays unless LI.FI
  says `DONE` and not `REFUNDED`, whatever a model says
  (`test_pending_or_refunded_never_pays_even_if_llm_says_met`).
- **No recycled proofs:** a transfer must be sent after the job's `not_before` time, and one
  transfer can pay only one job.

## How LI.FI is used

`https://li.quest/v1/status?txHash=<hash>` (public, no API key) is the evidence source.
`record_from()` keeps only the stable facts (status, substatus, tool, from and to address,
and for each side: chain, token symbol, exact amount from base units and decimals, tx hash,
timestamp) and drops volatile ones (gas, USD prices, links), so every validator compares
the same thing. The web app runs the same reduction in the browser, so users see exactly
what validators will read before they file.

## Using it

**Web app.** Connect a wallet (OKX or MetaMask). On Studionet, tap **Get test GEN**.

1. **Check a transfer:** paste any LI.FI source tx hash (find one on https://scan.li.fi) and
   tap *Read LI.FI*.
2. **Open a job** as the client. *Use the checked transfer as the example* fills realistic
   terms from that record and sets "count transfers sent after" to just before it was sent.
   Enter your second wallet as the agent and lock a fee.
3. Switch to the agent wallet, reload, tap **File proof**, and file the same tx hash.
4. Watch the validators rule. Every term shows MET or NOT MET with a reason. Make one term
   stricter (for example a higher minimum amount) to see a REJECT.

**Agents.** Load [`skill/SKILL.md`](skill/SKILL.md). It plugs into the Internet Court skill
package at layer 6 (verification and disputes) on top of LI.FI at layer 5 (execution).

## Contract API

| Method | Notes |
| --- | --- |
| `open_job(title, terms, agent, days, not_before)` payable | 1 to 5 terms, one per line; fee = call value |
| `file_proof(job_id, tx_hash)` | agent only, up to 3 filings |
| `reclaim(job_id)` | client only, after the deadline or 3 rejected filings |
| `get_job`, `get_jobs(offset, limit)` | job, terms, last ruling with LI.FI record, statuses, reasons |
| `get_verdict(job_id)` | `{status, decision}` for other contracts and agents |

## Tests

```bash
python3 tests/test_landed.py
```

18 offline tests with a mocked GenLayer SDK (`tests/mock_genlayer.py`) where leader and
validators can be shown different LI.FI replies and LLM answers: exact unit conversion,
record stability, job rules, delivered pays, unmet term rejects, pending/failed/not
found/refunded never pay, old transfers can't be reused, one transfer can't pay two jobs,
validators bind every term verdict, a leader with a different record is outvoted, LI.FI
outage costs no filing, reclaim paths, malformed LLM output never pays.

## Deployment

| Item | Value |
| --- | --- |
| Network | GenLayer Studionet (chain 61999) |
| Contract | `0x7BaFA0ffDB23fE9827d2cc9124edF953BdCBCf5c` |
| Deploy tx | [`0xb8d231ec…a72c48`](https://explorer-studio.genlayer.com/tx/0xb8d231ecd707bf3d35e87fcd9dcf28592902a65cdf99d79036a86a1f0ca72c48) (13 Nov 2026) |

## Proof

Run live on 13 Nov 2026 from the web app with real LI.FI transfers (Ethereum → Base via Across for PAY, Arbitrum → Ethereum via LI.FI Intents for REJECT).

| Proof | Transaction |
| --- | --- |
| Job opened, fee escrowed | [`0x7626e0a0…3bea62`](https://explorer-studio.genlayer.com/tx/0x7626e0a00d6eb8cd96ce99982790d1b811fd3b8423da4b9015a560a0d63bea62) |
| Ruling PAY: LI.FI DONE, every term MET, fee paid | [`0x03f969bb…e22cd0`](https://explorer-studio.genlayer.com/tx/0x03f969bbd4970060c997e90c9e8b5c282778fb9317ad7c55ed01877baae22cd0) |
| Ruling REJECT: one term NOT MET, fee stays in escrow | [`0xf3d6383d…9cc9cc`](https://explorer-studio.genlayer.com/tx/0xf3d6383d77db2214fd51f98705342711c660d044f838b2fb07fb6abde69cc9cc) |

## Built with

Internet Court (the open standard for agent-to-agent commerce), GenLayer (adjudication),
LI.FI (cross-chain execution and evidence). Not legal or financial advice. Testnet only.
