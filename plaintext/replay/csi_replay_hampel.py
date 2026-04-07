#!/usr/bin/env python3
"""
CSI Replay with Hampel Filter
==============================

Confronta la pipeline ORIGINALE (CV grezzo) con quella HAMPEL-FILTERED
(outlier nel flusso di CV sostituiti con la mediana locale).

Il filtro di Hampel lavora sul **flusso temporale di CV** prima che venga
calcolata la Moving Variance. Per ogni valore controlla se si discosta
più di `threshold × MAD` dalla mediana della finestra locale e, in caso,
lo sostituisce con la mediana (reject spike).

  CV_grezzo → [Hampel] → CV_filtrato → Moving Var → Classificazione

Uso:
  python csi_replay_hampel.py recordings/final_session_01_video.csv --idle-seconds 14
  python csi_replay_hampel.py recordings/experiment.csv  --hampel-window 7 --hampel-sigma 3
"""

import argparse
import csv
import sys
from pathlib import Path

import numpy as np

try:
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    PLOT = True
except ImportError:
    PLOT = False


# ─────────────────────────────────────────────
# 1. CSV LOADING
# ─────────────────────────────────────────────

def load_csv(path: str) -> tuple[list[str], list[np.ndarray], list[float]]:
    labels, amp_arrays, timestamps = [], [], []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            amp_cols = sorted(
                [k for k in row.keys() if k.startswith("amp_") and k != "amp_mean"],
                key=lambda x: int(x.split("_")[1]),
            )
            n_subs_key = "n_subs" if "n_subs" in row else "n_sub"
            n_sub = int(row[n_subs_key])
            amps = np.array([float(row[c]) for c in amp_cols[:n_sub]], dtype=np.float64)
            amp_arrays.append(amps)
            labels.append(row.get("label", "").strip())
            timestamps.append(float(row.get("timestamp", 0.0)))
    return labels, amp_arrays, timestamps


# ─────────────────────────────────────────────
# 2. CV STREAM
# ─────────────────────────────────────────────

def compute_cv_stream(amp_arrays: list[np.ndarray]) -> np.ndarray:
    """Coefficiente di Variazione per ogni frame: σ/μ."""
    cv = np.zeros(len(amp_arrays))
    for i, amps in enumerate(amp_arrays):
        mu = np.mean(amps)
        cv[i] = np.std(amps) / (mu + 1e-9)
    return cv


# ─────────────────────────────────────────────
# 3. HAMPEL FILTER
# ─────────────────────────────────────────────

def hampel_filter(cv_stream: np.ndarray, window: int = 7, sigma: float = 3.0) -> tuple[np.ndarray, np.ndarray]:
    """
    Applica il filtro di Hampel al flusso di CV.

    Per ogni campione i:
      - prende una finestra centrata di ±half_w campioni
      - calcola mediana e MAD (Median Absolute Deviation)
      - se |cv[i] - mediana| > sigma × 1.4826 × MAD  →  sostituisce con mediana

    Restituisce (cv_filtrato, maschera_outlier).

    Parametri:
      window  : dimensione totale della finestra (deve essere dispari)
      sigma   : numero di deviazioni standard MAD per la soglia
    """
    n = len(cv_stream)
    filtered = cv_stream.copy()
    outlier_mask = np.zeros(n, dtype=bool)

    half_w = window // 2

    for i in range(n):
        lo = max(0, i - half_w)
        hi = min(n, i + half_w + 1)
        window_vals = cv_stream[lo:hi]

        median = np.median(window_vals)
        mad    = np.median(np.abs(window_vals - median))
        scaled_mad = 1.4826 * mad

        if scaled_mad > 1e-12 and abs(cv_stream[i] - median) > sigma * scaled_mad:
            filtered[i] = median
            outlier_mask[i] = True

    return filtered, outlier_mask


# ─────────────────────────────────────────────
# 4. MOVING VARIANCE
# ─────────────────────────────────────────────

def moving_var(cv_stream: np.ndarray, window: int) -> np.ndarray:
    n = len(cv_stream)
    mv = np.zeros(n)
    for i in range(n):
        start = max(0, i - window + 1)
        mv[i] = np.var(cv_stream[start:i+1])
    return mv


