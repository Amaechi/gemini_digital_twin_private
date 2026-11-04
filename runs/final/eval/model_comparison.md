# Model comparison (spec §12 S11)

| Model | Engine | Pass | Pending | Fail | Checks | Tool-call validity | Retries | Fallbacks | Overruns | Tokens | Runtime s |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| gemma4:12b-it-q4_K_M | llm | 8 | 3 | 0 | 46/46 | 94 % | 111 | 20 | 0 | 1089550 | 4166.7 |
| qwen3.6:27b | llm | 0 | 1 | 0 | 2/2 | 82 % | 17 | 7 | 0 | 126855 | 672.5 |
| qwen3.6:35b-a3b | llm | 0 | 0 | 1 | 1/2 | 97 % | 7 | 1 | 0 | 79061 | 167.3 |
| gemma4:26b-a4b | llm | 0 | 0 | 1 | 1/2 | 70 % | 14 | 7 | 0 | 101871 | 175.7 |
| qwen3.5:9b-q4_K_M | llm | 0 | 0 | 1 | 1/2 | 68 % | 17 | 7 | 0 | 83724 | 203.2 |
