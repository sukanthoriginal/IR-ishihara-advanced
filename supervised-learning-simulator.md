Meeting with @Sushrut Thorat  / Sukanth on Aug 5th:

Spatial attention and resolution - Testing for auditory cortex for spatial abilities (primitive to complex) - that is conventionally done for eyes / regular vision

Proposed a simulation based experimental interface to move beyond limitations of real-world self-supervised learning environments; add supervised training to  test visual-soundscape associations and ease of collecting data.

Build a simulator, visual-psychophysics-style but for soundscape, run on my monitor, responses by click, input is soundscape only:

Clickable screen with grid targets
Grid progression: 3×3 -> 4×3 -> 16 x 9 ...
Randomized target placement
Progress to alphabets, numbers, images
Fully simulated stimuli, in addition to real world self supervised IR stimuli

Metrics:

Reaction time to identify the correct grid
Accuracy of correct grid
L2 distance (spatial error magnitude)

Spatial resolution, how fine a grid is still discriminable
Number of grids / simulations completed

Controls and comparisons

Self-supervised vs. supervised (simulation-only) — compare accuracy, RT, spatial resolution. (Blocker: no second person available to run the self- vs. supervised arm.)
Baselines with novices - untrained participants at lossfunk on the same simulation.
Expected shape: self-supervised generalizes faster to novel patterns; supervised is slower but yields better immediate RT, accuracy and increase in spatial resolution
The key emergent question regarding reaction time: Do we reach a point where full soundscapes are no longer needed, where partial input suffices to identify spatial location or scene, and reaction times get that fast?

Neuroimaging (later stage):
After significant spatial test results:

Compare fMRI on a cohort of ~22–23-year-olds vs. me, to test whether actual rewiring has occurred in auditory or visual cortex.
EEG: mismatch negativity signal over occipital areas if IR absent in predicted areas, as a surprise
fMRI / MEG: worth pursuing if decoding proves fast, e.g. ~100 ms
Collaboration with IISc if the data is impressive - don't scan until there's data showing performance is at or way way beyond regular human capabilities.

Near-term plan:

2–3 weeks: strong data showing performance increasing, and faster/higher than anyone else's - in the simulation
Check whether the learning curve is saturating sometime
Sleep-consolidation training deferred.
Standing question: how good can we get in function, and what counts as good enough - compare with regular visual baselines?

Color Discussion:

Was already expected that new color is low probability, we had a small discussion that concluded that it looks unreachable with the current setup:
No mixers to learn from. Current IR doesn't blend with existing primaries the way real chromatic dimensions do, or the brain has learned to do - so in this case the brain has to not only learn the IR channel, but how IR channels mix with other wavelengths, which is hard to do if the channel is entirely different and arrives completely in parallel to conventional color processing pipeline
The pathway is LGN, V1, V2, V3, V4 - where atleast color processing happens in every layer - we can't short-circuit into it in every region to submit the IR compositions from auditory cortex
Even if Anthon (a new IR color) isn't possible or perception like representations, does this match primitive functional abilities that neuroscience attributes to visual processing, stage - atleast Cerebral Achomatopsia or Blindsight (damaged V1) like abilities - functional use of chromatic information despite color blindness?
