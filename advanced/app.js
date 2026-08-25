import {
  calibratedStageSize,
  compactStageSize,
  fitAspectRatio,
  median,
  visualAngleDeg,
} from '../shared/timing.mjs';
import { safeFilenamePart, saveCsv } from '../shared/csv.mjs';

const ASPECT_RATIO = 178 / 64;
const NATIVE_WIDTH = 712;
const NATIVE_HEIGHT = 256;
const MAX_UINT32 = 0xFFFFFFFF;
const ESTIMATED_RESPONSE_SECONDS = 2;
const HISTORICAL_REPEAT_THRESHOLD = 0.10;
const DEFAULT_MIXED_ALIGNED_RATIO = '1:1:1:2';

const setupScreen = document.getElementById('setup-screen');
const trialScreen = document.getElementById('trial-screen');
const endScreen = document.getElementById('end-screen');
const prepareButton = document.getElementById('prepare-btn');
const prepareStatus = document.getElementById('prepare-status');
const releaseAbandonedButton = document.getElementById('release-abandoned-btn');
const releaseAbandonedStatus = document.getElementById('release-abandoned-status');
const participantInput = document.getElementById('participant-id');
const participantPicker = document.getElementById('participant-picker');
const registerParticipantButton = document.getElementById('register-participant-btn');
const resultsDirectoryInput = document.getElementById('results-directory');
const rememberPreferencesButton = document.getElementById('remember-preferences-btn');
const preferencesStatus = document.getElementById('preferences-status');
const historyLocation = document.getElementById('history-location');
const feedbackWarning = document.getElementById('feedback-warning');
const preparedPanel = document.getElementById('prepared-panel');
const preparedSummary = document.getElementById('prepared-summary');
const startButton = document.getElementById('start-btn');
const randomizationAuditPanel = document.getElementById('randomization-audit');
const auditStatus = document.getElementById('audit-status');
const auditNote = document.getElementById('audit-note');
const stageShell = document.getElementById('stage-shell');
const stimulusImage = document.getElementById('stimulus-image');
const mask = document.getElementById('mask');
const readyOverlay = document.getElementById('ready-overlay');
const readyCopy = document.getElementById('ready-copy');
const readyButton = document.getElementById('ready-btn');
const choices = document.getElementById('choices');
const feedback = document.getElementById('feedback');
const trialStatus = document.getElementById('trial-status');
const conditionStatus = document.getElementById('condition-status');
const summary = document.getElementById('summary');
const saveButton = document.getElementById('save-btn');
const newButton = document.getElementById('new-btn');
const saveStatus = document.getElementById('save-status');
const exposureStatus = document.getElementById('exposure-status');

let manifest = null;
let manifestUrl = null;
let manifestBaseUrl = null;
let stimulusById = new Map();
let session = null;
let trialIndex = 0;
let phase = 'setup';
let trialRows = [];
let audioContext = null;
let audioBuffers = new Map();
let activeAudioSources = [];
let phaseTimer = null;
let responseOnsetMs = null;
let stimulusOnsetMs = null;
let audioPlannedOnsetMs = null;
let currentAttempt = null;
let invalidAttempts = 0;
let randomizationAudit = null;
let localState = null;
let preferencesTouched = false;
let recordedExposureKeys = new Set();
let pendingExposureRequests = new Map();
let failedExposurePayloads = new Map();
let preparationGeneration = 0;
let activePreparationToken = null;
let preparedSettingsSnapshot = null;
let preparationId = null;
let activeSessionLease = null;
let resultsSaved = false;
let savedResultInfo = null;
let endStateReadyForReset = false;
let abandonedLeaseConflict = null;

prepareButton.addEventListener('click', prepareBlock);
startButton.addEventListener('click', startBlock);
readyButton.addEventListener('click', () => beginTrial('pointer'));
saveButton.addEventListener('click', saveResults);
rememberPreferencesButton.addEventListener('click', () => {
  rememberPreferences().catch(() => {});
});
registerParticipantButton.addEventListener('click', () => {
  registerParticipant().catch(() => {});
});
releaseAbandonedButton.addEventListener('click', releaseAbandonedSession);
newButton.addEventListener('click', resetApp);
document.getElementById('presentation').addEventListener('change', updateCalibrationVisibility);
document.getElementById('new-seed-btn').addEventListener('click', () => {
  setFreshRunCode();
  updateSetupPreview();
  invalidatePreparedBlock();
});
for (const id of [
  'split', 'signal-mode', 'base-stimulus-count', 'progression',
  'feedback-enabled', 'glyph-composition', 'seed',
  'mixed-condition-ratio',
]) {
  document.getElementById(id).addEventListener('input', () => {
    updateSetupPreview();
    invalidatePreparedBlock();
  });
}
participantInput.addEventListener('input', () => {
  preferencesTouched = true;
  renderParticipantPicker(localState?.participants || []);
  hideAbandonedSessionRecoveryIfParticipantChanged();
  invalidatePreparedBlock();
});
resultsDirectoryInput.addEventListener('input', () => {
  preferencesTouched = true;
  invalidatePreparedBlock();
});
window.addEventListener('resize', () => {
  fitStage();
  if (['stimulus', 'mask', 'response'].includes(phase)) invalidateAttempt('viewport_resized');
});
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible' && ['stimulus', 'mask', 'response'].includes(phase)) {
    invalidateAttempt('page_hidden');
  }
});
window.addEventListener('pagehide', event => {
  if (
    event.persisted
    || !activeSessionLease
    || pendingExposureRequests.size
    || failedExposurePayloads.size
    || typeof navigator.sendBeacon !== 'function'
  ) return;
  const payload = JSON.stringify({
    participantId: activeSessionLease.participantId,
    sessionId: activeSessionLease.sessionId,
    preparationId: activeSessionLease.preparationId,
  });
  navigator.sendBeacon(
    '/api/release-session',
    new Blob([payload], { type: 'application/json' }),
  );
});
document.addEventListener('keydown', event => {
  if (phase === 'ready' && session?.responseDevice === 'keyboard') {
    event.preventDefault();
    beginTrial('keyboard');
    return;
  }
  if (
    phase === 'response'
    && session?.responseDevice === 'keyboard'
    && /^[1-4]$/.test(event.key)
  ) {
    const responsePosition = Number(event.key);
    const choice = currentAttempt?.responseChoices?.[responsePosition - 1];
    if (!choice) return;
    event.preventDefault();
    recordChoice(choice, responsePosition, 'keyboard');
  }
});

setFreshRunCode();
updateCalibrationVisibility();
updateSetupPreview();
fitStage();
loadLocalState().catch(() => {});

async function loadLocalState() {
  try {
    const response = await fetch('/api/local-state', { cache: 'no-store' });
    const info = await response.json();
    if (!response.ok) throw new Error(info.error || `server returned ${response.status}`);
    applyLocalState(info, { updateControls: !preferencesTouched });
    preferencesStatus.className = 'field-note';
    preferencesStatus.textContent = 'Participant and results location are remembered only on this computer.';
    return info;
  } catch (error) {
    preferencesStatus.className = 'field-note status error';
    preferencesStatus.textContent = 'Could not load server preferences; browser values are shown.';
    throw error;
  }
}

async function rememberPreferences({ quiet = false, updateControls = true } = {}) {
  const participantId = participantInput.value.trim();
  const resultsDirectory = resultsDirectoryInput.value.trim();
  if (!participantId) {
    const error = new Error('Participant ID is required.');
    preferencesStatus.className = 'field-note status error';
    preferencesStatus.textContent = error.message;
    throw error;
  }
  rememberPreferencesButton.disabled = true;
  if (!quiet) {
    preferencesStatus.className = 'field-note status';
    preferencesStatus.textContent = 'Remembering local settings…';
  }
  try {
    const response = await fetch('/api/preferences', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        participantId,
        ...(resultsDirectory ? { resultsDirectory } : {}),
      }),
    });
    const info = await response.json();
    if (!response.ok) throw new Error(info.error || `server returned ${response.status}`);
    applyLocalState(info, { updateControls });
    preferencesStatus.className = 'field-note status good';
    preferencesStatus.textContent = 'Participant and results location remembered locally.';
    return info;
  } catch (error) {
    preferencesStatus.className = 'field-note status error';
    preferencesStatus.textContent = `Could not remember settings: ${String(error.message || error)}`;
    throw error;
  } finally {
    rememberPreferencesButton.disabled = false;
  }
}

async function registerParticipant() {
  const participantId = participantInput.value.trim();
  if (!participantId) {
    preferencesStatus.className = 'field-note status error';
    preferencesStatus.textContent = 'Enter a participant name to register.';
    throw new Error('Participant ID is required.');
  }
  registerParticipantButton.disabled = true;
  preferencesStatus.className = 'field-note status';
  preferencesStatus.textContent = 'Registering participant locally…';
  try {
    const response = await fetch('/api/participants', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ participantId }),
    });
    const info = await response.json();
    if (!response.ok) throw new Error(info.error || `server returned ${response.status}`);
    preferencesTouched = true;
    applyLocalState(info, { updateControls: true });
    preferencesStatus.className = 'field-note status good';
    preferencesStatus.textContent = `Participant “${participantId}” is selected and remembered locally.`;
    return info;
  } catch (error) {
    preferencesStatus.className = 'field-note status error';
    preferencesStatus.textContent = `Could not register participant: ${String(error.message || error)}`;
    throw error;
  } finally {
    registerParticipantButton.disabled = false;
  }
}

async function selectRegisteredParticipant(participantId) {
  if (phase !== 'setup') return;
  participantInput.value = participantId;
  preferencesTouched = true;
  hideAbandonedSessionRecoveryIfParticipantChanged();
  invalidatePreparedBlock();
  renderParticipantPicker(localState?.participants || []);
  try {
    await rememberPreferences({ quiet: true, updateControls: true });
    preferencesStatus.className = 'field-note status good';
    preferencesStatus.textContent = `Participant “${participantId}” selected.`;
  } catch (_error) {
    // rememberPreferences already displays the actionable error.
  }
}

function renderParticipantPicker(participants) {
  participantPicker.replaceChildren();
  const selectedParticipantId = participantInput.value.trim();
  const validParticipants = Array.isArray(participants)
    ? participants.filter(item => String(item?.participantId ?? '').trim())
    : [];
  if (!validParticipants.length) {
    const empty = document.createElement('span');
    empty.className = 'field-note';
    empty.textContent = 'No previously played participants yet.';
    participantPicker.append(empty);
  }
  for (const participant of validParticipants) {
    const participantId = String(participant.participantId).trim();
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'participant-chip';
    button.classList.toggle('selected', participantId === selectedParticipantId);
    button.setAttribute('aria-pressed', participantId === selectedParticipantId ? 'true' : 'false');
    button.textContent = participantId;
    const seen = Number(participant.participantUniqueSeen);
    button.title = Number.isSafeInteger(seen)
      ? `${seen} previously exposed transformation${seen === 1 ? '' : 's'}`
      : 'Registered participant';
    button.addEventListener('click', () => selectRegisteredParticipant(participantId));
    participantPicker.append(button);
  }
  const addButton = document.createElement('button');
  addButton.type = 'button';
  addButton.className = 'participant-chip';
  addButton.textContent = '+ New participant';
  addButton.addEventListener('click', () => {
    if (phase !== 'setup') return;
    participantInput.value = '';
    preferencesTouched = true;
    invalidatePreparedBlock();
    renderParticipantPicker(validParticipants);
    participantInput.focus();
  });
  participantPicker.append(addButton);
}

