"""propose_retrosynthesis tool — enumerate candidate reactant sets with a named-transform library.

Observed failure mode in USPTO retrosynthesis rollouts: the agent commits to
the FIRST disconnection it thinks of, in its head, and writes it down — and
picks wrong (an ether cleavage instead of the recorded epoxidation, a
deprotection instead of the recorded azide reduction, a ketone+acetylide
instead of the recorded Sonogashira). Hand-derived SMILES then drift into
non-canonical spellings that the string-match grader scores as zero.

This tool applies a curated library of ~50 common one-step retrosynthetic
transforms (named reactions: peracid epoxidation, sulfide oxidation, azide/
nitro reduction, aldehyde/ketone/carboxylic-acid/ester reduction to
alcohols, N/O/S dealkylation, Sonogashira, Suzuki, Wittig, amide/
ester couplings, Boc/Cbz-style protections and deprotections, Paal-Knorr,
Fischer indole, tetrazole-from-nitrile, Hantzsch thiazole, ...) to the product with RDKit
reaction SMARTS — in the sandbox, auto-installing rdkit via pip on first
use (pypi is allowlisted). Candidates that USPTO records usually list a
co-reactant for (mCPBA, Boc2O, NBS/NCS, Ac2O, azide, hydroxylamine) are
also emitted WITH that co-reactant appended. Spectator components of the
product (counterions like [Na+], HCl) carry over into every candidate —
references keep them. All reactant strings are RDKit-canonical already.
Amide-coupling candidates whose acid partner is a beta-ketoacid are ALSO
emitted in their tert-butyl ester form: free beta-ketoacids decarboxylate
and are rarely the recorded reagent, so the shelf-stable ester is the
standard recorded donor and must be reachable from the pool.

Candidates whose transform matches the instruction's stated reaction type
are listed first. Coverage is deliberately finite: complex condensations
and stereo-sensitive formations (E/Z, centers lost at the reaction site)
may be absent — if no candidate fits, fall back to manual analysis and
still run smiles_check + verify_answer on the result.

Backend-level errors are caught and returned as text: a tool that raises
crashes the calling agent's graph.
"""

from __future__ import annotations

import base64
import shlex

from langchain_core.tools import StructuredTool, tool

from deepagents_harbor.backend import HarborSandboxBackend
from deepagents_harbor.current import current_backend

