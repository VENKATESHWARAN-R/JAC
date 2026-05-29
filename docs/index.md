# JAC

> **Just Another Companion/CLI — a local-first agentic coding harness with A2A superpowers.**

```mermaid
flowchart LR
    you["👤 you<br/><small>'jac, do the thing.'</small>"]

    %% Home machine
    subgraph local["💻 your machine · $ jac"]
        gru["🤖 Gru@local<br/><small>'A2A radio on. Who wants chaos?'</small>"]

        minionTests["🍌 minion-tests<br/><small>'bee do bee do... pytest!'</small>"]
        minionFiles["🍌 minion-files<br/><small>'para tu! checking files.'</small>"]

        sibling["🤖 Gru@different-project<br/><small>'same laptop, different evil plan.'</small>"]

        gru -. "spawns" .-> minionTests
        gru -. "spawns" .-> minionFiles
        gru <-->|"A2A · local handshake"| sibling
    end

    %% Remote server
    subgraph remote["🖥️ remote server"]
        remoteAgent["🧰 ops-agent<br/><small>'I have logs, boss.'</small>"]
    end

    %% Cloud
    subgraph cloud["☁️ cloud"]
        cloudAgent["🚀 research-agent<br/><small>'I searched 8 APIs and found one banana.'</small>"]
    end

    you -->|"$ jac"| gru

    %% A2A links
    gru <-->|"A2A · over network"| remoteAgent
    gru <-->|"A2A · cloud call"| cloudAgent

    %% Node styles
    classDef human fill:#111827,stroke:#9ca3af,stroke-width:1.5px,color:#f9fafb;
    classDef gru fill:#1e293b,stroke:#ffd43b,stroke-width:3px,color:#ffffff;
    classDef minion fill:#3b2f0b,stroke:#ffd43b,stroke-width:2px,color:#fff7cc;
    classDef localAgent fill:#1e3a5f,stroke:#60a5fa,stroke-width:2px,color:#eaf4ff;
    classDef remoteAgent fill:#172554,stroke:#38bdf8,stroke-width:2px,color:#e0f2fe;
    classDef cloudAgent fill:#2e1065,stroke:#c084fc,stroke-width:2px,color:#f5f3ff;

    class you human;
    class gru gru;
    class minionTests,minionFiles minion;
    class sibling localAgent;
    class remoteAgent remoteAgent;
    class cloudAgent cloudAgent;

    %% Subgraph zone styles
    style local fill:#111827,stroke:#ffd43b,stroke-width:1.5px,color:#f9fafb;
    style remote fill:#0f172a,stroke:#38bdf8,stroke-width:1.5px,color:#e0f2fe;
    style cloud fill:#1e1b4b,stroke:#c084fc,stroke-width:1.5px,color:#f5f3ff;

    %% Link styling
    linkStyle 0 stroke:#ffd43b,stroke-width:2px,stroke-dasharray: 5 5;
    linkStyle 1 stroke:#ffd43b,stroke-width:2px,stroke-dasharray: 5 5;
    linkStyle 2 stroke:#60a5fa,stroke-width:2px;
    linkStyle 3 stroke:#9ca3af,stroke-width:2px;
    linkStyle 4 stroke:#38bdf8,stroke-width:2px;
    linkStyle 5 stroke:#c084fc,stroke-width:2px;
```

JAC is a Python CLI that gives you a local coding companion with tools, memory,
sessions, skills, human approval gates, and Agent-to-Agent communication.

It runs on your machine — your keys, your files, your context.

## What is JAC?

<div class="grid cards" markdown>

-   :lucide-terminal: **Local-first CLI**

    Start a session, ask for changes, inspect tool calls, approve actions, and keep control. No SaaS overhead. No surprise bills. No mysterious 3am tool calls you didn't approve.

-   :lucide-brain: **Memory + skills**

    Give Gru persistent context and reusable routines without turning every prompt into a wall of text.

-   :lucide-users: **Minion army**

    Too much for one agent? Gru spawns sub-agents for parallel or context-heavy tasks — each minion works in its own window and reports back. Main context stays lean. Chaos stays delegated.

-   :lucide-radio-tower: **A2A as the main trick**

    Expose JAC as an A2A agent, talk to peer agents, transfer files, and coordinate work across agent boundaries.

</div>

## Where to go

| I want to… | Start here |
| --- | --- |
| **Install and run JAC** | [Getting started](user-guide/getting-started.md) |
| **See the A2A superpower** | [A2A operator guide](user-guide/a2a-operator.md) |
| **Look up commands and slash commands** | [CLI reference](user-guide/cli-reference.md) |
| **Configure profiles, tiers, and budgets** | [Configuration](user-guide/configuration.md) |
| **Understand sessions and memory** | [Sessions & memory](user-guide/sessions-and-memory.md) |
| **Add skills** | [Skills](user-guide/skills.md) |
| **Use MCP servers** | [MCP servers](user-guide/mcp.md) |
| **Contribute or extend JAC** | [Contributing](developer/contributing.md) |
| **Navigate the codebase** | [Codebase map](developer/codebase-map.md) |
| **Understand the architecture** | [Architecture](architecture.md) |
| **Read the cost-efficiency thesis** | [Cost-efficient orchestration](design/cost-efficient-orchestration.md) |
| **See what's shipped vs queued** | [Progress](progress.md) |

## Why this exists

Most coding agents are useful inside one process, one CLI, or one product boundary.
JAC explores a slightly different idea:

> What if your local coding agent could become a peer in a larger agent network?

That is where A2A comes in. JAC can act as your local companion, but it can also
participate in a broader agent workflow where agents exchange context, files,
requests, and results.

In normal mode, Gru helps you work inside your repository. In A2A mode, Gru gets
a radio, calls other agents, shares context, and tries very hard not to CC everyone
on the wrong thread.

## What makes it different?

<div class="grid cards" markdown>

-   :lucide-radio: **A2A-first direction**

    JAC is not only a coding REPL. It is an experiment in making a local CLI agent
    participate in agent-to-agent workflows.

-   :lucide-shield-check: **Approval-gated execution**

    Tool calls are visible and controlled, so the agent can help without silently
    doing surprising things to your workspace.

-   :lucide-wallet-cards: **Cost-aware orchestration**

    Profiles, model tiers, context budgets, compaction, and sub-agent routing are
    part of the design instead of an afterthought.

-   :lucide-boxes: **Composable capabilities**

    Skills, MCP servers, A2A, process handling, planning, clarification, and hooks
    are treated as capabilities around the core agent.

</div>

## Still serious under the goggles

The homepage is allowed to smile. The rest of the docs stay technical:

- [Architecture](architecture.md) — design decisions and system boundaries.
- [A2A operator guide](user-guide/a2a-operator.md) — run, expose, and operate A2A.
- [Cost-efficient orchestration](design/cost-efficient-orchestration.md) — context and budget strategy.
- [Documentation strategy](design/documentation-strategy.md) — audiences, source of truth, and writing rules.
- [Drift matrix](design/audit/drift-matrix.md) — doc/code alignment audit.
- [Roadmap archive](progress-archive-2026-05.md) — older design notes and historical context.

