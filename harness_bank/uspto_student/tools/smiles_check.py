"""smiles_check tool — RDKit-backed mechanical scoring of retrosynthesis candidates.

Observed failure modes on USPTO retrosynthesis rollouts (all arms):
agents hand-parse and hand-transcribe SMILES, then commit to the first
plausible disconnection with zero machine checks. Concretely seen:
fused rings silently rewritten as single rings, substituents moved
meta->para, cross-coupling substrates shipped with NO leaving-group
handle at the reaction center, and chemically-impossible fragments that
a valence check would have killed on sight.

This tool validates the product and every candidate reactant set with
RDKit (auto-installed once into the sandbox, `pip install rdkit`), and
returns a compact per-set report the model cannot rationalize away:

- VALID/INVALID per fragment (invalid SMILES kills the set);
- the RDKit-canonical form of every fragment — the string to ship,
  because the grader is an exact string set match with NO
  canonicalization and dataset references are RDKit-canonical;
- atom-mapping numbers (`[C:1]`) flagged and shown stripped — the
  contract says to ignore them, shipping them breaks the string match;
- heavy-atom budget: every product heavy atom must be accounted for by
  the reactants (a deficit = a dropped fragment); elements appearing
  only in reactants are listed as leaving-group/reagent candidates
  (Cl, Br, I, P, S, B, Si, metals are expected there — anything else
  is suspicious);
- MCS coverage per fragment: size of the maximum common substructure
  between the fragment and the product, as a fraction of the fragment's
  heavy atoms. A correct disconnection embeds almost completely into
  the product skeleton (only the reaction center differs); coverage
  well below 1.0 means skeleton drift — a transcription error, not a
  creative disconnection;
- a fragment identical to the product is a FAIL (no reaction
  predicted), and a near-zero atom surplus with no halide/P/B/metal
  handle anywhere in the set triggers a no-handle WARN — coupling/
  substitution disconnections need a leaving group at the reaction
  center (addition-type chemistry is exempt);
- changed-site count per fragment: the product atoms OUTSIDE the MCS,
  grouped into bond-contiguous clusters. A recorded single step changes
  ONE site — a fragment differing at 2+ disconnected sites means
  over-transformation (you also changed groups an earlier step made:
  sulfones, halides, pre-installed protecting groups), which the
  coverage fraction alone cannot see (both score ~1.0 coverage).

What the tool deliberately does NOT do: pick the reaction center or
judge forward-reaction plausibility — that is the worker's chemical
reasoning, guided by the `retrosynthesis-candidates` skill. The tool
reports mechanical facts; a FAIL line cannot be argued with.

Backend errors and RDKit install failures are returned as text: a tool
that raises crashes the calling agent's graph.
"""

from __future__ import annotations

import json
import shlex

from langchain_core.tools import StructuredTool, tool

from deepagents_harbor.backend import HarborSandboxBackend
from deepagents_harbor.current import current_backend

_SCRIPT = r"""
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
"""

@tool
def smiles_check(
    product: str,
    candidate_sets: list[list[str]],
    install_timeout: int = 180,
) -> str:
    """Mechanically score retrosynthesis candidate reactant sets against the
    product SMILES with RDKit (auto-installed on first call). `candidate_sets`
    is a list of candidate answers, each a list of reactant SMILES — pass
    EVERY candidate you are considering, one call. Per set reports: fragment
    validity (FAIL kills the set), RDKit-canonical form of each fragment (the
    exact string to ship — the grader is string-exact with no
    canonicalization), atom-mapping-number warnings, heavy-atom budget vs the
    product (every product atom must be accounted for), unexpected-element
    flags (only leaving-group elements may appear in reactants but not the
    product), and per-fragment MCS coverage against the product skeleton
    (coverage << 1.0 = you drifted from the skeleton: wrong ring, wrong
    substituent position — a transcription error, not a disconnection). Use
    this BEFORE choosing a candidate, never on a single favorite."""
    spec = {
        "product": product,
        "candidate_sets": [[str(s) for s in cand] for cand in candidate_sets],
        "install_timeout": max(60, min(int(install_timeout), 600)),
    }
    cmd = f"python3 - {shlex.quote(json.dumps(spec))} <<'PYEOF'\n{_SCRIPT}\nPYEOF"
    try:
        r = current_backend.get().execute(cmd, timeout=int(install_timeout) + 120)
    except Exception as e:  # never let a backend error kill the agent graph
        return f"error: backend execute failed: {e}"
    return r.output if r.exit_code in (0, 1) else f"error (exit {r.exit_code}): {r.output}"


def make_tool(backend: HarborSandboxBackend) -> StructuredTool:
    """Student-harness loader factory: return the `smiles_check` tool.

    The loader passes the trial's backend positionally; the tool body reads it
    from `deepagents_harbor.current.current_backend` at call time (published
    by `DeepHarnessAgent.run`), so one shared tool instance stays correct
    across concurrent trials. `backend` is part of the loader contract and is
    intentionally unused here.
    """
    return smiles_check