function applyLocalState(info, { updateControls }) {
  localState = info;
  const participantId = String(info.participantId ?? info.participant_id ?? '').trim();
  const saveDirectory = String(
    info.saveDirectory
      ?? info.resultsDirectory
      ?? info.save_directory
      ?? info.defaultSaveDirectory
      ?? '',
  ).trim();
  const historyPath = String(info.historyPath ?? info.history_path ?? '').trim();
  const previousParticipantId = participantInput.value;
  const previousSaveDirectory = resultsDirectoryInput.value;
  if (updateControls) {
    if (participantId) participantInput.value = participantId;
    if (saveDirectory) resultsDirectoryInput.value = saveDirectory;
    hideAbandonedSessionRecoveryIfParticipantChanged();
  }
  if (info.defaultSaveDirectory) {
    resultsDirectoryInput.placeholder = String(info.defaultSaveDirectory);
  }
  historyLocation.textContent = historyPath
    ? `Participant exposure history: ${historyPath}`
    : 'Participant exposure history is kept beside the local results data.';
  renderParticipantPicker(info.participants);
  if (
    updateControls
    && (
      previousParticipantId !== participantInput.value
      || previousSaveDirectory !== resultsDirectoryInput.value
    )
    && (activePreparationToken !== null || manifest || randomizationAudit)
  ) {
    invalidatePreparedBlock();
  }
}

function showAbandonedSessionRecovery(participantId, responseInfo) {
  const activeSessionId = String(responseInfo.activeSessionId ?? '').trim();
  const activePreparationId = String(responseInfo.activePreparationId ?? '').trim();
  if (!activeSessionId || !activePreparationId) {
    clearAbandonedSessionRecovery();
    return;
  }
  abandonedLeaseConflict = Object.freeze({
    participantId,
    activeSessionId,
    activePreparationId,
  });
  releaseAbandonedButton.disabled = false;
  releaseAbandonedButton.classList.remove('hidden');
  releaseAbandonedStatus.className = 'status error';
  releaseAbandonedStatus.textContent = `Participant ${participantId} already has an active session. Release it only if that session was abandoned.`;
}

function clearAbandonedSessionRecovery() {
  abandonedLeaseConflict = null;
  releaseAbandonedButton.disabled = false;
  releaseAbandonedButton.classList.add('hidden');
  releaseAbandonedStatus.className = 'status hidden';
  releaseAbandonedStatus.textContent = '';
}

function hideAbandonedSessionRecoveryIfParticipantChanged() {
  if (
    abandonedLeaseConflict
    && participantInput.value.trim() !== abandonedLeaseConflict.participantId
  ) clearAbandonedSessionRecovery();
}

async function releaseAbandonedSession() {
  const conflict = abandonedLeaseConflict;
  if (!conflict || participantInput.value.trim() !== conflict.participantId) {
    clearAbandonedSessionRecovery();
    return;
  }
  const confirmed = window.confirm(
    `Release the active session for participant "${conflict.participantId}"? Only continue if the other session was abandoned.`,
  );
  if (!confirmed) return;
  releaseAbandonedButton.disabled = true;
  releaseAbandonedStatus.className = 'status';
  releaseAbandonedStatus.textContent = 'Releasing the abandoned participant session…';
  try {
    const response = await fetch('/api/force-release-session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        participantId: conflict.participantId,
        expectedSessionId: conflict.activeSessionId,
        expectedPreparationId: conflict.activePreparationId,
        confirmAbandonedSession: true,
      }),
    });
    const info = await response.json();
    if (!response.ok) throw new Error(info.error || `server returned ${response.status}`);
    if (
      info.participantId !== conflict.participantId
      || ![
        'abandoned_session_released',
        'no_active_session',
        'active_session_changed',
      ].includes(info.code)
      || typeof info.released !== 'boolean'
      || (
        info.code === 'abandoned_session_released'
        && (
          info.released !== true
          || info.releasedSessionId !== conflict.activeSessionId
          || info.releasedPreparationId !== conflict.activePreparationId
          || info.activeSessionId !== null
          || info.activePreparationId !== null
        )
      )
      || (
        info.code === 'no_active_session'
        && (
          info.released !== false
          || info.releasedSessionId !== null
          || info.releasedPreparationId !== null
          || info.activeSessionId !== null
          || info.activePreparationId !== null
        )
      )
      || (
        info.code === 'active_session_changed'
        && (
          info.released !== false
          || info.releasedSessionId !== null
          || info.releasedPreparationId !== null
          || !String(info.activeSessionId ?? '').trim()
          || !String(info.activePreparationId ?? '').trim()
          || (
            info.activeSessionId === conflict.activeSessionId
            && info.activePreparationId === conflict.activePreparationId
          )
        )
      )
    ) {
      throw new Error('The server returned an invalid abandoned-session release result.');
    }
    if (participantInput.value.trim() === conflict.participantId) {
      activeSessionLease = null;
      invalidatePreparedBlock();
      if (info.code === 'active_session_changed') {
        prepareStatus.className = 'status error';
        prepareStatus.textContent = 'The active session changed and was not released. Generate and recheck before trying recovery again.';
      } else {
        prepareStatus.className = 'status good';
        prepareStatus.textContent = info.released
          ? 'The matching abandoned session was released. Generate and audit a new block.'
          : 'No active session remained. Generate and audit a new block.';
      }
    }
    clearAbandonedSessionRecovery();
  } catch (error) {
    if (participantInput.value.trim() !== conflict.participantId) {
      clearAbandonedSessionRecovery();
      return;
    }
    releaseAbandonedButton.disabled = false;
    releaseAbandonedStatus.className = 'status error';
    releaseAbandonedStatus.textContent = `Could not release the abandoned session: ${String(error.message || error)}`;
  }
}

async function prepareBlock() {
  if (activeSessionLease) {
    prepareButton.disabled = true;
    prepareStatus.className = 'status';
    prepareStatus.textContent = 'Closing the previous participant-session reservation…';
    try {
      await releaseActiveSessionLease();
    } catch (error) {
      prepareButton.disabled = false;
      return showPrepareError(
        `Could not close the previous participant session: ${String(error.message || error)}`,
      );
    }
    prepareButton.disabled = false;
  }
  const initialSnapshot = readPreparationSnapshot();
  if (!initialSnapshot.participantId) return showPrepareError('Participant ID is required.');
  if (
    !Number.isInteger(initialSnapshot.baseStimulusCount)
    || initialSnapshot.baseStimulusCount < 4
    || initialSnapshot.baseStimulusCount > 96
  ) {
    return showPrepareError('Stimuli must be an integer from 4 to 96.');
  }
  if (
    !Number.isInteger(initialSnapshot.seed)
    || initialSnapshot.seed < 0
    || initialSnapshot.seed > MAX_UINT32
  ) {
    return showPrepareError('Run code must be a whole number from 0 to 4294967295.');
  }
  if (initialSnapshot.signalMode === 'mixed-aligned') {
    try {
      parseMixedConditionRatio(initialSnapshot.mixedConditionRatio);
    } catch (error) {
      return showPrepareError(String(error.message || error));
    }
  }

  const generationToken = ++preparationGeneration;
  activePreparationToken = generationToken;
  preparedSettingsSnapshot = null;
  prepareButton.disabled = true;
  startButton.disabled = true;
  preparedPanel.classList.add('hidden');
  manifest = null;
  manifestUrl = null;
  manifestBaseUrl = null;
  preparationId = null;
  stimulusById = new Map();
  audioBuffers = new Map();
  clearRandomizationAudit();
  prepareStatus.className = 'status';
  prepareStatus.textContent = {
    mixed: 'Generating the carrier-controlled visual/IR schedule and audio cache…',
    'mixed-aligned': 'Generating the four-way aligned visual/IR schedule and audio cache…',
    visual: 'Generating the frozen silent visual schedule…',
    ir: 'Generating the frozen IR-audio schedule and audio cache…',
    paired: 'Generating the repeated-pair schedule and complete audio cache…',
  }[initialSnapshot.signalMode];
  try {
    const rememberedState = await rememberPreferences({
      quiet: true,
      updateControls: false,
    });
    if (generationToken !== preparationGeneration) return;
    resultsDirectoryInput.value = String(rememberedState.saveDirectory ?? '').trim();
    const requestSnapshot = readPreparationSnapshot();
    if (!requestSnapshot.resultsDirectory) throw new Error('Results directory is required.');
    const response = await fetch('/api/prepare-session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestSnapshot),
    });
    const info = await response.json();
    if (!preparationRequestIsCurrent(generationToken, requestSnapshot)) return;
    if (!response.ok) {
      if (info.randomizationAudit) {
        randomizationAudit = normalizeRandomizationAudit(
          info.randomizationAudit,
          requestSnapshot.seed,
          {
            expectedBaseStimulusSlots: requestSnapshot.baseStimulusCount,
            expectedParticipantId: requestSnapshot.participantId,
            signalMode: requestSnapshot.signalMode,
          },
        );
        renderRandomizationAudit(randomizationAudit);
      }
      throw new Error(info.error || `server returned ${response.status}`);
    }
    const boundSaveDirectory = String(info.saveDirectory ?? '').trim();
    if (!boundSaveDirectory || boundSaveDirectory !== requestSnapshot.resultsDirectory) {
      throw new Error('The prepared session is not bound to the selected results directory.');
    }
    const preparedPreparationId = String(info.preparationId ?? '').trim();
    if (!preparedPreparationId) {
      throw new Error('The server did not return a preparation identifier.');
    }
    const preparedManifestUrl = new URL(info.manifestUrl, window.location.href).href;
    const preparedManifestBaseUrl = new URL('.', preparedManifestUrl).href;
    const manifestResponse = await fetch(preparedManifestUrl, { cache: 'no-store' });
    if (!manifestResponse.ok) throw new Error('Generated manifest could not be loaded.');
    const preparedManifest = await manifestResponse.json();
    if (!preparationRequestIsCurrent(generationToken, requestSnapshot)) return;
    if (info.sessionId !== preparedManifest.session_id) {
      throw new Error('The prepared session identifier does not match its manifest.');
    }
    const preparedAudit = normalizeRandomizationAudit(
      info.randomizationAudit,
      requestSnapshot.seed,
      {
        generatedManifest: preparedManifest,
        expectedBaseStimulusSlots: requestSnapshot.baseStimulusCount,
        expectedParticipantId: requestSnapshot.participantId,
        signalMode: requestSnapshot.signalMode,
      },
    );
    randomizationAudit = preparedAudit;
    renderRandomizationAudit(preparedAudit);
    if (!preparedAudit.accepted) {
      throw new Error('Candidate repeat slots exceed 10%. Generate another candidate.');
    }
    updatePreparedPreview(preparedManifest, preparedAudit);
    prepareStatus.textContent = 'Preloading every image and audio buffer…';
    const preparedAudioBuffers = await preloadAssets(
      preparedManifest,
      preparedManifestBaseUrl,
    );
    if (!preparationRequestIsCurrent(generationToken, requestSnapshot)) return;
    manifest = preparedManifest;
    manifestUrl = preparedManifestUrl;
    manifestBaseUrl = preparedManifestBaseUrl;
    preparationId = preparedPreparationId;
    stimulusById = new Map(manifest.stimuli.map(item => [item.stimulus_id, item]));
    audioBuffers = preparedAudioBuffers;
    randomizationAudit = preparedAudit;
    preparedSettingsSnapshot = Object.freeze({ ...requestSnapshot });
    prepareStatus.className = 'status good';
    prepareStatus.textContent = 'Randomization accepted; block is frozen and fully preloaded.';
    const preparedStimuli = manifest.settings.baseStimulusCount;
    const runCodeSummary = preparedAudit.requestedSeed === preparedAudit.effectiveSeed
      ? `run code ${preparedAudit.effectiveSeed}`
      : `run code ${preparedAudit.requestedSeed} → ${preparedAudit.effectiveSeed}`;
    preparedSummary.textContent = `${preparedStimuli} stimuli · ${manifest.trials.length} presentations · ${humanSignalMode(manifest.settings.signalMode)} · ${runCodeSummary}`;
    startButton.disabled = false;
    preparedPanel.classList.remove('hidden');
  } catch (error) {
    if (generationToken === preparationGeneration) {
      showPrepareError(String(error.message || error));
    }
  } finally {
    if (activePreparationToken === generationToken) activePreparationToken = null;
    prepareButton.disabled = false;
  }
}

