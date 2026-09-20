"""Post-battle OCR burst: wait out reward count-up animation before publishing."""

from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image

from .ocr_parse import BattleReport


@dataclass(frozen=True)
class SettleConfig:
    """How many matching frames before a reward pair is considered final."""

    stable_required: int = 2
    # Relative OCR jitter allowed while treating two reads as the same tick.
    rp_slack_frac: float = 0.015
    sl_slack_frac: float = 0.015
    rp_slack_min: int = 8
    sl_slack_min: int = 20
    # Lean-ROI pixel compare (0–255 MAE on a tiny grayscale signature).
    # Count-up animation moves digits → high MAE; settled cells → low MAE.
    frame_mae_max: float = 10.0
    frame_sig_size: tuple[int, int] = (192, 48)


def _slack(value: int, *, frac: float, minimum: int) -> int:
    return max(minimum, int(abs(value) * frac))


def pairs_near(
    left: tuple[int, int],
    right: tuple[int, int],
    *,
    cfg: SettleConfig | None = None,
) -> bool:
    """True when two OCR pairs are the same reward within jitter."""
    cfg = cfg or SettleConfig()
    rp1, sl1 = left
    rp2, sl2 = right
    return abs(rp1 - rp2) <= _slack(rp1, frac=cfg.rp_slack_frac, minimum=cfg.rp_slack_min) and abs(
        sl1 - sl2
    ) <= _slack(sl1, frac=cfg.sl_slack_frac, minimum=cfg.sl_slack_min)


def looks_like_countup(
    previous: tuple[int, int],
    current: tuple[int, int],
    *,
    cfg: SettleConfig | None = None,
) -> bool:
    """
    WT reward cells tick upward. Treat a later read as mid-animation when both
    axes are non-decreasing (with OCR slack) and at least one clearly rose.
    """
    cfg = cfg or SettleConfig()
    prev_rp, prev_sl = previous
    cur_rp, cur_sl = current
    rp_floor = prev_rp - _slack(prev_rp, frac=cfg.rp_slack_frac, minimum=5)
    sl_floor = prev_sl - _slack(prev_sl, frac=cfg.sl_slack_frac, minimum=10)
    if cur_rp < rp_floor or cur_sl < sl_floor:
        return False
    rp_up = cur_rp > prev_rp + _slack(prev_rp, frac=cfg.rp_slack_frac, minimum=5)
    sl_up = cur_sl > prev_sl + _slack(prev_sl, frac=cfg.sl_slack_frac, minimum=10)
    return rp_up or sl_up


def prefer_higher(left: BattleReport, right: BattleReport) -> BattleReport:
    """Keep the larger reward pair (settled end of a count-up)."""
    left_score = (left.research_points, left.silver_lions, left.confidence)
    right_score = (right.research_points, right.silver_lions, right.confidence)
    return right if right_score >= left_score else left


def lean_roi_signature(
    frame: Image.Image,
    *,
    cfg: SettleConfig | None = None,
) -> Image.Image | None:
    """
    Tiny grayscale collage of lean digit ROIs — cheap fingerprint of reward cells.

    Full-client compare is noisy (background video). Lean crops track the
    animating RP/SL glyphs that matter for settle.
    """
    from .roi_layout import iter_reward_digit_rois

    cfg = cfg or SettleConfig()
    crops = [crop.convert("L") for _tag, crop in iter_reward_digit_rois(frame, dense=False)]
    if not crops:
        return None
    height = max(c.height for c in crops)
    width = sum(c.width for c in crops) + 2 * (len(crops) - 1)
    strip = Image.new("L", (max(1, width), max(1, height)), 0)
    x = 0
    for crop in crops:
        if crop.height != height:
            crop = crop.resize(
                (max(1, int(crop.width * height / float(crop.height))), height),
                Image.Resampling.BILINEAR,
            )
        strip.paste(crop, (x, 0))
        x += crop.width + 2
    return strip.resize(cfg.frame_sig_size, Image.Resampling.BILINEAR)


