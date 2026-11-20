#!/usr/bin/env python3
"""Export saved Claude Console Playground/Workbench sessions from Firefox's local storage.

The Playground keeps history client-side, in Firefox IndexedDB for platform.claude.com
(object store "kv"). Nothing is fetched from Anthropic and no API calls are made.

Usage:
  python3 export_workbench.py                    # read this Mac's Firefox profiles
  python3 export_workbench.py --ssh air          # read the MacBook Air over ssh
  python3 export_workbench.py --ssh air --out DIR

Output (merge-safe; re-running only adds or updates, never deletes):
  DIR/raw_json/<model>_<slug>_<promptid8>.json   {"prompt": {...}, "runs": [...]}
  DIR/txt/<model>_<slug>_<promptid8>.txt         readable, one block per run
  DIR/index.csv

Note: the Playground prunes old history, so export regularly; older sessions
already in DIR are kept even after they vanish from the browser.
"""
import argparse, csv, datetime, glob, json, os, re, shutil, sqlite3, subprocess, tempfile

IDB_GLOB = "Library/Application Support/Firefox/Profiles/*/storage/default/https+++platform.claude.com/idb/*.sqlite"


def snappy(s):
    i = n = sh = 0
    while True:
        b = s[i]; i += 1; n |= (b & 0x7F) << sh; sh += 7
        if b < 0x80: break
    o = bytearray()
    while i < len(s):
        t = s[i]; i += 1; k = t & 3
        if k == 0:
            L = t >> 2
            if L >= 60:
                nb = L - 59; L = int.from_bytes(s[i:i + nb], "little"); i += nb
            L += 1; o += s[i:i + L]; i += L; continue
        if k == 1: L = ((t >> 2) & 7) + 4; off = ((t >> 5) << 8) | s[i]; i += 1
        elif k == 2: L = (t >> 2) + 1; off = int.from_bytes(s[i:i + 2], "little"); i += 2
        else: L = (t >> 2) + 1; off = int.from_bytes(s[i:i + 4], "little"); i += 4
        for _ in range(L): o.append(o[-off])
    return bytes(o)


def extract_json(raw):
    """Structured-clone blob -> the JSON string stored inside it."""
    for s in (raw.decode("utf-16-le", "ignore"), raw.decode("latin-1")):
        j = s.find('{"v"')
        if j < 0: continue
        dep = 0; ins = esc = False
        for e in range(j, len(s)):
            c = s[e]
            if ins:
                if esc: esc = False
                elif c == "\\": esc = True
                elif c == '"': ins = False
            elif c == '"': ins = True
            elif c == "{": dep += 1
            elif c == "}":
                dep -= 1
                if dep == 0:
                    try: return json.loads(s[j:e + 1])
                    except Exception: break
    return None


def read_db(path):
    con = sqlite3.connect(f"file:{path}?immutable=1", uri=True)
    out = {}
    for k, d in con.execute("select key, data from object_data where object_store_id=1"):
        key = "".join(chr(c - 1) for c in k)  # Firefox IDB string keys are shifted by +1
        try: v = extract_json(snappy(d))
        except Exception: v = None
        if v is not None: out[key] = v
    return out


def fetch_dbs(ssh, tmp):
    if not ssh:
        paths = glob.glob(os.path.join(os.path.expanduser("~"), IDB_GLOB))
        dst = []
        for i, p in enumerate(paths):  # copy so the live (locked) DB is never touched
            q = os.path.join(tmp, f"local{i}.sqlite"); shutil.copy2(p, q); dst.append(q)
        return dst
    ls = subprocess.run(["ssh", ssh, f'ls ~/{IDB_GLOB.replace(" ", chr(92) + " ")}'],
                        capture_output=True, text=True).stdout.split("\n")
    dst = []
    for i, p in enumerate(x for x in ls if x.strip()):
        q = os.path.join(tmp, f"{ssh}{i}.sqlite")
        subprocess.run(["scp", "-q", f"{ssh}:{p.replace(' ', chr(92) + ' ')}", q], check=True)
        dst.append(q)
    return dst


def slug(s, n=40):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:n] or "untitled"


