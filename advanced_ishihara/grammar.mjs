/**
 * Declarative geometry grammar for the Advanced IR-Ishihara experiment.
 *
 * This module describes possibilities only. It does not rasterize plates,
 * construct response foils, or generate audio. Ordered one- through
 * three-position sequences are addressed lazily by rank so importing the
 * module never materializes the complete catalog.
 */

export const CATALOG_VERSION = 1;
export const MIN_COMBINATION_LENGTH = 1;
export const MAX_COMBINATION_LENGTH = 3;

const geometryDefinitions = [
  ['one', '1'],
  ['gamma', 'Γ'],
  ['l', 'L'],
  ['t', 'T'],
  ['v', 'V'],
  ['caret', '∧'],
  ['three', '3'],
  ['four', '4'],
  ['seven', '7'],
  ['nine', '9'],
  ['zero-o', '0/O'],
  ['c', 'C'],
  ['e', 'E'],
  ['f', 'F'],
  ['h', 'H'],
  ['j', 'J'],
  ['p', 'P'],
  ['u', 'U'],
  ['six', '6'],
  ['i', 'I'],
  ['x', 'X'],
  ['y', 'Y'],
  ['a', 'A'],
  ['eight-b', '8/B'],
  ['q', 'Q'],
  ['g', 'G'],
  ['r', 'R'],
];

export const GEOMETRIES = Object.freeze(geometryDefinitions.map(
  ([id, label]) => Object.freeze({ id, label }),
));

const geometryById = new Map(GEOMETRIES.map(geometry => [geometry.id, geometry]));

/** Minimal addition-only edges. All valid changed mappings are their closure. */
export const DIRECT_EDGES = Object.freeze({
  one: Object.freeze(['four', 'seven', 'j']),
  gamma: Object.freeze(['c', 'f']),
  l: Object.freeze(['c', 'u']),
  t: Object.freeze(['i']),
  v: Object.freeze(['x', 'y']),
  caret: Object.freeze(['a']),
  three: Object.freeze(['nine']),
  four: Object.freeze(['nine', 'h']),
  seven: Object.freeze(['three', 'zero-o']),
  nine: Object.freeze(['eight-b']),
  'zero-o': Object.freeze(['eight-b', 'q']),
  c: Object.freeze(['zero-o', 'e', 'g']),
  e: Object.freeze(['six']),
  f: Object.freeze(['e', 'p']),
  h: Object.freeze(['eight-b']),
  j: Object.freeze(['three', 'u']),
  p: Object.freeze(['eight-b', 'r']),
  u: Object.freeze(['zero-o']),
  six: Object.freeze(['eight-b']),
});

/**
 * Advisor-approved source-family split. Every identity and every reachable
 * target belonging to a source follows that source's assignment.
 */
export const TRAIN_SOURCE_IDS = Object.freeze([
  'one', 'l', 't', 'caret', 'three', 'seven', 'nine', 'zero-o',
  'c', 'f', 'p', 'u', 'six',
]);

export const TEST_SOURCE_IDS = Object.freeze([
  'gamma', 'v', 'j', 'four', 'e', 'h',
]);

const trainSourceIdSet = new Set(TRAIN_SOURCE_IDS);
const testSourceIdSet = new Set(TEST_SOURCE_IDS);

function reachableTargetIdSet(sourceId) {
  const reached = new Set();

  function visit(currentId) {
    for (const targetId of DIRECT_EDGES[currentId] ?? []) {
      if (reached.has(targetId)) continue;
      reached.add(targetId);
      visit(targetId);
    }
  }

  visit(sourceId);
  return reached;
}

function sourceSplitFor(sourceId) {
  if (trainSourceIdSet.has(sourceId)) return 'train';
  if (testSourceIdSet.has(sourceId)) return 'test';
  return 'terminal';
}

export const SOURCE_FAMILIES = Object.freeze(
  GEOMETRIES
    .filter(geometry => Object.hasOwn(DIRECT_EDGES, geometry.id))
    .map(source => {
      const reachableIds = reachableTargetIdSet(source.id);
      const changedTargetIds = GEOMETRIES
        .filter(geometry => reachableIds.has(geometry.id))
        .map(geometry => geometry.id);
      return Object.freeze({
        sourceId: source.id,
        sourceLabel: source.label,
        split: sourceSplitFor(source.id),
        directTargetIds: DIRECT_EDGES[source.id],
        changedTargetIds: Object.freeze(changedTargetIds),
        changedTargetLabels: Object.freeze(changedTargetIds.map(
          targetId => geometryById.get(targetId).label,
        )),
        changedCount: changedTargetIds.length,
        familySize: changedTargetIds.length + 1,
      });
    }),
);