# ─────────────────────────────────────────────
# 5. VALUTAZIONE SEPARAZIONE
# ─────────────────────────────────────────────

def evaluate_separation(metric: np.ndarray, labels: list[str]) -> dict:
    idle_mask = np.array([l == "idle"     for l in labels])
    move_mask = np.array([l == "movement" for l in labels])
    if not np.any(idle_mask) or not np.any(move_mask):
        return {}

    idle_vals = metric[idle_mask]
    move_vals = metric[move_mask]
    mu_i, sig_i = np.mean(idle_vals), np.std(idle_vals)
    mu_m, sig_m = np.mean(move_vals), np.std(move_vals)
    sep    = abs(mu_m - mu_i) / (sig_i + sig_m + 1e-9)
    thresh = mu_i + 2 * sig_i

    pred_idle = metric < thresh
    acc = (np.sum(pred_idle & idle_mask) + np.sum(~pred_idle & move_mask)) / len(labels)

    return {
        "mean_idle": mu_i, "std_idle": sig_i,
        "mean_move": mu_m, "std_move": sig_m,
        "separation": sep, "threshold": thresh, "accuracy": acc,
    }


# ─────────────────────────────────────────────
# 6. REPORT CONSOLE
# ─────────────────────────────────────────────

def print_report(base_stats: dict, filt_stats: dict, n_subs: int,
                 n_outliers: int, n_total: int, hw: int, hs: float):
    w = 62
    print("\n" + "═" * w)
    print("  Hampel Filter — Confronto Metriche")
    print("═" * w)
    print(f"  Hampel window={hw}  sigma={hs}  →  outlier rigettati: {n_outliers}/{n_total} ({100*n_outliers/n_total:.2f}%)")
    print()
    print(f"  {'Metrica':<22} {'Baseline':>12} {'Hampel':>12}  {'Δ':>8}")
    print("  " + "─" * (w - 4))

    for label, key, pct in [
        ("μ idle",       "mean_idle",   False),
        ("σ idle",       "std_idle",    False),
        ("μ movement",   "mean_move",   False),
        ("Separazione",  "separation",  False),
        ("Soglia",       "threshold",   False),
        ("Accuracy",     "accuracy",    True),
    ]:
        bv = base_stats.get(key, 0.0)
        fv = filt_stats.get(key, 0.0)
        delta = fv - bv
        arrow = "▼" if delta < 0 else "▲"
        if pct:
            print(f"  {label:<22} {bv:>11.2%} {fv:>11.2%}  {arrow}{abs(delta):>6.2%}")
        else:
            print(f"  {label:<22} {bv:>12.6f} {fv:>12.6f}  {arrow}{abs(delta):.6f}")

    sep_gain = (filt_stats.get("separation", 0) - base_stats.get("separation", 0)) / (base_stats.get("separation", 1e-9)) * 100
    print("  " + "─" * (w - 4))
    print(f"\n  Guadagno separazione Hampshire: {sep_gain:+.1f}%")
    if sep_gain > 0:
        print("  ✓ Hampel migliora la separazione idle/movement")
    else:
        print("  ~ Separazione invariata o leggermente ridotta (normale se il segnale è già pulito)")
    print("═" * w + "\n")


# ─────────────────────────────────────────────
# 7. GRAFICO
# ─────────────────────────────────────────────

