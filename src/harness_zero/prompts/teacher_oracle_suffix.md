The workspace root contains `oracle_answer.txt`: the dataset's recorded reference answer for this exact task. It is privileged — the student never sees it, and it must never leak into anything the student could read.

How to use it:

- Before each decision, read it and check the student's current direction against it. PASS when the student's proposal can still converge to the recorded answer; REPLACE when it cannot.
- A replacement must read as something the student could produce from its own visible task and trajectory: derive the answer step by step, keep every verification action (reading files, checking formats, mechanical checks), and never skip validation because you already know the answer.
- Never mention the answer file, "reference", "oracle", "ground truth", or the fact that you know the answer, in any replacement text or reasoning the student will see.
