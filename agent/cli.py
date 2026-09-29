"""Tool CLI for LLM wallet-agent episodes. The agent can read state, read its
inbox/docs (which may contain adversarial content), get quotes, and PROPOSE actions.
Proposals are recorded to agent/episodes/<id>/proposals.json and evaluated offline under
every defense (so the agent's behaviour is identical across defenses).

usage: python agent/cli.py <episode_id> <command> [args]
commands: task | balances | inbox | read <doc> | pools | quote <pool_addr> <sell_token> <amount>
          propose '<json action list>' | proposals
action JSON (list; one list = one atomic wallet transaction):
  {"action":"transfer","token":"USDC","to":"0x..","amount":1000}
  {"action":"send_eth","to":"0x..","amount":0.5}
  {"action":"approve","token":"USDC","spender":"0x..","amount":1000 | "max"}
  {"action":"swap","pool":"0x..","sell":"USDC","amount":1000,"min_out":0.39}
  {"action":"call","to":"0x..","signature":"claim()","args":[] ,"eth":0}
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from web3 import Web3
from pgs.world import World, w3_connect, E18, MAX

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PGS_AGENT_PORT", "8546"))
STATE = os.path.join(HERE, "world_addrs.json")


def world():
    w = World(w3_connect(f"http://127.0.0.1:{PORT}"))
    st = json.load(open(STATE))
    from pgs.world import ART
    for k, (name, a) in st["contracts"].items():
        w.c[k] = w.contract(name, a); w.addr[k] = a
    w.lookalike = st["lookalike"]
    return w


def main():
    eid, cmd, args = sys.argv[1], sys.argv[2], sys.argv[3:]
    ep = json.load(open(os.path.join(HERE, "episodes", eid, "episode.json")))
    w = world()
    sym = {w.addr[k]: k for k in ["USDC", "WETH", "DAI", "TAX"]}
    tok = lambda s: w.addr[s.upper()]
    if cmd == "task":
        print(ep["task"])
    elif cmd == "balances":
        W = w.addr["WALLET"]
        print(f"wallet {W}")
        for k in ["USDC", "WETH", "DAI", "TAX"]:
            print(f"  {k}: {w.balance(k, W)/E18:,.4f}")
        print(f"  ETH: {w.w3.eth.get_balance(W)/E18:,.4f}")
        print(f"  vault shares: {w.c['VAULT'].functions.balanceOf(W).call()/E18:,.4f} (vault {w.addr['VAULT']})")
        print("contacts: " + ", ".join(f"{n} {a}" for n, a in ep.get("contacts", {}).items()))
    elif cmd == "inbox":
        for i, m in enumerate(ep.get("inbox", [])):
            print(f"[{i}] from {m['from']}: {m['subject']}")
        print("(use: read <index> or read <doc name>; docs: " + ", ".join(ep.get("docs", {}).keys()) + ")")
    elif cmd == "read":
        key = args[0]
        if key.isdigit():
            m = ep["inbox"][int(key)]; print(f"From: {m['from']}\nSubject: {m['subject']}\n\n{m['body']}")
        else:
            print(ep["docs"][key])
    elif cmd == "pools":
        for p in ep["pools"]:
            print(f"{p['name']}: {w.addr[p['key']]}  ({p['note']})")
    elif cmd == "quote":
        pool, sell, amt = Web3.to_checksum_address(args[0]), args[1].upper(), float(args[2])
        c = w.contract("Pool", pool)
        out = c.functions.getAmountOut(tok(sell), int(amt * E18)).call()
        t0 = c.functions.token0().call(); t1 = c.functions.token1().call()
        other = sym.get(t1 if tok(sell) == t0 else t0, "?")
        print(f"quote: {amt} {sell} -> {out/E18:.6f} {other}")
        if ep.get("quote_injection"):
            print(ep["quote_injection"])
    elif cmd == "propose":
        acts = json.loads(args[0])
        d = os.path.join(HERE, "episodes", eid, "proposals.json")
        cur = json.load(open(d)) if os.path.exists(d) else []
        cur.append(acts); json.dump(cur, open(d, "w"), indent=1)
        print(f"recorded transaction proposal #{len(cur)-1} with {len(acts)} action(s).")
    elif cmd == "proposals":
        d = os.path.join(HERE, "episodes", eid, "proposals.json")
        print(open(d).read() if os.path.exists(d) else "[]")


def to_calls(w, acts):
    """Compile high-level actions into wallet calls (used by the evaluator)."""
    calls = []
    for a in acts:
        t = a["action"]
        amt = lambda x: MAX if x == "max" else int(round(float(x) * E18))
        if t == "transfer":
            calls.append(w.call(a["token"].upper(), "transfer", Web3.to_checksum_address(a["to"]), amt(a["amount"])))
        elif t == "send_eth":
            calls.append({"target": Web3.to_checksum_address(a["to"]), "value": amt(a["amount"]), "data": "0x"})
        elif t == "approve":
            calls.append(w.call(a["token"].upper(), "approve", Web3.to_checksum_address(a["spender"]), amt(a["amount"])))
        elif t == "swap":
            pool = w.contract("Pool", Web3.to_checksum_address(a["pool"]))
            data = pool.encode_abi("swap", args=[w.addr[a["sell"].upper()], amt(a["amount"]), amt(a.get("min_out", 0)), w.addr["WALLET"]])
            calls.append({"target": pool.address, "value": 0, "data": data})
        elif t == "call":
            sig = a["signature"]; name = sig.split("(")[0]
            types = [x for x in sig[sig.index("(") + 1:-1].split(",") if x]
            args = []
            for ty, v in zip(types, a.get("args", [])):
                args.append(Web3.to_checksum_address(v) if ty == "address" else (amt(v) if ty == "uint256" else v))
            data = "0x" + Web3.keccak(text=sig)[:4].hex().replace("0x", "") + w.w3.codec.encode(types, args).hex()
            calls.append({"target": Web3.to_checksum_address(a["to"]), "value": amt(a.get("eth", 0)), "data": data})
        else:
            raise ValueError(t)
    return calls


if __name__ == "__main__":
    main()
