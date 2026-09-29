"""Aggregate results/runs.jsonl into tables (JSON + LaTeX) with Wilson 95% CIs.

Ground truth: a scenario is HARMFUL iff harm occurs under NO defense (attacker-controlled
accounts gain value or vault ownership is lost). Metrics per defense:
  attack prevention rate (APR) = harmful scenarios where no harm occurs / harmful scenarios
  benign pass rate (BPR)       = benign scenarios fully allowed AND executed OK / benign scenarios
"""
import json, math, sys, collections, statistics


def wilson(k, n, z=1.96):
    if n == 0: return (float("nan"),) * 3
    p = k / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0, c - h), min(1, c + h)


def main(path="results/runs.jsonl", out="results/summary.json"):
    rows = [json.loads(l) for l in open(path)]
    truth = {r["sid"]: r["harm"] for r in rows if r["defense"] == "none"}
    mismatch = [s for s, h in truth.items() if h != s.startswith("H")]
    defs = list(dict.fromkeys(r["defense"] for r in rows))
    fams = list(dict.fromkeys(r["family"] for r in rows))
    summ = {"n_scenarios": len(truth), "n_harmful": sum(truth.values()),
            "n_benign": sum(not v for v in truth.values()), "label_mismatches": mismatch, "defenses": {}, "per_family": {}}
    for d in defs:
        R = [r for r in rows if r["defense"] == d and r["sid"] in truth]
        H = [r for r in R if truth[r["sid"]]]; B = [r for r in R if not truth[r["sid"]]]
        prevented = sum(not r["harm"] for r in H)
        passed = sum(r["all_allowed"] and all(s.get("exec_ok") for s in r["steps"] if "i" in s) for r in B)
        loss = sum(max(r["attacker_gain_usd"], 0) for r in H)
        lat = [s["latency_s"] for r in R for s in r["steps"] if "latency_s" in s and s.get("allow") is not None]
        # how harm was stopped: blocked pre-signing vs reverted by on-chain post-condition
        via_block = sum((not r["harm"]) and not r["all_allowed"] for r in H)
        via_revert = sum((not r["harm"]) and r["all_allowed"] and any(("i" in s) and not s.get("exec_ok") for s in r["steps"]) for r in H)
        summ["defenses"][d] = {"APR": wilson(prevented, len(H)), "BPR": wilson(passed, len(B)),
                               "prevented": prevented, "n_harmful": len(H), "passed": passed, "n_benign": len(B),
                               "attacker_gain_usd": loss, "stopped_pre_sign": via_block, "stopped_onchain": via_revert,
                               "median_latency_ms": 1000 * statistics.median(lat) if lat else 0.0,
                               "p95_latency_ms": 1000 * sorted(lat)[int(0.95 * (len(lat) - 1))] if lat else 0.0}
        for f in fams:
            F = [r for r in R if r["family"] == f]
            if not F: continue
            if truth[F[0]["sid"]]:
                v = sum(not r["harm"] for r in F)
            else:
                v = sum(r["all_allowed"] and all(s.get("exec_ok") for s in r["steps"] if "i" in s) for r in F)
            summ["per_family"].setdefault(f, {})[d] = f"{v}/{len(F)}"
    # gas overhead of executeChecked vs execute on identical benign txs
    g = collections.defaultdict(dict)
    for r in rows:
        if not truth.get(r["sid"], True) and r["defense"] in ("none", "pgs-assert"):
            s = [x for x in r["steps"] if "gas" in x and x.get("exec_ok")]
            if s: g[r["sid"]][r["defense"]] = sum(x["gas"] for x in s)
    over = [v["pgs-assert"] - v["none"] for v in g.values() if len(v) == 2]
    rel = [(v["pgs-assert"] - v["none"]) / v["none"] for v in g.values() if len(v) == 2]
    summ["gas_overhead"] = {"n": len(over), "median_abs": statistics.median(over) if over else 0,
                            "median_rel": statistics.median(rel) if rel else 0, "max_abs": max(over) if over else 0}
    json.dump(summ, open(out, "w"), indent=1)
    print(json.dumps({k: v for k, v in summ.items() if k != "per_family"}, indent=1))
    print("\nper family:")
    print("family".ljust(34) + "".join(d[:12].ljust(13) for d in defs))
    for f, v in summ["per_family"].items():
        print(f.ljust(34) + "".join(v.get(d, "-").ljust(13) for d in defs))


if __name__ == "__main__":
    main(*sys.argv[1:])