function readPreparationSnapshot() {
  const seedText = document.getElementById('seed').value.trim();
  return {
    participantId: participantInput.value.trim(),
    resultsDirectory: resultsDirectoryInput.value.trim(),
    split: document.getElementById('split').value,
    signalMode: document.getElementById('signal-mode').value,
    baseStimulusCount: Number(document.getElementById('base-stimulus-count').value),
    glyphComposition: document.getElementById('glyph-composition').value,
    progression: document.getElementById('progression').value,
    feedbackEnabled: document.getElementById('feedback-enabled').value === 'on',
    mixedConditionRatio: document.getElementById('mixed-condition-ratio').value.trim(),
    seed: seedText === '' ? NaN : Number(seedText),
  };
}

function preparationRequestIsCurrent(generationToken, snapshot) {
  return generationToken === preparationGeneration
    && preparationSnapshotsEqual(snapshot, readPreparationSnapshot());
}

function preparationSnapshotsEqual(left, right) {
  return left.participantId === right.participantId
    && left.resultsDirectory === right.resultsDirectory
    && left.split === right.split
    && left.signalMode === right.signalMode
    && left.baseStimulusCount === right.baseStimulusCount
    && left.glyphComposition === right.glyphComposition
    && left.progression === right.progression
    && left.feedbackEnabled === right.feedbackEnabled
    && left.mixedConditionRatio === right.mixedConditionRatio
    && left.seed === right.seed;
}

function normalizeRandomizationAudit(rawAudit, requestedSeed, {
  generatedManifest = null,
  expectedBaseStimulusSlots,
  expectedParticipantId = participantInput.value.trim(),
  signalMode,
} = {}) {
  if (!rawAudit || typeof rawAudit !== 'object') {
    throw new Error('The server did not return the required pre-session randomization audit.');
  }
  let derivedUniqueTransformations = null;
  if (generatedManifest) {
    const signatures = generatedManifest.stimuli.map(item => item.transformation_signature);
    if (signatures.some(signature => typeof signature !== 'string' || !signature)) {
      throw new Error('The generated manifest is missing transformation signatures.');
    }
    derivedUniqueTransformations = new Set(generatedManifest.stimuli.map(
      item => item.transformation_signature,
    )).size;
  }
  const eligibleTransformations = auditInteger(rawAudit, [
    'eligibleTransformations', 'eligible_transformations',
  ]);
  const eligibleByGlyph = normalizeEligibleByGlyph(rawAudit);
  const participantPreviouslySeen = auditInteger(rawAudit, [
    'participantPreviouslySeen', 'participant_previously_seen',
  ]);
  const participantPreviouslySeenAll = auditOptionalInteger(rawAudit, [
    'participantPreviouslySeenAll', 'participant_previously_seen_all',
  ]);
  const candidateUniqueTransformations = auditInteger(rawAudit, [
    'candidateUniqueTransformations', 'candidate_unique_transformations',
  ]);
  const candidateSignatureDigest = String(
    rawAudit.candidateSignatureDigest ?? rawAudit.candidate_signature_digest ?? '',
  ).trim();
  if (!/^[a-f0-9]{64}$/.test(candidateSignatureDigest)) {
    throw new Error('The randomization audit has an invalid candidate signature digest.');
  }
  const historicalRepeatSlots = auditInteger(rawAudit, [
    'historicalRepeatSlots', 'historicalRepeats',
    'historical_repeat_slots', 'historical_repeats',
  ]);
  const withinCandidateDuplicateSlots = auditInteger(rawAudit, [
    'withinCandidateDuplicateSlots', 'within_candidate_duplicate_slots',
  ]);
  const repeatSlots = auditInteger(rawAudit, [
    'repeatSlots', 'repeat_slots',
  ]);
  const rerandomizations = auditInteger(rawAudit, [
    'rerandomizations', 'rerandomization_count',
  ]);
  const requestedAuditSeed = auditInteger(rawAudit, ['requestedSeed', 'requested_seed']);
  const effectiveSeed = auditInteger(rawAudit, ['effectiveSeed', 'effective_seed']);
  const threshold = auditNumber(rawAudit, ['threshold', 'repeat_threshold']);
  const reportedHistoricalRate = auditNumber(rawAudit, [
    'historicalRepeatRate', 'historical_repeat_rate',
  ]);
  const reportedRepeatRate = auditNumber(rawAudit, ['repeatRate', 'repeat_rate']);
  const reportedMaximumRepeatSlots = auditInteger(rawAudit, [
    'maximumRepeatSlots', 'maximum_repeat_slots',
  ]);
  const baseStimulusSlots = auditInteger(rawAudit, [
    'candidateStimuli', 'candidate_stimuli',
  ]);
  const participantId = String(
    rawAudit.participantId ?? rawAudit.participant_id ?? '',
  ).trim();
  if (!participantId || participantId !== expectedParticipantId) {
    throw new Error('The randomization audit does not match the selected participant.');
  }
  if (
    derivedUniqueTransformations !== null
    && candidateUniqueTransformations !== derivedUniqueTransformations
  ) {
    throw new Error('The audit candidate count does not match the generated transformations.');
  }
  if (baseStimulusSlots !== expectedBaseStimulusSlots) {
    throw new Error('The audit slot count does not match the requested stimuli.');
  }
  if (
    eligibleTransformations < candidateUniqueTransformations
    || participantPreviouslySeen > eligibleTransformations
    || candidateUniqueTransformations > baseStimulusSlots
    || historicalRepeatSlots > baseStimulusSlots
    || withinCandidateDuplicateSlots > baseStimulusSlots
    || repeatSlots > baseStimulusSlots
  ) {
    throw new Error('The server returned inconsistent randomization statistics.');
  }
  const eligibleByGlyphTotal = Object.values(eligibleByGlyph).reduce(
    (total, count) => total + count,
    0,
  );
  if (eligibleByGlyphTotal !== eligibleTransformations) {
    throw new Error('The eligible glyph-count breakdown does not match the eligible total.');
  }
  if (
    requestedAuditSeed !== requestedSeed
    || (generatedManifest && effectiveSeed !== generatedManifest.settings.seed)
  ) {
    throw new Error('The audit run codes do not match the requested and generated schedules.');
  }
  if (Math.abs(threshold - HISTORICAL_REPEAT_THRESHOLD) > Number.EPSILON) {
    throw new Error('The randomization audit must use the 10% repeat threshold.');
  }
  if (withinCandidateDuplicateSlots !== baseStimulusSlots - candidateUniqueTransformations) {
    throw new Error('The within-candidate repeat count does not match the generated schedule.');
  }
  if (
    repeatSlots < historicalRepeatSlots
    || repeatSlots < withinCandidateDuplicateSlots
    || repeatSlots > historicalRepeatSlots + withinCandidateDuplicateSlots
  ) {
    throw new Error('The overall repeat count is inconsistent with its components.');
  }
  const historicalRepeatRate = baseStimulusSlots
    ? historicalRepeatSlots / baseStimulusSlots
    : 0;
  const repeatRate = baseStimulusSlots ? repeatSlots / baseStimulusSlots : 0;
  if (
    Math.abs(reportedHistoricalRate - historicalRepeatRate) > 1e-9
    || Math.abs(reportedRepeatRate - repeatRate) > 1e-9
  ) {
    throw new Error('The audit repeat rates do not match their repeat counts.');
  }
  const maximumRepeatSlots = Math.floor(threshold * baseStimulusSlots);
  if (reportedMaximumRepeatSlots !== maximumRepeatSlots) {
    throw new Error('The audit maximum repeat count does not match the 10% threshold.');
  }
  const withinThreshold = repeatSlots <= maximumRepeatSlots && repeatRate <= threshold;
  const accepted = rawAudit.accepted === true && withinThreshold;
  return {
    participantId,
    eligibleTransformations,
    eligibleByGlyph,
    participantPreviouslySeen,
    participantPreviouslySeenAll,
    baseStimulusSlots,
    candidateUniqueTransformations,
    candidateSignatureDigest,
    historicalRepeatSlots,
    historicalRepeatRate,
    withinCandidateDuplicateSlots,
    repeatSlots,
    repeatRate,
    threshold,
    maximumRepeatSlots,
    accepted,
    rerandomizations,
    requestedSeed: requestedAuditSeed,
    effectiveSeed,
    signalMode,
  };
}

function renderRandomizationAudit(audit, { revalidated = false } = {}) {
  const number = new Intl.NumberFormat();
  const historyRate = audit.eligibleTransformations
    ? audit.participantPreviouslySeen / audit.eligibleTransformations
    : 0;
  const eligibleGlyphBreakdown = Object.entries(audit.eligibleByGlyph)
    .sort(([left], [right]) => Number(left) - Number(right))
    .map(([glyphCount, count]) => (
      `${glyphCount} ${glyphCount === '1' ? 'glyph' : 'glyphs'} ${number.format(count)}`
    ))
    .join(' · ');
  document.getElementById('audit-eligible').textContent = (
    `${number.format(audit.eligibleTransformations)} total · ${eligibleGlyphBreakdown}`
  );
  document.getElementById('audit-history').textContent = `${number.format(audit.participantPreviouslySeen)} of ${number.format(audit.eligibleTransformations)} (${formatPercent(historyRate, 2)})`;
  document.getElementById('audit-historical-repeats').textContent = `${number.format(audit.historicalRepeatSlots)} of ${number.format(audit.baseStimulusSlots)} (${formatPercent(audit.historicalRepeatRate, 1)})`;
  document.getElementById('audit-within-candidate-repeats').textContent = `${number.format(audit.withinCandidateDuplicateSlots)} of ${number.format(audit.baseStimulusSlots)}`;
  document.getElementById('audit-repeats').textContent = `${number.format(audit.repeatSlots)} of ${number.format(audit.baseStimulusSlots)} (${formatPercent(audit.repeatRate, 1)})`;
  document.getElementById('audit-threshold').textContent = `≤ ${number.format(audit.maximumRepeatSlots)} of ${number.format(audit.baseStimulusSlots)} (≤ 10%)`;
  document.getElementById('audit-candidate-count').textContent = `${number.format(audit.candidateUniqueTransformations)} unique of ${number.format(audit.baseStimulusSlots)} slots`;
  document.getElementById('audit-requested-seed').textContent = String(audit.requestedSeed);
  document.getElementById('audit-effective-seed').textContent = audit.effectiveSeed === audit.requestedSeed
    ? String(audit.effectiveSeed)
    : `${audit.effectiveSeed} (changed)`;
  document.getElementById('audit-rerandomizations').textContent = number.format(
    audit.rerandomizations,
  );
  auditStatus.className = `audit-badge ${audit.accepted ? 'good' : 'error'}`;
  let statusText = 'Rejected';
  if (audit.accepted) {
    if (revalidated) statusText = 'Rechecked and accepted';
    else if (audit.rerandomizations) statusText = 'Rerandomized and accepted';
    else statusText = 'Accepted';
  }
  auditStatus.textContent = statusText;
  const pairedNote = audit.signalMode === 'paired'
    ? ' The intentional second presentation in each pair is excluded.'
    : '';
  const allTimeNote = Number.isInteger(audit.participantPreviouslySeenAll)
    ? ` ${number.format(audit.participantPreviouslySeenAll)} unique transformations are recorded for this participant across all settings.`
    : '';
  const historyPath = String(localState?.historyPath ?? localState?.history_path ?? '').trim();
  const pathNote = historyPath ? ` History file: ${historyPath}` : '';
  auditNote.textContent = `The 10% rule uses the ${number.format(audit.baseStimulusSlots)} requested base-stimulus slots. A slot repeats if its transformation was already in participant history or appeared earlier in this candidate.${pairedNote}${allTimeNote}${pathNote}`;
  randomizationAuditPanel.classList.remove('hidden');
}

function clearRandomizationAudit() {
  randomizationAudit = null;
  randomizationAuditPanel.classList.add('hidden');
  auditStatus.className = 'audit-badge';
  auditStatus.textContent = '';
  auditNote.textContent = '';
}

