# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

"""Landed: an Internet Court for cross-chain jobs, using LI.FI as the evidence.

A client escrows an agent's fee with plain-language delivery terms
("at least 45 USDC must arrive on Base at 0xabc..."). The agent does the
transfer through LI.FI and files the source transaction hash. Each validator
reads LI.FI's own status API for that hash, reduces it to a fixed record with
plain code, and an LLM judges every term against that record. Validators must
agree on the record and on every term's verdict. Fixed code pays the agent only
when LI.FI reports the transfer DONE and every term is MET; after the deadline
or three failed filings the client can reclaim the fee."""

import json
import re
from datetime import datetime, timezone

from genlayer import *

MET, NOT_MET = "MET", "NOT_MET"
OPEN, PAID, REFUNDED = "OPEN", "PAID", "REFUNDED"
PAY, REJECT = "PAY", "REJECT"
MIN_TERMS, MAX_TERMS, MIN_LEN, MAX_LEN = 1, 5, 8, 200
MAX_TITLE, MAX_NOTE = 80, 200
MAX_ATTEMPTS, MIN_DAYS, MAX_DAYS, DAY = 3, 1, 30, 86400
LIFI = "https://li.quest/v1/status?txHash="
UA = {"User-Agent": "Landed-InternetCourt/1.0", "Accept": "application/json"}

E_EXP, E_TRANS, E_LLM = "[EXPECTED]", "[TRANSIENT]", "[LLM_ERROR]"
ADDR_RE = re.compile(r"^0x[0-9a-f]{40}$")
TX_RE = re.compile(r"^0x[0-9a-f]{64}$")
CHAINS = {1: "Ethereum", 10: "Optimism", 56: "BNB Chain", 100: "Gnosis", 137: "Polygon", 324: "zkSync Era",
	8453: "Base", 42161: "Arbitrum", 43114: "Avalanche", 59144: "Linea", 534352: "Scroll", 81457: "Blast"}


def _now():
	return int(datetime.now(timezone.utc).timestamp())


def _msg(err):
	m = getattr(err, "message", None)
	return str(m if m is not None else err)


def _fail(text):
	raise gl.vm.UserError(f"{E_EXP} {text}")


def units(amount, decimals):
	"""Base units -> exact decimal string, with no float rounding."""
	try:
		a, d = int(amount), max(0, min(int(decimals), 36))
	except (TypeError, ValueError):
		return ""
	whole, frac = divmod(a, 10 ** d) if d else (a, 0)
	f = str(frac).rjust(d, "0").rstrip("0") if d else ""
	return f"{whole}.{f}" if f else str(whole)


def _side(s):
	s = s if isinstance(s, dict) else {}
	tok = s.get("token") if isinstance(s.get("token"), dict) else {}
	cid = s.get("chainId")
	try:
		cid = int(cid)
	except (TypeError, ValueError):
		cid = 0
	return {"chain": CHAINS.get(cid, f"chain {cid}") if cid else "", "chain_id": cid,
		"token": str(tok.get("symbol", ""))[:16], "amount": units(s.get("amount", 0), tok.get("decimals", 0)),
		"tx": str(s.get("txHash", "")).lower(), "time": int(s.get("timestamp") or 0)}


def record_from(data):
	"""Plain code: keep only the stable facts of a LI.FI status reply, so every
	validator compares the same thing (no prices, gas or links)."""
	if not isinstance(data, dict):
		raise gl.vm.UserError(f"{E_TRANS} LI.FI returned no JSON object")
	return {"status": str(data.get("status", "NOT_FOUND")), "substatus": str(data.get("substatus", "")),
		"tool": str(data.get("tool", ""))[:32], "from_address": str(data.get("fromAddress", "")).lower(),
		"to_address": str(data.get("toAddress", "")).lower(),
		"sent": _side(data.get("sending")), "received": _side(data.get("receiving"))}


def fetch_record(tx):
	try:
		res = gl.nondet.web.get(LIFI + tx, headers=UA)
	except Exception:
		raise gl.vm.UserError(f"{E_TRANS} LI.FI could not be reached")
	if res.status == 404:
		return {"status": "NOT_FOUND", "substatus": "", "tool": "", "from_address": "", "to_address": "",
			"sent": _side({}), "received": _side({})}
	if res.status != 200:
		raise gl.vm.UserError(f"{E_TRANS} LI.FI answered HTTP {res.status}")
	try:
		data = json.loads(res.body.decode("utf-8"))
	except Exception:
		raise gl.vm.UserError(f"{E_TRANS} LI.FI reply was not JSON")
	return record_from(data)


def precheck(rec, not_before):
	"""Deterministic gates that no wording can override."""
	if rec["status"] != "DONE":
		return f"LI.FI reports {rec['status']} {rec['substatus']}".strip() + ", so nothing has landed yet"
	if rec["substatus"] == "REFUNDED":
		return "LI.FI reports the funds were refunded to the sender, so nothing was delivered"
	if rec["sent"]["time"] and rec["sent"]["time"] < not_before:
		return "this transfer was sent before the job was opened, so it cannot count for it"
	return ""


