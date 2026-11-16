# Auspice — submission pack

**A two-sided prediction market where the page decides, not an oracle.**

## The idea

A market is a question and the public page that will answer it. Two sides, YES and
NO, and one pool that grows as people back them. When trading closes, anyone may
ask the committee to read the page once. It must copy the sentence that answers the
question; without that quote there is no answer, and no money moves.

The payout is parimutuel: the whole pool is split among the winning side in
proportion to what each account staked. Nobody sets odds — the pool sets them. One
account may add to its side but never back both.

## Deployed facts

| | |
|---|---|
| Network | GenLayer Studio — chainId `61999` (`0xf22f`) |
| Contract | [`0xce62214444a073A7B4C18563D7f3F20AdD815401`](https://explorer-studio.genlayer.com/address/0xce62214444a073A7B4C18563D7f3F20AdD815401) |
| Source revision | the first twelve characters of the SHA-256 of `contracts/Auspice.py`, recorded with the address |
| Owner | none: no pause, no setter, no admin, no upgrade path |
| Money | test GEN only |

`python scripts/verify_deployment.py` recomputes that revision from the checkout
and fails if the contract has drifted from the recorded address.

## What is proven, and where

| Suite | Proves |
|---|---|
| **Direct**, in memory | opening guards, both pools, the one-side rule, every refusal as a credit, all three readings, the proportional payout with its rounding rule, the void path, and the ledger invariant |
| **Studio**, live | a real market opened, two sides backed by two wallets, the committee reading the frozen page after the close, the payout claimed by both sides, and the void fallback when no reading can be grounded |
| **Money paths**, live | a stake below the floor, a wrong side, a duplicate reference, a claim before settlement, a void before it is earned, a stranger reaching for somebody else's credit, and the views agreeing with the ledger |
| **Lint** | `genvm-lint check contracts/Auspice.py` |

The evidence is the [Actions](https://github.com/weels007/genlayer-auspice/actions)
history, and every live assertion is a delta so the suites can be re-run against
the same deployment.

The live suites sign with throwaway Studio accounts kept in Actions secrets — one
for each side of a market — so no key material is committed.

## Limits, stated plainly

- **The page is read after the close.** What the market resolves against is what
  the page says then, not what it said when the first stake arrived. The digest
  records exactly what was read.
- **A vague question will sometimes come back SILENT** rather than wrong, and a
  market nobody can read is voided with every stake refunded. That is the contract
  refusing to guess, and it is the price of having no oracle to appeal to.
- **A winning side with no backers is void.** An answer nobody staked on cannot
  hand the other side's money to the contract; every stake goes back.
- **Studio only.** No mainnet, no audited token, and the caps exist to keep a demo
  bounded rather than to be economic parameters.

## Try it

```bash
pip install -r requirements.txt

python scripts/verify_deployment.py     # the address is this source
pytest tests/direct -v                  # the whole market, in memory, milliseconds
pytest tests/integration -v -s          # the deployed market, live
pytest tests/e2e -v -s                  # the deployed market's money paths
```
