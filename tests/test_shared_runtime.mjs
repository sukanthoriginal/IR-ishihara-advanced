import assert from 'node:assert/strict';

import { buildCsv, csvCell, safeFilenamePart } from '../shared/csv.mjs';
import {
  calibratedStageSize,
  compactStageSize,
  fitAspectRatio,
  median,
  mulberry32,
  physicalSizeForVisualAngle,
  repeatedStimulusDurationMs,
  shuffle,
  visualAngleDeg,
} from '../shared/timing.mjs';

assert.equal(median([8, 1, 4]), 4);
assert.equal(median([8, 1, 4, 2]), 3);
assert.equal(median([]), null);
assert.equal(repeatedStimulusDurationMs(1050, 3, 250), 3650);
assert.equal(repeatedStimulusDurationMs(1050, 1, 250), 1050);
assert.equal(repeatedStimulusDurationMs(0, 3, 250), 0);

const widthLimited = fitAspectRatio(800, 800, 178 / 64);
assert.equal(widthLimited.width, 800);
assert.equal(widthLimited.width / widthLimited.height, 178 / 64);
const heightLimited = fitAspectRatio(1600, 400, 178 / 64);
assert.equal(heightLimited.height, 400);
assert.equal(heightLimited.width / heightLimited.height, 178 / 64);

assert.deepEqual(compactStageSize({
  availableWidthCssPx: 1600,
  availableHeightCssPx: 900,
  nativeWidthPx: 712,
  nativeHeightPx: 256,
  aspectRatio: 178 / 64,
}), { width: 712, height: 256 });

const widthAtFiftyDegrees = physicalSizeForVisualAngle(50, 100);
assert.ok(Math.abs(widthAtFiftyDegrees - 93.2615316) < 1e-6);
assert.ok(Math.abs(visualAngleDeg(widthAtFiftyDegrees, 100) - 50) < 1e-9);
const calibrated = calibratedStageSize({
  targetWidthAngleDeg: 50,
  viewingDistanceCm: 100,
  displayWidthCm: 100,
  screenWidthCssPx: 1000,
  availableWidthCssPx: 1000,
  availableHeightCssPx: 600,
  aspectRatio: 178 / 64,
});
assert.equal(calibrated.fits, true);
assert.equal(calibrated.width / calibrated.height, 178 / 64);

const firstShuffle = shuffle([1, 2, 3, 4, 5], mulberry32(77));
const secondShuffle = shuffle([1, 2, 3, 4, 5], mulberry32(77));
assert.deepEqual(firstShuffle, secondShuffle);

assert.equal(csvCell('a,"b"'), '"a,""b"""');
assert.equal(csvCell(['l', 'c']), 'l|c');
assert.equal(safeFilenamePart('Sukanth / pilot'), 'Sukanth_pilot');
assert.equal(
  buildCsv([{ id: 1, values: ['l', 'c'] }], ['id', 'values']),
  'id,values\n1,l|c',
);

console.log('Shared runtime tests passed.');
