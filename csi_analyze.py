#!/usr/bin/env python3
"""CSI Signal Analyzer — compare metrics and find thresholds."""

import argparse
import csv
import sys
import numpy as np

try:
    import matplotlib.pyplot as plt
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False


def load_experiment(csv_path: str) -> tuple[list[dict], list[np.ndarray]]:
    """Load CSV. Returns (rows_metadata, amplitude_arrays)."""
    rows_metadata = []
    amplitude_arrays = []

    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Dynamically find amplitude columns
            amp_cols = [k for k in row.keys() if k.startswith("amp_") and k != "amp_mean"]
            amp_cols.sort(key=lambda x: int(x.split('_')[1]))

            amps = np.array([float(row[c]) for c in amp_cols], dtype=np.float64)
            amplitude_arrays.append(amps)

            meta = {
                "timestamp": float(row["timestamp"]),
                "seq": int(row["seq"]),
                "rssi": int(row["rssi"]),
                "label": row["label"],
                "amp_mean": float(row["amp_mean"])
            }
            rows_metadata.append(meta)

    return rows_metadata, amplitude_arrays


def compute_metrics(amplitude_arrays: list[np.ndarray], window: int) -> dict[str, np.ndarray]:
    """Compute 4 metrics for every frame."""
    n = len(amplitude_arrays)
    metrics = {
        "Amplitude Var": np.zeros(n),
        "CV (sigma/mu)": np.zeros(n),
        "Temporal Diff": np.zeros(n),
        "Moving Var CV": np.zeros(n)
    }

    cv_array = np.zeros(n)

    for i in range(n):
        amps = amplitude_arrays[i]

        # 1. Amplitude Variance
        var = np.var(amps)
        metrics["Amplitude Var"][i] = var

        # 2. CV
        mean = np.mean(amps)
        std = np.std(amps)
        cv = std / (mean + 1e-9)
        metrics["CV (sigma/mu)"][i] = cv
        cv_array[i] = cv

        # 3. Temporal Diff
        if i == 0:
            metrics["Temporal Diff"][i] = 0.0
        else:
            prev_amps = amplitude_arrays[i-1]
            diff = np.sqrt(np.mean((amps - prev_amps)**2)) / (mean + 1e-9)
            metrics["Temporal Diff"][i] = diff

        # 4. Moving Var CV
        start_idx = max(0, i - window + 1)
        metrics["Moving Var CV"][i] = np.var(cv_array[start_idx:i+1])

    return metrics


def find_best_metric(metrics: dict, labels: list[str]) -> tuple[str, float, float]:
    """Find which metric best separates 'idle' from 'movement'."""
    best_name = None
    best_sep = -1.0
    best_thresh = 0.0

    idle_mask = np.array([l == "idle" for l in labels])
    move_mask = np.array([l == "movement" for l in labels])

    if not np.any(idle_mask) or not np.any(move_mask):
        return list(metrics.keys())[0], 0.0, 0.0

    for name, values in metrics.items():
        idle_vals = values[idle_mask]
        move_vals = values[move_mask]

        mean_idle = np.mean(idle_vals)
        mean_move = np.mean(move_vals)
        std_idle = np.std(idle_vals)
        std_move = np.std(move_vals)

        sep = abs(mean_move - mean_idle) / (std_idle + std_move + 1e-9)

        if sep > best_sep:
            best_sep = sep
            best_name = name
            best_thresh = mean_idle + 2 * std_idle

    return best_name, best_thresh, best_sep


