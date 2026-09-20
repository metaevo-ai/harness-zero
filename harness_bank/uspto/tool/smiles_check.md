# smiles_check

> Mechanically score retrosynthesis candidate reactant sets against the product with RDKit — validity, canonical form, atom budget, MCS coverage, changed sites.

Use this for candidate diagnostics: parsing, canonicalization, atom inventory,
MCS overlap and rough changed-site indicators. Parsing failures are concrete
representation errors. Atom-budget/MCS flags require chemical interpretation:
large leaving groups lower per-reactant overlap, and reported reactants may
omit reagents whose atoms enter the product. A PASS does not identify the
historically recorded route. Do not rank by atom delta alone, or add a species
solely to force balanced counts/charge. Use memory v2's ranking procedure.

```bash
python3 -c "import rdkit" 2>/dev/null || pip install -q rdkit
mkdir -p .agent-tools
cat > .agent-tools/smiles_check.py <<'PY'
import json, sys
spec = json.loads(sys.argv[1])

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
from rdkit.Chem import rdFMCS

MAP_RE = __import__("re").compile(r":\d+\]")

def parse(s, label):
    if MAP_RE.search(s):
        print(f"  WARN {label}: atom-mapping numbers present (strip them: {s!r})")
    m = Chem.MolFromSmiles(s)
    if m is None:
        print(f"  FAIL {label}: invalid SMILES: {s!r}")
        return None
    return m

def heavy(m):
    return sum(1 for a in m.GetAtoms() if a.GetAtomicNum() > 1)

def elements(m):
    return sorted({a.GetSymbol() for a in m.GetAtoms() if a.GetAtomicNum() > 1})

LG_OK = {"Cl","Br","I","F","P","S","B","Si","Na","K","Li","Mg","Zn","Cu","Sn","O"}
# Elements that can serve as a coupling/substitution handle at a reaction center.
HANDLE = {"Cl","Br","I","P","B","Sn","Si","Mg","Zn","Li","Cu","S"}

def site_clusters(mol, idxs):
    # Number of bond-contiguous clusters among the given atom indices.
    idxs, seen, clusters = set(idxs), set(), 0
    for i in idxs:
        if i in seen:
            continue
        clusters += 1
        stack = [i]
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            for b in mol.GetAtomWithIdx(x).GetBonds():
                y = b.GetOtherAtomIdx(x)
                if y in idxs and y not in seen:
                    stack.append(y)
    return clusters

prod = parse(spec["product"], "product")
if prod is None:
    sys.exit(1)
p_heavy, p_elem = heavy(prod), set(elements(prod))
print(f"product: {Chem.MolToSmiles(prod)}  heavy_atoms={p_heavy} elements={sorted(p_elem)}")

for i, cand in enumerate(spec["candidate_sets"], 1):
    print(f"--- candidate set {i} ({len(cand)} reactants)")
    mols, ok = [], True
    tot = 0
    r_elem = set()
    for j, s in enumerate(cand, 1):
        m = parse(s, f"r{j}")
        if m is None:
            ok = False
            continue
        can = Chem.MolToSmiles(m)
        h = heavy(m)
        tot += h
        r_elem |= set(elements(m))
        print(f"  r{j}: {can}  heavy_atoms={h}")
        if can == Chem.MolToSmiles(prod):
            ok = False
            print(f"  FAIL r{j}: identical to the product — no reaction predicted")
        mols.append((j, m, h))
    if not ok:
        print("  VERDICT: FAIL (see FAIL lines above)")
        continue
    missing = p_elem - r_elem
    extra = r_elem - p_elem
    if missing:
        ok = False
        print(f"  FAIL atom budget: product elements absent from reactants: {sorted(missing)} (dropped fragment?)")
    if extra:
        bad = extra - LG_OK
        tag = "leaving-group/reagent candidates" if not bad else "FAIL unexpected elements"
        if bad:
            ok = False
        print(f"  {'INFO' if not bad else 'FAIL'} extra elements in reactants: {sorted(extra)} ({tag})")
    if tot < p_heavy:
        ok = False
        print(f"  FAIL atom budget: reactants have {tot} heavy atoms < product {p_heavy} (product atoms unaccounted)")
    else:
        print(f"  atom budget: reactants {tot} vs product {p_heavy} heavy atoms (delta {tot - p_heavy} = leaving groups/reagents)")
    if tot - p_heavy <= 1 and not (extra & HANDLE):
        print("  WARN no-handle: no halide/P/B/metal handle among the reactants and near-zero atom surplus — "
              "if this is a coupling/substitution disconnection, one partner MUST bear a leaving group "
              "at the reaction center (a bare C-H cannot react); fine only for addition-type chemistry")
    for j, m, h in mols:
        try:
            mcs = rdFMCS.FindMCS([m, prod], timeout=10,
                                 bondCompare=rdFMCS.BondCompare.CompareOrderExact)
            cov = mcs.numAtoms / max(h, 1)
            print(f"  r{j} MCS coverage vs product: {mcs.numAtoms}/{h} = {cov:.2f}"
                  + ("" if cov >= 0.85 else "  <-- LOW: skeleton drift vs product (transcription error?)"))
            patt = Chem.MolFromSmarts(mcs.smartsString)
            outside = [x for x in range(prod.GetNumAtoms())
                       if x not in set(prod.GetSubstructMatch(patt))]
            nsites = site_clusters(prod, outside) if outside else 0
            print(f"  r{j} changed product sites: {len(outside)} atom(s) in {nsites} site(s)"
                  + ("" if nsites <= 1 else
                     "  <-- 2+ disconnected changed sites: a single recorded step changes "
                     "ONE site (over-transformation?)"))
        except Exception as e:
            print(f"  r{j} MCS: error {e}")
    print(f"  VERDICT: {'PASS (mechanical checks only — still judge forward plausibility)' if ok else 'FAIL'}")
PY
python3 .agent-tools/smiles_check.py '{"product": "<product SMILES>", "candidate_sets": [["<r1>", "<r2>"], ["<r1alt>"]], "install_timeout": 180}'
```

Inspect supported competing candidates together where useful. Separate the
script's diagnostic warnings from the chemical ranking. After choosing one
set, check the actual saved file; no diagnostic score is an oracle.
