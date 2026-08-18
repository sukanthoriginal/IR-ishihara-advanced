import {
  buildSchedule, calibratedStageSize, compactStageSize, fitAspectRatio,
  median, mulberry32,
  repeatedStimulusDurationMs, repeatedSweepPosition,
} from './task_logic.mjs?v=simple-ui-4';

const STIMULUS_ROOT = '../ishihara_stimuli/';
const MASK_DURATION_MS = 220;
const AUDIO_SWEEP_REPETITIONS = 3;
const INTER_SWEEP_INTERVAL_MS = 250;

const setupScreen = document.getElementById('setup-screen');
const trialScreen = document.getElementById('trial-screen');
const endScreen = document.getElementById('end-screen');
const startBtn = document.getElementById('start-btn');
const readyBtn = document.getElementById('ready-btn');
const readyPanel = document.getElementById('ready-panel');
const readyInstructionEl = document.getElementById('ready-instruction');
const stageShellEl = document.getElementById('stage-shell');
const trialStageEl = document.getElementById('trial-stage');
const stimulusCanvasEl = document.getElementById('stimulus-canvas');
const stimulusContext = stimulusCanvasEl.getContext('2d', { alpha: true });
const maskEl = document.getElementById('mask');
const choicesEl = document.getElementById('choices');
const feedbackEl = document.getElementById('feedback');
const progressEl = document.getElementById('progress');
const trialStatusEl = document.getElementById('trial-status');
const apparatusReadoutEl = document.getElementById('apparatus-readout');
const presentationModeEl = document.getElementById('presentation-mode');
const targetAngleEl = document.getElementById('target-angle-deg');
const calibrationFieldsEl = document.getElementById('calibration-fields');
const sessionPresetEl = document.getElementById('session-preset');
const sessionPresetNoteEl = document.getElementById('session-preset-note');
const experimentModeEl = document.getElementById('experiment-mode');
const experimentModeNoteEl = document.getElementById('experiment-mode-note');
const conditionEl = document.getElementById('condition');
const summaryEl = document.getElementById('summary');
const saveStatusEl = document.getElementById('save-status');

let manifest = null;
let audioCtx = null;
let audioCache = new Map();
let imageCache = new Map();
let session = null;
let schedule = [];
let trialIndex = 0;
let currentTrial = null;
let trialLog = [];
let acceptingChoice = false;
let trialPhase = 'idle';
let phaseTimer = null;
let presentationAnimationId = null;

startBtn.addEventListener('click', startSession);
readyBtn.addEventListener('click', event => {
  if (session?.responseDevice === 'keyboard') {
    readyInstructionEl.textContent = 'Look at the centre and press any key to start';
    return;
  }
  // Native buttons also synthesize click events for Space/Enter. Reject those
  // for pointer blocks: this gate must place the pointer at screen centre.
  if (event.detail === 0) {
    readyInstructionEl.textContent = 'Use the pointer to click the centre crosshair';
    return;
  }
  beginTrial('pointer');
});
document.getElementById('download-btn').addEventListener('click', saveResults);
document.getElementById('retry-btn').addEventListener('click', retryBlock);
document.getElementById('new-session-btn').addEventListener('click', newSession);
document.addEventListener('keydown', onKeyDown);
document.addEventListener('visibilitychange', onVisibilityChange);
document.addEventListener('fullscreenchange', onFullscreenChange);
window.addEventListener('resize', onWindowResize);
new ResizeObserver(fitTrialStage).observe(stageShellEl);
presentationModeEl.addEventListener('change', updatePresentationControls);
sessionPresetEl.addEventListener('change', updateSessionPreset);
experimentModeEl.addEventListener('change', updateExperimentMode);
document.documentElement.dataset.ishiharaAppVersion = 'simple-ui-4';
updatePresentationControls();
updateSessionPreset();
updateExperimentMode(false);
startBtn.disabled = false;
startBtn.textContent = 'Start session';

function onKeyDown(event) {
  if (
    trialPhase === 'ready'
    && session?.responseDevice === 'keyboard'
    && isKeyboardStartKey(event)
  ) {
    event.preventDefault();
    beginTrial('keyboard');
    return;
  }
  if (
    acceptingChoice
    && session.responseDevice === 'keyboard'
    && ['Digit1', 'Digit2', 'Digit3', 'Digit4'].includes(event.code)
  ) {
    event.preventDefault();
    const index = Number(event.code.slice(-1)) - 1;
    const button = choicesEl.querySelectorAll('.choice')[index];
    if (button) recordChoice(button.dataset.glyph, index, 'keyboard');
  }
}

function isKeyboardStartKey(event) {
  const ignoredKeys = new Set([
    'Shift', 'Control', 'Alt', 'Meta', 'CapsLock', 'NumLock', 'ScrollLock',
    'Escape', 'Tab', 'Unidentified',
  ]);
  return !event.repeat
    && !event.isComposing
    && !event.metaKey
    && !event.ctrlKey
    && !event.altKey
    && !ignoredKeys.has(event.key);
}

function usesKeyboardStart() {
  return session?.responseDevice === 'keyboard';
}

function setReadyGate(action = 'start') {
  const keyboard = usesKeyboardStart();
  const messages = keyboard
    ? {
        start: 'Look at the centre and press any key to start',
        retry: 'Look at the centre and press any key to retry',
        fullscreen: 'Restore fullscreen, then press any key to retry',
        audio: 'Audio could not start — press any key to retry',
      }
    : {
        start: 'Click the centre crosshair to start',
        retry: 'Click the centre crosshair to retry',
        fullscreen: 'Restore fullscreen, then click the centre crosshair',
        audio: 'Audio could not start — click the centre crosshair to retry',
      };
  readyInstructionEl.textContent = messages[action];
  readyBtn.classList.toggle('keyboard-start', keyboard);
  readyBtn.tabIndex = keyboard ? -1 : 0;
  readyBtn.setAttribute(
    'aria-label',
    keyboard
      ? 'Fixation crosshair; press any key to start trial'
      : 'Centre pointer and start trial',
  );
}

function updatePresentationControls() {
  const calibrated = presentationModeEl.value === 'fullscreen-calibrated';
  targetAngleEl.disabled = !calibrated;
  calibrationFieldsEl.classList.toggle('hidden', !calibrated);
}

function updateSessionPreset() {
  const training = sessionPresetEl.value === 'training';
  document.getElementById('split').value = training ? 'train' : 'test';
  document.getElementById('mode').value = training ? 'train' : 'test';
  sessionPresetNoteEl.textContent = training
    ? 'Uses the familiar stimulus bank and shows the correct answer after every trial.'
    : 'Uses the reserved stimulus bank and gives no answer feedback. Avoid practising these patterns beforehand.';
}

function updateExperimentMode(updateTrialCount = true) {
  const visualOnly = experimentModeEl.value === 'visual-only';
  experimentModeNoteEl.textContent = visualOnly
    ? 'Presents complete visible-colour composites in silence for 3.65 seconds; IR discrimination is not tested.'
    : 'Presents matched visible-colour and IR-audio composites; every trial has the matched auditory carrier.';
  if (updateTrialCount) {
    document.getElementById('num-trials').value = visualOnly ? 16 : 32;
  }
}