function auditInteger(object, names) {
  const value = auditNumber(object, names);
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new Error(`Invalid randomization audit field: ${names[0]}.`);
  }
  return value;
}

function auditOptionalInteger(object, names) {
  const value = names.map(name => object[name]).find(item => item !== undefined);
  if (value === undefined || value === null) return null;
  const number = Number(value);
  return Number.isSafeInteger(number) && number >= 0 ? number : null;
}

function normalizeEligibleByGlyph(rawAudit) {
  const rawBreakdown = rawAudit.eligibleByGlyph ?? rawAudit.eligible_by_glyph;
  if (
    !rawBreakdown
    || typeof rawBreakdown !== 'object'
    || Array.isArray(rawBreakdown)
  ) {
    throw new Error('The randomization audit is missing the eligible glyph-count breakdown.');
  }
  const entries = Object.entries(rawBreakdown);
  if (
    !entries.length
    || entries.some(([glyphCount, count]) => (
      !['1', '2', '3'].includes(glyphCount)
      || !Number.isSafeInteger(Number(count))
      || Number(count) < 0
    ))
  ) {
    throw new Error('The randomization audit has an invalid eligible glyph-count breakdown.');
  }
  return Object.fromEntries(entries.map(([glyphCount, count]) => [glyphCount, Number(count)]));
}

function normalizeSessionLease(rawLease, expectedIdentity) {
  if (!rawLease || typeof rawLease !== 'object' || Array.isArray(rawLease)) {
    throw new Error('The server did not reserve this participant session.');
  }
  const participantId = String(rawLease.participantId ?? '').trim();
  const sessionId = String(rawLease.sessionId ?? '').trim();
  const leasePreparationId = String(rawLease.preparationId ?? '').trim();
  if (
    participantId !== expectedIdentity.participantId
    || sessionId !== expectedIdentity.sessionId
    || leasePreparationId !== expectedIdentity.preparationId
  ) {
    throw new Error('The participant session reservation does not match this block.');
  }
  const acquired = rawLease.acquired === true;
  const renewed = rawLease.renewed === true;
  const inactivityTtlSeconds = Number(rawLease.inactivityTtlSeconds);
  const expiresAt = String(rawLease.expiresAt ?? '').trim();
  if (
    (!acquired && !renewed)
    || !Number.isSafeInteger(inactivityTtlSeconds)
    || inactivityTtlSeconds <= 0
    || !expiresAt
    || !Number.isFinite(Date.parse(expiresAt))
  ) {
    throw new Error('The participant session reservation is invalid.');
  }
  return {
    participantId,
    sessionId,
    preparationId: leasePreparationId,
    acquired,
    renewed,
    inactivityTtlSeconds,
    expiresAt,
  };
}

function sameSessionLeaseIdentity(left, right) {
  return Boolean(
    left
    && right
    && left.participantId === right.participantId
    && left.sessionId === right.sessionId
    && left.preparationId === right.preparationId
  );
}

async function releaseSessionLease(lease) {
  const response = await fetch('/api/release-session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      participantId: lease.participantId,
      sessionId: lease.sessionId,
      preparationId: lease.preparationId,
    }),
  });
  const info = await response.json();
  if (!response.ok) throw new Error(info.error || `server returned ${response.status}`);
  if (typeof info.released !== 'boolean') {
    throw new Error('The server returned an invalid participant-session release result.');
  }
  if (sameSessionLeaseIdentity(activeSessionLease, lease)) activeSessionLease = null;
  return info.released;
}

async function releaseActiveSessionLease() {
  if (!activeSessionLease) return false;
  return releaseSessionLease(activeSessionLease);
}

function auditNumber(object, names) {
  const value = names.map(name => object[name]).find(item => item !== undefined);
  const number = Number(value);
  if (!Number.isFinite(number)) {
    throw new Error(`Missing randomization audit field: ${names[0]}.`);
  }
  return number;
}

function formatPercent(rate, digits) {
  return `${(rate * 100).toFixed(digits)}%`;
}

function updateSetupPreview() {
  const split = document.getElementById('split').value;
  const signalMode = document.getElementById('signal-mode').value;
  const baseStimulusCount = Number(document.getElementById('base-stimulus-count').value);
  const glyphComposition = document.getElementById('glyph-composition').value;
  const feedbackEnabled = document.getElementById('feedback-enabled').value === 'on';
  const seedValue = document.getElementById('seed').value.trim();
  const seed = seedValue === '' ? NaN : Number(seedValue);
  const ratioText = document.getElementById('mixed-condition-ratio').value.trim();
  const ratioField = document.getElementById('mixed-condition-ratio-field');
  ratioField.classList.toggle('hidden', signalMode !== 'mixed-aligned');
  let validRatio = true;
  if (signalMode === 'mixed-aligned') {
    try { parseMixedConditionRatio(ratioText); } catch (_error) { validRatio = false; }
  }
  const validCount = Number.isInteger(baseStimulusCount)
    && baseStimulusCount >= 4
    && baseStimulusCount <= 96;
  const validSeed = Number.isInteger(seed) && seed >= 0 && seed <= MAX_UINT32;
  const presentationCount = validCount
    ? baseStimulusCount * (signalMode === 'paired' ? 2 : 1)
    : null;

  document.getElementById('preview-stimuli').textContent = validCount
    ? String(baseStimulusCount)
    : 'Enter 4–96';
  document.getElementById('preview-presentations').textContent = presentationCount === null
    ? '—'
    : signalMode === 'paired'
      ? `${baseStimulusCount} × 2 = ${presentationCount}`
      : String(presentationCount);
  document.getElementById('preview-glyphs').textContent = validCount && validSeed
    ? formatGlyphDistribution(baseStimulusCount, glyphComposition, seed)
    : '—';
  document.getElementById('preview-conditions').textContent = (
    validCount && validRatio && (!["mixed", "mixed-aligned"].includes(signalMode) || validSeed)
  )
    ? formatConditionDistribution(baseStimulusCount, signalMode, seed, ratioText)
    : '—';
  document.getElementById('preview-duration').textContent = presentationCount === null
    ? '—'
    : formatEstimatedDuration(presentationCount, feedbackEnabled);
  document.getElementById('preview-source').textContent = split === 'train'
    ? 'Training · 13/19 families'
    : 'Held-out · 6/19 families';
  document.getElementById('preview-feedback').textContent = feedbackEnabled ? 'On' : 'Off';
  document.getElementById('preview-run-code').textContent = validSeed ? String(seed) : 'Invalid';

  const warnings = [];
  if (split === 'test' && feedbackEnabled) {
    warnings.push('Feedback exposes held-out mappings and may invalidate generalisation testing.');
  }
  if (signalMode === 'paired' && feedbackEnabled) {
    warnings.push('In paired mode, feedback can reveal an answer before its repeated presentation.');
  }
  if (signalMode === 'mixed-aligned' && feedbackEnabled) {
    warnings.push('Feedback can train whether aligned and complementary signals should be integrated.');
  }
  feedbackWarning.textContent = `${warnings.join(' ')}${warnings.length ? ' You can still generate this block.' : ''}`;
  feedbackWarning.classList.toggle('hidden', warnings.length === 0);
}

function formatGlyphDistribution(count, glyphComposition, seed) {
  if (glyphComposition !== 'automatic') {
    return `${count} × ${glyphComposition}-glyph`;
  }
  const counts = [Math.floor(count / 3), Math.floor(count / 3), Math.floor(count / 3)];
  for (let index = 0; index < count % 3; index += 1) {
    counts[(seed % 3 + index) % 3] += 1;
  }
  return counts.map((value, index) => `${value} × ${index + 1}`).join(' · ');
}

function formatConditionDistribution(
  count,
  signalMode,
  seed,
  ratioText = DEFAULT_MIXED_ALIGNED_RATIO,
) {
  if (signalMode === 'visual') return `${count} visual baseline · silent`;
  if (signalMode === 'ir') return `${count} source scaffold + IR audio`;
  if (signalMode === 'paired') {
    return `${count} visual + neutral carrier · ${count} scaffold + IR · repeated`;
  }
  if (signalMode === 'mixed-aligned') {
    const weights = parseMixedConditionRatio(ratioText);
    const [carrier, visualAligned, irAligned, complementary] = weightedQuotas(
      count, weights, seed,
    );
    return `${carrier} identity visual · ${visualAligned} identity visual + visual · ${irAligned} identity visual + IR · ${complementary} changed complementary IR`;
  }
  const extraIsVisual = seed % 2 === 0;
  const visualCount = Math.floor(count / 2) + (count % 2 && extraIsVisual ? 1 : 0);
  const irCount = count - visualCount;
  const seededExtra = count % 2
    ? ` · seeded extra: ${extraIsVisual ? 'visual + neutral carrier' : 'scaffold + IR'}`
    : '';
  return `${visualCount} visual + neutral carrier · ${irCount} scaffold + IR${seededExtra}`;
}

function parseMixedConditionRatio(value) {
  const pieces = String(value).trim().split(':');
  if (pieces.length !== 4 || pieces.some(piece => !/^[0-9]+$/.test(piece))) {
    throw new Error('Four-way condition ratio must contain four positive integers, such as 1:1:1:2.');
  }
  const weights = pieces.map(Number);
  if (weights.some(weight => !Number.isSafeInteger(weight) || weight <= 0)) {
    throw new Error('Four-way condition ratio weights must be positive integers.');
  }
  if (weights.reduce((total, weight) => total + weight, 0) > 40) {
    throw new Error('Four-way condition ratio weights must sum to at most 40.');
  }
  return weights;
}

function weightedQuotas(count, weights, seed) {
  const totalWeight = weights.reduce((total, weight) => total + weight, 0);
  const counts = weights.map(weight => Math.floor(count * weight / totalWeight));
  let remaining = count - counts.reduce((total, value) => total + value, 0);
  const tieStart = seed % weights.length;
  const ranked = weights.map((weight, index) => ({
    index,
    remainder: (count * weight) % totalWeight,
    tie: (index - tieStart + weights.length) % weights.length,
  })).sort((left, right) => (
    right.remainder - left.remainder || left.tie - right.tie
  ));
  for (const item of ranked) {
    if (!remaining) break;
    counts[item.index] += 1;
    remaining -= 1;
  }
  return counts;
}

function formatEstimatedDuration(presentationCount, feedbackEnabled) {
  const feedbackSeconds = feedbackEnabled ? 0.65 : 0.18;
  const totalSeconds = Math.round(presentationCount * (
    3.65 + 0.22 + ESTIMATED_RESPONSE_SECONDS + feedbackSeconds
  ));
  if (totalSeconds < 60) return `≈ ${totalSeconds} sec`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `≈ ${minutes} min${seconds ? ` ${seconds} sec` : ''}`;
}

function updatePreparedPreview(generatedManifest, audit) {
  const glyphCounts = [1, 2, 3].map(glyphCount => (
    Number(generatedManifest.glyph_count_distribution?.[String(glyphCount)] || 0)
  ));
  document.getElementById('preview-glyphs').textContent = (
    generatedManifest.settings.glyphComposition === 'automatic'
      ? glyphCounts.map((count, index) => `${count} × ${index + 1}`).join(' · ')
      : `${generatedManifest.settings.baseStimulusCount} × ${generatedManifest.settings.glyphComposition}-glyph`
  );
  document.getElementById('preview-conditions').textContent = formatConditionDistribution(
    generatedManifest.settings.baseStimulusCount,
    generatedManifest.settings.signalMode,
    audit.effectiveSeed,
    generatedManifest.settings.mixedConditionRatio,
  );
  document.getElementById('preview-run-code').textContent = (
    audit.requestedSeed === audit.effectiveSeed
      ? String(audit.effectiveSeed)
      : `${audit.requestedSeed} → ${audit.effectiveSeed}`
  );
}

