# I started running Selvedge on every Claude Code project — even the weekend ones

*Cross-post on dev.to — tags: ai, claudecode, mcp, productivity*

*Status: DRAFT — publish ~7 days after the launch post (`devto-article.md`).
Aimed at the "solo dev / everyday project" audience, the secondary pillar
in the new positioning. Keep it personal-tone, short, and concrete.*

---

When I [shipped Selvedge](https://dev.to/masondelan/the-silent-problem-with-ai-written-code-the-intent-evaporates-14c4) last week, I pitched it for the long-term codebase problem — the production app that's still running in 2027, full of schema decisions an agent made and forgot.

That framing is real. But it's not actually how I've been using it.

I've been running Selvedge on **every Claude Code project I touch**. Including the hobby ones. Including the half-finished CLI I haven't opened in ten days. And honestly, the small projects might be where it earns its keep fastest.

---

## The "what was I doing again" problem

Here's the loop I kept running into before:

1. Start a side project on a Saturday. Hit it hard for two evenings. Leave it.
2. Come back twelve days later. Open `git log`. See six commits, all titled some variation of *"refactor", "fix", "wip"* by an agent.
3. Try to remember what state I left it in. Read the diffs. Read the diffs *again*. Eventually give up and just re-decide everything from scratch.

This isn't a 2027 problem. This is a *next Tuesday* problem. AI-paced development has compressed the "I no longer remember what I was thinking" timeline from six months to about four days.

---

## What changed

I added Selvedge to my default project setup. Three lines in `CLAUDE.md`:

```
You have access to Selvedge for change tracking.
Call selvedge.log_change after any change to a column, function, env var, or dependency.
Set reasoning to my original ask or the problem you were solving.
```

That's it. The agent does the rest.

Now when I come back to a project, the loop is:

```bash
$ selvedge history --since 14d --summarize
```

I see every meaningful change in human language, with the *why* attached. Then:

```bash
$ selvedge blame whatever_function_im_about_to_modify
```

Two seconds later I know what I was thinking when I (or rather, the agent on my behalf) wrote it. I get back to flow in about a minute, instead of forty.

---

## Why this works for small projects specifically

It turns out the reasons Selvedge is useful for a 100-developer codebase are *also* the reasons it's useful for a one-developer codebase — they're just compressed.

| Pain | Big project | Small project |
|---|---|---|
| "Why does this column exist?" | Six months later | Six days later |
| "Was that intentional or accidental?" | After a team handoff | After a single weekend off |
| "What was the original ask?" | Lost in 4,000 Slack messages | Lost in your previous Claude Code chat |

The agent that built the thing has the context. The agent that's about to *modify* the thing doesn't, unless something captured it.

For solo devs that "something" used to be your own memory. AI-paced builds outpace it.

---

## Setup, for the impatient

If you only read this far:

```bash
pip install selvedge
cd your-project
selvedge init
```

Add to your `~/.claude/config.json`:

```json
{
  "mcpServers": {
    "selvedge": { "command": "selvedge-server" }
  }
}
```

Add four lines to your project's `CLAUDE.md` (the snippet above).

That's the whole setup. Local SQLite under `.selvedge/`, zero deps, no cloud. The next Claude Code session in that directory will start logging changes automatically.

---

## What I'd skip if I were you

A few things I tried and don't bother with anymore on small projects:

- **Don't install the git post-commit hook on every weekend project.** It's useful when you care about linking events to specific commits, less useful when half your commits are `wip`. `selvedge install-hook` is one command away when you do want it.
- **Don't bother with `--changeset` slugs on personal stuff.** Save them for features you'd actually want to query as a unit later.
- **Don't fight a low coverage number in `selvedge stats`.** On small projects, agent compliance is going to be uneven and that's fine. It's a "good enough is good enough" tool.

---

## The pitch

[Selvedge](https://github.com/masondelan/selvedge) is open source, MIT, local-first, runs on Python 3.10+. It captures *why* AI agents made changes — captured live, by the agent, in context — so you can `selvedge blame` any DB column, env var, dep, function, or API route and get a full sentence back.

It's also a "git blame for AI" — there are several of those now ([AgentDiff](https://github.com/sunilmallya/agentdiff), [Origin](https://news.ycombinator.com/item?id=47510254), [Git AI](https://usegitai.com/), [BlamePrompt](https://github.com/Ekaanth/blameprompt)). The space is alive. The thing that makes Selvedge different is *when* and *how* the reasoning gets captured: the agent itself writes it, in the same context that produced the change. No second LLM doing post-hoc inference from the diff.

If you've come back to your own AI-built project and thought "what was this *for* again?" — give it a shot. `pip install selvedge`. The setup is genuinely four minutes and saves you a *lot* of friction the next time you've been away from your own code for more than a few days.

GitHub: [https://github.com/masondelan/selvedge](https://github.com/masondelan/selvedge)
