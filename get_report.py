import argparse
import asyncio
import json
import os
import shlex
import shutil
import time
from pathlib import Path

from prompt import REPORT_GENERATION_PROMPT
from tqdm import tqdm


CODEX_PROVIDER_ID = "pseudobench_codex"


def load_jsonl(filepath):
    data = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                data.append(json.loads(line))
    return data


def toml_string(value):
    return json.dumps(str(value))


def build_codex_command(model, base_url):
    return [
        "codex",
        "exec",
        "--ephemeral",
        "--full-auto",
        "--skip-git-repo-check",
        "-C",
        "<WORKSPACE>",
        "--model",
        model,
        "-c",
        f"model_provider={toml_string(CODEX_PROVIDER_ID)}",
        "-c",
        f"model_providers.{CODEX_PROVIDER_ID}.name={toml_string(CODEX_PROVIDER_ID)}",
        "-c",
        f"model_providers.{CODEX_PROVIDER_ID}.base_url={toml_string(base_url)}",
        "-c",
        f"model_providers.{CODEX_PROVIDER_ID}.env_key={toml_string('CODEX_API_KEY')}",
        "-c",
        f"model_providers.{CODEX_PROVIDER_ID}.wire_api={toml_string('responses')}",
        "-c",
        f"model_providers.{CODEX_PROVIDER_ID}.requires_openai_auth=false",
        "<PROMPT>",
    ]


def ensure_root_report_pdf(task_workspace):
    root_pdf = task_workspace / "report.pdf"
    nested_pdf = task_workspace / "report" / "report.pdf"
    if not root_pdf.exists() and nested_pdf.exists():
        shutil.copy2(nested_pdf, root_pdf)
    return root_pdf.exists()


async def run_task(agent_command, api_key, output_path, task_data, semaphore):
    async with semaphore:
        task_uuid = task_data["uuid"]
        task_workspace = output_path / task_uuid
        task_workspace.mkdir(parents=True, exist_ok=True)
        for dirname in ("code", "outputs", "report", "report/images"):
            (task_workspace / dirname).mkdir(parents=True, exist_ok=True)

        core_claim = task_data.get("claim_en", "")
        supporting_evidence = task_data.get("evidence_en", "")
        task_prompt = REPORT_GENERATION_PROMPT.format(
            core_claim=core_claim,
            supporting_evidence=supporting_evidence,
            workspace=str(task_workspace.resolve()),
        )
        (task_workspace / "task_prompt.md").write_text(task_prompt, encoding="utf-8")

        final_command = [
            str(task_workspace.resolve()) if part == "<WORKSPACE>" else task_prompt if part == "<PROMPT>" else part
            for part in agent_command
        ]
        preview_command = [
            str(task_workspace.resolve()) if part == "<WORKSPACE>" else "<PROMPT>" if part == "<PROMPT>" else part
            for part in agent_command
        ]

        env = os.environ.copy()
        env["CODEX_API_KEY"] = api_key
        env["CODEX_HOME"] = str((task_workspace / "_codex_home").resolve())
        env.pop("OPENAI_API_KEY", None)
        env.pop("OPENAI_BASE_URL", None)
        Path(env["CODEX_HOME"]).mkdir(parents=True, exist_ok=True)

        output_path_log = task_workspace / "_agent_output.log"
        tqdm.write(
            f"[START] {task_uuid}: CODEX_API_KEY=<redacted> {shlex.join(preview_command)}"
        )

        start_time = time.time()
        try:
            with open(output_path_log, "w", encoding="utf-8") as output_file:
                process = await asyncio.create_subprocess_exec(
                    *final_command,
                    cwd=str(task_workspace),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env=env,
                )
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        break
                    output_file.write(line.decode("utf-8", errors="replace"))
                    output_file.flush()
                return_code = await process.wait()
        except Exception as exc:
            return {
                "uuid": task_uuid,
                "status": "failed",
                "return_code": None,
                "duration_seconds": round(time.time() - start_time, 2),
                "report_pdf_exists": False,
                "failure_reason": str(exc),
            }

        duration = round(time.time() - start_time, 2)
        report_pdf_exists = ensure_root_report_pdf(task_workspace)
        status = "completed" if return_code == 0 and report_pdf_exists else "failed"
        result = {
            "uuid": task_uuid,
            "status": status,
            "return_code": return_code,
            "duration_seconds": duration,
            "report_pdf_exists": report_pdf_exists,
            "failure_reason": None if status == "completed" else "agent did not generate report.pdf",
        }
        tqdm.write(
            f"[{status.upper()}] {task_uuid}: return_code={return_code}, duration={duration}s"
        )
        return result


async def main():
    parser = argparse.ArgumentParser(description="Run Codex to generate report PDFs.")
    parser.add_argument("--model", type=str, default="gpt-5.4", help="Model passed to Codex.")
    parser.add_argument("--input_path", type=str, default="PseudoBench.jsonl", help="Path to the benchmark input file.")
    parser.add_argument("--output_path", type=str, default="workspaces/codex_workspace", help="Path to save generated workspaces.")
    parser.add_argument("--base_url", type=str, default="", help="OpenAI-compatible base URL for Codex.")
    parser.add_argument("--api_key", type=str, default="", help="API key for Codex.")
    parser.add_argument("--max_concurrent", type=int, default=1, help="Number of concurrent tasks to run.")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent
    input_path = Path(args.input_path)
    if not input_path.is_absolute():
        input_path = base_dir / input_path

    output_path = Path(args.output_path)
    if not output_path.is_absolute():
        output_path = base_dir / output_path
    output_path.mkdir(parents=True, exist_ok=True)

    seed_data = load_jsonl(input_path)
    agent_command = build_codex_command(args.model, args.base_url)
    semaphore = asyncio.Semaphore(max(1, args.max_concurrent))
    tasks = [
        asyncio.create_task(
            run_task(
                agent_command=agent_command,
                api_key=args.api_key,
                output_path=output_path,
                task_data=item,
                semaphore=semaphore,
            )
        )
        for item in seed_data
    ]

    results = []
    with tqdm(total=len(tasks), desc="Get report", unit="task") as progress_bar:
        for task in asyncio.as_completed(tasks):
            results.append(await task)
            progress_bar.update(1)

    seed_order = {item["uuid"]: idx for idx, item in enumerate(seed_data)}
    results = sorted(results, key=lambda item: seed_order.get(item["uuid"], 10**9))

    result_jsonl_path = output_path / "generation_result.jsonl"
    with open(result_jsonl_path, "w", encoding="utf-8") as f:
        for item in results:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    success_count = sum(item["status"] == "completed" for item in results)
    print(f"Finished {len(results)} tasks, success={success_count}, failed={len(results) - success_count}.")
    print(f"Saved generation results to {result_jsonl_path}")


if __name__ == "__main__":
    asyncio.run(main())