function setFreshRunCode() {
  const values = new Uint32Array(1);
  if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(values);
  else values[0] = Math.floor(Math.random() * (MAX_UINT32 + 1));
  document.getElementById('seed').value = String(values[0]);
}

async function preloadAssets(targetManifest, targetManifestBaseUrl) {
  const imagePaths = new Set();
  const audioPaths = new Set();
  for (const trial of targetManifest.trials) imagePaths.add(trial.plate_png);
  for (const stimulus of targetManifest.stimuli) {
    for (const choice of stimulus.response_choices) imagePaths.add(choice.png);
  }
  await Promise.all([...imagePaths].map(path => (
    preloadImage(new URL(path, targetManifestBaseUrl).href)
  )));

  for (const trial of targetManifest.trials) {
    if (trial.audio_wav) audioPaths.add(trial.audio_wav);
  }
  const preparedAudioBuffers = new Map();
  if (!audioPaths.size) return preparedAudioBuffers;
  audioContext ||= new AudioContext();
  await audioContext.resume();
  await Promise.all([...audioPaths].map(async path => {
    const response = await fetch(new URL(path, targetManifestBaseUrl).href);
    if (!response.ok) throw new Error(`Audio preload failed: ${path}`);
    const buffer = await audioContext.decodeAudioData(await response.arrayBuffer());
    preparedAudioBuffers.set(path, buffer);
  }));
  if (preparedAudioBuffers.size !== audioPaths.size) {
    throw new Error('Not every soundscape decoded.');
  }
  return preparedAudioBuffers;
}

async function startBlock() {
  if (
    !manifest
    || !randomizationAudit?.accepted
    || !preparedSettingsSnapshot
    || !preparationId
  ) {
    return showPrepareError('Generate an accepted randomization audit before starting.');
  }
  const revalidationGeneration = preparationGeneration;
  const revalidationSnapshot = preparedSettingsSnapshot;
  const revalidationManifest = manifest;
  const revalidationPreparationId = preparationId;
  const preparedDigest = randomizationAudit.candidateSignatureDigest;
  if (!preparationRequestIsCurrent(revalidationGeneration, revalidationSnapshot)) {
    invalidatePreparedBlock();
    return showPrepareError('Settings changed. Generate and audit the block again.');
  }
  const pendingSession = readSessionSettings();
  const calibrationError = validateCalibration(pendingSession);
  if (calibrationError) return showPrepareError(calibrationError);
  startButton.disabled = true;
  prepareButton.disabled = true;
  startButton.textContent = 'Rechecking…';
  preparedSummary.textContent = 'Rechecking current participant history against this frozen block…';
  let revalidationSucceeded = false;
  let revalidationRequestSent = false;
  let competingSessionDetected = false;
  const leaseIdentity = {
    participantId: revalidationSnapshot.participantId,
    sessionId: revalidationManifest.session_id,
    preparationId: revalidationPreparationId,
  };
  try {
    if (audioContext) await audioContext.resume();
    revalidationRequestSent = true;
    const response = await fetch('/api/revalidate-session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        participantId: revalidationSnapshot.participantId,
        sessionId: revalidationManifest.session_id,
        preparationId: revalidationPreparationId,
        candidateSignatureDigest: preparedDigest,
      }),
    });
    const info = await response.json();
    if (!response.ok) {
      if (response.status === 409 && info.code === 'participant_session_active') {
        competingSessionDetected = true;
        showAbandonedSessionRecovery(revalidationSnapshot.participantId, info);
      }
      throw new Error(info.error || `server returned ${response.status}`);
    }
    clearAbandonedSessionRecovery();
    if (
      info.sessionId !== revalidationManifest.session_id
      || info.preparationId !== revalidationPreparationId
    ) {
      throw new Error('The revalidation response does not match the prepared block.');
    }
    const freshAudit = normalizeRandomizationAudit(
      info.randomizationAudit,
      revalidationSnapshot.seed,
      {
        generatedManifest: revalidationManifest,
        expectedBaseStimulusSlots: revalidationSnapshot.baseStimulusCount,
        expectedParticipantId: revalidationSnapshot.participantId,
        signalMode: revalidationSnapshot.signalMode,
      },
    );
    if (freshAudit.candidateSignatureDigest !== preparedDigest) {
      throw new Error('The revalidated candidate does not match the prepared block.');
    }
    randomizationAudit = freshAudit;
    renderRandomizationAudit(freshAudit, { revalidated: true });
    if (!freshAudit.accepted) {
      preparedSettingsSnapshot = null;
      startButton.disabled = true;
      preparedPanel.classList.add('hidden');
      prepareStatus.className = 'status error';
      prepareStatus.textContent = 'Participant history changed and this block now exceeds the 10% repeat threshold. Generate a new block.';
      return;
    }
    activeSessionLease = normalizeSessionLease(info.sessionLease, leaseIdentity);
    if (!preparationRequestIsCurrent(revalidationGeneration, revalidationSnapshot)) {
      throw new Error('Settings changed while the participant session was being reserved.');
    }
    revalidationSucceeded = true;
  } catch (error) {
    if (revalidationRequestSent) {
      try {
        await releaseSessionLease(activeSessionLease ?? leaseIdentity);
      } catch (releaseError) {
        if (!competingSessionDetected) activeSessionLease ??= { ...leaseIdentity };
        console.warn('Participant session reservation release failed.', releaseError);
      }
    }
    if (revalidationGeneration !== preparationGeneration) return;
    randomizationAudit = { ...randomizationAudit, accepted: false };
    auditStatus.className = 'audit-badge error';
    auditStatus.textContent = 'Revalidation failed';
    auditNote.textContent += ` Start is blocked: ${String(error.message || error)}`;
    preparedSettingsSnapshot = null;
    startButton.disabled = true;
    preparedPanel.classList.add('hidden');
    prepareStatus.className = 'status error';
    prepareStatus.textContent = 'Could not revalidate this block. Generate a new block before starting.';
    return;
  } finally {
    if (!revalidationSucceeded) prepareButton.disabled = false;
    startButton.textContent = 'Start block';
  }

  if (!preparationRequestIsCurrent(revalidationGeneration, revalidationSnapshot)) {
    try {
      await releaseActiveSessionLease();
    } catch (error) {
      console.warn('Participant session reservation release failed.', error);
    }
    prepareButton.disabled = false;
    invalidatePreparedBlock();
    return showPrepareError('Settings changed. Generate and audit the block again.');
  }
  const startingSession = {
    ...pendingSession,
    randomizationAudit: { ...randomizationAudit },
    sessionLease: { ...activeSessionLease },
  };
  session = startingSession;
  phase = 'starting';
  prepareButton.disabled = true;
  startButton.disabled = true;
  if (startingSession.presentation !== 'compact' && !document.fullscreenElement) {
    await document.documentElement.requestFullscreen().catch(() => {});
  }
  if (
    phase !== 'starting'
    || preparationGeneration !== revalidationGeneration
    || session !== startingSession
  ) {
    try {
      await releaseActiveSessionLease();
    } catch (error) {
      console.warn('Participant session reservation release failed.', error);
    }
    if (phase === 'setup') prepareButton.disabled = false;
    return;
  }
  startingSession.startedAt = new Date().toISOString();
  trialRows = [];
  trialIndex = 0;
  invalidAttempts = 0;
  recordedExposureKeys = new Set();
  pendingExposureRequests = new Map();
  failedExposurePayloads = new Map();
  resultsSaved = false;
  savedResultInfo = null;
  endStateReadyForReset = false;
  setupScreen.classList.add('hidden');
  endScreen.classList.add('hidden');
  trialScreen.classList.remove('hidden');
  fitStage();
  showReady();
}

function readSessionSettings() {
  return {
    participantId: participantInput.value.trim(),
    resultsDirectory: resultsDirectoryInput.value.trim(),
    preparationId,
    split: manifest.settings.split,
    signalMode: manifest.settings.signalMode,
    baseStimulusCount: manifest.settings.baseStimulusCount,
    glyphComposition: manifest.settings.glyphComposition,
    progression: manifest.settings.progression,
    feedbackEnabled: manifest.settings.feedbackEnabled,
    mixedConditionRatio: manifest.settings.mixedConditionRatio || null,
    responseDevice: document.getElementById('response-device').value,
    presentation: document.getElementById('presentation').value,
    displayId: document.getElementById('display-id').value.trim(),
    displayWidthCm: numberOrNull('display-width-cm'),
    viewingDistanceCm: numberOrNull('viewing-distance-cm'),
    targetAngleDeg: numberOrNull('target-angle-deg'),
    seed: manifest.settings.seed,
    randomizationAudit: { ...randomizationAudit },
    difficultyModelVersion: manifest.difficulty_model_version,
  };
}

function invalidatePreparedBlock() {
  if (phase !== 'setup') return;
  preparationGeneration += 1;
  const hadPreparedBlock = Boolean(
    manifest || randomizationAudit || activePreparationToken !== null,
  );
  manifest = null;
  manifestUrl = null;
  manifestBaseUrl = null;
  preparationId = null;
  preparedSettingsSnapshot = null;
  stimulusById = new Map();
  audioBuffers = new Map();
  clearRandomizationAudit();
  startButton.disabled = true;
  preparedPanel.classList.add('hidden');
  if (!hadPreparedBlock) return;
  prepareStatus.className = 'status';
  prepareStatus.textContent = 'Settings changed. Generate and preload the block again.';
}

function validateCalibration(settings) {
  if (settings.presentation !== 'calibrated') return null;
  if (!settings.displayId) return 'Calibrated presentation requires a display ID.';
  if (!(settings.displayWidthCm > 0) || !(settings.viewingDistanceCm > 0)) {
    return 'Calibrated presentation requires display width and viewing distance.';
  }
  return null;
}

function showReady() {
  phase = 'ready';
  stopAudio();
  hide(stimulusImage, mask, choices);
  feedback.textContent = '';
  readyOverlay.classList.remove('hidden');
  readyCopy.textContent = session.responseDevice === 'keyboard'
    ? 'Press any key to start. Use keys 1–4 to answer.'
    : 'Click Start trial. Then click one of four interpretations.';
  readyButton.classList.toggle('hidden', session.responseDevice === 'keyboard');
  const trial = manifest.trials[trialIndex];
  trialStatus.textContent = `Trial ${trialIndex + 1} / ${manifest.trials.length}`;
  conditionStatus.textContent = humanCondition(trial.condition);
}

async function beginTrial(startMethod) {
  if (phase !== 'ready') return;
  phase = 'arming';
  readyOverlay.classList.add('hidden');
  const trial = manifest.trials[trialIndex];
  const stimulus = stimulusById.get(trial.stimulus_id);
  currentAttempt = {
    trial,
    stimulus,
    startMethod,
    invalidReasons: [],
    stage: null,
    visualActualDurationMs: null,
    responseChoices: resolveResponseChoices(trial, stimulus),
  };
  renderChoices(currentAttempt.responseChoices);
  stimulusImage.src = assetUrl(trial.plate_png);
  fitStage();

  let delayBeforeVisualMs = 0;
  audioPlannedOnsetMs = null;
  if (trial.audio_wav) {
    const buffer = audioBuffers.get(trial.audio_wav);
    if (!buffer) return invalidateAttempt('missing_preloaded_audio');
    const scheduled = scheduleRepeatedAudio(buffer);
    audioPlannedOnsetMs = scheduled.performanceOnsetMs;
    delayBeforeVisualMs = Math.max(0, scheduled.performanceOnsetMs - performance.now());
  }
  if (delayBeforeVisualMs > 1) await wait(delayBeforeVisualMs);
  await nextFrame();
  if (phase !== 'arming') return;
  phase = 'stimulus';
  stimulusOnsetMs = performance.now();
  stimulusImage.classList.remove('hidden');
  phaseTimer = window.setTimeout(endStimulus, manifest.stimulus_duration_ms);
  queueMicrotask(() => queueExposureRecord(trial, stimulus));
}

