import csv
import json
import os
import platform
import random
import subprocess
import sys
import time
from datetime import datetime

import pandas as pd
from tqdm import tqdm

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# --- CONFIGURATION ---
OUTPUT_CSV = "final_experiment_results.csv"
WARMUP_ROUNDS = 5
ACTUAL_ROUNDS = 30
BROWSER = "chrome"  # "chrome" or "firefox"
MAX_RETRIES = 2
COOLDOWN_SEC = 2

CONFIG_PATH = "pyenergibridge_config.json"


def load_energibridge_path() -> str:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    path = cfg.get("binary_path")
    if not path:
        raise FileNotFoundError(f"`binary_path` missing in {CONFIG_PATH}")
    if not os.path.exists(path):
        raise FileNotFoundError(f"EnergiBridge binary not found at: {path}")
    return path


ENERGIBRIDGE_EXE = load_energibridge_path()


# --- METRIC EXTRACTION ---
def extract_metrics(temp_filename: str):
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

    # Peak Watts
    if "SYSTEM_POWER (Watts)" in df.columns:
        peak_watts = float(df["SYSTEM_POWER (Watts)"].max())
    elif "CPU_ENERGY (J)" in df.columns and "Delta" in df.columns:
        energy_diff = df["CPU_ENERGY (J)"].diff().dropna()
        delta_sec = df["Delta"].iloc[1:].values / 1_000_000  # µs → s
        valid = delta_sec > 0
        if valid.any():
            instantaneous_watts = energy_diff.values[valid] / delta_sec[valid]
            peak_watts = float(instantaneous_watts.max())
        else:
            peak_watts = 0.0
    else:
        peak_watts = 0.0

    # Max Temp
    temp_cols = [c for c in df.columns if "TEMP" in c.upper()]
    max_temp = float(df[temp_cols].max().max()) if temp_cols else 0.0

    # Avg Frequency
    freq_cols = [c for c in df.columns if "FREQUENCY" in c.upper()]
    avg_freq = float(df[freq_cols].mean().mean()) if freq_cols else 0.0

    # Avg RAM GB
    avg_ram_gb = float(df["USED_MEMORY"].mean() / (1024**3)) if "USED_MEMORY" in df.columns else 0.0

    return {
        "Peak_Watts": peak_watts,
        "Max_Temp": max_temp,
        "Avg_Freq_MHz": avg_freq,
        "Avg_RAM_GB": avg_ram_gb,
    }


# --- SELENIUM SETUP ---
def get_driver():
    if BROWSER == "chrome":
        opts = ChromeOptions()
        opts.add_argument("--incognito")
        opts.add_argument("--use-fake-ui-for-media-stream")
        opts.add_argument("--disable-extensions")
        opts.add_argument("--disable-background-networking")
        opts.add_argument("--disable-background-timer-throttling")
        opts.add_argument("--disable-renderer-backgrounding")
        driver = webdriver.Chrome(options=opts)
    elif BROWSER == "firefox":
        opts = FirefoxOptions()
        opts.add_argument("--private")
        opts.set_preference("media.navigator.permission.disabled", True)
        opts.set_preference("app.update.auto", False)
        opts.set_preference("app.update.enabled", False)
        driver = webdriver.Firefox(options=opts)
    else:
        raise ValueError(f"Unsupported browser: {BROWSER}")
    return driver


# --- ENERGIBRIDGE (CLI) ---
def _kill_process(proc: subprocess.Popen):
    try:
        if platform.system() == "Windows":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, text=True)
        else:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
    except Exception:
        pass


def _parse_summary(output_text: str):
    # Expected:
    # "Energy consumption in joules: X for Y sec of execution."
    for line in output_text.splitlines():
        line = line.strip()
        if line.lower().startswith("energy consumption in joules:"):
            joules_str = line.split("joules:")[1].split("for")[0].strip()
            secs_str = line.split("for")[1].split("sec")[0].strip()
            return float(joules_str), float(secs_str)
    return None, None

def measure_until_done(temp_csv: str, run_benchmark_fn):
    """
    Start EnergiBridge as a background process (writes temp CSV),
    run Selenium benchmark until completion, then signal EnergiBridge
    to exit cleanly so it prints the --summary line.

    Returns (energy_joules, duration_seconds).
    """

    # Clean old files
    temp_csv = os.path.abspath(temp_csv)
    stop_flag = os.path.abspath(temp_csv + ".stop")

    for p in (temp_csv, stop_flag):
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

    # Cross-platform "wait until stop_flag exists" command
    if platform.system() == "Windows":
        # PowerShell loop (cmd.exe has no while-loop)
        wait_cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            f"while (-not (Test-Path -LiteralPath '{stop_flag}')) "
            f"{{ Start-Sleep -Milliseconds 200 }}",
        ]
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        # POSIX shell loop
        wait_cmd = ["sh", "-c", f"while [ ! -f '{stop_flag}' ]; do sleep 0.2; done"]
        creationflags = 0

    cmd = [ENERGIBRIDGE_EXE, "--summary", "-o", temp_csv, *wait_cmd]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=creationflags,
    )

    # If EnergiBridge died immediately, fail fast with its output
    time.sleep(0.3)
    if proc.poll() is not None:
        out, err = proc.communicate(timeout=5)
        raise RuntimeError(f"EnergiBridge exited immediately.\n{out}\n{err}")

    t0 = time.time()
    try:
        # Run the benchmark (blocks until Selenium wait condition completes)
        run_benchmark_fn()
    finally:
        # Signal EnergiBridge to stop by creating the flag file
        try:
            with open(stop_flag, "w", encoding="utf-8"):
                pass
        except Exception:
            # If we can't create the flag, fall back to killing EnergiBridge
            _kill_process(proc)

    # Wait for EnergiBridge to exit cleanly and print summary
    try:
        out, err = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        _kill_process(proc)
        out, err = proc.communicate(timeout=10)

    combined = (out or "") + "\n" + (err or "")
    energy, dur = _parse_summary(combined)

    # Validate outputs
    if not os.path.exists(temp_csv) or os.path.getsize(temp_csv) < 50:
        raise RuntimeError(f"EnergiBridge did not produce temp CSV: {temp_csv}\n{combined}")

    if energy is None or dur is None or energy <= 0 or dur <= 0:
        wall = max(0.001, time.time() - t0)
        raise RuntimeError(
            f"Failed to parse EnergiBridge energy/duration.\n"
            f"Wall time was ~{wall:.2f}s.\n\nOutput:\n{combined}"
        )

    # Cleanup stop flag (optional)
    try:
        if os.path.exists(stop_flag):
            os.remove(stop_flag)
    except Exception:
        pass

    return energy, dur