const sourceFamilyById = new Map(
  SOURCE_FAMILIES.map(family => [family.sourceId, family]),
);

export const TERMINAL_GEOMETRIES = Object.freeze(
  GEOMETRIES.filter(geometry => !sourceFamilyById.has(geometry.id)),
);

function mappingId(sourceId, targetId) {
  return `${sourceId}--${targetId}`;
}

function makeMapping(source, target, kind) {
  const sourceFamily = sourceFamilyById.get(source.id);
  return Object.freeze({
    id: mappingId(source.id, target.id),
    sourceId: source.id,
    sourceLabel: source.label,
    targetId: target.id,
    targetLabel: target.label,
    kind,
    changed: kind === 'change',
    sourceSplit: sourceFamily?.split ?? 'terminal',
    transformableSource: Boolean(sourceFamily),
    terminalIdentity: kind === 'identity' && !sourceFamily,
  });
}

const rawMappings = [];
for (const source of GEOMETRIES) {
  rawMappings.push(makeMapping(source, source, 'identity'));
  const family = sourceFamilyById.get(source.id);
  for (const targetId of family?.changedTargetIds ?? []) {
    rawMappings.push(makeMapping(source, geometryById.get(targetId), 'change'));
  }
}

export const RAW_MAPPINGS = Object.freeze(rawMappings);
export const CHANGED_MAPPINGS = Object.freeze(
  RAW_MAPPINGS.filter(mapping => mapping.changed),
);
export const IDENTITY_MAPPINGS = Object.freeze(
  RAW_MAPPINGS.filter(mapping => !mapping.changed),
);
export const TRAIN_MAPPINGS = Object.freeze(
  RAW_MAPPINGS.filter(mapping => mapping.sourceSplit === 'train'),
);
export const TEST_MAPPINGS = Object.freeze(
  RAW_MAPPINGS.filter(mapping => mapping.sourceSplit === 'test'),
);
export const TERMINAL_IDENTITY_MAPPINGS = Object.freeze(
  RAW_MAPPINGS.filter(mapping => mapping.terminalIdentity),
);

const mappingIndexById = new Map(
  RAW_MAPPINGS.map((mapping, index) => [mapping.id, index]),
);

function assertIntegerInRange(value, minimum, maximum, name) {
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new RangeError(`${name} must be an integer from ${minimum} to ${maximum}`);
  }
}

export function rawCombinationCount(characterCount) {
  assertIntegerInRange(
    characterCount,
    MIN_COMBINATION_LENGTH,
    MAX_COMBINATION_LENGTH,
    'characterCount',
  );
  return RAW_MAPPINGS.length ** characterCount;
}

export function rawCombinationAt(characterCount, rankWithinSize) {
  const count = rawCombinationCount(characterCount);
  assertIntegerInRange(rankWithinSize, 0, count - 1, 'rankWithinSize');

  const mappingIndices = new Array(characterCount);
  let remainingRank = rankWithinSize;
  for (let position = characterCount - 1; position >= 0; position -= 1) {
    mappingIndices[position] = remainingRank % RAW_MAPPINGS.length;
    remainingRank = Math.floor(remainingRank / RAW_MAPPINGS.length);
  }
  return buildCombination(mappingIndices, rankWithinSize);
}

export function rawCombinationFromMappingIds(mappingIds) {
  assertIntegerInRange(
    mappingIds.length,
    MIN_COMBINATION_LENGTH,
    MAX_COMBINATION_LENGTH,
    'mappingIds.length',
  );

  const mappingIndices = mappingIds.map(id => {
    const index = mappingIndexById.get(id);
    if (index === undefined) throw new RangeError(`unknown mapping ID: ${id}`);
    return index;
  });
  const rankWithinSize = mappingIndices.reduce(
    (rank, index) => rank * RAW_MAPPINGS.length + index,
    0,
  );
  return buildCombination(mappingIndices, rankWithinSize);
}

