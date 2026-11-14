# AI agents: the complete guide (monetize today)

Source: pasted Spanish-language article, summarized here in English. Unlike the other notes it is not about Grok Bot; it is a general guide to building and selling AI agents. It gives no revenue figures or price points. Claims such as MCP adoption by Anthropic, Google and OpenAI are the article's and unchecked.

## Core idea
An agent is **LLM + tool calling + execution loop + memory/state**. Without all four it's a chatbot with a long prompt. The people who earn from agents understand the architecture well enough to diagnose a real business problem, and sell infrastructure with a measurable outcome, not "I integrated GPT/Claude into your business."

## TL;DR rules
- Start with deterministic automation (n8n/Make) plus one LLM decision point, not a fully autonomous agent on day one: less error surface, easier to sell.
- Sell the SLA: response time, resolution rate, human hours freed per week.
- RAG is most of what makes a vertical agent useful: a well-indexed knowledge base matters more than the model.
- No observability (logs, tracing, evals) means a demo, not a product.
- Version prompts and flows as code (git) from the first client.
- Highest tickets: multi-agent plus deep integration with legacy systems (ERP, proprietary CRM).
- Charge from the first client; free portfolio work gives demo feedback, not production feedback.

## Level 0: minimum architecture
- **Agent loop:** perceive, reason, act, observe, repeat until the goal or a step limit.
- **Tool/function calling:** tools defined by schema (name, parameters, description).
- **MCP (Model Context Protocol):** open standard for exposing tools and data to agents uniformly; exposing your own tools via MCP saves rewriting when the model or client changes.
- **Memory:** short-term (conversation) versus long-term (per-user, vector or key-value store). No persistent memory means repeated questions every session.
- **RAG:** index the client's knowledge (docs, FAQs, catalog) in a vector store and inject relevant chunks. Bad chunking or embeddings ruin an agent that technically works.
- Pick the use case by **domain advantage**, not market size. Define the measurable result before the architecture (e.g. first response from 4 hours to 2 minutes, auto-escalation to a human when model confidence drops below X%).

## Level 1: automation plus one LLM call (billable this week)
1. **First-line support bot with intent classification:** webhook, LLM classifies intent via function calling, deterministic flow acts or escalates. n8n/Make plus model API plus the business database. Monthly fee with a response-time SLA.
2. **Research and summary agent with basic RAG** (lawyers, consultants, analysts): answers citing the exact source; chunking must preserve article/clause context, not arbitrary N-token cuts.
3. **Lead qualification with scoring:** 3-4 structured questions, structured output/JSON mode extracts budget, urgency, product type, score routes to a human or nurturing.
4. **Follow-up agent with state-based triggers:** reads CRM or appointment state before acting, not a blind cron.

First client: don't offer a generic "AI agent." Diagnose a measurable bottleneck in a specific business and propose the specific flow.

## Level 2: from flow to packaged product
Parameterized flow templates (client only adds credentials and knowledge base); per-vertical prompt and function-schema kits; pre-structured RAG knowledge bases by industry (the indexing structure and metadata schema are the product); a short course on your own stack. Sell via infoproduct platforms, a paid private repo, or manual delivery while validating demand.

## Level 3: systems that run alone
24/7 monitoring agent (competitor prices, brand mentions, regulatory changes) that compares against saved state and alerts only on relevant change, with an LLM verification step against false positives; newsletter with an automated research pipeline (ingestion and deduplication are the hard part; you add the angle); an analysis account benchmarking agent platforms (n8n vs Make vs LangGraph or Claude Agent SDK) with clear methodology and affiliate links. Typical error: not logging failed runs, so the client finds failures before you do.

## Level 4: deep integration (high ticket)
Agents on legacy systems (RPA plus function calling, or custom middleware); multi-agent architectures (orchestrator, data agent, writer, validator) via LangGraph, CrewAI or the Anthropic agent SDK, where the decision graph and partial-failure handling is what's paid for; agentic-maturity audits with an implementation plan and proposed architecture; maintenance with observability as a service (tracing dashboards, regression evals on prompt change, degradation alerts). Requires reading others' API docs, OAuth/API keys/signed webhooks, and reasoning about partial failures in distributed systems.

## Level 5: make it a system
Version prompts, schemas and flows in git; internal per-vertical templates (week one becomes day one by client five); raise prices before capacity saturation; measure real cost per client (tokens, maintenance hours, incidents) against revenue, since a heavy, failure-prone agent can be worse business than three simple Level 1 clients.

## Common technical mistakes
Mistaking a long prompt for an agent; no step or cost limits (a runaway loop burns the token budget unnoticed); ignoring observability until the client complains; generic RAG chunking for legal/medical/technical text; not versioning prompts; building multi-agent for a problem a simple deterministic automation solves.

## Stack by category
- Models/APIs: Claude, GPT, Gemini with native function calling and structured output.
- Agent orchestration: LangGraph, CrewAI, provider SDKs (Claude Agent SDK).
- No-code deterministic layer: n8n, Make, Zapier.
- Tool protocol: MCP.
- Vector stores: Pinecone, Weaviate, pgvector (pgvector if you already run Postgres).
- Persistent memory: dedicated key-value or vector stores, separate from the knowledge-base RAG.
- Observability/evals: LLM tracing such as LangSmith or equivalents, plus regression evals before each prompt change.
- Version control: git for prompts, function schemas and exported flows.

## Takeaway
Start at Level 1 this week: one deterministic flow, one LLM decision point, measurable, charged from day one.

## Related notes
The Grok Bot notes cover agent teams, approval lines and handoffs; for client-facing work, their approval and data-line rules apply (see `grok-bot-opus-10-step-tutorial.md`).
