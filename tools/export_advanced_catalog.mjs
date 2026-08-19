#!/usr/bin/env node

import {
  advancedCatalogCounts,
  CATALOG_VERSION,
  DIRECT_EDGES,
  enumerateRawCombinations,
  GEOMETRIES,
  RAW_MAPPINGS,
  SOURCE_FAMILIES,
  TERMINAL_GEOMETRIES,
} from '../advanced_ishihara/grammar.mjs';

const formatArgument = process.argv.find(argument => argument.startsWith('--format='));
const format = formatArgument?.split('=', 2)[1] ?? 'summary';

if (format === 'summary') {
  process.stdout.write(`${JSON.stringify(advancedCatalogCounts(), null, 2)}\n`);
} else if (format === 'grammar') {
  process.stdout.write(`${JSON.stringify({
    catalogVersion: CATALOG_VERSION,
    counts: advancedCatalogCounts(),
    geometries: GEOMETRIES,
    directEdges: DIRECT_EDGES,
    sourceFamilies: SOURCE_FAMILIES,
    terminalGeometries: TERMINAL_GEOMETRIES,
    mappings: RAW_MAPPINGS,
  }, null, 2)}\n`);
} else if (format === 'jsonl') {
  for (const combination of enumerateRawCombinations()) {
    process.stdout.write(`${JSON.stringify(combination)}\n`);
  }
} else if (format === 'csv') {
  process.stdout.write([
    'combination_id', 'character_count', 'rank_within_size',
    'source_labels', 'target_labels', 'mapping_ids', 'source_split_pattern',
    'changed_count', 'identity_count', 'terminal_identity_count',
    'all_identity', 'has_change',
  ].join(',') + '\n');
  for (const combination of enumerateRawCombinations()) {
    process.stdout.write([
      combination.combinationId,
      combination.characterCount,
      combination.rankWithinSize,
      csvCell(combination.sourceLabels.join('|')),
      csvCell(combination.targetLabels.join('|')),
      csvCell(combination.mappingIds.join('|')),
      csvCell(combination.sourceSplitPattern.join('|')),
      combination.changedCount,
      combination.identityCount,
      combination.terminalIdentityCount,
      combination.allIdentity,
      combination.hasChange,
    ].join(',') + '\n');
  }
} else {
  process.stderr.write(
    'Usage: node tools/export_advanced_catalog.mjs --format=summary|grammar|jsonl|csv\n',
  );
  process.exitCode = 2;
}

function csvCell(value) {
  return `"${String(value).replaceAll('"', '""')}"`;
}