def print_table(metrics: dict, labels: list[str], best_name: str, csv_path: str):
    """Print the formatted console output."""
    n_total = len(labels)
    n_idle = labels.count("idle")
    n_move = labels.count("movement")
    
    print("═══════════════════════════════════════════════")
    print("  CSI Signal Analysis")
    print(f"  Input: {csv_path}")
    print(f"  Frames: {n_total} ({n_idle} idle, {n_move} movement)")
    print("═══════════════════════════════════════════════\n")
    
    print("Metric Comparison:")
    print("┌───────────────────┬───────────┬───────────┬───────────┬────────────┐")
    print("│ Metric            │ Idle (μ)  │ Move (μ)  │ Ratio     │ Separation │")
    print("├───────────────────┼───────────┼───────────┼───────────┼────────────┤")

    idle_mask = np.array([l == "idle" for l in labels])
    move_mask = np.array([l == "movement" for l in labels])
    
    best_thresh = 0.0
    best_sep = 0.0

    if not np.any(idle_mask) or not np.any(move_mask):
        print("│ Not enough data to compute separation metrics                        │")
    else:
        for name, values in metrics.items():
            idle_vals = values[idle_mask]
            move_vals = values[move_mask]
    
            mean_idle = np.mean(idle_vals)
            mean_move = np.mean(move_vals)
            std_idle = np.std(idle_vals)
            
            ratio = (mean_move / mean_idle) if mean_idle > 1e-9 else 0.0
            sep = abs(mean_move - mean_idle) / (np.std(idle_vals) + np.std(move_vals) + 1e-9)
            
            star = "★" if name == best_name else " "
            if name == best_name:
                best_thresh = mean_idle + 2 * std_idle
                best_sep = sep
                
            col_name = f"{name[:17]:<17}"
            col_idle = f"{mean_idle:8.4f}"
            col_move = f"{mean_move:8.4f}"
            col_ratio = f"{ratio:7.1f}x"
            col_sep = f"{sep:8.2f}"
            
            print(f"│ {col_name} │ {col_idle}  │ {col_move}  │ {col_ratio}  │ {col_sep} {star} │")

    print("└───────────────────┴───────────┴───────────┴───────────┴────────────┘\n")
    
    if np.any(idle_mask) and np.any(move_mask):
        print(f"★ Best metric: {best_name} (separation = {best_sep:.2f})")
        print(f"  Recommended threshold: {best_thresh:.4f}\n")
        print(f"  Values BELOW {best_thresh:.4f} → classify as IDLE/ABSENT")
        print(f"  Values ABOVE {best_thresh:.4f} → classify as MOVING\n")


def plot_results(metrics: dict, labels: list[str], best_name: str, best_thresh: float, out_path: str):
    """Generate a matplotlib plot with 2 subplots."""
    if not MATPLOTLIB_AVAILABLE:
        print(f"Warning: matplotlib not available. Skipping plot generation to {out_path}.")
        return

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), dpi=150)
    
    best_vals = metrics[best_name]
    idle_mask = np.array([l == "idle" for l in labels])
    move_mask = np.array([l == "movement" for l in labels])
    x = np.arange(len(best_vals))
    
    # Subplot 1: Time Series
    ax1.plot(x, best_vals, color='black', linewidth=1)
    
    # Plot backgrounds based on labels
    # Using blocks to make contiguous backgrounds
    changes = np.where(idle_mask[:-1] != idle_mask[1:])[0]
    starts = [0] + list(changes + 1)
    ends = list(changes + 1) + [len(labels)]
    
    for s, e in zip(starts, ends):
        color = '#d0e8ff' if idle_mask[s] else '#ffe0b2'
        ax1.axvspan(s, e, alpha=0.5, color=color, zorder=0)

    ax1.axhline(best_thresh, color='red', linestyle='--', label='Threshold')
    ax1.set_title(f"Best Metric: {best_name} over time")
    ax1.set_xlabel("Frame Index")
    ax1.set_ylabel(best_name)
    ax1.legend(loc="upper right")
    
    # Subplot 2: Distribution
    if np.any(idle_mask):
        ax2.hist(best_vals[idle_mask], bins=30, alpha=0.6, color='tab:blue', label='Idle')
    if np.any(move_mask):
        ax2.hist(best_vals[move_mask], bins=30, alpha=0.6, color='tab:orange', label='Movement')
        
    ax2.axvline(best_thresh, color='red', linestyle='--', label='Threshold')
    ax2.set_title("Idle vs Movement Distribution")
    ax2.set_xlabel(best_name)
    ax2.set_ylabel("Count")
    ax2.legend()
    
    fig.tight_layout()
    plt.savefig(out_path)
    print(f"Saved analysis plot to {out_path}")


