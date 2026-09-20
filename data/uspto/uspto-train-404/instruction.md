You are solving one retrosynthesis prediction task.

## Product
Context: The reaction type is Oxidations.
Input: COc1ccc2c3c1O[C@H]1[C@@]4(OC)CC[C@@]5(C[C@@H]4CS(=O)Cc4ccccc4)[C@@H](C2)N(CC2CC2)CC[C@]315

## Output contract
Write your final answer to `/app/answer.txt`: the precursor reactant SMILES, separated by "." if there are multiple reactants (e.g. `CC(=O)Cl.c1ccccc1O`). Ignore atom-mapping numbers.
Scoring: the reactant SET must match the reference exactly (order-free, case-normalized). Jaccard similarity is recorded as an auxiliary metric.

Requirements:
- You MUST write `/app/answer.txt` before you finish — a missing file scores 0.
- Only the file's final content is graded; anything else you create is ignored.
- Work efficiently.