def make_plot(labels_eval: list[str], cv_raw: np.ndarray, cv_filtered: np.ndarray,
              mv_base: np.ndarray, mv_filt: np.ndarray, outlier_mask: np.ndarray,
              base_stats: dict, filt_stats: dict, hw: int, hs: float, out_path: str):
    if not PLOT:
        print("matplotlib non disponibile — grafico saltato.")
        return

    n = len(labels_eval)
    x = np.arange(n)
    idle_mask = np.array([l == "idle"     for l in labels_eval])
    move_mask = np.array([l == "movement" for l in labels_eval])
    has_stats = bool(base_stats)

    fig = plt.figure(figsize=(16, 10), dpi=130)
    gs  = gridspec.GridSpec(3, 2, figure=fig, hspace=0.5, wspace=0.3)

    # ── 1. CV raw ──
    ax1 = fig.add_subplot(gs[0, 0])
    ax1.plot(x, cv_raw, color="steelblue", lw=0.5, alpha=0.8, label="CV grezzo")
    # evidenzia outlier
    outlier_x = x[outlier_mask]
    ax1.scatter(outlier_x, cv_raw[outlier_mask], color="red", s=6, zorder=5, label=f"Outlier rigettati ({outlier_mask.sum()})")
    _shade(ax1, idle_mask, n)
    ax1.set_title("CV grezzo (prima di Hampel)")
    ax1.set_ylabel("CV"); ax1.legend(fontsize=7); ax1.grid(alpha=0.3)

    # ── 2. CV filtrato ──
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.plot(x, cv_filtered, color="darkorange", lw=0.5, alpha=0.8, label="CV dopo Hampel")
    _shade(ax2, idle_mask, n)
    ax2.set_title(f"CV dopo Hampel (window={hw}, σ={hs})")
    ax2.set_ylabel("CV"); ax2.legend(fontsize=7); ax2.grid(alpha=0.3)

    # ── 3. Moving Var CV – baseline ──
    ax3 = fig.add_subplot(gs[1, 0])
    ax3.plot(x, mv_base, color="steelblue", lw=0.6, label="Moving Var CV (baseline)")
    if has_stats and "threshold" in base_stats:
        ax3.axhline(base_stats["threshold"], color="red", ls="--", lw=1, label="Soglia")
    _shade(ax3, idle_mask, n)
    ax3.set_title("Moving Var CV — BASELINE (nessun filtro)")
    ax3.set_ylabel("Moving Var CV"); ax3.legend(fontsize=7); ax3.grid(alpha=0.3)

    # ── 4. Moving Var CV – Hampel ──
    ax4 = fig.add_subplot(gs[1, 1])
    ax4.plot(x, mv_filt, color="darkorange", lw=0.6, label="Moving Var CV (Hampel)")
    if has_stats and "threshold" in filt_stats:
        ax4.axhline(filt_stats["threshold"], color="red", ls="--", lw=1, label="Soglia")
    _shade(ax4, idle_mask, n)
    ax4.set_title("Moving Var CV — HAMPEL FILTERED")
    ax4.set_ylabel("Moving Var CV"); ax4.legend(fontsize=7); ax4.grid(alpha=0.3)

    # ── 5+6. Distribuzione Moving Var ──
    ax5 = fig.add_subplot(gs[2, 0])
    ax6 = fig.add_subplot(gs[2, 1])
    if has_stats and np.any(idle_mask) and np.any(move_mask):
        ax5.hist(mv_base[idle_mask], bins=50, alpha=0.6, color="tab:blue",   label="Idle")
        ax5.hist(mv_base[move_mask], bins=50, alpha=0.6, color="tab:orange", label="Movement")
        if "threshold" in base_stats:
            ax5.axvline(base_stats["threshold"], color="red", ls="--", lw=1, label="Soglia")
        ax5.legend(fontsize=7)

        ax6.hist(mv_filt[idle_mask], bins=50, alpha=0.6, color="tab:blue",   label="Idle")
        ax6.hist(mv_filt[move_mask], bins=50, alpha=0.6, color="tab:orange", label="Movement")
        if "threshold" in filt_stats:
            ax6.axvline(filt_stats["threshold"], color="red", ls="--", lw=1, label="Soglia")
        ax6.legend(fontsize=7)
    else:
        ax5.hist(mv_base, bins=60, color="steelblue", alpha=0.7)
        ax6.hist(mv_filt, bins=60, color="darkorange", alpha=0.7)

    ax5.set_title("Distribuzione Baseline"); ax5.set_xlabel("Moving Var CV"); ax5.grid(alpha=0.3)
    ax6.set_title("Distribuzione Hampel");   ax6.set_xlabel("Moving Var CV"); ax6.grid(alpha=0.3)

    fig.suptitle("Confronto Pipeline: Baseline vs Hampel Filter", fontsize=13)
    plt.savefig(out_path, bbox_inches="tight")
    print(f"  Grafico salvato in: {out_path}")