async function startSession() {
  const participantId = document.getElementById('participant-id').value.trim();
  if (!participantId) {
    alert('Enter a participant ID before starting.');
    document.getElementById('participant-id').focus();
    return;
  }

  startBtn.disabled = true;
  startBtn.textContent = 'Loading stimuli…';
  try {
    // Create/resume during the setup click so Web Audio's user-activation
    // requirement cannot delay the first experimental soundscape.
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    await audioCtx.resume();

    if (!manifest) {
      const response = await fetch(`${STIMULUS_ROOT}manifest.json`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`manifest request returned ${response.status}`);
      manifest = await response.json();
      validateManifest(manifest);
    }

    const experimentMode = experimentModeEl.value;
    const conditionOverride = conditionEl.value;
    const condition = conditionOverride === 'use-experiment-mode'
      ? experimentMode === 'visual-only' ? 'visual-composite-silent' : 'mixed'
      : conditionOverride;
    if (conditionRequiresAudio(condition) && !manifest.audio_generated) {
      throw new Error('This stimulus bank was generated with --skip-audio. Regenerate it with raspivoice so every trial includes its matched auditory carrier.');
    }

    const complexity = document.getElementById('complexity').value;
    const channelRecipe = document.getElementById('channel-recipe').value;
    const hasProgression = complexity === 'curriculum' || channelRecipe === 'curriculum';
    const minimumTrials = hasProgression && condition === 'mixed' ? 8 : 4;
    const requestedTrials = Math.max(
      minimumTrials,
      Number.parseInt(document.getElementById('num-trials').value, 10) || 16,
    );
    const numTrials = condition === 'mixed' && requestedTrials % 2
      ? requestedTrials + 1
      : requestedTrials;
    const presentationMode = document.getElementById('presentation-mode').value;
    const displayId = document.getElementById('display-id').value.trim();
    const displayWidthCm = Number.parseFloat(document.getElementById('display-width-cm').value);
    const viewingDistanceCm = Number.parseFloat(document.getElementById('viewing-distance-cm').value);
    const targetWidthAngleDeg = Number.parseFloat(
      document.getElementById('target-angle-deg').value,
    );
    if (
      presentationMode === 'fullscreen-calibrated'
      && (
        !displayId
        || !(displayWidthCm > 0)
        || !(viewingDistanceCm > 0)
        || !(targetWidthAngleDeg >= 5 && targetWidthAngleDeg <= 80)
      )
    ) {
      throw new Error(
        'Fullscreen data collection requires display ID, physical display width, viewing distance, and a 5–80° target plate width.',
      );
    }
    session = {
      participantId,
      arm: document.getElementById('arm').value,
      experimentMode,
      conditionOverride,
      condition,
      complexity,
      channelRecipe,
      split: document.getElementById('split').value,
      mode: document.getElementById('mode').value,
      presentationMode,
      responseDevice: document.getElementById('response-device').value,
      displayId,
      displayWidthCm: displayWidthCm > 0 ? displayWidthCm : null,
      viewingDistanceCm: viewingDistanceCm > 0 ? viewingDistanceCm : null,
      targetWidthAngleDeg: presentationMode === 'fullscreen-calibrated'
        ? targetWidthAngleDeg
        : null,
      numTrials,
      seed: makeSeed(),
      startedAt: new Date().toISOString(),
    };
    beginBlock();
  } catch (error) {
    alert(`Could not start the Ishihara task: ${error.message}`);
    startBtn.disabled = false;
    startBtn.textContent = 'Start session';
  }
}

function validateManifest(value) {
  if (
    value.schema_version !== 8
    || value.task !== 'ir-ishihara-ambiguous-metamers'
    || !Array.isArray(value.stimuli)
    || !value.complexity_tiers
    || !value.metamer_families
    || !value.channel_recipes
  ) {
    throw new Error('manifest has an unsupported schema');
  }
  if (!(value.soundscape_width > 1) || !(value.soundscape_duration_ms > 0)) {
    throw new Error('manifest is missing soundscape sweep geometry');
  }
  if (value.soundscape_uses_bspline === false) {
    throw new Error('this task requires the quadratic B-spline soundscape sweep');
  }
  if (
    value.coordinate_mapping !== 'full-frame-normalized-no-crop'
    || value.plate_width / value.soundscape_width !== value.visual_to_audio_scale_x
    || value.plate_height / value.soundscape_height !== value.visual_to_audio_scale_y
  ) {
    throw new Error('manifest does not declare an exact full-field visual-to-audio map');
  }
  const requiredAssets = [
    'visual_composite_png', 'visible_components_png', 'neutral_plate_png',
    'ir_input_png', 'ir_scrambled_input_png', 'ir_background_input_png',
  ];
  if (value.stimuli.some(stimulus => requiredAssets.some(key => !stimulus[key]))) {
    throw new Error('manifest is missing ambiguity-grammar assets');
  }
  if (value.stimuli.some(stimulus => (
    !stimulus.target_choice_id
    || !stimulus.decoy_choice_id
    || !stimulus.choice_structure
    || !stimulus.probe_state
    || !Array.isArray(stimulus.response_choices)
    || stimulus.response_choices.length !== 4
    || !stimulus.response_choices.includes(stimulus.target_choice_id)
    || !stimulus.response_choices.includes(stimulus.decoy_choice_id)
  ))) {
    throw new Error('manifest is missing target/decoy response mappings');
  }
  if (
    value.audio_generated
    && value.stimuli.some(stimulus => (
      !Number.isFinite(stimulus.ir_wav_rms_int16)
      || !Number.isFinite(stimulus.ir_scrambled_wav_rms_int16)
      || !Number.isFinite(stimulus.ir_background_wav_rms_int16)
      || !stimulus.ir_wav
      || !stimulus.ir_scrambled_wav
      || !stimulus.ir_background_wav
    ))
  ) {
    throw new Error('manifest is missing normalized audio RMS metadata');
  }
}

function conditionRequiresAudio(condition) {
  return [
    'mixed', 'visual-composite', 'ir-composite',
    'visible-only', 'ir-only', 'ir-scrambled',
  ].includes(condition);
}

function beginBlock() {
  const rng = mulberry32(session.seed);
  schedule = buildSchedule(manifest, session, rng);
  trialIndex = 0;
  trialLog = [];
  currentTrial = null;
  trialPhase = 'idle';
  setupScreen.classList.add('hidden');
  endScreen.classList.add('hidden');
  trialScreen.classList.remove('hidden');
  document.body.classList.add('trial-active');
  fitTrialStage();
  prepareNextTrial();
}

