# Claim extraction: LangGraph + evals + cost (research, 2026-11-08)

## TL;DR
- Pipeline: **LangGraph** `StateGraph` + `ChatAnthropic.with_structured_output` + `AsyncSqliteSaver`. Code below was run with a fake LLM: retry loop and crash-resume work. [Certain]
- Evals: **LangSmith**. LangGraph traces it with 2 env vars. `client.evaluate()` + the experiment compare view gives "prompt v1 vs v2, per-field scores" with no extra code. [Likely fastest]
- Cost: 10k posts on `claude-haiku-5-5` costs about **$2 standard** and **~$1 batch**. The $25 budget is not the constraint. Do not use the Batches API: it saves about $1 and breaks the retry loop. [Likely]

## 1. Versions (PyPI, checked 2026-11-08) [Certain]
| package | version | released |
|---|---|---|
| langgraph | 1.2.20 | 2026-11-05 |
| langchain-anthropic | 1.7.10 | 2026-11-04 |
| langchain-core | 1.7.6 | 2026-11-05 |
| langgraph-checkpoint-sqlite | 3.1.3 | 2026-10-30 |
| anthropic | 2.6.0 | 2026-11-05 |
| langsmith | 0.14.17 | 2026-11-02 |

Add (from repo root): `uv add langgraph langchain-anthropic langgraph-checkpoint-sqlite aiosqlite langsmith`. Imports verified: `from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver` (sync: `langgraph.checkpoint.sqlite.SqliteSaver`), `from langgraph.types import RetryPolicy`, `from langchain_core.callbacks import get_usage_metadata_callback`.

## 2. Graph: extract → validate → (retry | write)
Tested copy: `/tmp/claude-1001/-home-vince-Vincent/ada349be-30b0-448a-be47-bb27e756a129/scratchpad/lg/extract_graph.py` (+ `test_fake.py`).
```python
import operator
from functools import lru_cache
from typing import Annotated, Literal, TypedDict
from pydantic import BaseModel, Field
from langchain_anthropic import ChatAnthropic
from langchain_core.callbacks import get_usage_metadata_callback
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import RetryPolicy

class Claim(BaseModel):
    settlement: str = Field(description="Settlement name exactly as written in the post")
    settlement_lemma: str = Field(description="Nominative form, for the gazetteer")
    state: Literal["captured", "contested", "repelled", "liberated", "advancing"]
    actor: Literal["RU", "UA", "unknown"]
    modality: Literal["asserted", "reported", "denied", "speculative"]
    event_date: str | None = Field(None, description="YYYY-MM-DD or null")
    evidence: list[str] = Field(description="Verbatim substrings copied from the post")
class Claims(BaseModel):
    claims: list[Claim]

SYSTEM = "...2k-token instructions + few-shot (frozen text: no dates/ids, or the cache breaks)..."
GAZETTEER = {"покровськ": "Pokrovsk", "покровск": "Pokrovsk"}   # stub
@lru_cache
def get_llm(model: str):  # do NOT set effort=/thinking= (see 4.3)
    return ChatAnthropic(model=model, max_tokens=2048, max_retries=6).with_structured_output(Claims)

class S(TypedDict):
    post_id: str; text: str; claims: list[dict]; errors: list[str]; status: str
    attempts: Annotated[int, operator.add]

async def extract(s: S, config: RunnableConfig):
    c = config["configurable"]  # prompt + model come from config -> A/B in evals
    msgs = [{"role": "system", "content": [{"type": "text", "text": c.get("system", SYSTEM),
             "cache_control": {"type": "ephemeral"}}]},
            {"role": "user", "content": f"<post>{s['text']}</post>"}]
    if s.get("errors"):
        msgs.append({"role": "user", "content": f"Previous output:\n{s['claims']}\n"
                     "Fix these errors and return the full list again:\n" + "\n".join(s["errors"])})
    out = await get_llm(c.get("model", "claude-haiku-5-5")).ainvoke(msgs)
    return {"claims": [x.model_dump() for x in out.claims], "attempts": 1}

def validate(s: S):
    errs = []
    for i, c in enumerate(s["claims"]):
        errs += [f"claim {i}: evidence {e!r} is not an exact substring of the post"
                 for e in c["evidence"] if e not in s["text"]]
        if c["settlement_lemma"].lower().strip() not in GAZETTEER:
            errs.append(f"claim {i}: settlement {c['settlement_lemma']!r} not in gazetteer")
    return {"errors": errs}

def route(s: S):
    return "write" if not s["errors"] or s["attempts"] >= 3 else "extract"  # 1 try + 2 retries

def write(s: S):  # store_client post here: deterministic ids -> re-post is a no-op
    return {"status": "ok" if not s["errors"] else "failed"}

g = StateGraph(S)
g.add_node("extract", extract, retry_policy=RetryPolicy(max_attempts=4))  # 429/5xx/network
g.add_node("validate", validate); g.add_node("write", write)
g.add_edge(START, "extract"); g.add_edge("extract", "validate")
g.add_conditional_edges("validate", route, ["extract", "write"]); g.add_edge("write", END)

async def run(posts: list[dict], concurrency: int = 16):
    async with AsyncSqliteSaver.from_conn_string("checkpoints.db") as saver:
        app = g.compile(checkpointer=saver)
        todo, cfgs = [], []
        for p in posts:
            cfg = {"configurable": {"thread_id": p["id"]}, "max_concurrency": concurrency}
            st = await app.aget_state(cfg)
            if st.values.get("status"): continue          # done in an earlier run -> skip
            todo.append(None if st.next else {"post_id": p["id"], "text": p["text"], "errors": []})
            cfgs.append(cfg)                              # None = resume from last checkpoint
        with get_usage_metadata_callback() as cb:
            res = await app.abatch(todo, cfgs, return_exceptions=True)
        print(cb.usage_metadata)  # per model: input/output tokens, cache_read, cache_creation
        return res
```
- `thread_id = post id` gives resume. A second run over the same posts made 0 LLM calls in the test. [Certain]
- `abatch` reads `max_concurrency` from `configs[0]` only (checked in `langchain_core` source). [Certain] Run it in chunks of ~500 posts. A list of 10k coroutines is fine, but chunks give you progress output and a cost print after each chunk.
- Payload check: the system block keeps `cache_control`. `tool_choice={"type":"tool","name":"Claims"}` is forced. No `temperature` or `thinking` is sent. [Certain] Do not set `temperature`: Haiku 5.5 returns 400 for non-default sampling params.
- `checkpoints.db` is derived data. Keep it in `trust/`, not in `records.db`.

