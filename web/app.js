// Phase 3 MVP trial loop. No build step, no framework -- see simulator-plan.md.
// Grid is deliberately blank (no image shown): the task is to localize the
// sound, not to click on a picture.

const setupScreen = document.getElementById('setup-screen');
const trialScreen = document.getElementById('trial-screen');
const endScreen = document.getElementById('end-screen');
const trialStatus = document.getElementById('trial-status');
const gridEl = document.getElementById('grid');
const readyBtn = document.getElementById('ready-btn');
const progressEl = document.getElementById('progress');
const summaryEl = document.getElementById('summary');
const saveStatusEl = document.getElementById('save-status');

let manifest = null;
let audioCtx = null;
let audioBuffers = {}; // wav filename -> AudioBuffer
let trialLog = [];
let session = null; // { participantId, arm, mode, numTrials, trialIndex }
let currentTrial = null; // { cell, audioStartMs, awaitingClick }
let maxErrorPx = null; // image diagonal -- worst case possible l2 error
let chanceErrorPx = null; // simulated mean error of blind random clicking on this grid

document.getElementById('start-btn').addEventListener('click', startSession);
document.getElementById('download-btn').addEventListener('click', downloadCsv);
document.getElementById('retry-btn').addEventListener('click', retryBlock);
document.getElementById('new-session-btn').addEventListener('click', newSession);
readyBtn.addEventListener('click', () => {
  readyBtn.classList.add('hidden');
  runNextTrial();
});

async function startSession() {
  const grid = document.getElementById('grid-select').value;
  const participantId = document.getElementById('participant-id').value.trim() || 'anon';
  const arm = document.getElementById('arm').value;
  const mode = document.getElementById('mode-select').value;
  const numTrials = Math.max(1, parseInt(document.getElementById('num-trials').value, 10) || 24);

  document.getElementById('start-btn').disabled = true;
  document.getElementById('start-btn').textContent = 'Loading stimuli...';

  const manifestUrl = `../stimuli/${grid}/manifest.json`;
  const res = await fetch(manifestUrl);
  if (!res.ok) {
    alert(`Could not load manifest at ${manifestUrl}. Run generate_stimuli.py first.`);
    document.getElementById('start-btn').disabled = false;
    document.getElementById('start-btn').textContent = 'Start block';
    return;
  }
  manifest = await res.json();
  maxErrorPx = Math.hypot(manifest.image_width, manifest.image_height);
  chanceErrorPx = estimateChanceErrorPx(manifest);

  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  audioBuffers = {};
  for (const cell of manifest.cells) {
    const buf = await fetch(`../stimuli/${grid}/${cell.wav}`).then(r => r.arrayBuffer());
    audioBuffers[cell.wav] = await audioCtx.decodeAudioData(buf);
  }

  session = { participantId, arm, mode, numTrials, trialIndex: 0 };
  trialLog = [];

  buildGrid();
  setupScreen.classList.add('hidden');
  trialScreen.classList.remove('hidden');
  runNextTrial();
}

// Monte Carlo baseline for "blind random clicking" on this grid: target drawn
// uniformly from the grid's actual (jittered) cell points -- same as a real
// trial -- against a click uniformly random anywhere in the blank image.
// Grounds "percent better than chance" in this grid's real target layout
// rather than a made-up formula.
function estimateChanceErrorPx(manifest, iterations = 20000) {
  const { cells, image_width: w, image_height: h } = manifest;
  let sum = 0;
  for (let i = 0; i < iterations; i++) {
    const target = cells[Math.floor(Math.random() * cells.length)];
    const rx = Math.random() * w;
    const ry = Math.random() * h;
    sum += Math.hypot(rx - target.target_x_px, ry - target.target_y_px);
  }
  return sum / iterations;
}

function buildGrid() {
  gridEl.style.gridTemplateColumns = `repeat(${manifest.grid_cols}, 1fr)`;
  gridEl.style.gridTemplateRows = `repeat(${manifest.grid_rows}, 1fr)`;
  gridEl.innerHTML = '';
  for (let i = 0; i < manifest.grid_rows * manifest.grid_cols; i++) {
    const c = document.createElement('div');
    c.className = 'cell';
    gridEl.appendChild(c);
  }
  gridEl.addEventListener('click', onGridClick);
}

function runNextTrial() {
  stopCurrentAudio();
  if (session.trialIndex >= session.numTrials) {
    finishSession();
    return;
  }
  const cell = manifest.cells[Math.floor(Math.random() * manifest.cells.length)];
  currentTrial = { cell, awaitingClick: true };

  progressEl.textContent = `Trial ${session.trialIndex + 1} / ${session.numTrials}`;
  trialStatus.textContent = 'Listen, then click where the sound came from...';

  const source = audioCtx.createBufferSource();
  source.buffer = audioBuffers[cell.wav];
  source.connect(audioCtx.destination);
  currentTrial.audioStartMs = performance.now();
  currentTrial.source = source;
  source.start();
}

