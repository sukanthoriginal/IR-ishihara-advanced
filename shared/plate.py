"""Geometry masks, dot plates, and response images for Advanced Ishihara."""

from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw

from shared.soundscape import AUDIO_HEIGHT, AUDIO_WIDTH

PLATE_SCALE = 4
PLATE_WIDTH = AUDIO_WIDTH * PLATE_SCALE
PLATE_HEIGHT = AUDIO_HEIGHT * PLATE_SCALE
DOT_STEP = 16

GEOMETRY_SEGMENTS: dict[str, tuple[str, ...]] = {
    "one": ("right-upper", "right-lower"),
    "gamma": ("top", "left-upper", "left-lower"),
    "l": ("left-upper", "left-lower", "bottom"),
    "t": ("top", "center"),
    "v": ("vee-left", "vee-right"),
    "caret": ("caret-left", "caret-right"),
    "three": ("top", "middle", "bottom", "right-upper", "right-lower"),
    "four": ("middle", "left-upper", "right-upper", "right-lower"),
    "seven": ("top", "right-upper", "right-lower"),
    "nine": (
        "top", "middle", "bottom", "left-upper", "right-upper", "right-lower",
    ),
    "zero-o": (
        "top", "bottom", "left-upper", "left-lower", "right-upper", "right-lower",
    ),
    "c": ("top", "bottom", "left-upper", "left-lower"),
    "e": ("top", "middle", "bottom", "left-upper", "left-lower"),
    "f": ("top", "middle", "left-upper", "left-lower"),
    "h": ("middle", "left-upper", "left-lower", "right-upper", "right-lower"),
    "j": ("bottom", "right-upper", "right-lower"),
    "p": ("top", "middle", "left-upper", "left-lower", "right-upper"),
    "u": ("bottom", "left-upper", "left-lower", "right-upper", "right-lower"),
    "six": (
        "top", "middle", "bottom", "left-upper", "left-lower", "right-lower",
    ),
    "i": ("top", "center", "bottom"),
    "x": ("vee-left", "vee-right", "x-lower-left", "x-lower-right"),
    "y": ("vee-left", "vee-right", "stem"),
    "a": ("caret-left", "caret-right", "crossbar"),
    "eight-b": (
        "top", "middle", "bottom", "left-upper", "left-lower",
        "right-upper", "right-lower",
    ),
    "q": (
        "top", "bottom", "left-upper", "left-lower", "right-upper",
        "right-lower", "tail",
    ),
    "g": (
        "top", "bottom", "left-upper", "left-lower", "half-middle", "right-lower",
    ),
    "r": (
        "top", "middle", "left-upper", "left-lower", "right-upper", "right-leg",
    ),
}

DISPLAY_LABELS = {
    "zero-o": "O",
    "eight-b": "8",
    "caret": "∧",
    "gamma": "Γ",
    "one": "1",
    "three": "3",
    "four": "4",
    "seven": "7",
    "nine": "9",
    "six": "6",
    **{identifier: identifier.upper() for identifier in (
        "l", "t", "v", "c", "e", "f", "h", "j", "p", "u",
        "i", "x", "y", "a", "q", "g", "r",
    )},
}

SOURCE_COLOURS = (
    (220, 62, 68),
    (66, 188, 108),
    (65, 125, 224),
)
VISIBLE_PROBE_COLOUR = (238, 198, 63)
BACKGROUND_COLOUR = (54, 56, 58)


def segment_closure_relations() -> set[tuple[str, str]]:
    """Return every strict subset relation induced by the drawn segments."""
    relations = set()
    for source_id, target_id in itertools.permutations(GEOMETRY_SEGMENTS, 2):
        source = set(GEOMETRY_SEGMENTS[source_id])
        target = set(GEOMETRY_SEGMENTS[target_id])
        if source < target:
            relations.add((source_id, target_id))
    return relations


def symbol_boxes(symbol_count: int) -> tuple[tuple[float, float, float, float], ...]:
    if symbol_count == 1:
        return ((0.35, 0.08, 0.65, 0.92),)
    if symbol_count == 2:
        return ((0.22, 0.08, 0.46, 0.92), (0.54, 0.08, 0.78, 0.92))
    if symbol_count == 3:
        return (
            (0.13, 0.08, 0.35, 0.92),
            (0.39, 0.08, 0.61, 0.92),
            (0.65, 0.08, 0.87, 0.92),
        )
    raise ValueError(f"unsupported symbol count: {symbol_count}")


def draw_geometry_mask(geometry_ids: tuple[str, ...] | list[str], width: int = 6) -> Image.Image:
    mask = Image.new("L", (AUDIO_WIDTH, AUDIO_HEIGHT), 0)
    draw = ImageDraw.Draw(mask)
    for geometry_id, box in zip(geometry_ids, symbol_boxes(len(geometry_ids))):
        draw_symbol(draw, geometry_id, box, width)
    return mask


