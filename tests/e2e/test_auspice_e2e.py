"""Auspice end to end on the deployed market: the paths that need no model.

A market opened, a stake below the floor, a wrong side, a duplicate reference, a
claim before settlement, a void before it is earned, a stranger reaching for
somebody else's credit, and the views agreeing with the ledger.

A market reference is caller-scoped and permanent, so every reference carries the
run's start time; every assertion is a delta.

    pytest tests/e2e -v -s
"""

import json
import re
import sys
import time
from pathlib import Path

import pytest
from genlayer_py import create_client, studionet

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from wallet import load_account  # noqa: E402

DEPLOYMENT_PATH = ROOT / "deployment.json"
ADDRESS_VALUE = re.compile(r"0x[0-9a-fA-F]{40}")

POLL_INTERVAL = 10_000
POLL_RETRIES = 60
RETRY_PAUSE = 20

MIN_STAKE = 10
CLOSE = 3600
QUESTION = "Did the project publish a signed release this month?"
URL = "https://raw.githubusercontent.com/weels007/genlayer-vow/main/README.md"

RUN = str(time.time_ns())


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _deployment() -> dict:
    if not DEPLOYMENT_PATH.exists():
        pytest.skip("no deployment.json yet; run the Deploy Contract workflow first")
    payload = json.loads(DEPLOYMENT_PATH.read_text(encoding="utf-8"))
    address = str(payload.get("address", "")).strip()
    if not ADDRESS_VALUE.fullmatch(address):
        pytest.skip(f"deployment.json has no usable address: {address!r}")
    return payload


@pytest.fixture(scope="module")
def contract() -> str:
    payload = _deployment()
    print(f"\n[auspice-e2e] contract {payload['address']}")
    return payload["address"]


@pytest.fixture(scope="module")
def account():
    return load_account("tester1")


@pytest.fixture(scope="module")
def second(account):
    """A second funded wallet, or None when only one is configured."""
    try:
        other = load_account("tester2")
    except SystemExit:
        return None
    if _hex(other.address) == _hex(account.address):
        return None
    return other


@pytest.fixture(scope="module")
def client(account):
    return create_client(studionet, account=account)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _hex(value) -> str:
    return str(value).lower().replace("0x", "")


def _succeeded(result) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("status_name") not in ("ACCEPTED", "FINALIZED"):
        return False
    leader = (result.get("consensus_data") or {}).get("leader_receipt")
    if isinstance(leader, list):
        leader = leader[0] if leader else None
    if isinstance(leader, dict):
        return leader.get("execution_result") == "SUCCESS"
    return str(result.get("tx_execution_result_name", "")) == "FINISHED_WITH_RETURN"


def _definite_rollback(result) -> bool:
    if not isinstance(result, dict):
        return False
    leader = (result.get("consensus_data") or {}).get("leader_receipt")
    if isinstance(leader, list):
        leader = leader[0] if leader else None
    return isinstance(leader, dict) and leader.get("execution_result") == "ERROR"


def _attempt(client, account, contract, method, args=None, value=0):
    try:
        tx_hash = client.write_contract(
            contract, method, args=args or [], value=value, account=account
        )
        return client.wait_for_transaction_receipt(
            tx_hash, retries=POLL_RETRIES, interval=POLL_INTERVAL
        )
    except Exception as error:
        return error


def _expect_ok(client, account, contract, method, args=None, value=0, verify=None):
    result = None
    for attempt in range(3):
        result = _attempt(client, account, contract, method, args, value)
        if _succeeded(result):
            return result
        if verify is not None and verify():
            return result
        if _definite_rollback(result):
            raise AssertionError(f"{method} was refused: {result}")
        if attempt < 2:
            print(f"  {method} did not reach consensus, retrying")
            time.sleep(RETRY_PAUSE)
    raise AssertionError(f"{method} did not reach consensus: {result}")


def _expect_denied(client, account, contract, method, args=None, value=0):
    result = _attempt(client, account, contract, method, args, value)
    assert not _succeeded(result), f"{method} should have been refused: {result}"
    return result


def _read(client, contract, method, args=None):
    """Read a view, retrying a provider hiccup (a 502 is not an answer)."""
    last = None
    for attempt in range(3):
        try:
            return client.read_contract(contract, method, args=args or [])
        except Exception as error:
            last = error
            if attempt < 2:
                print(f"  {method} read failed, retrying: {error}")
                time.sleep(RETRY_PAUSE)
    raise last


def _market(client, contract, market_id):
    return _read(client, contract, "get_market", [market_id])


def _market_ids(client, contract):
    return list(_read(client, contract, "get_market_ids").get("ids", []))


def _position(client, contract, market_id, who):
    return _read(client, contract, "get_position", [market_id, who.address])


def _credit(client, contract, who):
    return int(_read(client, contract, "get_credit", [who.address]))


def _ledger(client, contract):
    return _read(client, contract, "get_ledger")


def _balance(client, contract):
    return int(_read(client, contract, "get_balance"))


def _invariant_holds(client, contract) -> bool:
    ledger = _ledger(client, contract)
    return int(ledger["total_in"]) == int(ledger["total_out"]) + int(ledger["locked_total"])


