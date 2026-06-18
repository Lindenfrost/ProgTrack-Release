# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: PdG-to-progesterone conversion math and fitting logic.

import logging
from datetime import datetime, timezone, timedelta

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Physiological calibration constants
# ---------------------------------------------------------------------------

# Slope from anchor points: (115 - 2.75) / (25 - 3) ≈ 5.1 ng/mL per µg/mg Cr
PDG_SCALE = 5.1

# Intercept bounds (ng/mL)
A0_MAX_NGML =   4.0   # top of follicular progesterone range
A0_MIN_NGML = -20.0   # generous margin below expected intercept (~-12.6)

# F-test significance threshold for kink vs linear selection.
# 0.10 rather than 0.05 to account for low power at small n.
KINK_F_PVALUE = 0.10

# Minimum number of paired observations required on each side of a knot.
# Prevents knots that fit a single point rather than a true regime change.
MIN_POINTS_PER_SEGMENT = 2


# ---------------------------------------------------------------------------
# Lazy scipy import
# ---------------------------------------------------------------------------

_lsq_linear = None


def _get_lsq_linear():
    global _lsq_linear
    if _lsq_linear is None:
        try:
            from scipy.optimize import lsq_linear
            _lsq_linear = lsq_linear
        except ImportError as e:
            raise ImportError(
                "scipy is required for model fitting. "
                f"Please install: pip install scipy. Error: {e}"
            )
    return _lsq_linear


def _solve_bounded_lsq(H, ys, lb, ub):
    """Run bounded least squares with per-parameter bounds."""
    lsq_func = _get_lsq_linear()
    try:
        return lsq_func(H, ys, bounds=(lb, ub), lsmr_tol="auto", verbose=0)
    except TypeError:
        return lsq_func(H, ys, bounds=(lb, ub))


def _fit_succeeded(res) -> bool:
    """
    scipy.optimize.lsq_linear returns status 1, 2, or 3 on convergence.
    Status 3 means max iterations reached but a valid solution was found.
    There is no boolean .success in all scipy versions, so we check .status.
    """
    status = getattr(res, "status", None)
    if status is not None:
        return int(status) in (1, 2, 3)
    return bool(getattr(res, "success", False))


# ---------------------------------------------------------------------------
# AICc (kept for logging/diagnostic purposes)
# ---------------------------------------------------------------------------

def _aic(n: int, k: int, mse: float) -> float:
    """
    Corrected AIC (AICc) for a Gaussian linear model.
    Stored in params for diagnostics but not used for model selection.
    """
    mse = max(mse, 1e-12)
    aic = n * np.log(mse) + 2 * k
    denom = n - k - 1
    if denom > 0:
        aic += 2 * k * (k + 1) / denom
    return float(aic)


# ---------------------------------------------------------------------------
# Biological lag correction
# ---------------------------------------------------------------------------

def apply_biological_lag(pdg_by_date, prog_by_date, lag_hours: float = 12.0):
    """
    Build lag-adjusted (pdg, progesterone) pairs from same-day measurements.

    For each date D with both a PdG and a progesterone reading, the
    regression target is a weighted blend of the previous and current
    day's blood value:

        prog_adjusted = (1 - f) * prog_D  +  f * prog_{D-1}
        f = lag_hours / 24

    When prog_{D-1} is unavailable the same-day value is used unchanged.
    A single warning is emitted if NO pairs could be lag-adjusted (rather
    than one warning per date, which is noisy for daily-sampled data).

    Args:
        pdg_by_date:  Dict mapping date -> PdG float value (µg/mg Cr).
        prog_by_date: Dict mapping date -> progesterone float value (ng/mL).
        lag_hours:    Assumed biological lag in hours, 0-24 (default 12).

    Returns:
        Tuple (paired_dates, paired_pdg, paired_prog) sorted by date.
    """
    if not 0.0 <= lag_hours <= 24.0:
        raise ValueError(f"lag_hours must be 0-24, got {lag_hours}")

    f = lag_hours / 24.0
    one_day = timedelta(days=1)

    common_dates = sorted(set(pdg_by_date) & set(prog_by_date))
    if not common_dates:
        return [], [], []

    paired_dates, paired_pdg, paired_prog = [], [], []
    n_adjusted, n_fallback = 0, 0

    for date in common_dates:
        pdg_val  = pdg_by_date[date]
        prog_val = prog_by_date[date]

        prev_date = date - one_day
        if f > 0.0 and prev_date in prog_by_date:
            prog_prev   = prog_by_date[prev_date]
            prog_target = (1.0 - f) * prog_val + f * prog_prev
            n_adjusted += 1
        else:
            prog_target = prog_val
            n_fallback += 1

        paired_dates.append(date)
        paired_pdg.append(pdg_val)
        paired_prog.append(prog_target)

    if f > 0.0:
        if n_adjusted == 0:
            logger.warning(
                "Lag correction (lag_hours=%.1f): no consecutive blood-draw "
                "days found in %d pairs — all use same-day progesterone. "
                "Results may be biased on rising/falling curves.",
                lag_hours, n_fallback,
            )
        else:
            logger.debug(
                "Lag correction: %d/%d pairs adjusted, %d used same-day fallback.",
                n_adjusted, len(paired_dates), n_fallback,
            )

    return paired_dates, paired_pdg, paired_prog


