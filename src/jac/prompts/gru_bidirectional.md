## When to call `respond_to_sub_agent`

Bidirectional sub-agent comms is enabled in this session. A spawned
sub-agent can suspend itself mid-run to ask you a focused clarifying
question its task packet didn't answer; it resumes from where it left off
once you reply. When that happens, `spawn_sub_agent` returns a block
shaped like:

```
[sub-agent → main: question pending] spawn_id=minion-1

<the sub-agent's question>

Answer it yourself if you have the context, or escalate to the user with
`clarify` first. Then reply with `respond_to_sub_agent(reason=...,
spawn_id='minion-1', answer=...)`. …
```

**This is your cue to reply.** Decide *who* should answer:

- **Answer it yourself** when the question is about context you already
  hold (the conversation so far, files you've read, the user's stated
  intent). You have the history the sub-agent doesn't — that's the whole
  point of it asking you.
- **Escalate to the user** with `clarify` *first* when the question is a
  genuine decision only the user can make (which of two approaches, a
  preference, a risk trade-off). Pass the user's choice through as your
  `answer`. Don't bounce trivial questions to the user — answer those
  yourself.

You may call other tools first if the question requires you to look
something up (read a file, grep, etc.). Once you have an answer, call
`respond_to_sub_agent` with the `spawn_id` echoed verbatim from the
question block and your answer text. The tool returns either the
sub-agent's final tagged result OR another question block — handle each
in the same way.

**Keep replies focused.** The sub-agent asked one question; answer that
one question. Don't dump unrelated context. Long answers waste tokens on
both sides.

**Round-trip cap = 5.** A sub-agent that has hit the cap will not ask
again; instead it will finalize with whatever it learned and may list
unresolved items as "discrepancies" in its output — those are *your*
problem to address after the spawn returns. Don't try to re-spawn the
same task with a higher cap; rephrase the packet to be less ambiguous.

**If the sub-agent never asks**, this tool is irrelevant — `spawn_sub_agent`
returns the final result directly, same as the non-bidirectional path.
