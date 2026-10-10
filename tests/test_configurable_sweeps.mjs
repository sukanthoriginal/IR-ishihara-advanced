import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { repeatedStimulusDurationMs } from '../shared/timing.mjs';

const app = readFileSync(new URL('../advanced/app.js', import.meta.url), 'utf8');
function functionSource(name, nextName) {
  const start = app.indexOf(`function ${name}(`);
  const end = app.indexOf(`\nfunction ${nextName}(`, start);
  assert.ok(start >= 0 && end > start);
  return app.slice(start, end);
}

const snapshotEqual = vm.runInNewContext(
  functionSource('preparationSnapshotsEqual', 'normalizeRandomizationAudit')
    + '\npreparationSnapshotsEqual;',
);
assert.equal(snapshotEqual({ sweepRepetitions: 3 }, { sweepRepetitions: 3 }), true);
assert.equal(snapshotEqual({ sweepRepetitions: 3 }, { sweepRepetitions: 1 }), false);

const estimatedDuration = vm.runInNewContext(
  functionSource('formatEstimatedDuration', 'updatePreparedPreview')
    + '\nformatEstimatedDuration;',
  { repeatedStimulusDurationMs, ESTIMATED_RESPONSE_SECONDS: 2 },
);
assert.equal(estimatedDuration(30, true), '≈ 3 min 16 sec');
assert.equal(estimatedDuration(30, true, 3), '≈ 3 min 16 sec');
assert.equal(estimatedDuration(30, true, 2), '≈ 2 min 37 sec');
assert.equal(estimatedDuration(30, true, 1), '≈ 1 min 58 sec');

const buffer = { originalSingleSweep: true };
for (const repetitions of [1, 2, 3]) {
  const starts = [], buffers = [];
  const context = {
    currentTime: 10, destination: {},
    createBufferSource() {
      return {
        connect() {},
        start(at) { starts.push(at); buffers.push(this.buffer); },
      };
    },
  };
  const schedule = vm.runInNewContext(
    functionSource('scheduleRepeatedAudio', 'stopAudio') + '\nscheduleRepeatedAudio;',
    {
      audioContext: context,
      activeAudioSources: [],
      manifest: { sweep_repetitions: repetitions, sweep_duration_ms: 1050, inter_sweep_interval_ms: 250 },
      performance: { now: () => 1000 },
    },
  );
  schedule(buffer);
  assert.equal(starts.length, repetitions);
  assert.ok(buffers.every(item => item === buffer));
  starts.forEach((at, index) => assert.ok(Math.abs(at - (10.08 + index * 1.3)) < 1e-10));
}

console.log('Configurable sweep playback, frozen settings and preview tests passed.');
