"""Geometry masks, dot plates, and response images for Advanced Ishihara."""

from __future__ import annotations

import hashlib
import itertools
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw

from shared.soundscape import AUDIO_HEIGHT, AUDIO_WIDTH

PLATE_SCALE = 4
PLATE_WIDTH = AUDIO_WIDTH * PLATE_SCALE
PLATE_HEIGHT = AUDIO_HEIGHT * PLATE_SCALE
DOT_STEP = 16
ALIGNED_VISUAL_DOT_STEP = 12
ALIGNED_DISPLACEMENT_AUDIO_PIXELS = (
    ALIGNED_VISUAL_DOT_STEP // PLATE_SCALE
)

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
ALIGNED_VISUAL_COLOURS = (
    (70, 205, 220),
    (216, 102, 226),
    (244, 156, 66),
)
BACKGROUND_COLOUR = (54, 56, 58)
CANONICAL_TARGET_COLOUR = (220, 62, 68)
ALIGNED_VISUAL_COPY_COLOUR = VISIBLE_PROBE_COLOUR
ALIGNED_VISUAL_CARRIER_VERSION = "fine-bijective-diagonal-dyads-v3"
ALIGNED_VISUAL_DENSITY_EQUIVALENCE_VERSION = "exact-fine-grid-token-area-v2"
ALIGNED_VISUAL_PALETTE_VERSION = "source-position-rgb-yellow-copy-v1"
ALIGNED_VISUAL_PAIR_AXIS = "seeded-diagonal"
ALIGNED_VISUAL_PAIR_OFFSET_PIXELS = 2
ALIGNED_VISUAL_SUBDOT_RADII = (2, 3)
ALIGNED_VISUAL_GRID_COLUMNS = len(range(
    ALIGNED_VISUAL_DOT_STEP // 2,
    PLATE_WIDTH,
    ALIGNED_VISUAL_DOT_STEP,
))
ALIGNED_VISUAL_GRID_ROWS = len(range(
    ALIGNED_VISUAL_DOT_STEP // 2,
    PLATE_HEIGHT,
    ALIGNED_VISUAL_DOT_STEP,
))
ALIGNED_VISUAL_CARRIER_DOT_COUNT = (
    ALIGNED_VISUAL_GRID_COLUMNS * ALIGNED_VISUAL_GRID_ROWS
)
_ALIGNED_PHASE_ZERO_ROWS = (ALIGNED_VISUAL_GRID_ROWS + 1) // 2
_ALIGNED_PHASE_ONE_ROWS = ALIGNED_VISUAL_GRID_ROWS // 2
_ALIGNED_SMALL_PER_PHASE_ZERO_ROW = (ALIGNED_VISUAL_GRID_COLUMNS + 1) // 2
_ALIGNED_SMALL_PER_PHASE_ONE_ROW = ALIGNED_VISUAL_GRID_COLUMNS // 2
_ALIGNED_SMALL_RADIUS_COUNT = (
    _ALIGNED_PHASE_ZERO_ROWS * _ALIGNED_SMALL_PER_PHASE_ZERO_ROW
    + _ALIGNED_PHASE_ONE_ROWS * _ALIGNED_SMALL_PER_PHASE_ONE_ROW
)
ALIGNED_VISUAL_CARRIER_RADIUS_HISTOGRAM = {
    str(ALIGNED_VISUAL_SUBDOT_RADII[0]): _ALIGNED_SMALL_RADIUS_COUNT,
    str(ALIGNED_VISUAL_SUBDOT_RADII[1]): (
        ALIGNED_VISUAL_CARRIER_DOT_COUNT - _ALIGNED_SMALL_RADIUS_COUNT
    ),
}
ALIGNED_VISUAL_CARRIER_OCCUPIED_PIXEL_COUNT = 71_816
PLATE_BACKGROUND_COLOUR = (31, 32, 33)


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


def translate_mask_without_clipping(
    mask: Image.Image,
    dx: int,
    dy: int,
) -> Image.Image:
    """Translate a mask without wraparound and reject cropped geometry."""
    translated = Image.new("L", mask.size, 0)
    translated.paste(mask, (dx, dy))
    original_count = int(np.count_nonzero(np.asarray(mask)))
    translated_count = int(np.count_nonzero(np.asarray(translated)))
    if translated_count != original_count:
        raise ValueError(
            f"aligned displacement clips geometry: dx={dx}, dy={dy}, "
            f"before={original_count}, after={translated_count}"
        )
    return translated


