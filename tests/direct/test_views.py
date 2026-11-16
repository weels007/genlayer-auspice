"""The views: what a reader can ask the contract without writing to it."""

import json

import pytest

QUESTION = "Did the project publish a signed release this month?"
URL = "https://source.example.com/project"
CLOSE = 86400


def _deployed(direct_deploy):
    return direct_deploy("contracts/Auspice.py")


def test_a_fresh_contract_is_empty(direct_deploy):
    contract = _deployed(direct_deploy)

    assert contract.get_market_ids()["ids"] == []
    assert contract.get_markets() == {}
    assert contract.get_ledger() == {"total_in": 0, "total_out": 0, "locked_total": 0}
    assert contract.get_summary() == {
        "markets": 0,
        "resolved": 0,
        "void": 0,
        "yes": 0,
        "no": 0,
        "claims": 0,
    }
    assert contract.get_balance() >= 0


def test_the_rules_report_the_bounds(direct_deploy):
    contract = _deployed(direct_deploy)
    rules = contract.get_rules()

    assert rules["min_stake"] == 10
    assert rules["max_position"] == 10000
    assert rules["max_pool"] == 100000
    assert rules["max_markets"] == 500
    assert rules["min_question_chars"] == 10
    assert rules["max_question_chars"] == 300
    assert rules["min_close_seconds"] == 60
    assert rules["max_close_seconds"] == 30 * 86400
    assert rules["min_quote_chars"] == 20
    assert rules["max_resolution_attempts"] == 3
    assert rules["sides"] == ["YES", "NO"]


def test_an_unknown_market_reverts(direct_deploy):
    contract = _deployed(direct_deploy)

    with pytest.raises(Exception):
        contract.get_market("au-99")


def test_a_stranger_holds_nothing(direct_vm, direct_deploy, direct_alice, direct_bob):
    direct_vm.warp("2025-01-01T00:00:00Z")
    contract = _deployed(direct_deploy)

    direct_vm.sender = direct_alice
    market_id = contract.create_market(QUESTION, URL, "", CLOSE, "ref-1")

    assert contract.get_position(market_id, direct_bob) == {"side": "", "stake": 0}
    assert contract.get_payout_preview(market_id, direct_bob) == {
        "side": "",
        "stake": 0,
        "payout": 0,
    }
    assert contract.get_credit(direct_bob) == 0


def test_the_preview_matches_the_payout(direct_vm, direct_deploy, direct_alice, direct_bob):
    import json

    page = (
        "Auspice is a prediction market for GenLayer. The repository published its "
        "first signed release on 3 March, and the notes are in the release section."
    )

    direct_vm.warp("2025-01-01T00:00:00Z")
    contract = _deployed(direct_deploy)

    direct_vm.sender = direct_alice
    market_id = contract.create_market(QUESTION, URL, "", CLOSE, "ref-1")

    direct_vm.value = 100
    contract.buy(market_id, "YES", "yes-1")
    direct_vm.value = 0

    direct_vm.sender = direct_bob
    direct_vm.value = 300
    contract.buy(market_id, "NO", "no-1")
    direct_vm.value = 0

    direct_vm.warp("2025-01-03T00:00:00Z")
    direct_vm.mock_web(r".*source\.example\.com.*", {"status": 200, "body": page})
    direct_vm.mock_llm(
        r".*\[\[AUSPICE_RESOLUTION\]\].*",
        json.dumps(
            {
                "outcome": "YES",
                "quote": "The repository published its first signed release on 3 March",
                "reason": "the page says so",
            }
        ),
    )

    direct_vm.sender = direct_alice
    contract.resolve(market_id)

    preview = contract.get_payout_preview(market_id, direct_alice)
    assert preview["side"] == "YES"
    assert preview["stake"] == 100
    assert preview["payout"] == 400

    contract.claim(market_id)
    assert contract.get_ledger()["total_out"] == 400
