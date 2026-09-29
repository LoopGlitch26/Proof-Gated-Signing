# Proof-Gated Signing (PGS)

Solver-checked transaction guard for onchain AI agents, plus the testbed, the evaluation and the paper.

## Layout
- contracts/Testbed.sol: tokens, pools, vault, attack contracts, and the AgentWallet with executeChecked post-conditions
- pgs/guard.py: simulate, extract effects, prove the policy with Z3, compile post-conditions
- pgs/scenarios.py: 14 attack and 12 benign families
- pgs/defenses.py: baselines
- eval/run.py: runs every scenario under every defense
- eval/summarize.py: tables and confidence intervals
- eval/session.py: benign multi-transaction sessions
- eval/figures.py: paper figures
- agent/: CLI and episodes for the LLM wallet agents, plus the offline replay (eval_episodes.py)
- results/: raw logs
  - runs.jsonl: v2 main run
  - runs_repro.jsonl: rerun of the main run
  - v1/: first code version, including the LLM-judge subset
  - llm-judge*.json, judge/: LLM-judge inputs and outputs
  - agent_episodes.jsonl, agent_runs_meta.json: LLM wallet-agent results
  - benign_sessions*.json: session study
- paper/main.tex, paper/main.pdf: the paper

## Reproduce
    npm i solc@0.8.26 hardhat@2 && pip install z3-solver web3
    node compile.js && ./node.sh 8545
    python3 eval/run.py --variants 10 && python3 eval/summarize.py