async function prepareNextTrial() {
  acceptingChoice = false;
  choicesEl.classList.add('hidden');
  feedbackEl.textContent = '';
  stopPresentationAnimation();
  clearStimulusCanvas();
  stimulusCanvasEl.classList.add('hidden');
  maskEl.classList.add('hidden');
  clearPhaseTimer();

  if (trialIndex >= schedule.length) {
    finishBlock();
    return;
  }

  currentTrial = {
    ...schedule[trialIndex],
    invalidReasons: [],
  };
  trialPhase = 'preparing';
  progressEl.textContent = `Trial ${trialIndex + 1} / ${schedule.length}`;
  trialStatusEl.textContent = 'Preparing stimulus…';
  readyPanel.classList.add('hidden');

  try {
    await Promise.all([
      getImage(assetUrl(plateFileFor(currentTrial))),
      ...currentTrial.choices.map(glyph => (
        getImage(assetUrl(manifest.glyph_thumbnails[glyph]))
      )),
    ]);
    const wav = wavFileFor(currentTrial);
    if (wav) await getAudioBuffer(wav);
    readyPanel.classList.remove('hidden');
    readyBtn.disabled = false;
    setReadyGate('start');
    trialStatusEl.textContent = usesKeyboardStart()
      ? 'Ready — look at the centre before the stimulus.'
      : 'Ready — recenter the pointer before the stimulus.';
    trialPhase = 'ready';
    if (usesKeyboardStart()) readyBtn.blur();
    else readyBtn.focus();
  } catch (error) {
    trialStatusEl.textContent = `Stimulus failed to load: ${error.message}`;
  }
}

async function beginTrial(startMethod) {
  if (!currentTrial || trialPhase !== 'ready' || readyPanel.classList.contains('hidden')) return;
  trialPhase = 'starting';
  currentTrial.startMethod = startMethod;
  readyBtn.disabled = true;
  const audioResume = audioCtx.state === 'running'
    ? Promise.resolve()
    : audioCtx.resume();

  if (session.presentationMode.startsWith('fullscreen') && !document.fullscreenElement) {
    try {
      await document.documentElement.requestFullscreen();
    } catch (_) {
      await audioResume.catch(() => {});
      readyBtn.disabled = false;
      setReadyGate('fullscreen');
      trialStatusEl.textContent = 'The stimulus was not presented.';
      trialPhase = 'ready';
      return;
    }
  }

  try {
    await audioResume;
  } catch (_) {
    readyBtn.disabled = false;
    setReadyGate('audio');
    trialStatusEl.textContent = 'The stimulus was not presented.';
    trialPhase = 'ready';
    return;
  }

  // Let fullscreen/resize layout settle before measuring and presenting.
  await settleLayout();
  const stageFit = fitTrialStage();
  if (
    session.presentationMode === 'fullscreen-calibrated'
    && stageFit
    && !stageFit.fits
  ) {
    const maximum = Number.isFinite(stageFit.maximumWidthAngleDeg)
      ? ` The largest plate that fits this display is ${stageFit.maximumWidthAngleDeg.toFixed(1)}°.`
      : '';
    alert(`The requested ${session.targetWidthAngleDeg.toFixed(1)}° plate does not fit.${maximum} Reduce the target angle or use a larger display; the trial was not presented.`);
    newSession();
    return;
  }
  if (document.visibilityState !== 'visible') {
    readyBtn.disabled = false;
    setReadyGate('retry');
    trialPhase = 'ready';
    return;
  }

  readyPanel.classList.add('hidden');
  choicesEl.classList.add('hidden');
  maskEl.classList.add('hidden');
  const stimulusImage = await getImage(assetUrl(plateFileFor(currentTrial)));
  configureStimulusCanvas(stimulusImage);
  stimulusCanvasEl.classList.remove('hidden');
  const scaffoldRecipe = currentTrial.stimulus.scaffold_channels.join(' + ');
  const visibleProbe = currentTrial.stimulus.visible_probe_channel;
  const conditionLabels = {
    'visual-composite-silent': `Static ${scaffoldRecipe} scaffold + ${visibleProbe} probe; no audio`,
    'visual-composite': `Static ${scaffoldRecipe} scaffold + ${visibleProbe} probe; background IR carrier`,
    'ir-composite': `Static ${scaffoldRecipe} scaffold + IR probe audio`,
    'visible-only': `Static ${scaffoldRecipe} scaffold; background IR carrier only`,
    'ir-only': 'Static neutral plate + IR probe audio',
    'ir-scrambled': `Static ${scaffoldRecipe} scaffold + scrambled IR probe`,
  };
  currentTrial.conditionLabel = conditionLabels[currentTrial.condition]
    ?? 'Composite-shape sweep';
  trialStatusEl.textContent = currentTrial.conditionLabel;

  const stageRect = trialStageEl.getBoundingClientRect();
  const stimulusRect = stimulusCanvasEl.getBoundingClientRect();
  currentTrial.presentation = {
    viewportWidthCssPx: window.innerWidth,
    viewportHeightCssPx: window.innerHeight,
    stageWidthCssPx: stageRect.width,
    stageHeightCssPx: stageRect.height,
    stimulusWidthCssPx: stimulusRect.width,
    stimulusHeightCssPx: stimulusRect.height,
    cssPxPerAudioColumn: stimulusRect.width / manifest.soundscape_width,
    cssPxPerAudioRow: stimulusRect.height / manifest.soundscape_height,
    displayCoordinateMapping: displayCoordinateMapping(session.presentationMode),
    displayAxisStretchYOverX: (
      stimulusRect.height / manifest.soundscape_height
    ) / (
      stimulusRect.width / manifest.soundscape_width
    ),
    devicePixelRatio: window.devicePixelRatio,
    fullscreen: Boolean(document.fullscreenElement),
    ...calibratedStageGeometry(stageRect),
  };
  currentTrial.audioBaseLatencyMs = Number.isFinite(audioCtx.baseLatency)
    ? audioCtx.baseLatency * 1000
    : null;
  currentTrial.audioOutputLatencyMs = Number.isFinite(audioCtx.outputLatency)
    ? audioCtx.outputLatency * 1000
    : null;
  const wav = wavFileFor(currentTrial);

  trialPhase = 'stimulus-arming';
  await nextFrame();
  if (trialPhase !== 'stimulus-arming') return;

  // Schedule all audio sweeps slightly ahead on one Web Audio timeline, then
  // use that clock only to reveal the complete static RGB plate at onset.
  const scheduleLeadSeconds = 0.075;
  const audioStartContextSeconds = audioCtx.currentTime + scheduleLeadSeconds;
  const sweepStartPerformanceMs = wav
    ? audioContextTimeToPerformanceMs(audioStartContextSeconds)
    : performance.now() + scheduleLeadSeconds * 1000;
  currentTrial.stimulusStartedMs = sweepStartPerformanceMs;
  currentTrial.audioStartContextSeconds = wav ? audioStartContextSeconds : null;
  currentTrial.audioStartContextSecondsByRepetition = [];
  currentTrial.sources = [];
  currentTrial.hasAudioSweep = Boolean(wav);
  const repeatStrideMs = manifest.soundscape_duration_ms + INTER_SWEEP_INTERVAL_MS;
  if (wav) {
    for (let repetition = 0; repetition < AUDIO_SWEEP_REPETITIONS; repetition += 1) {
      const source = audioCtx.createBufferSource();
      source.buffer = audioCache.get(wav);
      source.connect(audioCtx.destination);
      const contextStart = audioStartContextSeconds + repetition * repeatStrideMs / 1000;
      source.start(contextStart);
      currentTrial.sources.push(source);
      currentTrial.audioStartContextSecondsByRepetition.push(contextStart);
    }
  }
  trialPhase = 'stimulus';
  startStaticVisualPresentation(stimulusImage, sweepStartPerformanceMs);
  const plannedDurationMs = repeatedStimulusDurationMs(
    manifest.soundscape_duration_ms,
    AUDIO_SWEEP_REPETITIONS,
    INTER_SWEEP_INTERVAL_MS,
  );
  const remainingMs = Math.max(
    0,
    sweepStartPerformanceMs + plannedDurationMs - performance.now(),
  );
  phaseTimer = window.setTimeout(showMask, remainingMs);
}