def run_self_test():
    """Generate synthetic CSV, run analysis, verify results."""
    print("Running self-test...")
    import os
    import random
    
    csv_path = "/tmp/csi_analysis_test.csv"
    
    # 1. Generate synthetic CSV
    with open(csv_path, 'w', newline='') as f:
        writer = csv.writer(f)
        header = ["timestamp", "seq", "rssi", "noise_floor", "channel", "node_id", "n_subs", "amp_mean", "label"]
        header.extend([f"amp_{i}" for i in range(56)])
        writer.writerow(header)
        
        # 100 idle frames
        for i in range(100):
            row = [1.0, i, -50, -90, 6, 1, 56, 10.0, "idle"]
            amps = [10.0 + random.uniform(0, 1.0) for _ in range(56)]
            row.extend(amps)
            writer.writerow(row)
            
        # 100 movement frames
        for i in range(100, 200):
            row = [2.0, i, -50, -90, 6, 1, 56, 10.0, "movement"]
            amps = [10.0 + random.uniform(0, 8.0) for _ in range(56)]
            row.extend(amps)
            writer.writerow(row)
            
    # 2. Run analysis
    try:
        rows, amps = load_experiment(csv_path)
        labels = [r["label"] for r in rows]
        metrics = compute_metrics(amps, window=20)
        best_name, best_thresh, best_sep = find_best_metric(metrics, labels)
        
        checks = 0
        if len(rows) == 200 and len(amps) == 200:
            checks += 1
            print("✓ CSV loaded correctly")
            
        if all(len(v) == 200 for v in metrics.values()):
            checks += 1
            print("✓ Metrics computed correctly")
            
        if best_sep > 1.0:
            checks += 1
            print(f"✓ Separation is good ({best_sep:.2f})")
            
        idle_mask = np.array([l == "idle" for l in labels])
        move_mask = np.array([l == "movement" for l in labels])
        mean_idle = np.mean(metrics[best_name][idle_mask])
        mean_move = np.mean(metrics[best_name][move_mask])
        
        if (mean_idle < best_thresh < mean_move) or (mean_move < best_thresh < mean_idle):
            checks += 1
            print("✓ Threshold is sensible")
            
        if MATPLOTLIB_AVAILABLE:
            plot_path = "/tmp/csi_analysis_plot.png"
            plot_results(metrics, labels, best_name, best_thresh, plot_path)
            if os.path.exists(plot_path):
                checks += 1
                print("✓ Plot generated correctly")
            total_checks = 5
        else:
            total_checks = 4
            
        if checks == total_checks:
            print(f"PASS ({checks}/{total_checks})")
            sys.exit(0)
        else:
            print(f"FAIL ({checks}/{total_checks})")
            sys.exit(1)
            
    except Exception as e:
        print(f"Error during self-test: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="CSI Signal Analyzer")
    parser.add_argument("csv_file", nargs='?', help="Path to experiment CSV")
    parser.add_argument("--window", type=int, default=20, help="Window size for moving variance")
    parser.add_argument("--output-plot", type=str, default="recordings/analysis.png", help="Path to save plot")
    parser.add_argument("--self-test", action="store_true", help="Run self-test with synthetic data")
    
    args = parser.parse_args()
    
    if args.self_test:
        run_self_test()
        return
        
    if not args.csv_file:
        parser.print_help()
        sys.exit(1)
        
    try:
        rows, amps = load_experiment(args.csv_file)
    except Exception as e:
        print(f"Failed to load {args.csv_file}: {e}")
        sys.exit(1)
        
    if len(rows) == 0:
        print("CSV dataset is empty.")
        sys.exit(1)
    
    labels = [r["label"] for r in rows]
    all_same = all(l == labels[0] for l in labels)
    if all_same:
        print(f"Warning: all rows have the same label '{labels[0]}'. Separation metrics will be 0.")
        
    metrics = compute_metrics(amps, args.window)
    best_name, best_thresh, best_sep = find_best_metric(metrics, labels)
    
    print_table(metrics, labels, best_name, args.csv_file)
    plot_results(metrics, labels, best_name, best_thresh, args.output_plot)


if __name__ == "__main__":
    main()
