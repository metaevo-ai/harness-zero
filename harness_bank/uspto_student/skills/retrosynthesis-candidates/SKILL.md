---
name: retrosynthesis-candidates
description: Workflow for single-step retrosynthesis answer tasks graded by exact reactant-SET string match — enumerate at least 3 candidate disconnections for the NAMED reaction type, filter them mechanically (parse + element balance), rank by dataset-frequency prior, commit to ONE; includes reactant-set inclusion conventions and representation pitfalls (tautomers, stereo tags, atom mapping) that score zero like a wrong molecule.
---

# Retrosynthesis: candidate enumeration under exact-match grading

The grader compares your `/app/answer.txt` reactant strings as a lowercased
SET against the reference — order-free, but with NO chemical canonicalization.
A representation slip (wrong tautomer, an extra stereo tag, a leftover
atom-mapping number) scores exactly zero, same as a wrong molecule. Most
failed runs are not chemistry failures; they are "first plausible guess,
zero mechanical checks".

## Workflow (non-negotiable)

1. Parse the product with RDKit first (`pip install rdkit` if needed, or let
   `verify_answer` do it). Ring membership, heteroatoms, and
   substituent positions decide the disconnection — never reason from the
   raw string alone.
2. Disconnect ONLY the bond(s) the NAMED reaction type forms. Protecting
   groups elsewhere in the molecule (a Boc on another nitrogen, a ketal, a
   silyl ether) are part of the substrate — do not strip them into extra
   reactants. Over-disconnection adds wrong reactants; under-disconnection
   drops real ones.
3. Before enumerating, try to RECALL: if this exact reaction (or the standard
   patent procedure for this product via this reaction type) is one you
   recognize from the USPTO/patent literature, write the recorded reactants
   down first — recall is an independent channel from reasoning and is often
   more accurate for classic reactions. Then enumerate at least 3 candidate
   reactant sets from the transform table below. Write each candidate's
   SMILES down explicitly.
4. Filter every candidate mechanically: each part must parse, and the
   reactant element counts must cover the product's heavy atoms (deficit =
   a co-reactant is missing). `verify_answer` runs this battery.
5. Rank the survivors under TWO frames before committing to ONE: (a) this
   dataset's recorded frequency (the per-class figures below), and (b)
   forward-reaction correctness (the electrophile bears a leaving group, the
   transform is chemically real). When the two frames disagree, take the
   smallest atom delta vs the product — the minimal edit outranks every
   prior. The answer file holds exactly one candidate set — hedging is
   impossible, and anchoring on the first idea without enumerating is the
   dominant failure. A verification step must DISCRIMINATE between the
   enumerated candidates — re-deriving your own candidate's formula or
   re-reading the answer file proves consistency, not correctness, and a
   wrong candidate is usually self-consistent too.

## Reactant-set inclusion conventions (this dataset)

- Reactants = every species whose atoms land in the product. For
  Protections / Acylation / Alkylation: the protecting-group or acyl/alkyl
  REAGENT is a reactant (e.g. the dicarbonate for Boc, an anhydride for
  acetyl).
- For Deprotections: the answer is usually the single protected substrate;
  hydrolysis/deprotection agents (acid, base, H2) are NOT listed.
- Atom-mapping numbers (`:1` etc.) must be stripped.
- Multi-step sequences are not expected: give the reactants of the ONE
  named step only.
- A bare element or ion (a lone halogen atom, `Na`, `Cl`) is NEVER a
  recorded reactant — every element the product gained is delivered by a
  named molecule (the standard reagent for that transformation); if you
  cannot point at which product atoms a co-reactant supplies, drop it.
- Amine reactants have two first-class spellings — free base and protonated
  `[NH3+]` salt; when the reaction context implies an amine salt (amine
  protections, products recorded as HCl salts), enumerate both explicitly
  instead of defaulting to the neutral form.

## Transform table by named reaction type

