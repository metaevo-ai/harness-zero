# verify_answer

> Output-contract battery for /app/answer.txt, with an RDKit-canonical rewrite of the final file.

Use after the last chosen write to verify the output contract and obtain
canonical SMILES from the actual file. Format and parsing checks catch concrete
submission problems. Atom-budget/MCS checks are diagnostics whose assumptions
must be examined; they do not prove chemistry correctness, and reagent
inclusion cannot be inferred from balanced counts alone. If new evidence
reveals a wrong choice, repair it and verify again; otherwise finish with
visible text instead of repeated checks. Do not call this a reference match.

```bash
python3 -c "import rdkit" 2>/dev/null || pip install -q rdkit
mkdir -p .agent-tools
cat > .agent-tools/verify_answer.py <<'PY'
import json, os, re, sys
spec = json.loads(sys.argv[1])
path = spec["path"]
fails = 0

def report(ok, label):
    global fails
    fails += not ok
    print(("PASS" if ok else "FAIL") + " " + label)

if not os.path.exists(path):
    print(f"FAIL exists: {path}: no such file — a missing answer file scores 0")
    sys.exit(1)
report(True, f"exists: {path}")

raw = open(path, "rb").read()
report(bool(raw.strip()), f"non-empty: {len(raw)}B")
if not raw.strip():
    sys.exit(1)

text = raw.decode("utf-8", errors="replace")
lines = [ln for ln in text.splitlines() if ln.strip()]
report(len(lines) == 1, f"single line ({len(lines)} non-empty lines)")
line = lines[0].strip()
report(line == lines[0] or True, f"content: {line[:200]!r}")

frags = line.split(".")
report(all(f.strip() for f in frags), f"no empty fragments ({len(frags)} fragments)")
frags = [f.strip() for f in frags]
report(not any(re.search(r"\s", f) for f in frags), "no whitespace inside fragments")

MAP_RE = re.compile(r":\d+\]")
mapped = [f for f in frags if MAP_RE.search(f)]
report(not mapped, f"no atom-mapping numbers ({len(mapped)} fragments mapped: {mapped[:2]})")

def boot():
    try:
        from rdkit import Chem
        return Chem
    except ImportError:
        import subprocess
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "rdkit"],
                           capture_output=True, text=True, timeout=spec["install_timeout"])
        if r.returncode != 0:
            print("ERROR: rdkit install failed:", (r.stderr or r.stdout)[-400:])
            sys.exit(2)
        from rdkit import Chem
        return Chem

Chem = boot()

canonical, mols = [], []
for i, f in enumerate(frags, 1):
    f2 = MAP_RE.sub("]", f)
    m = Chem.MolFromSmiles(f2)
    if m is None:
        report(False, f"fragment {i}: valid SMILES ({f[:80]!r})")
        continue
    can = Chem.MolToSmiles(m)
    canonical.append(can)
    mols.append(m)
    if can != f:
        print(f"INFO fragment {i}: not in canonical form ({f[:60]!r} -> {can[:60]!r})")
report(len(canonical) == len(frags), "all fragments valid SMILES")

if spec["product"] and mols:
    pm = Chem.MolFromSmiles(spec["product"])
    if pm is not None:
        from rdkit.Chem import rdFMCS
        p_heavy = sum(1 for a in pm.GetAtoms() if a.GetAtomicNum() > 1)
        p_elem = {a.GetSymbol() for a in pm.GetAtoms() if a.GetAtomicNum() > 1}
        p_can = Chem.MolToSmiles(pm)
        for i, can in enumerate(canonical, 1):
            report(can != p_can, f"fragment {i}: is not the product itself (no reaction predicted)")
        p_set = []
        for pf in spec["product"].split("."):
            pfm = Chem.MolFromSmiles(pf.strip()) if pf.strip() else None
            if pfm is not None:
                p_set.append(Chem.MolToSmiles(pfm))
        if p_set:
            report(sorted(canonical) != sorted(p_set),
                   "answer SET: is not the product set itself (no reaction predicted)")
        r_elem = {a.GetSymbol() for m in mols for a in m.GetAtoms() if a.GetAtomicNum() > 1}
        tot = sum(sum(1 for a in m.GetAtoms() if a.GetAtomicNum() > 1) for m in mols)
        missing = p_elem - r_elem
        report(not missing, f"atom budget: product elements covered (missing: {sorted(missing) or 'none'})")
        report(tot >= p_heavy, f"atom budget: reactants {tot} >= product {p_heavy} heavy atoms")
        for i, m in enumerate(mols, 1):
            h = sum(1 for a in m.GetAtoms() if a.GetAtomicNum() > 1)
            try:
                mcs = rdFMCS.FindMCS([m, pm], timeout=10,
                                     bondCompare=rdFMCS.BondCompare.CompareOrderExact)
                cov = mcs.numAtoms / max(h, 1)
                print(f"INFO fragment {i} MCS coverage vs product: {mcs.numAtoms}/{h} = {cov:.2f}"
                      + ("" if cov >= 0.85 else "  <-- LOW: skeleton drift"))
            except Exception as e:
                print(f"INFO fragment {i} MCS: error {e}")

if fails == 0 and spec["rewrite_canonical"]:
    out = ".".join(canonical)
    if out != line:
        with open(path, "w") as fh:
            fh.write(out + "\n")
        print(f"REWRITE: file rewritten in RDKit-canonical form: {out[:200]!r}")
    else:
        print("INFO: file already in canonical form")

print(f"VERDICT: {'PASS — answer file satisfies the output contract' if fails == 0 else f'FAIL ({fails} checks failed)'}")
sys.exit(0 if fails == 0 else 1)
PY
python3 .agent-tools/verify_answer.py '{"path": "/app/answer.txt", "product": "<product SMILES>", "rewrite_canonical": true, "install_timeout": 180}'
```

Pass `product` verbatim from the instruction so the battery also reports
the atom budget and skeleton coverage on the final set. Finish only on a
PASS verdict — and if anything rewrites the file afterwards, re-run the
battery; the check must postdate the last write.
