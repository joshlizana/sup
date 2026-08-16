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
- **Delete any sentence that restates the one before it.**
- **Objections go to the owner in conversation**, never into a file.
- **ADRs** hold the decision, the options considered at a line or two each,
  and the reasons. Cut the ADR's own edit trail: what it used to recommend,
  risks it once flagged, questions since closed.

The audience is a reader without the context — which includes the owner in
six months. Write for them, not to ward off a hypothetical edit.
