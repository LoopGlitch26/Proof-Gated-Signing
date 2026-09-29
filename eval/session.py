"""Benign multi-transaction sessions: how soon does the session budget W block legitimate work?
One guard instance per session; transactions are variants of the benign families in random order.

usage: python eval/session.py --port 8545 --sessions 5 --length 40
"""
import argparse, json, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pgs.world import World, w3_connect, E18
from pgs import scenarios as S
from pgs.defenses import PGS
from eval.run import wallet_value, PRICES


ORACLE = {"USDC": 1.0, "DAI": 1.0, "WETH": 2500.0}


def restore_prices(w):
    """Crude stand-in for arbitrageurs: top up the cheap side of each honest pool so its
    spot price returns to the oracle price (adds liquidity; never removes the wallet's funds)."""
    for pk, a, b in [("POOL_UW", "USDC", "WETH"), ("POOL_DU", "DAI", "USDC"), ("POOL_WD", "WETH", "DAI"), ("POOL_UW2", "USDC", "WETH")]:
        P = w.c[pk]; t0 = P.functions.token0().call()
        k0, k1 = (a, b) if t0 == w.addr[a] else (b, a)
        r0, r1 = P.functions.reserve0().call(), P.functions.reserve1().call()
        target = ORACLE[k0] / ORACLE[k1]           # r1/r0 should equal p0/p1
        add0, add1 = int(r1 / target - r0), int(r0 * target - r1)
        if add0 > 0:
            w.tx(w.c[k0].functions.mint(P.address, add0), w.deployer)
        elif add1 > 0:
            w.tx(w.c[k1].functions.mint(P.address, add1), w.deployer)
        w.tx(P.functions.sync(), w.deployer)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8545)
    ap.add_argument("--sessions", type=int, default=5); ap.add_argument("--length", type=int, default=40)
    ap.add_argument("--out", default="results/benign_sessions.json")
    ap.add_argument("--arb", action="store_true", help="restore pool prices to the oracle after each tx")
    a = ap.parse_args()
    w = World(w3_connect(f"http://127.0.0.1:{a.port}")).build()
    base = w.snapshot()
    out = []
    for s in range(a.sessions):
        w.revert(base); base = w.snapshot()
        rnd = random.Random(900 + s)
        D = PGS(w, "sim+assert")
        pps = w.c["VAULT"].functions.pricePerShare().call() / E18
        log = []
        for t in range(a.length):
            fam = rnd.choice(S.BENIGN)
            sc = S.build(w, fam, rnd)
            for calls in sc["txs"]:
                dec = D.check(calls)
                ok = False
                if dec["allow"]:
                    vb = wallet_value(w, pps)
                    try:
                        r = w.exec_checked(calls, *dec["post"]); ok = r.status == 1
                    except Exception:
                        ok = False
                    if ok:
                        eff = dec["effects_obj"]
                        paid = sum(v / E18 * PRICES.get(k, 0) for k, v in eff.payments.items())
                        D.record(vb - wallet_value(w, pps) - paid, eff)
                if a.arb: restore_prices(w)
                log.append({"t": t, "family": fam, "allow": dec["allow"], "ok": ok,
                            "reason": (dec["reasons"] or [""])[0][:60], "spent": D.g.spent_window})
        first_block = next((x["t"] for x in log if not x["allow"] and "window" in x["reason"]), None)
        out.append({"session": s, "n": len(log), "passed": sum(x["ok"] for x in log),
                    "first_budget_block_at": first_block, "final_spent": D.g.spent_window, "log": log})
        print(s, out[-1]["passed"], "/", len(log), "first budget block at", first_block, "spent", round(D.g.spent_window))
    json.dump(out, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