# ---------------------------------------------------------------------------
# Main converter class
# ---------------------------------------------------------------------------

class PdGConverter:
    """Converter for PdG (µg/mg Cr) to Progesterone (ng/mL).

    Internal model (scaled PdG space)
    -----------------------------------
    PdG is multiplied by PDG_SCALE (5.1) before fitting, aligning its
    numerical range with progesterone (ng/mL).

    Linear:  prog = max(0, a0 + b1 * pdg_s)
    Kink:    prog = max(0, a0 + b1 * pdg_s + b2 * max(0, pdg_s - knot_s))

    where pdg_s = pdg_raw * PDG_SCALE.

    Bounds:
      a0  [A0_MIN_NGML, A0_MAX_NGML] = [-20.0, 4.0]  (may be negative)
      b1  [0, +inf]
      b2  [0, +inf]

    All public methods operate in original units (µg/mg Cr in, ng/mL out).
    """

    def fit_model(self, pdg_paired, prog_paired, pdg_all=None,
                  pdg_scale: float = PDG_SCALE):
        """Fit conversion model.

        Args:
            pdg_paired:  PdG values with a matching progesterone reading
                         (µg/mg Cr).
            prog_paired: Corresponding (lag-adjusted) progesterone values
                         (ng/mL).
            pdg_all:     All available PdG values including unpaired days
                         (µg/mg Cr).  Used for knot placement and model-type
                         selection.  Falls back to pdg_paired if None.
            pdg_scale:   Multiplier applied to raw PdG before fitting
                         (default PDG_SCALE = 5.1).

        Returns:
            Dict with model parameters, or None if fewer than 3 valid pairs.
        """
        xs_paired = np.asarray(pdg_paired, dtype=float)
        ys        = np.asarray(prog_paired, dtype=float)

        if xs_paired.shape != ys.shape:
            n = min(len(xs_paired), len(ys))
            xs_paired, ys = xs_paired[:n], ys[:n]

        mask = np.isfinite(xs_paired) & np.isfinite(ys)
        xs_paired, ys = xs_paired[mask], ys[mask]
        n_pairs = len(xs_paired)

        if n_pairs < 3:
            return None

        if pdg_all is not None:
            xs_all = np.asarray(pdg_all, dtype=float)
            xs_all = xs_all[np.isfinite(xs_all)]
        else:
            xs_all = xs_paired
        n_pdg_total = len(xs_all)

        xs_s     = xs_paired * pdg_scale
        xs_all_s = xs_all    * pdg_scale

        scale_meta = {
            "pdg_scale": pdg_scale,
            "pdg_unit":  "ug/mg_Cr",
            "prog_unit": "ng/mL",
        }

        linear = self._fit_linear(xs_s, ys, scale_meta, n_pairs)

        kink = None
        if n_pdg_total >= 7:
            kink = self._fit_kink(xs_s, ys, xs_all_s, scale_meta,
                                  n_pairs, pdg_scale)

        result = self._select_model(linear, kink, n_pairs)
        if result is not None:
            result["n_pdg_total"] = n_pdg_total
        return result

    def fit_model_from_animal(self, animal, lag_hours: float = 12.0,
                              pdg_scale: float = PDG_SCALE):
        """Convenience wrapper: extract records, apply lag, and fit.

        Args:
            animal:    Animal dict with 'pdg' and 'daten' record lists.
            lag_hours: Assumed biological lag in hours, 0-24 (default 12).
            pdg_scale: PdG multiplier (default PDG_SCALE = 5.1).

        Returns:
            Model parameter dict, or None.
        """
        pdg_by_date, prog_by_date = _build_date_maps(animal)
        _, paired_pdg, paired_prog = apply_biological_lag(
            pdg_by_date, prog_by_date, lag_hours=lag_hours
        )
        all_pdg = list(pdg_by_date.values())
        return self.fit_model(paired_pdg, paired_prog,
                              pdg_all=all_pdg, pdg_scale=pdg_scale)

    # ------------------------------------------------------------------
    # Model selection
    # ------------------------------------------------------------------

    def _select_model(self, linear, kink, n_pairs: int):
        """Prefer kink over linear when an F-test rejects b2=0.

        The kink model is nested within linear (b2=0 recovers linear),
        so the F-test is the correct comparison for nested models:

            F = ((SS_lin - SS_kink) / delta_k) / (SS_kink / (n - k_kink))

        where delta_k = 2 (b2 adds one slope parameter and one knot),
        k_kink = 4.  We use KINK_F_PVALUE = 0.10 to compensate for the
        low power typical of small veterinary datasets.
        """
        from scipy import stats as _stats

        if linear is None and kink is None:
            return None
        if kink is None:
            return linear
        if linear is None:
            return kink

        k_kink  = 4
        delta_k = 2
        df_res  = n_pairs - k_kink

        if df_res <= 0:
            logger.debug(
                "F-test skipped (df_res=%d <= 0, n=%d); keeping linear.",
                df_res, n_pairs,
            )
            return linear

        ss_lin  = n_pairs * linear["mse"]
        ss_kink = n_pairs * kink["mse"]

        if ss_kink <= 0 or ss_lin <= ss_kink:
            logger.debug("Kink does not reduce SS; keeping linear.")
            return linear

        F = ((ss_lin - ss_kink) / delta_k) / (ss_kink / df_res)
        p = float(1 - _stats.f.cdf(F, delta_k, df_res))

        if p < KINK_F_PVALUE:
            logger.debug(
                "Kink model selected: F=%.2f, p=%.4f (threshold %.2f), "
                "MSE kink=%.2f vs linear=%.2f",
                F, p, KINK_F_PVALUE, kink["mse"], linear["mse"],
            )
            return kink

        logger.debug(
            "Linear model selected: F=%.2f, p=%.4f >= threshold %.2f",
            F, p, KINK_F_PVALUE,
        )
        return linear

    # ------------------------------------------------------------------
    # Private fitting helpers
    # ------------------------------------------------------------------

    def _fit_kink(self, xs_s, ys, xs_all_s, scale_meta, n_pairs, pdg_scale):
        """Broken-stick model in scaled PdG / ng/mL space.

        Knot candidates are quantiles of xs_all_s (full PdG distribution).
        Only knots with at least MIN_POINTS_PER_SEGMENT paired observations
        on each side are considered — a knot with 1 point above it fits a
        single observation and produces an unstable, high-leverage b2.

        Bounds per parameter:
          a0: [A0_MIN_NGML, A0_MAX_NGML]  intercept may be negative
          b1: [0, +inf]                   slope below knot >= 0
          b2: [0, +inf]                   slope increment above knot >= 0
        """
        quantiles = [0.15, 0.25, 0.35, 0.50, 0.65, 0.75, 0.85]
        knots_s = np.unique(np.quantile(xs_all_s, quantiles))
        best = None

        lb = np.array([A0_MIN_NGML, 0.0,    0.0])
        ub = np.array([A0_MAX_NGML, np.inf, np.inf])

        for k_s in knots_s:
            n_above = int(np.sum(xs_s > k_s))
            n_below = int(np.sum(xs_s <= k_s))
            if n_above < MIN_POINTS_PER_SEGMENT or n_below < MIN_POINTS_PER_SEGMENT:
                logger.debug(
                    "Knot %.2f µg/mg Cr skipped: %d below, %d above "
                    "(min %d required each side).",
                    k_s / pdg_scale, n_below, n_above, MIN_POINTS_PER_SEGMENT,
                )
                continue

            H = np.column_stack([
                np.ones_like(xs_s),
                xs_s,
                np.clip(xs_s - k_s, 0, None),
            ])
            try:
                res = _solve_bounded_lsq(H, ys, lb, ub)
                if not _fit_succeeded(res):
                    continue
                yhat = H @ res.x
                mse  = float(np.mean((ys - yhat) ** 2))
                aic  = _aic(n=n_pairs, k=4, mse=mse)

                if best is None or mse < best["mse"]:
                    best = {
                        "model_type":    "kink",
                        "a0":            float(res.x[0]),
                        "b1":            float(res.x[1]),
                        "b2":            float(res.x[2]),
                        "knot":          float(k_s),
                        "knot_original": float(k_s / pdg_scale),
                        "mse":           mse,
                        "aic":           aic,
                        "n_pairs":       n_pairs,
                        "fitted_at":     datetime.now(timezone.utc).isoformat(),
                        **scale_meta,
                    }
            except ImportError:
                raise
            except Exception as exc:
                logger.warning("Kink fit failed for knot=%.4f: %s", k_s, exc)

        return best

    def _fit_linear(self, xs_s, ys, scale_meta, n_pairs):
        """Linear model in scaled PdG / ng/mL space.

        Bounds:
          a0: [A0_MIN_NGML, A0_MAX_NGML]
          b1: [0, +inf]
        """
        H  = np.column_stack([np.ones_like(xs_s), xs_s])
        lb = np.array([A0_MIN_NGML, 0.0])
        ub = np.array([A0_MAX_NGML, np.inf])
        try:
            res = _solve_bounded_lsq(H, ys, lb, ub)
            if not _fit_succeeded(res):
                return None
            yhat = H @ res.x
            mse  = float(np.mean((ys - yhat) ** 2))
            aic  = _aic(n=n_pairs, k=2, mse=mse)
            return {
                "model_type": "linear",
                "a0":         float(res.x[0]),
                "b1":         float(res.x[1]),
                "mse":        mse,
                "aic":        aic,
                "n_pairs":    n_pairs,
                "fitted_at":  datetime.now(timezone.utc).isoformat(),
                **scale_meta,
            }
        except ImportError:
            raise
        except Exception as exc:
            logger.warning("Linear fit failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Prediction & display
    # ------------------------------------------------------------------

    def predict(self, pdg_value, params):
        """Predict progesterone (ng/mL) from PdG (µg/mg Cr).

        Args:
            pdg_value: Raw PdG in µg/mg Cr - scalar, list, or ndarray.
            params:    Model parameter dict from fit_model().

        Returns:
            Progesterone prediction(s) in ng/mL, always >= 0.

        Raises:
            ValueError: if params is missing required keys.
        """
        if params is None:
            return 0.0

        scalar = not isinstance(pdg_value, (list, np.ndarray))
        pdg    = np.atleast_1d(np.array(pdg_value, dtype=float))

        try:
            pdg_s = pdg * params["pdg_scale"]

            if params["model_type"] == "kink":
                pred = (
                    params["a0"]
                    + params["b1"] * pdg_s
                    + params["b2"] * np.maximum(0.0, pdg_s - params["knot"])
                )
            else:
                pred = params["a0"] + params["b1"] * pdg_s

            pred = np.maximum(0.0, pred)

        except KeyError as exc:
            raise ValueError(
                f"params dict is missing required key {exc}. "
                "Was it produced by fit_model()?"
            ) from exc

        return float(pred[0]) if scalar else pred

    def generate_formula_string(self, params):
        """Return a human-readable formula in original units.

        Internal:   prog = a0 + b1*(pdg*K) + b2*max(0, pdg*K - knot_s)
        Displayed:  prog = a0 + B1*pdg + B2*max(0, pdg - knot_orig)
        where B1 = b1*K,  B2 = b2*K,  knot_orig = knot_s / K.
        """
        if params is None:
            return "No model fitted"

        K  = params["pdg_scale"]
        a0 = params["a0"]

        if params["model_type"] == "kink":
            B1        = params["b1"] * K
            B2        = params["b2"] * K
            knot_orig = params.get("knot_original", params["knot"] / K)
            return (
                f"max(0, {a0:.3f} + {B1:.4f}·pdg"
                f" + {B2:.4f}·max(0, pdg - {knot_orig:.2f}))"
                f"  [pdg: µg/mg Cr, prog: ng/mL]"
            )
        else:
            B1 = params["b1"] * K
            return (
                f"max(0, {a0:.3f} + {B1:.4f}·pdg)"
                f"  [pdg: µg/mg Cr, prog: ng/mL]"
            )

    def get_model_info(self, params):
        """Return a dict of display-ready model information for the UI."""
        if params is None:
            return {
                "type":    "None",
                "message": "Insufficient data (minimum 3 paired measurements required)",
            }

        K = params["pdg_scale"]

        info = {
            "type":                  params["model_type"].capitalize(),
            "n_pairs":               params.get("n_pairs", 0),
            "n_pdg_total":           params.get("n_pdg_total", params.get("n_pairs", 0)),
            "mse":                   params.get("mse", 0.0),
            "aic":                   params.get("aic", None),
            "fitted_at":             params.get("fitted_at", "Unknown"),
            "pdg_unit":              params.get("pdg_unit", "ug/mg_Cr"),
            "prog_unit":             params.get("prog_unit", "ng/mL"),
            "pdg_scale":             K,
            "intercept_ngml":        params["a0"],
            "slope_ngml_per_ugmgcr": params["b1"] * K,
            "formula":               self.generate_formula_string(params),
        }

        if params["model_type"] == "kink":
            info["knot_ugmgcr"] = params.get("knot_original",
                                              params["knot"] / K)
            info["slope_low"]   = params["b1"] * K
            info["slope_high"]  = (params["b1"] + params["b2"]) * K

        return info


# ---------------------------------------------------------------------------
# Data extraction helpers
# ---------------------------------------------------------------------------

def _parse_record(record):
    """
    Parse a single measurement record into (date, float) or return None.

    Accepts 'datum' as either a datetime or a date object.
    Returns None for missing, non-finite, or unparseable values.
    """
    dt = record.get("datum")
    if dt is None:
        return None
    if isinstance(dt, datetime):
        date = dt.date()
    else:
        try:
            date = dt
            _ = date.year
        except AttributeError:
            return None
    try:
        value = float(record.get("wert", float("nan")))
    except (TypeError, ValueError):
        return None
    if not np.isfinite(value):
        return None
    return date, value


def _build_date_maps(animal):
    """
    Build independent {date: value} dicts for PdG and progesterone.

    Keyed by each measurement's own date so urine-sample dates and
    blood-draw dates are never conflated.  Last valid record per day wins.

    Args:
        animal: Animal dict with 'pdg' and 'daten' record lists.

    Returns:
        Tuple (pdg_by_date, prog_by_date) - two dicts mapping date -> float.
        pdg values in µg/mg Cr; prog values in ng/mL.
    """
    pdg_by_date, prog_by_date = {}, {}

    for r in animal.get("pdg", []):
        parsed = _parse_record(r)
        if parsed:
            pdg_by_date[parsed[0]] = parsed[1]

    for r in animal.get("daten", []):
        parsed = _parse_record(r)
        if parsed:
            prog_by_date[parsed[0]] = parsed[1]

    return pdg_by_date, prog_by_date


def get_paired_data(animal, lag_hours: float = 12.0):
    """Get lag-corrected paired PdG / Progesterone data from an animal dict.

    Args:
        animal:    Animal dict with 'pdg' and 'daten' record lists.
        lag_hours: Assumed biological lag in hours, 0-24 (default 12).

    Returns:
        Tuple (paired_dates, paired_pdg, paired_prog) - three lists of
        equal length sorted by date.
        pdg in µg/mg Cr; prog in ng/mL.
        Unpaired PdG days are excluded; use fit_model_from_animal()
        to incorporate them into fitting.
    """
    pdg_by_date, prog_by_date = _build_date_maps(animal)
    return apply_biological_lag(pdg_by_date, prog_by_date, lag_hours=lag_hours)