async function showMask() {
  if (trialPhase !== 'stimulus') return;
  clearPhaseTimer();
  stopCurrentAudio();
  stopPresentationAnimation();
  stimulusCanvasEl.classList.add('hidden');
  maskEl.classList.remove('hidden');
  trialStatusEl.textContent = 'Which interpretation did the probe specify?';
  trialPhase = 'mask-arming';
  const maskOnsetMs = await nextFrame();
  if (trialPhase !== 'mask-arming') return;
  currentTrial.stimulusActualDurationMs = maskOnsetMs - currentTrial.stimulusStartedMs;
  currentTrial.audioSweepsCompleted = currentTrial.hasAudioSweep
    ? AUDIO_SWEEP_REPETITIONS
    : 0;
  currentTrial.maskStartedMs = maskOnsetMs;
  trialPhase = 'mask';
  phaseTimer = window.setTimeout(showChoices, MASK_DURATION_MS);
}

async function showChoices() {
  if (trialPhase !== 'mask') return;
  clearPhaseTimer();
  maskEl.classList.add('hidden');
  choicesEl.innerHTML = '';
  choicesEl.classList.toggle('keyboard-only', session.responseDevice === 'keyboard');
  for (const [index, glyph] of currentTrial.choices.entries()) {
    const button = document.createElement('button');
    button.className = 'choice';
    button.type = 'button';
    button.dataset.glyph = glyph;
    button.innerHTML = `
      <span class="choice-index">${index + 1}</span>
      <img src="${assetUrl(manifest.glyph_thumbnails[glyph])}" alt="Choice ${index + 1}">
    `;
    button.tabIndex = session.responseDevice === 'keyboard' ? -1 : 0;
    button.addEventListener('click', () => {
      if (session.responseDevice === 'pointer') recordChoice(glyph, index, 'pointer');
    });
    choicesEl.appendChild(button);
  }
  choicesEl.classList.remove('hidden');
  trialPhase = 'response-arming';
  const responseOnsetMs = await nextFrame();
  if (trialPhase !== 'response-arming') return;
  currentTrial.maskActualDurationMs = responseOnsetMs - currentTrial.maskStartedMs;
  currentTrial.responseStartedMs = responseOnsetMs;
  trialPhase = 'response';
  acceptingChoice = true;
}

