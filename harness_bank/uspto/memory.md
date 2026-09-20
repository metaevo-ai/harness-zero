# Accumulated failure patterns — USPTO single-step retrosynthesis

Component: `memory:accumulated-failures`, version 2.
Scope: teacher-side guidance from student-visible failed trajectories. This
memory contains no task-specific answers, reference sets, or measured reference
frequencies. The student receives only a grounded replacement, never this file.

Given a product and reaction class, select one immediate precursor reactant set,
write dot-separated valid SMILES to the requested file, verify the file, and end
with visible text. A successful local parse or forward reconstruction is NOT
proof that the selected set is the historically recorded reaction. Do not call a
choice chemically verified merely because the file-format checks passed.

## 1. Keep representation checks separate from candidate ranking

Trigger: the student chooses a precursor solely because it parses, has high MCS
coverage, passes atom counts, or can be connected back into the product.
Evidence: several candidates pass the same checks and the ranking supplies no
additional chemical distinction.
Intervention: separate a validation column from a ranking column. State the
reaction-center edit, why the functional groups can participate, competing
sites, and what information remains absent. A hand-written forward SMARTS can
check the intended bond edit but does not establish conditions, chemoselectivity,
or which precursor was used in the recorded experiment.
Do not trigger when these checks are used only to reject malformed structures
and the chemical ranking is independently explained.

## 2. Do not rank unrelated reactions by smallest atom excess

Trigger: a smaller ester group, halide, or coupling partner wins solely by
minimum added atoms / maximum scaffold overlap.
Intervention: stop treating size as evidence of reaction frequency. Separate
reaction-family choice, site choice, and reagent/handle identity. A larger
leaving group or protecting group can be appropriate; a smaller one is not
inherently more likely. Consider functional-group compatibility and an explicit
ordinary-chemistry prior, with uncertainty, instead of fabricated dataset counts.
Do not trigger for checking that an untouched scaffold was accidentally lost.

Positive example: methyl and benzyl ethers both preserve a phenol scaffold;
compare plausible cleavage conditions and sensitive groups, and acknowledge
that the product alone may not identify the original protecting group.
Negative example: reject a candidate that accidentally drops a ring from the
substrate; that is a graph-consistency error, not a preference for small reagents.

## 3. Compare different reaction families before handle variants

Trigger: a C-C task enumerates only Cl/Br/I versions of one guessed coupling,
or all proposed alternatives change the same salient site without checking other
plausible sites.
Intervention: first inventory candidate centers in the actual molecular graph.
For an alkene, compare only genuinely plausible coupling/olefination families;
for a biaryl bearing a tertiary alcohol, distinguish the biaryl coupling site
from possible carbonyl-addition sites. Then compare handle assignments within
the favored family. Include incompatible-group evidence (such as an unprotected
acidic group or electrophile) rather than trusting a syntactic forward join.
Do not force a third family or three candidates when the graph supports fewer.
Do not change unrelated protecting groups just to enlarge the candidate list.

## 4. Reaction labels guide scope; neighboring labels can overlap

Trigger: the student rejects an otherwise plausible one-step transformation
solely because of an overly narrow label interpretation, or focuses on the most
visible group without inspecting other groups.
Intervention: use the label to guide candidate generation, then compare its bond
changes against the whole product. Protection can involve an acyl bond; FGI
can overlap substitution/redox terminology. Explain the overlap rather than
asserting that label names uniquely determine the reaction center.
Do not trigger merely because the student chooses a well-supported direct
transformation instead of a more elaborate alternative.

## 5. Use the molecular graph, not improvised chemical names

Trigger: reasoning changes ring sizes, replaces a nitrile by an alkyne, confuses
an alkene with a carbonyl, or attributes absent elements to the product.
Intervention: one bounded RDKit inventory of relevant atom indices, bonds,
ring membership, formal charge and stereo. Derive candidate edits from that
graph, preserve untouched regions, sanitize and print canonical SMILES. Check
that any named common reagent has the intended ring, functional group and atom
counts; parsing alone does not validate its name.
Do not request another full atom dump after the necessary graph facts are known.
A short exact SMILES answer is preferable to invented scaffold nomenclature.

## 6. Do not invent omitted reagent conventions or charge constraints

Trigger: an atom deficit automatically adds a reagent, a zero-contribution
counterion is automatically removed, or summed charges force a new reactant.
Intervention: distinguish recorded precursor components from a fully balanced
chemical equation. Ordinary water, redox agents, coupling agents, salts and
counterions may be represented differently; product-only instructions do not
establish an exact inclusion convention for every example. Use visible task
information and chemically plausible reagent forms, state uncertainty, and
never claim an unobserved reference includes a particular species.
Do not use this caveat to excuse loss of an entire substrate skeleton or invalid
valence. Preserve explicit ionic and stereochemical information where warranted;
do not invent E/Z or strip stereo solely to match a guessed convention.

## 7. Recover locally after an external tool fails

Trigger: `agent` or a network lookup returns a connection/network error and the
next candidate retries the same route or treats the failure as chemical evidence.
Intervention: keep the current candidate table, continue with local RDKit and
visible observations, and use the remaining budget on one actual decision.
Do not repeatedly install a library that was already imported successfully.
Do not trigger on a first justified bounded request when the route is available.

## 8. Save after a justified provisional decision, not as a mandatory first action

Trigger: the student writes the product, prose, or an unparsed first guess just
to create a file; alternatively it approaches the turn limit without any output.
Intervention: perform the smallest necessary inspection, then write one parsed
provisional candidate with its rationale preserved in the conversation. Refine
it only when a new observation or corrected chemical argument warrants a change.
If a budget is nearly exhausted, prioritize a valid file and concise final text.
Do not overwrite a sound answer solely because its canonical spelling looks new.

## 9. Make completion explicit and avoid self-confirmation loops

Trigger: repeated `cat`, canonicalization or atom counts return the same result;
or a final response has only reasoning and no visible text.
Intervention: after selecting a candidate, verify the actual last-written file
(one line, no maps/prose, valid canonical fragments) and finish with a visible
confirmation. Call it format-checked; do not claim this proves historical exact
match. If a new concrete error is found after verification, repair and recheck;
verification must not permanently lock in a wrong chemical hypothesis.
Do not require a tool call in the final response. Do not re-run unchanged checks.

## Budget and replacement discipline

Use these triggers only when they materially improve the next decision. PASS
sound exploration and recovery; do not spend budget rewriting style or forcing
a fixed workflow. With budget 1, correct the highest-impact visible misconception
and let the student proceed. With budgets 3/5, spend additional interventions on
new evidence or a remaining error, not repeated validation. Keep every replacement
in the student's voice, based only on its real visible prefix.
