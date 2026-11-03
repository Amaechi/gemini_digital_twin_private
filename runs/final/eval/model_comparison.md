# Model comparison (spec §12 S11)

| Model | Engine | Pass | Pending | Fail | Checks | Tool-call validity | Retries | Fallbacks | Tokens | Runtime s |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| gemma4:12b-it-q4_K_M | llm | 5 | 5 | 1 | 40/41 | 92 % | 114 | 26 | 1073346 | 3727.9 |
| qwen3.6:27b | llm | 0 | 1 | 0 | 2/2 | 82 % | 17 | 7 | 126855 | 672.5 |
| qwen3.6:35b-a3b | llm | 0 | 0 | 1 | 1/2 | 97 % | 7 | 1 | 79061 | 167.3 |
| gemma4:26b-a4b | llm | 0 | 0 | 1 | 1/2 | 70 % | 14 | 7 | 101871 | 175.7 |
| qwen3.5:9b-q4_K_M | llm | 0 | 0 | 1 | 1/2 | 68 % | 17 | 7 | 83724 | 203.2 |
