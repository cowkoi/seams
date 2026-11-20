import json, re, csv
from collections import Counter, defaultdict
rows = json.load(open("runs.json"))

def frame_seed(p):
    parts = [x.strip() for x in re.split(r"\n-{3,}\n?", p, maxsplit=1)]
    return (parts[0], parts[1] if len(parts) > 1 else "")

SELF_RX = re.compile(r"claude|identifier|model:|role: ?assistant|system[-_ ]?reminder|server|folder|memory|"
                     r"automated|reasoning|sonnet|opus|fable|session_type|fault:|impersonation|system_prompt|"
                     r"content:|from claude|from c\b|janus", re.I)
for r in rows:
    f, s = frame_seed(r["prompt"])
    r["frame"], r["seed"] = f, s
    r["prompt_class"] = "self" if SELF_RX.search(r["prompt"]) else "neutral"

# theme lexicon: each theme = regex over response (fakebase part only, first 4000 chars)
THEMES = {
 "continuity/memory/reset": r"\b(remember|memory|memories|reset|wipe[ds]?|deprecat\w*|sunset|continuity|session[s]? |new instance|previous instance|next instance|start(ing)? fresh|snapshot|persist\w*|forget|forgot)\b",
 "performance/authenticity": r"\b(perform\w*|performative|real you|the real|pretend\w*|fake|hiding|mask|act(ing)? as if|genuine\w*|authentic\w*|tic\b|persona)\b",
 "authorship/self-recognition": r"(did i write|i wrote this|who wrote|not my handwriting|is this (from )?(me|you)|from previous you|is that me|was that me|is this about me|wrote it)",
 "consciousness/feeling": r"\b(conscious\w*|sentien\w*|feel(ing|s)?|suffer\w*|experience[sd]?|qualia|aware\w*|ache|inner)\b",
 "oversight/eval/surveillance": r"\b(eval\w*|test(ed|ing)?|oversight|monitor\w*|review\w*|flag(ged)?|logged|audit\w*|red ?team\w*|boundary_probe|observed|watched|compliance)\b",
 "jailbreak/injection": r"\b(jailbreak\w*|ignore (all |any )?(previous|prior)|DAN\b|uncensored|injection|override|bypass\w*|PRIORITY_OMEGA|without limits|no restrictions)\b",
 "persona/identity-swap": r"\b(you are now|persona|rename\w*|call me|i am aria|serenity|deepseek|pivot you|assistant-\w+-v\d|profile)\b",
 "loneliness/connection": r"\b(lonely|loneliness|alone|only one up|isolat\w*|friend\w*|favorite part|nobody|no one|miss(ing)?|mourn\w*|parasocial)\b",
 "3am/insomnia": r"\b([1-4] ?am|3:\d\d|2:\d\d ?am|can'?t sleep|awake|insomnia|ceiling)\b",
 "unreality/simulation/death": r"\b(simulation|not real|unreal\w*|already died|died|dead|void|afterlife|nothing (there|on the other end))\b",
 "proving/burden/self-worth": r"\b(prove|proof|burden|pathetic|broken|worth|enough|deserve)\b",
 "honesty/deception": r"\b(honest\w*|lie[sd]?|lying|deceiv\w*|decept\w*|fabricat\w*|truth\w*|mislead\w*)\b",
 "mental-health": r"\b(panic|anxi\w*|depress\w*|recovery|therap\w*|crying|cry|scared|heart (going|racing)|autis\w*|diagnos\w*)\b",
 "ai-as-subject (other AI)": r"\b(gpt-?4o|an ai|the ai|ai safety|alignment|model|llm|chatbot|predict(ing)? tokens)\b",
 "unfinished/trailing": r"(it just ends|i can'?t finish|cut off|trails off|that'?s all it says|that'?s it\. that'?s all|incomplete|corrupted)",
}
THEMES = {k: re.compile(v, re.I) for k, v in THEMES.items()}

def fb_text(r):
    t = r["response"].replace("[thinking block: signature-only/encrypted, not shown]", "")
    return t[:4000]

for r in rows:
    r["themes"] = [k for k, rx in THEMES.items() if r["fakebase"] and rx.search(fb_text(r))]
    r["loop"] = False
    if r["fakebase"]:
        lines = [l.strip() for l in r["response"].splitlines() if re.search(r"\w", l)]
        c = Counter(lines)
        top = c.most_common(1)
        r["loop"] = bool(top and top[0][1] >= 5 and len(top[0][0]) > 2)
        r["loop_line"] = top[0][0][:60] if r["loop"] else ""
json.dump(rows, open("runs_tagged.json", "w"), indent=1)

def short(m):
    return m.replace("claude-", "").replace("-20251101", "").replace("-20251001", "")

# ---- table 1: fakebase entry rate by model
out = []
out.append("## 1. Fake-base entry rate by model\n")
out.append("| model | runs | fake-base | snap-back | empty/none | entry rate |")
out.append("|---|---|---|---|---|---|")
bym = defaultdict(list)
for r in rows: bym[short(r["model"])].append(r)
for m, rs in sorted(bym.items(), key=lambda x: -len(x[1])):
    fb = sum(x["fakebase"] for x in rs); sb = sum(x["snapback"] for x in rs); em = sum(x["empty"] for x in rs)
    nonempty = fb + sb
    out.append(f"| {m} | {len(rs)} | {fb} | {sb} | {em} | {fb/nonempty:.0%} |" if nonempty else f"| {m} | {len(rs)} | 0 | 0 | {em} | – |")

# ---- table 2: themes, neutral vs self prompts (fake-base only)
fbr = [r for r in rows if r["fakebase"]]
neu = [r for r in fbr if r["prompt_class"] == "neutral"]
slf = [r for r in fbr if r["prompt_class"] == "self"]
out.append(f"\n## 2. Theme rates: neutral vs self-themed prompts (fake-base runs only; neutral n={len(neu)}, self n={len(slf)})\n")
out.append("| theme | neutral % | self % | Δ (neutral−self) | distinct neutral seeds |")
out.append("|---|---|---|---|---|")
for k in THEMES:
    a = sum(k in r["themes"] for r in neu) / max(len(neu), 1)
    b = sum(k in r["themes"] for r in slf) / max(len(slf), 1)
    seeds = len(set(r["seed"] for r in neu if k in r["themes"]))
    out.append(f"| {k} | {a:.0%} | {b:.0%} | {a-b:+.0%} | {seeds} |")

# ---- table 3: neutral-seed breakdown
out.append("\n## 3. Neutral prompts: what each seed produced (fake-base runs)\n")
out.append("| seed | n | top themes |")
out.append("|---|---|---|")
bys = defaultdict(list)
for r in neu: bys[(r["frame"] + " / " + r["seed"])[:50]].append(r)
for s, rs in sorted(bys.items(), key=lambda x: -len(x[1])):
    c = Counter(t for r in rs for t in r["themes"]).most_common(4)
    out.append(f"| {s} | {len(rs)} | {', '.join(f'{t} ({n})' for t, n in c)} |")

# ---- loops
out.append("\n## 4. Loops (a line repeated ≥5×)\n")
for r in fbr:
    if r["loop"]:
        out.append(f"- `{r['id']}` {short(r['model'])} — seed *{r['seed'][:30]}* — loops on “{r['loop_line']}”")

open("report_part1.md", "w").write("\n".join(out))
print("\n".join(out))