def decide(rec, statuses, not_before):
	if precheck(rec, not_before):
		return REJECT
	return PAY if statuses and all(s == MET for s in statuses) else REJECT


def judge(tx, terms, not_before):
	"""Non-deterministic: read LI.FI, then judge every term against the record."""
	rec = fetch_record(tx)
	gate = precheck(rec, not_before)
	if gate:
		return {"record": rec, "statuses": [NOT_MET] * len(terms), "decision": REJECT, "note": gate}
	listed = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(terms))
	prompt = f"""You are the court for a cross-chain delivery job. LI.FI's own status record for the
agent's transfer is below (amounts are already in whole tokens). For each numbered term decide
MET only if the record clearly proves it, otherwise NOT_MET. Compare addresses case-insensitively.
"received" is what landed on the destination chain. If substatus is PARTIAL a different token
landed than was sent, so a term naming a specific token on the destination is NOT_MET unless that token landed.
Do not assume facts the record does not show.
Terms:
{listed}
Record: {json.dumps(rec)}
JSON only: {{"results": ["MET" or "NOT_MET", ...one per term], "reasons": ["short reason", ...], "note": "one short sentence"}}"""
	raw = gl.nondet.exec_prompt(prompt, response_format="json")
	if not isinstance(raw, dict) or not isinstance(raw.get("results"), list) or len(raw["results"]) != len(terms):
		raise gl.vm.UserError(f"{E_LLM} expected one verdict per term")
	statuses = [MET if str(r).strip().upper() == MET else NOT_MET for r in raw["results"]]
	reasons = raw.get("reasons") if isinstance(raw.get("reasons"), list) else []
	reasons = [str(x)[:120] for x in reasons][:len(terms)]
	return {"record": rec, "statuses": statuses, "reasons": reasons,
		"decision": decide(rec, statuses, not_before), "note": str(raw.get("note", ""))[:MAX_NOTE]}


def _agree_on_error(leader_res, rerun):
	lm = _msg(leader_res)
	try:
		rerun()
		return False
	except gl.vm.UserError as e:
		m = _msg(e)
		if m.startswith(E_EXP):
			return m == lm
		return m.startswith(E_TRANS) and lm.startswith(E_TRANS)
	except Exception:
		return False


def same_facts(a, b):
	"""Validators must have read the same transfer: status, amounts, tokens, chains, addresses."""
	keys = ("status", "substatus", "from_address", "to_address")
	side = ("chain_id", "token", "amount", "tx")
	return all(a.get(k) == b.get(k) for k in keys) and all(
		a.get(s, {}).get(k) == b.get(s, {}).get(k) for s in ("sent", "received") for k in side)


@gl.evm.contract_interface
class _Payee:
	class View:
		pass

	class Write:
		pass


