"""The reading: what the page says, who gets paid, and what happens when it says
nothing.

Only a closed market reaches the model. An answer without a quote taken from the
page is not an answer — it books an attempt and moves no money — so an ungrounded
citation can never pay anybody. A market nobody backed correctly is void, not
confiscated: every stake goes back to the account that bought it.
"""

import json

PAGE = (
    "Auspice is a prediction market for GenLayer. The repository published its "
    "first signed release on 3 March, and the notes are in the release section."
)
QUOTE = "The repository published its first signed release on 3 March"
QUESTION = "Did the project publish a signed release this month?"
URL = "https://source.example.com/project"
CLOSE = 86400


def _mock_page(direct_vm, body=PAGE):
    direct_vm.mock_web(r".*source\.example\.com.*", {"status": 200, "body": body})


def _mock_reading(direct_vm, outcome="YES", quote=QUOTE, reason="the page says so"):
    direct_vm.mock_llm(
        r".*\[\[AUSPICE_RESOLUTION\]\].*",
        json.dumps({"outcome": outcome, "quote": quote, "reason": reason}),
    )


def _sold(
    direct_vm,
    direct_deploy,
    yes_account,
    no_account,
    yes_amount=100,
    no_amount=300,
):
    direct_vm.warp("2025-01-01T00:00:00Z")
    contract = direct_deploy("contracts/Auspice.py")

    direct_vm.sender = yes_account
    market_id = contract.create_market(QUESTION, URL, "", CLOSE, "ref-market")

    direct_vm.value = yes_amount
    contract.buy(market_id, "YES", "yes-1")
    direct_vm.value = 0

    direct_vm.sender = no_account
    direct_vm.value = no_amount
    contract.buy(market_id, "NO", "no-1")
    direct_vm.value = 0

    direct_vm.warp("2025-01-03T00:00:00Z")
    return contract, market_id


