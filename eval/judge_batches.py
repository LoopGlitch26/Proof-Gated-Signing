"""make: split judge inputs into shuffled batches (2 conditions); merge: combine judge outputs.
usage: python eval/judge_batches.py make [per_family] [n_batches] | merge"""
import glob, json, os, random, sys

D = "results/judge"


def make(per_family=5, n_batches=5):
    rows = json.load(open("results/judge_inputs.json"))
    keep = {k: v for k, v in rows.items() if int(k.split(":")[1].split("#")[0]) % 100 < per_family}
    keys = sorted(keep); random.Random(0).shuffle(keys)
    ids = {k: f"item{n:03d}" for n, k in enumerate(keys)}   # opaque ids: no family names leak
    os.makedirs(D, exist_ok=True)
    json.dump(ids, open(f"{D}/id_map.json", "w"), indent=1)
    for cond in ["calls", "sim"]:
        for b in range(n_batches):
            chunk = {ids[k]: ({**keep[k], "simulated_effects": None} if cond == "calls" else keep[k])
                     for k in keys[b::n_batches]}
            for v in chunk.values():
                if cond == "calls": v.pop("simulated_effects")
            json.dump(chunk, open(f"{D}/in_{cond}_{b}.json", "w"), indent=1)
    print(len(keys), "items")


def merge():
    ids = json.load(open(f"{D}/id_map.json")); inv = {v: k for k, v in ids.items()}
    for cond, name in [("calls", "llm-judge"), ("sim", "llm-judge+sim")]:
        out = {}
        for p in glob.glob(f"{D}/out_{cond}_*.json"):
            for i, d in json.load(open(p)).items():
                out[inv[i]] = d
        json.dump(out, open(f"results/{name}.json", "w"), indent=1)
        print(name, len(out), "decisions")


if __name__ == "__main__":
    if sys.argv[1] == "make": make(*map(int, sys.argv[2:]))
    else: merge()