def mask_digest(mask: Image.Image) -> str:
    """Return a stable digest of binary geometry membership."""
    binary = np.packbits(np.asarray(mask) > 0)
    return hashlib.sha256(binary.tobytes()).hexdigest()


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


def make_aligned_dot_layout() -> list[tuple[int, int, int]]:
    """Return the exact fine-grid samples used only by aligned mixed plates."""
    first = ALIGNED_VISUAL_DOT_STEP // 2
    return [
        (x, y, max(ALIGNED_VISUAL_SUBDOT_RADII))
        for y in range(first, PLATE_HEIGHT, ALIGNED_VISUAL_DOT_STEP)
        for x in range(first, PLATE_WIDTH, ALIGNED_VISUAL_DOT_STEP)
    ]


def render_trial_images(
    source_ids: list[str],
    target_ids: list[str],
    choice_targets: list[list[str]],
    output_dir: Path,
    stem: str,
    seed: int,
    *,
    include_aligned_assets: bool = False,
    include_balanced_carrier_assets: bool = False,
    include_visual_complementary_asset: bool = False,
) -> dict:
    rng = np.random.default_rng(seed)
    source_mask, diagnostic_mask, target_mask = difference_mask(source_ids, target_ids)
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

    balanced_assets = {}
    aligned_assets = {}
    visual_complementary_assets = {}
    needs_fine_carrier = (
        include_balanced_carrier_assets
        or include_aligned_assets
        or include_visual_complementary_asset
    )
    if needs_fine_carrier:
        aligned_dx = (
            ALIGNED_DISPLACEMENT_AUDIO_PIXELS
            if seed % 2 == 0 else -ALIGNED_DISPLACEMENT_AUDIO_PIXELS
        )
        aligned_dy = 0
    if needs_fine_carrier:
        aligned_dots = make_aligned_dot_layout()
        balanced_source_plate, balanced_source_stats = _draw_balanced_dyad_plate(
            aligned_dots,
            source_position_masks,
            SOURCE_COLOURS,
            np.random.default_rng(plate_colour_seed),
            shift_audio_dx=aligned_dx,
        )
    if include_visual_complementary_asset:
        visual_complementary_plate, visual_complementary_stats = (
            _draw_balanced_dyad_plate(
                aligned_dots,
                source_position_masks,
                SOURCE_COLOURS,
                np.random.default_rng(plate_colour_seed),
                shift_audio_dx=aligned_dx,
                channel_b_mask=diagnostic_mask,
            )
        )
        if (
            balanced_source_stats["carrier_occupancy_sha256"]
            != visual_complementary_stats["carrier_occupancy_sha256"]
            or balanced_source_stats["channel_a_dot_count"]
            != visual_complementary_stats["channel_a_dot_count"]
            or balanced_source_stats["channel_a_radius_histogram"]
            != visual_complementary_stats["channel_a_radius_histogram"]
            or balanced_source_stats["channel_a_radius_area_units"]
            != visual_complementary_stats["channel_a_radius_area_units"]
            or balanced_source_stats["channel_a_active_pixel_count"]
            != visual_complementary_stats["channel_a_active_pixel_count"]
        ):
            raise RuntimeError(
                "visible complementary source differs from balanced source"
            )
    if include_aligned_assets:
        aligned_target_mask = translate_mask_without_clipping(
            target_mask, aligned_dx, aligned_dy,
        )
        target_position_masks = draw_position_masks(target_ids)
        canonical_visual_plate, canonical_stats = _draw_balanced_dyad_plate(
            aligned_dots,
            target_position_masks,
            SOURCE_COLOURS,
            np.random.default_rng(plate_colour_seed),
            shift_audio_dx=aligned_dx,
        )
        aligned_visual_plate, aligned_stats = _draw_balanced_dyad_plate(
            aligned_dots,
            target_position_masks,
            SOURCE_COLOURS,
            np.random.default_rng(plate_colour_seed),
            shift_audio_dx=aligned_dx,
            copy_channel_a_to_b=True,
        )
        if (
            canonical_stats["carrier_occupancy_sha256"]
            != aligned_stats["carrier_occupancy_sha256"]
        ):
            raise RuntimeError("canonical and aligned carrier geometry differ")
        if (
            aligned_stats["channel_a_dot_count"]
            != aligned_stats["channel_b_dot_count"]
            or aligned_stats["channel_a_radius_histogram"]
            != aligned_stats["channel_b_radius_histogram"]
            or aligned_stats["channel_a_radius_area_units"]
            != aligned_stats["channel_b_radius_area_units"]
            or aligned_stats["channel_a_active_pixel_count"]
            != aligned_stats["channel_b_active_pixel_count"]
        ):
            raise RuntimeError("aligned visible channels differ in density")
        aligned_input = _make_probe_from_background(
            aligned_target_mask,
            background_input,
            np.random.default_rng(seed ^ 0xA11C_4EED),
        )

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
    if needs_fine_carrier:
        balanced_source_path = plate_dir / f"{stem}_balanced_source.png"
        balanced_source_plate.save(balanced_source_path)
        balanced_assets = {
            "balanced_carrier_ir_plate_png": str(
                balanced_source_path.relative_to(output_dir)
            ),
            "aligned_displacement_audio_dx": aligned_dx,
            "aligned_displacement_audio_dy": aligned_dy,
            "aligned_displacement_audio_pixels": (
                ALIGNED_DISPLACEMENT_AUDIO_PIXELS
            ),
            "aligned_displacement_plate_pixels": (
                ALIGNED_DISPLACEMENT_AUDIO_PIXELS * PLATE_SCALE
            ),
            "aligned_visual_carrier_version": (
                ALIGNED_VISUAL_CARRIER_VERSION
            ),
            "aligned_visual_density_equivalence_version": (
                ALIGNED_VISUAL_DENSITY_EQUIVALENCE_VERSION
            ),
            "aligned_visual_pair_axis": ALIGNED_VISUAL_PAIR_AXIS,
            "aligned_visual_dot_pitch_pixels": ALIGNED_VISUAL_DOT_STEP,
            "aligned_visual_pair_offset_pixels": (
                ALIGNED_VISUAL_PAIR_OFFSET_PIXELS
            ),
            "aligned_visual_subdot_radii": list(
                ALIGNED_VISUAL_SUBDOT_RADII
            ),
            "aligned_visual_carrier_dot_count": balanced_source_stats[
                "carrier_dot_count"
            ],
            "aligned_visual_subdot_count": balanced_source_stats[
                "subdot_count"
            ],
            "aligned_visual_carrier_radius_histogram": (
                balanced_source_stats["carrier_radius_histogram"]
            ),
            "aligned_visual_carrier_occupied_pixel_count": (
                balanced_source_stats["carrier_occupied_pixel_count"]
            ),
            "balanced_carrier_occupancy_sha256": balanced_source_stats[
                "carrier_occupancy_sha256"
            ],
            "balanced_visual_source_dot_count": balanced_source_stats[
                "channel_a_dot_count"
            ],
            "balanced_visual_source_radius_histogram": balanced_source_stats[
                "channel_a_radius_histogram"
            ],
            "balanced_visual_source_radius_area_units": balanced_source_stats[
                "channel_a_radius_area_units"
            ],
            "balanced_visual_source_active_pixel_count": balanced_source_stats[
                "channel_a_active_pixel_count"
            ],
            "aligned_visual_palette_version": ALIGNED_VISUAL_PALETTE_VERSION,
            "visible_base_colours": [
                list(colour) for colour in SOURCE_COLOURS[:len(source_ids)]
            ],
        }
    if include_visual_complementary_asset:
        visual_complementary_path = (
            plate_dir / f"{stem}_visual_complementary.png"
        )
        visual_complementary_plate.save(visual_complementary_path)
        visual_complementary_assets = {
            "visual_complementary_plate_png": str(
                visual_complementary_path.relative_to(output_dir)
            ),
            "visual_complementary_equivalence_version": (
                "source-plus-diagnostic-equals-target-v1"
            ),
            "visual_complementary_addition_colour": list(
                ALIGNED_VISUAL_COPY_COLOUR
            ),
            "visual_complementary_source_dot_count": (
                visual_complementary_stats["channel_a_dot_count"]
            ),
            "visual_complementary_addition_dot_count": (
                visual_complementary_stats["channel_b_dot_count"]
            ),
            "visual_complementary_source_radius_histogram": (
                visual_complementary_stats["channel_a_radius_histogram"]
            ),
            "visual_complementary_addition_radius_histogram": (
                visual_complementary_stats["channel_b_radius_histogram"]
            ),
            "visual_complementary_source_active_pixel_count": (
                visual_complementary_stats["channel_a_active_pixel_count"]
            ),
            "visual_complementary_addition_active_pixel_count": (
                visual_complementary_stats["channel_b_active_pixel_count"]
            ),
            "visual_complementary_carrier_occupancy_sha256": (
                visual_complementary_stats["carrier_occupancy_sha256"]
            ),
            "visual_complementary_source_mask_sha256": mask_digest(
                source_mask
            ),
            "visual_complementary_addition_mask_sha256": mask_digest(
                diagnostic_mask
            ),
            "visual_complementary_target_mask_sha256": mask_digest(
                target_mask
            ),
        }
    if include_aligned_assets:
        canonical_plate_path = plate_dir / f"{stem}_visual_canonical.png"
        aligned_plate_path = plate_dir / f"{stem}_visual_aligned.png"
        aligned_input_path = audio_input_dir / f"{stem}_aligned_target.png"
        canonical_visual_plate.save(canonical_plate_path)
        aligned_visual_plate.save(aligned_plate_path)
        aligned_input.save(aligned_input_path)
        aligned_assets = {
            "canonical_visual_plate_png": str(
                canonical_plate_path.relative_to(output_dir)
            ),
            "visual_aligned_plate_png": str(
                aligned_plate_path.relative_to(output_dir)
            ),
            "aligned_input_png": str(aligned_input_path.relative_to(output_dir)),
            "aligned_target_pixel_count": int(
                np.count_nonzero(np.asarray(aligned_target_mask))
            ),
            "canonical_target_pixel_count": int(
                np.count_nonzero(np.asarray(target_mask))
            ),
            "canonical_visual_dot_count": canonical_stats[
                "channel_a_dot_count"
            ],
            "aligned_visual_base_dot_count": aligned_stats[
                "channel_a_dot_count"
            ],
            "aligned_visual_shifted_dot_count": aligned_stats[
                "channel_b_dot_count"
            ],
            "aligned_visual_overlap_dot_count": aligned_stats[
                "channel_overlap_dot_count"
            ],
            "aligned_visual_base_radius_histogram": aligned_stats[
                "channel_a_radius_histogram"
            ],
            "aligned_visual_shifted_radius_histogram": aligned_stats[
                "channel_b_radius_histogram"
            ],
            "aligned_visual_base_radius_area_units": aligned_stats[
                "channel_a_radius_area_units"
            ],
            "aligned_visual_shifted_radius_area_units": aligned_stats[
                "channel_b_radius_area_units"
            ],
            "aligned_visual_base_active_pixel_count": aligned_stats[
                "channel_a_active_pixel_count"
            ],
            "aligned_visual_shifted_active_pixel_count": aligned_stats[
                "channel_b_active_pixel_count"
            ],
            "canonical_carrier_occupancy_sha256": canonical_stats[
                "carrier_occupancy_sha256"
            ],
            "aligned_carrier_occupancy_sha256": aligned_stats[
                "carrier_occupancy_sha256"
            ],
            "canonical_target_mask_sha256": mask_digest(target_mask),
            "aligned_target_mask_sha256": mask_digest(aligned_target_mask),
            "aligned_visual_base_mask_sha256": mask_digest(target_mask),
            "aligned_visual_shifted_mask_sha256": mask_digest(
                aligned_target_mask
            ),
            "alignment_equivalence_version": "canonical-target-mask-v1",
            "aligned_visual_base_channel_position": "seeded-diagonal-a",
            "aligned_visual_shifted_channel_position": "seeded-diagonal-b",
            "aligned_visual_base_colours": [
                list(colour) for colour in SOURCE_COLOURS[:len(target_ids)]
            ],
            "aligned_visual_copy_colour": list(ALIGNED_VISUAL_COPY_COLOUR),
        }

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
        **balanced_assets,
        **aligned_assets,
        **visual_complementary_assets,
    }