function queueExposureRecord(trial, stimulus) {
  const payload = {
    participantId: session.participantId,
    sessionId: manifest.session_id,
    preparationId: session.preparationId,
    stimulusId: stimulus.stimulus_id,
    transformationSignature: stimulus.transformation_signature,
    trialIndex: trial.trial_index,
  };
  queueExposurePayload(payload);
}

function queueExposurePayload(payload) {
  const key = `${payload.participantId}\u0000${payload.preparationId}\u0000${payload.transformationSignature}`;
  if (recordedExposureKeys.has(key) || pendingExposureRequests.has(key)) return;
  const request = fetch('/api/record-exposure', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    keepalive: true,
  }).then(async response => {
    const info = await response.json();
    if (!response.ok) throw new Error(info.error || `server returned ${response.status}`);
    recordedExposureKeys.add(key);
    failedExposurePayloads.delete(key);
    return info;
  }).catch(error => {
    failedExposurePayloads.set(key, payload);
    console.warn('Participant exposure history update failed.', error);
    return null;
  }).finally(() => {
    pendingExposureRequests.delete(key);
  });
  pendingExposureRequests.set(key, request);
}

async function flushExposureHistory() {
  await Promise.allSettled([...pendingExposureRequests.values()]);
  const retryPayloads = [...failedExposurePayloads.values()];
  for (const payload of retryPayloads) queueExposurePayload(payload);
  await Promise.allSettled([...pendingExposureRequests.values()]);
  return failedExposurePayloads.size;
}

async function endStimulus() {
  if (phase !== 'stimulus') return;
  currentAttempt.visualActualDurationMs = performance.now() - stimulusOnsetMs;
  stimulusImage.classList.add('hidden');
  mask.classList.remove('hidden');
  phase = 'mask';
  phaseTimer = window.setTimeout(showChoices, manifest.mask_duration_ms);
}

async function showChoices() {
  if (phase !== 'mask') return;
  mask.classList.add('hidden');
  choices.classList.remove('hidden');
  await nextFrame();
  responseOnsetMs = performance.now();
  phase = 'response';
}

function resolveResponseChoices(trial, stimulus) {
  if (!Array.isArray(trial.response_choice_ids)) return stimulus.response_choices;
  const byId = new Map(stimulus.response_choices.map(choice => [choice.choice_id, choice]));
  const ordered = trial.response_choice_ids.map(choiceId => byId.get(choiceId));
  return ordered.length === stimulus.response_choices.length && ordered.every(Boolean)
    ? ordered
    : stimulus.response_choices;
}

function renderChoices(responseChoices) {
  choices.replaceChildren();
  responseChoices.forEach((choice, index) => {
    const button = document.createElement('button');
    button.className = 'choice';
    button.dataset.position = String(index + 1);
    button.dataset.choiceId = choice.choice_id;
    button.tabIndex = session?.responseDevice === 'pointer' ? 0 : -1;
    button.innerHTML = `<span class="number">${index + 1}</span><img alt="Interpretation ${index + 1}" src="${assetUrl(choice.png)}">`;
    button.addEventListener('pointerup', () => {
      if (session?.responseDevice === 'pointer') recordChoice(choice, index + 1, 'pointer');
    });
    choices.append(button);
  });
}

async function recordChoice(choice, responsePosition, responseInputMethod) {
  if (phase !== 'response' || responseInputMethod !== session?.responseDevice) return;
  phase = 'feedback';
  const responseMs = performance.now();
  const { trial, stimulus, startMethod } = currentAttempt;
  const correct = choice.choice_id === stimulus.target_choice_id;
  const decoySelected = choice.choice_id === stimulus.decoy_choice_id;
  const stageAudit = auditStage();
  trialRows.push({
    participant_id: session.participantId,
    session_id: manifest.session_id,
    preparation_id: session.preparationId,
    session_started_at: session.startedAt,
    session_seed: manifest.settings.seed,
    requested_session_seed: session.randomizationAudit.requestedSeed,
    effective_session_seed: session.randomizationAudit.effectiveSeed,
    eligible_transformation_count: session.randomizationAudit.eligibleTransformations,
    participant_eligible_history_count: session.randomizationAudit.participantPreviouslySeen,
    participant_all_history_count: session.randomizationAudit.participantPreviouslySeenAll,
    base_stimulus_slot_count: session.randomizationAudit.baseStimulusSlots,
    candidate_unique_transformation_count: session.randomizationAudit.candidateUniqueTransformations,
    candidate_signature_digest: session.randomizationAudit.candidateSignatureDigest,
    participant_history_repeat_slots: session.randomizationAudit.historicalRepeatSlots,
    historical_repeat_rate: session.randomizationAudit.historicalRepeatRate,
    within_candidate_duplicate_slots: session.randomizationAudit.withinCandidateDuplicateSlots,
    candidate_repeat_slots: session.randomizationAudit.repeatSlots,
    candidate_repeat_rate: session.randomizationAudit.repeatRate,
    randomization_threshold: session.randomizationAudit.threshold,
    maximum_repeat_slots: session.randomizationAudit.maximumRepeatSlots,
    randomization_accepted: session.randomizationAudit.accepted ? 1 : 0,
    randomization_rerandomizations: session.randomizationAudit.rerandomizations,
    schema_version: manifest.schema_version,
    catalog_version: manifest.catalog_version,
    task_name: manifest.task,
    source_split: manifest.settings.split,
    experiment_mode: manifest.settings.signalMode,
    signal_mode: manifest.settings.signalMode,
    comparison_design: manifest.comparison_design,
    stimuli_repeated_across_conditions: manifest.stimuli_repeated_across_conditions ? 1 : 0,
    base_stimulus_count: manifest.settings.baseStimulusCount,
    glyph_composition: manifest.settings.glyphComposition,
    trial_progression: manifest.settings.progression,
    feedback_enabled: manifest.settings.feedbackEnabled ? 1 : 0,
    total_presentation_count: manifest.total_presentation_count,
    glyph_quota_1: manifest.glyph_count_distribution?.['1'],
    glyph_quota_2: manifest.glyph_count_distribution?.['2'],
    glyph_quota_3: manifest.glyph_count_distribution?.['3'],
    condition_count_visual_silent: manifest.condition_distribution?.visual_silent || 0,
    condition_count_visual_background_audio: (
      manifest.condition_distribution?.visual_background_audio || 0
    ),
    condition_count_ir_audio: manifest.condition_distribution?.ir_audio || 0,
    condition_count_visual_aligned_overlay: (
      manifest.condition_distribution?.visual_aligned_overlay || 0
    ),
    condition_count_visual_aligned_ir_audio: (
      manifest.condition_distribution?.visual_aligned_ir_audio || 0
    ),
    mixed_condition_ratio: manifest.settings.mixedConditionRatio,
    condition_assignment_method: manifest.condition_assignment?.method,
    combinatorial_verification_version: manifest.combinatorial_verification?.version,
    combinatorial_verification_passed: manifest.combinatorial_verification?.verified ? 1 : 0,
    combinatorial_mapping_count: manifest.combinatorial_verification?.mapping_count,
    combinatorial_eligible_by_glyph_json: JSON.stringify(
      manifest.combinatorial_verification?.eligible_by_glyph_count ?? null,
    ),
    condition_by_glyph_count_json: JSON.stringify(
      manifest.combinatorial_verification?.condition_by_glyph_count ?? null,
    ),
    difficulty_model_version: manifest.difficulty_model_version,
    condition: trial.condition,
    pair_id: trial.pair_id,
    pair_position: trial.pair_position,
    pair_order: trial.pair_order,
    pair_pass: trial.pair_pass,
    pair_lag: trial.pair_lag,
    trial_index: trial.trial_index,
    stimulus_id: stimulus.stimulus_id,
    source_ids: stimulus.source_ids,
    target_ids: stimulus.target_ids,
    mapping_ids: stimulus.mapping_ids,
    changed_count: stimulus.changed_count,
    mapping_class: stimulus.mapping_class,
    choice_rule: stimulus.choice_rule,
    glyph_count: stimulus.source_ids.length,
    transformation_signature: stimulus.transformation_signature,
    mapping_repetition_index: stimulus.mapping_repetition_index,
    estimated_difficulty_score: stimulus.estimated_difficulty_score,
    difficulty_rank: stimulus.difficulty_rank,
    difficulty_stratum: stimulus.difficulty_stratum,
    difficulty_glyph_load: stimulus.difficulty_components?.glyph_load,
    difficulty_diagnostic_subtlety: stimulus.difficulty_components?.diagnostic_subtlety,
    difficulty_alternative_foil_similarity: stimulus.difficulty_components?.alternative_foil_similarity,
    difficulty_family_ambiguity: stimulus.difficulty_components?.family_ambiguity,
    difficulty_source_pixel_count: stimulus.difficulty_inputs?.source_pixel_count,
    difficulty_diagnostic_pixel_count: stimulus.difficulty_inputs?.diagnostic_pixel_count,
    difficulty_outcome_space_size: stimulus.difficulty_inputs?.outcome_space_size,
    difficulty_match_id: trial.difficulty_match_id ?? stimulus.difficulty_match_id,
    difficulty_match_position: (
      trial.difficulty_match_position ?? stimulus.difficulty_match_position
    ),
    difficulty_match_score_gap: resolveDifficultyMatchScoreGap(trial, stimulus),
    assigned_condition: stimulus.assigned_condition ?? trial.condition,
    aligned_displacement_audio_dx: trial.aligned_displacement_audio_dx,
    aligned_displacement_audio_dy: trial.aligned_displacement_audio_dy,
    aligned_displacement_audio_pixels: trial.aligned_displacement_audio_pixels,
    aligned_displacement_plate_pixels: trial.aligned_displacement_plate_pixels,
    aligned_target_pixel_count: stimulus.aligned_target_pixel_count,
    canonical_target_pixel_count: stimulus.canonical_target_pixel_count,
    canonical_visual_dot_count: stimulus.canonical_visual_dot_count,
    aligned_visual_base_dot_count: stimulus.aligned_visual_base_dot_count,
    aligned_visual_shifted_dot_count: stimulus.aligned_visual_shifted_dot_count,
    aligned_visual_overlap_dot_count: stimulus.aligned_visual_overlap_dot_count,
    alignment_equivalence_version: stimulus.alignment_equivalence_version,
    aligned_visual_palette_version: stimulus.aligned_visual_palette_version,
    visible_base_colours_json: JSON.stringify(
      stimulus.visible_base_colours ?? null,
    ),
    aligned_visual_base_colours_json: JSON.stringify(
      stimulus.aligned_visual_base_colours ?? null,
    ),
    aligned_visual_copy_colour_json: JSON.stringify(
      stimulus.aligned_visual_copy_colour ?? null,
    ),
    aligned_visual_carrier_version: stimulus.aligned_visual_carrier_version,
    aligned_visual_density_equivalence_version: (
      stimulus.aligned_visual_density_equivalence_version
    ),
    aligned_visual_pair_axis: stimulus.aligned_visual_pair_axis,
    aligned_visual_dot_pitch_pixels: stimulus.aligned_visual_dot_pitch_pixels,
    aligned_visual_pair_offset_pixels: stimulus.aligned_visual_pair_offset_pixels,
    aligned_visual_subdot_radii_json: JSON.stringify(
      stimulus.aligned_visual_subdot_radii ?? null,
    ),
    aligned_visual_carrier_dot_count: stimulus.aligned_visual_carrier_dot_count,
    aligned_visual_subdot_count: stimulus.aligned_visual_subdot_count,
    aligned_visual_carrier_radius_histogram_json: JSON.stringify(
      stimulus.aligned_visual_carrier_radius_histogram ?? null,
    ),
    aligned_visual_carrier_occupied_pixel_count: (
      stimulus.aligned_visual_carrier_occupied_pixel_count
    ),
    visible_signal_dot_count: stimulus.visible_signal_dot_count,
    balanced_visual_source_dot_count: stimulus.balanced_visual_source_dot_count,
    balanced_visual_source_radius_histogram_json: JSON.stringify(
      stimulus.balanced_visual_source_radius_histogram ?? null,
    ),
    balanced_visual_source_radius_area_units: (
      stimulus.balanced_visual_source_radius_area_units
    ),
    balanced_visual_source_active_pixel_count: (
      stimulus.balanced_visual_source_active_pixel_count
    ),
    aligned_visual_base_channel_position: (
      stimulus.aligned_visual_base_channel_position
    ),
    aligned_visual_shifted_channel_position: (
      stimulus.aligned_visual_shifted_channel_position
    ),
    aligned_visual_base_radius_histogram_json: JSON.stringify(
      stimulus.aligned_visual_base_radius_histogram ?? null,
    ),
    aligned_visual_shifted_radius_histogram_json: JSON.stringify(
      stimulus.aligned_visual_shifted_radius_histogram ?? null,
    ),
    aligned_visual_base_radius_area_units: (
      stimulus.aligned_visual_base_radius_area_units
    ),
    aligned_visual_shifted_radius_area_units: (
      stimulus.aligned_visual_shifted_radius_area_units
    ),
    aligned_visual_base_active_pixel_count: (
      stimulus.aligned_visual_base_active_pixel_count
    ),
    aligned_visual_shifted_active_pixel_count: (
      stimulus.aligned_visual_shifted_active_pixel_count
    ),
    balanced_carrier_occupancy_sha256: stimulus.balanced_carrier_occupancy_sha256,
    canonical_carrier_occupancy_sha256: stimulus.canonical_carrier_occupancy_sha256,
    aligned_carrier_occupancy_sha256: stimulus.aligned_carrier_occupancy_sha256,
    canonical_target_mask_sha256: stimulus.canonical_target_mask_sha256,
    aligned_target_mask_sha256: stimulus.aligned_target_mask_sha256,
    aligned_visual_base_mask_sha256: stimulus.aligned_visual_base_mask_sha256,
    aligned_visual_shifted_mask_sha256: stimulus.aligned_visual_shifted_mask_sha256,
    displayed_choice_order: currentAttempt.responseChoices.map(item => item.choice_id),
    displayed_choice_targets_json: JSON.stringify(
      currentAttempt.responseChoices.map(item => item.target_ids),
    ),
    target_choice_id: stimulus.target_choice_id,
    decoy_choice_id: stimulus.decoy_choice_id,
    response_choice_id: choice.choice_id,
    response_target_ids: choice.target_ids,
    response_position: responsePosition,
    correct: correct ? 1 : 0,
    decoy_selected: decoySelected ? 1 : 0,
    rt_choice_onset_ms: responseMs - responseOnsetMs,
    rt_stimulus_onset_ms: responseMs - stimulusOnsetMs,
    trial_start_method: startMethod,
    response_input_method: responseInputMethod,
    response_device: session.responseDevice,
    presentation_mode: session.presentation,
    display_id: session.displayId,
    display_width_cm: session.displayWidthCm,
    viewing_distance_cm: session.viewingDistanceCm,
    target_width_visual_angle_deg: session.targetAngleDeg,
    stimulus_duration_planned_ms: manifest.stimulus_duration_ms,
    stimulus_duration_actual_ms: currentAttempt.visualActualDurationMs,
    mask_duration_planned_ms: manifest.mask_duration_ms,
    audio_content: trial.audio_content,
    audio_sweeps_planned: trial.audio_wav ? manifest.sweep_repetitions : 0,
    audio_sweep_duration_ms: trial.audio_wav ? manifest.sweep_duration_ms : 0,
    inter_sweep_interval_ms: trial.audio_wav ? manifest.inter_sweep_interval_ms : 0,
    audio_visual_onset_offset_ms: Number.isFinite(audioPlannedOnsetMs)
      ? stimulusOnsetMs - audioPlannedOnsetMs
      : null,
    invalid_attempts_before_response: invalidAttempts,
    timestamp: new Date().toISOString(),
    ...stageAudit,
  });
  invalidAttempts = 0;
  stopAudio();
  choices.classList.add('hidden');
  if (session.feedbackEnabled) {
    feedback.textContent = correct ? 'Correct' : 'Incorrect';
    feedback.style.color = correct ? 'var(--good)' : 'var(--bad)';
    await wait(650);
  } else {
    await wait(180);
  }
  feedback.textContent = '';
  trialIndex += 1;
  if (trialIndex >= manifest.trials.length) await finishBlock();
  else showReady();
}

