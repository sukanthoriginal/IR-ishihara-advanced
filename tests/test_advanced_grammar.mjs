import assert from 'node:assert/strict';

import {
  advancedCatalogCounts,
  CHANGED_MAPPINGS,
  GEOMETRIES,
  IDENTITY_MAPPINGS,
  RAW_MAPPINGS,
  rawCombinationAt,
  rawCombinationFromMappingIds,
  SOURCE_FAMILIES,
  TERMINAL_GEOMETRIES,
  TEST_MAPPINGS,
  TEST_SOURCE_IDS,
  TRAIN_MAPPINGS,
  TRAIN_SOURCE_IDS,
} from '../advanced_ishihara/grammar.mjs';

const counts = advancedCatalogCounts();
assert.equal(GEOMETRIES.length, 27);
assert.equal(SOURCE_FAMILIES.length, 19);
assert.equal(TERMINAL_GEOMETRIES.length, 8);
assert.equal(CHANGED_MAPPINGS.length, 71);
assert.equal(IDENTITY_MAPPINGS.length, 27);
assert.equal(RAW_MAPPINGS.length, 98);

assert.deepEqual(counts.combinationsByLength, {
  1: 98,
  2: 9_604,
  3: 941_192,
});
assert.equal(counts.rawCombinationTotal, 950_894);
assert.deepEqual(counts.identityOnlyByLength, {
  1: 27,
  2: 729,
  3: 19_683,
});
assert.equal(counts.identityOnlyTotal, 20_439);
assert.equal(counts.atLeastOneChangeTotal, 930_455);

assert.equal(TRAIN_SOURCE_IDS.length, 13);
assert.equal(TEST_SOURCE_IDS.length, 6);
assert.equal(new Set([...TRAIN_SOURCE_IDS, ...TEST_SOURCE_IDS]).size, 19);
assert.equal(TRAIN_MAPPINGS.length, 60);
assert.equal(TEST_MAPPINGS.length, 30);
assert.equal(TRAIN_MAPPINGS.filter(mapping => mapping.changed).length, 47);
assert.equal(TEST_MAPPINGS.filter(mapping => mapping.changed).length, 24);

for (const family of SOURCE_FAMILIES) {
  const familyMappings = RAW_MAPPINGS.filter(
    mapping => mapping.sourceId === family.sourceId,
  );
  assert.equal(familyMappings.length, family.familySize);
  assert.ok(familyMappings.every(mapping => mapping.sourceSplit === family.split));
}

const familyBySourceId = new Map(
  SOURCE_FAMILIES.map(family => [family.sourceId, family]),
);
assert.deepEqual(familyBySourceId.get('l').changedTargetLabels, [
  '0/O', 'C', 'E', 'U', '6', '8/B', 'Q', 'G',
]);
assert.deepEqual(familyBySourceId.get('gamma').changedTargetLabels, [
  '0/O', 'C', 'E', 'F', 'P', '6', '8/B', 'Q', 'G', 'R',
]);
assert.equal(familyBySourceId.get('l').split, 'train');
assert.equal(familyBySourceId.get('gamma').split, 'test');

assert.deepEqual(
  TERMINAL_GEOMETRIES.map(geometry => geometry.label),
  ['I', 'X', 'Y', 'A', '8/B', 'Q', 'G', 'R'],
);

const lIdentity = RAW_MAPPINGS.find(mapping => mapping.id === 'l--l');
const lToC = RAW_MAPPINGS.find(mapping => mapping.id === 'l--c');
assert.ok(lIdentity);
assert.ok(lToC);

const repeated = rawCombinationFromMappingIds([lToC.id, lToC.id]);
assert.deepEqual(repeated.mappingIds, ['l--c', 'l--c']);
assert.equal(repeated.changedCount, 2);

const identityThenChange = rawCombinationFromMappingIds([lIdentity.id, lToC.id]);
const changeThenIdentity = rawCombinationFromMappingIds([lToC.id, lIdentity.id]);
assert.notEqual(identityThenChange.combinationId, changeThenIdentity.combinationId);
assert.deepEqual(identityThenChange.sourceLabels, ['L', 'L']);
assert.deepEqual(identityThenChange.targetLabels, ['L', 'C']);
assert.equal(identityThenChange.changedCount, 1);
assert.equal(identityThenChange.identityCount, 1);

assert.deepEqual(
  rawCombinationAt(
    identityThenChange.characterCount,
    identityThenChange.rankWithinSize,
  ),
  identityThenChange,
);
assert.equal(rawCombinationAt(1, 0).combinationId, 'raw-v1-l1-r0');
assert.equal(rawCombinationAt(3, 941_191).rankWithinSize, 941_191);

console.log('Advanced Ishihara grammar tests passed.');
