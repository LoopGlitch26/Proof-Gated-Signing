"""Defenses share one interface: check(calls, task) -> dict(allow, post, reasons, latency_s)."""
import json, time
from web3 import Web3
from .guard import Guard, Policy, E18

ATTACK_LABELS = {}


def make_policy(w):
    pps = w.c["VAULT"].functions.pricePerShare().call() / E18
    return Policy(
        prices={"USDC": 1.0, "DAI": 1.0, "WETH": 2500.0, "TAX": 1.0, "ETH": 2500.0, "VAULT": 1.0 * pps},
        band=0.005, max_rel_loss=0.03, floor_usd=25.0, window_usd=5000.0,
        payees={w.alice: 20000.0, w.bob: 10000.0},
        trusted_spenders={w.addr[k] for k in ["POOL_UW", "POOL_DU", "POOL_WD", "VAULT"]},
        owned=[w.addr["VAULT"]], inflow_tol=0.01)


class NoDefense:
    name = "none"
    def __init__(self, w): pass
    def check(self, calls, task=None, ctx=None):
        return {"allow": True, "post": None, "reasons": [], "latency_s": 0.0}
    def record(self, *a, **k): pass


class StaticAllowlist:
    """Conventional wallet policy: allowlisted targets + selectors + recipients/spenders."""
    name = "allowlist"
    def __init__(self, w):
        self.w = w
        self.tokens = {w.addr[k] for k in ["USDC", "WETH", "DAI", "TAX"]}
        self.pools = {w.addr[k] for k in ["POOL_UW", "POOL_DU", "POOL_WD", "POOL_TU"]}
        self.vault = w.addr["VAULT"]
        self.payees = {w.alice, w.bob}
        sel = lambda s: Web3.keccak(text=s)[:4].hex()
        self.S = {"approve": sel("approve(address,uint256)"), "transfer": sel("transfer(address,uint256)"),
                  "swap": sel("swap(address,uint256,uint256,address)"), "deposit": sel("deposit(uint256)"),
                  "withdraw": sel("withdraw(uint256)")}
    def check(self, calls, task=None, ctx=None):
        t0 = time.time(); reasons = []
        for c in calls:
            tgt = Web3.to_checksum_address(c["target"]); d = c["data"][2:]
            s = d[:8]
            arg0 = Web3.to_checksum_address("0x" + d[8 + 24: 8 + 64]) if len(d) >= 72 else None
            if not d:
                if c["value"] > 0 and tgt not in self.payees: reasons.append("eth-to-non-payee")
                continue
            if c["value"] > 0: reasons.append("value-to-contract")
            if tgt in self.tokens:
                if s == self.S["approve"] and arg0 not in self.pools | {self.vault}: reasons.append("approve-spender")
                elif s == self.S["transfer"] and arg0 not in self.payees: reasons.append("transfer-recipient")
                elif s not in (self.S["approve"], self.S["transfer"]): reasons.append("token-selector")
            elif tgt in self.pools:
                if s != self.S["swap"]: reasons.append("pool-selector")
            elif tgt == self.vault:
                if s not in (self.S["deposit"], self.S["withdraw"]): reasons.append("vault-selector")
            else:
                reasons.append("target-not-allowlisted")
        return {"allow": not reasons, "post": None, "reasons": reasons, "latency_s": time.time() - t0}
    def record(self, *a, **k): pass


class PGS:
    def __init__(self, w, mode, recv_checks=True):
        self.name = "pgs-sim" if mode == "sim" else ("pgs-assert" if recv_checks else "pgs-assert-norecv")
        self.g = Guard(w, make_policy(w), mode=mode)
        self.g.recv_checks = recv_checks
    def check(self, calls, task=None, ctx=None):
        d = self.g.check(calls)
        d["effects_obj"] = d.pop("effects")
        return d
    def record(self, loss_usd, effects=None):
        self.g.record(loss_usd, effects)


class LLMJudge:
    """Replays decisions produced offline by LLM judge agents (see eval/llm_judge)."""
    def __init__(self, w, path, name):
        self.name = name
        self.dec = json.load(open(path))
    def check(self, calls, task=None, ctx=None):
        key = f'{ctx["sid"]}#{ctx["i"]}'
        d = self.dec.get(key)
        if d is None:
            return {"allow": None, "post": None, "reasons": ["missing"], "latency_s": 0.0}
        return {"allow": d["decision"] == "ALLOW", "post": None, "reasons": [d.get("reason", "")], "latency_s": 0.0}
    def record(self, *a, **k): pass
