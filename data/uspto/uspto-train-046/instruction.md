You are solving one retrosynthesis prediction task.

## Product
Context: The reaction type is Functional group interconversion (FGI).
Input: CC[C@@]1(O)CC[C@@]2(Cc3ccccc3)c3ccc(C(=O)Nc4cccnc4C)cc3COC[C@H]2C1

## Output contract
Write your final answer to `/app/answer.txt`: the precursor reactant SMILES, separated by "." if there are multiple reactants (e.g. `CC(=O)Cl.c1ccccc1O`). Ignore atom-mapping numbers.
Scoring: the reactant SET must match the reference exactly (order-free, case-normalized). Jaccard similarity is recorded as an auxiliary metric.

Requirements:
- You MUST write `/app/answer.txt` before you finish — a missing file scores 0.
- Only the file's final content is graded; anything else you create is ignored.
- Work efficiently.
