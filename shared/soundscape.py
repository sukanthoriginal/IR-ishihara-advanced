"""vOICe soundscape generation and validation shared by session builders."""

from __future__ import annotations

import math
import os
import subprocess
import time
import wave
from pathlib import Path

import numpy as np

AUDIO_WIDTH = 178
AUDIO_HEIGHT = 64
SWEEP_DURATION_S = 1.05
SAMPLE_RATE_HZ = 48_000
CHANNELS = 2
BYTES_PER_SAMPLE = 2
SAMPLE_COUNT = round(SWEEP_DURATION_S * SAMPLE_RATE_HZ)
SAMPLES_PER_COLUMN = SAMPLE_COUNT // AUDIO_WIDTH
EXPECTED_WAV_FRAMES = SAMPLE_COUNT
RASPIVOICE_MAX_WAIT_S = 8.0
RASPIVOICE_POLL_INTERVAL_S = 0.02
CARRIER_TARGET_RMS_INT16 = 185.0
PEAK_CEILING_DBFS = -3.0
AUDIO_NORMALIZATION_METHOD = "carrier-referenced-shared-gain-v1"


def default_raspivoice_bin(repo_root: Path) -> Path:
    configured = os.environ.get("RASPIVOICE_BIN")
    if configured:
        return Path(configured).expanduser().resolve()
    candidates = (
        repo_root / "bin" / "raspivoice",
        repo_root.parent / "IR-vOICe" / "raspivoice" / "Release" / "raspivoice",
        Path.home() / "Dev" / "Lossfunk" / "IR-vOICe" / "raspivoice" / "Release" / "raspivoice",
    )
    return next((candidate for candidate in candidates if raspivoice_available(candidate)), candidates[0])


def raspivoice_available(binary: Path) -> bool:
    return binary.is_file() and os.access(binary, os.X_OK)


def ensure_aplay_shim(cache_root: Path) -> Path:
    shim_dir = cache_root / ".aplay_shim"
    shim_dir.mkdir(parents=True, exist_ok=True)
    shim = shim_dir / "aplay"
    if not shim.exists():
        shim.write_text("#!/bin/sh\nexit 0\n")
        shim.chmod(0o755)
    return shim_dir


def validate_wav(path: Path) -> dict[str, int]:
    if not path.is_file():
        raise RuntimeError(f"soundscape was not created: {path}")
    try:
        with wave.open(str(path), "rb") as wav_file:
            metadata = {
                "channels": wav_file.getnchannels(),
                "sample_width_bytes": wav_file.getsampwidth(),
                "sample_rate_hz": wav_file.getframerate(),
                "frame_count": wav_file.getnframes(),
            }
    except (EOFError, wave.Error) as error:
        raise RuntimeError(f"invalid WAV file: {path}") from error

    expected = {
        "channels": CHANNELS,
        "sample_width_bytes": BYTES_PER_SAMPLE,
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "frame_count": EXPECTED_WAV_FRAMES,
    }
    if metadata != expected:
        raise RuntimeError(
            f"unexpected soundscape metadata for {path}: "
            f"expected {expected}, received {metadata}"
        )
    return metadata


def wav_rms_int16(path: Path) -> float:
    _params, samples = _read_wav_samples(path)
    values = samples.astype(np.float64)
    return float(np.sqrt(np.mean(values * values)))


def wav_peak_int16(path: Path) -> int:
    _params, samples = _read_wav_samples(path)
    return int(np.max(np.abs(samples.astype(np.int32))))


