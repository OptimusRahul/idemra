# Architecture — System Overview

Companion to [`docs/adr/`](adr/) — the ADRs record *why* each decision was
made; this is the *what*, as a single picture. Update this diagram when the
shape of the system changes enough that a reader would get lost without it,
not on every commit.

## System diagram

```mermaid
flowchart TD
    Repo["Target Repo<br/>git, user-owned"]:::external --> CLI["Idemra CLI<br/>Typer"]:::hub

    CLI --> L1

    subgraph PermGate [Permission Gate]
        direction TB
        L1["Layer 1: Hardcoded<br/>jail, secrets, git-ops"]:::l1
        L2["Layer 2: permissions.yml<br/>parsed, Phase 2"]:::l2
        L3["Layer 3: Per-action<br/>planned"]:::planned
        L1 --> L2 --> L3
    end

    CLI --> W1

    subgraph WorldModel [World Model Pipeline]
        direction TB
        W1["idemra index REPO"]:::hub
        W2["Snapshot + Symbol Index<br/>tree-sitter"]:::data
        W3[".idemra/brain/<br/>gitignored"]:::data
        W1 --> W2 --> W3
    end

    CLI --> PG

    subgraph Postgres [Postgres — Source of Truth]
        direction TB
        PG["Event Log<br/>append-only"]:::hub
        Runs["runs"]:::data
        Events["events"]:::data
        Approvals["approvals"]:::data
        DeadLetters["dead_letters"]:::data
        PG --> Runs & Events & Approvals & DeadLetters
    end

    PG -. reads/writes .-> Control["Control Plane<br/>orchestrated"]:::l2
    Control --> Exec["Execution Plane<br/>choreographed, Phase 3+"]:::planned

    Redis["Redis + RQ<br/>provisioned, not wired"]:::planned
    Qdrant["Qdrant<br/>Phase 11, not wired"]:::planned

    classDef external fill:#ffd8a8,stroke:#f59e0b,color:#1e1e1e
    classDef hub fill:#a5d8ff,stroke:#4a9eed,color:#1e1e1e
    classDef l1 fill:#ffc9c9,stroke:#ef4444,color:#1e1e1e
    classDef l2 fill:#d0bfff,stroke:#8b5cf6,color:#1e1e1e
    classDef planned fill:#fff3bf,stroke:#f59e0b,stroke-dasharray: 5 5,color:#1e1e1e
    classDef data fill:#c3fae8,stroke:#06b6d4,color:#1e1e1e
```

**Reading the diagram:**
- **Solid-bordered boxes** are built and wired into the CLI today.
- **Dashed-bordered boxes** are provisioned (in `docker-compose.yml`) or
  scoped for a later phase, but not yet load-bearing — Redis+RQ and Qdrant
  sit idle by design until the phases that need them (background job
  processing; Phase 11's Memory Graph) land. Layer 3 permissions and the
  Execution Plane are designed-for but not yet built.
- **Zones** (Permission Gate, World Model Pipeline, Postgres) group related
  components; the CLI is the only thing that talks to all three today.

## Phase roadmap

| Phase | Status |
|---|---|
| 1 — Skeleton & Permissions | Done |
| 2 — World Model | Done |
| 3 — First Agent & LLM | Next |
| 4 — Master Agent & Routing | Planned |
| 5 — Background Execution | Planned |
| 6 — GitHub & Self-Healing | Planned |
| 7 — Shadow Mode | Planned |
| 7.5 — MCP & Web Search | Planned |
| 8 — Deep MCP | Planned |
| 11 — Memory Graph & Learning | Planned |
| 12 — Watch Mode & Contracts | Planned |
| 13 — Dashboard & Views | Planned |

Live status: [GitHub Milestones](../../milestones).