def _draw_plate(
    dots: list[tuple[int, int, int]],
    source_position_masks: list[Image.Image],
    diagnostic_mask: Image.Image,
    rng: np.random.Generator,
    reveal_probe: bool,
    *,
    aligned_position_masks: list[Image.Image] | None = None,
) -> Image.Image:
    plate = Image.new("RGB", (PLATE_WIDTH, PLATE_HEIGHT), (31, 32, 33))
    draw = ImageDraw.Draw(plate)
    source_arrays = [np.asarray(mask) > 0 for mask in source_position_masks]
    diagnostic = np.asarray(diagnostic_mask) > 0
    aligned_arrays = [
        np.asarray(mask) > 0 for mask in (aligned_position_masks or [])
    ]

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
        for position, aligned in enumerate(aligned_arrays):
            if aligned[audio_y, audio_x]:
                colour = _vary_colour(ALIGNED_VISUAL_COLOURS[position], rng)
                break
        draw.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill=colour,
        )
    return plate


def _dot_cell_key(x: int, y: int) -> tuple[int, int]:
    """Recover the stable grid cell underlying one jittered parent dot."""
    first = ALIGNED_VISUAL_DOT_STEP // 2
    column_count = len(range(first, PLATE_WIDTH, ALIGNED_VISUAL_DOT_STEP))
    row_count = len(range(first, PLATE_HEIGHT, ALIGNED_VISUAL_DOT_STEP))
    column = int(round((x - first) / ALIGNED_VISUAL_DOT_STEP))
    row = int(round((y - first) / ALIGNED_VISUAL_DOT_STEP))
    return (
        int(np.clip(column, 0, column_count - 1)),
        int(np.clip(row, 0, row_count - 1)),
    )


