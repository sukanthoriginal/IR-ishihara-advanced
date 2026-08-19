"""vOICe soundscape generation and validation shared by session builders."""

from __future__ import annotations

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
TARGET_RMS_INT16 = 1_000.0


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
    validate_wav(path)
    with wave.open(str(path), "rb") as wav_file:
        samples = np.frombuffer(wav_file.readframes(wav_file.getnframes()), dtype="<i2")
    values = samples.astype(np.float64)
    return float(np.sqrt(np.mean(values * values)))


def normalize_wav_rms(
    paths: list[Path] | tuple[Path, ...],
    target_rms: float = TARGET_RMS_INT16,
) -> dict[str, float]:
    """Give matched soundscapes the same global RMS without changing timing."""
    if not paths:
        return {}
    if not 0 < target_rms < 32_768:
        raise ValueError("target_rms must be between 0 and 32768")
    measured = {}
    for path in paths:
        validate_wav(path)
        with wave.open(str(path), "rb") as wav_file:
            params = wav_file.getparams()
            samples = np.frombuffer(
                wav_file.readframes(wav_file.getnframes()), dtype="<i2",
            ).astype(np.float64)
        source_rms = float(np.sqrt(np.mean(samples * samples)))
        if source_rms <= 0:
            raise RuntimeError(f"cannot RMS-normalize silent soundscape: {path}")
        scaled = np.rint(samples * (target_rms / source_rms))
        scaled = np.clip(scaled, -32_768, 32_767).astype("<i2")
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setparams(params)
            wav_file.writeframes(scaled.tobytes())
        validate_wav(path)
        final_rms = wav_rms_int16(path)
        if abs(final_rms - target_rms) > 1.0:
            raise RuntimeError(
                f"RMS normalization exceeded tolerance for {path}: "
                f"target={target_rms}, measured={final_rms}"
            )
        measured[path.name] = final_rms
    return measured


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