function stopCurrentAudio() {
  if (currentTrial && currentTrial.source) {
    try { currentTrial.source.stop(); } catch (e) { /* already ended */ }
    currentTrial.source = null;
  }
}

function onGridClick(evt) {
  if (!currentTrial || !currentTrial.awaitingClick) return;
  currentTrial.awaitingClick = false;
  stopCurrentAudio();

  const rect = gridEl.getBoundingClientRect();
  const xImg = (evt.clientX - rect.left) / rect.width * manifest.image_width;
  const yImg = (evt.clientY - rect.top) / rect.height * manifest.image_height;

  const clickCol = Math.min(manifest.grid_cols - 1, Math.max(0, Math.floor(xImg / (manifest.image_width / manifest.grid_cols))));
  const clickRow = Math.min(manifest.grid_rows - 1, Math.max(0, Math.floor(yImg / (manifest.image_height / manifest.grid_rows))));
  const clickedCellIndex = clickRow * manifest.grid_cols + clickCol;

  const target = currentTrial.cell;
  const correct = clickedCellIndex === target.cell_index;
  const rtMs = performance.now() - currentTrial.audioStartMs;
  const l2Error = Math.hypot(xImg - target.target_x_px, yImg - target.target_y_px);

  // How far off, in grid terms and in scale-independent terms -- lets you
  // tell a near-miss (adjacent cell, small error) apart from a click that's
  // just wrong, and compare "nearness" across grids of different cell sizes.
  const cellsOff = Math.max(Math.abs(clickRow - target.row), Math.abs(clickCol - target.col));
  const cellDiagPx = Math.hypot(manifest.image_width / manifest.grid_cols, manifest.image_height / manifest.grid_rows);
  const l2ErrorNorm = l2Error / cellDiagPx;
  const l2ErrorPctOfMax = (l2Error / maxErrorPx) * 100;
  const pctBetterThanChance = (1 - l2Error / chanceErrorPx) * 100;

  trialLog.push({
    participant_id: session.participantId,
    arm: session.arm,
    mode: session.mode,
    grid_rows: manifest.grid_rows,
    grid_cols: manifest.grid_cols,
    image_width: manifest.image_width,
    image_height: manifest.image_height,
    trial_index: session.trialIndex,
    target_cell: target.cell_index,
    target_x_px: target.target_x_px,
    target_y_px: target.target_y_px,
    click_x_px: Math.round(xImg * 10) / 10,
    click_y_px: Math.round(yImg * 10) / 10,
    correct: correct ? 1 : 0,
    rt_ms: Math.round(rtMs),
    l2_error_px: Math.round(l2Error * 10) / 10,
    cells_off: cellsOff,
    l2_error_norm: Math.round(l2ErrorNorm * 1000) / 1000,
    l2_error_pct_of_max: Math.round(l2ErrorPctOfMax * 10) / 10,
    // The exact chance-baseline value used for this block's pct_better_than_chance
    // (a stochastic simulation, re-run per block) -- stored per-row so the
    // relationship l2_error_px / chance_error_px is reconstructible from this
    // CSV alone, without re-simulating or needing the grid's manifest.json.
    chance_error_px: Math.round(chanceErrorPx * 10) / 10,
    pct_better_than_chance: Math.round(pctBetterThanChance * 10) / 10,
    timestamp: new Date().toISOString(),
  });

  if (session.mode === 'train') {
    showFeedback(evt, correct, target);
    trialStatus.textContent = correct ? 'Correct!' : 'Incorrect -- yellow ring shows the true target';
  } else {
    trialStatus.textContent = 'Recorded.';
  }

  session.trialIndex++;
  setTimeout(runNextTrial, 700);
}

function showFeedback(evt, correct, target) {
  const rect = gridEl.getBoundingClientRect();

  const dot = document.createElement('div');
  dot.className = `flash ${correct ? 'correct' : 'incorrect'}`;
  dot.style.left = `${evt.clientX - rect.left}px`;
  dot.style.top = `${evt.clientY - rect.top}px`;
  gridEl.appendChild(dot);
  setTimeout(() => dot.remove(), 650);

  if (!correct) {
    const marker = document.createElement('div');
    marker.className = 'target-marker';
    marker.style.left = `${target.target_x_px / manifest.image_width * rect.width}px`;
    marker.style.top = `${target.target_y_px / manifest.image_height * rect.height}px`;
    gridEl.appendChild(marker);
    setTimeout(() => marker.remove(), 650);
  }
}

