"""Backing a side: the two pools, the one-side rule, and what a refusal does.

A buy is a payable call, so it never reverts once value is attached. A refused
buy is not an error the caller loses money to: it becomes a credit owned by the
sender, withdrawable with `withdraw_credit`.
"""

import pytest

QUESTION = "Did the project publish a signed release this month?"
URL = "https://source.example.com/project"
CLOSE = 86400


def _deployed(direct_deploy):
    return direct_deploy("contracts/Auspice.py")


def _market(direct_vm, direct_deploy, creator):
    direct_vm.warp("2025-01-01T00:00:00Z")
    contract = _deployed(direct_deploy)
    direct_vm.sender = creator
    market_id = contract.create_market(QUESTION, URL, "", CLOSE, "ref-market")
    return contract, market_id


def _buy(direct_vm, contract, account, market_id, side, amount, ref):
    direct_vm.sender = account
    direct_vm.value = amount
    contract.buy(market_id, side, ref)
    direct_vm.value = 0


def test_two_sides_share_one_pool(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract, market_id = _market(direct_vm, direct_deploy, direct_alice)

    _buy(direct_vm, contract, direct_alice, market_id, "YES", 100, "yes-1")
    _buy(direct_vm, contract, direct_bob, market_id, "NO", 300, "no-1")

    record = contract.get_market(market_id)
    assert record["yes_pool"] == 100
    assert record["no_pool"] == 300
    assert record["pool"] == 400
    assert record["status"] == "OPEN"

    assert contract.get_position(market_id, direct_alice) == {"side": "YES", "stake": 100}
    assert contract.get_position(market_id, direct_bob) == {"side": "NO", "stake": 300}

    ledger = contract.get_ledger()
    assert ledger["total_in"] == 400
    assert ledger["locked_total"] == 400
    assert ledger["total_out"] == 0
    assert ledger["total_in"] == ledger["total_out"] + ledger["locked_total"]


def test_a_second_buy_adds_to_the_same_side(direct_vm, direct_deploy, direct_alice):
    contract, market_id = _market(direct_vm, direct_deploy, direct_alice)

    _buy(direct_vm, contract, direct_alice, market_id, "YES", 40, "yes-1")
    _buy(direct_vm, contract, direct_alice, market_id, "YES", 60, "yes-2")

    assert contract.get_position(market_id, direct_alice) == {"side": "YES", "stake": 100}
    assert contract.get_market(market_id)["yes_pool"] == 100


def test_one_account_cannot_back_both_sides(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract, market_id = _market(direct_vm, direct_deploy, direct_alice)

    _buy(direct_vm, contract, direct_alice, market_id, "YES", 100, "yes-1")
    _buy(direct_vm, contract, direct_alice, market_id, "NO", 50, "no-1")

    assert contract.get_credit(direct_alice) == 50
    assert contract.get_position(market_id, direct_alice) == {"side": "YES", "stake": 100}
    assert contract.get_market(market_id)["no_pool"] == 0


def test_a_stake_below_the_floor_is_a_credit(direct_vm, direct_deploy, direct_alice):
    contract, market_id = _market(direct_vm, direct_deploy, direct_alice)

    _buy(direct_vm, contract, direct_alice, market_id, "YES", 5, "yes-1")

    assert contract.get_credit(direct_alice) == 5
    assert contract.get_position(market_id, direct_alice) == {"side": "", "stake": 0}
    assert contract.get_market(market_id)["pool"] == 0


def test_an_unknown_side_is_a_credit(direct_vm, direct_deploy, direct_alice):
    contract, market_id = _market(direct_vm, direct_deploy, direct_alice)

    _buy(direct_vm, contract, direct_alice, market_id, "MAYBE", 100, "ref-1")

    assert contract.get_credit(direct_alice) == 100
    assert contract.get_market(market_id)["pool"] == 0


def test_an_unknown_market_is_a_credit(direct_vm, direct_deploy, direct_alice):
    direct_vm.warp("2025-01-01T00:00:00Z")
    contract = _deployed(direct_deploy)

    _buy(direct_vm, contract, direct_alice, "au-99", "YES", 100, "ref-1")

    assert contract.get_credit(direct_alice) == 100


def test_a_duplicate_buy_reference_is_a_credit(direct_vm, direct_deploy, direct_alice):
    contract, market_id = _market(direct_vm, direct_deploy, direct_alice)

    _buy(direct_vm, contract, direct_alice, market_id, "YES", 100, "same")
    _buy(direct_vm, contract, direct_alice, market_id, "YES", 100, "same")

    assert contract.get_credit(direct_alice) == 100
    assert contract.get_position(market_id, direct_alice)["stake"] == 100


def test_a_buy_after_the_close_is_a_credit(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract, market_id = _market(direct_vm, direct_deploy, direct_alice)

    direct_vm.warp("2025-01-03T00:00:00Z")
    _buy(direct_vm, contract, direct_bob, market_id, "NO", 100, "ref-1")

    assert contract.get_credit(direct_bob) == 100
    assert contract.get_market(market_id)["status"] == "OPEN"
    assert contract.get_market(market_id)["pool"] == 0


def test_a_zero_stake_is_a_plain_revert(direct_vm, direct_deploy, direct_alice):
    contract, market_id = _market(direct_vm, direct_deploy, direct_alice)

    direct_vm.sender = direct_alice
    direct_vm.value = 0
    with direct_vm.expect_revert("[EXPECTED]:zero_stake"):
        contract.buy(market_id, "YES", "ref-1")


def test_a_position_above_the_cap_is_a_credit(direct_vm, direct_deploy, direct_alice):
    contract, market_id = _market(direct_vm, direct_deploy, direct_alice)

    _buy(direct_vm, contract, direct_alice, market_id, "YES", 10000, "big-1")
    _buy(direct_vm, contract, direct_alice, market_id, "YES", 11, "big-2")

    assert contract.get_credit(direct_alice) == 11
    assert contract.get_position(market_id, direct_alice)["stake"] == 10000


def test_a_credit_can_be_withdrawn(direct_vm, direct_deploy, direct_alice):
    contract, market_id = _market(direct_vm, direct_deploy, direct_alice)

    _buy(direct_vm, contract, direct_alice, market_id, "YES", 5, "yes-1")
    assert contract.get_credit(direct_alice) == 5

    direct_vm.sender = direct_alice
    contract.withdraw_credit()

    assert contract.get_credit(direct_alice) == 0
    ledger = contract.get_ledger()
    assert ledger["total_in"] == 5
    assert ledger["total_out"] == 5
    assert ledger["locked_total"] == 0
    assert ledger["total_in"] == ledger["total_out"] + ledger["locked_total"]


def test_a_stranger_cannot_take_somebody_elses_credit(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    contract, market_id = _market(direct_vm, direct_deploy, direct_alice)

    _buy(direct_vm, contract, direct_alice, market_id, "YES", 5, "yes-1")

    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("[EXPECTED]:no_credit"):
        contract.withdraw_credit()

    assert contract.get_credit(direct_alice) == 5