def signature_mae(left: Image.Image, right: Image.Image) -> float:
    """Mean absolute pixel difference (0–255) for equal-sized L-mode images."""
    if left.size != right.size:
        right = right.resize(left.size, Image.Resampling.BILINEAR)
    a = left.convert("L")
    b = right.convert("L")
    pa = a.tobytes()
    pb = b.tobytes()
    if not pa:
        return 0.0
    total = 0
    for x, y in zip(pa, pb, strict=False):
        total += abs(x - y)
    return total / float(len(pa))


def signatures_near(
    left: Image.Image,
    right: Image.Image,
    *,
    cfg: SettleConfig | None = None,
) -> bool:
    cfg = cfg or SettleConfig()
    return signature_mae(left, right) <= cfg.frame_mae_max


@dataclass
class LeanRoiFrameGate:
    """
    Require lean reward crops to look visually stable across frames.

    Catches mid-animation digits even when OCR briefly returns the same pair.
    """

    cfg: SettleConfig = field(default_factory=SettleConfig)
    last_sig: Image.Image | None = None
    stable_count: int = 0
    last_mae: float | None = None

    def observe(self, frame: Image.Image) -> bool:
        """
        Ingest one full client frame.

        Returns True once lean ROI pixels have matched for ``stable_required`` frames.
        """
        sig = lean_roi_signature(frame, cfg=self.cfg)
        if sig is None:
            self.last_sig = None
            self.stable_count = 0
            self.last_mae = None
            return False

        if self.last_sig is None:
            self.last_sig = sig
            self.stable_count = 1
            self.last_mae = None
            return False

        mae = signature_mae(self.last_sig, sig)
        self.last_mae = mae
        if mae <= self.cfg.frame_mae_max:
            self.stable_count += 1
            self.last_sig = sig
            return self.stable_count >= self.cfg.stable_required

        # Pixels still moving (count-up / fade) — restart streak.
        self.last_sig = sig
        self.stable_count = 1
        return False

    def reset(self) -> None:
        self.last_sig = None
        self.stable_count = 0
        self.last_mae = None


@dataclass
class SettleTracker:
    """
    Tracks consecutive OCR pairs until the count-up animation stops moving.

    Publish only after [stable_required] near-identical frames with no further rise.
    """

    cfg: SettleConfig = field(default_factory=SettleConfig)
    last_pair: tuple[int, int] | None = None
    stable_count: int = 0
    candidate: BattleReport | None = None
    best_seen: BattleReport | None = None

    def observe(self, report: BattleReport) -> BattleReport | None:
        """
        Ingest one confident OCR report.

        Returns a report ready to publish when the pair has settled, else None.
        """
        pair = (report.research_points, report.silver_lions)
        self.best_seen = report if self.best_seen is None else prefer_higher(self.best_seen, report)

        if self.last_pair is None:
            self.last_pair = pair
            self.stable_count = 1
            self.candidate = report
            return None

        if pairs_near(self.last_pair, pair, cfg=self.cfg):
            self.stable_count += 1
            # Prefer the higher of the near-equal reads (OCR sometimes undershoots).
            self.candidate = (
                prefer_higher(self.candidate, report) if self.candidate is not None else report
            )
            self.last_pair = (
                self.candidate.research_points,
                self.candidate.silver_lions,
            )
            if self.stable_count >= self.cfg.stable_required:
                return self.candidate
            return None

        if looks_like_countup(self.last_pair, pair, cfg=self.cfg):
            # Animation still ticking — reset the stability streak on the new totals.
            self.last_pair = pair
            self.stable_count = 1
            self.candidate = report
            return None

        # Unrelated jump (wrong ROI / column flicker): keep chasing the higher pair
        # but restart stability so we do not publish a one-off glitch.
        if (
            pair[0] >= self.last_pair[0]
            and pair[1] >= self.last_pair[1]
            and (pair[0] > self.last_pair[0] or pair[1] > self.last_pair[1])
        ):
            self.last_pair = pair
            self.stable_count = 1
            self.candidate = report
        else:
            self.stable_count = 1
            self.last_pair = pair
            self.candidate = report
        return None

    def finalize(self) -> BattleReport | None:
        """Best effort when the results screen closes before a full settle."""
        if self.candidate is not None and self.stable_count >= 1:
            return self.candidate
        return self.best_seen