def measure_window(seconds: int, temp_csv: str):
    if os.path.exists(temp_csv):
        try:
            os.remove(temp_csv)
        except Exception:
            pass

    if platform.system() == "Windows":
        anchor = ["cmd", "/c", f"timeout /t {seconds} /nobreak >nul"]
    else:
        anchor = ["sleep", str(seconds)]

    cmd = [ENERGIBRIDGE_EXE, "--summary", "-o", temp_csv, *anchor]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")

    if proc.returncode != 0:
        raise RuntimeError(f"EnergiBridge failed (exit {proc.returncode}).\n{out}")

    energy, dur = _parse_summary(out)
    if energy is None or dur is None or energy <= 0 or dur <= 0:
        raise RuntimeError(f"Failed to parse EnergiBridge summary / invalid energy.\n{out}")

    return energy, dur


# --- BENCHMARKS (variable duration) ---
def run_control(driver):
    driver.get("https://browserbench.org/")
    time.sleep(15)  # keep control fixed (or make it variable if you want)


def run_speedometer(driver):
    driver.get("https://browserbench.org/Speedometer3.1/")
    wait = WebDriverWait(driver, 600)
    start_btn = wait.until(EC.element_to_be_clickable((By.CLASS_NAME, "start-tests-button")))
    start_btn.click()
    wait.until(EC.visibility_of_element_located((By.ID, "result-number")))


def run_jetstream(driver):
    driver.get("https://browserbench.org/JetStream/")
    wait = WebDriverWait(driver, 1200)
    start_btn = wait.until(EC.element_to_be_clickable((By.CLASS_NAME, "button")))
    start_btn.click()
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "#result-summary.done")))


def run_motionmark(driver):
    driver.get("https://browserbench.org/MotionMark1.3.1/")
    wait = WebDriverWait(driver, 1200)
    start_btn = wait.until(EC.element_to_be_clickable((By.ID, "start-button")))
    start_btn.click()
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "#results.selected")))


# --- MAIN ---
def main():
    output_file = f"{BROWSER}_{OUTPUT_CSV}"
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
        with open(output_file, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(header)

    tests = ["control", "speedometer", "jetstream", "motionmark"]

    queue = [("warmup", random.choice(tests)) for _ in range(WARMUP_ROUNDS)]
    actual_tasks = []
    for t in tests:
        actual_tasks.extend([t] * ACTUAL_ROUNDS)
    random.shuffle(actual_tasks)
    queue.extend([("actual", t) for t in actual_tasks])

    print(f"Total runs scheduled: {len(queue)}")

    for i, (round_type, test_name) in enumerate(tqdm(queue, desc="Running Experiment")):
        tqdm.write(f"[{i + 1}/{len(queue)}] Running {round_type} -> {test_name}...")

        for attempt in range(1, MAX_RETRIES + 1):
            driver = None
            try:
                driver = get_driver()

                if test_name == "control":
                    temp_csv = "temp_control.csv"
                    energy, dur = measure_window(15, temp_csv)
                elif test_name == "speedometer":
                    temp_csv = "temp_speedometer.csv"
                    energy, dur = measure_until_done(temp_csv, lambda: run_speedometer(driver))
                elif test_name == "jetstream":
                    temp_csv = "temp_jetstream.csv"
                    energy, dur = measure_until_done(temp_csv, lambda: run_jetstream(driver))
                elif test_name == "motionmark":
                    temp_csv = "temp_motionmark.csv"
                    energy, dur = measure_until_done(temp_csv, lambda: run_motionmark(driver))
                else:
                    raise ValueError(f"Unknown test: {test_name}")

                metrics = extract_metrics(temp_csv)
                avg_watts = energy / dur if dur > 0 else 0.0

                with open(output_file, "a", newline="", encoding="utf-8") as f:
                    csv.writer(f).writerow([
                        datetime.now().isoformat(),
                        round_type,
                        test_name,
                        energy,
                        dur,
                        avg_watts,
                        metrics["Peak_Watts"],
                        metrics["Max_Temp"],
                        metrics["Avg_Freq_MHz"],
                        metrics["Avg_RAM_GB"],
                    ])

                tqdm.write(f"   -> {energy:.2f} J over {dur:.2f} s ({avg_watts:.2f} W)")
                break

            except Exception as e:
                if attempt < MAX_RETRIES:
                    tqdm.write(f"   -> Attempt {attempt} failed, retrying: {e}")
                    time.sleep(3)
                else:
                    tqdm.write(f"   -> FAILED after {MAX_RETRIES} attempts: {e}")

            finally:
                if driver:
                    driver.quit()

        time.sleep(COOLDOWN_SEC)


if __name__ == "__main__":
    main()