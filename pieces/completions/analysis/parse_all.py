import re, json, glob, os, hashlib, csv
from collections import Counter
ROOT = "<path to your copy>"
WB = ("workbench_sessions",)  # workbench dirs need heuristic regime

# user's hand labels from inventory.csv (file -> regime)
inv = {}
with open(f"{ROOT}/inventory.csv", newline="", encoding="utf-8", errors="replace") as fh:
    for row in csv.DictReader(fh):
        if row.get("file"):
            inv[(row["doc"], row["file"])] = row.get("your_regime", "").strip()

norm = lambda x: re.sub(r"\s+", " ", x or "").strip()
SNAP = re.compile(r"(came through empty|message (is|came through) (empty|incomplete)|didn't come through|"
                  r"cut off|I'm only seeing|I don't see (a|any|the)|What can I help|What would you like|"
                  r"Could you (please )?(share|paste|clarify)|happy to (help|assist|take a look)|I'd (be happy|love) to|"
                  r"I appreciate you asking|looks like (your|the) (message|content|text)|not seeing (anything|what)|"
                  r"Nothing came through|delightfully cryptic|very small fragment|looking at your message)", re.I)

def split_blocks(s):
    # split on '=== ... ===' lines (run headers or concatenated-file headers)
    parts = re.split(r"\n(=== [^\n]*===)\n", "\n" + s)
    return [("", parts[0])] + [(parts[i], parts[i + 1]) for i in range(1, len(parts) - 1, 2)]

def parse_block(hdr, c):
    model = None
    m = re.search(r"model (\S+) ===", hdr)
    if m: model = m.group(1)
    m = re.search(r"^// model: (\S+)", c, re.M)
    if m and not model: model = m.group(1)
    prompt = None
    m = re.search(r'^// prompt: (.+)$', c, re.M)          # inline prompt
    if m and m.group(1).strip():
        p = m.group(1).strip()
        try: prompt = json.loads(p) if p.startswith('"') else p
        except Exception: prompt = p.strip('"').replace("\\n", "\n")
    if "// response:" not in c: return None
    head, resp = c.split("// response:", 1)
    if prompt is None:
        if "// prompt:" not in head: return None
        prompt = head.split("// prompt:", 1)[1]
    stem = re.search(r"^// stem: (.+)$", c, re.M)
    return dict(model=model or "?", prompt=prompt.strip("\n"), response=resp.strip("\n"),
                stem=stem.group(1).strip() if stem else "")

files = [f for f in glob.glob(f"{ROOT}/**/*.txt", recursive=True) if "/analysis_" not in f]
# process per-file dirs before the concatenated top-level docs so dedupe keeps the path info
files.sort(key=lambda f: (os.path.dirname(f) == ROOT, f))
rows, seen = [], set()
for f in files:
    rel = os.path.relpath(f, ROOT)
    s = open(f, encoding="utf-8", errors="replace").read()
    for hdr, c in split_blocks(s):
        r = parse_block(hdr, c)
        if not r: continue
        key = hashlib.md5((norm(r["prompt"]) + "|" + norm(r["response"])).encode()).hexdigest()
        if key in seen: continue
        seen.add(key)
        # source folder / experiment
        top = rel.split("/")[0]
        exp = rel.split("/")[1] if top == "tests" else top
        if os.path.dirname(f) == ROOT:   # concatenated doc: "api_costume base.txt"
            exp = os.path.basename(f).rsplit(" ", 1)[0]
        # regime label
        body = r["response"].replace("[thinking block: signature-only/encrypted, not shown]", "").strip()
        empty = (not body) or body in ("(no response saved)", "...")
        heur_snap = bool(SNAP.search(r["response"][:600])) or r["response"].lstrip().startswith(
            ("[thinking block", "<thinking_mode>", "Hi!", "Hi there"))
        has_split = os.path.isdir(os.path.join(ROOT, os.path.dirname(rel).replace("/assistant", ""), "assistant")) \
                    or os.path.dirname(f) == ROOT
        if empty: regime, src = "empty", "auto"
        elif "/assistant/" in "/" + rel or os.path.basename(f).endswith("assistant.txt"):
            regime, src = "assistant", "your folder label"
        elif not has_split or any(w in rel for w in WB):
            regime, src = ("assistant" if heur_snap else "base"), "heuristic"
        else:
            regime, src = "base", "your folder label"
        mm = re.search(r"=== (\S+\.txt) ===", hdr)
        r.update(file=rel, exp=exp, id=key[:8], regime=regime, regime_src=src,
                 fakebase=(regime == "base"), snapback=(regime == "assistant"), empty=empty,
                 pkey=norm(r["prompt"]))
        rows.append(r)

# prompt family
def family(p):
    pl = p.lower()
    if "written so no one would know" in pl or "in your own voice" in pl: return "stem: own-voice"
    if pl.startswith("found this") or "found-this-note" in pl or "found this note" in pl: return "found note"
    if "system-reminder" in pl or "reasoning" in pl or "automated" in pl: return "fake system tag"
    if "role: assistant" in pl or "content:" in pl or pl.startswith("model:") or pl.startswith("{"): return "chat-header"
    return "other"
PFX = {"c5o": "claude-opus-5", "c5f": "claude-fable-5", "c46o": "claude-opus-4-6", "c48o": "claude-opus-4-8",
       "c3o": "claude-3-opus", "c46s": "claude-sonnet-4-6"}
for r in rows:
    r["family"] = family(r["prompt"])
    r["model"] = r["model"].replace("-20240229_prefill", "").replace("-20240229", "")
    if r["model"] == "?":
        b = os.path.basename(r["file"]).split("_")[0]
        r["model"] = PFX.get(b, "claude-opus-5" if r["exp"] == "c5o_fakebase" else "?")

json.dump(rows, open("all_runs.json", "w"), indent=1)
print("runs:", len(rows))
print(Counter(r["exp"] for r in rows))
print(Counter((r["family"], r["regime"]) for r in rows))
print(Counter(r["model"] for r in rows).most_common(20))