def draw_position_masks(geometry_ids: tuple[str, ...] | list[str]) -> list[Image.Image]:
    masks = []
    boxes = symbol_boxes(len(geometry_ids))
    for geometry_id, box in zip(geometry_ids, boxes):
        mask = Image.new("L", (AUDIO_WIDTH, AUDIO_HEIGHT), 0)
        draw_symbol(ImageDraw.Draw(mask), geometry_id, box, 6)
        masks.append(mask)
    return masks


def draw_symbol(
    draw: ImageDraw.ImageDraw,
    geometry_id: str,
    box: tuple[float, float, float, float],
    width: int,
) -> None:
    x0, y0, x1, y1 = box

    def point(x: float, y: float) -> tuple[int, int]:
        return (
            round((x0 + x * (x1 - x0)) * (AUDIO_WIDTH - 1)),
            round((y0 + y * (y1 - y0)) * (AUDIO_HEIGHT - 1)),
        )

    segments = {
        "top": (point(0.12, 0.08), point(0.88, 0.08)),
        "middle": (point(0.12, 0.50), point(0.88, 0.50)),
        "half-middle": (point(0.50, 0.50), point(0.88, 0.50)),
        "bottom": (point(0.12, 0.92), point(0.88, 0.92)),
        "left-upper": (point(0.12, 0.08), point(0.12, 0.50)),
        "left-lower": (point(0.12, 0.50), point(0.12, 0.92)),
        "right-upper": (point(0.88, 0.08), point(0.88, 0.50)),
        "right-lower": (point(0.88, 0.50), point(0.88, 0.92)),
        "center": (point(0.50, 0.08), point(0.50, 0.92)),
        "right-leg": (point(0.50, 0.50), point(0.90, 0.92)),
        "tail": (point(0.58, 0.67), point(1.08, 1.08)),
        "vee-left": (point(0.10, 0.08), point(0.50, 0.52)),
        "vee-right": (point(0.90, 0.08), point(0.50, 0.52)),
        "x-lower-left": (point(0.50, 0.52), point(0.10, 0.96)),
        "x-lower-right": (point(0.50, 0.52), point(0.90, 0.96)),
        "stem": (point(0.50, 0.52), point(0.50, 0.96)),
        "caret-left": (point(0.10, 0.92), point(0.50, 0.08)),
        "caret-right": (point(0.90, 0.92), point(0.50, 0.08)),
        "crossbar": (point(0.29, 0.58), point(0.71, 0.58)),
    }
    try:
        geometry_segments = GEOMETRY_SEGMENTS[geometry_id]
    except KeyError as error:
        raise ValueError(f"unknown geometry: {geometry_id}") from error
    for segment in geometry_segments:
        draw.line(segments[segment], fill=255, width=width, joint="curve")


def difference_mask(source_ids: list[str], target_ids: list[str]) -> tuple[Image.Image, Image.Image, Image.Image]:
    source = draw_geometry_mask(source_ids)
    target = draw_geometry_mask(target_ids)
    if ImageChops.subtract(source, target).getbbox() is not None:
        raise ValueError(f"source is not contained in target: {source_ids} -> {target_ids}")
    diagnostic = ImageChops.subtract(target, source)
    return source, diagnostic, target


