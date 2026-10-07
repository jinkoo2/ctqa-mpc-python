# CTQA-MPC

Python port of the MPC CT geometry QA pipeline (C# `CTQA_MPC`). One process, two modes:

```
python -m ctqa_mpc                 # GUI
python -m ctqa_mpc --mode service  # MPC folder watcher
python -m ctqa_mpc analyze <case>
```

Clinic `settings.json` next to the app is gitignored. Start from `settings.sample.json`.

The watcher looks for import folders named `MMDDYYYY_MPC` (for example `10062026_MPC`), maps `{PatientLastName}_{StationName}` to a machine (for example `mpc_ctsim`), finds the 16 metal BBs, compares distances and axis angles to baseline, emails `report.html`, and publishes `{cases_dir}/{YYYYMMDD}_{HHmmss}_{Operator}`.

There is no elastix registration and no DocuForms2 step yet (`PostProcessing` is empty).

## CI / CD

GitHub Actions runs tests on Linux, Windows, and macOS. A `vMAJOR.MINOR.PATCH` tag builds desktop apps and a wheel:

- `CTQAMPC-<version>-windows-x64.exe`
- `CTQAMPC-<version>-linux-x64`
- `CTQAMPC-<version>-macos-arm64`
- `ctqa_mpc-<version>-py3-none-any.whl`
