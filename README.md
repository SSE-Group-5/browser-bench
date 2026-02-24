# Browser Energy Benchmark

This tool automates energy consumption measurements for web browsers while running standard benchmarks (Speedometer 3.1, JetStream 2, MotionMark 1.3.1) and a control idle test. It uses Selenium for automation and `pyEnergiBridge` for energy readings. Each benchmark runs until its results appear on screen (event-driven), except for the control test which uses a fixed 15-second timer.

## Setup

1. **Prerequisites**: Ensure you have Python and `uv` installed.
2. **Install Dependencies**:
   ```sh
   uv init
   uv sync
   ```
3. **Install Browser Drivers**:
   ```sh
   brew install --cask chromedriver
   # For Firefox: brew install --cask geckodriver
   ```
4. **Configure EnergiBridge**:
   Update `pyenergibridge_config.json` with the absolute path to your `energibridge` binary.
   ```json
   {
       "binary_path": "/path/to/energibridge"
   }
   ```
   For more setup instructions, see [pyEnergiBridge on GitHub](https://github.com/luiscruz/pyEnergiBridge).
   On Linux, after every reboot, the permissions need to be set again on the MSR files:
   ```sh
   sudo chgrp -R msr /dev/cpu/*/msr;
   sudo chmod g+r /dev/cpu/*/msr;
   ```

## Usage

Run the benchmark suite:

```sh
uv run main.py
```

## Configuration

Edit the `--- CONFIGURATION ---` section in `main.py` to adjust:

- `BROWSER`: Target browser (`"chrome"` or `"firefox"`).
- `ACTUAL_ROUNDS`: Number of measurement rounds per test (default: 30).
- `WARMUP_ROUNDS`: Number of warmup rounds before measurements (default: 5).
- `OUTPUT_CSV`: Base filename for results (output is saved as `[BROWSER]_[OUTPUT_CSV]`).

The order of actual test rounds is **randomized** to avoid ordering bias. A **30-second cooldown** is enforced between consecutive runs for thermal consistency. If a run fails, it is retried up to 2 times before being skipped.

> **⚠️ Warning:** A full run takes a significant amount of time depending on your configuration. Ensure your device is plugged in to power and keep the machine idle during tests for accurate results.

## Output Data

Results are saved to `[BROWSER]_final_experiment_results.csv` containing:

- **Timestamp**: ISO 8601 time of test.
- **Round_Type**: `warmup` or `actual`.
- **Test_Name**: Benchmark name (`control`, `speedometer`, `jetstream`, `motionmark`).
- **Energy_Joules**: Total energy consumed during the test.
- **Duration_Sec**: Exact duration of the test.
- **Avg_Watts**: Average power usage (Energy / Duration).
- **Peak_Watts**: Maximum instantaneous power recorded.
- **Max_Temp_C**: Maximum CPU temperature during the test.
- **Avg_Freq_MHz**: Average CPU frequency.
- **Avg_RAM_GB**: Average RAM usage.