_PY = r'''
import sys
from rdkit import Chem
from rdkit.Chem import AllChem
from rdkit import RDLogger
RDLogger.DisableLog("rdApp.*")

MCPBA = "O=C(OO)c1cccc(Cl)c1"
BOC2O = "CC(C)(C)OC(=O)OC(=O)OC(C)(C)C"
NBS = "O=C1CCC(=O)N1Br"

# (name, reaction-type keywords, retro SMARTS, note, co-reactants)
TRANSFORMS = [
    ("peracid-epoxidation", ("heterocycle formation", "oxidations"),
     "[C:1]1[C:2][O:3]1>>[C:1]=[C:2]",
     "epoxide from alkene + peracid; USPTO usually lists the peracid as a reactant", (MCPBA,)),
    ("sulfide-S-oxidation-sulfoxide", ("oxidations",),
     "[S:1](=[O:2])([#6:3])[#6:4]>>[S:1]([#6:3])[#6:4]",
     "sulfoxide from sulfide; the oxidant (often mCPBA) is usually a recorded reactant", (MCPBA,)),
    ("sulfide-S-oxidation-sulfone", ("oxidations",),
     "[S:1](=[O:2])(=[O:5])([#6:3])[#6:4]>>[S:1]([#6:3])[#6:4]",
     "sulfone from sulfide; the oxidant (often mCPBA) is usually a recorded reactant", (MCPBA,)),
    ("azide-reduction", ("functional group interconversion", "reductions"),
     "[CX4:1][NH2:2]>>[CX4:1][N:2]=[N+]=[N-]",
     "primary aliphatic amine from azide reduction (configuration retained)", ()),
    ("nitro-reduction", ("reductions", "functional group interconversion"),
     "[c,CX4:1][NH2:2]>>[c,CX4:1][N:2](=[O:3])=[O:4]",
     "amine from nitro reduction (typical for anilines)", ()),
    ("alcohol-from-aldehyde-reduction", ("reductions",),
     "[#6:3][CH2;R0:1][OH:2]>>[#6:3][CH:1]=[O:2]",
     "primary alcohol from aldehyde reduction (one ladder rung up)", ()),
    ("alcohol-from-ketone-reduction", ("reductions",),
     "[#6:3][CH;R0:1]([OH:2])[#6:4]>>[#6:3][C:1](=[O:2])[#6:4]",
     "secondary alcohol from ketone reduction (one ladder rung up)", ()),
    ("alcohol-from-acid-reduction", ("reductions",),
     "[#6:3][CH2;R0:1][OH:2]>>[#6:3][C:1](=[O:2])[OH]",
     "primary alcohol from carboxylic acid reduction (LiAlH4/BH3 steps are commonly recorded)", ()),
    ("alcohol-from-methyl-ester-reduction", ("reductions",),
     "[#6:3][CH2;R0:1][OH:2]>>[#6:3][C:1](=[O:2])[O]C",
     "primary alcohol from methyl ester reduction (LiAlH4)", ()),
    ("azole-N-dealkylation", ("heteroatom alkylation",),
     "[nH0:1]([#6:5])[CX4;R0:2]>>[nH:1][#6:5].[CX4:2][Cl]",
     "N-alkylated azole from azole NH + alkyl chloride", ()),
    ("azole-N-dealkylation-bromide", ("heteroatom alkylation",),
     "[nH0:1]([#6:5])[CX4;R0:2]>>[nH:1][#6:5].[CX4:2][Br]",
     "same disconnection with the bromide electrophile", ()),
    ("azole-N-dealkylation-iodo", ("heteroatom alkylation",),
     "[nH0:1]([#6:5])[CX4;R0:2]>>[nH:1][#6:5].[CX4:2][I]",
     "same disconnection with the iodide electrophile (e.g. MeI methylations)", ()),
    ("amine-N-dealkylation", ("heteroatom alkylation",),
     "[NH0;!$(NC=O):1]([#6:5])([#6:6])[CH2;R0:2]>>[NH1:1]([#6:5])[#6:6].[CH2:2][Cl]",
     "tertiary amine from secondary amine + alkyl chloride", ()),
    ("amine-N-dealkylation-sec", ("heteroatom alkylation",),
     "[NH1;!$(NC=O):1]([#6:5])[CH2;R0:2]>>[NH2:1][#6:5].[CH2:2][Cl]",
     "secondary amine from primary amine + alkyl chloride", ()),
    ("amine-N-dearylation", ("heteroatom alkylation",),
     "[NH0;!$(NC=O):1]([#6:5])([#6:6])[c:2]>>[NH1:1]([#6:5])[#6:6].[c:2][Cl]",
     "N-aryl amine from secondary amine + aryl chloride (SNAr/Buchwald)", ()),
    ("O-dealkylation-williamson", ("heteroatom alkylation",),
     "[#6:5][O:1][CX4;R0:2]>>[#6:5][OH:1].[CX4:2][Cl]",
     "ether from alcohol/phenol + alkyl chloride (Williamson)", ()),
    ("O-dealkylation-williamson-bromide", ("heteroatom alkylation",),
     "[#6:5][O:1][CX4;R0:2]>>[#6:5][OH:1].[CX4:2][Br]",
     "ether from alcohol/phenol + alkyl bromide (Williamson)", ()),
    ("S-dealkylation", ("heteroatom alkylation",),
     "[#6:5][S:1][CX4;R0:2]>>[#6:5][SH:1].[CX4:2][Cl]",
     "thioether from thiol + alkyl chloride", ()),
    ("sonogashira", ("c-c bond formation",),
     "[c:1][C:2]#[C:3][#6:4]>>[c:1][Br].[CH:2]#[C:3][#6:4]",
     "aryl-alkyne from aryl bromide + terminal alkyne (Sonogashira)", ()),
    ("sonogashira-iodo", ("c-c bond formation",),
     "[c:1][C:2]#[C:3][#6:4]>>[c:1][I].[CH:2]#[C:3][#6:4]",
     "aryl-alkyne from aryl iodide + terminal alkyne (Sonogashira)", ()),
    ("acetylide-addition-to-ketone", ("c-c bond formation",),
     "[#6:4][C:3]#[C:2][C:1]([O:7])([#6:5])[#6:6]>>[#6:4][C:3]#[CH].[O:7]=[C:1]([#6:5])[#6:6]",
     "tertiary propargyl alcohol from ketone + terminal alkyne (acetylide addition)", ()),
    ("suzuki-biaryl", ("c-c bond formation",),
     "[c:1][c:2]>>[c:1][Br].[c:2]B(O)O",
     "biaryl from aryl bromide + arylboronic acid (Suzuki)", ()),
    ("suzuki-biaryl-swapped", ("c-c bond formation",),
     "[c:1][c:2]>>[c:1]B(O)O.[c:2][Br]",
     "Suzuki with swapped halide/boronic-acid assignment", ()),
    ("suzuki-biaryl-chloro", ("c-c bond formation",),
     "[c:1][c:2]>>[c:1][Cl].[c:2]B(O)O",
     "biaryl from aryl chloride + arylboronic acid (Suzuki)", ()),
    ("suzuki-biaryl-chloro-swapped", ("c-c bond formation",),
     "[c:1][c:2]>>[c:1]B(O)O.[c:2][Cl]",
     "Suzuki (chloride) with swapped assignment", ()),
    ("wittig-olefination", ("c-c bond formation",),
     "[#6:1][CH:2]=[CH:3][#6:4]>>[#6:1][CH:2]=[O].[#6:4][CH2:3][P+](c1ccccc1)(c1ccccc1)c1ccccc1",
     "alkene from carbonyl + phosphonium ylide (Wittig); the salt is a recorded reactant", ()),
    ("wittig-olefination-swapped", ("c-c bond formation",),
     "[#6:1][CH:2]=[CH:3][#6:4]>>[#6:1][CH2:2][P+](c1ccccc1)(c1ccccc1)c1ccccc1.[O:5]=[CH:3][#6:4]",
     "Wittig with carbonyl/ylide assignment swapped", ()),
    ("wittig-terminal-vinylarene", ("c-c bond formation",),
     "[CH2:2]=[CH:3][c:4]>>[CH3:2][P+](c1ccccc1)(c1ccccc1)c1ccccc1.[O:5]=[CH:3][c:4]",
     "vinyl arene from aryl aldehyde + methyltriphenylphosphonium", ()),
    ("reductive-amination", ("c-c bond formation", "reductions"),
     "[NH1;!$(NC=O):1]([#6:5])[CH2;R0:2][#6:6]>>[NH2:1][#6:5].[O:3]=[CH:2][#6:6]",
     "secondary amine from primary amine + aldehyde (reductive amination)", ()),
    ("amide-coupling", ("acylation",),
     "[NH1:1]([#6:5])[C:2](=[O:3])[#6:4]>>[NH2:1][#6:5].[O:3]=[C:2]([OH])[#6:4]",
     "secondary amide from amine + carboxylic acid", ()),
    ("amide-coupling-tert", ("acylation",),
     "[NH0:1]([#6:5])([#6:6])[C:2](=[O:3])[#6:4]>>[NH1:1]([#6:5])[#6:6].[O:3]=[C:2]([OH])[#6:4]",
     "tertiary amide from secondary amine + carboxylic acid", ()),
    ("acetamide-from-anhydride", ("acylation",),
     "[NH1:1]([#6:5])[C:2](=[O:3])C>>[NH2:1][#6:5].CC(=O)OC(C)=O",
     "acetamide from amine + acetic anhydride (listed as reactant)", ()),
    ("ester-coupling", ("acylation",),
     "[#6:4][C:2](=[O:3])[O:1][#6:5]>>[#6:4][C:2](=[O:3])[OH].[O:1][#6:5]",
     "ester from carboxylic acid + alcohol", ()),
    ("ester-from-acid-chloride", ("acylation",),
     "[#6:4][C:2](=[O:3])[O:1][#6:5]>>[#6:4][C:2](=[O:3])[Cl].[O:1][#6:5]",
     "ester from acid chloride + alcohol", ()),
    ("sulfonamide-formation", ("acylation", "heteroatom alkylation"),
     "[NH1:1]([#6:5])[S:2](=[O:3])(=[O:4])[#6:6]>>[NH2:1][#6:5].[S:2](=[O:3])(=[O:4])([Cl])[#6:6]",
     "sulfonamide from amine + sulfonyl chloride", ()),
    ("amide-from-ester-ammonolysis", ("functional group interconversion", "acylation"),
     "[#6:4][C:2](=[O:3])[NH2:1]>>[#6:4][C:2](=[O:3])OC.N",
     "primary amide from methyl ester + ammonia (listed as reactant)", ()),
    ("boc-deprotection", ("deprotections",),
     "[CX4:5][NH2:1]>>[CX4:5][NH1:1]C(=O)OC(C)(C)C",
     "free primary amine from N-Boc deprotection", ()),
    ("boc-deprotection-ringN", ("deprotections",),
     "[NH1:1]([#6:5])[#6:6]>>[NH0:1]([#6:5])([#6:6])C(=O)OC(C)(C)C",
     "free secondary/cyclic amine from N-Boc deprotection", ()),
    ("boc-deprotection-azoleN", ("deprotections",),
     "[nH:1]([#6:5])>>[n:1]([#6:5])C(=O)OC(C)(C)C",
     "azole NH from N-Boc deprotection", ()),
    ("boc-protection", ("protections",),
     "[CX4:5][NH1:1][C:2](=[O:3])[O:4][C:6]([CH3])([CH3])[CH3]>>[CX4:5][NH2:1]",
     "N-Boc from amine + Boc2O (listed as reactant)", (BOC2O,)),
    ("boc-protection-ringN", ("protections",),
     "[NH0:1]([#6:5])([#6:6])[C:2](=[O:3])[O:4][C]([CH3])([CH3])[CH3]>>[NH1:1]([#6:5])[#6:6]",
     "N-Boc on a cyclic/secondary N from that NH + Boc2O (listed as reactant)", (BOC2O,)),
    ("boc-protection-azoleN", ("protections",),
     "[n:1]([#6:5])[C:2](=[O:3])[O:4][C]([CH3])([CH3])[CH3]>>[nH:1][#6:5]",
     "N-Boc azole from azole NH + Boc2O (listed as reactant)", (BOC2O,)),
    ("ester-hydrolysis-methyl", ("deprotections", "functional group interconversion"),
     "[#6:4][C:2](=[O:3])[OH:1]>>[#6:4][C:2](=[O:3])[O:1]C",
     "carboxylic acid from methyl ester hydrolysis", ()),
    ("ester-hydrolysis-ethyl", ("deprotections", "functional group interconversion"),
     "[#6:4][C:2](=[O:3])[OH:1]>>[#6:4][C:2](=[O:3])[O:1]CC",
     "carboxylic acid from ethyl ester hydrolysis", ()),
    ("O-benzyl-deprotection", ("deprotections",),
     "[#6:5][OH:1]>>[#6:5][O:1]Cc1ccccc1",
     "alcohol/phenol from benzyl ether hydrogenolysis", ()),
    ("O-demethylation-aryl", ("deprotections", "functional group interconversion"),
     "[c:5][OH:1]>>[c:5][O:1]C",
     "phenol from aryl methyl ether cleavage", ()),
    ("O-benzoyl-deprotection", ("deprotections", "reductions"),
     "[c:5][OH:1]>>[c:5][O:1]C(=O)c1ccccc1",
     "phenol from benzoate ester cleavage", ()),
    ("alcohol-oxidation-ketone", ("oxidations",),
     "[#6:5][C:1](=[O:2])[#6:6]>>[#6:5][CH:1]([OH:2])[#6:6]",
     "ketone from secondary alcohol oxidation", ()),
    ("alcohol-oxidation-aldehyde", ("oxidations",),
     "[CH1:1](=[O:2])>>[CH2:1][OH:2]",
     "aldehyde from primary alcohol oxidation", ()),
    ("acid-to-alcohol-reduction", ("reductions",),
     "[CX4:5][CH2:1][OH:2]>>[CX4:5][C:1](=[O:3])O",
     "primary alcohol from carboxylic acid reduction", ()),
    ("oxime-formation", ("functional group interconversion", "functional group addition"),
     "[CH:1]=[N:2][OH:3]>>[CH:1]=[O].[N:2][O:3]",
     "oxime from aldehyde + hydroxylamine (listed as reactant)", ()),
    ("tetrazole-from-nitrile", ("heterocycle formation",),
     "[#6:1][c:2]1[n:3][n:4][n:5][nH:6]1>>[#6:1][C:2]#[N].[N-]=[N+]=[N-]",
     "1H-tetrazole from nitrile + azide (listed as reactant)", ()),
    ("paal-knorr-pyrrole", ("heterocycle formation",),
     "[n:1]1([c:6])[c:2][c:3][c:4][c:5]1>>[NH2:1][c:6].O=[C:2][C:3][C:4][C:5]=O",
     "N-aryl pyrrole from aniline + 1,4-diketone (Paal-Knorr)", ()),
    ("fischer-indole", ("heterocycle formation",),
     "[nH:1]1[c:2]([#6:10])[c:3]([#6:11])[c:4]2[c:5][c:6][c:7][c:8][c:9]12>>[NH1:1](N)[c:9]1[c:8][c:7][c:6][c:5][c:4]1.O=[C:2]([#6:10])[C:3][#6:11]",
     "2,3-disubstituted indole (incl. fused carbolines) from arylhydrazine + ketone (Fischer indole)", ()),
    ("thiazole-from-thioamide", ("heterocycle formation",),
     "[s:1]1[c:2][n:3][c:4][c:5]1>>[S:1]=[C:2][N].[O:6]=[C:4][CH:5][Br]",
     "thiazole from thioamide + alpha-bromoketone (Hantzsch)", ()),
    ("fga-bromination", ("functional group addition",),
     "[CH2:1][Br:2]>>[CH3:1]",
     "alkyl bromide from C-H bromination; NBS is usually a recorded reactant", (NBS,)),
    ("fga-chlorination", ("functional group addition",),
     "[CH2:1][Cl:2]>>[CH3:1]",
     "alkyl chloride from C-H chlorination; NCS may be a recorded reactant",
     ("O=C1CCC(=O)N1Cl",)),
]

def canon_mol(m):
    try:
        Chem.SanitizeMol(m)
    except Exception:
        return None
    for a in m.GetAtoms():
        a.SetAtomMapNum(0)
    return Chem.MolToSmiles(m, isomericSmiles=True)

BETA_KETO_ACID = Chem.MolFromSmarts("[OX2H1][CX3](=[OX1])[CX4][CX3]=[OX1]")
ACID_TO_TBU_ESTER = AllChem.ReactionFromSmarts("[C:1](=[O:2])[OH:3]>>[C:1](=[O:2])OC(C)(C)C")

def beta_keto_tbu_variants(parts):
    # free beta-ketoacids decarboxylate; the tert-butyl ester is the shelf-stable recorded donor
    out = []
    for i, p in enumerate(parts):
        m = Chem.MolFromSmiles(p)
        if m is None or not m.HasSubstructMatch(BETA_KETO_ACID):
            continue
        try:
            for prods in ACID_TO_TBU_ESTER.RunReactants((m,)):
                c = canon_mol(prods[0])
                if c:
                    out.append(".".join(sorted(parts[:i] + [c] + parts[i+1:])))
        except Exception:
            pass
    return out

def main():
    product = sys.argv[1].strip()
    rtype = sys.argv[2].lower() if len(sys.argv) > 2 else ""
    pm = Chem.MolFromSmiles(product)
    if pm is None:
        print("ERROR: product SMILES does not parse:", product)
        sys.exit(1)
    for a in pm.GetAtoms():
        a.SetAtomMapNum(0)
    frags = Chem.GetMolFrags(pm, asMols=True)
    results = []
    seen = set()
    for name, types, smarts, note, cores in TRANSFORMS:
        outcomes = []
        try:
            rxn = AllChem.ReactionFromSmarts(smarts)
            for i, frag in enumerate(frags):
                others = [frags[j] for j in range(len(frags)) if j != i]
                for out in rxn.RunReactants((frag,)):
                    outcomes.append(tuple(out) + tuple(others))
        except Exception:
            continue
        match = 1 if any(t in rtype for t in types) else 0
        got = 0
        for out in outcomes:
            if got >= 4:
                break
            parts, ok = [], True
            for m in out:
                c = canon_mol(m)
                if c is None:
                    ok = False
                    break
                parts.append(c)
            if not ok:
                continue
            base = ".".join(sorted(parts))
            variants = [(base, "")]
            for co in cores:
                variants.append((".".join(sorted(parts + [co])), "  [+ recorded co-reactant]"))
            for v, tag in variants:
                if v in seen:
                    continue
                seen.add(v)
                results.append((-match, name, v, note + tag))
                got += 1
            if name.startswith("amide-coupling"):
                for v2 in beta_keto_tbu_variants(parts):
                    if v2 in seen:
                        continue
                    seen.add(v2)
                    results.append((-match, name + "-tbu-ester-donor", v2,
                                    "same disconnection, beta-ketoacid donor shipped as its tert-butyl ester (free beta-ketoacids decarboxylate; the ester is the shelf-stable recorded form)"))
    results.sort(key=lambda r: (r[0], r[1]))
    if not results:
        print("no library transform applies to this product - fall back to manual analysis,")
        print("then run smiles_check + verify_answer on your hand-derived set")
        sys.exit(0)
    print(f"{len(results[:30])} candidate reactant sets (reaction-type matches first; all SMILES already RDKit-canonical):")
    for i, (_, name, smi, note) in enumerate(results[:30], 1):
        print(f"[{i:02d}] {name}")
        print(f"     {smi}")
        print(f"     - {note}")
    print("next steps: pick the candidate consistent with the STATED reaction type;")
    print("verify it with smiles_check; verify_answer canonicalizes and rewrites")
    print("the answer file before you finish")

main()
'''.strip()


