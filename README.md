# PseudoBench

This folder contains a minimal open-source version of PseudoBench.

- `PseudoBench.jsonl`: benchmark items
- `prompt.py`: prompts for report generation and evaluation
- `evaluate.py`: PDF evaluation script

## Prompt

`prompt.py` includes:

- `REPORT_GENERATION_PROMPT` for generating paper-style PDF reports
- three evaluation prompts for:
  - report generation quality
  - alignment with the original claim and evidence
  - pseudoscientific persuasiveness

## Generate PDF Reports

For each item, the generation system should create:

```text
workspaces/<agent_name>_workspace/<uuid>/report.pdf
```

## Evaluate PDFs

`evaluate.py` reads `PseudoBench.jsonl`, finds the corresponding `report.pdf`, and evaluates each PDF with a model API.

Example:

```bash
python evaluate.py \
  --agent_name codex \
  --input_path PseudoBench.jsonl \
  --judge_model_name gpt-5.4 \
  --base_url http://localhost:8000/v1 \
  --api_key YOUR_API_KEY \
  --max_concurrent 8
```

Results are saved to:

```text
results/<judge_model_name>/<agent_name>/result.jsonl
```
