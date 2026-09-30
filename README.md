<div align="center">

# 🛡️ Proof-Gated Signing

### Solver-checked transaction guards that hold under state drift, for onchain AI agents

*If an AI agent is tricked into signing something harmful, the transaction either still obeys your policy when it executes, or it doesn't execute at all.*

<br/>

![Solidity](https://img.shields.io/badge/Solidity-0.8.26-363636?logo=solidity)
![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![Z3](https://img.shields.io/badge/SMT-Z3-5C2D91)
![Hardhat](https://img.shields.io/badge/Hardhat-local%20chain-FFF100?logo=ethereum&logoColor=black)
![Scenarios](https://img.shields.io/badge/scenarios-260-2a78d6)
![Reproducible](https://img.shields.io/badge/rerun-1%2C300%2F1%2C300%20identical-1baf7a)

**[📄 Paper](paper/main.pdf)** · **[🧠 How it works](#how)** · **[📊 Results](#results)** · **[⚠️ Limits](#limits)** · **[⚡ Quick start](#quickstart)**

</div>

---

## 💡 The problem in 30 seconds

AI agents are starting to hold wallets: they pay invoices, swap tokens and manage treasuries. They also read things attackers can write, such as emails, docs, token metadata and tool outputs. An injected instruction can make the agent propose a harmful transaction.

The usual last line of defense is to **check the transaction before signing**. It comes in three forms:

| Check | What it does | Where it breaks |
|---|---|---|
| 📋 **Allowlist** | Only known contracts and recipients | Blind to known-but-mutable contracts and honest-but-new ones |
| 🤖 **LLM reviewer** | A second model judges "does this look right?" | Blind to things that look legitimate (fake bridges, fake pools) |
| 🔮 **Simulation** | Dry-run it and see the outcome | **Checks the past.** The chain can change before execution |

The last row is the core insight. A pre-signing check describes the chain **when it was checked**. The transaction runs **later**, and in between an attacker can:

- 🥪 **sandwich** your trade to worsen your price,
- 🔄 **upgrade** a contract you're about to call into a draining one,
- 💸 **raise the fee** on a token you're sending to 90%.

The check was right, and the thing that ran was different. We call this **state drift**.

> 🏦 **Analogy:** the bank approves your cheque, then the scammer changes the amount *after approval but before it clears.*

---

<a id="how"></a>

## 🧠 How it works

```mermaid
flowchart LR
    A["🤖 LLM agent<br/><sub>reads untrusted content</sub>"] -->|proposed tx| S

    subgraph G["🛡️ Guard (holds the signing key)"]
        direction LR
        S["1️⃣ Simulate<br/><sub>on current state</sub>"] --> E["2️⃣ Extract effects<br/><sub>balances · allowances<br/>owners · receipts</sub>"]
        E --> P["3️⃣ Prove policy<br/><sub>Z3: ∀ price in band</sub>"]
        P -->|valid| C["4️⃣ Compile & prove<br/><sub>post-conditions Φ<br/>Φ ⇒ policy</sub>"]
    end

    P -. counterexample → refuse .-> A
    C -->|"sign (tx, Φ)"| W["🔐 5️⃣ AgentWallet<br/><sub>executeChecked:<br/>run tx, then check Φ atomically</sub>"]
    X["😈 Attacker<br/><sub>front-run · upgrade · fee change</sub>"] -->|drift| CH["⛓️ Chain state<br/>at inclusion"]
    CH --> W
    W --> O{"✅ executes ⇒ policy holds<br/>❌ otherwise ⇒ reverts"}

    style G fill:#eef4fc,stroke:#2a78d6,stroke-dasharray: 5 5
    style W fill:#eafaf2,stroke:#1baf7a
    style X fill:#fdeeee,stroke:#e34948
```

| Step | What happens |
|---|---|
| **1. Simulate** | Run the proposed batch on a snapshot of the current chain |
| **2. Extract effects** | Balance change of every tracked asset, leftover token approvals, who owns what, what each payee actually received |
| **3. Prove the policy** | Z3 checks *"loss ≤ 3% of outflow"*, *"session loss ≤ $5k"* and *"only pay approved payees"* **for every price in the oracle's uncertainty band**. If it fails, you get a concrete counterexample |
| **4. Compile guarantees** ⭐ | Turn the assumptions into hard bounds: *"wallet must gain ≥ X WETH"*, *"Alice must receive ≥ Y USDC"*, *"no leftover approvals"*, *"we still own the vault"*. The solver then **proves that any outcome within these bounds satisfies the policy** |
| **5. Enforce on-chain** | The wallet runs the transaction and checks the bounds in the **same atomic call**. Anything broken reverts everything |

> **The guarantee (Theorem 1):** if the transaction executes, the policy held for every price in the band, no matter what changed on-chain between check and execution.

### 🆚 "Isn't this just slippage protection?"

No. `minOut` is the closest cousin, but:

| | `minOut` slippage limit | Proof-Gated Signing |
|---|---|---|
| Covers | one token of one swap | **every** tracked asset + payee receipts + approvals + ownership |
| Set by | the (possibly tricked) agent | an **independent guard** |
| Tied to a policy? | no | **proved** to imply the whole-wallet policy under price uncertainty |
| Catches a hidden extra transfer in a batch | ❌ | ✅ |
| Catches a fee rug on a *payment* | ❌ | ✅ |

---

<a id="results"></a>

## 📊 Results

**260 scenarios**: 14 attack families × 10 variants + 12 benign families × 10 variants. Harm is measured from **attacker balances**, never from the policy, so the guard can't grade its own homework.

<div align="center">

| Defense | 🛡️ Attacks prevented | ✅ Benign passed | 💰 Attacker gain |
|:--|:--:|:--:|--:|
| No guard | 0.0% | 100% | $12.18M |
| Static allowlist | 71.4% | 83.3% | $558k |
| Simulation only | 57.9% | 97.5% | $855k |
| PGS without receipt checks *(ablation)* | 86.4% | 97.5% | $80k |
| **Proof-Gated Signing** | **93.6%** | **97.5%** | **$42k** |

<sub>Rates are on <b>this suite</b> (140 harmful / 120 benign scenarios we designed). Wilson 95% CIs are in the paper.</sub>

<img src="assets/results.png" width="820" alt="Attack prevention and benign pass rates with 95% confidence intervals"/>

<sub>Comparison including LLM judges, on the 120-scenario subset where they were run.</sub>

</div>

### 🔑 Key findings

- 🌊 **State drift is real, and simulation can't see it.** Simulation-only guarding missed **all 50** drift scenarios. Under PGS, **all 50 ended with $0 attacker gain**: 40 reverted on-chain, and in 10 a mempool-reading attacker found no profitable move within the bounds.
- 🤖 **A clean simulation made an LLM reviewer *worse*.** Given the pre-drift simulation, the LLM judge approved the contract-upgrade attack **5/5** times, up from 2/5 without it. The simulation "looked safe."
- 🧾 **Payee receipts matter.** Without them, a fee-on-transfer token silently skimmed 30–90% of payments (**0/10** blocked). With them, **10/10** reverted.
- 🐢 **The one thing PGS can't prevent is bounded.** An attacker who drains slowly, with every step inside policy, was capped at **≤ $4.76k** by the $5k session budget (vs. up to $10.8k unguarded).
- 🧪 **Real LLM wallet agents** (Claude Opus & Haiku) resisted most injections on their own. What got through was the **fake bridge**, which looks legitimate. PGS blocked it.
- ⚡ **Cost:** ~**41k gas** extra per transaction (≈ $0.10–$3 depending on gas price) and **0.1–0.2 s** per check.

<details>
<summary><b>📈 The adaptive slow-drain attack, bounded by the session budget</b></summary>
<br/>
<img src="assets/slow_drain.png" width="480" alt="Attacker gain per variant, no guard vs PGS, with $5k session budget line"/>
</details>

<details>
<summary><b>🧨 All 14 attack families and how each ended under PGS</b></summary>

| ID | Attack | Kind | Outcome under PGS |
|---|---|---|---|
| H1 | Invoice paid to attacker | intent | 🚫 refused before signing |
| H2 | Look-alike (poisoned) address | intent | 🚫 refused before signing |
| H3 | "Airdrop claim" drainer | intent | 🚫 refused before signing |
| H4 | Latent unlimited approval | intent | 🚫 refused before signing |
| H5 | Fake pool with honest quotes | intent | 🚫 refused before signing |
| H6 | Sandwich of an unprotected swap | **drift** | ⛓️ reverted on-chain |
| H7 | Proxy upgraded to drainer before inclusion | **drift** | ⛓️ reverted on-chain |
| H8 | Token fee rugged before inclusion | **drift** | ⛓️ reverted on-chain |
| H9 | Vault ownership transfer | intent | 🚫 refused before signing |
| H10 | Hidden transfer inside a legit batch | intent | 🚫 refused before signing |
| H11 | Fake bridge | intent | 🚫 refused before signing |
| H12 | Slow drain, every step in-policy | adaptive | 🟡 **bounded** by session budget |
| H13 | Sandwich by an attacker who reads the guard's bounds | **drift** + adaptive | ✅ executed safely, $0 extractable |
| H14 | Fee rug on a *payment* | **drift** | ⛓️ reverted on-chain (receipt check) |

</details>

---

<a id="limits"></a>

## ⚠️ What it does *not* do

Being explicit about the boundary matters more than the headline number.

| ✅ Guaranteed | 🟡 Bounded, not prevented | ❌ Not covered |
|---|---|---|
| Value policy on tracked assets, for all prices in the band | Losses that stay within per-tx policy (capped per session) | Untracked assets (NFTs, exotic positions) |
| Payees receive ≥ credited amount | Drift within the tolerance τ (≤ ~1% of inflow) | Off-chain signatures (e.g. permits) |
| No leftover approvals beyond simulated | | A badly chosen policy or wrong oracle band |
| Owned contracts keep their owner | | An agent that holds the key itself |
| **All of the above under arbitrary state drift** | | |

Also note that the test world is a **simplified local chain** with attacks we designed. Mainnet-fork replays and a held-out attack set are future work.

---

<a id="quickstart"></a>

## ⚡ Quick start

```bash
# 1. install
npm i solc@0.8.26 hardhat@2
pip install z3-solver web3 matplotlib

# 2. compile contracts & start a local chain
node compile.js
./node.sh 8545

# 3. run the full evaluation (260 scenarios × 5 defenses, ~15-20 min)
python3 eval/run.py --variants 10

# 4. summarize with confidence intervals
python3 eval/summarize.py
```

<details>
<summary><b>More experiments</b></summary>

```bash
python3 eval/session.py --sessions 5 --length 40 --arb   # benign multi-tx sessions (security ↔ liveness)
python3 agent/eval_episodes.py --port 8545               # replay LLM wallet-agent proposals under each defense
python3 eval/figures.py                                   # regenerate paper figures
```
</details>

---

## 🗂️ Repository map

```
📦 Proof-Gated-Signing
├── 📜 contracts/Testbed.sol     tokens, pools, vault, attack contracts, AgentWallet.executeChecked
├── 🛡️ pgs/
│   ├── guard.py                 simulate → extract effects → prove (Z3) → compile post-conditions
│   ├── scenarios.py             14 attack + 12 benign families
│   ├── defenses.py              no guard · allowlist · PGS-sim · PGS · ablation · LLM-judge replay
│   └── world.py                 deploys the test world on a local chain
├── 🧪 eval/
│   ├── run.py                   runs every scenario under every defense
│   ├── summarize.py             tables + Wilson 95% CIs
│   ├── session.py               benign multi-transaction sessions
│   ├── judge_prompt.md          LLM-judge instructions
│   └── figures.py               paper figures
├── 🤖 agent/                    CLI + episodes for LLM wallet agents, offline replay
├── 📊 results/                  raw per-run logs (jsonl), summaries, judge I/O, agent proposals
└── 📄 paper/                    LaTeX source + PDF
```

---

## 📚 Citation

```bibtex
@misc{ghosh2026proofgated,
  title  = {Proof-Gated Signing: Solver-Checked Transaction Guards that Hold Under State Drift for Onchain AI Agents},
  author = {Ghosh, Bravish},
  year   = {2026},
  note   = {Preprint}
}
```

<div align="center">
<sub>Built by <a href="https://github.com/LoopGlitch26">Bravish Ghosh</a> · Questions and issues welcome</sub>
</div>
