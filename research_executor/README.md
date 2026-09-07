# Research CoScientist Public Executor

This namespace is the generic public execution substrate for the broader Research CoScientist. It is deliberately isolated from the existing Management Science engine in this repository.

## Security/privacy boundary

The public executor accepts only an **opaque `job_id`** and a non-sensitive public task selector. It must not receive the unpublished research question, hypothesis, manuscript, private dataset manifest, Drive credentials, tokens, or API-key values as workflow inputs.

Session 02 implements only `SMOKE_TEST`. Session 03 will add a private Google Drive bridge so the runner can fetch the detailed job manifest after dispatch using the existing credential-bearing infrastructure.

## Local smoke test

```bash
python -m unittest discover -s research_executor/tests -v
python -m research_executor.validate_dispatch --job-id COSCI-SMOKE-20260907-001 --task-type SMOKE_TEST
python -m research_executor.router --job-id COSCI-SMOKE-20260907-001 --task-type SMOKE_TEST --output out/status.json
```

The resulting `out/status.json` is classified `PUBLIC_EXECUTOR_SAFE`.
