---
name: retrosynthesis-candidates
description: Generate and compare plausible immediate precursors from a product graph and reaction label, preserving structure and distinguishing chemical ranking from format checks.
---

# Teacher use

Apply this only to a relevant student-visible retrosynthesis decision. Use
`memory:accumulated-failures` version 2 for triggers and non-trigger cases.
Preserve sound student work and translate a needed correction into its next
native execute action or visible final response. Do not impose a mandatory
first-step replacement or a fixed number of candidate sets.

## Working procedure

1. Read the product and reaction label. Parse the graph with RDKit when ring,
   connectivity, charge or stereo interpretation matters. Inventory relevant
   sites once, rather than repeatedly printing the full molecule.
2. Propose a small set of distinct plausible immediate transformations. First
   compare reaction families/sites; only then compare leaving-group identities
   or protecting groups within a family. One uniquely supported candidate is
   acceptable. Broad labels do not always separate neighboring reaction classes.
3. Derive the edited main scaffolds from the parsed molecule; sanitize every
   component and inspect unexpected graph/valence/stereo changes. Retain groups
   unrelated to the proposed step. A single recorded reaction can include several
   linked bond edits, so a simple MCS changed-site count is only a warning.
4. Rank by chemical feasibility and compatibility with the actual functional
   groups, not smallest reagent size, most balanced fragment sizes, or counts
   claimed from hidden references. A custom forward transform tests the chosen
   edit but is not independent proof of the original reaction route.
5. When product plus label underdetermines the original reagent/precursor,
   acknowledge that internally and choose one chemically motivated hypothesis.
   Do not write multiple alternative sets to the answer file. Do not invent
   task-specific reference frequencies or exact-route recall as evidence.
6. Save one parsed, canonical, unmapped set to the required file; verify the
   exact saved contents after the last write. End with visible text. Correct a
   newly discovered error if needed, but avoid unchanged re-check loops.

## Candidate families to consider when the graph supports them

- Protection/deprotection: identify the exposed/protected heteroatom and
  preserve other groups. Compare plausible protecting-group identities and
  compatibility rather than always taking the smallest ester/ether. Product
  structure alone often does not specify the original protecting group.
- Acylation: identify the acyl bond and nucleophile. Acids, activated derivatives,
  acid chlorides and anhydrides are hypotheses to compare, not a fixed donor
  hierarchy. Multiple acyl groups require site reasoning.
- C-C formation: consider supported cross-couplings, carbonyl additions,
  olefinations, condensations or cyclizations. Compare coupling orientation,
  reactive handles and group compatibility before selecting halide identity.
- Heteroatom alkylation/arylation and FGA: identify the new bond or substituent;
  distinguish substitution from atom/group transfer. Do not replace a whole
  reagent with an arbitrary atom-containing fragment merely to satisfy counts.
- Heterocycle formation: identify the actual ring graph; account for which
  precursor contributes each ring atom and substituent before writing SMILES.
  Preserve unrelated rings and protecting groups.
- Redox/FGI: inventory relevant centers and plausible oxidation states. Do not
  force the nearest rung or the smallest atom delta. Handle regioisomerism,
  explicit stereochemistry and sensitive functional groups consistently.

## Representation and evidence limits

Use RDKit canonicalization to avoid accidental alternate spellings; it cannot
infer a missing protecting group, the recorded halide, salt representation or
reaction conditions. Atom inventory/MCS/charge checks can reveal graph damage,
but neither their PASS nor their FAIL alone identifies the correct recorded set.
The answer list is not necessarily a fully balanced equation: do not manufacture
extra water/reagents/counterions solely to balance atoms or total charge.
Preserve specified stereochemistry at untouched centers; no blanket insertion of
E geometry or removal of chiral tags. Validate common reagent identity beyond
parseability when it affects the hypothesis.

## Completion

The file contains exactly one chosen reactant set, dot-separated, with no prose
or mapping numbers; all components parse. Read back the last write and provide a
visible final response. Existing component scripts are inspection aids, not an
oracle: their format/graph verdicts do not establish reaction correctness or
reference membership. Network/delegation failures provide no chemistry evidence.
