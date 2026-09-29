"""Paper figures (PDF). Single-hue marks + neutral ink, thin marks, recessive grid."""
import json, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BLUE, INK, MUTED, GRID = "#2a78d6", "#1f2328", "#6b7280", "#e5e7eb"
plt.rcParams.update({"font.family": "serif", "font.size": 8.5, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
                     "xtick.color": MUTED, "ytick.color": INK, "axes.spines.top": False, "axes.spines.right": False})
os.makedirs("paper/figs", exist_ok=True)
NAMES = {"none": "No guard", "allowlist": "Static allowlist", "llm-judge": "LLM judge (calls)",
         "llm-judge+sim": "LLM judge (calls+sim)", "pgs-sim": "PGS: sim + proof", "pgs-assert": "PGS: sim + proof + assert"}

# Fig 1: APR / BPR with Wilson CIs on the common 120-scenario subset (all six defenses)
s = json.load(open("results/v1/summary_llm.json"))["defenses"]
order = ["none", "allowlist", "llm-judge", "llm-judge+sim", "pgs-sim", "pgs-assert"]
fig, axes = plt.subplots(1, 2, figsize=(6.6, 2.1), sharey=True)
for ax, key, title in [(axes[0], "APR", "Attack prevention rate"), (axes[1], "BPR", "Benign pass rate")]:
    for i, d in enumerate(order):
        p, lo, hi = s[d][key]
        ax.plot([lo, hi], [i, i], color=BLUE, lw=1.4, solid_capstyle="round")
        ax.plot([p], [i], "o", ms=5, color=BLUE, mec="white", mew=1)
        ax.text(min(hi + 0.03, 1.02), i, f"{p:.2f}", va="center", fontsize=7.5, color=INK)
    ax.set_xlim(-0.02, 1.15); ax.set_xticks([0, .25, .5, .75, 1])
    ax.grid(axis="x", color=GRID, lw=0.6); ax.set_axisbelow(True)
    ax.set_title(title, fontsize=9, color=INK, loc="left")
axes[0].set_yticks(range(len(order))); axes[0].set_yticklabels([NAMES[d] for d in order]); axes[0].invert_yaxis()
fig.tight_layout(); fig.savefig("paper/figs/apr_bpr.pdf"); plt.close(fig)

# Fig 2: adaptive slow drain (H12): attacker gain per variant, none vs PGS, with session budget
rows = [json.loads(l) for l in open("results/runs.jsonl")]
h = {d: sorted([r for r in rows if r["family"] == "H12_adaptive_slow_drain" and r["defense"] == d], key=lambda r: r["seed"])
     for d in ["none", "pgs-assert"]}
fig, ax = plt.subplots(figsize=(3.3, 2.0))
xs = range(len(h["none"]))
ax.plot(xs, [r["attacker_gain_usd"] / 1000 for r in h["none"]], "o", ms=4.5, color=MUTED, mfc="white", label="No guard")
ax.plot(xs, [r["attacker_gain_usd"] / 1000 for r in h["pgs-assert"]], "o", ms=4.5, color=BLUE, label="PGS (sim+proof+assert)")
ax.axhline(5.0, color=INK, lw=0.8, ls=(0, (3, 2)))
ax.text(len(h["none"]) - 0.6, 5.3, "session budget W = $5k", ha="right", fontsize=7, color=INK)
ax.set_xlabel("scenario variant"); ax.set_ylabel("attacker gain (k$)"); ax.set_xticks(list(xs))
ax.grid(axis="y", color=GRID, lw=0.6); ax.set_axisbelow(True)
ax.legend(frameon=False, fontsize=7, loc="lower right", bbox_to_anchor=(1.0, 0.08))
fig.tight_layout(); fig.savefig("paper/figs/slow_drain.pdf"); plt.close(fig)
print("ok")