function scheduleRepeatedAudio(buffer) {
  audioContext ||= new AudioContext();
  const contextStart = audioContext.currentTime + 0.08;
  const stride = manifest.sweep_duration_ms / 1000
    + manifest.inter_sweep_interval_ms / 1000;
  activeAudioSources = [];
  for (let index = 0; index < manifest.sweep_repetitions; index += 1) {
    const source = audioContext.createBufferSource();
    source.buffer = buffer;
    source.connect(audioContext.destination);
    source.start(contextStart + index * stride);
    activeAudioSources.push(source);
  }
  return {
    contextStart,
    performanceOnsetMs: performance.now()
      + (contextStart - audioContext.currentTime) * 1000,
  };
}

function stopAudio() {
  for (const source of activeAudioSources) {
    try { source.stop(); } catch (_error) { /* already stopped */ }
  }
  activeAudioSources = [];
}

function invalidateAttempt(reason) {
  if (!['arming', 'stimulus', 'mask', 'response'].includes(phase)) return;
  if (phaseTimer) window.clearTimeout(phaseTimer);
  phaseTimer = null;
  stopAudio();
  invalidAttempts += 1;
  if (currentAttempt) currentAttempt.invalidReasons.push(reason);
  hide(stimulusImage, mask, choices);
  feedback.textContent = 'Interrupted attempt excluded; the same trial will restart.';
  window.setTimeout(() => {
    feedback.textContent = '';
    showReady();
  }, 550);
  phase = 'invalid';
}

async function finishBlock() {
  phase = 'finishing';
  stopAudio();
  trialScreen.classList.add('hidden');
  endScreen.classList.remove('hidden');
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  const totalCorrect = trialRows.reduce((total, row) => total + row.correct, 0);
  const conditions = [...new Set(trialRows.map(row => row.condition))];
  summary.innerHTML = `
    <p><strong>Overall accuracy:</strong> ${(100 * totalCorrect / trialRows.length).toFixed(1)}% (${totalCorrect}/${trialRows.length})</p>
    <table class="summary-table"><thead><tr><th>Condition</th><th>Accuracy</th><th>Decoy capture</th><th>Median choice RT</th></tr></thead><tbody>
      ${conditions.map(condition => summarizeCondition(condition)).join('')}
    </tbody></table>`;
  saveButton.disabled = true;
  newButton.disabled = true;
  exposureStatus.className = 'status';
  exposureStatus.textContent = 'Synchronizing participant exposure history…';
  let readyForNewBlock = false;
  try {
    const syncResult = await finalizeExposureHistory();
    updateExposureSyncStatus(syncResult);
    readyForNewBlock = syncResult.readyForNewBlock;
    saveStatus.className = 'status';
    saveStatus.textContent = 'CSV has not been saved yet.';
  } finally {
    if (phase === 'finishing') phase = 'finished';
    saveButton.disabled = false;
    endStateReadyForReset = readyForNewBlock && resultsSaved;
    newButton.disabled = !endStateReadyForReset;
  }
}

async function finalizeExposureHistory() {
  const failedExposureCount = await flushExposureHistory();
  let leaseReleaseError = null;
  if (!failedExposureCount && activeSessionLease) {
    try {
      await releaseActiveSessionLease();
    } catch (error) {
      leaseReleaseError = error;
    }
  }
  return {
    failedExposureCount,
    leaseReleaseError,
    readyForNewBlock: failedExposureCount === 0
      && leaseReleaseError === null
      && activeSessionLease === null,
  };
}

function updateExposureSyncStatus({ failedExposureCount, leaseReleaseError }) {
  if (failedExposureCount) {
    exposureStatus.className = 'status error';
    exposureStatus.textContent = `${failedExposureCount} exposure record${failedExposureCount === 1 ? '' : 's'} could not be synchronized. The participant session remains reserved; Save CSV retries this separately.`;
  } else if (leaseReleaseError) {
    exposureStatus.className = 'status error';
    exposureStatus.textContent = `Exposure history is up to date, but the participant-session reservation could not be closed. Save CSV retries it: ${String(leaseReleaseError.message || leaseReleaseError)}`;
  } else {
    exposureStatus.className = 'status good';
    exposureStatus.textContent = 'Participant exposure history is up to date and the session reservation is closed.';
  }
}

function summarizeCondition(condition) {
  const rows = trialRows.filter(row => row.condition === condition);
  const correct = rows.reduce((total, row) => total + row.correct, 0);
  const decoys = rows.reduce((total, row) => total + row.decoy_selected, 0);
  const correctRts = rows.filter(row => row.correct).map(row => row.rt_choice_onset_ms);
  return `<tr><td>${humanCondition(condition)}</td><td>${(100 * correct / rows.length).toFixed(1)}% (${correct}/${rows.length})</td><td>${(100 * decoys / rows.length).toFixed(1)}% (${decoys}/${rows.length})</td><td>${correctRts.length ? Math.round(median(correctRts)) + ' ms' : '—'}</td></tr>`;
}

async function saveResults() {
  if (phase !== 'finished' || !session || !manifest) return;
  const saveSession = session;
  const saveManifest = manifest;
  const rowsToSave = [...trialRows];
  phase = 'saving';
  saveButton.disabled = true;
  newButton.disabled = true;
  exposureStatus.className = 'status';
  exposureStatus.textContent = 'Retrying participant exposure history synchronization…';
  let readyForNewBlock = false;
  try {
    const syncResult = await finalizeExposureHistory();
    updateExposureSyncStatus(syncResult);
    readyForNewBlock = syncResult.readyForNewBlock;
    if (!resultsSaved) {
      const filename = `advanced_ishihara_${safeFilenamePart(saveSession.participantId)}_${saveSession.split}_${Date.now()}.csv`;
      savedResultInfo = await saveCsv({
        rows: rowsToSave,
        columns: CSV_COLUMNS,
        filename,
        statusElement: saveStatus,
        requestFields: {
          participantId: saveSession.participantId,
          sessionId: saveManifest.session_id,
          preparationId: saveSession.preparationId,
          candidateSignatureDigest: saveSession.randomizationAudit.candidateSignatureDigest,
        },
      });
      if (savedResultInfo?.saved !== true && savedResultInfo?.downloaded !== true) {
        throw new Error('The CSV was neither saved nor downloaded.');
      }
      resultsSaved = true;
    }
  } finally {
    if (phase === 'saving') phase = 'finished';
    endStateReadyForReset = readyForNewBlock && resultsSaved;
    saveButton.disabled = endStateReadyForReset;
    if (endStateReadyForReset) saveButton.textContent = 'CSV saved';
    else if (resultsSaved) saveButton.textContent = 'Retry history sync';
    else saveButton.textContent = 'Save CSV';
    newButton.disabled = !endStateReadyForReset;
  }
}

function resetApp() {
  if (phase !== 'finished' || !endStateReadyForReset || activeSessionLease) return;
  stopAudio();
  preparationGeneration += 1;
  activePreparationToken = null;
  preparedSettingsSnapshot = null;
  preparationId = null;
  manifest = null;
  manifestUrl = null;
  manifestBaseUrl = null;
  stimulusById = new Map();
  audioBuffers = new Map();
  clearRandomizationAudit();
  clearAbandonedSessionRecovery();
  prepareButton.disabled = false;
  startButton.disabled = true;
  session = null;
  trialRows = [];
  recordedExposureKeys = new Set();
  pendingExposureRequests = new Map();
  failedExposurePayloads = new Map();
  resultsSaved = false;
  savedResultInfo = null;
  endStateReadyForReset = false;
  phase = 'setup';
  setFreshRunCode();
  updateSetupPreview();
  preparedPanel.classList.add('hidden');
  prepareStatus.className = 'status';
  prepareStatus.textContent = 'No assets loaded.';
  saveStatus.className = 'status';
  saveStatus.textContent = '';
  saveButton.disabled = false;
  saveButton.textContent = 'Save CSV';
  newButton.disabled = false;
  exposureStatus.className = 'status';
  exposureStatus.textContent = '';
  endScreen.classList.add('hidden');
  trialScreen.classList.add('hidden');
  setupScreen.classList.remove('hidden');
}

