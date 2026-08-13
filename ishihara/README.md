# IR–Ishihara task

This task tests whether an IR soundscape can perform the figure–ground grouping
role ordinarily performed by visible hue.

Every generated trial has one dot layout and one target glyph mask. The visible
benchmark colours target and background dots differently. In the IR condition,
all dots are drawn from the same visible-colour distribution and the target
membership exists only in the IR image passed to raspivoice.

The four train glyphs and four held-out glyphs are separated in the manifest.
Response alternatives are images rather than labels, so unfamiliar shapes can
be evaluated without teaching their names.

Primary outcome: four-choice accuracy (chance = 25%).

Secondary outcome: median correct response time measured after stimulus offset
and masking, when the choices become available.
