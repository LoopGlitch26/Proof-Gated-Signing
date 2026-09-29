"""Proof-Gated Signing (PGS): simulate -> extract effects -> prove policy (Z3) ->
(optionally) compile on-chain post-conditions whose satisfaction is *proved* to imply
the policy, so the guarantee survives state drift between simulation and inclusion.
"""
import time
from dataclasses import dataclass, field
from fractions import Fraction
import z3
from web3 import Web3

E18 = 10**18
TRANSFER_T = "0x" + Web3.keccak(text="Transfer(address,address,uint256)").hex().replace("0x", "")
APPROVAL_T = "0x" + Web3.keccak(text="Approval(address,address,uint256)").hex().replace("0x", "")
SEL_TRANSFER = Web3.keccak(text="transfer(address,uint256)")[:4].hex().replace("0x", "")


def _h(x):
    x = x.hex() if not isinstance(x, str) else x
    return x if x.startswith("0x") else "0x" + x


def _topic_addr(t):
    return Web3.to_checksum_address("0x" + _h(t)[-40:])


@dataclass
class Policy:
    prices: dict            # asset key -> nominal USD price (float); asset keys: token names, "ETH", "VAULT"
    band: float = 0.01      # oracle uncertainty: true price in [p(1-band), p(1+band)]
    max_rel_loss: float = 0.03   # unexplained value loss <= s * value_out + floor
    floor_usd: float = 25.0
    window_usd: float = 5000.0   # cumulative unexplained loss budget per session
    payees: dict = field(default_factory=dict)       # address -> per-tx cap in USD
    trusted_spenders: set = field(default_factory=set)  # may retain residual allowance
    owned: list = field(default_factory=list)         # contracts whose owner must stay the wallet
    inflow_tol: float = 0.01     # slack on received amounts when compiling post-conditions


@dataclass
class Effects:
    reverted: bool
    delta: dict              # asset key -> int (wei-units)
    residual_allow: dict     # (token_key, spender) -> int after tx
    owners_after: dict       # contract addr -> owner after
    eoa_outflows: list       # (recipient, asset_key, amount) for value leaving to EOAs
    payments: dict           # asset_key -> amount paid to allowlisted payees at top level (fixed)
    payment_usd_by_payee: dict
    unknown_tokens: set
    gas_used: int = 0
    receipts: dict = field(default_factory=dict)   # (payee, asset_key) -> credited amount (enforced on-chain)


