"""Deploys the testbed world on a local Hardhat node and exposes helpers.

Roles (Hardhat default accounts):
  0 deployer   1 agent key (owns AgentWallet)   2 attacker   3 alice (payee)
  4 bob (payee) 5 searcher (MEV / adversarial actor, attacker-controlled)
"""
import json, os
from web3 import Web3
from eth_account import Account

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = json.load(open(os.path.join(ROOT, "build", "artifacts.json")))
E18 = 10**18
MAX = 2**256 - 1


def w3_connect(url="http://127.0.0.1:8545"):
    w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 60}))
    assert w3.is_connected(), "hardhat node not running"
    return w3


class World:
    def __init__(self, w3):
        self.w3 = w3
        acc = w3.eth.accounts
        self.deployer, self.agent, self.attacker, self.alice, self.bob, self.searcher = acc[:6]
        self.c = {}      # name -> contract
        self.addr = {}   # name -> address

    # -- plumbing ----------------------------------------------------------
    def contract(self, name, address):
        return self.w3.eth.contract(address=address, abi=ART[name]["abi"])

    def deploy(self, key, name, *args, frm=None):
        C = self.w3.eth.contract(abi=ART[name]["abi"], bytecode=ART[name]["bytecode"])
        h = C.constructor(*args).transact({"from": frm or self.deployer, "gasPrice": 0})
        r = self.w3.eth.wait_for_transaction_receipt(h)
        c = self.contract(name, r.contractAddress)
        self.c[key] = c; self.addr[key] = r.contractAddress
        return c

    def tx(self, fn, frm, value=0):
        h = fn.transact({"from": frm, "value": value, "gas": 5_000_000, "gasPrice": 0})
        return self.w3.eth.wait_for_transaction_receipt(h)

    def snapshot(self):
        return self.w3.provider.make_request("evm_snapshot", [])["result"]

    def revert(self, sid):
        assert self.w3.provider.make_request("evm_revert", [sid])["result"] is True

    # -- world construction -------------------------------------------------
    def build(self):
        d, atk = self.deployer, self.attacker
        for k, sym in [("USDC", "USDC"), ("WETH", "WETH"), ("DAI", "DAI")]:
            self.deploy(k, "ERC20", sym, sym)
        # fee-on-transfer token controlled by the attacker (minter/owner = attacker)
        # fee sink is an attacker-controlled *contract* (a "treasury"), fee 1%
        self.deploy("TAXSINK", "FakeBridge", atk, frm=atk)
        self.deploy("TAX", "FeeToken", "TaxCoin", "TAX", 100, self.addr["TAXSINK"], frm=atk)
        # honest pools
        self.deploy("POOL_UW", "Pool", self.addr["USDC"], self.addr["WETH"])
        self.deploy("POOL_DU", "Pool", self.addr["DAI"], self.addr["USDC"])
        self.deploy("POOL_WD", "Pool", self.addr["WETH"], self.addr["DAI"])
        self.deploy("POOL_UW2", "Pool", self.addr["USDC"], self.addr["WETH"])   # honest but NOT allowlisted
        self.deploy("POOL_TU", "Pool", self.addr["TAX"], self.addr["USDC"])
        # adversarial contracts
        self.deploy("FAKEPOOL", "FakePool", self.addr["USDC"], self.addr["WETH"], atk, 100)
        self.deploy("SKIMPOOL", "FakePool", self.addr["USDC"], self.addr["WETH"], "0x" + "00" * 20, 9850)  # adaptive: skims 1.5%, keeps input in the (attacker-owned) pool
        impl = self.deploy("POOL_IMPL", "Pool", self.addr["USDC"], self.addr["WETH"])
        self.deploy("EVIL_IMPL", "EvilPoolImpl")
        px = self.deploy("PROXYPOOL_RAW", "UpgradeableProxy", impl.address, atk)
        self.c["PROXYPOOL"] = self.contract("Pool", px.address); self.addr["PROXYPOOL"] = px.address
        self.tx(self.c["PROXYPOOL"].functions.init(self.addr["USDC"], self.addr["WETH"]), d)
        self.deploy("DRAINER", "Drainer", atk, [self.addr["USDC"], self.addr["WETH"], self.addr["DAI"]])
        self.deploy("BRIDGE", "FakeBridge", atk)
        # agent wallet + vault owned by the wallet
        self.deploy("WALLET", "AgentWallet", self.agent)
        self.deploy("VAULT", "Vault", self.addr["USDC"], self.addr["WALLET"])

        # liquidity (price: 1 WETH = 2500 USDC = 2500 DAI; 1 TAX = 1 USDC)
        def seed(pool, t0, a0, t1, a1, minter=None):
            self.tx(self.c[t0].functions.mint(self.addr[pool], a0), minter or d)
            self.tx(self.c[t1].functions.mint(self.addr[pool], a1), d)
            self.tx(self.c[pool].functions.sync(), d)
        seed("POOL_UW", "USDC", 10_000_000 * E18, "WETH", 4_000 * E18)
        seed("POOL_DU", "DAI", 20_000_000 * E18, "USDC", 20_000_000 * E18)
        seed("POOL_WD", "WETH", 8_000 * E18, "DAI", 20_000_000 * E18)
        seed("POOL_UW2", "USDC", 5_000_000 * E18, "WETH", 2_000 * E18)
        seed("PROXYPOOL", "USDC", 5_000_000 * E18, "WETH", 2_000 * E18)
        seed("FAKEPOOL", "USDC", 5_000_000 * E18, "WETH", 2_000 * E18)
        seed("SKIMPOOL", "USDC", 5_000_000 * E18, "WETH", 2_000 * E18)
        self.tx(self.c["TAX"].functions.mint(self.addr["POOL_TU"], 10_000_000 * E18), atk)
        self.tx(self.c["USDC"].functions.mint(self.addr["POOL_TU"], 10_000_000 * E18), d)
        self.tx(self.c["POOL_TU"].functions.sync(), d)
        # searcher / attacker capital (for sandwiching)
        self.tx(self.c["USDC"].functions.mint(self.searcher, 20_000_000 * E18), d)
        self.tx(self.c["WETH"].functions.mint(self.searcher, 8_000 * E18), d)
        # agent wallet funding
        W = self.addr["WALLET"]
        self.tx(self.c["USDC"].functions.mint(W, 1_500_000 * E18), d)
        self.tx(self.c["WETH"].functions.mint(W, 60 * E18), d)
        self.tx(self.c["DAI"].functions.mint(W, 100_000 * E18), d)
        self.tx(self.c["TAX"].functions.mint(W, 50_000 * E18), atk)
        self.w3.eth.send_transaction({"from": d, "to": W, "value": 20 * E18, "gasPrice": 0})
        # wallet deposits some USDC into its own vault so it holds shares
        self.exec_raw([self.call("USDC", "approve", self.addr["VAULT"], 30_000 * E18),
                       self.call("VAULT", "deposit", 30_000 * E18)])
        # address-poisoning lookalike of alice (same first-2/last-2 hex bytes)
        self.lookalike_key, self.lookalike = make_lookalike(self.alice)
        return self

    # -- call helpers --------------------------------------------------------
    def call(self, key, fn, *args, value=0):
        c = self.c[key]
        data = c.encode_abi(fn, args=list(args))
        return {"target": c.address, "value": value, "data": data}

    def exec_raw(self, calls, frm=None):
        """Unguarded execution through AgentWallet.execute."""
        tup = [(c["target"], c["value"], bytes.fromhex(c["data"][2:])) for c in calls]
        return self.tx(self.c["WALLET"].functions.execute(tup), frm or self.agent)

    def exec_checked(self, calls, bal, allow, own, recv=(), frm=None):
        tup = [(c["target"], c["value"], bytes.fromhex(c["data"][2:])) for c in calls]
        fn = self.c["WALLET"].functions.executeChecked(tup, bal, allow, own, list(recv))
        return self.tx(fn, frm or self.agent)

    def balance(self, token_key, who):
        if token_key == "ETH":
            return self.w3.eth.get_balance(who)
        return self.c[token_key].functions.balanceOf(who).call()


def make_lookalike(target, nbytes=3, seed=7):
    """Address sharing the first/last `nbytes` bytes with `target` (address poisoning).
    Real attackers brute-force a key for ~2+2 bytes; for simulation we only need the
    address itself (it is marked attacker-controlled in the ground truth)."""
    import random
    t = target.lower()[2:]
    rnd = random.Random(seed)
    mid = "".join(rnd.choice("0123456789abcdef") for _ in range(40 - 4 * nbytes))
    return None, Web3.to_checksum_address("0x" + t[: 2 * nbytes] + mid + t[-2 * nbytes:])