def make_dot_layout(rng: np.random.Generator) -> list[tuple[int, int, int]]:
    dots = []
    for base_y in range(DOT_STEP // 2, PLATE_HEIGHT, DOT_STEP):
        for base_x in range(DOT_STEP // 2, PLATE_WIDTH, DOT_STEP):
            x = int(np.clip(base_x + rng.integers(-5, 6), 4, PLATE_WIDTH - 5))
            y = int(np.clip(base_y + rng.integers(-5, 6), 4, PLATE_HEIGHT - 5))
            radius = int(rng.integers(5, 10))
            dots.append((x, y, radius))
    rng.shuffle(dots)
    return dots


def render_trial_images(
    source_ids: list[str],
    target_ids: list[str],
    choice_targets: list[list[str]],
    output_dir: Path,
    stem: str,
    seed: int,
) -> dict:
    rng = np.random.default_rng(seed)
    source_mask, diagnostic_mask, _target_mask = difference_mask(source_ids, target_ids)
    source_position_masks = draw_position_masks(source_ids)
    dots = make_dot_layout(rng)
    plate_colour_seed = int(rng.integers(0, 2**32, dtype=np.uint32))

    ir_plate = _draw_plate(
        dots, source_position_masks, diagnostic_mask,
        np.random.default_rng(plate_colour_seed), False,
    )
    visual_plate = _draw_plate(
        dots, source_position_masks, diagnostic_mask,
        np.random.default_rng(plate_colour_seed), True,
    )
    ir_input, background_input = _make_audio_inputs(diagnostic_mask, rng)

    plate_dir = output_dir / "plates"
    audio_input_dir = output_dir / "audio_inputs"
    choice_dir = output_dir / "choices"
    plate_dir.mkdir(parents=True, exist_ok=True)
    audio_input_dir.mkdir(parents=True, exist_ok=True)
    choice_dir.mkdir(parents=True, exist_ok=True)

    ir_plate_path = plate_dir / f"{stem}_ir.png"
    visual_plate_path = plate_dir / f"{stem}_visual.png"
    ir_input_path = audio_input_dir / f"{stem}_probe.png"
    background_input_path = audio_input_dir / f"{stem}_background.png"
    ir_plate.save(ir_plate_path)
    visual_plate.save(visual_plate_path)
    ir_input.save(ir_input_path)
    background_input.save(background_input_path)

    choices = []
    for index, geometry_ids in enumerate(choice_targets):
        choice_path = choice_dir / f"{stem}_choice_{index + 1}.png"
        render_choice(geometry_ids).save(choice_path)
        choices.append({
            "choice_id": f"{stem}-choice-{index + 1}",
            "target_ids": geometry_ids,
            "label": "".join(DISPLAY_LABELS[item] for item in geometry_ids),
            "png": str(choice_path.relative_to(output_dir)),
        })

    source_values = np.asarray(source_mask)
    diagnostic_values = np.asarray(diagnostic_mask)
    return {
        "ir_plate_png": str(ir_plate_path.relative_to(output_dir)),
        "visual_plate_png": str(visual_plate_path.relative_to(output_dir)),
        "ir_input_png": str(ir_input_path.relative_to(output_dir)),
        "background_input_png": str(background_input_path.relative_to(output_dir)),
        "choices": choices,
        "source_pixel_count": int(np.count_nonzero(source_values)),
        "diagnostic_pixel_count": int(np.count_nonzero(diagnostic_values)),
    }


def _draw_plate(
    dots: list[tuple[int, int, int]],
    source_position_masks: list[Image.Image],
    diagnostic_mask: Image.Image,
    rng: np.random.Generator,
    reveal_probe: bool,
) -> Image.Image:
    plate = Image.new("RGB", (PLATE_WIDTH, PLATE_HEIGHT), (31, 32, 33))
    draw = ImageDraw.Draw(plate)
    source_arrays = [np.asarray(mask) > 0 for mask in source_position_masks]
    diagnostic = np.asarray(diagnostic_mask) > 0

    for x, y, radius in dots:
        audio_x = min(AUDIO_WIDTH - 1, x // PLATE_SCALE)
        audio_y = min(AUDIO_HEIGHT - 1, y // PLATE_SCALE)
        background_colour = _vary_colour(BACKGROUND_COLOUR, rng)
        source_colours = [
            _vary_colour(base_colour, rng) for base_colour in SOURCE_COLOURS
        ]
        diagnostic_colour = _vary_colour(VISIBLE_PROBE_COLOUR, rng)
        colour = background_colour
        for position, source in enumerate(source_arrays):
            if source[audio_y, audio_x]:
                colour = source_colours[position]
                break
        if diagnostic[audio_y, audio_x] and reveal_probe:
            colour = diagnostic_colour
        draw.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill=colour,
        )
    return plate


def _make_audio_inputs(
    diagnostic_mask: Image.Image,
    rng: np.random.Generator,
) -> tuple[Image.Image, Image.Image]:
    background = rng.integers(4, 28, size=(AUDIO_HEIGHT, AUDIO_WIDTH), dtype=np.uint8)
    probe = background.copy()
    diagnostic = np.asarray(diagnostic_mask) > 0
    probe[diagnostic] = rng.integers(210, 256, size=int(diagnostic.sum()), dtype=np.uint8)
    return Image.fromarray(probe, mode="L"), Image.fromarray(background, mode="L")


def render_choice(geometry_ids: list[str]) -> Image.Image:
    mask = draw_geometry_mask(geometry_ids, width=5)
    image = Image.new("RGB", (AUDIO_WIDTH * 2, AUDIO_HEIGHT * 2), (8, 8, 9))
    enlarged = mask.resize(image.size, Image.Resampling.NEAREST)
    ink = Image.new("RGB", image.size, (245, 245, 245))
    image.paste(ink, mask=enlarged)
    return image


def _vary_colour(
    base: tuple[int, int, int],
    rng: np.random.Generator,
) -> tuple[int, int, int]:
    delta = int(rng.integers(-12, 13))
    return tuple(int(np.clip(channel + delta, 0, 255)) for channel in base)