def apply_carrier_referenced_gain(
    paths: list[Path] | tuple[Path, ...],
    carrier_reference_path: Path,
    carrier_target_rms: float = CARRIER_TARGET_RMS_INT16,
    peak_ceiling_dbfs: float = PEAK_CEILING_DBFS,
) -> dict:
    """Scale a carrier and its counterfactual probe with one linear gain.

    The carrier reference determines the gain for every path. This preserves
    the diagnostic-to-carrier contrast instead of independently equalising the
    total RMS of a sparse probe and a background-only carrier.
    """
    if not paths:
        raise ValueError("at least one soundscape is required")
    paths = tuple(Path(path) for path in paths)
    carrier_reference_path = Path(carrier_reference_path)
    if len(set(paths)) != len(paths):
        raise ValueError("soundscape paths must be unique")
    if carrier_reference_path not in paths:
        raise ValueError("carrier reference must be included in soundscape paths")
    if not 0 < carrier_target_rms < 32_768:
        raise ValueError("carrier_target_rms must be between 0 and 32768")
    if not peak_ceiling_dbfs < 0:
        raise ValueError("peak_ceiling_dbfs must be below 0 dBFS")

    peak_ceiling_int16 = int(math.floor(
        32_767 * (10 ** (peak_ceiling_dbfs / 20)),
    ))
    if carrier_target_rms >= peak_ceiling_int16:
        raise ValueError("carrier target must be below the peak ceiling")

    raw = {}
    for path in paths:
        params, integer_samples = _read_wav_samples(path)
        samples = integer_samples.astype(np.float64)
        source_rms = float(np.sqrt(np.mean(samples * samples)))
        if source_rms <= 0:
            raise RuntimeError(f"cannot normalize silent soundscape: {path}")
        raw[path] = {
            "params": params,
            "samples": samples,
            "rms": source_rms,
            "peak": int(np.max(np.abs(integer_samples.astype(np.int32)))),
        }

    raw_carrier_rms = raw[carrier_reference_path]["rms"]
    requested_gain = carrier_target_rms / raw_carrier_rms
    maximum_raw_peak = max(item["peak"] for item in raw.values())
    peak_safe_gain = (
        (peak_ceiling_int16 - 0.5) / maximum_raw_peak
        if maximum_raw_peak else requested_gain
    )
    shared_gain = min(requested_gain, peak_safe_gain)
    peak_limited = shared_gain < requested_gain
    prepared = {}
    for path in paths:
        scaled = np.rint(raw[path]["samples"] * shared_gain)
        scaled_peak = int(np.max(np.abs(scaled)))
        if scaled_peak > peak_ceiling_int16:
            raise RuntimeError(
                f"carrier-referenced gain would exceed {peak_ceiling_dbfs:g} dBFS "
                f"for {path}: peak={scaled_peak}, ceiling={peak_ceiling_int16}"
            )
        prepared[path] = scaled.astype("<i2")

    measured = {}
    for path in paths:
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setparams(raw[path]["params"])
            wav_file.writeframes(prepared[path].tobytes())
        validate_wav(path)
        final_rms = wav_rms_int16(path)
        final_peak = wav_peak_int16(path)
        if final_peak > peak_ceiling_int16:
            raise RuntimeError(f"normalized soundscape exceeds peak ceiling: {path}")
        measured[path.name] = {
            "raw_rms_int16": raw[path]["rms"],
            "final_rms_int16": final_rms,
            "raw_peak_int16": raw[path]["peak"],
            "final_peak_int16": final_peak,
        }

    final_carrier_rms = measured[carrier_reference_path.name]["final_rms_int16"]
    if not peak_limited and abs(final_carrier_rms - carrier_target_rms) > 1.0:
        raise RuntimeError(
            "carrier normalization exceeded tolerance: "
            f"target={carrier_target_rms}, measured={final_carrier_rms}"
        )
    if final_carrier_rms > carrier_target_rms + 1.0:
        raise RuntimeError("carrier normalization exceeded its target")
    return {
        "method": AUDIO_NORMALIZATION_METHOD,
        "carrier_target_rms_int16": carrier_target_rms,
        "raw_carrier_rms_int16": raw_carrier_rms,
        "requested_gain_linear": requested_gain,
        "shared_gain_linear": shared_gain,
        "shared_gain_db": 20 * math.log10(shared_gain),
        "peak_limited": peak_limited,
        "peak_ceiling_dbfs": peak_ceiling_dbfs,
        "peak_ceiling_int16": peak_ceiling_int16,
        "files": measured,
    }


def _read_wav_samples(path: Path) -> tuple[object, np.ndarray]:
    validate_wav(path)
    with wave.open(str(path), "rb") as wav_file:
        params = wav_file.getparams()
        samples = np.frombuffer(
            wav_file.readframes(wav_file.getnframes()), dtype="<i2",
        ).copy()
    return params, samples


def generate_soundscape(
    png_path: Path,
    wav_path: Path,
    raspivoice_bin: Path,
    cache_root: Path,
    retries: int = 3,
) -> None:
    """Generate one validated stereo WAV without playing it during creation."""
    if wav_path.exists():
        try:
            validate_wav(wav_path)
            return
        except RuntimeError:
            wav_path.unlink()

    if not raspivoice_available(raspivoice_bin):
        raise RuntimeError(
            "raspivoice is required for multimodal sessions. Set RASPIVOICE_BIN "
            f"to an executable binary; checked {raspivoice_bin}"
        )

    shim_dir = ensure_aplay_shim(cache_root)
    last_error: RuntimeError | None = None
    for _attempt in range(retries):
        try:
            _generate_once(png_path, wav_path, raspivoice_bin, shim_dir)
            validate_wav(wav_path)
            return
        except RuntimeError as error:
            last_error = error
    raise last_error or RuntimeError(f"failed to create {wav_path}")


def _generate_once(
    png_path: Path,
    wav_path: Path,
    raspivoice_bin: Path,
    shim_dir: Path,
) -> None:
    wav_path.unlink(missing_ok=True)
    environment = os.environ.copy()
    environment["PATH"] = f"{shim_dir}:{environment.get('PATH', '')}"
    process = subprocess.Popen(
        [
            str(raspivoice_bin),
            "-s0",
            "-i", str(png_path),
            "-o", str(wav_path),
            "-r", str(AUDIO_HEIGHT),
            "-c", str(AUDIO_WIDTH),
            "-t", str(SWEEP_DURATION_S),
            "-Z", str(SAMPLE_RATE_HZ),
            "--no_record",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
    )

    deadline = time.monotonic() + RASPIVOICE_MAX_WAIT_S
    while time.monotonic() < deadline:
        if wav_path.exists() and wav_path.stat().st_size > 44:
            break
        time.sleep(RASPIVOICE_POLL_INTERVAL_S)

    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()

    if not wav_path.exists():
        raise RuntimeError(f"raspivoice did not create {wav_path.name}")
