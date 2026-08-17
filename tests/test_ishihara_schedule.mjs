import assert from 'node:assert/strict';
import {
  bsplineSweepWeights, buildSchedule, calibratedStageSize, compactStageSize,
  fitAspectRatio, median, mulberry32, physicalSizeForVisualAngle,
  repeatedStimulusDurationMs, repeatedSweepPosition,
} from '../ishihara/task_logic.mjs';

const tierGlyphs = {
  '1': ['fp-to-ep', 'ct-to-ci', 'co-to-gq', 'vt-to-yt'],
  '2': ['f-to-e-fork', 'f-to-p-fork', 'f-to-r-fork', 'c-to-g-fork'],
  '3': ['pl-to-bl', 'three-l-to-three-e', 'p3-to-b8', 'l3-to-e3'],
  '4': ['fpt-to-frt', 'cot-to-cqt', 'vct-to-vci', 'plc-to-blc'],
};
const familyIds = {
  '1': 'factorial-pairs',
  '2': 'completion-forks',
  '3': 'multistroke-factorials',
  '4': 'ternary-chimeras',
};
const responseSets = {
  'fp-to-ep': ['pair-ep', 'pair-fp', 'pair-fr', 'pair-er'],
  'ct-to-ci': ['pair-ci', 'pair-ct', 'pair-gt', 'pair-gi'],
  'co-to-gq': ['pair-gq', 'pair-co', 'pair-go', 'pair-cq'],
  'vt-to-yt': ['pair-yt', 'pair-vt', 'pair-vi', 'pair-yi'],
  'f-to-e-fork': ['glyph-e', 'glyph-f', 'glyph-p', 'glyph-r'],
  'f-to-p-fork': ['glyph-p', 'glyph-f', 'glyph-e', 'glyph-r'],
  'f-to-r-fork': ['glyph-r', 'glyph-f', 'glyph-e', 'glyph-p'],
  'c-to-g-fork': ['glyph-g', 'glyph-c', 'glyph-o', 'glyph-q'],
  'pl-to-bl': ['pair-bl', 'pair-pl', 'pair-pe', 'pair-be'],
  'three-l-to-three-e': ['pair-3e', 'pair-3l', 'pair-8l', 'pair-8e'],
  'p3-to-b8': ['pair-b8', 'pair-p3', 'pair-b3', 'pair-p8'],
  'l3-to-e3': ['pair-e3', 'pair-l3', 'pair-l8', 'pair-e8'],
  'fpt-to-frt': ['triple-frt', 'triple-fpt', 'triple-ept', 'triple-fpi'],
  'cot-to-cqt': ['triple-cqt', 'triple-cot', 'triple-got', 'triple-coi'],
  'vct-to-vci': ['triple-vci', 'triple-vct', 'triple-yct', 'triple-vgt'],
  'plc-to-blc': [
    'triple-b-l-caret', 'triple-p-l-caret',
    'triple-p-e-caret', 'triple-p-l-a',
  ],
};
const recipeIds = ['r-ir', 'g-ir', 'rg-ir', 'rgb-ir'];
const manifest = {
  complexity_tiers: Object.fromEntries(
    Object.entries(tierGlyphs).map(([level, glyphs]) => [level, {
      glyphs, family_id: familyIds[level],
    }]),
  ),
  channel_recipes: Object.fromEntries(recipeIds.map(recipe => [recipe, {}])),
  curriculum_recipe_by_complexity: {
    '1': 'r-ir', '2': 'g-ir', '3': 'rg-ir', '4': 'rgb-ir',
  },
  stimuli: Object.entries(tierGlyphs).flatMap(([level, glyphs]) => (
    recipeIds.flatMap(recipe => glyphs.map((glyph, index) => ({
      split: 'train',
      stimulus_id: `l${level}-${recipe}-${index}`,
      glyph_id: glyph,
      response_choices: responseSets[glyph],
      family_id: familyIds[level],
      complexity_level: Number(level),
      channel_recipe_id: recipe,
    })))
  )),
};

const mixed = buildSchedule(manifest, {
  split: 'train', condition: 'mixed', complexity: 'curriculum',
  channelRecipe: 'curriculum', numTrials: 8,
}, mulberry32(42));

assert.equal(mixed.length, 8);
assert.equal(mixed.filter(trial => trial.condition === 'visual-composite').length, 4);
assert.equal(mixed.filter(trial => trial.condition === 'ir-composite').length, 4);

