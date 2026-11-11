---
name: paper-reading
description: Read a research paper the way a researcher does before explaining it, and write down what an explainer needs - the method in execution order, every tensor and size, every number with its table or figure, what was released, and what the paper leaves undefined. Use before planning any explainer video, figure or summary of a paper, or when asked to read, digest or extract a paper.
when_to_use: The user hands over a paper (PDF, arXiv id, DOI) and wants it explained, visualised, summarised or turned into a video; or another skill (an explainer) needs the paper's method in a reliable, cited form.
argument-hint: "[path to the PDF, or arXiv id]"
license: MIT
---

# Reading a paper before explaining it

An explainer is only as true as the reading behind it. This skill turns a paper into `paper.json` and a short
`method.md`, which every later step (scene plan, captions, numbers on screen) cites. Nothing goes on screen that
cannot be traced to a line in one of them.

## The three passes

Read the whole paper three times, each time for something different. Do not skip to the method.

1. **What it claims (10 minutes).** Title, abstract, introduction's contribution list, every figure and its caption,
   conclusion. Write the claim in one sentence, the problem in one sentence, and the contributions as a list. Note
   which contribution is the *method* (what a reader could re-implement) and which are data or benchmarks.
2. **How it works, in the order the code runs.** Read the method section with the framework figure open. Write the
   modules in execution order: for each, its input, its output, its size at each step, and the equation or
   algorithm that defines it. Follow one sample through; if the paper never says a size, compute it from the
   architecture and mark it `derived`.
3. **The details a re-implementer would trip on.** Hyperparameters (with the sentence that states them),
   thresholds, training versus inference differences, pre- and post-processing (resize, normalisation, NMS),
   every number in the tables you may show, and every place the paper is silent.

Tables and figures are often images in the PDF: render the page (`pdftoppm -r 200`) and read them; never guess a
number from text that did not extract.

## What to write

`paper.json`, in this shape (keys may be empty, never invented):

```json
{
  "paper": {"title": "", "authors": [], "venue": "", "year": 0, "doi": "", "arxiv": "", "license": ""},
  "claim": "", "problem": "",
  "contributions": [{"kind": "method|data|benchmark|analysis", "text": ""}],
  "modules": [{"name": "", "order": 1, "input": "", "output": "", "sizes": [""], "defined_by": "Eq. 3 / Alg. 1 / Sec. III-B",
               "parallel_with": []}],
  "equations": [{"id": "Eq. 1", "tex": "", "symbols": {"x": "meaning"}, "where": "Sec."}],
  "hyperparameters": [{"name": "", "value": "", "quote": "", "where": ""}],
  "numbers": [{"value": "", "meaning": "", "where": "Table III, row SiamFC, column noDET"}],
  "released": {"code": "", "weights": "", "data": "", "licenses": ""},
  "gaps": [{"what": "", "why_it_matters": "", "our_assumption": ""}],
  "figures_to_redraw": [{"figure": "Fig. 7", "why": "", "license_note": ""}]
}
```

`method.md`: the method in plain words, one short paragraph per module, in execution order, each ending with the
source it came from. This is what a reviewer (the paper's own field) reads to check the explainer.

## Rules

- **Quote, do not paraphrase, every hyperparameter and threshold**, with its section. "τt and τd are set to 0.9" beats
  "high thresholds".
- **Every number carries its location**: table, row, column; or figure and axis. Numbers measured by us are never
  presented as the paper's, and the paper's are never presented as ours.
- **Separate the paper's method from what it uses.** A tracking paper that plugs in Faster R-CNN does not own
  Faster R-CNN; say which parts are the contribution.
- **Find what was released** before planning: repository, licence, weights, data, and what is *not* there. A video
  that needs the authors' weights cannot be made from a paper that did not release them; plan with what exists and
  label substitutes.
- **List the gaps.** Anything undefined that an explainer must decide (a score's normalisation, an unstated input
  size) goes in `gaps` with the assumption chosen. The explainer states that assumption on screen.
- **Licences of the paper itself**: the publisher's version is usually all rights reserved; the arXiv version may
  carry a Creative Commons licence. Redraw figures from your own real data either way, and cite the original.
- **Cross-check against code** wherever code exists (sign conventions, transposes, inverse matrices, the order of
  steps), and record what was checked.

## Hand-off

When the reading is done, tell the user the claim, the modules in order, the gaps and their assumptions, and what
was and was not released, in a few lines. The explainer plan starts from `paper.json`, not from the PDF.
