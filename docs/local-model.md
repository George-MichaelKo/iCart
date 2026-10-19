# Local model experiment

This optional CLI adds a real model behind the existing application contracts. The extraction prompt embeds the sentence as JSON data; this improves the observed behavior but is not a prompt-injection security boundary. It uses fictional catalogs, does not save missions or personal preferences, and cannot send messages or purchase anything. The original explicit CLI and PostgreSQL worker still use the scripted adapter and need no model runtime. Attaching the model to the worker requires lease-aware execution first: the current job lease is 60 seconds, while two model calls can exceed that. Lease renewal, bounded cancellation, and recovery need explicit design before that integration.

## Components and request flow

```mermaid
sequenceDiagram
    actor User
    participant CLI as Natural-language CLI
    participant Intent as Intent validation
    participant LLM as Ollama / Qwen
    participant Service as Shopping service
    participant Gate as Tool gateway
    participant Core as Comparator and renderer
    User->>CLI: Shopping sentence
    CLI->>Intent: Text + application-owned identity
    Intent->>LLM: Extract a JSON draft (no tools)
    LLM-->>Intent: Items, constraints, exact item excerpts
    Intent->>Intent: Check shape, quantity evidence, units, missing fields
    alt Missing or reported unsupported requirements
        Intent-->>CLI: Draft + questions
    else Complete draft and --plan requested
        CLI->>Service: Validated mission
        Service->>Service: Search fixtures; issue scoped offer refs
        Service->>LLM: Propose compare_baskets using refs only
        LLM-->>Gate: Native structured tool call
        Gate->>Gate: Check allowlist, identity, full reference set
        Gate->>Core: Authorized comparison
        Core-->>CLI: Verified fictional shopping plan
    end
    CLI-->>User: Draft, questions/plan, model timings
```

`IntentModel` is a Python protocol: a contract any future model adapter can implement. `OllamaModel` translates that contract into Ollama HTTP requests. Ollama runs model weights on the computer; it is not the commerce orchestrator. `ShoppingService` remains the application coordinator. `ToolGateway` owns authorization. The domain comparator owns money and basket ranking. LangGraph and NVIDIA NeMo Agent Toolkit are not required by, or connected to, this experiment.

There are at most two generation calls per invocation: extraction, then optional tool proposal. There is no autonomous retry loop. The second call currently has no tool-selection choice and is useful for integration testing; it is not a latency improvement over the scripted path.

## Run locally

Install Ollama from its [official distribution](https://docs.ollama.com/macos). The development run used the official portable macOS release **0.33.3**. Start a dedicated server in a terminal, from the repository root:

```sh
mkdir -p .local/models
OLLAMA_HOST=127.0.0.1:11435 \
OLLAMA_MODELS="$PWD/.local/models" \
OLLAMA_NO_CLOUD=1 \
OLLAMA_NUM_PARALLEL=1 \
OLLAMA_MAX_LOADED_MODELS=1 \
LLAMA_ARG_DEVICE=none \
LLAMA_ARG_KV_OFFLOAD=0 \
ollama serve
```

The two `LLAMA_ARG_*` settings select CPU execution in the tested runtime's llama-server backend. They were necessary because Metal could not initialize in the development environment. The adapter also sets `num_gpu=0`. This is a reproducible CPU baseline, not an Apple GPU performance measurement. Other runtime versions may require different configuration.

In another terminal, download the explicit model profile once (approximately 2.5 GB), then run:

```sh
OLLAMA_HOST=127.0.0.1:11435 ollama pull qwen3:4b-instruct-2507-q4_K_M

PYTHONPATH=src python3 -m icart.interfaces.natural_cli \
  'Find 1 litre of milk, 12 eggs, and 1 kg of rice under $30 for pickup in 60601 within 6 hours.' \
  --plan --json

# Missing quantities remain questions; they are not defaulted.
PYTHONPATH=src python3 -m icart.interfaces.natural_cli \
  'Get milk under $30 for delivery in 60601 within 6 hours.'

# Run the opt-in synthetic development evaluations.
PYTHONPATH=src python3 evals/run_local_model.py --output .local/model-eval.json
```

For the portable runtime already downloaded during development, replace `ollama` in these commands with `.local/runtime/ollama-0.33.3/ollama`. Runtime files and model weights are ignored by Git. Ollama also creates local configuration/key files under `~/.ollama`. Stop the dedicated server with Ctrl-C when finished; no background startup service is installed by this project.

The adapter connects only to a literal loopback address, disables HTTP proxies and redirects, verifies local-model metadata, and never pulls models automatically. There is no hosted fallback or API key. The server's `OLLAMA_NO_CLOUD=1` setting is separate from the application's endpoint restriction; use both. The local runtime is trusted infrastructure, not a sandbox against malicious software already running on the computer.

## Validation and limits

- Model output must match the application-owned schema. Item evidence must appear verbatim in the request. Quantity evidence uses a small deterministic recognizer; unfamiliar phrasing produces clarification. Integer litres/kilograms are converted by code. Fractional units and many natural-language number forms are not supported yet.
- Identity is supplied by application context, never extracted from the sentence. The CLI identity is a demo identity, not authentication.
- Missing fields stop planning. Requirements the model reports as unsupported also stop planning. **A model can fail to detect a requirement**, or extract the wrong budget, product, ZIP, or deadline. Shape checks and matching excerpts do not prove complete semantic correctness. Always inspect the displayed draft.
- The second call receives temporary labels such as `offer_1`, not listing descriptions, prices, user identity, database access, or arbitrary tools. A call-local map resolves labels to the original random scoped IDs. Unknown labels fail; duplicates and omissions still reach the gateway checks. The labels are not authorization and cannot access another invocation. The gateway and renderer enforce reference ownership and grounded totals.
- Each HTTP operation uses a 120-second socket timeout and a 1 MiB response cap. Generation has a 768-token output cap and 4096-token context. This local profile accepts at most 24 offer references per call, below the core comparator's 200-offer limit. These are local safeguards, not a production end-to-end deadline or cancellation guarantee. There is no streaming response or multi-turn clarification state yet.
- CPU timings include runtime/model overhead and depend on load and caching. Local inference has no hosted API charge but uses RAM, disk, electricity, and time.

The six examples in `evals/local_intent.json` are development cases, including examples used while refining the prompt. They are not held out and do not measure general accuracy or adversarial robustness. The runner records model digest, runtime, source/dataset hashes, field checks, grounded totals, tokens, latency, and failures. It uses deterministic assertions rather than a paid LLM judge. A truncated response or unavailable runtime counts as a failure, not a successful refusal.

The initial generic `qwen3:4b` thinking profile produced incorrect unit conversion and one truncated response during development. The first Instruct evaluation passed four of six cases; its report is retained in `evals/local-model-initial.json`. Prompt packaging improved extraction, and a subsequent full-ID tool call failed gateway validation, motivating the call-local label mapping. Those observations motivated explicit model selection and backend-owned conversion; they are not a claim that all semantic errors are now eliminated. See [evaluation.md](evaluation.md) for recorded results and remaining release gates.

Primary references: [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs), [native tool calling](https://docs.ollama.com/capabilities/tool-calling), and [explicit Qwen model tags](https://ollama.com/library/qwen3/tags).