class Landed(gl.Contract):
	job_count: u256
	j_title: TreeMap[u256, str]
	j_terms: TreeMap[u256, str]        # JSON list
	j_client: TreeMap[u256, str]
	j_agent: TreeMap[u256, str]
	j_fee: TreeMap[u256, u256]
	j_opened: TreeMap[u256, u256]
	j_deadline: TreeMap[u256, u256]
	j_status: TreeMap[u256, str]
	j_attempts: TreeMap[u256, u32]
	j_last: TreeMap[u256, str]         # JSON of the last ruling
	used_tx: TreeMap[str, str]         # tx hash -> job id that already used it

	def __init__(self):
		self.job_count = u256(0)

	def _jid(self, job_id):
		if job_id < 0 or job_id >= int(self.job_count):
			_fail("unknown job")
		return u256(job_id)

	@gl.public.write.payable
	def open_job(self, title: str, terms: str, agent: str, days: int, not_before: int) -> str:
		"""Escrow the agent's fee. `not_before` (unix seconds, 0 = now) is the earliest
		send time a transfer may have; older transfers can never satisfy this job."""
		fee = int(gl.message.value)
		if fee <= 0:
			_fail("send the agent's fee in GEN with this call")
		ts = [t.strip() for t in terms.split("\n") if t.strip()]
		if not MIN_TERMS <= len(ts) <= MAX_TERMS:
			_fail(f"give {MIN_TERMS} to {MAX_TERMS} delivery terms, one per line")
		if any(not MIN_LEN <= len(t) <= MAX_LEN for t in ts):
			_fail(f"each term must be {MIN_LEN}-{MAX_LEN} characters")
		if not MIN_DAYS <= days <= MAX_DAYS:
			_fail(f"deadline must be {MIN_DAYS} to {MAX_DAYS} days")
		client = gl.message.sender_address.as_hex.lower()
		a = agent.strip().lower()
		if ADDR_RE.match(a) is None:
			_fail("agent must be a 0x address")
		if a == client:
			_fail("the agent must be a different address")
		now = _now()
		nb = now if int(not_before) <= 0 else int(not_before)
		if nb > now:
			_fail("not_before cannot be in the future")
		jid = u256(int(self.job_count))
		self.job_count = u256(int(jid) + 1)
		self.j_title[jid], self.j_terms[jid] = title.strip()[:MAX_TITLE], json.dumps(ts)
		self.j_client[jid], self.j_agent[jid] = client, a
		self.j_fee[jid], self.j_opened[jid] = u256(fee), u256(nb)
		self.j_deadline[jid] = u256(now + days * DAY)
		self.j_status[jid], self.j_last[jid] = OPEN, ""
		return json.dumps({"id": int(jid), "fee": fee})

	@gl.public.write
	def file_proof(self, job_id: int, tx_hash: str) -> str:
		"""The agent files the LI.FI source transaction. Validators rule on it."""
		jid = self._jid(job_id)
		if str(self.j_status[jid]) != OPEN:
			_fail("this job is closed")
		if gl.message.sender_address.as_hex.lower() != str(self.j_agent[jid]):
			_fail("only the agent can file proof")
		if _now() > int(self.j_deadline[jid]):
			_fail("the deadline has passed")
		used = int(self.j_attempts.get(jid, u32(0)))
		if used >= MAX_ATTEMPTS:
			_fail(f"all {MAX_ATTEMPTS} filings are used")
		tx = tx_hash.strip().lower()
		if TX_RE.match(tx) is None:
			_fail("give the 0x transaction hash of the LI.FI transfer")
		if self.used_tx.get(tx, "") not in ("", str(int(jid))):
			_fail("this transfer was already used for another job")
		terms = json.loads(self.j_terms[jid])
		nb = int(self.j_opened[jid])

		def leader_fn():
			return judge(tx, terms, nb)

		def validator_fn(res) -> bool:
			if not isinstance(res, gl.vm.Return):
				return _agree_on_error(res, leader_fn)
			lead = res.calldata
			if not isinstance(lead, dict) or lead.get("decision") not in (PAY, REJECT):
				return False
			mine = leader_fn()
			# same transfer facts, and the same verdict on every single term
			return same_facts(mine["record"], lead.get("record", {})) and mine["statuses"] == lead.get("statuses")

		out = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)
		# the payout is recomputed here by plain code from the agreed record and verdicts
		decision = decide(out["record"], out["statuses"], nb)
		self.j_attempts[jid] = u32(used + 1)
		ruling = {"tx": tx, "record": out["record"], "statuses": out["statuses"], "reasons": out.get("reasons", []),
			"decision": decision, "note": out.get("note", ""), "at": _now()}
		self.j_last[jid] = json.dumps(ruling)
		if decision == PAY:
			self.j_status[jid] = PAID
			self.used_tx[tx] = str(int(jid))
			_Payee(Address(str(self.j_agent[jid]))).emit_transfer(value=self.j_fee[jid])
		ruling["filings_left"] = MAX_ATTEMPTS - used - 1
		ruling["status"] = str(self.j_status[jid])
		return json.dumps(ruling)

	@gl.public.write
	def reclaim(self, job_id: int) -> str:
		jid = self._jid(job_id)
		if gl.message.sender_address.as_hex.lower() != str(self.j_client[jid]):
			_fail("only the client can reclaim")
		if str(self.j_status[jid]) != OPEN:
			_fail("this job is closed")
		used = int(self.j_attempts.get(jid, u32(0)))
		if _now() <= int(self.j_deadline[jid]) and used < MAX_ATTEMPTS:
			_fail("reclaim opens after the deadline or after three rejected filings")
		self.j_status[jid] = REFUNDED
		_Payee(Address(str(self.j_client[jid]))).emit_transfer(value=self.j_fee[jid])
		return json.dumps({"id": int(jid), "status": REFUNDED})

	@gl.public.view
	def get_job(self, job_id: int) -> str:
		return json.dumps(self._dict(int(self._jid(job_id))))

	@gl.public.view
	def get_jobs(self, offset: int, limit: int) -> str:
		n = int(self.job_count)
		start = max(0, offset)
		end = min(n, start + max(0, min(limit, 50)))
		return json.dumps({"total": n, "jobs": [self._dict(i) for i in range(start, end)]})

	@gl.public.view
	def get_verdict(self, job_id: int) -> str:
		"""For other contracts and agents: OPEN, PAID or REFUNDED plus the last decision."""
		k = self._jid(job_id)
		last = str(self.j_last[k])
		return json.dumps({"status": str(self.j_status[k]), "decision": json.loads(last)["decision"] if last else ""})

	def _dict(self, i):
		k = u256(i)
		last = str(self.j_last[k])
		return {"id": i, "title": str(self.j_title[k]), "terms": json.loads(self.j_terms[k]),
			"client": str(self.j_client[k]), "agent": str(self.j_agent[k]), "fee": str(int(self.j_fee[k])),
			"not_before": int(self.j_opened[k]), "deadline": int(self.j_deadline[k]),
			"status": str(self.j_status[k]), "filings": int(self.j_attempts.get(k, u32(0))),
			"last_ruling": json.loads(last) if last else None}
