# Task converter

Convert a UTF-8 task CSV file to normalized JSON with:

```text
python -m task_converter --input INPUT.csv --output OUTPUT.json
```

The command validates and serializes the complete result before atomically
replacing the output path. Failures return exit code `2`, print one concise
`error: ...` line to standard error, and preserve an existing output file.