- Protections: substrate + protecting reagent. Boc → di-tert-butyl
  dicarbonate — by far the dominant protection in this dataset (Boc2O in 13
  of 15 protection references); Cbz → Cbz-Cl; Fmoc → Fmoc-Cl; silyl ether →
  silyl chloride; acetal/ketal → carbonyl + diol (or diol + carbonyl,
  direction matters). The deprotected heteroatom's protonation state is where
  answers die — a lactam/heteroaryl N-H must carry `[nH]`, not bare `n`.
  Build the free substrate by DELETING the protecting group from the product
  and changing nothing else, and anchor the choice with atom arithmetic: its
  heavy-atom count must equal the product's minus the protecting group's (a
  Boc group is 7 heavy atoms, C5O2) — a candidate whose count violates the
  arithmetic is a redrawn skeleton, not a stripped precursor.
- Deprotections: the protected substrate alone. Carboxylic acid → ester
  homolog is UNDERDETERMINED (methyl / ethyl / benzyl / tert-butyl): treat
  it as an explicit ranked guess — in this dataset methyl and ethyl are a
  genuine coin flip (15 methyl vs 14 ethyl among 150 references), so do NOT
  break the tie by habit; rank by the smallest atom delta first and look for
  any recorded hint in the task, never infer the ester from other groups in
  the molecule (an N-tert-butyl elsewhere is no evidence for a tert-butyl
  ester). Amine → Boc/Cbz/Fmoc carbamate; phenol/alcohol → silyl/benzyl
  ether.
- Acylation and related processes: free amine/alcohol substrate + acyl
  source. In this dataset the FREE CARBOXYLIC ACID is the dominant recorded
  acyl donor (8 of 15 references) — anhydride and acetyl chloride are rare
  (1 each). Rank the free acid first; do not reach for Ac2O or acetyl
  chloride out of textbook habit. Disconnect only the most recently formed
  acyl bond.
- C-C bond formation: enumerate the standard families before choosing —
  cross-coupling (Suzuki/Stille/Negishi/Kumada/Heck: aryl/vinyl halide +
  organometallic or alkene), carbonyl olefination (Wittig/Horner:
  aldehyde/ketone + phosphonium ylide or phosphonate), organometallic
  addition to carbonyls, aldol/enolate chemistry, cyanation. Pick by what
  the product's functional groups actually support (e.g. a terminal vinyl
  arene is as consistent with olefination of an aldehyde as with
  cross-coupling of a vinyl metal). Halide identity on the electrophile is a
  ranked choice, never a guess: enumerate Cl / Br / I variants as separate
  candidate sets and rank by this dataset's actual frequency — Cl and Br are
  the same order of magnitude (44 Cl vs 32 Br among 150 references, I
  essentially absent) — so do NOT default to Br out of cross-coupling habit.
  Complementary Suzuki handle assignments (which partner bears the halide vs
  the boronic acid) all pass mechanical checks equally — rank by chemistry:
  when one partner is a 2-amino-azaheterocycle, the heterocycle bears the
  halide and the carbocyclic partner bears the boronic acid
  (2-aminoheteroaryl boronic acids protodeboronate and are rarely recorded).
  An aryl–alkyne motif records predominantly as Sonogashira — check the
  aryl-halide split before a ketone + acetylide reading.
- Heteroatom alkylation/arylation: the recorded set is TWO components — the
  dealkylated substrate plus the alkylating agent (e.g. an alkyl halide) —
  and the only bond disconnected is the heteroatom–substituent bond, never a
  ring bond. Enumerate the alkylating agent across halide identities
  (Cl / Br / I) as separate candidate sets; N- and O-methylation records most
  often use methyl iodide. With several heteroatom–carbon bonds available,
  rank sites by the acidity of the freed heteroatom: a phenol/enol OH
  outranks any aliphatic alcohol. Check the freed heteroatom's protonation
  state (`[nH]` vs bare `n`).
