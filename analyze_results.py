import pandas as pd
import numpy as np
from scipy import stats
import matplotlib.pyplot as plt
import seaborn as sns
import os

# --- CONFIGURATION ---
CHROME_FILE = "windows/chrome_final_experiment_results.csv"
FIREFOX_FILE = "windows/firefox_final_experiment_results.csv"
METRICS = ["Energy_Joules", "Avg_Watts", "Peak_Watts", "Max_Temp_C", "Avg_RAM_GB", "Duration_Sec"]

# Custom colors for browsers
BROWSER_PALETTE = {"Chrome": "#4285F4", "Firefox": "#FF7139"}


def load_and_filter_data(filepath, label):
    """Loads CSV, filters for 'actual' rounds, and adds a Browser label."""
    if not os.path.exists(filepath):
        print(f"Error: File not found {filepath}")
        return None
    df = pd.read_csv(filepath)
    df = df[df['Round_Type'] == 'actual'].copy()
    df['Browser'] = label
    return df


def calculate_cohens_d(group1, group2):
    """Calculates Cohen's d effect size."""
    n1, n2 = len(group1), len(group2)
    var1, var2 = np.var(group1, ddof=1), np.var(group2, ddof=1)
    pooled_std = np.sqrt(((n1 - 1) * var1 + (n2 - 1) * var2) / (n1 + n2 - 2))
    return (np.mean(group1) - np.mean(group2)) / pooled_std if pooled_std != 0 else 0


def filter_outliers(df, metric):
    """Removes points where the deviation from the mean > 3 standard deviations."""
    # Group by both Test and Browser to get local means and stds
    grouped = df.groupby(['Test_Name', 'Browser'])[metric]
    means = grouped.transform('mean')
    stds = grouped.transform('std').fillna(0)  # Fill NaN with 0 for single-item groups

    # Filter condition: |x - mean| <= 3 * std
    mask = abs(df[metric] - means) <= 3 * stds
    return df[mask]


def analyze():
    chrome_df = load_and_filter_data(CHROME_FILE, "Chrome")
    firefox_df = load_and_filter_data(FIREFOX_FILE, "Firefox")

    if chrome_df is None or firefox_df is None:
        return

    combined_df = pd.concat([chrome_df, firefox_df])

    # Create a consistently sorted list of experiments for the X-axis
    sorted_tests = sorted(combined_df['Test_Name'].unique())

    summary_rows = []

    for test in sorted_tests:
        c_test = chrome_df[chrome_df['Test_Name'] == test]
        f_test = firefox_df[firefox_df['Test_Name'] == test]

        for metric in METRICS:
            c_vals = c_test[metric].dropna()
            f_vals = f_test[metric].dropna()

            if len(c_vals) < 2 or len(f_vals) < 2:
                continue

            t_stat, p_val = stats.ttest_ind(c_vals, f_vals)
            _, mwu_p = stats.mannwhitneyu(c_vals, f_vals, alternative='two-sided')
            _, c_norm_p = stats.shapiro(c_vals) if len(c_vals) >= 3 else (None, 1.0)
            _, f_norm_p = stats.shapiro(f_vals) if len(f_vals) >= 3 else (None, 1.0)
            _, var_p = stats.levene(c_vals, f_vals)
            d = calculate_cohens_d(c_vals, f_vals)

            summary_rows.append({
                "Test_Name": test,
                "Metric": metric,
                "Chrome_Mean": np.mean(c_vals),
                "Firefox_Mean": np.mean(f_vals),
                "Chrome_Std": np.std(c_vals, ddof=1),
                "Firefox_Std": np.std(f_vals, ddof=1),
                "Normality_p": min(c_norm_p, f_norm_p),
                "Variance_p": var_p,
                "Mann_Whitney_p": mwu_p,
                "T_Test_p": p_val,
                "Cohens_D": d,
                "Difference_%": ((np.mean(f_vals) - np.mean(c_vals)) / np.mean(c_vals)) * 100
            })

    summary_df = pd.DataFrame(summary_rows)
    summary_df.to_csv("analysis_summary.csv", index=False)

    # Generate Visualizations
    for metric in METRICS:
        # Filter outliers for the current metric before plotting
        plot_df = filter_outliers(combined_df.dropna(subset=[metric]), metric)

        plt.figure(figsize=(12, 6))

        # Enhanced boxplot with visible means, sorted X-axis, and distinct colors
        ax = sns.boxplot(
            data=plot_df,
            x='Test_Name',
            y=metric,
            hue='Browser',
            order=sorted_tests,
            palette=BROWSER_PALETTE,
            showmeans=True,
            meanprops={"marker": "D", "markerfacecolor": "white", "markeredgecolor": "black", "markersize": 7},
            flierprops={"marker": "x", "color": "gray", "alpha": 0.5}  # Makes remaining strict outliers less dominant
        )

        plt.title(f"{metric} Comparison by Browser and Test")
        plt.grid(axis='y', linestyle='--', alpha=0.7)

        # Optional: If Y-axis variance between tests is still too massive, uncomment the next line
        # ax.set_yscale('symlog') 

        plt.tight_layout()
        plt.savefig(f"{metric.lower()}_comparison_boxplot.png", dpi=150)
        plt.close()

    # Heatmap of Firefox vs Chrome Delta
    pivot_diff = summary_df.pivot(index="Test_Name", columns="Metric", values="Difference_%")
    # Reindex to match our sorted order
    pivot_diff = pivot_diff.reindex(sorted_tests)

    plt.figure(figsize=(10, 6))
    sns.heatmap(pivot_diff, annot=True, cmap="RdYlGn_r", center=0, fmt=".1f")
    plt.title("Percentage Increase in Firefox compared to Chrome (%)")
    plt.tight_layout()
    plt.savefig("metric_difference_heatmap.png", dpi=150)
    plt.close()

    # Energy (Joules) Distribution — violin + individual data points
    energy_df = combined_df[combined_df['Test_Name'] != 'control'].copy()
    energy_df = filter_outliers(energy_df, 'Energy_Joules')  # Apply filtering here too

    sorted_energy_tests = [t for t in sorted_tests if t != 'control']

    fig, axes = plt.subplots(1, 2, figsize=(16, 7), sharey=False)
    fig.suptitle("Energy Consumption (Joules) Distribution by Benchmark", fontsize=14, fontweight='bold')

    for ax, (browser, grp) in zip(axes, energy_df.groupby('Browser')):
        sns.violinplot(
            data=grp, x='Test_Name', y='Energy_Joules', hue='Test_Name',
            order=sorted_energy_tests, inner=None, palette='Set2', legend=False, ax=ax, alpha=0.6
        )
        sns.stripplot(
            data=grp, x='Test_Name', y='Energy_Joules',
            order=sorted_energy_tests, color='black', size=3, alpha=0.5, jitter=True, ax=ax
        )
        ax.set_title(browser, fontsize=12)
        ax.set_xlabel("Benchmark")
        ax.set_ylabel("Energy (Joules)")
        ax.grid(axis='y', linestyle='--', alpha=0.6)

    plt.tight_layout()
    plt.savefig("energy_joules_distribution.png", dpi=150)
    plt.close()

    print("Analysis summary saved to analysis_summary.csv")


if __name__ == "__main__":
    analyze()