## 3. Eval tooling for the 60-post gold set
| tool | setup | per-field scores | compare v1 vs v2 UI | traces | data leaves VM | verdict |
|---|---|---|---|---|---|---|
| **LangSmith** | account + `LANGSMITH_API_KEY`, `LANGSMITH_TRACING=true` | Python evaluator functions, 1 key per field | yes, built in (Compare experiments, diff per example) | automatic for LangGraph nodes | yes (US default; EU region exists [Likely]) | **pick** |
| Langfuse cloud (Hobby) | account + keys + `langfuse.langchain.CallbackHandler` in config | `run_experiment(evaluators=...)` | yes | yes, via callback | yes (US/EU/JP) | close 2nd |
| Langfuse self-host | docker-compose (Postgres + ClickHouse + Redis + S3/minio) | same | same | same | no | too much setup for 11 h |
| promptfoo | `npx promptfoo`, YAML config, Python provider for the graph | custom JS/Python asserts | local web viewer, good matrix view | weak (no node-level graph traces) | no | good for prompt-only A/B. Our retry loop makes it awkward. |
| DeepEval | pytest-style | mostly LLM-judge metrics; custom metric = class | needs Confident AI cloud | via Confident AI | yes | wrong fit (judge-centric) |
| Inspect AI | Task/Solver/Scorer | custom scorer, multi-value | `inspect view` local log viewer | own logs, not LangGraph | no | strong tool, more concepts to learn |

Free tiers [Certain, pricing pages 2026-11-08]: **LangSmith Developer** is $0, 1 seat, 5k base traces/month, 14-day retention, pay-as-you-go above that. **Langfuse Hobby** is 50k units/month (hard cap), 2 users, 30-day access, and includes datasets, experiments and LLM-judge.

**Trace budget:** 1 graph run = 1 trace. 60 gold posts × ~20 experiments = 1.2k traces, which fits easily. The bulk 10k run does not fit in the free 5k traces: **set `LANGSMITH_TRACING=false` for bulk runs.** [Certain]

