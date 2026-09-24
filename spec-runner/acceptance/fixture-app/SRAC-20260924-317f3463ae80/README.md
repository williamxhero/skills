# Task CSV converter

Convert a UTF-8 task CSV file to deterministic JSON:

```text
python -m task_converter --input INPUT.csv --output OUTPUT.json
```

The command validates and serializes the complete result before atomically
replacing the destination. Handled failures return exit code `2`, write one
concise `error: ...` line to stderr, and preserve any existing destination.