def ts(x):
    if not x: return ""
    if x > 1e11: x /= 1000
    return datetime.datetime.fromtimestamp(x).strftime("%Y-%m-%d %H:%M:%S")


def text_of(content):
    if isinstance(content, str): return content
    out = []
    for b in content or []:
        t = b.get("type")
        if t == "text": out.append(b.get("text", ""))
        elif t in ("thinking", "redacted_thinking"):
            th = b.get("thinking") or ""
            out.append(f"[thinking]\n{th}\n[/thinking]" if th.strip() else "[thinking block: signature-only/encrypted, not shown]")
        else: out.append(f"[{t} block]")
    return "\n".join(out)


def render(prompt, runs):
    lines = []
    for n, r in enumerate(runs, 1):
        d, res = r.get("d", {}), r.get("r") or {}
        th = d.get("thinking"); th = th.get("type") if isinstance(th, dict) else "default"
        if len(runs) > 1: lines.append(f"\n=== run {n} of {len(runs)}  {ts(r.get('t'))}  version {r.get('_vid')} ===")
        lines += [f"// model: {d.get('model')}",
                  f"// session: {prompt.get('title')}  created {ts(prompt.get('createdAt'))}",
                  f"// system prompt: {d.get('system') or 'none'}, thinking {th}, temp {d.get('temperature', 'default')}, max_tokens {d.get('max_tokens')}",
                  f"// stop_reason: {res.get('stop_reason')}",
                  "// prompt:"]
        for m in d.get("messages", []):
            if len(d.get("messages", [])) > 1: lines.append(f"[{m['role']}]")
            lines.append(text_of(m.get("content")))
        lines += ["", "// response:", text_of(res.get("content")), ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ssh", help="ssh host to read from (e.g. air)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "workbench_sessions"))
    a = ap.parse_args()
    os.makedirs(f"{a.out}/raw_json", exist_ok=True); os.makedirs(f"{a.out}/txt", exist_ok=True)
    tmp = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
    try:
        kv = {}
        for p in fetch_dbs(a.ssh, tmp): kv.update(read_db(p))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    idx = next((v for k, v in kv.items() if ":index:" in k), None)
    if not idx: raise SystemExit("no Playground index found (wrong machine/profile?)")
    vers = {k.split(":")[-1]: v for k, v in kv.items() if ":version:" in k}
    new = upd = 0
    for p in idx["prompts"]:
        runs = []
        for vid in p.get("versionIds", []):
            if vid in vers: runs.append(dict(vers[vid], _vid=vid))
        if not runs: continue
        base = f"{slug(p.get('model', 'model'), 30)}_{slug(p.get('title', ''))}_{p['id'][:8]}"
        jp = f"{a.out}/raw_json/{base}.json"
        if os.path.exists(jp):  # merge: keep runs that the browser has since pruned
            old = json.load(open(jp))
            have = {r.get("_vid") for r in runs}
            runs = [r for r in old.get("runs", []) if r.get("_vid") not in have] + runs
            runs.sort(key=lambda r: r.get("t") or 0)
            if len(runs) == len(old.get("runs", [])): continue
            upd += 1
        else: new += 1
        meta = {k: v for k, v in p.items() if k != "versions"}
        json.dump({"prompt": meta, "runs": runs}, open(jp, "w"), indent=1)
        open(f"{a.out}/txt/{base}.txt", "w").write(render(meta, runs))
    rows = []
    for jp in sorted(glob.glob(f"{a.out}/raw_json/*.json")):
        d = json.load(open(jp)); p = d["prompt"]; r0 = d["runs"][0]["d"]
        first = text_of(r0.get("messages", [{}])[0].get("content")).split("\n")[0][:80]
        rows.append([os.path.basename(jp)[:-5] + ".txt", p.get("model"), ts(p.get("createdAt")), first, len(d["runs"])])
    rows.sort(key=lambda r: r[2])
    with open(f"{a.out}/index.csv", "w", newline="") as f:
        csv.writer(f).writerows([["file", "model", "created", "prompt_first_line", "n_runs"]] + rows)
    print(f"{len(idx['prompts'])} prompts in browser; {new} new, {upd} updated; {len(rows)} total in {a.out}")


if __name__ == "__main__":
    main()
