# Writing guidelines — check before composing, not after

Applies to every permanent file: ADR, TDD, TODO, CHANGELOG, comment, docstring.

- **Affirmative form only.** Describe what is true, never what changed. Cut
  "no longer", "used to", "previously", "stays", "now", "rather than X",
  "instead of Y". Justifying a change against what came before belongs in
  the commit message.
- **No rhetorical contrast** to make a point land. If the plain statement
  works, the contrast is padding.
- **State the measurement**; it carries the judgment. Replace any adjective
  a number could replace.
- **Measure the thing you name.** A figure entering a permanent file comes
  from timing that step on its own, several times, quoted at the median. A
  measurement of a phase says nothing about which step inside it is slow: a
  7.74 s audit phase went into ADR-0013 as the windowed `LAG` scan, which
  ran in 0.26 s. Say so in the same sentence when a number is inferred from
  a difference between runs rather than measured directly.
- **Delete any sentence that restates the one before it.**
- **Objections go to the owner in conversation**, never into a file.
- **ADRs** hold the decision, the options considered at a line or two each,
  and the reasons. Cut the ADR's own edit trail: what it used to recommend,
  risks it once flagged, questions since closed.

The audience is a reader without the context — which includes the owner in
six months. Write for them, not to ward off a hypothetical edit.