**Data:** Public Telegram posts. A private SaaS project is acceptable for a hackathon. Do not publish trace links: they can contain fresh positions or strike sites (CLAUDE.md rule). [Likely]

LangSmith eval loop (signature verified in langsmith 0.14.17: `Client.evaluate(target, data=, evaluators=, experiment_prefix=, metadata=, max_concurrency=, num_repetitions=)`):
```python
from langsmith import Client
ls = Client()
# once: gold = [{"inputs": {"text": ...}, "outputs": {"claims": [...]}}, ...]
# ls.create_dataset("gold-v1"); ls.create_examples(dataset_name="gold-v1", examples=gold)
app_eval = g.compile()  # no checkpointer for evals
FIELDS = ["state", "actor", "modality", "event_date"]
def match(pred, gold):  # pair claims by settlement lemma
    gi = {c["settlement_lemma"].lower(): c for c in gold}
    return [(p, gi[p["settlement_lemma"].lower()]) for p in pred if p["settlement_lemma"].lower() in gi]
def scores(outputs: dict, reference_outputs: dict) -> list[dict]:
    pred, gold = outputs["claims"], reference_outputs["claims"]
    m = match(pred, gold)
    res = [{"key": "claim_recall", "score": len(m) / max(len(gold), 1)},
           {"key": "claim_precision", "score": len(m) / max(len(pred), 1)},
           {"key": "spans_exact", "score": float(outputs["status"] == "ok")},
           {"key": "attempts", "score": outputs["attempts"]}]
    res += [{"key": f, "score": sum(p[f] == q[f] for p, q in m) / len(m)} for f in FIELDS if m]
    return res
async def run_exp(name, system, model):   # nodes are async -> use aevaluate
    async def target(inp):
        return await app_eval.ainvoke({"post_id": "gold", "text": inp["text"], "errors": []},
                                      {"configurable": {"system": system, "model": model}})
    await ls.aevaluate(target, data="gold-v1", evaluators=[scores], experiment_prefix=f"{name}-{model}",
                       metadata={"prompt": name, "model": model}, max_concurrency=8)
# asyncio.run(run_exp("v1", SYS_V1, "claude-haiku-5-5")); asyncio.run(run_exp("v2", SYS_V2, "claude-haiku-5-5"))
```
In the LangSmith UI, open the dataset, select the experiments and click Compare. The view shows per-key averages and per-example diffs, with a link to each trace (extract → validate → extract …). [Likely, UI not tested: no account here.] Keep 15 of the 60 posts held out. Do not tune on them.

## 4. Anthropic cost levers
### 4.1 Prices, claude-haiku-5-5, prompts ≤100k tokens [Certain, platform.claude.com/docs pricing page]
| | input | 5m cache write | 1h cache write | cache read | output |
|---|---|---|---|---|---|
| standard | $0.10 | $0.125 | $0.20 | $0.01 | $0.50 |
| batch (-50%) | $0.05 | | | | $0.25 |
(per MTok. Prompts over 100k tokens cost 5×. Not relevant here.) Minimum cacheable prefix on Haiku 5.5 is 512 tokens [Likely, skill docs], so a 2k system prompt caches. Tool use adds ~286–406 hidden system tokens. Our forced-tool schema is ~300 tokens. Total cached prefix ≈ 2.7k. 
### 4.2 Estimate: 10k posts × 600 in + 150 out, 2.7k cached prefix
| item | standard | batch |
|---|---|---|
| uncached input 6M tok (cache writes negligible) | $0.60 | $0.30 |
| cache reads 27M tok | $0.27 | $0.14 (cache hits in batch are best-effort) |
| output 1.5M tok | $0.75 | $0.38 |
| retries (~20% of posts, +1 call) | ~$0.35 | ~$0.18 |
| **total** | **≈ $2.0** | **≈ $1.0** |
| no caching at all | ≈ $4.4 | ≈ $2.2 |
- Cyrillic counts more tokens than English, and the Haiku 5.5 tokenizer uses ~30% more tokens than Haiku 4.5. Measure 20 real posts with `client.messages.count_tokens`. Even at 2× the estimate, the run costs <$5. [Likely]
- The gold-set loop is negligible: 60 posts × 20 experiments ≈ $0.15 on Haiku, ≈ $3 on `claude-sonnet-5-5` ($2/$10).
- Rate limits: Haiku 5.5 Start tier is 4,000 RPM, 2M ITPM, 400k OTPM. Cache reads do not count toward ITPM. 16–32 concurrent requests are well under the limit. A new org can start in a lower "Evaluation tier", so watch for 429s. [Certain]

