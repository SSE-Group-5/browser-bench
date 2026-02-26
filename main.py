import csv
import os
import random
import time
from datetime import datetime

import pandas as pd
from pyEnergiBridge.api import EnergiBridgeRunner
from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from tqdm import tqdm

# --- CONFIGURATION ---
OUTPUT_CSV = "final_experiment_results.csv"
WARMUP_ROUNDS = 5
ACTUAL_ROUNDS = 30
BROWSER = "firefox"  # or "firefox"

# --- UTILITY FUNCTIONS ---


def extract_metrics(temp_filename):
    """
    Reads the EnergiBridge temp CSV and extracts summary statistics.

    Handles two different column layouts produced by EnergiBridge:
      - macOS / Windows: exposes SYSTEM_POWER (Watts) and CPU_TEMP_* directly.
      - Linux:           exposes cumulative CPU_ENERGY (J); Peak_Watts is
                         derived from successive energy deltas divided by the
                         Delta (µs) interval.  Temperature is not available
                         via MSR on Linux, so Max_Temp returns 0.
    """
    try:
        df = pd.read_csv(temp_filename)
    except Exception as e:
        print(f"  [extract_metrics] Could not read {temp_filename}: {e}")
        return {"Peak_Watts": 0, "Max_Temp": 0, "Avg_Freq_MHz": 0, "Avg_RAM_GB": 0}

    # ── Peak Watts ────────────────────────────────────────────────────────────
    if "SYSTEM_POWER (Watts)" in df.columns:
        # macOS / Windows path
        peak_watts = df["SYSTEM_POWER (Watts)"].max()
    elif "CPU_ENERGY (J)" in df.columns and "Delta" in df.columns:
        # Linux path: CPU_ENERGY (J) is cumulative; derive instantaneous power
        # from successive differences.  Delta is in microseconds.
        energy_diff = df["CPU_ENERGY (J)"].diff().dropna()
        delta_sec = df["Delta"].iloc[1:].values / 1_000_000  # µs → s
        valid = delta_sec > 0
        if valid.any():
            instantaneous_watts = energy_diff.values[valid] / delta_sec[valid]
            peak_watts = float(instantaneous_watts.max())
        else:
            peak_watts = 0
    else:
        peak_watts = 0

    # ── Max Temperature ───────────────────────────────────────────────────────
    temp_cols = [c for c in df.columns if "TEMP" in c.upper()]
    max_temp = float(df[temp_cols].max().max()) if temp_cols else 0

    # ── Average CPU Frequency (MHz) ───────────────────────────────────────────
    freq_cols = [c for c in df.columns if "FREQUENCY" in c.upper()]
    avg_freq = float(df[freq_cols].mean().mean()) if freq_cols else 0

    # ── Average RAM (GB) ──────────────────────────────────────────────────────
    avg_ram_gb = (
        float(df["USED_MEMORY"].mean() / (1024**3))
        if "USED_MEMORY" in df.columns
        else 0
    )

    return {
        "Peak_Watts": peak_watts,
        "Max_Temp": max_temp,
        "Avg_Freq_MHz": avg_freq,
        "Avg_RAM_GB": avg_ram_gb,
    }


# --- SELENIUM SETUP ---


def get_driver():
    """Configures browser with isolation flags to minimize background noise."""
    if BROWSER == "chrome":
        opts = ChromeOptions()
        opts.add_argument("--incognito")
        opts.add_argument("--no-sandbox")
        # Isolation: Prevent background updates and networking spikes
        opts.add_argument("--disable-extensions")
        opts.add_argument("--disable-background-networking")
        opts.add_argument("--disable-background-timer-throttling")
        opts.add_argument("--disable-renderer-backgrounding")
        opts.add_argument("--disable-client-side-phishing-detection")
        driver = webdriver.Chrome(options=opts)
    elif BROWSER == "firefox":
        opts = FirefoxOptions()
        opts.add_argument("--private")
        # Isolation: Disable auto-updates during measurement
        opts.set_preference("app.update.auto", False)
        opts.set_preference("app.update.enabled", False)
        driver = webdriver.Firefox(options=opts)
    else:
        raise ValueError(f"Unsupported browser: {BROWSER}")
    return driver


# --- BENCHMARK FUNCTIONS ---


def run_control(driver, runner, duration=15):
    """Static Control: Remains the only function using a fixed timer."""
    driver.get("https://browserbench.org/")
    runner.start(results_file="temp_control.csv")
    try:
        time.sleep(duration)
    finally:
        return runner.stop()


