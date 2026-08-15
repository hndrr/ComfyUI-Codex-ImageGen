from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image


MAX_VIDEO_REFERENCE_FRAMES = 4


@dataclass(frozen=True)
class PreparedMedia:
    image_inputs: list[object]
    prompt_notes: tuple[str, ...]


def prepare_media_references(
    image_inputs: list[object | None],
    *,
    audio=None,
    video=None,
) -> PreparedMedia:
    references = [value for value in image_inputs if value is not None]
    notes: list[str] = []

    if video is not None:
        video_frames, selected_count, total_count = sample_video_reference_frames(video)
        references.append(video_frames)
        notes.append(
            f"The video reference is represented by {selected_count} chronological frames "
            f"sampled from {total_count} total frames."
        )

    if audio is not None:
        spectrogram, duration, sample_rate = audio_to_spectrogram_reference(audio)
        references.append(spectrogram)
        notes.append(
            "The audio reference is represented by an attached spectrogram "
            f"({duration:.2f} seconds at {sample_rate} Hz). Use it only for visible rhythm, "
            "frequency, and intensity cues; speech and musical meaning are not available natively."
        )

    return PreparedMedia(image_inputs=references, prompt_notes=tuple(notes))


def append_media_notes(prompt: str, notes: tuple[str, ...]) -> str:
    if not notes:
        return prompt
    formatted = "\n".join(f"- {note}" for note in notes)
    return f"{prompt.rstrip()}\n\nReference media notes:\n{formatted}"


def sample_video_reference_frames(video, max_frames: int = MAX_VIDEO_REFERENCE_FRAMES):
    if max_frames < 1:
        raise ValueError("max_frames must be at least 1.")
    if not hasattr(video, "get_components"):
        raise ValueError("video must be a ComfyUI VIDEO input.")

    components = video.get_components()
    images = components.images
    total_count = int(images.shape[0])
    if total_count < 1:
        raise ValueError("The video input does not contain any frames.")

    selected_count = min(max_frames, total_count)
    indices = np.linspace(0, total_count - 1, selected_count).round().astype(int).tolist()
    indices = list(dict.fromkeys(indices))
    return images[indices], len(indices), total_count


def audio_to_spectrogram_reference(audio, *, width: int = 512, height: int = 256):
    if not isinstance(audio, dict) or "waveform" not in audio or "sample_rate" not in audio:
        raise ValueError("audio must be a ComfyUI AUDIO input with waveform and sample_rate.")

    sample_rate = int(audio["sample_rate"])
    if sample_rate <= 0:
        raise ValueError("audio sample_rate must be greater than zero.")

    waveform = _to_numpy(audio["waveform"]).astype(np.float32)
    if waveform.ndim == 0:
        raise ValueError("audio waveform is empty.")
    while waveform.ndim > 1:
        waveform = waveform.mean(axis=0)
    if waveform.size == 0:
        raise ValueError("audio waveform is empty.")

    duration = float(waveform.size / sample_rate)
    waveform = _downsample_for_analysis(waveform, 262_144)
    window_size = min(2048, max(64, _largest_power_of_two(waveform.size)))
    if waveform.size < window_size:
        waveform = np.pad(waveform, (0, window_size - waveform.size))

    frame_count = min(width, max(1, waveform.size // max(1, window_size // 4)))
    starts = np.linspace(0, max(0, waveform.size - window_size), frame_count).astype(int)
    window = np.hanning(window_size).astype(np.float32)
    spectrum = np.stack(
        [np.log1p(np.abs(np.fft.rfft(waveform[start : start + window_size] * window))) for start in starts],
        axis=1,
    )
    spectrum = np.flipud(spectrum)
    spectrum = _normalize_image(spectrum)
    grayscale = (spectrum * 255.0).round().astype(np.uint8)
    image = Image.fromarray(grayscale, mode="L").resize((width, height), Image.Resampling.BILINEAR).convert("RGB")
    array = np.asarray(image).astype(np.float32) / 255.0
    return np.expand_dims(array, axis=0), duration, sample_rate


def _to_numpy(value) -> np.ndarray:
    if hasattr(value, "detach"):
        return value.detach().cpu().numpy()
    return np.asarray(value)


def _downsample_for_analysis(waveform: np.ndarray, max_samples: int) -> np.ndarray:
    if waveform.size <= max_samples:
        return waveform
    positions = np.linspace(0, waveform.size - 1, max_samples)
    return np.interp(positions, np.arange(waveform.size), waveform).astype(np.float32)


def _largest_power_of_two(value: int) -> int:
    if value < 1:
        return 1
    return 1 << (value.bit_length() - 1)


def _normalize_image(array: np.ndarray) -> np.ndarray:
    low = float(np.percentile(array, 1))
    high = float(np.percentile(array, 99))
    if high <= low:
        return np.zeros_like(array, dtype=np.float32)
    return np.clip((array - low) / (high - low), 0.0, 1.0).astype(np.float32)
