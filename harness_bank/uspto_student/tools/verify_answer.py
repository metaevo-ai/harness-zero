"""verify_answer tool — mechanical contract battery for /app/answer.txt.

The USPTO retrosynthesis output contract is brutal in its simplicity:
the reactant SET must match the reference exactly — order-free and
case-normalized, but otherwise a pure STRING match with NO chemical
canonicalization. Concretely seen in rollouts: a trial ended with the
answer file never written (missing file scores 0), and a chemically
correct phenylacetylene written backwards (`c1ccccc1C#C`) scored 0
against the canonical `C#Cc1ccccc1` — dataset references are
RDKit-canonical SMILES, so the safest string to ship is the
RDKit-canonical form.

This tool replaces the hand-rolled final check with ONE call:

- file exists at the exact contract path and is non-empty;
- single line, fragments separated by "." with no empty fragments and
  no whitespace inside;
- atom-mapping numbers flagged (`[C:1]` — the contract says ignore
  them; shipping them breaks the string match);
- every fragment a valid SMILES (RDKit, auto-installed once);
- with `product`: per-fragment "is not the product itself" PLUS a whole-set
  check — an answer SET identical to the product SET (e.g. product written
  back as its own salt fragments) is "no reaction predicted" and FAILs;
- `rewrite_canonical=True` (default): rewrites the file with each
  fragment in RDKit-canonical form, "."-joined — the form dataset
  references use — so a correct answer cannot be lost to string drift;
- optional `product`: also runs the atom-budget / MCS skeleton-drift
  summary on the final set (same machinery as `smiles_check`).

Returns a per-check PASS/FAIL list plus a final verdict line — a FAIL
line cannot be rationalized away. Backend errors are returned as text:
a tool that raises crashes the calling agent's graph.
"""

from __future__ import annotations

import json
import shlex

from langchain_core.tools import StructuredTool, tool

from deepagents_harbor.backend import HarborSandboxBackend
from deepagents_harbor.current import current_backend

_SCRIPT = r"""
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
"""

@tool
def verify_answer(
    path: str = "/app/answer.txt",
    product: str = "",
    rewrite_canonical: bool = True,
    install_timeout: int = 180,
) -> str:
    """Run the mechanical output-contract battery on the retrosynthesis
    answer file: exists at the exact path, non-empty, single line,
    "."-separated fragments with no empties and no inner whitespace, no
    atom-mapping numbers, every fragment a valid SMILES (RDKit,
    auto-installed on first call). With `rewrite_canonical=True` (default)
    the file is rewritten with each fragment in RDKit-canonical form — the
    grader is an exact string set match with NO canonicalization and dataset
    references are RDKit-canonical, so this rescues chemically-correct
    answers written in a non-canonical string. Pass `product` (the product
    SMILES from the instruction) to also get the atom-budget and MCS
    skeleton-drift summary on the final set. Run this as the LAST step
    before finishing; finish only on a PASS verdict."""
    spec = {
        "path": path,
        "product": product,
        "rewrite_canonical": bool(rewrite_canonical),
        "install_timeout": max(60, min(int(install_timeout), 600)),
    }
    cmd = f"python3 - {shlex.quote(json.dumps(spec))} <<'PYEOF'\n{_SCRIPT}\nPYEOF"
    try:
        r = current_backend.get().execute(cmd, timeout=int(install_timeout) + 120)
    except Exception as e:  # never let a backend error kill the agent graph
        return f"error: backend execute failed: {e}"
    return r.output if r.exit_code in (0, 1) else f"error (exit {r.exit_code}): {r.output}"


def make_tool(backend: HarborSandboxBackend) -> StructuredTool:
    """Student-harness loader factory: return the `verify_answer` tool.

    The loader passes the trial's backend positionally; the tool body reads it
    from `deepagents_harbor.current.current_backend` at call time (published
    by `DeepHarnessAgent.run`), so one shared tool instance stays correct
    across concurrent trials. `backend` is part of the loader contract and is
    intentionally unused here.
    """
    return verify_answer