function finishSession() {
  trialScreen.classList.add('hidden');
  endScreen.classList.remove('hidden');

  const n = trialLog.length;
  const nCorrect = trialLog.filter(t => t.correct).length;
  const meanRt = trialLog.reduce((s, t) => s + t.rt_ms, 0) / n;
  const meanL2 = trialLog.reduce((s, t) => s + t.l2_error_px, 0) / n;
  const meanL2Norm = trialLog.reduce((s, t) => s + t.l2_error_norm, 0) / n;
  const meanL2PctMax = trialLog.reduce((s, t) => s + t.l2_error_pct_of_max, 0) / n;
  const meanPctBetterThanChance = trialLog.reduce((s, t) => s + t.pct_better_than_chance, 0) / n;
  const gridLabel = `${manifest.grid_cols}x${manifest.grid_rows}`;
  const vsChanceLabel = meanPctBetterThanChance >= 0
    ? `${meanPctBetterThanChance.toFixed(1)}% better than random guessing`
    : `${Math.abs(meanPctBetterThanChance).toFixed(1)}% worse than random guessing`;

  summaryEl.innerHTML = `
    <p>Trials: ${n}</p>
    <p>Accuracy: ${(100 * nCorrect / n).toFixed(1)}% (${nCorrect}/${n})</p>
    <p>Mean RT: ${meanRt.toFixed(0)} ms</p>
    <p>l2_error_px: ${meanL2.toFixed(1)} px</p>
    <p>l2_error_norm_grid (${gridLabel}): ${meanL2Norm.toFixed(3)}</p>
    <p>l2_error_pct_max: ${meanL2PctMax.toFixed(1)}%</p>
    <p>pct_better_than_chance: ${vsChanceLabel}</p>
  `;
}

function retryBlock() {
  // Restart a fresh block with the same settings (grid/mode/trial count already
  // loaded), skipping the setup form and the manifest/audio fetch. Waits for
  // "Start when ready" instead of jumping straight into trial 1, so there's
  // time to get ready before the first sound plays.
  session.trialIndex = 0;
  trialLog = [];
  currentTrial = null;
  endScreen.classList.add('hidden');
  trialScreen.classList.remove('hidden');
  trialStatus.textContent = 'Click "Start when ready" to begin.';
  progressEl.textContent = `0 / ${session.numTrials}`;
  readyBtn.classList.remove('hidden');
}

function newSession() {
  // Back to the setup form to pick a different grid/arm/mode/trial count.
  stopCurrentAudio();
  currentTrial = null;
  session = null;
  trialLog = [];
  endScreen.classList.add('hidden');
  trialScreen.classList.add('hidden');
  setupScreen.classList.remove('hidden');
  const startBtn = document.getElementById('start-btn');
  startBtn.disabled = false;
  startBtn.textContent = 'Start block';
}

function buildCsv() {
  const cols = ['participant_id', 'arm', 'mode', 'grid_rows', 'grid_cols',
    'image_width', 'image_height', 'trial_index',
    'target_cell', 'target_x_px', 'target_y_px', 'click_x_px', 'click_y_px',
    'correct', 'rt_ms', 'l2_error_px', 'cells_off', 'l2_error_norm',
    'l2_error_pct_of_max', 'chance_error_px', 'pct_better_than_chance', 'timestamp'];
  const lines = [cols.join(',')];
  for (const row of trialLog) {
    lines.push(cols.map(c => row[c]).join(','));
  }
  return lines.join('\n');
}

async function downloadCsv() {
  const csv = buildCsv();
  const filename = `voice_sim_${session.participantId}_${session.arm}_${session.mode}_${Date.now()}.csv`;

  const btn = document.getElementById('download-btn');
  btn.disabled = true;
  saveStatusEl.textContent = 'Saving...';
  saveStatusEl.classList.remove('error');

  try {
    const res = await fetch('/api/save-run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename, csv }),
    });
    if (!res.ok) throw new Error(`server responded ${res.status}`);
    const info = await res.json();
    saveStatusEl.textContent = `Saved to ${info.path}`;
  } catch (err) {
    // Server not running / unreachable -- fall back to a normal browser
    // download so the run isn't lost. Move the file into test_data/ by hand.
    console.warn('Auto-save to test_data failed, falling back to browser download:', err);
    triggerBrowserDownload(csv, filename);
    saveStatusEl.textContent = 'Auto-save failed -- downloaded to your Downloads folder instead.';
    saveStatusEl.classList.add('error');
  } finally {
    btn.disabled = false;
  }
}

function triggerBrowserDownload(csv, filename) {
  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