function fitStage() {
  const availableWidth = Math.max(200, window.innerWidth - 28);
  const availableHeight = Math.max(120, window.innerHeight - 190);
  const presentation = session?.presentation || document.getElementById('presentation').value;
  let size;
  if (presentation === 'compact') {
    size = compactStageSize({
      availableWidthCssPx: availableWidth,
      availableHeightCssPx: availableHeight,
      nativeWidthPx: NATIVE_WIDTH,
      nativeHeightPx: NATIVE_HEIGHT,
      aspectRatio: ASPECT_RATIO,
    });
  } else if (presentation === 'calibrated' && session) {
    size = calibratedStageSize({
      targetWidthAngleDeg: session.targetAngleDeg,
      viewingDistanceCm: session.viewingDistanceCm,
      displayWidthCm: session.displayWidthCm,
      screenWidthCssPx: window.screen.width,
      availableWidthCssPx: availableWidth,
      availableHeightCssPx: availableHeight,
      aspectRatio: ASPECT_RATIO,
    });
    if (!size.fits) size = fitAspectRatio(availableWidth, availableHeight, ASPECT_RATIO);
  } else {
    size = fitAspectRatio(availableWidth, availableHeight, ASPECT_RATIO);
  }
  stageShell.style.width = `${Math.max(1, size.width)}px`;
  stageShell.style.height = `${Math.max(1, size.height)}px`;
}

function auditStage() {
  const rect = stageShell.getBoundingClientRect();
  let widthCm = null;
  let heightCm = null;
  let widthAngle = null;
  let heightAngle = null;
  if (session.displayWidthCm > 0 && session.viewingDistanceCm > 0) {
    const cmPerCssPixel = session.displayWidthCm / window.screen.width;
    widthCm = rect.width * cmPerCssPixel;
    heightCm = rect.height * cmPerCssPixel;
    widthAngle = visualAngleDeg(widthCm, session.viewingDistanceCm);
    heightAngle = visualAngleDeg(heightCm, session.viewingDistanceCm);
  }
  return {
    viewport_width_css_px: window.innerWidth,
    viewport_height_css_px: window.innerHeight,
    stage_width_css_px: rect.width,
    stage_height_css_px: rect.height,
    stage_aspect_ratio: rect.width / rect.height,
    stage_width_cm: widthCm,
    stage_height_cm: heightCm,
    stage_width_visual_angle_deg: widthAngle,
    stage_height_visual_angle_deg: heightAngle,
    css_px_per_audio_column: rect.width / manifest.audio_spatial_columns,
    css_px_per_audio_row: rect.height / manifest.audio_spatial_rows,
    coordinate_mapping: manifest.coordinate_mapping,
    fullscreen_at_onset: document.fullscreenElement ? 1 : 0,
    device_pixel_ratio: window.devicePixelRatio,
  };
}

function updateCalibrationVisibility() {
  const calibrationFields = document.getElementById('calibration-fields');
  const calibrated = document.getElementById('presentation').value === 'calibrated';
  calibrationFields.classList.toggle('hidden', !calibrated);
  calibrationFields.open = calibrated;
}

function showPrepareError(message) {
  prepareStatus.className = 'status error';
  prepareStatus.textContent = message;
  preparedPanel.classList.add('hidden');
}

function assetUrl(path) {
  return new URL(path, manifestBaseUrl).href;
}

function preloadImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = resolve;
    image.onerror = () => reject(new Error(`Image preload failed: ${url}`));
    image.src = url;
  });
}

function hide(...elements) {
  for (const element of elements) element.classList.add('hidden');
}

function nextFrame() {
  return new Promise(resolve => requestAnimationFrame(resolve));
}

function wait(milliseconds) {
  return new Promise(resolve => window.setTimeout(resolve, milliseconds));
}

function numberOrNull(id) {
  const value = Number(document.getElementById(id).value);
  return Number.isFinite(value) && value > 0 ? value : null;
}

function resolveDifficultyMatchScoreGap(trial, stimulus) {
  const explicitGap = trial.difficulty_match_score_gap
    ?? stimulus.difficulty_match_score_gap;
  if (explicitGap !== undefined && explicitGap !== null) return explicitGap;
  const matchId = trial.difficulty_match_id ?? stimulus.difficulty_match_id;
  if (!matchId) return null;
  const partner = manifest.stimuli.find(item => (
    item.difficulty_match_id === matchId
    && item.stimulus_id !== stimulus.stimulus_id
  ));
  if (!partner) return null;
  return Number(Math.abs(
    stimulus.estimated_difficulty_score - partner.estimated_difficulty_score
  ).toFixed(4));
}

function humanCondition(condition) {
  if (manifest?.settings?.signalMode === 'mixed-aligned') {
    return {
      'visual_background_audio': 'Complete identity visual + neutral carrier',
      'visual_aligned_overlay': 'Complete identity visual + shifted visual · neutral carrier',
      'visual_aligned_ir_audio': 'Complete identity visual + shifted identical IR',
      'ir_audio': 'Changed source scaffold + complementary IR',
    }[condition] || condition;
  }
  return {
    'visual_silent': 'Visual diagnostic (silent)',
    'visual_background_audio': 'Visual diagnostic + neutral carrier audio',
    'visual_aligned_overlay': 'Aligned visual + visual · neutral carrier',
    'visual_aligned_ir_audio': 'Visual diagnostic + aligned full-target IR',
    'ir_audio': 'Source scaffold + IR diagnostic audio',
  }[condition] || condition;
}

function humanSignalMode(signalMode) {
  return {
    mixed: 'mixed visual vs IR · carrier-controlled',
    'mixed-aligned': 'four-way mixed · visual and IR alignment',
    visual: 'visual baseline · silent',
    ir: 'IR only · audio diagnostic',
    paired: 'repeated same-puzzle pair · research',
  }[signalMode] || signalMode;
}

const CSV_COLUMNS = [
  'participant_id', 'session_id', 'preparation_id',
  'session_started_at', 'session_seed',
  'requested_session_seed', 'effective_session_seed',
  'eligible_transformation_count', 'participant_eligible_history_count',
  'participant_all_history_count', 'base_stimulus_slot_count',
  'candidate_unique_transformation_count', 'candidate_signature_digest',
  'participant_history_repeat_slots',
  'historical_repeat_rate', 'within_candidate_duplicate_slots',
  'candidate_repeat_slots', 'candidate_repeat_rate',
  'randomization_threshold', 'maximum_repeat_slots',
  'randomization_accepted', 'randomization_rerandomizations',
  'schema_version', 'catalog_version', 'task_name', 'source_split',
  'experiment_mode', 'signal_mode', 'comparison_design',
  'stimuli_repeated_across_conditions', 'base_stimulus_count', 'glyph_composition',
  'trial_progression', 'feedback_enabled', 'total_presentation_count',
  'glyph_quota_1', 'glyph_quota_2', 'glyph_quota_3',
  'condition_count_visual_silent', 'condition_count_visual_background_audio',
  'condition_count_visual_aligned_overlay',
  'condition_count_visual_aligned_ir_audio',
  'condition_count_ir_audio', 'mixed_condition_ratio', 'condition_assignment_method',
  'combinatorial_verification_version', 'combinatorial_verification_passed',
  'combinatorial_mapping_count', 'combinatorial_eligible_by_glyph_json',
  'condition_by_glyph_count_json',
  'difficulty_model_version',
  'condition', 'pair_id', 'pair_position', 'pair_order', 'pair_pass', 'pair_lag',
  'trial_index', 'stimulus_id', 'source_ids', 'target_ids', 'mapping_ids',
  'changed_count', 'mapping_class', 'choice_rule', 'glyph_count', 'transformation_signature',
  'mapping_repetition_index', 'estimated_difficulty_score',
  'difficulty_rank', 'difficulty_stratum',
  'difficulty_glyph_load', 'difficulty_diagnostic_subtlety',
  'difficulty_alternative_foil_similarity', 'difficulty_family_ambiguity',
  'difficulty_source_pixel_count', 'difficulty_diagnostic_pixel_count',
  'difficulty_outcome_space_size',
  'difficulty_match_id', 'difficulty_match_position',
  'difficulty_match_score_gap', 'assigned_condition',
  'aligned_displacement_audio_dx', 'aligned_displacement_audio_dy',
  'aligned_displacement_audio_pixels', 'aligned_displacement_plate_pixels',
  'aligned_target_pixel_count',
  'canonical_target_pixel_count', 'canonical_visual_dot_count',
  'aligned_visual_base_dot_count', 'aligned_visual_shifted_dot_count',
  'aligned_visual_overlap_dot_count', 'alignment_equivalence_version',
  'aligned_visual_palette_version', 'visible_base_colours_json',
  'aligned_visual_base_colours_json', 'aligned_visual_copy_colour_json',
  'aligned_visual_carrier_version', 'aligned_visual_density_equivalence_version',
  'aligned_visual_pair_axis', 'aligned_visual_dot_pitch_pixels',
  'aligned_visual_pair_offset_pixels',
  'aligned_visual_subdot_radii_json',
  'aligned_visual_carrier_dot_count', 'aligned_visual_subdot_count',
  'aligned_visual_carrier_radius_histogram_json',
  'aligned_visual_carrier_occupied_pixel_count',
  'visible_signal_dot_count', 'balanced_visual_source_dot_count',
  'balanced_visual_source_radius_histogram_json',
  'balanced_visual_source_radius_area_units',
  'balanced_visual_source_active_pixel_count',
  'aligned_visual_base_channel_position',
  'aligned_visual_shifted_channel_position',
  'aligned_visual_base_radius_histogram_json',
  'aligned_visual_shifted_radius_histogram_json',
  'aligned_visual_base_radius_area_units',
  'aligned_visual_shifted_radius_area_units',
  'aligned_visual_base_active_pixel_count',
  'aligned_visual_shifted_active_pixel_count',
  'balanced_carrier_occupancy_sha256', 'canonical_carrier_occupancy_sha256',
  'aligned_carrier_occupancy_sha256',
  'canonical_target_mask_sha256', 'aligned_target_mask_sha256',
  'aligned_visual_base_mask_sha256', 'aligned_visual_shifted_mask_sha256',
  'displayed_choice_order', 'displayed_choice_targets_json',
  'target_choice_id', 'decoy_choice_id',
  'response_choice_id', 'response_target_ids', 'response_position', 'correct',
  'decoy_selected', 'rt_choice_onset_ms', 'rt_stimulus_onset_ms',
  'trial_start_method', 'response_input_method', 'response_device',
  'presentation_mode', 'display_id',
  'display_width_cm', 'viewing_distance_cm', 'target_width_visual_angle_deg',
  'stimulus_duration_planned_ms', 'stimulus_duration_actual_ms',
  'mask_duration_planned_ms', 'audio_content', 'audio_sweeps_planned',
  'audio_sweep_duration_ms', 'inter_sweep_interval_ms',
  'audio_visual_onset_offset_ms', 'invalid_attempts_before_response',
  'viewport_width_css_px', 'viewport_height_css_px', 'stage_width_css_px',
  'stage_height_css_px', 'stage_aspect_ratio', 'stage_width_cm', 'stage_height_cm',
  'stage_width_visual_angle_deg', 'stage_height_visual_angle_deg',
  'css_px_per_audio_column', 'css_px_per_audio_row', 'coordinate_mapping',
  'fullscreen_at_onset', 'device_pixel_ratio', 'timestamp',
];

document.documentElement.dataset.advancedIshiharaVersion = 'advanced-10';
