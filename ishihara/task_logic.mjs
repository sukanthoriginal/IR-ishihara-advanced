export function buildSchedule(manifest, settings, rng) {
  const eligible = manifest.stimuli.filter(item => item.split === settings.split);
  if (!eligible.length) throw new Error(`no stimuli found for ${settings.split} split`);

  if (settings.condition === 'mixed') {
    // Exact within-plate pairing: each chosen dot layout/glyph is presented
    // once with visible hue and once with the aligned IR layer. This removes
    // stimulus identity as an explanation for a condition difference.
    if (settings.numTrials % 2) throw new Error('mixed blocks require an even trial count');
    const pairCount = settings.numTrials / 2;
    const bases = [];
    while (bases.length < pairCount) bases.push(...shuffle([...eligible], rng));
    const paired = bases.slice(0, pairCount).flatMap(stimulus => [
      { stimulus, condition: 'visible' },
      { stimulus, condition: 'ir' },
    ]);
    return shuffle(paired, rng).map(trial => ({
      ...trial,
      choices: shuffle([...manifest.glyphs[settings.split]], rng),
    }));
  }

  const stimuli = [];
  while (stimuli.length < settings.numTrials) stimuli.push(...shuffle([...eligible], rng));
  return stimuli.slice(0, settings.numTrials).map(stimulus => ({
    stimulus,
    condition: settings.condition,
    choices: shuffle([...manifest.glyphs[settings.split]], rng),
  }));
}

export function shuffle(items, rng) {
  for (let i = items.length - 1; i > 0; i -= 1) {
    const j = Math.floor(rng() * (i + 1));
    [items[i], items[j]] = [items[j], items[i]];
  }
  return items;
}

export function median(values) {
  const ordered = [...values].sort((a, b) => a - b);
  const middle = Math.floor(ordered.length / 2);
  return ordered.length % 2 ? ordered[middle] : (ordered[middle - 1] + ordered[middle]) / 2;
}

export function mulberry32(seed) {
  return function random() {
    let value = seed += 0x6D2B79F5;
    value = Math.imul(value ^ value >>> 15, value | 1);
    value ^= value + Math.imul(value ^ value >>> 7, value | 61);
    return ((value ^ value >>> 14) >>> 0) / 4294967296;
  };
}
