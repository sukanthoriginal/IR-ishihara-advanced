import assert from 'node:assert/strict';
import { buildSchedule, median, mulberry32 } from '../ishihara/task_logic.mjs';

const manifest = {
  glyphs: { train: ['star', 'triangle', 'crescent', 'lightning'] },
  stimuli: Array.from({ length: 8 }, (_, index) => ({
    split: 'train',
    stimulus_id: `stimulus-${index}`,
    glyph_id: ['star', 'triangle', 'crescent', 'lightning'][index % 4],
  })),
};

const mixed = buildSchedule(manifest, {
  split: 'train', condition: 'mixed', numTrials: 8,
}, mulberry32(42));

assert.equal(mixed.length, 8);
assert.equal(mixed.filter(trial => trial.condition === 'visible').length, 4);
assert.equal(mixed.filter(trial => trial.condition === 'ir').length, 4);

const byStimulus = Map.groupBy(mixed, trial => trial.stimulus.stimulus_id);
for (const trials of byStimulus.values()) {
  assert.deepEqual(new Set(trials.map(trial => trial.condition)), new Set(['visible', 'ir']));
}
for (const trial of mixed) {
  assert.deepEqual(new Set(trial.choices), new Set(manifest.glyphs.train));
}

const scrambled = buildSchedule(manifest, {
  split: 'train', condition: 'ir-scrambled', numTrials: 5,
}, mulberry32(7));
assert.equal(scrambled.length, 5);
assert.ok(scrambled.every(trial => trial.condition === 'ir-scrambled'));

assert.equal(median([8, 1, 4]), 4);
assert.equal(median([8, 1, 4, 2]), 3);

console.log('Ishihara schedule tests passed');
