"""
Offline tests for contracts/landed.py with a mocked GenLayer SDK.
LI.FI replies and LLM answers are mocked per role, so leader and validators
can be shown different evidence.  Run:  python3 tests/test_landed.py
"""

import importlib.util
import json
import pathlib
import sys
import traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mock_genlayer as mg  # noqa: E402

mg.install()
SRC = HERE.parent / "contracts" / "landed.py"
GEN = 10**18
CLIENT = mg.Address("0x" + "c" * 40)
AGENT = mg.Address("0x" + "a" * 40)
OTHER = mg.Address("0x" + "b" * 40)
DEST = "0x" + "d" * 40
TX = "0x" + "1" * 64
URL = "https://li.quest/v1/status?txHash=" + TX
TERMS = "Move USDC from Arbitrum to Base\nAt least 45 USDC must arrive on Base\nFunds must arrive at " + DEST


def load():
    spec = importlib.util.spec_from_file_location("landed", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.datetime = mg._FrozenDatetime
    return mod


def fresh():
    mg.world.__init__()
    mod = load()
    return mod, mod.Landed()


def as_(who, value=0):
    mg.world.sender, mg.world.value = who, value


def lifi(status="DONE", sub="COMPLETED", recv_amount="48900000", recv_token="USDC", to=DEST, sent_at=None, recv_chain=8453):
    sent_at = mg.world.now + 60 if sent_at is None else sent_at
    return json.dumps({
        "transactionId": "0xabc", "status": status, "substatus": sub, "tool": "across",
        "fromAddress": "0x" + "e" * 40, "toAddress": to, "lifiExplorerLink": "https://scan.li.fi/tx/" + TX,
        "sending": {"txHash": TX, "chainId": 42161, "amount": "50000000", "timestamp": sent_at,
                    "token": {"symbol": "USDC", "decimals": 6}, "gasPrice": "123", "amountUSD": "50.01"},
        "receiving": {"txHash": "0x" + "2" * 64, "chainId": recv_chain, "amount": recv_amount, "timestamp": sent_at + 40,
                      "token": {"symbol": recv_token, "decimals": 6}, "gasPrice": "999", "amountUSD": "48.9"},
    })


def llm(answers, role=None):
    mg.world.set_llm(lambda p: {"results": answers, "reasons": ["r"] * len(answers), "note": "ok"}, role)


def open_job(c, fee=GEN, days=7, not_before=0):
    as_(CLIENT, fee)
    return json.loads(c.open_job("Bridge 50 USDC to Base", TERMS, AGENT.as_hex, days, not_before))["id"]


def expect_error(fn, text):
    try:
        fn()
    except mg.UserError as e:
        assert text in str(e), f"wrong error: {e}"
        return
    raise AssertionError(f"expected error containing {text!r}")


def file(c, jid, tx=TX, who=AGENT):
    as_(who)
    return json.loads(c.file_proof(jid, tx))


# ---------------------------------------------------------------- pure helpers

def test_header_and_size():
    lines = SRC.read_text().splitlines()
    assert lines[0].startswith('# { "Depends": "py-genlayer:')
    json.loads(lines[0][1:])
    assert len(SRC.read_bytes()) < 16_000


def test_units_is_exact():
    mod = load()
    assert mod.units("48900000", 6) == "48.9"
    assert mod.units("1000000000000000000", 18) == "1"
    assert mod.units("123", 0) == "123"
    assert mod.units("1", 6) == "0.000001"
    assert mod.units("x", 6) == ""


def test_record_keeps_only_stable_facts():
    mod, _ = fresh()
    rec = mod.record_from(json.loads(lifi()))
    flat = json.dumps(rec)
    for noisy in ("gasPrice", "amountUSD", "lifiExplorerLink", "transactionId"):
        assert noisy not in flat
    assert rec["received"]["amount"] == "48.9" and rec["received"]["chain"] == "Base" and rec["sent"]["chain"] == "Arbitrum"


# ---------------------------------------------------------------- opening jobs

def test_open_job_rules():
    mod, c = fresh()
    as_(CLIENT, 0)
    expect_error(lambda: c.open_job("t", TERMS, AGENT.as_hex, 7, 0), "fee")
    as_(CLIENT, GEN)
    expect_error(lambda: c.open_job("t", "", AGENT.as_hex, 7, 0), "terms")
    expect_error(lambda: c.open_job("t", "short", AGENT.as_hex, 7, 0), "characters")
    expect_error(lambda: c.open_job("t", TERMS, "nope", 7, 0), "0x address")
    expect_error(lambda: c.open_job("t", TERMS, CLIENT.as_hex, 7, 0), "different")
    expect_error(lambda: c.open_job("t", TERMS, AGENT.as_hex, 99, 0), "deadline")
    expect_error(lambda: c.open_job("t", TERMS, AGENT.as_hex, 7, mg.world.now + 10), "future")
    jid = open_job(c)
    j = json.loads(c.get_job(jid))
    assert j["status"] == "OPEN" and len(j["terms"]) == 3 and j["not_before"] == mg.world.now


# ---------------------------------------------------------------- rulings

def test_delivered_transfer_pays_agent():
    mod, c = fresh()
    jid = open_job(c)
    mg.world.set_web(URL, lifi())
    llm(["MET", "MET", "MET"])
    mg.world.transfers.clear()
    r = file(c, jid)
    assert r["decision"] == "PAY" and r["status"] == "PAID"
    assert mg.world.transfers == [(AGENT.as_hex.lower(), GEN)]
    assert json.loads(c.get_verdict(jid)) == {"status": "PAID", "decision": "PAY"}
    expect_error(lambda: file(c, jid), "closed")


def test_one_unmet_term_rejects():
    mod, c = fresh()
    jid = open_job(c)
    mg.world.set_web(URL, lifi(recv_amount="40000000"))
    llm(["MET", "NOT_MET", "MET"])
    r = file(c, jid)
    assert r["decision"] == "REJECT" and r["status"] == "OPEN" and r["filings_left"] == 2 and mg.world.transfers == []


def test_pending_or_refunded_never_pays_even_if_llm_says_met():
    for status, sub in (("PENDING", "WAIT_DESTINATION_TRANSACTION"), ("FAILED", "SLIPPAGE_EXCEEDED"), ("NOT_FOUND", "")):
        mod, c = fresh()
        jid = open_job(c)
        mg.world.set_web(URL, lifi(status=status, sub=sub))
        llm(["MET", "MET", "MET"])
        r = file(c, jid)
        assert r["decision"] == "REJECT" and "nothing has landed" in r["note"], status
        assert not any(call[0] == "prompt" for call in mg.world.nondet_calls), "LLM must not be asked"
    mod, c = fresh()
    jid = open_job(c)
    mg.world.set_web(URL, lifi(sub="REFUNDED", recv_chain=42161))
    llm(["MET", "MET", "MET"])        # even a careless model cannot pay out a refunded transfer
    r = file(c, jid)
    assert r["decision"] == "REJECT" and "refunded" in r["note"] and mg.world.transfers == []


def test_old_transfer_cannot_be_reused_for_new_job():
    mod, c = fresh()
    jid = open_job(c)
    mg.world.set_web(URL, lifi(sent_at=mg.world.now - 3600))
    llm(["MET", "MET", "MET"])
    r = file(c, jid)
    assert r["decision"] == "REJECT" and "before the job" in r["note"] and mg.world.transfers == []


def test_not_before_lets_client_accept_an_earlier_transfer():
    mod, c = fresh()
    jid = open_job(c, not_before=mg.world.now - 7200)
    mg.world.set_web(URL, lifi(sent_at=mg.world.now - 3600))
    llm(["MET", "MET", "MET"])
    assert file(c, jid)["decision"] == "PAY"


def test_same_transfer_cannot_pay_two_jobs():
    mod, c = fresh()
    a, b = open_job(c), open_job(c)
    mg.world.set_web(URL, lifi())
    llm(["MET", "MET", "MET"])
    assert file(c, a)["decision"] == "PAY"
    expect_error(lambda: file(c, b), "already used")


def test_only_agent_files_and_hash_format():
    mod, c = fresh()
    jid = open_job(c)
    expect_error(lambda: file(c, jid, who=OTHER), "only the agent")
    expect_error(lambda: file(c, jid, tx="0x123"), "transaction hash")


def test_validators_bind_every_term_verdict():
    """Leader says all MET, validators say term 2 is NOT_MET: same PAY/REJECT? no, and even
    a matching final decision is not enough; every term must match, so consensus fails."""
    mod, c = fresh()
    jid = open_job(c)
    mg.world.set_web(URL, lifi())
    llm(["MET", "MET", "MET"], role="leader")
    llm(["MET", "NOT_MET", "MET"], role="validator")
    try:
        file(c, jid)
        raise AssertionError("expected consensus failure")
    except mg.ConsensusFailure:
        pass
    assert mg.world.transfers == []


def test_validators_reject_a_leader_reading_a_different_transfer():
    mod, c = fresh()
    jid = open_job(c)
    mg.world.set_web(URL, lifi(recv_amount="49900000"), role="leader")   # leader saw a fake bigger amount
    mg.world.set_web(URL, lifi(recv_amount="40000000"), role="validator")
    llm(["MET", "MET", "MET"])
    try:
        file(c, jid)
        raise AssertionError("expected consensus failure")
    except mg.ConsensusFailure:
        pass


def test_lifi_down_is_transient_and_costs_no_filing():
    mod, c = fresh()
    jid = open_job(c)
    mg.world.set_web(URL, 503)
    expect_error(lambda: file(c, jid), "HTTP 503")
    assert json.loads(c.get_job(jid))["filings"] == 0


def test_reclaim_after_three_rejections_or_deadline():
    mod, c = fresh()
    jid = open_job(c)
    as_(CLIENT)
    expect_error(lambda: c.reclaim(jid), "reclaim opens")
    mg.world.set_web(URL, lifi(recv_amount="1000000"))
    llm(["MET", "NOT_MET", "MET"])
    for _ in range(3):
        file(c, jid)
    expect_error(lambda: file(c, jid), "filings are used")
    as_(OTHER)
    expect_error(lambda: c.reclaim(jid), "only the client")
    as_(CLIENT)
    mg.world.transfers.clear()
    assert json.loads(c.reclaim(jid))["status"] == "REFUNDED"
    assert mg.world.transfers == [(CLIENT.as_hex.lower(), GEN)]
    mod, c = fresh()
    jid = open_job(c, days=1)
    mg.world.now += 2 * 86400
    as_(CLIENT)
    assert json.loads(c.reclaim(jid))["status"] == "REFUNDED"


def test_bad_llm_shape_is_an_error_not_a_payout():
    mod, c = fresh()
    jid = open_job(c)
    mg.world.set_web(URL, lifi())
    mg.world.set_llm(lambda p: {"results": ["MET"]})
    try:                      # every validator hits the same LLM error, so they agree it failed
        file(c, jid)
        raise AssertionError("expected an error")
    except (mg.UserError, mg.ConsensusFailure):
        pass
    assert mg.world.transfers == [] and json.loads(c.get_job(jid))["filings"] == 0


def test_prompt_contains_record_not_raw_reply():
    mod, c = fresh()
    jid = open_job(c)
    mg.world.set_web(URL, lifi())
    seen = {}
    mg.world.set_llm(lambda p: seen.setdefault("p", p) and {"results": ["MET"] * 3, "reasons": [], "note": ""})
    file(c, jid)
    assert '"amount": "48.9"' in seen["p"] and "gasPrice" not in seen["p"]


def test_list_view():
    mod, c = fresh()
    open_job(c)
    open_job(c)
    data = json.loads(c.get_jobs(0, 10))
    assert data["total"] == 2 and [j["id"] for j in data["jobs"]] == [0, 1]


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