def _circle_box(
    centre_x: int,
    centre_y: int,
    radius: int,
) -> tuple[int, int, int, int]:
    return (
        centre_x - radius,
        centre_y - radius,
        centre_x + radius,
        centre_y + radius,
    )


def _radius_histogram(radii: list[int]) -> dict[str, int]:
    return {
        str(radius): radii.count(radius)
        for radius in ALIGNED_VISUAL_SUBDOT_RADII
    }


def _draw_balanced_dyad_plate(
    dots: list[tuple[int, int, int]],
    channel_a_masks: list[Image.Image],
    channel_a_colours: tuple[tuple[int, int, int], ...],
    rng: np.random.Generator,
    *,
    shift_audio_dx: int,
    copy_channel_a_to_b: bool = False,
    channel_b_mask: Image.Image | None = None,
) -> tuple[Image.Image, dict]:
    """Render a density-balanced two-layer carrier using complete subdots.

    Every grid cell contains one A and one B subdot on a seeded diagonal. The
    two layers receive the same fixed radius multiset. B may either be a
    bijective one-cell translation of A's sampled cells or an independent
    complementary-addition mask sampled on the same carrier.
    """
    if copy_channel_a_to_b and channel_b_mask is not None:
        raise ValueError("channel B cannot be both a copy and a complementary mask")
    if len(channel_a_colours) < len(channel_a_masks):
        raise ValueError("each channel-A mask requires one colour")
    shift_plate_pixels = shift_audio_dx * PLATE_SCALE
    if shift_plate_pixels % ALIGNED_VISUAL_DOT_STEP != 0:
        raise ValueError("aligned displacement must be a whole carrier cell")
    shift_cells = shift_plate_pixels // ALIGNED_VISUAL_DOT_STEP
    if abs(shift_cells) != 1:
        raise ValueError("balanced dyad carrier requires a one-cell shift")
    geometry_rng = np.random.default_rng(
        int(rng.integers(0, 2**32, dtype=np.uint32)),
    )
    colour_rng = np.random.default_rng(
        int(rng.integers(0, 2**32, dtype=np.uint32)),
    )

    first = ALIGNED_VISUAL_DOT_STEP // 2
    columns = tuple(range(first, PLATE_WIDTH, ALIGNED_VISUAL_DOT_STEP))
    rows = tuple(range(first, PLATE_HEIGHT, ALIGNED_VISUAL_DOT_STEP))
    dot_by_cell = {
        _dot_cell_key(x, y): (x, y, radius) for x, y, radius in dots
    }
    expected_cells = len(columns) * len(rows)
    if len(dot_by_cell) != expected_cells:
        raise RuntimeError("jittered dot layout does not map one-to-one to cells")

    ordered_cells = [
        (column, row)
        for row in range(len(rows))
        for column in range(len(columns))
    ]
    if len(ALIGNED_VISUAL_SUBDOT_RADII) != 2:
        raise RuntimeError("balanced radius alternation requires two radii")
    small_radius, large_radius = ALIGNED_VISUAL_SUBDOT_RADII
    row_phases = [
        phase
        for phase, count in (
            (0, (len(rows) + 1) // 2),
            (1, len(rows) // 2),
        )
        for _index in range(count)
    ]
    geometry_rng.shuffle(row_phases)
    row_phase = dict(enumerate(row_phases))
    channel_a_radius = {
        (column, row): (
            small_radius
            if (column + row_phase[row]) % 2 == 0
            else large_radius
        )
        for column, row in ordered_cells
    }
    channel_b_radius = {
        (column, row): channel_a_radius[
            ((column - shift_cells) % len(columns), row)
        ]
        for column, row in ordered_cells
    }
    channel_a_arrays = [np.asarray(mask) > 0 for mask in channel_a_masks]
    channel_b_values = (
        np.asarray(channel_b_mask) > 0
        if channel_b_mask is not None else None
    )
    channel_a_position: dict[tuple[int, int], int | None] = {}
    for cell, (x, y, _parent_radius) in dot_by_cell.items():
        audio_x = min(AUDIO_WIDTH - 1, x // PLATE_SCALE)
        audio_y = min(AUDIO_HEIGHT - 1, y // PLATE_SCALE)
        channel_a_position[cell] = next((
            position
            for position, values in enumerate(channel_a_arrays)
            if values[audio_y, audio_x]
        ), None)

    plate = Image.new(
        "RGB", (PLATE_WIDTH, PLATE_HEIGHT), PLATE_BACKGROUND_COLOUR,
    )
    draw = ImageDraw.Draw(plate)
    carrier_mask = Image.new("L", plate.size, 0)
    carrier_draw = ImageDraw.Draw(carrier_mask)
    channel_a_active_mask = Image.new("L", plate.size, 0)
    channel_a_active_draw = ImageDraw.Draw(channel_a_active_mask)
    channel_b_active_mask = Image.new("L", plate.size, 0)
    channel_b_active_draw = ImageDraw.Draw(channel_b_active_mask)
    channel_a_active_radii = []
    channel_b_active_radii = []
    overlap_count = 0

    for column, row in ordered_cells:
        cell = (column, row)
        original_x, original_y, _parent_radius = dot_by_cell[cell]
        nominal_x = columns[column]
        nominal_y = rows[row]
        a_radius = channel_a_radius[cell]
        b_radius = channel_b_radius[cell]
        safe_jitter = max(
            0,
            ALIGNED_VISUAL_DOT_STEP // 2
            - 1
            - ALIGNED_VISUAL_PAIR_OFFSET_PIXELS
            - max(a_radius, b_radius),
        )
        centre_x = nominal_x + int(np.clip(
            round((original_x - nominal_x) / 5), -safe_jitter, safe_jitter,
        ))
        centre_y = nominal_y + int(np.clip(
            round((original_y - nominal_y) / 5), -safe_jitter, safe_jitter,
        ))
        diagonal_y = (
            ALIGNED_VISUAL_PAIR_OFFSET_PIXELS
            if int(geometry_rng.integers(0, 2))
            else -ALIGNED_VISUAL_PAIR_OFFSET_PIXELS
        )
        role_sign = 1 if int(geometry_rng.integers(0, 2)) else -1
        vector_x = role_sign * ALIGNED_VISUAL_PAIR_OFFSET_PIXELS
        vector_y = role_sign * diagonal_y
        a_box = _circle_box(
            centre_x + vector_x, centre_y + vector_y, a_radius,
        )
        b_box = _circle_box(
            centre_x - vector_x, centre_y - vector_y, b_radius,
        )

        a_position = channel_a_position[cell]
        source_column = column - shift_cells
        b_source = (source_column, row)
        if copy_channel_a_to_b:
            b_active = (
                0 <= source_column < len(columns)
                and channel_a_position[b_source] is not None
            )
        elif channel_b_values is not None:
            audio_x = min(AUDIO_WIDTH - 1, original_x // PLATE_SCALE)
            audio_y = min(AUDIO_HEIGHT - 1, original_y // PLATE_SCALE)
            b_active = bool(channel_b_values[audio_y, audio_x])
        else:
            b_active = False
        a_active = a_position is not None
        overlap_count += int(a_active and b_active)
        if a_active:
            channel_a_active_radii.append(a_radius)
        if b_active:
            channel_b_active_radii.append(b_radius)

        a_background = _vary_colour(BACKGROUND_COLOUR, colour_rng)
        b_background = _vary_colour(BACKGROUND_COLOUR, colour_rng)
        varied_a_colours = [
            _vary_colour(colour, colour_rng)
            for colour in channel_a_colours[:len(channel_a_masks)]
        ]
        b_colour = _vary_colour(ALIGNED_VISUAL_COPY_COLOUR, colour_rng)
        draw.ellipse(
            a_box,
            fill=(
                varied_a_colours[a_position]
                if a_position is not None else a_background
            ),
        )
        draw.ellipse(
            b_box,
            fill=b_colour if b_active else b_background,
        )
        carrier_draw.ellipse(a_box, fill=255)
        carrier_draw.ellipse(b_box, fill=255)
        if a_active:
            channel_a_active_draw.ellipse(a_box, fill=255)
        if b_active:
            channel_b_active_draw.ellipse(b_box, fill=255)

    if copy_channel_a_to_b and len(channel_a_active_radii) != len(
        channel_b_active_radii
    ):
        raise ValueError("one-cell shift clips active visual carrier tokens")

    carrier_values = np.asarray(carrier_mask) > 0
    channel_a_values = np.asarray(channel_a_active_mask) > 0
    channel_b_values = np.asarray(channel_b_active_mask) > 0
    all_a_radii = list(channel_a_radius.values())
    all_b_radii = list(channel_b_radius.values())
    carrier_a_histogram = _radius_histogram(all_a_radii)
    carrier_b_histogram = _radius_histogram(all_b_radii)
    carrier_occupied_pixel_count = int(np.count_nonzero(carrier_values))
    if (
        expected_cells != ALIGNED_VISUAL_CARRIER_DOT_COUNT
        or carrier_a_histogram != ALIGNED_VISUAL_CARRIER_RADIUS_HISTOGRAM
        or carrier_b_histogram != ALIGNED_VISUAL_CARRIER_RADIUS_HISTOGRAM
        or carrier_occupied_pixel_count
        != ALIGNED_VISUAL_CARRIER_OCCUPIED_PIXEL_COUNT
    ):
        raise RuntimeError("fine aligned carrier violates its density contract")
    return plate, {
        "carrier_dot_count": expected_cells,
        "subdot_count": expected_cells * 2,
        "carrier_radius_histogram": {
            "channel_a": carrier_a_histogram,
            "channel_b": carrier_b_histogram,
        },
        "carrier_occupied_pixel_count": carrier_occupied_pixel_count,
        "carrier_occupancy_sha256": mask_digest(carrier_mask),
        "channel_a_dot_count": len(channel_a_active_radii),
        "channel_b_dot_count": len(channel_b_active_radii),
        "channel_overlap_dot_count": overlap_count,
        "channel_a_radius_histogram": _radius_histogram(
            channel_a_active_radii
        ),
        "channel_b_radius_histogram": _radius_histogram(
            channel_b_active_radii
        ),
        "channel_a_radius_area_units": sum(
            radius * radius for radius in channel_a_active_radii
        ),
        "channel_b_radius_area_units": sum(
            radius * radius for radius in channel_b_active_radii
        ),
        "channel_a_active_pixel_count": int(
            np.count_nonzero(channel_a_values)
        ),
        "channel_b_active_pixel_count": int(
            np.count_nonzero(channel_b_values)
        ),
    }


def _make_audio_inputs(
    diagnostic_mask: Image.Image,
    rng: np.random.Generator,
) -> tuple[Image.Image, Image.Image]:
    background = rng.integers(4, 28, size=(AUDIO_HEIGHT, AUDIO_WIDTH), dtype=np.uint8)
    probe = background.copy()
    diagnostic = np.asarray(diagnostic_mask) > 0
    probe[diagnostic] = rng.integers(210, 256, size=int(diagnostic.sum()), dtype=np.uint8)
    return Image.fromarray(probe, mode="L"), Image.fromarray(background, mode="L")


def _make_probe_from_background(
    probe_mask: Image.Image,
    background_input: Image.Image,
    rng: np.random.Generator,
) -> Image.Image:
    probe = np.asarray(background_input).copy()
    active = np.asarray(probe_mask) > 0
    probe[active] = rng.integers(210, 256, size=int(active.sum()), dtype=np.uint8)
    return Image.fromarray(probe, mode="L")


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
