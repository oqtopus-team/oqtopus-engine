# Pipeline Selection Conditions (`if`)

This page describes the small condition language used in the `if` field of
each pipeline entry under `pipeline_manager.pipelines` in `config.yaml` (see
[Configuration](./config.md)). It is used to decide which pipeline a job is
routed to, based on the job's own fields.

## How pipeline selection works

```yaml
pipeline_manager:
  pipelines:
    - name: sampling
      if: job.job_type == "sampling"
      steps: [...]
    - name: sse
      if: job.job_type == "sse"
      steps: [...]
```

- Pipelines are evaluated **top to bottom**. The **first** pipeline whose
  `if` evaluates to `true` is selected for the job.
- If **no** pipeline matches, the job is **automatically failed**: it is
  passed to the configured `exception_handler`, which sets
  `job.status = "failed"` and a human-readable `job.message` explaining that
  no pipeline matched. You do **not** need to write a catch-all pipeline for
  this to work.
- `if: true` is valid syntax and can still be used to write an explicit
  catch-all pipeline (evaluated like any other pipeline, in order), but it is
  optional, not required.
- Conditions are parsed and type-checked once, at Engine startup (using
  [Lark](https://github.com/lark-parser/lark)), not re-parsed per job. A
  malformed condition prevents the Engine from starting, with an error
  identifying the problem — it never fails at job-processing time.

## Syntax

| Operator | Meaning |
| --- | --- |
| `==` | equal |
| `!=` | not equal |
| `&&` | logical and |
| `\|\|` | logical or |
| `!` | logical not |
| `()` | grouping |

Literals: a double-quoted string (`"sampling"`), `true`, `false`, `null`.

Fields: `job.<name>` — see [Available fields](#available-fields) below for
which names are allowed.

Operator precedence, loosest to tightest: `||` < `&&` < `!` < `==`/`!=` <
`()`/literals/fields. `!` negates the entire comparison it applies to, so
`!(job.job_type == "sse")` and `!job.some_bool_field` both work as expected,
but partial negation of one side of a comparison (e.g. `(!job.x) == "y"`) is
not supported.

Examples:

```yaml
if: job.job_type == "sampling"
if: job.job_type == "estimation" && job.device_id == "device-a"
if: job.job_type == "sampling" || job.job_type == "multi_manual"
if: !(job.job_type == "sse")
if: true   # unquoted YAML bool — also valid, treated as the literal `true`
```

There is currently no support for `<`, `<=`, `>`, `>=`, `in`, numbers,
arrays, or string functions. The language is intentionally kept minimal;
these may be added later if a real need comes up.

## Available fields

Only fields explicitly allowed can be used — referencing anything else is a
startup error, not a silently-`false` condition. Today the allowed fields
are:

| Field | Type |
| --- | --- |
| `job.job_type` | string |
| `job.device_id` | string |

This list is deliberately short and hand-maintained rather than
auto-generated from the `Job` model: a field that isn't allowed yet fails
loudly at startup, which is safer than silently exposing every internal
`Job` field to configuration. If you need to route on a field that isn't
listed here, it needs to be added to the engine's allow-list first.

## Type checking

An `if` expression must be **statically typed as `bool`** as a whole:

- `==`/`!=` require both sides to have the same type (e.g. you cannot
  compare `job.job_type` — a string — to `true`).
- `&&`/`||`/`!` require their operand(s) to already be `bool`. Since none of
  the currently allowed fields are booleans, a bare field on its own (e.g.
  `if: job.job_type`) is always a startup error today — it only becomes
  usable once a boolean field is added to the allow-list.

All of this is checked once at startup, so a misconfigured condition is
caught before the Engine starts serving jobs.

## Startup validation errors

The Engine fails fast on any of the following, with an error message
identifying the specific problem:

- `pipelines` missing, not a list, or empty.
- A pipeline entry missing `name`, `if`, or `steps`, or with an empty `steps`
  list.
- Two pipelines sharing the same `name`.
- An `if` string that fails to parse.
- An `if` string referencing a field outside the allow-list above.
- An `if` string that is not statically typed as `bool` (see above).
- A `steps` entry, `job_buffer`, or `exception_handler` that does not name a
  component registered in `di_container.registry`.

The only condition-related situation that is **not** an error is a config
with no pipeline whose `if` always matches — see "How pipeline selection
works" above for what happens to jobs that hit that case at runtime.