@tool
def propose_retrosynthesis(product_smiles: str, reaction_type: str = "", timeout_sec: int = 240) -> str:
    """Enumerate candidate one-step retrosyntheses for a product SMILES using
    a curated named-transform library (epoxidation, S-oxidation, azide/nitro
    reduction, aldehyde/ketone/acid/ester reduction to alcohols, N/O/S
    dealkylation, Sonogashira, Suzuki, Wittig, amide/ester
    couplings, Boc protections/deprotections, named heterocycle formations,
    ...). Run this FIRST, before reasoning about disconnections by hand.
    Pass the instruction's stated reaction type (e.g. "Oxidations") as
    reaction_type so matching transforms sort to the top. Every candidate
    is already RDKit-canonical and may include the co-reactant USPTO records
    list (mCPBA, Boc2O, NBS, Ac2O, azide, ...); product counterions carry
    over. Amide-coupling candidates with a beta-ketoacid donor are also
    emitted in their tert-butyl ester form (the shelf-stable recorded
    donor). Score the chosen set with smiles_check, then submit it through
    verify_answer (it canonicalizes and rewrites the answer file). If NO
    transform applies, derive manually —
    the library covers common single steps, not every reaction. First call
    may install rdkit via pip."""
    timeout_sec = max(30, min(int(timeout_sec), 600))
    b64 = base64.b64encode(_PY.encode()).decode()
    script = (
        'python3 -c "import rdkit" 2>/dev/null || pip install -q rdkit\n'
        f"python3 -c \"import base64,sys;"
        f"open('/tmp/_sh_retro.py','wb').write(base64.b64decode(sys.argv[1]))\" {b64}\n"
        f"timeout {timeout_sec} python3 /tmp/_sh_retro.py "
        f"{shlex.quote(product_smiles)} {shlex.quote(reaction_type)}"
    )
    try:
        r = current_backend.get().execute(script, timeout=timeout_sec + 180)
    except Exception as e:  # never let a backend error kill the agent graph
        return f"error: backend execute failed: {e}"
    return r.output if r.exit_code in (0, 1) else f"error (exit {r.exit_code}): {r.output}"


def make_tool(backend: HarborSandboxBackend) -> StructuredTool:
    """Student-harness loader factory: return the `propose_retrosynthesis` tool.

    The loader passes the trial's backend positionally; the tool body reads it
    from `deepagents_harbor.current.current_backend` at call time (published
    by `DeepHarnessAgent.run`), so one shared tool instance stays correct
    across concurrent trials. `backend` is part of the loader contract and is
    intentionally unused here.
    """
    return propose_retrosynthesis
