"""Opening a market: what may be asked, and what may never be stored.

A market freezes its question, its page and its close time at creation. Every
guard here protects that freeze: nobody can re-aim a market at a friendlier page
after people have staked against it, and a replay of the same reference cannot
mint a second one.
"""

import pytest

QUESTION = "Did the project publish a signed release this month?"
URL = "https://source.example.com/project"
CLOSE = 86400


def _deployed(direct_deploy):
    return direct_deploy("contracts/Auspice.py")


def _hex(value) -> str:
    return str(value).lower().replace("0x", "")


def test_create_market_records_the_question(direct_vm, direct_deploy, direct_alice):
    direct_vm.warp("2025-01-01T00:00:00Z")
    contract = _deployed(direct_deploy)
    direct_vm.sender = direct_alice

    market_id = contract.create_market(QUESTION, URL, "", CLOSE, "ref-1")
    assert market_id == "au-1"

    record = contract.get_market(market_id)
    assert record["market_id"] == "au-1"
    assert record["question"] == QUESTION
    assert record["source_url"] == URL
    assert record["criteria"] == ""
    assert record["status"] == "OPEN"
    assert record["outcome"] == ""
    assert record["pool"] == 0
    assert record["yes_pool"] == 0
    assert record["no_pool"] == 0
    assert record["attempts"] == 0
    assert record["quote"] == ""
    assert record["close_ts"] - record["created_ts"] == CLOSE
    assert _hex(record["creator"]) == _hex(direct_alice)

    assert contract.get_market_ids()["ids"] == ["au-1"]
    assert contract.get_summary()["markets"] == 1
    assert contract.get_ledger() == {"total_in": 0, "total_out": 0, "locked_total": 0}


def test_market_ids_increment(direct_vm, direct_deploy, direct_alice):
    direct_vm.warp("2025-01-01T00:00:00Z")
    contract = _deployed(direct_deploy)
    direct_vm.sender = direct_alice

    first = contract.create_market(QUESTION, URL, "", CLOSE, "ref-a")
    second = contract.create_market(QUESTION, URL, "", CLOSE, "ref-b")

    assert first == "au-1"
    assert second == "au-2"
    assert contract.get_market_ids()["ids"] == ["au-1", "au-2"]
    assert len(contract.get_markets()) == 2


def test_a_short_question_is_rejected(direct_vm, direct_deploy, direct_alice):
    contract = _deployed(direct_deploy)
    direct_vm.sender = direct_alice

    with direct_vm.expect_revert("[EXPECTED]:question_too_short"):
        contract.create_market("soon", URL, "", CLOSE, "ref-1")


def test_a_missing_reference_is_rejected(direct_vm, direct_deploy, direct_alice):
    contract = _deployed(direct_deploy)
    direct_vm.sender = direct_alice

    with direct_vm.expect_revert("[EXPECTED]:missing_ref"):
        contract.create_market(QUESTION, URL, "", CLOSE, "   ")


def test_a_duplicate_reference_is_rejected(direct_vm, direct_deploy, direct_alice):
    direct_vm.warp("2025-01-01T00:00:00Z")
    contract = _deployed(direct_deploy)
    direct_vm.sender = direct_alice

    contract.create_market(QUESTION, URL, "", CLOSE, "same")

    with direct_vm.expect_revert("[EXPECTED]:duplicate_ref"):
        contract.create_market(QUESTION, URL, "", CLOSE, "same")


def test_references_are_scoped_per_creator(direct_vm, direct_deploy, direct_alice, direct_bob):
    direct_vm.warp("2025-01-01T00:00:00Z")
    contract = _deployed(direct_deploy)

    direct_vm.sender = direct_alice
    first = contract.create_market(QUESTION, URL, "", CLOSE, "shared")

    direct_vm.sender = direct_bob
    second = contract.create_market(QUESTION, URL, "", CLOSE, "shared")

    assert first == "au-1"
    assert second == "au-2"


@pytest.mark.parametrize(
    "url",
    [
        "http://source.example.com/x",
        "https://127.0.0.1/x",
        "https://127.1/x",
        "https://2130706433/x",
        "https://[::1]/x",
        "https://intranet/x",
        "https://source.local/x",
        "https://metadata.google.internal/x",
    ],
)
def test_private_or_insecure_pages_are_rejected(
    direct_vm, direct_deploy, direct_alice, url
):
    contract = _deployed(direct_deploy)
    direct_vm.sender = direct_alice

    with direct_vm.expect_revert("[EXPECTED]:invalid_source_url"):
        contract.create_market(QUESTION, url, "", CLOSE, "ref-1")


def test_close_must_be_within_the_supported_range(direct_vm, direct_deploy, direct_alice):
    contract = _deployed(direct_deploy)
    direct_vm.sender = direct_alice

    with direct_vm.expect_revert("[EXPECTED]:invalid_close"):
        contract.create_market(QUESTION, URL, "", 0, "ref-1")

    with direct_vm.expect_revert("[EXPECTED]:invalid_close"):
        contract.create_market(QUESTION, URL, "", 31 * 86400, "ref-2")


def test_a_long_question_is_truncated(direct_vm, direct_deploy, direct_alice):
    direct_vm.warp("2025-01-01T00:00:00Z")
    contract = _deployed(direct_deploy)
    direct_vm.sender = direct_alice

    market_id = contract.create_market("x" * 5000, URL, "", CLOSE, "ref-1")

    assert len(contract.get_market(market_id)["question"]) == 300