function recordChoice(choiceGlyph, responsePosition, responseMethod) {
  if (!acceptingChoice || trialPhase !== 'response') return;
  acceptingChoice = false;
  trialPhase = 'feedback';
  const responseMs = performance.now();
  const choiceRtMs = responseMs - currentTrial.responseStartedMs;
  const stimulusRtMs = responseMs - currentTrial.stimulusStartedMs;
  const targetGlyph = currentTrial.stimulus.target_choice_id;
  const correct = choiceGlyph === targetGlyph;
  const decoySelected = choiceGlyph === currentTrial.stimulus.decoy_choice_id;
  const presentationAudit = summarizePresentationAudit(currentTrial);
  const activeChannels = activeChannelRecipe(currentTrial);

  trialLog.push({
    participant_id: session.participantId,
    arm: session.arm,
    experiment_mode: session.experimentMode,
    condition_override: session.conditionOverride,
    requested_condition: session.condition,
    requested_complexity: session.complexity,
    requested_channel_recipe: session.channelRecipe,
    condition: currentTrial.condition,
    visual_presentation: 'static-full-plate',
    audio_presentation: currentTrial.hasAudioSweep
      ? 'three-left-to-right-sweeps'
      : 'none',
    audio_content: audioContentForTrial(currentTrial),
    split: session.split,
    mode: session.mode,
    presentation_mode: session.presentationMode,
    presentation_scale_mode: scaleModeForPresentation(session.presentationMode),
    response_device: session.responseDevice,
    trial_start_method: currentTrial.startMethod,
    response_method: responseMethod,
    display_id: session.displayId,
    display_width_cm: roundMetric(session.displayWidthCm),
    viewing_distance_cm: roundMetric(session.viewingDistanceCm),
    target_stage_width_visual_angle_deg: roundMetric(session.targetWidthAngleDeg),
    pair_id: currentTrial.pairId ?? '',
    pair_position: currentTrial.pairPosition ?? '',
    pair_condition_order: currentTrial.pairConditionOrder ?? '',
    session_seed: session.seed,
    trial_index: trialIndex,
    stimulus_id: currentTrial.stimulus.stimulus_id,
    stimulus_seed: currentTrial.stimulus.seed,
    complexity_level: currentTrial.stimulus.complexity_level,
    complexity_label: currentTrial.stimulus.complexity_label,
    family_id: currentTrial.stimulus.family_id,
    channel_recipe_id: currentTrial.stimulus.channel_recipe_id,
    channel_recipe_label: currentTrial.stimulus.channel_recipe_label,
    visible_comparator_channels: currentTrial.stimulus.visible_channels.join('+'),
    crossmodal_channels: currentTrial.stimulus.crossmodal_channels.join('+'),
    active_channels: activeChannels,
    scaffold_channels: currentTrial.stimulus.scaffold_channels.join('+'),
    visible_probe_channel: currentTrial.stimulus.visible_probe_channel,
    crossmodal_probe_channel: currentTrial.stimulus.crossmodal_probe_channel,
    component_count: currentTrial.stimulus.component_count,
    component_dot_counts: currentTrial.stimulus.component_dot_counts.join('|'),
    scaffold_dot_count: currentTrial.stimulus.scaffold_dot_count,
    diagnostic_dot_count: currentTrial.stimulus.diagnostic_dot_count,
    transformation_id: currentTrial.stimulus.transformation_id,
    choice_structure: currentTrial.stimulus.choice_structure,
    probe_state: currentTrial.stimulus.probe_state,
    target_variant: targetGlyph,
    target_variant_label: currentTrial.stimulus.target_label,
    decoy_variant: currentTrial.stimulus.decoy_choice_id,
    decoy_variant_label: currentTrial.stimulus.decoy_label,
    decoy_selected: decoySelected ? 1 : 0,
    response_set: currentTrial.stimulus.response_choices.join('|'),
    choice_variant: choiceGlyph,
    response_position: responsePosition + 1,
    correct: correct ? 1 : 0,
    rt_ms: Math.round(choiceRtMs),
    rt_choice_onset_ms: Math.round(choiceRtMs),
    rt_stimulus_onset_ms: Math.round(stimulusRtMs),
    stimulus_duration_planned_ms: repeatedStimulusDurationMs(
      manifest.soundscape_duration_ms,
      AUDIO_SWEEP_REPETITIONS,
      INTER_SWEEP_INTERVAL_MS,
    ),
    stimulus_duration_actual_ms: roundMetric(currentTrial.stimulusActualDurationMs),
    audio_sweeps_planned: currentTrial.hasAudioSweep ? AUDIO_SWEEP_REPETITIONS : 0,
    audio_sweeps_completed: currentTrial.audioSweepsCompleted,
    single_audio_sweep_duration_ms: manifest.soundscape_duration_ms,
    inter_audio_sweep_interval_ms: INTER_SWEEP_INTERVAL_MS,
    static_visual_duration_planned_ms: repeatedStimulusDurationMs(
      manifest.soundscape_duration_ms,
      AUDIO_SWEEP_REPETITIONS,
      INTER_SWEEP_INTERVAL_MS,
    ),
    static_visual_onset_frame_offset_ms: roundMetric(
      presentationAudit.staticVisualOnsetFrameOffsetMs,
    ),
    mask_duration_planned_ms: MASK_DURATION_MS,
    mask_duration_actual_ms: roundMetric(currentTrial.maskActualDurationMs),
    viewport_width_css_px: currentTrial.presentation.viewportWidthCssPx,
    viewport_height_css_px: currentTrial.presentation.viewportHeightCssPx,
    stage_width_css_px: roundMetric(currentTrial.presentation.stageWidthCssPx),
    stage_height_css_px: roundMetric(currentTrial.presentation.stageHeightCssPx),
    stimulus_width_css_px: roundMetric(
      currentTrial.presentation.stimulusWidthCssPx,
    ),
    stimulus_height_css_px: roundMetric(
      currentTrial.presentation.stimulusHeightCssPx,
    ),
    css_px_per_audio_column: roundMetric(
      currentTrial.presentation.cssPxPerAudioColumn,
    ),
    css_px_per_audio_row: roundMetric(currentTrial.presentation.cssPxPerAudioRow),
    display_coordinate_mapping: currentTrial.presentation.displayCoordinateMapping,
    display_axis_stretch_y_over_x: roundMetric(
      currentTrial.presentation.displayAxisStretchYOverX,
    ),
    stage_aspect_ratio: roundMetric(
      currentTrial.presentation.stageWidthCssPx / currentTrial.presentation.stageHeightCssPx,
    ),
    screen_width_css_px: currentTrial.presentation.screenWidthCssPx,
    screen_height_css_px: currentTrial.presentation.screenHeightCssPx,
    stage_width_cm: roundMetric(currentTrial.presentation.stageWidthCm),
    stage_height_cm: roundMetric(currentTrial.presentation.stageHeightCm),
    stage_width_visual_angle_deg: roundMetric(
      currentTrial.presentation.stageWidthVisualAngleDeg,
    ),
    stage_height_visual_angle_deg: roundMetric(
      currentTrial.presentation.stageHeightVisualAngleDeg,
    ),
    stage_width_visual_angle_error_deg: roundMetric(
      currentTrial.presentation.stageWidthVisualAngleErrorDeg,
    ),
    visual_raster_width_px: manifest.plate_width,
    visual_raster_height_px: manifest.plate_height,
    audio_spatial_columns: manifest.soundscape_width,
    audio_spatial_rows: manifest.soundscape_height,
    visual_to_audio_scale_x: manifest.visual_to_audio_scale_x,
    visual_to_audio_scale_y: manifest.visual_to_audio_scale_y,
    coordinate_mapping: manifest.coordinate_mapping,
    device_pixel_ratio: currentTrial.presentation.devicePixelRatio,
    fullscreen_at_onset: currentTrial.presentation.fullscreen ? 1 : 0,
    page_visibility_at_response: document.visibilityState,
    audio_base_latency_ms: roundMetric(currentTrial.audioBaseLatencyMs),
    audio_output_latency_ms: roundMetric(currentTrial.audioOutputLatencyMs),
    audio_sweep_columns: presentationAudit.columns,
    audio_sweep_sample_rate_hz: presentationAudit.sampleRateHz,
    audio_sweep_samples_per_column: presentationAudit.samplesPerColumn,
    audio_sweep_column_slice_ms: roundMetric(presentationAudit.columnSliceMs),
    audio_sweep_bspline_support_ms: roundMetric(presentationAudit.bsplineSupportMs),
    audio_file_rms_int16: audioRmsForTrial(currentTrial),
    invalid_attempts_before_response: currentTrial.invalidReasons.length,
    invalid_reasons: currentTrial.invalidReasons.join('|'),
    timestamp: new Date().toISOString(),
  });

  choicesEl.querySelectorAll('.choice').forEach(button => { button.disabled = true; });
  if (session.mode === 'train') {
    feedbackEl.className = correct ? 'correct-text' : 'incorrect-text';
    feedbackEl.textContent = correct
      ? 'Correct'
      : decoySelected
        ? `Probe missed — ${currentTrial.stimulus.decoy_label} becomes ${currentTrial.stimulus.target_label}`
        : `Incorrect — target was ${currentTrial.stimulus.target_label}`;
  } else {
    feedbackEl.className = '';
    feedbackEl.textContent = 'Response recorded.';
  }
  trialIndex += 1;
  phaseTimer = window.setTimeout(prepareNextTrial, session.mode === 'train' ? 850 : 400);
}

function plateFileFor(trial) {
  if (['visual-composite', 'visual-composite-silent'].includes(trial.condition)) {
    return trial.stimulus.visual_composite_png;
  }
  if (trial.condition === 'ir-only') return trial.stimulus.neutral_plate_png;
  if (['ir-composite', 'visible-only', 'ir-scrambled'].includes(trial.condition)) {
    return trial.stimulus.visible_components_png;
  }
  throw new Error(`unsupported condition: ${trial.condition}`);
}

function wavFileFor(trial) {
  if (['ir-composite', 'ir-only'].includes(trial.condition)) return trial.stimulus.ir_wav;
  if (trial.condition === 'ir-scrambled') return trial.stimulus.ir_scrambled_wav;
  if (['visual-composite', 'visible-only'].includes(trial.condition)) {
    return trial.stimulus.ir_background_wav;
  }
  return null;
}

function audioContentForTrial(trial) {
  if (['ir-composite', 'ir-only'].includes(trial.condition)) return 'aligned-ir-probe';
  if (trial.condition === 'ir-scrambled') return 'scrambled-ir-probe';
  if (['visual-composite', 'visible-only'].includes(trial.condition)) {
    return 'background-only-ir-carrier';
  }
  return 'none';
}

function audioRmsForTrial(trial) {
  if (['ir-composite', 'ir-only'].includes(trial.condition)) {
    return trial.stimulus.ir_wav_rms_int16;
  }
  if (trial.condition === 'ir-scrambled') {
    return trial.stimulus.ir_scrambled_wav_rms_int16;
  }
  if (['visual-composite', 'visible-only'].includes(trial.condition)) {
    return trial.stimulus.ir_background_wav_rms_int16;
  }
  return '';
}