def _shade(ax, idle_mask, n):
    """Sfondo azzurro=idle, arancione=movement."""
    if not np.any(idle_mask) and not np.any(~idle_mask):
        return
    changes = np.where(idle_mask[:-1] != idle_mask[1:])[0]
    starts  = [0] + list(changes + 1)
    ends    = list(changes + 1) + [n]
    for s, e in zip(starts, ends):
        color = "#d0e8ff" if idle_mask[s] else "#ffe0b2"
        ax.axvspan(s, e, alpha=0.35, color=color, zorder=0)


# ─────────────────────────────────────────────
# 8. MAIN
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Replay CSV con confronto Baseline vs Hampel Filter sul flusso di CV"
    )
    parser.add_argument("csv_file", help="Percorso al CSV registrato")
    parser.add_argument("--idle-seconds",  type=float, default=None,
                        help="Usa i primi N secondi come idle (per CSV senza label)")
    parser.add_argument("--hampel-window", type=int,   default=7,
                        help="Dimensione finestra Hampel (dispari, default: 7)")
    parser.add_argument("--hampel-sigma",  type=float, default=3.0,
                        help="Soglia sigma Hampel (default: 3.0)")
    parser.add_argument("--mv-window",     type=int,   default=30,
                        help="Finestra Moving Variance (default: 30)")
    parser.add_argument("--output",        type=str,   default=None,
                        help="Percorso output PNG")
    args = parser.parse_args()

    # Assicura finestra dispari
    if args.hampel_window % 2 == 0:
        args.hampel_window += 1
        print(f"  [!] Hampel window arrotondata a dispari: {args.hampel_window}")

    # ── Load ──
    print(f"\n  Caricamento: {args.csv_file}")
    labels, amp_arrays, timestamps = load_csv(args.csv_file)
    print(f"  Frame totali: {len(labels)}  (idle: {labels.count('idle')}, movement: {labels.count('movement')})")
    print(f"  Subcarrier:   {len(amp_arrays[0])}")

    # ── Label sintetiche (se --idle-seconds) ──
    if args.idle_seconds is not None:
        t0 = timestamps[0]
        labels_eval = [
            "idle" if t - t0 <= args.idle_seconds else "movement"
            for t in timestamps
        ]
        print(f"  [Label sintetiche] idle={labels_eval.count('idle')}  movement={labels_eval.count('movement')}")
    else:
        labels_eval = labels

    # ── CV stream ──
    print("\n  Calcolo CV stream...")
    cv_raw = compute_cv_stream(amp_arrays)

    # ── Hampel ──
    print(f"  Applicazione Hampel filter (window={args.hampel_window}, σ={args.hampel_sigma})...")
    cv_filtered, outlier_mask = hampel_filter(cv_raw, window=args.hampel_window, sigma=args.hampel_sigma)
    print(f"  Outlier rigettati: {outlier_mask.sum()} / {len(cv_raw)} ({100*outlier_mask.mean():.2f}%)")

    # ── Moving Variance ──
    print(f"  Calcolo Moving Variance (window={args.mv_window})...")
    mv_base = moving_var(cv_raw,      args.mv_window)
    mv_filt = moving_var(cv_filtered, args.mv_window)

    # ── Statistiche ──
    base_stats = evaluate_separation(mv_base, labels_eval)
    filt_stats = evaluate_separation(mv_filt, labels_eval)

    # ── Report ──
    print_report(base_stats, filt_stats, len(amp_arrays[0]),
                 int(outlier_mask.sum()), len(cv_raw),
                 args.hampel_window, args.hampel_sigma)

    # ── Plot ──
    if args.output is None:
        stem = Path(args.csv_file).stem
        args.output = f"recordings/{stem}_hampel_w{args.hampel_window}.png"

    make_plot(labels_eval, cv_raw, cv_filtered, mv_base, mv_filt, outlier_mask,
              base_stats, filt_stats, args.hampel_window, args.hampel_sigma, args.output)


if __name__ == "__main__":
    main()
