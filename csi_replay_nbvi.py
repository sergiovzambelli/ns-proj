#!/usr/bin/env python3
"""
CSI Replay with NBVI Subcarrier Selection
==========================================

Confronta la pipeline ORIGINALE (tutte le subcarrier) con quella NBVI
(solo le K più stabili, selezionate dai frame idle del CSV).

Flusso:
  1. Carica il CSV (con colonna 'label': 'idle' | 'movement')
  2. NBVI Calibration: usa tutti i frame idle per scegliere le K subcarrier
     più stabili (NBVI score basso) e non consecutive
  3. Replay: per ogni frame calcola le metriche con TUTTE le subcarrier
     (baseline) e con le subcarrier SELEZIONATE (nbvi), poi le confronta
  4. Output: tabella in console + grafico PNG di confronto

Uso:
  python csi_replay_nbvi.py recordings/experiment_20260328_231031.csv
  python csi_replay_nbvi.py recordings/experiment_20260328_231031.csv --k 8 --window 30
  python csi_replay_nbvi.py recordings/my_file.csv --output output/nbvi_compare.png
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
    """
    Carica il CSV.
    Restituisce (labels, amp_arrays, timestamps) dove amp_arrays[i] è l'array
    delle ampiezze grezze per il frame i.
    Le colonne amp vengono rilevate dinamicamente (amp_0, amp_1, ...).
    """
    labels = []
    amp_arrays = []
    timestamps = []

    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            amp_cols = sorted(
                [k for k in row.keys() if k.startswith("amp_") and k != "amp_mean"],
                key=lambda x: int(x.split("_")[1]),
            )
            # Alcuni file usano 'n_subs', altri 'n_sub'
            n_subs_key = "n_subs" if "n_subs" in row else "n_sub"
            n_sub = int(row[n_subs_key])
            # Prendi solo le prime n_sub colonne (le altre possono essere padding di 0)
            amps = np.array([float(row[c]) for c in amp_cols[:n_sub]], dtype=np.float64)
            amp_arrays.append(amps)
            labels.append(row.get("label", "").strip())
            timestamps.append(float(row.get("timestamp", 0.0)))

    return labels, amp_arrays, timestamps


# ─────────────────────────────────────────────
# 2. NBVI CALIBRATION
# ─────────────────────────────────────────────

def nbvi_calibrate(idle_amps: list[np.ndarray], k: int = 12, alpha: float = 0.5) -> np.ndarray:
    """
    Seleziona le K subcarrier più stabili usando l'indice NBVI.

    NBVI = α*(σ/μ²) + (1-α)*(σ/μ)   → più basso = più stabile

    Regole:
      - Noise gate: esclude subcarrier con ampiezza media < P25
      - Non-consecutive: spacing minimo di 2 tra subcarrier selezionate
        (diversità spettrale)

    Restituisce: array di indici ordinati delle subcarrier selezionate.
    """
    # Normalizza alla lunghezza minima comune (in caso di frame eterogenei)
    min_len = min(len(a) for a in idle_amps)
    cal = np.array([a[:min_len] for a in idle_amps], dtype=np.float64)  # shape (N, n_sub)
    n_sub = cal.shape[1]

    means = np.mean(cal, axis=0)   # media per subcarrier
    stds  = np.std(cal, axis=0)    # std per subcarrier

    # Noise gate: P25 delle medie
    p25 = np.percentile(means, 25)
    valid = means > p25            # maschera booleana

    # NBVI score (inf per subcarrier non valide)
    nbvi = np.full(n_sub, np.inf)
    safe = (means > 1e-9) & valid
    nbvi[safe] = (
        alpha * (stds[safe] / (means[safe] ** 2))
        + (1 - alpha) * (stds[safe] / means[safe])
    )

    # Ordina per NBVI crescente (migliori prime)
    ranked = np.argsort(nbvi)

    # Seleziona K non consecutive
    selected = []
    for idx in ranked:
        if nbvi[idx] == np.inf:
            break
        if not selected or abs(int(idx) - selected[-1]) >= 2:
            selected.append(int(idx))
        if len(selected) >= k:
            break

    # Fallback: se non ne troviamo abbastanza, prendi le top-K senza vincoli
    if len(selected) < k:
        selected = ranked[:k].tolist()

    return np.array(sorted(selected))


# ─────────────────────────────────────────────
# 3. METRICHE (usate sia per baseline che NBVI)
# ─────────────────────────────────────────────

def compute_stream_metrics(amp_arrays: list[np.ndarray], window: int,
                           selected_subs: np.ndarray | None = None) -> np.ndarray:
    """
    Calcola la Moving Variance del CV per ogni frame.

    Se selected_subs è None → usa TUTTE le subcarrier (baseline).
    Se selected_subs è un array di indici → usa solo quelle.

    Restituisce array di float (una voce per frame).
    """
    n = len(amp_arrays)
    cv_stream = np.zeros(n)
    mv_cv     = np.zeros(n)

    for i, amps in enumerate(amp_arrays):
        if selected_subs is not None:
            # Usa solo le subcarrier selezionate (gestisce frame più corti)
            valid = selected_subs[selected_subs < len(amps)]
            sub = amps[valid] if len(valid) > 0 else amps
        else:
            sub = amps

        mu = np.mean(sub)
        cv = np.std(sub) / (mu + 1e-9)
        cv_stream[i] = cv

        start = max(0, i - window + 1)
        mv_cv[i] = np.var(cv_stream[start : i + 1])

    return mv_cv


# ─────────────────────────────────────────────
# 4. VALUTAZIONE SEPARAZIONE
# ─────────────────────────────────────────────

def evaluate_separation(metric: np.ndarray, labels: list[str]) -> dict:
    """
    Calcola statistiche idle vs movement e la separazione (Fisher-like).
    Restituisce anche la soglia ottimale = mean_idle + 2*std_idle.
    """
    idle_mask = np.array([l == "idle"     for l in labels])
    move_mask = np.array([l == "movement" for l in labels])

    if not np.any(idle_mask) or not np.any(move_mask):
        return {}

    idle_vals = metric[idle_mask]
    move_vals = metric[move_mask]

    mean_idle = np.mean(idle_vals)
    std_idle  = np.std(idle_vals)
    mean_move = np.mean(move_vals)
    std_move  = np.std(move_vals)

    sep = abs(mean_move - mean_idle) / (std_idle + std_move + 1e-9)
    threshold = mean_idle + 2 * std_idle

    # Accuracy con soglia semplice
    predicted_idle = metric < threshold
    tp = np.sum(predicted_idle & idle_mask)
    tn = np.sum(~predicted_idle & move_mask)
    acc = (tp + tn) / len(labels)

    return {
        "mean_idle": mean_idle,
        "std_idle":  std_idle,
        "mean_move": mean_move,
        "std_move":  std_move,
        "separation": sep,
        "threshold": threshold,
        "accuracy": acc,
    }


# ─────────────────────────────────────────────
# 5. REPORT CONSOLE
# ─────────────────────────────────────────────

def print_report(baseline_stats: dict, nbvi_stats: dict, selected: np.ndarray, k: int):
    w = 60
    print("\n" + "═" * w)
    print("  NBVI Subcarrier Selection — Confronto Metriche")
    print("═" * w)
    print(f"  Subcarrier usate:  Baseline = {baseline_stats.get('_n_subs', '?')}  →  NBVI = {k}")
    print(f"  Indici selezionati: {selected.tolist()}")
    print()
    print(f"  {'Metrica':<22} {'Baseline':>12} {'NBVI':>12}  {'Δ':>8}")
    print("  " + "─" * (w - 4))

    def row(label, key, fmt=".6f", pct=False):
        bv = baseline_stats.get(key, 0.0)
        nv = nbvi_stats.get(key, 0.0)
        delta = nv - bv
        arrow = "▼" if delta < 0 else "▲"
        if pct:
            print(f"  {label:<22} {bv:>11.2%} {nv:>11.2%}  {arrow}{abs(delta):>6.2%}")
        else:
            print(f"  {label:<22} {bv:>{12}.{6}f} {nv:>{12}.{6}f}  {arrow}{abs(delta):>.6f}")

    row("μ idle",        "mean_idle")
    row("σ idle",        "std_idle")
    row("μ movement",    "mean_move")
    row("Separazione",   "separation")
    row("Soglia",        "threshold")
    row("Accuracy",      "accuracy", pct=True)

    sep_b = baseline_stats.get("separation", 0)
    sep_n = nbvi_stats.get("separation", 0)
    gain  = (sep_n - sep_b) / (sep_b + 1e-9) * 100

    print("  " + "─" * (w - 4))
    print(f"\n  Guadagno di separazione NBVI: {gain:+.1f}%")
    if gain > 0:
        print("  ✓ NBVI migliora la separazione idle/movement")
    else:
        print("  ✗ NBVI non ha migliorato su questo dataset (prova --k diverso)")
    print("═" * w + "\n")


# ─────────────────────────────────────────────
# 6. GRAFICO
# ─────────────────────────────────────────────

def make_plot(labels, baseline_mv, nbvi_mv, baseline_stats, nbvi_stats,
              selected, k, out_path: str):
    if not PLOT:
        print("matplotlib non disponibile — grafico saltato.")
        return

    has_labels = baseline_stats.get("threshold") is not None
    idle_mask = np.array([l == "idle"     for l in labels])
    move_mask = np.array([l == "movement" for l in labels])
    x = np.arange(len(labels))

    fig = plt.figure(figsize=(15, 9), dpi=130)
    gs  = gridspec.GridSpec(2, 2, figure=fig, hspace=0.45, wspace=0.3)

    # ── Subplot 1: Baseline time series ──
    ax1 = fig.add_subplot(gs[0, 0])
    ax1.plot(x, baseline_mv, color="steelblue", lw=0.6, label="Moving Var CV (baseline)")
    if has_labels:
        ax1.axhline(baseline_stats["threshold"], color="red", ls="--", lw=1, label="Soglia")
    _shade_labels(ax1, idle_mask, x)
    ax1.set_title(f"BASELINE — tutte le subcarrier ({baseline_stats.get('_n_subs','?')})")
    ax1.set_ylabel("Moving Var CV"); ax1.legend(fontsize=7); ax1.grid(alpha=0.3)

    # ── Subplot 2: NBVI time series ──
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.plot(x, nbvi_mv, color="darkorange", lw=0.6, label=f"Moving Var CV (NBVI k={k})")
    if has_labels:
        ax2.axhline(nbvi_stats["threshold"], color="red", ls="--", lw=1, label="Soglia")
    _shade_labels(ax2, idle_mask, x)
    ax2.set_title(f"NBVI — {k} subcarrier selezionate")
    ax2.set_ylabel("Moving Var CV"); ax2.legend(fontsize=7); ax2.grid(alpha=0.3)

    # ── Subplot 3: Distribuzione baseline ──
    ax3 = fig.add_subplot(gs[1, 0])
    if has_labels and np.any(idle_mask) and np.any(move_mask):
        ax3.hist(baseline_mv[idle_mask], bins=50, alpha=0.6, color="tab:blue",   label="Idle")
        ax3.hist(baseline_mv[move_mask], bins=50, alpha=0.6, color="tab:orange", label="Movement")
        ax3.axvline(baseline_stats["threshold"], color="red", ls="--", lw=1, label="Soglia")
        ax3.legend(fontsize=7)
    else:
        ax3.hist(baseline_mv, bins=60, color="steelblue", alpha=0.7)
    ax3.set_title("Distribuzione Baseline"); ax3.set_xlabel("Moving Var CV")
    ax3.grid(alpha=0.3)

    # ── Subplot 4: Distribuzione NBVI ──
    ax4 = fig.add_subplot(gs[1, 1])
    if has_labels and np.any(idle_mask) and np.any(move_mask):
        ax4.hist(nbvi_mv[idle_mask], bins=50, alpha=0.6, color="tab:blue",   label="Idle")
        ax4.hist(nbvi_mv[move_mask], bins=50, alpha=0.6, color="tab:orange", label="Movement")
        ax4.axvline(nbvi_stats["threshold"], color="red", ls="--", lw=1, label="Soglia")
        ax4.legend(fontsize=7)
    else:
        ax4.hist(nbvi_mv, bins=60, color="darkorange", alpha=0.7)
    ax4.set_title("Distribuzione NBVI"); ax4.set_xlabel("Moving Var CV")
    ax4.grid(alpha=0.3)

    fig.suptitle("Confronto Pipeline: Baseline vs NBVI Subcarrier Selection", fontsize=13)
    plt.savefig(out_path, bbox_inches="tight")
    print(f"\n  Grafico salvato in: {out_path}")


def _shade_labels(ax, idle_mask, x):
    """Sfondo azzurro=idle, arancione=movement."""
    changes = np.where(idle_mask[:-1] != idle_mask[1:])[0]
    starts = [0] + list(changes + 1)
    ends   = list(changes + 1) + [len(idle_mask)]
    for s, e in zip(starts, ends):
        color = "#d0e8ff" if idle_mask[s] else "#ffe0b2"
        ax.axvspan(s, e, alpha=0.4, color=color, zorder=0)


# ─────────────────────────────────────────────
# 7. MAIN
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Replay CSV con confronto Baseline vs NBVI Subcarrier Selection"
    )
    parser.add_argument("csv_file", help="Percorso al CSV registrato")
    parser.add_argument("--k",            type=int,   default=12,  help="Numero subcarrier NBVI (default: 12)")
    parser.add_argument("--alpha",        type=float, default=0.5, help="Bilanciamento NBVI: stabilità vs ampiezza (default: 0.5)")
    parser.add_argument("--window",       type=int,   default=30,  help="Finestra Moving Variance (default: 30)")
    parser.add_argument("--idle-seconds", type=float, default=None,
                        help="Usa i primi N secondi come idle per la calibrazione NBVI "
                             "(per CSV senza etichette label)")
    parser.add_argument("--output",  type=str,   default=None, help="Percorso output grafico PNG")
    args = parser.parse_args()

    # ── Load ──
    print(f"\n  Caricamento: {args.csv_file}")
    labels, amp_arrays, timestamps = load_csv(args.csv_file)
    print(f"  Frame totali: {len(labels)}  (idle: {labels.count('idle')}, movement: {labels.count('movement')})")
    n_subs = len(amp_arrays[0])
    print(f"  Subcarrier rilevate: {n_subs}")

    # ── Selezione frame idle per calibrazione NBVI ──
    if args.idle_seconds is not None:
        # Usa i primi N secondi come idle sintetico
        t0 = timestamps[0]
        idle_amps = [a for a, t in zip(amp_arrays, timestamps)
                     if t - t0 <= args.idle_seconds]
        print(f"  [NBVI] Idle sintetico: primi {args.idle_seconds}s → {len(idle_amps)} frame")
        # Costruisce anche le label sintetiche per la valutazione statistica e i plot
        #   (prime N secondi = idle, resto = movement)
        labels_eval = [
            "idle" if t - t0 <= args.idle_seconds else "movement"
            for t in timestamps
        ]
        print(f"  [Label sintetiche] idle={labels_eval.count('idle')}  movement={labels_eval.count('movement')}")
    else:
        idle_amps     = [a for a, l in zip(amp_arrays, labels) if l == "idle"]
        labels_eval   = labels

    if len(idle_amps) == 0:
        print("\nERRORE: nessun frame idle disponibile per la calibrazione NBVI.")
        print("  Usa --idle-seconds N per specificare quanti secondi iniziali usare come idle.")
        sys.exit(1)

    # ── NBVI Calibration ──
    print(f"  [NBVI] Calibrazione su {len(idle_amps)} frame idle (k={args.k}, alpha={args.alpha})...")
    selected = nbvi_calibrate(idle_amps, k=args.k, alpha=args.alpha)
    print(f"  [NBVI] Subcarrier selezionate: {selected.tolist()}")

    # ── Compute metrics ──
    print(f"\n  Calcolo metrica Baseline (tutte {n_subs} subcarrier)...")
    baseline_mv = compute_stream_metrics(amp_arrays, args.window, selected_subs=None)

    print(f"  Calcolo metrica NBVI ({args.k} subcarrier)...")
    nbvi_mv = compute_stream_metrics(amp_arrays, args.window, selected_subs=selected)

    # ── Evaluation ──
    baseline_stats = evaluate_separation(baseline_mv, labels_eval)
    baseline_stats["_n_subs"] = n_subs

    nbvi_stats = evaluate_separation(nbvi_mv, labels_eval)

    # ── Report ──
    print_report(baseline_stats, nbvi_stats, selected, args.k)

    # ── Plot ──
    if args.output is None:
        stem = Path(args.csv_file).stem
        args.output = f"recordings/{stem}_nbvi_k{args.k}.png"

    make_plot(labels_eval, baseline_mv, nbvi_mv, baseline_stats, nbvi_stats,
              selected, args.k, args.output)


if __name__ == "__main__":
    main()
