You are solving one retrosynthesis prediction task.

## Product
Context: The reaction type is Deprotections.
Input: COc1ccc(S(=O)(=O)N(Cc2cccnc2)c2c(C(=O)O)cnc3c2c(C)nn3C)cc1

## Output contract
Write your final answer to `/app/answer.txt`: the precursor reactant SMILES, separated by "." if there are multiple reactants (e.g. `CC(=O)Cl.c1ccccc1O`). Ignore atom-mapping numbers.
Scoring: the reactant SET must match the reference exactly (order-free, case-normalized). Jaccard similarity is recorded as an auxiliary metric.

Requirements:
- You MUST write `/app/answer.txt` before you finish — a missing file scores 0.
- Only the file's final content is graded; anything else you create is ignored.
- Work efficiently.
