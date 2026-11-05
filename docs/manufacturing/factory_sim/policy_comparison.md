| Policy | Profit incl. salvage ($) | ± std ($) | Shipped | Fill rate | Late unit-days | Output/day | Avg WIP | Flow time (h) | S5 FPY | EOL yield | Escape rate | Operator util. | Winder failures |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| heuristic (flex staffing) | 1,106 | 1,581 | 370 | 0.85 | 11 | 20.7 | 28.8 | 31.9 | 0.970 | 0.986 | 0.0052 | 0.86 | 0.1 |
| heuristic (zoned staffing) | -1,392 | 678 | 269 | 0.64 | 136 | 14.3 | 33.4 | 52.5 | 0.972 | 0.981 | 0.0058 | 0.62 | 0.1 |
| random | -14,165 | 705 | 125 | 0.31 | 840 | 6.5 | 12.5 | 43.9 | 0.951 | 0.977 | 0.0052 | 0.46 | 0.0 |
| PPO from scratch | -8,043 | 859 | 29 | 0.07 | 1500 | 1.5 | 5.7 | 72.8 | 0.984 | 0.984 | 0.0000 | 0.09 | 0.0 |
| residual PPO (on flex heuristic) | 1,049 | 1,473 | 368 | 0.84 | 10 | 20.4 | 28.5 | 32.0 | 0.970 | 0.983 | 0.0061 | 0.85 | 0.1 |
