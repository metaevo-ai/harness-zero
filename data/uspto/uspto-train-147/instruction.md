You are solving one retrosynthesis prediction task.

## Product
Context: The reaction type is C-C bond formation.
Input: CC(C)(C)OC(=O)N1CCN(C2=NC(=O)C(=Cc3ccc4c(c3)c(Cl)nn4Cc3ccc(C(F)(F)F)cc3C(F)(F)F)S2)C[C@@H]1CO

## Output contract
Write your final answer to `/app/answer.txt`: the precursor reactant SMILES, separated by "." if there are multiple reactants (e.g. `CC(=O)Cl.c1ccccc1O`). Ignore atom-mapping numbers.
Scoring: the reactant SET must match the reference exactly (order-free, case-normalized). Jaccard similarity is recorded as an auxiliary metric.

Requirements:
- You MUST write `/app/answer.txt` before you finish — a missing file scores 0.
- Only the file's final content is graded; anything else you create is ignored.
- Work efficiently.