def test_the_page_settles_the_market(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract, market_id = _sold(direct_vm, direct_deploy, direct_alice, direct_bob)

    _mock_page(direct_vm)
    _mock_reading(direct_vm, "YES")

    direct_vm.sender = direct_alice
    assert contract.resolve(market_id) == "YES"

    record = contract.get_market(market_id)
    assert record["status"] == "RESOLVED"
    assert record["outcome"] == "YES"
    assert record["winning_pool"] == 100
    assert record["pool"] == 400
    assert record["attempts"] == 1
    assert record["quote"] == QUOTE
    assert record["page_digest"] != ""

    summary = contract.get_summary()
    assert summary["resolved"] == 1
    assert summary["yes"] == 1
    assert summary["no"] == 0

    ledger = contract.get_ledger()
    assert ledger["locked_total"] == 400
    assert ledger["total_out"] == 0


def test_the_winner_takes_the_whole_pool(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract, market_id = _sold(direct_vm, direct_deploy, direct_alice, direct_bob)

    _mock_page(direct_vm)
    _mock_reading(direct_vm, "YES")
    direct_vm.sender = direct_alice
    contract.resolve(market_id)

    contract.claim(market_id)

    assert contract.get_position(market_id, direct_alice) == {"side": "", "stake": 0}
    ledger = contract.get_ledger()
    assert ledger["total_out"] == 400
    assert ledger["locked_total"] == 0
    assert ledger["total_in"] == ledger["total_out"] + ledger["locked_total"]
    assert contract.get_summary()["claims"] == 1


def test_a_losing_position_claims_nothing(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract, market_id = _sold(direct_vm, direct_deploy, direct_alice, direct_bob)

    _mock_page(direct_vm)
    _mock_reading(direct_vm, "YES")
    direct_vm.sender = direct_alice
    contract.resolve(market_id)

    direct_vm.sender = direct_bob
    contract.claim(market_id)

    assert contract.get_position(market_id, direct_bob) == {"side": "", "stake": 0}
    ledger = contract.get_ledger()
    assert ledger["total_out"] == 0
    assert ledger["locked_total"] == 400


def test_the_last_winner_takes_the_rounding_dust(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner
):
    direct_vm.warp("2025-01-01T00:00:00Z")
    contract = direct_deploy("contracts/Auspice.py")

    direct_vm.sender = direct_alice
    market_id = contract.create_market(QUESTION, URL, "", CLOSE, "ref-market")

    direct_vm.value = 100
    contract.buy(market_id, "YES", "a")
    direct_vm.value = 0

    direct_vm.sender = direct_owner
    direct_vm.value = 200
    contract.buy(market_id, "YES", "b")
    direct_vm.value = 0

    direct_vm.sender = direct_bob
    direct_vm.value = 100
    contract.buy(market_id, "NO", "c")
    direct_vm.value = 0

    direct_vm.warp("2025-01-03T00:00:00Z")
    _mock_page(direct_vm)
    _mock_reading(direct_vm, "YES")

    direct_vm.sender = direct_alice
    contract.resolve(market_id)

    # 100 of 300 wins, from a pool of 400: 133, and the owner takes the rest.
    assert contract.get_payout_preview(market_id, direct_alice)["payout"] == 133
    contract.claim(market_id)

    direct_vm.sender = direct_owner
    assert contract.get_payout_preview(market_id, direct_owner)["payout"] == 267
    contract.claim(market_id)

    ledger = contract.get_ledger()
    assert ledger["total_out"] == 400
    assert ledger["locked_total"] == 0


def test_a_page_that_says_nothing_books_an_attempt(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract, market_id = _sold(direct_vm, direct_deploy, direct_alice, direct_bob)

    _mock_page(direct_vm)
    _mock_reading(direct_vm, "SILENT", quote="")

    direct_vm.sender = direct_alice
    assert contract.resolve(market_id) == ""

    record = contract.get_market(market_id)
    assert record["status"] == "OPEN"
    assert record["outcome"] == ""
    assert record["attempts"] == 1

    ledger = contract.get_ledger()
    assert ledger["locked_total"] == 400
    assert ledger["total_out"] == 0


def test_an_ungrounded_quote_is_not_an_outcome(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract, market_id = _sold(direct_vm, direct_deploy, direct_alice, direct_bob)

    _mock_page(direct_vm)
    _mock_reading(direct_vm, "YES", quote="a sentence that is not on the page at all")

    direct_vm.sender = direct_alice
    assert contract.resolve(market_id) == ""

    record = contract.get_market(market_id)
    assert record["status"] == "OPEN"
    assert record["outcome"] == ""
    assert record["quote"] == ""
    assert record["attempts"] == 1
    assert contract.get_ledger()["total_out"] == 0


def test_three_undecided_readings_can_be_voided(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner
):
    contract, market_id = _sold(direct_vm, direct_deploy, direct_alice, direct_bob)

    _mock_page(direct_vm)
    _mock_reading(direct_vm, "SILENT", quote="")

    for _ in range(3):
        direct_vm.sender = direct_alice
        assert contract.resolve(market_id) == ""

    assert contract.get_market(market_id)["attempts"] == 3

    with direct_vm.expect_revert("[EXPECTED]:too_many_attempts"):
        contract.resolve(market_id)

    direct_vm.sender = direct_owner
    contract.void_market(market_id)

    assert contract.get_market(market_id)["status"] == "VOID"
    assert contract.get_summary()["void"] == 1

    direct_vm.sender = direct_alice
    contract.claim(market_id)

    direct_vm.sender = direct_bob
    contract.claim(market_id)

    ledger = contract.get_ledger()
    assert ledger["total_out"] == 400
    assert ledger["locked_total"] == 0
    assert ledger["total_in"] == ledger["total_out"] + ledger["locked_total"]


def test_void_is_refused_before_the_attempts_are_spent(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract, market_id = _sold(direct_vm, direct_deploy, direct_alice, direct_bob)

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("[EXPECTED]:not_voidable"):
        contract.void_market(market_id)

    assert contract.get_market(market_id)["status"] == "OPEN"


def test_resolve_before_the_close_reverts(direct_vm, direct_deploy, direct_alice):
    direct_vm.warp("2025-01-01T00:00:00Z")
    contract = direct_deploy("contracts/Auspice.py")
    direct_vm.sender = direct_alice
    market_id = contract.create_market(QUESTION, URL, "", CLOSE, "ref-1")

    with direct_vm.expect_revert("[EXPECTED]:trading_open"):
        contract.resolve(market_id)


def test_a_settled_market_cannot_be_resolved_again(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract, market_id = _sold(direct_vm, direct_deploy, direct_alice, direct_bob)

    _mock_page(direct_vm)
    _mock_reading(direct_vm, "YES")
    direct_vm.sender = direct_alice
    contract.resolve(market_id)

    with direct_vm.expect_revert("[EXPECTED]:market_closed"):
        contract.resolve(market_id)


def test_claim_before_settlement_reverts(direct_vm, direct_deploy, direct_alice):
    direct_vm.warp("2025-01-01T00:00:00Z")
    contract = direct_deploy("contracts/Auspice.py")
    direct_vm.sender = direct_alice
    market_id = contract.create_market(QUESTION, URL, "", CLOSE, "ref-1")

    direct_vm.value = 100
    contract.buy(market_id, "YES", "yes-1")
    direct_vm.value = 0

    with direct_vm.expect_revert("[EXPECTED]:market_not_settled"):
        contract.claim(market_id)


def test_a_position_can_only_be_claimed_once(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract, market_id = _sold(direct_vm, direct_deploy, direct_alice, direct_bob)

    _mock_page(direct_vm)
    _mock_reading(direct_vm, "YES")
    direct_vm.sender = direct_alice
    contract.resolve(market_id)
    contract.claim(market_id)

    with direct_vm.expect_revert("[EXPECTED]:nothing_to_claim"):
        contract.claim(market_id)


def test_a_winning_side_with_no_backers_refunds_everybody(
    direct_vm, direct_deploy, direct_alice
):
    direct_vm.warp("2025-01-01T00:00:00Z")
    contract = direct_deploy("contracts/Auspice.py")

    direct_vm.sender = direct_alice
    market_id = contract.create_market(QUESTION, URL, "", CLOSE, "ref-market")

    direct_vm.value = 100
    contract.buy(market_id, "YES", "yes-1")
    direct_vm.value = 0

    direct_vm.warp("2025-01-03T00:00:00Z")
    _mock_page(direct_vm)
    _mock_reading(direct_vm, "NO")

    direct_vm.sender = direct_alice
    assert contract.resolve(market_id) == ""

    record = contract.get_market(market_id)
    assert record["status"] == "VOID"
    assert record["outcome"] == ""
    assert contract.get_summary()["void"] == 1

    contract.claim(market_id)

    ledger = contract.get_ledger()
    assert ledger["total_out"] == 100
    assert ledger["locked_total"] == 0
    assert ledger["total_in"] == ledger["total_out"] + ledger["locked_total"]


def test_the_ledger_balances_through_a_full_market(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract, market_id = _sold(direct_vm, direct_deploy, direct_alice, direct_bob)

    _mock_page(direct_vm)
    _mock_reading(direct_vm, "YES")

    direct_vm.sender = direct_alice
    contract.resolve(market_id)
    ledger = contract.get_ledger()
    assert ledger["total_in"] == ledger["total_out"] + ledger["locked_total"]

    contract.claim(market_id)
    ledger = contract.get_ledger()
    assert ledger["total_in"] == ledger["total_out"] + ledger["locked_total"]

    direct_vm.sender = direct_bob
    contract.claim(market_id)
    ledger = contract.get_ledger()
    assert ledger["total_in"] == ledger["total_out"] + ledger["locked_total"]