function activeChannelRecipe(trial) {
  if (['visual-composite', 'visual-composite-silent'].includes(trial.condition)) {
    return trial.stimulus.visible_channels.join('+');
  }
  if (trial.condition === 'ir-composite') {
    return trial.stimulus.crossmodal_channels.join('+');
  }
  if (trial.condition === 'visible-only') {
    return trial.stimulus.crossmodal_channels.filter(channel => channel !== 'IR').join('+');
  }
  if (trial.condition === 'ir-only') return 'IR';
  if (trial.condition === 'ir-scrambled') {
    const visible = trial.stimulus.crossmodal_channels
      .filter(channel => channel !== 'IR');
    return [...visible, 'IR-scrambled'].join('+');
  }
  return 'unknown';
}

function assetUrl(relativePath) {
  return `${STIMULUS_ROOT}${relativePath}`;
}

async function getAudioBuffer(relativePath) {
  if (audioCache.has(relativePath)) return audioCache.get(relativePath);
  const response = await fetch(assetUrl(relativePath));
  if (!response.ok) throw new Error(`${relativePath} returned ${response.status}`);
  const buffer = await audioCtx.decodeAudioData(await response.arrayBuffer());
  audioCache.set(relativePath, buffer);
  return buffer;
}

function getImage(url) {
  if (imageCache.has(url)) return imageCache.get(url);
  const promise = new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error(`could not load ${url}`));
    image.src = url;
  });
  imageCache.set(url, promise);
  return promise;
}

function configureStimulusCanvas(image) {
  stimulusCanvasEl.width = image.naturalWidth;
  stimulusCanvasEl.height = image.naturalHeight;
  stimulusContext.imageSmoothingEnabled = false;
  clearStimulusCanvas();
}

function clearStimulusCanvas() {
  stimulusContext.clearRect(0, 0, stimulusCanvasEl.width, stimulusCanvasEl.height);
}

function drawStaticStimulus(image) {
  clearStimulusCanvas();
  stimulusContext.globalAlpha = 1;
  stimulusContext.drawImage(
    image, 0, 0, stimulusCanvasEl.width, stimulusCanvasEl.height,
  );
}

function audioSweepConfig() {
  const columns = manifest.soundscape_width;
  const sampleRateHz = manifest.soundscape_sample_rate_hz ?? 48000;
  const sampleCount = manifest.soundscape_sample_count
    ?? Math.round(manifest.soundscape_duration_ms / 1000 * sampleRateHz);
  const samplesPerColumn = manifest.soundscape_samples_per_column
    ?? Math.floor(sampleCount / columns);
  return { columns, sampleRateHz, sampleCount, samplesPerColumn };
}

function startStaticVisualPresentation(image, startPerformanceMs) {
  stopPresentationAnimation();
  currentTrial.staticVisualPlannedOnsetMs = startPerformanceMs;
  currentTrial.staticVisualActualOnsetMs = null;
  let displayedAudioState = '';

  const drawFrame = timestamp => {
    if (trialPhase !== 'stimulus') return;
    if (timestamp < startPerformanceMs) {
      presentationAnimationId = requestAnimationFrame(drawFrame);
      return;
    }

    if (currentTrial.staticVisualActualOnsetMs === null) {
      drawStaticStimulus(image);
      currentTrial.staticVisualActualOnsetMs = timestamp;
    }

    const position = repeatedSweepPosition(
      timestamp - startPerformanceMs,
      manifest.soundscape_duration_ms,
      AUDIO_SWEEP_REPETITIONS,
      INTER_SWEEP_INTERVAL_MS,
    );
    if (position.complete) return;
    if (currentTrial.hasAudioSweep) {
      const audioState = `${position.repetitionIndex}:${position.sweepActive}`;
      if (audioState !== displayedAudioState) {
        displayedAudioState = audioState;
        trialStatusEl.textContent = position.sweepActive
          ? `${currentTrial.conditionLabel} — audio sweep ${position.repetitionIndex + 1}/${AUDIO_SWEEP_REPETITIONS}`
          : `Audio sweep ${position.repetitionIndex + 1}/${AUDIO_SWEEP_REPETITIONS} complete`;
      }
    }
    presentationAnimationId = requestAnimationFrame(drawFrame);
  };
  presentationAnimationId = requestAnimationFrame(drawFrame);
}

function stopPresentationAnimation() {
  if (presentationAnimationId !== null) {
    cancelAnimationFrame(presentationAnimationId);
    presentationAnimationId = null;
  }
}

function audioContextTimeToPerformanceMs(contextTimeSeconds) {
  if (typeof audioCtx.getOutputTimestamp === 'function') {
    const timestamp = audioCtx.getOutputTimestamp();
    if (Number.isFinite(timestamp.contextTime) && Number.isFinite(timestamp.performanceTime)) {
      const projectedMs = timestamp.performanceTime
        + (contextTimeSeconds - timestamp.contextTime) * 1000;
      if (projectedMs >= performance.now() - 100 && projectedMs <= performance.now() + 1000) {
        return projectedMs;
      }
    }
  }
  const outputLatencySeconds = Number.isFinite(audioCtx.outputLatency)
    ? audioCtx.outputLatency
    : Number.isFinite(audioCtx.baseLatency) ? audioCtx.baseLatency : 0;
  return performance.now()
    + (contextTimeSeconds - audioCtx.currentTime + outputLatencySeconds) * 1000;
}

function stopCurrentAudio() {
  if (currentTrial?.sources) {
    for (const source of currentTrial.sources) {
      try { source.stop(); } catch (_) { /* already ended */ }
    }
    currentTrial.sources = [];
  }
}

function clearPhaseTimer() {
  if (phaseTimer !== null) {
    window.clearTimeout(phaseTimer);
    phaseTimer = null;
  }
}

function fitTrialStage() {
  const bounds = stageShellEl.getBoundingClientRect();
  const aspectRatio = manifest
    ? manifest.plate_width / manifest.plate_height
    : 178 / 64;
  const presentationMode = session?.presentationMode ?? 'windowed';
  const calibrated = presentationMode === 'fullscreen-calibrated';
  let size;
  if (calibrated) {
    size = calibratedStageSize({
      targetWidthAngleDeg: session.targetWidthAngleDeg,
      viewingDistanceCm: session.viewingDistanceCm,
      displayWidthCm: session.displayWidthCm,
      screenWidthCssPx: window.screen.width,
      availableWidthCssPx: bounds.width,
      availableHeightCssPx: bounds.height,
      aspectRatio,
    });
    const maximumWidthCssPx = Math.min(bounds.width, bounds.height * aspectRatio);
    const maximumWidthCm = maximumWidthCssPx / window.screen.width * session.displayWidthCm;
    size.maximumWidthAngleDeg = visualAngleDeg(
      maximumWidthCm, session.viewingDistanceCm,
    );
    size.scaleMode = 'calibrated-visual-angle';
  } else if (presentationMode === 'fullscreen-expanded') {
    size = {
      ...fitAspectRatio(bounds.width, bounds.height, aspectRatio),
      fits: true,
      scaleMode: 'native-aspect-expanded',
    };
  } else {
    size = {
      ...compactStageSize({
        availableWidthCssPx: bounds.width,
        availableHeightCssPx: bounds.height,
        nativeWidthPx: manifest?.plate_width ?? 712,
        nativeHeightPx: manifest?.plate_height ?? 256,
        aspectRatio,
      }),
      fits: true,
      scaleMode: 'compact-native-raster',
    };
  }
  if (size.width > 0 && size.height > 0) {
    trialStageEl.style.width = `${size.width}px`;
    trialStageEl.style.height = `${size.height}px`;
  }
  updateApparatusReadout(size);
  return size;
}