const byPair = new Map();
for (const trial of mixed) {
  const trials = byPair.get(trial.pairId) ?? [];
  trials.push(trial);
  byPair.set(trial.pairId, trials);
}
assert.equal(byPair.size, 4);
for (const trials of byPair.values()) {
  assert.equal(trials.length, 2);
  assert.deepEqual(trials.map(trial => trial.pairPosition), [1, 2]);
  assert.deepEqual(
    new Set(trials.map(trial => trial.condition)),
    new Set(['visual-composite', 'ir-composite']),
  );
  assert.ok(trials[1].pairConditionOrder.endsWith(trials[1].condition));
}
assert.equal(mixed.slice(0, 4).filter(trial => trial.condition === 'visual-composite').length, 2);
assert.equal(mixed.slice(0, 4).filter(trial => trial.condition === 'ir-composite').length, 2);
assert.ok(mixed.slice(0, 4).every(trial => trial.pairPosition === 1));
assert.ok(mixed.slice(4).every(trial => trial.pairPosition === 2));
assert.deepEqual(mixed.slice(0, 4).map(trial => trial.stimulus.complexity_level), [1, 2, 3, 4]);
assert.deepEqual(
  mixed.slice(0, 4).map(trial => trial.stimulus.channel_recipe_id),
  recipeIds,
);
for (const trial of mixed) {
  assert.deepEqual(
    new Set(trial.choices),
    new Set(trial.stimulus.response_choices),
  );
}

const fullCurriculum = buildSchedule(manifest, {
  split: 'train', condition: 'mixed', complexity: 'curriculum',
  channelRecipe: 'curriculum', numTrials: 32,
}, mulberry32(43));
const transformationCounts = new Map();
for (const trial of fullCurriculum) {
  const id = trial.stimulus.glyph_id;
  transformationCounts.set(id, (transformationCounts.get(id) ?? 0) + 1);
}
assert.equal(transformationCounts.size, 16);
assert.deepEqual(new Set(transformationCounts.values()), new Set([2]));

const scrambled = buildSchedule(manifest, {
  split: 'train', condition: 'ir-scrambled', complexity: '2',
  channelRecipe: 'rgb-ir', numTrials: 5,
}, mulberry32(7));
assert.equal(scrambled.length, 5);
assert.ok(scrambled.every(trial => trial.condition === 'ir-scrambled'));
assert.ok(scrambled.every(trial => trial.stimulus.complexity_level === 2));
assert.ok(scrambled.every(trial => trial.stimulus.channel_recipe_id === 'rgb-ir'));

const fixedRecipeProgression = buildSchedule(manifest, {
  split: 'train', condition: 'ir-composite', complexity: 'curriculum',
  channelRecipe: 'r-ir', numTrials: 4,
}, mulberry32(17));
assert.deepEqual(
  fixedRecipeProgression.map(trial => trial.stimulus.complexity_level),
  [1, 2, 3, 4],
);
assert.ok(fixedRecipeProgression.every(
  trial => trial.stimulus.channel_recipe_id === 'r-ir',
));

const fixedShapeProgression = buildSchedule(manifest, {
  split: 'train', condition: 'ir-composite', complexity: '3',
  channelRecipe: 'curriculum', numTrials: 4,
}, mulberry32(23));
assert.deepEqual(
  fixedShapeProgression.map(trial => trial.stimulus.channel_recipe_id), recipeIds,
);
assert.ok(fixedShapeProgression.every(
  trial => trial.stimulus.complexity_level === 3,
));

assert.equal(median([8, 1, 4]), 4);
assert.equal(median([8, 1, 4, 2]), 3);

assert.equal(repeatedStimulusDurationMs(1050, 3, 250), 3650);
assert.equal(repeatedStimulusDurationMs(1050, 1, 250), 1050);
assert.equal(repeatedStimulusDurationMs(0, 3, 250), 0);
assert.deepEqual(repeatedSweepPosition(0, 1050, 3, 250), {
  complete: false, repetitionIndex: 0, sweepActive: true, sweepElapsedMs: 0,
});
assert.equal(repeatedSweepPosition(1050, 1050, 3, 250).sweepActive, false);
assert.equal(repeatedSweepPosition(1300, 1050, 3, 250).repetitionIndex, 1);
assert.equal(repeatedSweepPosition(2600, 1050, 3, 250).repetitionIndex, 2);
assert.equal(repeatedSweepPosition(3649, 1050, 3, 250).sweepActive, true);
assert.equal(repeatedSweepPosition(3650, 1050, 3, 250).complete, true);

