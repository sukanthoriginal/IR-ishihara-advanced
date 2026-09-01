import assert from 'node:assert/strict';

import {
  conditionRequiresTransformationInference,
  scoreGlyphResponse,
  summarizeTrialRows,
} from '../advanced/results.mjs';

for (const condition of [
  'visual_aligned_silent',
  'visual_aligned_overlay',
  'visual_aligned_ir_audio',
]) {
  assert.equal(conditionRequiresTransformationInference(condition), false);
}
for (const condition of [
  'visual_silent',
  'visual_complementary_silent',
  'visual_background_audio',
  'ir_audio',
]) {
  assert.equal(conditionRequiresTransformationInference(condition), true);
}

const partial = {
  source_ids: ['one', 'l', 'seven'],
  target_ids: ['four', 'l', 'nine'],
  response_target_ids: ['four', 'l', 'eight-b'],
  correct: 0,
  decoy_selected: 0,
  rt_choice_onset_ms: 4100,
};
assert.deepEqual(scoreGlyphResponse(partial), {
  valid: true,
  glyphTotal: 3,
  glyphCorrect: 2,
  transformedTotal: 2,
  transformedCorrect: 1,
  unchangedTotal: 1,
  unchangedCorrect: 1,
  missedGlyphs: 1,
});

const rows = [
  partial,
  {
    source_ids: 'c|f|p',
    target_ids: 'c|f|p',
    response_target_ids: 'c|f|p',
    correct: 1,
    decoy_selected: 0,
    rt_choice_onset_ms: 1200,
  },
  {
    source_ids: ['v'],
    target_ids: ['x'],
    response_target_ids: ['v'],
    correct: 0,
    decoy_selected: 1,
    rt_choice_onset_ms: 2300,
  },
  {
    source_ids: ['one', 'l'],
    target_ids: ['four', 'e'],
    response_target_ids: ['four'],
    correct: 0,
    decoy_selected: 0,
    rt_choice_onset_ms: 1800,
  },
];

assert.deepEqual(summarizeTrialRows(rows), {
  plateTotal: 4,
  plateCorrect: 1,
  glyphTotal: 7,
  glyphCorrect: 5,
  transformedTotal: 3,
  transformedCorrect: 1,
  unchangedTotal: 4,
  unchangedCorrect: 4,
  decoySelected: 1,
  correctRts: [1200],
  missedGlyphCounts: { 0: 1, 1: 2 },
  invalidGlyphRows: 1,
});

console.log('Advanced Ishihara results-summary tests passed.');