function updateApparatusReadout(size) {
  if (!manifest) {
    apparatusReadoutEl.textContent = '';
    return;
  }
  const rasterMap = `${manifest.plate_width}×${manifest.plate_height} visual → ${manifest.soundscape_width}×${manifest.soundscape_height} audio`;
  const renderedSize = size?.width > 0 && size?.height > 0
    ? `${size.width.toFixed(0)}×${size.height.toFixed(0)} CSS px`
    : 'size unavailable';
  const cssPxPerCell = size?.width > 0 && size?.height > 0
    ? `${(size.width / manifest.soundscape_width).toFixed(1)}×${(size.height / manifest.soundscape_height).toFixed(1)} CSS px/audio cell (x×y)`
    : '';
  if (session?.presentationMode === 'fullscreen-calibrated' && size?.widthCm > 0) {
    apparatusReadoutEl.textContent = `${session.targetWidthAngleDeg.toFixed(1)}° calibrated · ${size.widthCm.toFixed(1)} cm · ${renderedSize} · ${cssPxPerCell} · ${rasterMap}`;
  } else if (session?.presentationMode === 'fullscreen-expanded') {
    apparatusReadoutEl.textContent = `expanded native-aspect max-fit · ${renderedSize} · ${cssPxPerCell} · ${rasterMap}`;
  } else {
    apparatusReadoutEl.textContent = `compact native-raster · ${renderedSize} · ${cssPxPerCell} · ${rasterMap}`;
  }
}

function scaleModeForPresentation(presentationMode) {
  if (presentationMode === 'fullscreen-calibrated') return 'calibrated-visual-angle';
  if (presentationMode === 'fullscreen-expanded') return 'native-aspect-expanded';
  return 'compact-native-raster';
}

function displayCoordinateMapping() {
  return 'native-soundscape-aspect';
}

function calibratedStageGeometry(stageRect) {
  const screenWidthCssPx = window.screen.width;
  const screenHeightCssPx = window.screen.height;
  if (!(session.displayWidthCm > 0) || !(session.viewingDistanceCm > 0) || screenWidthCssPx <= 0) {
    return {
      screenWidthCssPx,
      screenHeightCssPx,
      stageWidthCm: null,
      stageHeightCm: null,
      stageWidthVisualAngleDeg: null,
      stageHeightVisualAngleDeg: null,
      stageWidthVisualAngleErrorDeg: null,
    };
  }

  // CSS pixels are square. In fullscreen, the reported physical display
  // width supplies a centimetres-per-CSS-pixel calibration for both axes.
  const cmPerCssPx = session.displayWidthCm / screenWidthCssPx;
  const stageWidthCm = stageRect.width * cmPerCssPx;
  const stageHeightCm = stageRect.height * cmPerCssPx;
  return {
    screenWidthCssPx,
    screenHeightCssPx,
    stageWidthCm,
    stageHeightCm,
    stageWidthVisualAngleDeg: visualAngleDeg(stageWidthCm, session.viewingDistanceCm),
    stageHeightVisualAngleDeg: visualAngleDeg(stageHeightCm, session.viewingDistanceCm),
    stageWidthVisualAngleErrorDeg: Number.isFinite(session.targetWidthAngleDeg)
      ? visualAngleDeg(stageWidthCm, session.viewingDistanceCm)
        - session.targetWidthAngleDeg
      : null,
  };
}

function visualAngleDeg(sizeCm, distanceCm) {
  return 2 * Math.atan(sizeCm / (2 * distanceCm)) * 180 / Math.PI;
}

function onWindowResize() {
  fitTrialStage();
  if (isActiveAttemptPhase()) {
    invalidateActiveAttempt('viewport_resized');
  }
}

function onVisibilityChange() {
  if (document.visibilityState !== 'visible') {
    invalidateActiveAttempt('page_hidden');
  }
}

function onFullscreenChange() {
  fitTrialStage();
  if (
    session
    && session.presentationMode.startsWith('fullscreen')
    && !document.fullscreenElement
    && isActiveAttemptPhase()
  ) {
    invalidateActiveAttempt('fullscreen_exited');
  }
}

function invalidateActiveAttempt(reason) {
  if (!currentTrial || !isActiveAttemptPhase()) return;
  clearPhaseTimer();
  stopCurrentAudio();
  stopPresentationAnimation();
  acceptingChoice = false;
  currentTrial.invalidReasons.push(reason);
  stimulusCanvasEl.classList.add('hidden');
  maskEl.classList.add('hidden');
  choicesEl.classList.add('hidden');
  feedbackEl.className = 'incorrect-text';
  feedbackEl.textContent = 'Interrupted attempt excluded; the same trial will restart.';
  readyPanel.classList.remove('hidden');
  readyBtn.disabled = false;
  setReadyGate(session.presentationMode.startsWith('fullscreen') ? 'fullscreen' : 'retry');
  trialStatusEl.textContent = `Attempt invalidated: ${reason.replaceAll('_', ' ')}.`;
  trialPhase = 'ready';
}

function isActiveAttemptPhase() {
  return [
    'stimulus-arming', 'stimulus', 'mask-arming', 'mask',
    'response-arming', 'response',
  ].includes(trialPhase);
}

function nextFrame() {
  return new Promise(resolve => requestAnimationFrame(resolve));
}

function settleLayout() {
  return new Promise(resolve => {
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  });
}

function roundMetric(value) {
  return Number.isFinite(value) ? Math.round(value * 10) / 10 : '';
}

function summarizePresentationAudit(trial) {
  const config = audioSweepConfig();
  const columnSliceMs = config.samplesPerColumn / config.sampleRateHz * 1000;
  const bsplineSupportMs = columnSliceMs * 3;
  return {
    ...config,
    columnSliceMs,
    bsplineSupportMs,
    staticVisualOnsetFrameOffsetMs: Number.isFinite(trial.staticVisualActualOnsetMs)
      ? trial.staticVisualActualOnsetMs - trial.staticVisualPlannedOnsetMs
      : null,
  };
}