def run_speedometer(driver, runner):
    """Speedometer 3.1: Stops measurement once the score renders."""
    driver.get("https://browserbench.org/Speedometer3.1/")
    wait = WebDriverWait(driver, 600)  # 10-minute maximum wait

    start_btn = wait.until(
        EC.element_to_be_clickable((By.CLASS_NAME, "start-tests-button"))
    )

    runner.start(results_file="temp_speedometer.csv")
    try:
        start_btn.click()
        # Wait for the score-value element to become visible
        wait.until(EC.visibility_of_element_located((By.ID, "result-number")))
    finally:
        return runner.stop()


def run_jetstream(driver, runner):
    """JetStream 2: Stops measurement when the result summary table appears."""
    driver.get("https://browserbench.org/JetStream/")
    wait = WebDriverWait(driver, 1200)

    start_btn = wait.until(EC.element_to_be_clickable((By.CLASS_NAME, "button")))

    runner.start(results_file="temp_jetstream.csv")
    try:
        start_btn.click()
        # Wait for the result-summary ID to be populated
        wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#result-summary.done"))
        )
    finally:
        return runner.stop()


def run_motionmark(driver, runner):
    """MotionMark 1.3.1: Stops measurement when the final score is shown."""
    driver.get("https://browserbench.org/MotionMark1.3.1/")
    wait = WebDriverWait(driver, 1200)

    start_btn = wait.until(EC.element_to_be_clickable((By.ID, "start-button")))

    runner.start(results_file="temp_motionmark.csv")
    try:
        start_btn.click()
        # Wait for the presence of the final score value
        wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#results.selected"))
        )
    finally:
        return runner.stop()


# --- MAIN ORCHESTRATOR ---

MAX_RETRIES = 2


def main():
    runner = EnergiBridgeRunner()
    output_file = f"{BROWSER}_{OUTPUT_CSV}"

    # CSV Header with Metrics
    header = [
        "Timestamp",
        "Round_Type",
        "Test_Name",
        "Energy_Joules",
        "Duration_Sec",
        "Avg_Watts",
        "Peak_Watts",
        "Max_Temp_C",
        "Avg_Freq_MHz",
        "Avg_RAM_GB",
    ]

    if not os.path.exists(output_file):
        with open(output_file, "w", newline="") as f:
            csv.writer(f).writerow(header)

    tests = ["control", "speedometer", "jetstream", "motionmark"]

    # Generate execution queue
    queue = [("warmup", random.choice(tests)) for _ in range(WARMUP_ROUNDS)]

    # Add Actual Rounds
    actual_tasks = []
    for t in tests:
        actual_tasks.extend([t] * ACTUAL_ROUNDS)
    random.shuffle(actual_tasks)  # Randomize order

    for t in actual_tasks:
        queue.append(("actual", t))

    print(f"Total runs scheduled: {len(queue)}")

    # Execution loop
    for i, (round_type, test_name) in enumerate(tqdm(queue, desc="Running Experiment")):
        tqdm.write(f"[{i + 1}/{len(queue)}] Running {round_type} -> {test_name}...")

        for attempt in range(1, MAX_RETRIES + 1):
            driver = None
            try:
                driver = get_driver()
                energy, exec_time = 0.0, 0.0

                if test_name == "control":
                    (en, dur), tmp = run_control(driver, runner), "temp_control.csv"
                elif test_name == "speedometer":
                    (en, dur), tmp = (
                        run_speedometer(driver, runner),
                        "temp_speedometer.csv",
                    )
                elif test_name == "jetstream":
                    (en, dur), tmp = run_jetstream(driver, runner), "temp_jetstream.csv"
                elif test_name == "motionmark":
                    (en, dur), tmp = (
                        run_motionmark(driver, runner),
                        "temp_motionmark.csv",
                    )

                # Metric Extraction
                metrics = extract_metrics(tmp)
                avg_p = en / dur if dur > 0 else 0

                # Save to CSV
                with open(output_file, "a", newline="") as f:
                    csv.writer(f).writerow(
                        [
                            datetime.now().isoformat(),
                            round_type,
                            test_name,
                            en,
                            dur,
                            avg_p,
                            metrics["Peak_Watts"],
                            metrics["Max_Temp"],
                            metrics["Avg_Freq_MHz"],
                            metrics["Avg_RAM_GB"],
                        ]
                    )

                tqdm.write(f"   -> {en:.2f} J over {dur:.2f} s ({avg_p:.2f} W)")
                break  # Success, move to next test

            except Exception as e:
                # Ensure EnergiBridge stops if it was left running
                try:
                    runner.stop()
                except:
                    pass

                if attempt < MAX_RETRIES:
                    tqdm.write(f"   -> Attempt {attempt} failed, retrying: {e}")
                    time.sleep(3)
                else:
                    tqdm.write(f"   -> FAILED after {MAX_RETRIES} attempts: {e}")
            finally:
                if driver:
                    driver.quit()

        # Cooldown for Thermal Consistency
        time.sleep(30)


if __name__ == "__main__":
    main()
