# Scope of this branch — read this first if you arrived via the repository

You are on an **orphan branch** named `l5-blind-package`. It has **no
commit history** and shares **no ancestry** with any other branch in this
repository. Its entire contents are the files listed in
`README_UPLOAD.md` and nothing else.

## Stay on this branch

**Do not read any other branch, and do not read this repository's commit
history, issues, or pull requests.**

This is not a secrecy request. The repository's other branches contain a
prior analysis of this same dataset, written by the people asking you the
question. The whole point of this exercise is to get an account of the
data that was formed **independently** of that analysis. If you read it
first, your answer stops being evidence and becomes an echo — and worse,
the people reading your answer will not be able to tell which parts you
derived and which parts you absorbed.

So: if you find a file on another branch that looks like it answers the
question, **that is the contamination this branch exists to prevent.**
Ignore it and say in your reply that you encountered it and declined to
read it.

## What is deliberately absent

* Any conclusion, hypothesis, model or interpretation held by the people
  who built this package. There is none in these files, by construction.
* The prior analysis mentioned above. It exists, it will be shown to you
  **after** you answer, and you will then be asked to compare.
* Any credential, private key, account identifier or infrastructure
  detail. `scripts/60_verify_package.py` enforces this and fails the
  package if any appears.

## What is here

A descriptive dataset and two briefs. The subject is a third party's
**public** trading activity, retrieved from a public API. The subject's
on-chain address appears throughout because it is public and it is the
subject of the research.

Start with `README_UPLOAD.md`, then your brief — `PROMPT_GROK.md` or
`PROMPT_PERPLEXITY.md`.
