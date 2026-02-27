import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import os

# --- CONFIGURATION ---
# Map folder names to display names for the plots
OS_FOLDERS = {
    "macOs": "macOS",
    "windows": "Windows",
    "linux": "Linux"
}

CHROME_FILENAME = "chrome_final_experiment_results.csv"
FIREFOX_FILENAME = "firefox_final_experiment_results.csv"

# The specific benchmarks you want to compare
TARGET_TESTS = ["motionmark", "speedometer", "jetstream"]


# Update these string values if your CSV uses different capitalization (e.g., "MotionMark")

def load_os_data():
    """Loops through OS folders and calculates the energy penalty."""
    results = []

    for folder, display_name in OS_FOLDERS.items():
        c_path = os.path.join(folder, CHROME_FILENAME)
        f_path = os.path.join(folder, FIREFOX_FILENAME)

        if not os.path.exists(c_path) or not os.path.exists(f_path):
            print(f"Warning: Missing data in {folder}/. Skipping.")
            continue

        c_df = pd.read_csv(c_path)
        f_df = pd.read_csv(f_path)

        # Filter for actual rounds
        c_df = c_df[c_df['Round_Type'] == 'actual']
        f_df = f_df[f_df['Round_Type'] == 'actual']

        for test in TARGET_TESTS:
            # Handle potential case-sensitivity in test names
            c_test = c_df[c_df['Test_Name'].str.lower() == test.lower()]
            f_test = f_df[f_df['Test_Name'].str.lower() == test.lower()]

            if c_test.empty or f_test.empty:
                continue

            c_energy = c_test['Energy_Joules'].dropna()
            f_energy = f_test['Energy_Joules'].dropna()

            if len(c_energy) == 0 or len(f_energy) == 0:
                continue

            c_mean = np.mean(c_energy)
            f_mean = np.mean(f_energy)

            # Penalty: Positive means Firefox uses MORE energy (Chrome wins)
            # Negative means Firefox uses LESS energy (Firefox wins)
            penalty_pct = ((f_mean - c_mean) / c_mean) * 100

            results.append({
                "OS": display_name,
                "Benchmark": test.capitalize(),  # e.g., "Motionmark"
                "Penalty_%": penalty_pct
            })

    return pd.DataFrame(results)


def generate_cross_os_plots():
    df = load_os_data()

    if df.empty:
        print("No data found to plot. Check your folder structures and file names.")
        return

    # 1. Heatmap: OS vs Benchmark
    pivot_df = df.pivot(index="OS", columns="Benchmark", values="Penalty_%")

    # Ensure rows are sorted consistently if all exist
    os_order = [name for name in OS_FOLDERS.values() if name in pivot_df.index]
    pivot_df = pivot_df.reindex(os_order)

    plt.figure(figsize=(8, 5))
    # RdYlGn_r: Green for low/negative (Firefox wins), Red for high/positive (Chrome wins)
    ax_heatmap = sns.heatmap(pivot_df, annot=True, cmap="RdYlGn_r", center=0, fmt=".1f",
                             cbar_kws={'label': 'Energy Penalty (%)'})
    plt.title("Firefox Energy Penalty vs Chrome (%)\nGreen = Firefox Wins | Red = Chrome Wins", pad=15)
    plt.ylabel("Operating System")
    plt.xlabel("Benchmark")
    plt.tight_layout()
    plt.savefig("cross_os_energy_heatmap.png", dpi=150)
    plt.close()

    print("Heatmap saved to cross_os_energy_heatmap.png")

    # 2. Slopegraph (Line plot connecting categorical OS points)
    plt.figure(figsize=(9, 6))

    # Custom markers and thicker lines to emphasize the "slope"
    sns.lineplot(
        data=df,
        x="OS",
        y="Penalty_%",
        hue="Benchmark",
        marker="o",
        markersize=10,
        linewidth=2.5,
        palette="Set1"
    )

    # Add a baseline at 0% (Parity)
    plt.axhline(0, color='black', linestyle='--', alpha=0.5, label='Parity (0%)')

    plt.title("Cross-OS Firefox Energy Penalty by Benchmark", pad=15)
    plt.ylabel("Firefox Energy Penalty (%) -> Higher means Chrome wins")
    plt.xlabel("Operating System")
    plt.grid(axis='y', linestyle='--', alpha=0.4)
    plt.legend(title="Benchmark", bbox_to_anchor=(1.05, 1), loc='upper left')

    plt.tight_layout()
    plt.savefig("cross_os_energy_slopegraph.png", dpi=150)
    plt.close()

    print("Slopegraph saved to cross_os_energy_slopegraph.png")


if __name__ == "__main__":
    generate_cross_os_plots()