### 4.3 Gotchas that change cost
- **Thinking:** Haiku 5.5 runs adaptive thinking by default, and thinking tokens bill as output. `with_structured_output` (function_calling) forces the tool call, and a forced tool call skips thinking. That keeps output tokens low. If you set `effort=` or `thinking=` on `ChatAnthropic`, langchain-anthropic switches to non-forced tool mode (source: `with_structured_output`), thinking turns on, and output cost can grow 3–10×. [Certain on code path; Guessing on the multiplier]
- `method="json_schema"` (`output_config.format`) is the alternative. With it, thinking stays on unless you lower effort. Keep the default.
- **Cache breakers:** any per-post text in `SYSTEM` (dates, channel names). Put the post date in the user message. Check `cache_read` in `cb.usage_metadata`: it must be about 2.7k × the number of calls.

### 4.4 Batches API
- LangChain cannot use it. `ChatAnthropic.abatch` only runs concurrent normal requests. langchain-anthropic 1.7.10 has no `messages.batches` code (grep of the package). [Certain]
- To use it, call the `anthropic` SDK directly: `client.messages.batches.create(requests=[{"custom_id": post_id, "params": {...}}])`, poll `retrieve(id).processing_status == "ended"`, then read `results(id)` keyed by `custom_id`.
- The retry loop becomes rounds: batch 1 → validate locally → batch 2 with the failures + error text → batch 3. Each round has up to 24 h latency (usually <1 h). [Likely]
- **Recommendation:** the saving is ~$1, so use the standard API with concurrency. Keep batch for a later 100k+ backfill.

## 5. Gotchas: exact-substring spans in Cyrillic
Expected failure modes [Likely]:
- Character swaps: `«»„“`→`"`, `ʼ` (U+02BC) or `’`→`'`, NBSP or double space → one space, `—`/`–`→`-`, `ё`→`е`.
- Lost characters: emoji or zero-width chars dropped mid-span.
- Rewrites: typos "fixed", RU↔UK translation.
- Structure: a span across a line break, two fragments joined with "...".

Mitigation, in order (cheapest first):
1. **Prompt:** "Copy evidence character-for-character. One clause, ≤20 words, no ellipsis, no translation, do not change quotes or apostrophes." Put 2 few-shot examples with `«»` and `ʼ` in the cached system prompt.
2. **Deterministic repair before the LLM retry** (no LLM cost). Normalise the text and the span the same way: NFKC, map quotes, apostrophes and dashes to one char each, `ё→е`, collapse whitespace, strip zero-width chars. Keep an index map from normalised position to original position. Find the span in the normalised text and replace it with `text[orig_start:orig_end]`. Then do the exact `in` check again. The rule "span is an exact substring" still holds, because the stored span comes from the original text.
3. Fallback locate: `rapidfuzz.fuzz.partial_ratio_alignment(span, text)` with score ≥ 92 → take the original slice → exact check. (`uv add rapidfuzz`.)
4. Only if 2–3 fail, send the LLM retry with the error message. Expect most retries to go away. [Guessing]
5. Option if spans still fail often: number the sentences in the input (`[3] ...`) and ask for `sentence_ids`. Spans are then exact by construction but coarser. Use this as plan B, not the default.

- **Settlement names:** posts use inflected forms (`в Покровську`, `под Покровском`) and old or renamed names (Красноармейск = Покровськ). Ask for the as-written form (evidence) and `settlement_lemma`. Resolve the lemma in the gazetteer with RU+UK+translit aliases.
- **event_date:** give the post date in the user message. Tell the model to resolve "вчера/сьогодні" from it, otherwise null. This is a no-leakage requirement: the date must come only from the post.
- **Refusals:** war content can trigger `stop_reason: "refusal"` on Haiku 5.5, and Haiku 5.5 has no server-side fallback. langchain then raises a parse error. Catch it in `extract` and mark the post `refused`. Count refusals. [Likely]