function buildCombination(mappingIndices, rankWithinSize) {
  const mappings = mappingIndices.map(index => RAW_MAPPINGS[index]);
  const changedCount = mappings.filter(mapping => mapping.changed).length;
  const identityCount = mappings.length - changedCount;
  const terminalIdentityCount = mappings.filter(
    mapping => mapping.terminalIdentity,
  ).length;

  return Object.freeze({
    combinationId: `raw-v${CATALOG_VERSION}-l${mappings.length}-r${rankWithinSize}`,
    characterCount: mappings.length,
    rankWithinSize,
    mappingIds: Object.freeze(mappings.map(mapping => mapping.id)),
    sourceIds: Object.freeze(mappings.map(mapping => mapping.sourceId)),
    sourceLabels: Object.freeze(mappings.map(mapping => mapping.sourceLabel)),
    targetIds: Object.freeze(mappings.map(mapping => mapping.targetId)),
    targetLabels: Object.freeze(mappings.map(mapping => mapping.targetLabel)),
    sourceSplitPattern: Object.freeze(mappings.map(mapping => mapping.sourceSplit)),
    changedCount,
    identityCount,
    terminalIdentityCount,
    allIdentity: changedCount === 0,
    hasChange: changedCount > 0,
  });
}

export function* enumerateRawCombinations({
  lengths = [1, 2, 3],
  predicate = null,
} = {}) {
  for (const characterCount of lengths) {
    const count = rawCombinationCount(characterCount);
    for (let rankWithinSize = 0; rankWithinSize < count; rankWithinSize += 1) {
      const combination = rawCombinationAt(characterCount, rankWithinSize);
      if (!predicate || predicate(combination)) yield combination;
    }
  }
}

export function advancedCatalogCounts() {
  const combinationsByLength = Object.fromEntries(
    [1, 2, 3].map(length => [length, rawCombinationCount(length)]),
  );
  const identityOnlyByLength = Object.fromEntries(
    [1, 2, 3].map(length => [length, IDENTITY_MAPPINGS.length ** length]),
  );
  const rawCombinationTotal = Object.values(combinationsByLength).reduce(
    (total, count) => total + count,
    0,
  );
  const identityOnlyTotal = Object.values(identityOnlyByLength).reduce(
    (total, count) => total + count,
    0,
  );

  return Object.freeze({
    geometryCount: GEOMETRIES.length,
    transformableSourceCount: SOURCE_FAMILIES.length,
    terminalGeometryCount: TERMINAL_GEOMETRIES.length,
    directEdgeCount: Object.values(DIRECT_EDGES).reduce(
      (total, targets) => total + targets.length,
      0,
    ),
    changedMappingCount: CHANGED_MAPPINGS.length,
    identityMappingCount: IDENTITY_MAPPINGS.length,
    rawMappingCount: RAW_MAPPINGS.length,
    trainSourceCount: TRAIN_SOURCE_IDS.length,
    testSourceCount: TEST_SOURCE_IDS.length,
    trainChangedMappingCount: TRAIN_MAPPINGS.filter(mapping => mapping.changed).length,
    testChangedMappingCount: TEST_MAPPINGS.filter(mapping => mapping.changed).length,
    trainMappingCount: TRAIN_MAPPINGS.length,
    testMappingCount: TEST_MAPPINGS.length,
    terminalIdentityMappingCount: TERMINAL_IDENTITY_MAPPINGS.length,
    combinationsByLength: Object.freeze(combinationsByLength),
    rawCombinationTotal,
    identityOnlyByLength: Object.freeze(identityOnlyByLength),
    identityOnlyTotal,
    atLeastOneChangeTotal: rawCombinationTotal - identityOnlyTotal,
  });
}

function verifyGrammar() {
  const counts = advancedCatalogCounts();
  const expected = {
    geometryCount: 27,
    transformableSourceCount: 19,
    terminalGeometryCount: 8,
    directEdgeCount: 32,
    changedMappingCount: 71,
    identityMappingCount: 27,
    rawMappingCount: 98,
    trainSourceCount: 13,
    testSourceCount: 6,
    trainChangedMappingCount: 47,
    testChangedMappingCount: 24,
    trainMappingCount: 60,
    testMappingCount: 30,
    terminalIdentityMappingCount: 8,
    rawCombinationTotal: 950_894,
    identityOnlyTotal: 20_439,
    atLeastOneChangeTotal: 930_455,
  };

  for (const [name, expectedValue] of Object.entries(expected)) {
    if (counts[name] !== expectedValue) {
      throw new Error(`${name}: expected ${expectedValue}, received ${counts[name]}`);
    }
  }

  const allAssignedSourceIds = new Set([...TRAIN_SOURCE_IDS, ...TEST_SOURCE_IDS]);
  if (allAssignedSourceIds.size !== SOURCE_FAMILIES.length) {
    throw new Error('train/test source families overlap or do not cover all sources');
  }
  for (const family of SOURCE_FAMILIES) {
    if (!allAssignedSourceIds.has(family.sourceId)) {
      throw new Error(`unassigned source family: ${family.sourceId}`);
    }
  }
}

verifyGrammar();
