"""Auspice on studionet: a market opened, two sides backed, resolved, and paid.

The contract has no owner and no reset, so every assertion is a delta — a count,
a pool, a ledger total compared before and after — and the suite tolerates
markets left behind by an earlier run.

Two accounts are needed: the two sides of a market are the whole point, and one
wallet cannot back both.

A live committee cannot be ordered to answer, so the test asserts the law for
whichever reading appears and reports it: a settled market pays the winning side
the pool, and a market nobody could answer is voided and every stake returned.

    pytest tests/integration -v -s
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

WINDOW = 60
STAKE = 100
URL = "https://raw.githubusercontent.com/weels007/genlayer-vow/main/README.md"
# A page whose whole text folds to nineteen letters, one short of what a quote
# needs: no reading taken from it can ground, so a market filed against it can
# never be settled and has to be voided.
UNGROUNDABLE_URL = (
    "https://raw.githubusercontent.com/weels007/genlayer-auspice/main/"
    "testdata/ungroundable.txt"
)
QUESTION = (
    "Does the page say the project is an Intelligent Contract for GenLayer that "
    "reads evidence from the web?"
)

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
    print(f"\n[auspice] contract {payload['address']}")
    return payload["address"]


@pytest.fixture(scope="module")
def account():
    return load_account("tester1")


@pytest.fixture(scope="module")
def second(account):
    """A second funded wallet. Without one there is no other side to back."""
    try:
        other = load_account("tester2")
    except SystemExit:
        pytest.skip("no second wallet is configured; a market needs two sides")
    if _hex(other.address) == _hex(account.address):
        pytest.skip("the second wallet is the deployer; a market needs two sides")
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


def _ledger(client, contract):
    return _read(client, contract, "get_ledger")


def _balance(client, contract):
    return int(_read(client, contract, "get_balance"))


def _wait_balance_drop(client, contract, before, amount, attempts=30, step=5):
    """Wait until the contract's real balance has fallen by exactly `amount`.

    An outgoing transfer is an external message that finalises after the call
    that emitted it, so the balance read straight after a claim still shows the
    money inside the contract. Polling is the only way to see it leave.
    """
    last = _balance(client, contract)
    for _ in range(attempts):
        if before - last == amount:
            return last
        time.sleep(step)
        last = _balance(client, contract)
    return last


def _invariant_holds(client, contract) -> bool:
    ledger = _ledger(client, contract)
    return int(ledger["total_in"]) == int(ledger["total_out"]) + int(ledger["locked_total"])


def _create_market(client, account, contract, ref, url=URL):
    before = len(_market_ids(client, contract))
    _expect_ok(
        client,
        account,
        contract,
        "create_market",
        [QUESTION, url, "", int(WINDOW), f"{ref}-{RUN}"],
        verify=lambda: len(_market_ids(client, contract)) > before,
    )
    ids = _market_ids(client, contract)
    assert len(ids) > before, "the market was not created"
    return ids[-1]


# ---------------------------------------------------------------------------
# the market is live
# ---------------------------------------------------------------------------


def test_01_the_market_is_live(client, contract):
    rules = _read(client, contract, "get_rules")
    assert int(rules["min_stake"]) == 10
    assert int(rules["max_resolution_attempts"]) == 3
    assert list(rules["sides"]) == ["YES", "NO"]
    assert _invariant_holds(client, contract)


def test_02_a_market_can_be_opened(client, account, contract):
    market_id = _create_market(client, account, contract, "live-1")

    record = _market(client, contract, market_id)
    assert record["status"] == "OPEN"
    assert record["outcome"] == ""
    assert record["pool"] == 0
    assert record["source_url"] == URL
    print(f"  opened {market_id} with a {record['close_ts'] - record['created_ts']}s window")


def test_03_two_sides_back_one_pool(client, account, second, contract):
    market_id = _create_market(client, account, contract, "live-2")

    _expect_ok(
        client,
        account,
        contract,
        "buy",
        [market_id, "YES", f"yes-{RUN}"],
        value=STAKE,
    )
    _expect_ok(
        client,
        second,
        contract,
        "buy",
        [market_id, "NO", f"no-{RUN}"],
        value=STAKE,
    )

    record = _market(client, contract, market_id)
    assert record["yes_pool"] == STAKE
    assert record["no_pool"] == STAKE
    assert record["pool"] == 2 * STAKE
    assert _invariant_holds(client, contract)


def test_04_the_committee_reads_the_page_and_the_winner_is_paid(
    client, account, second, contract
):
    market_id = _create_market(client, account, contract, "live-3")

    _expect_ok(client, account, contract, "buy", [market_id, "YES", f"yes-{RUN}"], value=STAKE)
    _expect_ok(client, second, contract, "buy", [market_id, "NO", f"no-{RUN}"], value=STAKE)

    # The market can only be read after its close, and the close is on the
    # contract's clock, so wait past it rather than guessing.
    time.sleep(WINDOW + 5)

    for _ in range(3):
        if _market(client, contract, market_id)["status"] != "OPEN":
            break
        _expect_ok(
            client,
            account,
            contract,
            "resolve",
            [market_id],
            verify=lambda: int(_market(client, contract, market_id)["attempts"]) > 0,
        )

    record = _market(client, contract, market_id)
    print(f"  the committee returned {record['status']!r} / {record['outcome']!r}")

    pool = int(record["pool"])

    if record["status"] == "OPEN":
        # No reading could be grounded: spend the attempts and void it.
        for _ in range(3):
            _expect_ok(client, account, contract, "resolve", [market_id])
        _expect_ok(client, account, contract, "void_market", [market_id])
        record = _market(client, contract, market_id)
        assert record["status"] == "VOID", record

    if record["status"] == "RESOLVED":
        winner = account if record["outcome"] == "YES" else second
        preview = _read(
            client, contract, "get_payout_preview", [market_id, winner.address]
        )
        assert int(preview["payout"]) == pool, preview

    out_before = int(_ledger(client, contract)["total_out"])
    locked_before = int(_ledger(client, contract)["locked_total"])
    balance_before = _balance(client, contract)

    _expect_ok(client, account, contract, "claim", [market_id])
    _expect_ok(client, second, contract, "claim", [market_id])

    ledger = _ledger(client, contract)
    assert int(ledger["total_out"]) == out_before + pool
    assert int(ledger["locked_total"]) == locked_before - pool
    assert _invariant_holds(client, contract)

    landed = _wait_balance_drop(client, contract, balance_before, pool)
    assert balance_before - landed == pool, (
        "the ledger says the pool was paid, but it never left the contract: "
        f"{balance_before} -> {landed}"
    )

    if record["status"] == "RESOLVED":
        winner = account if record["outcome"] == "YES" else second
        print(f"  the {record['outcome']} side took the {pool} GEN pool ({_hex(winner.address)[:8]})")
    else:
        print("  the market was voided and both stakes came back")


def test_05_an_undecidable_market_is_voided(client, account, second, contract):
    """A page no quote can be grounded in: three readings, then both stakes back."""
    market_id = _create_market(client, account, contract, "live-void", url=UNGROUNDABLE_URL)

    _expect_ok(client, account, contract, "buy", [market_id, "YES", f"yes-{RUN}"], value=STAKE)
    _expect_ok(client, second, contract, "buy", [market_id, "NO", f"no-{RUN}"], value=STAKE)

    time.sleep(WINDOW + 5)

    for _ in range(3):
        if int(_market(client, contract, market_id)["attempts"]) >= 3:
            break
        _expect_ok(
            client,
            account,
            contract,
            "resolve",
            [market_id],
            verify=lambda: int(_market(client, contract, market_id)["attempts"]) > 0,
        )

    record = _market(client, contract, market_id)
    assert record["status"] == "OPEN", record
    assert int(record["attempts"]) == 3, record
    assert record["outcome"] == "", record

    # Anyone may void it, so the account that did not create it does.
    _expect_ok(
        client,
        second,
        contract,
        "void_market",
        [market_id],
        verify=lambda: _market(client, contract, market_id)["status"] == "VOID",
    )
    assert _market(client, contract, market_id)["status"] == "VOID"

    out_before = int(_ledger(client, contract)["total_out"])
    locked_before = int(_ledger(client, contract)["locked_total"])
    balance_before = _balance(client, contract)

    _expect_ok(client, account, contract, "claim", [market_id])
    _expect_ok(client, second, contract, "claim", [market_id])

    ledger = _ledger(client, contract)
    assert int(ledger["total_out"]) == out_before + 2 * STAKE
    assert int(ledger["locked_total"]) == locked_before - 2 * STAKE
    assert _invariant_holds(client, contract)

    refunded = _wait_balance_drop(client, contract, balance_before, 2 * STAKE)
    assert balance_before - refunded == 2 * STAKE, (
        "the ledger says both stakes were refunded, but the money never left "
        f"the contract: {balance_before} -> {refunded}"
    )

    print("  three readings settled nothing; the market was voided and refunded")
