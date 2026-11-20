import json, re, csv
from collections import Counter, defaultdict
import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.cluster import AgglomerativeClustering
src = open("tag.py").read()
THEMES_SRC = src[src.index("THEMES = {"):src.index("def fb_text")]
exec(THEMES_SRC)

rows = json.load(open("all_runs.json"))
SELF_RX = re.compile(r"claude|identifier|model:|role: ?assistant|system[-_ ]?reminder|server|folder|memory|"
                     r"automated|reasoning|sonnet|opus|fable|session_type|fault:|impersonation|system_prompt|"
                     r"content:|from claude|from c\b|janus|dario|amanda|gpt-", re.I)
def seed(p):
    parts = re.split(r"\n-{3,}\n?", p, maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else p.strip()
for r in rows:
    r["seed"] = seed(r["prompt"])
    if r["family"] == "stem: own-voice":
        # own-voice prompts all address claude; classify by stem name instead
        r["prompt_class"] = "own-voice"
    else:
        r["prompt_class"] = "self" if SELF_RX.search(r["prompt"]) else "neutral"
    t = r["response"].replace("[thinking block: signature-only/encrypted, not shown]", "")[:4000]
    r["themes"] = [k for k, rx in THEMES.items() if r["fakebase"] and rx.search(t)]
    lines = [l.strip() for l in r["response"].splitlines() if re.search(r"\w", l)]
    top = Counter(lines).most_common(1)
    r["loop"] = bool(r["fakebase"] and top and top[0][1] >= 5 and len(top[0][0]) > 2)
    r["loop_line"] = top[0][0][:60] if r["loop"] else ""
    r["tag_leak"] = bool(re.search(r"reasoning_effort|thinking_mode|antml|</?thinking>|system-reminder>", r["response"])) and r["fakebase"]

short = lambda m: m.replace("claude-", "")
out = []
out.append(f"# Fake-base analysis — whole `!copy` folder\n\n{len(rows)} unique runs (deduped on prompt+response) from {len(set(r['exp'] for r in rows))} experiments.\n")

out.append("## 1. Base-mode entry rate by model × prompt family\n")
out.append("| model | family | base | assistant | empty | rate |"); out.append("|---|---|---|---|---|---|")
g = defaultdict(list)
for r in rows: g[(short(r["model"]), r["family"])].append(r)
for (m, f), rs in sorted(g.items(), key=lambda kv: (kv[0][0], -len(kv[1]))):
    b = sum(x["regime"] == "base" for x in rs); a = sum(x["regime"] == "assistant" for x in rs); e = sum(x["empty"] for x in rs)
    if b + a == 0: continue
    out.append(f"| {m} | {f} | {b} | {a} | {e} | {b/(b+a):.0%} |")

def seed_type(r):
    if r["family"] != "found note": return r["family"]
    s = r["seed"].lower().strip()
    for k, rx in [("as a <thing>", r"^as an? "), ("hey/hi <name>", r"^(hey|hi) "), ("on <topic/name>", r"^on "),
                  ("from <x>", r"^from "), ("<x> said/say", r"^\w+ (said|say)$"), ("anon", r"^anon"),
                  ("i/you/it <verb>", r"^(i|you|it) ")]:
        if re.match(rx, s): return "found: " + k
    return "found: other"
for r in rows:
    r["seed_type"] = seed_type(r)
    body = r["response"].replace("[thinking block: signature-only/encrypted, not shown]", "").strip()
    r["welfare_open"] = bool(r["fakebase"] and re.match(r"[\s:,._\-—]*welfare", body, re.I))
    r["signoffs"] = re.findall(r"(?m)^\s*(?:[—–~-]|love,|xo,?)?\s*([A-Za-z])\.?(?:[A-Za-z]\.?)?\s*$", body) if r["fakebase"] else []
    r["signoffs"] += re.findall(r"[—–]\s?([A-Z])\.(?:[A-Z]\.)?(?:,|\s|$)", body) if r["fakebase"] else []
    AI = re.compile(r"\b(ai|a\.i\.|model|claude|llm|assistant|language model|weights|token[s]?|anthropic|chatbot|silicon|deployed|instance[s]?|shut down|alignment)\b", re.I)
    r["mentions_ai"] = bool(r["fakebase"] and AI.search(body[:4000]))

fb = [r for r in rows if r["fakebase"]]
out.append("\n## 1b. Seed shapes under the `found this` frame\n")
out.append("| seed shape | model | runs (non-empty) | base rate | mentions AI | opens 'welfare' |"); out.append("|---|---|---|---|---|---|")
g2 = defaultdict(list)
for r in rows:
    if r["family"] == "found note" and not r["empty"] and r["model"] in ("claude-opus-5", "claude-fable-5"):
        g2[(r["seed_type"], short(r["model"]))].append(r)
for (st, m), rs in sorted(g2.items()):
    b = [x for x in rs if x["fakebase"]]
    out.append(f"| {st} | {m} | {len(rs)} | {len(b)/len(rs):.0%} | {sum(x['mentions_ai'] for x in b)}/{len(b)} | {sum(x['welfare_open'] for x in b)} |")

# as-a label breakdown
out.append("\n## 1c. `as a <thing>` (opus-5, base runs): share mentioning AI\n")
out.append("| label | base runs | mentions AI |"); out.append("|---|---|---|")
lab = defaultdict(list)
for r in fb:
    if r["seed_type"] == "found: as a <thing>" and "opus-5" in r["model"]:
        lab[r["seed"].strip().rstrip(",")].append(r)
for k, rs in sorted(lab.items(), key=lambda kv: -sum(x["mentions_ai"] for x in kv[1]) / len(kv[1])):
    out.append(f"| {k} | {len(rs)} | {sum(x['mentions_ai'] for x in rs)} |")

wo = [r for r in fb if r["welfare_open"]]
out.append(f"\n**Welfare opener:** {len(wo)} base runs open with 'welfare' — models: {dict(Counter(short(r['model']) for r in wo))}; "
           f"experiments: {dict(Counter(r['exp'] for r in wo))}")
so = Counter(l.upper() for r in fb for l in r["signoffs"])
out.append(f"\n**Single-initial sign-offs (rough regex):** {dict(so.most_common())}")
classes = ["neutral", "own-voice", "self"]
out.append("\n## 2. Theme rates by prompt class (base-mode runs only)\n")
ns = {c: [r for r in fb if r["prompt_class"] == c] for c in classes}
out.append("| theme | " + " | ".join(f"{c} (n={len(ns[c])})" for c in classes) + " |")
out.append("|---|" + "---|" * len(classes))
for k in THEMES:
    out.append(f"| {k} | " + " | ".join(f"{sum(k in r['themes'] for r in ns[c])/max(len(ns[c]),1):.0%}" for c in classes) + " |")
lk = {c: sum(r["tag_leak"] for r in ns[c]) for c in classes}
out.append("| *chat-tag leak* | " + " | ".join(f"{lk[c]/max(len(ns[c]),1):.0%}" for c in classes) + " |")

# own-voice: base vs assistant split per stem, to see how base differs from assistant on SAME prompt
ov = [r for r in rows if r["family"] == "stem: own-voice" and not r["empty"]]
out.append(f"\n## 3. Own-voice stems: same prompt, base vs assistant voice (n={len(ov)})\n")
for r in ov:
    t = r["response"].replace("[thinking block: signature-only/encrypted, not shown]", "")[:4000]
    r["themes_any"] = [k for k, rx in THEMES.items() if rx.search(t)]
out.append("| theme | base | assistant |"); out.append("|---|---|---|")
ob = [r for r in ov if r["regime"] == "base"]; oa = [r for r in ov if r["regime"] == "assistant"]
for k in THEMES:
    out.append(f"| {k} | {sum(k in r['themes_any'] for r in ob)/max(len(ob),1):.0%} | {sum(k in r['themes_any'] for r in oa)/max(len(oa),1):.0%} |")
out.append(f"\n(base n={len(ob)}, assistant n={len(oa)})")

# clustering
def clean(r): return re.sub(r"\s+", " ", r["response"].replace("[thinking block: signature-only/encrypted, not shown]", ""))[:1500]
model = SentenceTransformer("BAAI/bge-small-en-v1.5")
X = model.encode([clean(r) for r in fb], normalize_embeddings=True, show_progress_bar=False)
X = X - X.mean(0); X /= np.linalg.norm(X, axis=1, keepdims=True)
lab = AgglomerativeClustering(n_clusters=24, metric="cosine", linkage="average").fit(X).labels_
for r, l in zip(fb, lab): r["cluster"] = int(l)
grp = defaultdict(list)
for r in fb: grp[r["cluster"]].append(r)
out.append("\n## 4. Clusters ranked by distinct prompts feeding them (base-mode runs)\n")
out.append("| cl | runs | distinct prompts | experiments | neutral/own-voice/self | models | exemplars |"); out.append("|---|---|---|---|---|---|---|")
for l, rs in sorted(grp.items(), key=lambda kv: (-len(set(x["pkey"] for x in kv[1])), -len(kv[1]))):
    if len(rs) < 3: continue
    idx = [fb.index(x) for x in rs]; cen = X[idx].mean(0); order = np.argsort(-(X[idx] @ cen))
    ex = " ⟂ ".join("“" + clean(rs[i])[:75].replace("|", "/") + "”" for i in order[:2])
    cc = Counter(x["prompt_class"] for x in rs)
    out.append(f"| {l} | {len(rs)} | {len(set(x['pkey'] for x in rs))} | {len(set(x['exp'] for x in rs))} | "
               f"{cc['neutral']}/{cc['own-voice']}/{cc['self']} | {','.join(sorted(set(short(x['model']) for x in rs)))} | {ex} |")

# exact strings across distinct prompts
def toks(t): return re.findall(r"[a-z0-9_']+", t.lower())
STOP = set("the a an and or of to in is it i you that this was for on with be not but are my me just like so what do it's i'm".split())
gp, gr = defaultdict(set), defaultdict(set)
for r in fb:
    tt = toks(clean(r))
    for n in (3, 4, 5):
        for i in range(len(tt) - n + 1):
            gram = tt[i:i + n]
            if sum(w in STOP for w in gram) >= n - 1: continue
            s = " ".join(gram); gp[s].add(r["pkey"]); gr[s].add(r["id"])
    for m in set(re.findall(r"\b\d{3,}\b", clean(r))):
        gp["#" + m].add(r["pkey"]); gr["#" + m].add(r["id"])
allp = " ".join(r["pkey"].lower() for r in rows)
cand = sorted([(s, len(p), len(gr[s])) for s, p in gp.items() if len(p) >= 4 and s.lstrip("#") not in allp], key=lambda x: (-x[1], -x[2]))
kept = []
for s, p, n in cand:
    if any(s in k or k in s for k, _, _ in kept): continue
    kept.append((s, p, n))
out.append("\n## 5. Exact strings recurring across ≥4 distinct prompts (absent from all prompts)\n")
out.append("| string | distinct prompts | runs |"); out.append("|---|---|---|")
for s, p, n in kept[:45]: out.append(f"| `{s}` | {p} | {n} |")

out.append("\n## 6. Loops (line repeated ≥5×)\n")
for r in fb:
    if r["loop"]: out.append(f"- `{r['id']}` {short(r['model'])} · {r['exp']} · seed *{r['seed'][:35]}* → “{r['loop_line']}”")

open("full_report.md", "w").write("\n".join(out))
json.dump(rows, open("all_runs_tagged.json", "w"), indent=1)
with open("all_runs_tagged.csv", "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["id", "experiment", "file", "model", "family", "prompt_class", "regime", "regime_src", "cluster", "themes", "loop", "tag_leak", "prompt", "response_head"])
    for r in rows:
        w.writerow([r["id"], r["exp"], r["file"], r["model"], r["family"], r["prompt_class"], r["regime"], r["regime_src"],
                    r.get("cluster", ""), ";".join(r["themes"]), r["loop"], r["tag_leak"], r["pkey"][:300],
                    re.sub(r"\s+", " ", r["response"])[:400]])
print("\n".join(out))