def _create_market(client, account, contract, ref, question=QUESTION, url=URL):
    before = len(_market_ids(client, contract))
    _expect_ok(
        client,
        account,
        contract,
        "create_market",
        [question, url, "", int(CLOSE), f"{ref}-{RUN}"],
        verify=lambda: len(_market_ids(client, contract)) > before,
    )
    ids = _market_ids(client, contract)
    assert len(ids) > before, "the market was not created"
    return ids[-1]


def _buy(client, account, contract, market_id, side, amount, ref):
    credit_before = _credit(client, contract, account)
    _expect_ok(
        client,
        account,
        contract,
        "buy",
        [market_id, side, f"{ref}-{RUN}"],
        value=amount,
        verify=lambda: _credit(client, contract, account) > credit_before
        or int(_market(client, contract, market_id)["pool"]) > 0,
    )
    return _credit(client, contract, account) - credit_before


# ---------------------------------------------------------------------------
# the deployed market
# ---------------------------------------------------------------------------


def test_01_the_market_is_live(client, contract):
    rules = _read(client, contract, "get_rules")
    assert int(rules["min_stake"]) == MIN_STAKE
    assert int(rules["max_resolution_attempts"]) == 3
    assert list(rules["sides"]) == ["YES", "NO"]
    assert _invariant_holds(client, contract)


def test_02_a_market_is_opened(client, account, contract):
    market_id = _create_market(client, account, contract, "e2e-open")
    record = _market(client, contract, market_id)

    assert record["status"] == "OPEN"
    assert record["pool"] == 0
    assert record["outcome"] == ""
    assert record["question"] == QUESTION
    assert _invariant_holds(client, contract)


def test_03_a_stake_below_the_floor_is_a_credit(client, account, contract):
    market_id = _create_market(client, account, contract, "e2e-small")

    refused = _buy(client, account, contract, market_id, "YES", MIN_STAKE - 1, "small")

    assert refused == MIN_STAKE - 1
    assert _position(client, contract, market_id, account) == {"side": "", "stake": 0}
    assert _market(client, contract, market_id)["pool"] == 0
    assert _invariant_holds(client, contract)

    _expect_ok(
        client,
        account,
        contract,
        "withdraw_credit",
        verify=lambda: _credit(client, contract, account) == 0,
    )
    assert _invariant_holds(client, contract)


def test_04_a_wrong_side_is_a_credit(client, account, contract):
    market_id = _create_market(client, account, contract, "e2e-side")

    refused = _buy(client, account, contract, market_id, "MAYBE", 100, "side")

    assert refused == 100
    assert _market(client, contract, market_id)["pool"] == 0
    _expect_ok(client, account, contract, "withdraw_credit")


def test_05_a_duplicate_reference_is_a_credit(client, account, contract):
    market_id = _create_market(client, account, contract, "e2e-dup")
    ref = f"same-{RUN}"

    _expect_ok(client, account, contract, "buy", [market_id, "YES", ref], value=100)
    credit_before = _credit(client, contract, account)

    _expect_ok(
        client,
        account,
        contract,
        "buy",
        [market_id, "YES", ref],
        value=100,
        verify=lambda: _credit(client, contract, account) > credit_before,
    )

    assert _credit(client, contract, account) == credit_before + 100
    assert _position(client, contract, market_id, account)["stake"] == 100
    _expect_ok(client, account, contract, "withdraw_credit")


def test_06_a_position_cannot_be_claimed_before_settlement(client, account, contract):
    market_id = _create_market(client, account, contract, "e2e-claim")

    _expect_ok(client, account, contract, "buy", [market_id, "YES", f"yes-{RUN}"], value=100)

    touched = _attempt(client, account, contract, "claim", [market_id])
    assert not _succeeded(touched), "a position was paid before the market settled"
    assert _market(client, contract, market_id)["status"] == "OPEN"
    assert _position(client, contract, market_id, account)["stake"] == 100


def test_07_void_is_refused_before_it_is_earned(client, account, contract):
    market_id = _create_market(client, account, contract, "e2e-void")

    touched = _attempt(client, account, contract, "void_market", [market_id])
    assert not _succeeded(touched), "a market was voided before any reading was attempted"
    assert _market(client, contract, market_id)["status"] == "OPEN"
    assert _invariant_holds(client, contract)


def test_08_a_stranger_cannot_take_somebody_elses_credit(
    client, account, second, contract
):
    if second is None:
        pytest.skip("only one wallet is configured, so a stranger would be the owner")

    if _credit(client, contract, second) > 0:
        _expect_ok(client, second, contract, "withdraw_credit")

    market_id = _create_market(client, account, contract, "e2e-credit")
    refused = _buy(client, account, contract, market_id, "YES", MIN_STAKE - 1, "small")
    assert refused == MIN_STAKE - 1

    touched = _attempt(client, second, contract, "withdraw_credit")
    assert not _succeeded(touched), "a stranger withdrew somebody else's credit"
    assert _credit(client, contract, account) == refused

    _expect_ok(client, account, contract, "withdraw_credit")


def test_09_views_agree_with_the_ledger(client, contract):
    summary = _read(client, contract, "get_summary")
    markets = _read(client, contract, "get_markets")

    assert int(summary["markets"]) == len(markets)
    assert int(summary["markets"]) == len(_market_ids(client, contract))
    assert _balance(client, contract) >= 0
    assert _invariant_holds(client, contract)
