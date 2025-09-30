# FreqAI Training Container

Use the dedicated compose file to launch a GPU-enabled container that runs
Freqtrade backtesting with FreqAI enabled. The service reuses the same
JetPack 6.2.1 image and mounts the project `user_data/` and `tools/` folders, so
artifacts (models, logs, plots) are written back to the host.

## Usage
Build and run the training container (defaults to `--timerange 20240101-20250930`):
```bash
docker compose -f docker/docker-compose.train.jetson.yml run --rm freqai-train
```

Debugging tips
- The training compose runs with increased verbosity (`-vv`) and writes a logfile to
  `user_data/logs/train-debug.log`. You can tail it while the job runs:
  `tail -f user_data/logs/train-debug.log`.
- To quickly validate strategy import independent of backtesting, run:
  ```bash
  docker compose -f docker/docker-compose.train.jetson.yml run --rm freqai-train \
    freqtrade test-strategy --config user_data/config.json \
    --strategy-path user_data/strategies --strategy MyRLStrategy \
    --freqaimodel ReinforcementLearner -vv
  ```

The default command executes:
```bash
freqtrade backtesting \
  --config user_data/config.json \
  --strategy MyRLStrategy \
  --freqaimodel ReinforcementLearner \
  --timerange ${TIMERANGE-20240101-20250930}
```

## Customizing the training run
Set `TIMERANGE` before invoking the service (default is `20240101-20250930`, provided via
compose variable substitution). To retrain on a different span:
```bash
TIMERANGE=20240401-20250930 docker compose -f docker/docker-compose.train.jetson.yml \
  run --rm freqai-train
```

To add further CLI flags, override the command while reusing the environment:
```bash
TIMERANGE=20240401-20250930 docker compose -f docker/docker-compose.train.jetson.yml \
  run --rm freqai-train \
  freqtrade backtesting --config user_data/config.json --strategy MyRLStrategy \
  --freqaimodel ReinforcementLearner \
  --timerange ${TIMERANGE-20240101-20250930} --max-open-trades 3
```

All outputs (trained models, TensorBoard logs, etc.) appear under
`user_data/freqaimodels/` and `user_data/logs/` on the host.