function finishBlock() {
  clearPhaseTimer();
  stopCurrentAudio();
  stopPresentationAnimation();
  trialPhase = 'finished';
  trialScreen.classList.add('hidden');
  endScreen.classList.remove('hidden');
  document.body.classList.remove('trial-active');
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  const rows = [
    'visual-composite-silent', 'visual-composite', 'ir-composite',
    'visible-only', 'ir-only', 'ir-scrambled',
  ]
    .map(condition => summarize(condition))
    .filter(Boolean);
  const totalCorrect = trialLog.reduce((sum, row) => sum + row.correct, 0);
  const invalidAttempts = trialLog.reduce(
    (sum, row) => sum + row.invalid_attempts_before_response,
    0,
  );
  summaryEl.innerHTML = `
    <p><strong>Overall accuracy:</strong> ${(100 * totalCorrect / trialLog.length).toFixed(1)}%
      (${totalCorrect}/${trialLog.length})</p>
    <p><strong>Invalidated attempts:</strong> ${invalidAttempts}</p>
    <table>
      <thead><tr><th>Condition</th><th>Accuracy</th><th>Decoy capture</th><th>Median choice RT</th><th>Median onset-to-response</th></tr></thead>
      <tbody>${rows.map(row => `<tr><td>${row.condition}</td><td>${row.accuracy}</td><td>${row.decoyCapture}</td><td>${row.choiceRt}</td><td>${row.stimulusRt}</td></tr>`).join('')}</tbody>
    </table>
  `;
}

function summarize(condition) {
  const rows = trialLog.filter(row => row.condition === condition);
  if (!rows.length) return null;
  const nCorrect = rows.reduce((sum, row) => sum + row.correct, 0);
  const nDecoy = rows.reduce((sum, row) => sum + row.decoy_selected, 0);
  const correctChoiceRts = rows.filter(row => row.correct).map(row => row.rt_choice_onset_ms);
  const correctStimulusRts = rows.filter(row => row.correct).map(row => row.rt_stimulus_onset_ms);
  return {
    condition,
    accuracy: `${(100 * nCorrect / rows.length).toFixed(1)}% (${nCorrect}/${rows.length})`,
    decoyCapture: `${(100 * nDecoy / rows.length).toFixed(1)}% (${nDecoy}/${rows.length})`,
    choiceRt: correctChoiceRts.length ? `${Math.round(median(correctChoiceRts))} ms` : '—',
    stimulusRt: correctStimulusRts.length ? `${Math.round(median(correctStimulusRts))} ms` : '—',
  };
}

function retryBlock() {
  session.seed = makeSeed();
  session.startedAt = new Date().toISOString();
  beginBlock();
}

function newSession() {
  clearPhaseTimer();
  stopCurrentAudio();
  stopPresentationAnimation();
  trialLog = [];
  schedule = [];
  currentTrial = null;
  trialPhase = 'idle';
  endScreen.classList.add('hidden');
  trialScreen.classList.add('hidden');
  setupScreen.classList.remove('hidden');
  document.body.classList.remove('trial-active');
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  startBtn.disabled = false;
  startBtn.textContent = 'Start session';
}

async function saveResults() {
  const csv = buildCsv(trialLog);
  const filename = `ishihara_${safePart(session.participantId)}_${session.condition}_${session.split}_${Date.now()}.csv`;
  const button = document.getElementById('download-btn');
  button.disabled = true;
  saveStatusEl.className = '';
  saveStatusEl.textContent = 'Saving…';
  try {
    const response = await fetch('/api/save-run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename, csv }),
    });
    if (!response.ok) throw new Error(`server returned ${response.status}`);
    const info = await response.json();
    saveStatusEl.textContent = `Saved to ${info.path}`;
  } catch (error) {
    triggerDownload(csv, filename);
    saveStatusEl.className = 'error';
    saveStatusEl.textContent = 'Local auto-save failed; the CSV was downloaded instead.';
  } finally {
    button.disabled = false;
  }
}

function buildCsv(rows) {
  const columns = [
    'participant_id', 'arm', 'experiment_mode', 'condition_override',
    'requested_condition', 'requested_complexity',
    'requested_channel_recipe', 'condition',
    'visual_presentation', 'audio_presentation', 'audio_content', 'split', 'mode',
    'presentation_mode', 'presentation_scale_mode', 'response_device',
    'trial_start_method', 'response_method', 'display_id',
    'display_width_cm', 'viewing_distance_cm',
    'target_stage_width_visual_angle_deg', 'pair_id', 'pair_position',
    'pair_condition_order', 'session_seed',
    'trial_index', 'stimulus_id', 'stimulus_seed', 'complexity_level',
    'complexity_label', 'family_id', 'channel_recipe_id', 'channel_recipe_label',
    'visible_comparator_channels', 'crossmodal_channels', 'active_channels',
    'scaffold_channels', 'visible_probe_channel', 'crossmodal_probe_channel',
    'component_count', 'component_dot_counts', 'scaffold_dot_count',
    'diagnostic_dot_count', 'transformation_id', 'choice_structure',
    'probe_state', 'target_variant',
    'target_variant_label', 'decoy_variant', 'decoy_variant_label',
    'decoy_selected', 'response_set', 'choice_variant',
    'response_position', 'correct', 'rt_ms', 'rt_choice_onset_ms',
    'rt_stimulus_onset_ms', 'stimulus_duration_planned_ms',
    'stimulus_duration_actual_ms', 'audio_sweeps_planned', 'audio_sweeps_completed',
    'single_audio_sweep_duration_ms', 'inter_audio_sweep_interval_ms',
    'static_visual_duration_planned_ms', 'static_visual_onset_frame_offset_ms',
    'mask_duration_planned_ms', 'mask_duration_actual_ms',
    'viewport_width_css_px', 'viewport_height_css_px', 'stage_width_css_px',
    'stage_height_css_px', 'stimulus_width_css_px', 'stimulus_height_css_px',
    'css_px_per_audio_column', 'css_px_per_audio_row',
    'display_coordinate_mapping', 'display_axis_stretch_y_over_x',
    'stage_aspect_ratio', 'screen_width_css_px',
    'screen_height_css_px', 'stage_width_cm', 'stage_height_cm',
    'stage_width_visual_angle_deg', 'stage_height_visual_angle_deg',
    'stage_width_visual_angle_error_deg', 'visual_raster_width_px',
    'visual_raster_height_px', 'audio_spatial_columns', 'audio_spatial_rows',
    'visual_to_audio_scale_x', 'visual_to_audio_scale_y', 'coordinate_mapping',
    'device_pixel_ratio',
    'fullscreen_at_onset', 'page_visibility_at_response', 'audio_base_latency_ms',
    'audio_output_latency_ms', 'audio_sweep_columns', 'audio_sweep_sample_rate_hz',
    'audio_sweep_samples_per_column', 'audio_sweep_column_slice_ms',
    'audio_sweep_bspline_support_ms', 'audio_file_rms_int16',
    'invalid_attempts_before_response',
    'invalid_reasons',
    'timestamp',
  ];
  const lines = [columns.join(',')];
  for (const row of rows) lines.push(columns.map(column => csvCell(row[column])).join(','));
  return lines.join('\n');
}

function csvCell(value) {
  const text = String(value ?? '');
  return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function triggerDownload(text, filename) {
  const blob = new Blob([text], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function safePart(value) {
  return value.replace(/[^A-Za-z0-9_.-]+/g, '_') || 'participant';
}

function makeSeed() {
  const values = new Uint32Array(1);
  crypto.getRandomValues(values);
  return values[0];
}