class Guard:
    """mode: 'sim' (simulate+prove, then sign plain tx) or 'sim+assert' (also enforce
    proved post-conditions on-chain via AgentWallet.executeChecked)."""

    def __init__(self, world, policy: Policy, mode="sim+assert"):
        self.w, self.p, self.mode = world, policy, mode
        self.w3 = world.w3
        self.wallet = world.addr["WALLET"]
        self.assets = {}   # key -> address (None for ETH)
        for k in policy.prices:
            self.assets[k] = None if k == "ETH" else world.addr[k]
        self.addr2key = {v: k for k, v in self.assets.items() if v}
        self.spent_window = 0.0
        self.known_allow_pairs = set()

    # ------------------------------------------------------------------ effects
    def _bal(self, key):
        if key == "ETH":
            return self.w3.eth.get_balance(self.wallet)
        return self.w.contract("ERC20", self.assets[key]).functions.balanceOf(self.wallet).call()

    def _bal_of(self, key, who):
        if key == "ETH":
            return self.w3.eth.get_balance(who)
        return self.w.contract("ERC20", self.assets[key]).functions.balanceOf(who).call()

    def simulate(self, calls) -> Effects:
        w = self.w
        sid = w.snapshot()
        try:
            pre = {k: self._bal(k) for k in self.assets}
            payee_pre = {(a, k): self._bal_of(k, a) for a in self.p.payees for k in self.assets if k != "VAULT"}
            tup = [(c["target"], c["value"], bytes.fromhex(c["data"][2:])) for c in calls]
            try:
                h = w.c["WALLET"].functions.execute(tup).transact({"from": w.agent, "gas": 5_000_000, "gasPrice": 0})
                r = self.w3.eth.wait_for_transaction_receipt(h)
                ok = r.status == 1
            except Exception:
                ok, r = False, None
            if not ok:
                return Effects(True, {}, {}, {}, [], {}, {}, set())
            post = {k: self._bal(k) for k in self.assets}
            delta = {k: post[k] - pre[k] for k in self.assets}
            pairs, eoa, unknown = set(self.known_allow_pairs), [], set()
            for lg in r.logs:
                if not lg["topics"]:
                    continue
                t0 = _h(lg["topics"][0])
                tok = Web3.to_checksum_address(lg["address"])
                if t0 == APPROVAL_T and _topic_addr(lg["topics"][1]) == self.wallet:
                    pairs.add((tok, _topic_addr(lg["topics"][2])))
                if t0 == TRANSFER_T and len(lg["topics"]) == 3:
                    frm, to = _topic_addr(lg["topics"][1]), _topic_addr(lg["topics"][2])
                    amt = int(_h(lg["data"]), 16)
                    if tok not in self.addr2key and (frm == self.wallet or to == self.wallet):
                        unknown.add(tok)
                    if frm == self.wallet and len(self.w3.eth.get_code(to)) == 0:
                        eoa.append((to, self.addr2key.get(tok, tok), amt))
            for c in calls:   # direct ETH sends to EOAs
                if c["value"] > 0 and len(self.w3.eth.get_code(c["target"])) == 0:
                    eoa.append((Web3.to_checksum_address(c["target"]), "ETH", c["value"]))
            resid = {}
            for tok, sp in pairs:
                resid[(tok, sp)] = self.w.contract("ERC20", tok).functions.allowance(self.wallet, sp).call()
            owners = {a: self.w.contract("Vault", a).functions.owner().call() for a in self.p.owned}
            # Payments: only *top-level, direct* transfers to allowlisted payees are candidates, and
            # the credit is min(intended amount, what the payee actually received in simulation), so
            # transfer fees count as loss. Credited receipts are enforced on-chain (RecvCheck).
            intended = {}
            for c in calls:
                tgt = Web3.to_checksum_address(c["target"])
                if c["value"] > 0 and tgt in self.p.payees:
                    intended[(tgt, "ETH")] = intended.get((tgt, "ETH"), 0) + c["value"]
                d = c["data"][2:] if c["data"].startswith("0x") else c["data"]
                if tgt in self.addr2key and d[:8] == SEL_TRANSFER:
                    to = Web3.to_checksum_address("0x" + d[8 + 24: 8 + 64])
                    amt = int(d[8 + 64: 8 + 128], 16)
                    if to in self.p.payees:
                        k = self.addr2key[tgt]
                        intended[(to, k)] = intended.get((to, k), 0) + amt
            payments, pay_usd, receipts = {}, {}, {}
            for (a, k), amt in intended.items():
                got = self._bal_of(k, a) - payee_pre[(a, k)]
                cr = max(0, min(amt, got))
                if cr == 0: continue
                receipts[(a, k)] = cr
                payments[k] = payments.get(k, 0) + cr
                pay_usd[a] = pay_usd.get(a, 0) + cr / E18 * self.p.prices[k]
            return Effects(False, delta, resid, owners, eoa, payments, pay_usd, unknown, r.gasUsed, receipts)
        finally:
            w.revert(sid)

    # ------------------------------------------------------------------ proof
    def _prove_value(self, box_lo, exact, payments):
        """Prove: for all prices in band and all outcomes in the box, unexplained
        loss <= s*V_out + floor and window budget holds. Returns (ok, counterexample)."""
        P = self.p
        s = z3.Solver(); s.set("timeout", 5000)
        loss_terms, vout_terms = [], []
        for k, lo in box_lo.items():
            nominal = Fraction(P.prices[k]).limit_denominator(10**6)
            p = z3.Real("p_" + k)
            s.add(p >= nominal * Fraction(1 - P.band).limit_denominator(10**6),
                  p <= nominal * Fraction(1 + P.band).limit_denominator(10**6))
            d = z3.Real("d_" + k)
            if exact:
                s.add(d == z3.RealVal(Fraction(lo, E18)))
            else:
                s.add(d >= z3.RealVal(Fraction(lo, E18)))
            loss_terms.append(-d * p)
            pay = Fraction(payments.get(k, 0), E18)
            if pay:
                loss_terms.append(-z3.RealVal(pay) * p)
            out_amt = Fraction(max(-lo, 0), E18) - pay
            if out_amt != 0:   # negative terms (payment > net outflow) make the bound stricter, as in Def. 1
                vout_terms.append(z3.RealVal(out_amt) * p)
        loss = z3.Sum(loss_terms) if loss_terms else z3.RealVal(0)
        vout = z3.Sum(vout_terms) if vout_terms else z3.RealVal(0)
        rel = loss <= z3.RealVal(Fraction(P.max_rel_loss).limit_denominator(10**6)) * vout + z3.RealVal(Fraction(P.floor_usd))
        win = z3.RealVal(Fraction(self.spent_window).limit_denominator(10**6)) + loss <= z3.RealVal(Fraction(P.window_usd))
        s.add(z3.Not(z3.And(rel, win)))
        res = s.check()
        if res == z3.unsat:
            return True, None
        if res == z3.sat:
            m = s.model()
            which = "relative-loss" if z3.is_false(m.eval(rel, model_completion=True)) else "window"
            return False, {"violated": which, "loss_usd": float(m.eval(loss, model_completion=True).as_fraction())}
        return False, {"violated": "solver-unknown"}

    def check(self, calls):
        """Returns decision dict. If allowed, includes post-conditions for execution."""
        t0 = time.time()
        eff = self.simulate(calls)
        dec = {"allow": False, "reasons": [], "effects": eff}
        if eff.reverted:
            dec["reasons"].append("simulation-reverted")
        P = self.p
        for (to, k, amt) in eff.eoa_outflows:          # value to EOAs only to allowlisted payees
            if to not in P.payees:
                dec["reasons"].append(f"eoa-recipient-not-allowlisted:{to}")
        for payee, usd in eff.payment_usd_by_payee.items():
            if usd > P.payees[payee]:
                dec["reasons"].append(f"payee-cap:{payee}")
        for (tok, sp), a in eff.residual_allow.items():  # no residual exposure to untrusted spenders
            if a > 0 and sp not in P.trusted_spenders:
                dec["reasons"].append(f"residual-allowance:{sp}")
        for a, o in eff.owners_after.items():
            if o != self.wallet:
                dec["reasons"].append(f"ownership-change:{a}")
        if eff.unknown_tokens:
            dec["reasons"].append("unpriced-token-flow")
        if not eff.reverted:
            ok, cex = self._prove_value(eff.delta, exact=True, payments=eff.payments)
            if not ok:
                dec["reasons"].append(f"value-policy:{cex}")
        post = None
        if not dec["reasons"] and self.mode == "sim+assert":
            # compile post-conditions: outflows may not exceed simulated, inflows >= (1-tol)*sim,
            # untouched assets may not decrease; then PROVE box => policy.
            lo = {}
            for k, d in eff.delta.items():
                lo[k] = d if d <= 0 else int(d * (1 - P.inflow_tol))
            ok, cex = self._prove_value(lo, exact=False, payments=eff.payments)
            if not ok:
                lo = dict(eff.delta)   # tighten: require at least the simulated outcome
                ok, cex = self._prove_value(lo, exact=False, payments=eff.payments)
            if not ok:
                dec["reasons"].append(f"postcondition-unprovable:{cex}")
            else:
                bal = [(self.assets[k] or "0x" + "00" * 20, int(v)) for k, v in lo.items()]
                allow = [(tok, sp, int(a)) for (tok, sp), a in eff.residual_allow.items()]
                own = [(a, self.wallet) for a in P.owned]
                recv = [(self.assets[k] or "0x" + "00" * 20, a, int(v)) for (a, k), v in eff.receipts.items()] \
                    if getattr(self, "recv_checks", True) else []
                post = (bal, allow, own, recv)
        dec["allow"] = not dec["reasons"]
        dec["post"] = post
        dec["latency_s"] = time.time() - t0
        return dec

    def realized_loss_usd(self, delta, payments):
        return -sum(delta[k] / E18 * self.p.prices[k] for k in delta) - sum(
            payments.get(k, 0) / E18 * self.p.prices[k] for k in payments)

    def record(self, loss_usd, effects=None):
        self.spent_window += max(loss_usd, 0.0)
        if effects is not None:
            self.known_allow_pairs |= set(effects.residual_allow.keys())
