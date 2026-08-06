// Phase 3 MVP trial loop. No build step, no framework -- see simulator-plan.md.
// Grid is deliberately blank (no image shown): the task is to localize the
// sound, not to click on a picture.

const setupScreen = document.getElementById('setup-screen');
const trialScreen = document.getElementById('trial-screen');
const endScreen = document.getElementById('end-screen');
const trialStatus = document.getElementById('trial-status');
const gridEl = document.getElementById('grid');
const progressEl = document.getElementById('progress');
const summaryEl = document.getElementById('summary');

let manifest = null;
let audioCtx = null;
let audioBuffers = {}; // wav filename -> AudioBuffer
let trialLog = [];
let session = null; // { participantId, arm, numTrials, trialIndex }
let currentTrial = null; // { cell, audioStartMs, awaitingClick }

document.getElementById('start-btn').addEventListener('click', startSession);
document.getElementById('download-btn').addEventListener('click', downloadCsv);

async function startSession() {
  const grid = document.getElementById('grid-select').value;
  const participantId = document.getElementById('participant-id').value.trim() || 'anon';
  const arm = document.getElementById('arm').value;
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

  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  audioBuffers = {};
  for (const cell of manifest.cells) {
    const buf = await fetch(`../stimuli/${grid}/${cell.wav}`).then(r => r.arrayBuffer());
    audioBuffers[cell.wav] = await audioCtx.decodeAudioData(buf);
  }

  session = { participantId, arm, numTrials, trialIndex: 0 };
  trialLog = [];

  buildGrid();
  setupScreen.classList.add('hidden');
  trialScreen.classList.remove('hidden');
  runNextTrial();
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
  source.start();
}

function onGridClick(evt) {
  if (!currentTrial || !currentTrial.awaitingClick) return;
  currentTrial.awaitingClick = false;

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

  trialLog.push({
    participant_id: session.participantId,
    arm: session.arm,
    grid_rows: manifest.grid_rows,
    grid_cols: manifest.grid_cols,
    trial_index: session.trialIndex,
    target_cell: target.cell_index,
    target_x_px: target.target_x_px,
    target_y_px: target.target_y_px,
    click_x_px: Math.round(xImg * 10) / 10,
    click_y_px: Math.round(yImg * 10) / 10,
    correct: correct ? 1 : 0,
    rt_ms: Math.round(rtMs),
    l2_error_px: Math.round(l2Error * 10) / 10,
    timestamp: new Date().toISOString(),
  });

  showFeedback(evt, correct);
  trialStatus.textContent = correct ? 'Correct!' : 'Incorrect';

  session.trialIndex++;
  setTimeout(runNextTrial, 700);
}

function showFeedback(evt, correct) {
  const rect = gridEl.getBoundingClientRect();
  const dot = document.createElement('div');
  dot.className = `flash ${correct ? 'correct' : 'incorrect'}`;
  dot.style.left = `${evt.clientX - rect.left}px`;
  dot.style.top = `${evt.clientY - rect.top}px`;
  gridEl.appendChild(dot);
  setTimeout(() => dot.remove(), 650);
}

function finishSession() {
  trialScreen.classList.add('hidden');
  endScreen.classList.remove('hidden');

  const n = trialLog.length;
  const nCorrect = trialLog.filter(t => t.correct).length;
  const meanRt = trialLog.reduce((s, t) => s + t.rt_ms, 0) / n;
  const meanL2 = trialLog.reduce((s, t) => s + t.l2_error_px, 0) / n;

  summaryEl.innerHTML = `
    <p>Trials: ${n}</p>
    <p>Accuracy: ${(100 * nCorrect / n).toFixed(1)}% (${nCorrect}/${n})</p>
    <p>Mean RT: ${meanRt.toFixed(0)} ms</p>
    <p>Mean L2 error: ${meanL2.toFixed(1)} px</p>
  `;
}

function downloadCsv() {
  const cols = ['participant_id', 'arm', 'grid_rows', 'grid_cols', 'trial_index',
    'target_cell', 'target_x_px', 'target_y_px', 'click_x_px', 'click_y_px',
    'correct', 'rt_ms', 'l2_error_px', 'timestamp'];
  const lines = [cols.join(',')];
  for (const row of trialLog) {
    lines.push(cols.map(c => row[c]).join(','));
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `voice_sim_${session.participantId}_${session.arm}_${Date.now()}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}