- Functional-group addition (a new group on a C-H, e.g. benzylic/allylic
  halogenation): TWO components — the de-functionalized substrate plus the
  addition reagent — and pre-existing amide/ester bonds in the product are
  NOT the disconnection site. N-bromosuccinimide is the recorded halogenating
  reagent in this dataset at overwhelming frequency (13 of 15 references) —
  ship NBS for bromination, and do not invent exotic alternatives.
- Heterocycle formation: the newly formed ring appears in NO reactant —
  submitting the intact ring system as a reactant means the reaction-type
  hint was ignored. Identify the ring class (size, heteroatom count,
  unsaturation) from RDKit's ring inventory before choosing the named
  cyclization to reverse, and write the ring-atom provenance table (which
  partner supplies each ring atom and substituent) BEFORE writing any partner
  SMILES — assigning a substituent to the wrong partner is the classic silent
  loss.
- Reductions / Oxidations: usually a single substrate (the redox reagent is
  not listed; exception — atom-transfer oxidants whose oxygen ends up IN the
  product ARE recorded: peracid epoxidation and sulfide → sulfoxide/sulfone
  oxidation list the peracid, e.g. mCPBA). An epoxide records overwhelmingly
  as alkene + peracid epoxidation — do not disconnect pendant ethers or
  invent diol/dihalide precursors for it. Inventory EVERY
  oxidizable/reducible center (secondary alcohol, sulfide, aldehyde, amine,
  alkene, benzylic carbon) and give each a candidate — the recorded step is
  often not the most salient group. Fix the oxidation-state rung by formula
  delta and this dataset's actual frequency: among the recorded alcohol
  reductions here, carboxylic acid and ester donors dominate (3 acid + 4
  ester in one 15-task slice) while the aldehyde rung is essentially absent
  (0) — rank acid and ester donors FIRST, aldehyde LAST, and never default
  to the aldehyde merely because it is one step away.
- Functional-group interconversion: inventory EVERY functional group of the
  product and give each 1-2 precursor proposals before choosing the
  interconversion locus — the recorded step often changes a plain group, not
  the eye-catching one. A free primary aliphatic amine at a stereocenter
  alongside OTHER protected amines commonly records as the azide (azide
  reduction retains configuration).

## Representation pitfalls (each one is a silent zero)

- Tautomers/protonation: heterocycle N-H vs N, lactam vs lactim — match the
  most standard neutral form and verify it parses with correct valence.
- Stereocenter tags (`@`): keep them consistent with the product's own
  record, and minimal elsewhere — reference reactant strings often omit
  stereo near the reacted center even when the product carries it. An
  invented or surplus `@` scores zero like a wrong atom.
- Alkene E/Z is the OPPOSITE convention: a restored or newly formed acyclic
  C=C (alkene reductions, olefinations) is usually stereo-DEFINED in the
  reference record — write the `/`/`\\` geometry markers on that double bond,
  defaulting to E (trans) for conjugated alkenes unless the scaffold forces
  Z. Shipping it unspecified mismatches a geometry-defined reference exactly
  like a wrong tautomer.
- A restored unsaturation adjacent to a carbonyl records as the CONJUGATED
  isomer (C=O directly on the C=C), not the position isomer one carbon away —
  position isomers share the formula, so no mechanical check can separate
  them; the site choice is explicit chemistry judgment (conjugation
  thermodynamics + dataset frequency).
- Charged vs neutral form: write the form the dataset would record
  (phosphonium salts stay charged, carboxylates neutral unless named).
- Fragment spelling: use the conventional spelling of common reagents (e.g.
  dicarbonate as `CC(C)(C)OC(=O)OC(=O)OC(C)(C)C`).

## Final gate

Before finishing: the answer file exists, holds exactly one candidate set,
and `verify_answer` (with `product` passed verbatim) reports PASS
AFTER the last rewrite. Then stop — a validated near-miss and a validated
perfect answer are indistinguishable from inside; your job is to make the
near-misses mechanical (representation) rather than procedural (no checks).