const widthLimited = fitAspectRatio(800, 800, 178 / 64);
assert.equal(widthLimited.width, 800);
assert.ok(Math.abs(widthLimited.height - 800 / (178 / 64)) < 1e-9);

const heightLimited = fitAspectRatio(1600, 400, 178 / 64);
assert.equal(heightLimited.height, 400);
assert.equal(heightLimited.width, 400 * (178 / 64));

const nativeExpanded = fitAspectRatio(2020, 1100, 178 / 64);
assert.equal(nativeExpanded.width / nativeExpanded.height, 178 / 64);
assert.equal(nativeExpanded.width, 2020);

const compactNative = compactStageSize({
  availableWidthCssPx: 1600,
  availableHeightCssPx: 900,
  nativeWidthPx: 712,
  nativeHeightPx: 256,
  aspectRatio: 178 / 64,
});
assert.deepEqual(compactNative, { width: 712, height: 256 });
const compactConstrained = compactStageSize({
  availableWidthCssPx: 500,
  availableHeightCssPx: 150,
  nativeWidthPx: 712,
  nativeHeightPx: 256,
  aspectRatio: 178 / 64,
});
assert.equal(compactConstrained.height, 150);
assert.equal(compactConstrained.width, 150 * (178 / 64));

const widthAtFiftyDegrees = physicalSizeForVisualAngle(50, 100);
assert.ok(Math.abs(widthAtFiftyDegrees - 93.2615316) < 1e-6);
const calibratedFit = calibratedStageSize({
  targetWidthAngleDeg: 50,
  viewingDistanceCm: 100,
  displayWidthCm: 100,
  screenWidthCssPx: 1000,
  availableWidthCssPx: 1000,
  availableHeightCssPx: 600,
  aspectRatio: 178 / 64,
});
assert.equal(calibratedFit.fits, true);
assert.ok(Math.abs(calibratedFit.width - widthAtFiftyDegrees * 10) < 1e-6);
assert.ok(Math.abs(calibratedFit.height - calibratedFit.width / (178 / 64)) < 1e-9);
assert.equal(calibratedStageSize({
  targetWidthAngleDeg: 50,
  viewingDistanceCm: 100,
  displayWidthCm: 100,
  screenWidthCssPx: 1000,
  availableWidthCssPx: 800,
  availableHeightCssPx: 600,
  aspectRatio: 178 / 64,
}).fits, false);

const sliceSamples = 283;
assert.deepEqual(bsplineSweepWeights(0, 178, sliceSamples), [
  { column: 0, weight: 1 },
  { column: 1, weight: 0 },
]);
const midpointWeights = bsplineSweepWeights(10 * sliceSamples + 141, 178, sliceSamples);
assert.deepEqual(midpointWeights, [
  { column: 9, weight: 0.125 },
  { column: 10, weight: 0.75 },
  { column: 11, weight: 0.125 },
]);
assert.equal(midpointWeights.reduce((sum, item) => sum + item.weight, 0), 1);
const sliceEnd = bsplineSweepWeights(11 * sliceSamples - 1, 178, sliceSamples);
const nextSliceStart = bsplineSweepWeights(11 * sliceSamples, 178, sliceSamples);
assert.equal(sliceEnd[1].weight, nextSliceStart[0].weight);
assert.equal(sliceEnd[2].weight, nextSliceStart[1].weight);
for (let sample = 0; sample < 50400; sample += 37) {
  const weights = bsplineSweepWeights(sample, 178, sliceSamples);
  assert.ok(weights.every(item => item.column >= 0 && item.column < 178));
  assert.ok(weights.every(item => item.weight >= 0 && item.weight <= 1));
  const totalWeight = weights.reduce((sum, item) => sum + item.weight, 0);
  if (sample < 177 * sliceSamples) {
    assert.ok(Math.abs(totalWeight - 1) < 1e-12);
  } else {
    assert.ok(totalWeight > 0 && totalWeight <= 1); // exact raspivoice right-edge fade
  }
}

console.log('Ishihara schedule tests passed');
