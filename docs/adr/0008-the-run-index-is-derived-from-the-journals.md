# ADR 0008: The run index is derived from the journals

- Status: accepted
- Date: 2026-11-14
- Applies to: the engine, the run index port, and `delegate runs`

## Context

`delegate runs` lists the runs of one machine. It reads the journal of each run in the state root of that machine. A person who works on more than one host has no view of the runs of the other hosts. A host cannot read the state root of another host.

The view needs a list of the runs of each host, in a place that each host can read. The journal of a run is the record of the run: the engine, `delegate status`, `delegate watch` and `--resume` decide from it. A second record of the same facts can differ from the journal, and the two must not disagree.

## Decision

The run index is a list with one record for each run. The engine writes the record of its run. It does not read the record back, and no decision of the engine depends on it.

The index is derived from the journals. The record holds the values of the snapshot of the run at the write: the same values that `delegate status` gives from the journal. A record can therefore be wrong or late, but a journal cannot be wrong because of a record. The engine writes the record after `run-start`, `resume`, each `step-end`, `paused`, `halted` and `run-end`.

A write of the index never changes the run. When a write fails, for any reason, the engine adds one `index-warning` event with the reason to the journal, and the run goes on with the same result. A run with no index configured writes nothing and adds no event. A failed write loses nothing, because the journal holds each fact, and a later write replaces the record.

The index is a port of the run context, `delegate.ports.index`. It uses the words of the domain: record a run, list the records, forget a run. It names no file system. The first backend is a shared directory, `DELEGATE_INDEX`. It keeps one JSON file for each run, named by the run id. The backend writes a temporary file in the same directory and renames it, so a reader never sees half a file.

The record holds `run_id`, `host`, `repo` (the name of the repository, not its path), `started`, `state`, `tickets_ended`, `tickets_total`, `eta` (or null) and `heard` (the time of the write).

## Rejected alternatives

- **The index as the record of the run.** Two records of one run can disagree, and a failed write would then lose a fact. The journal is already append-only and checked.
- **A write that fails ends the run.** The index is a view. A full disk or a shared directory that goes away must not stop work that is correct.
- **A path in the record.** A path is true on one host only. The name of the repository is true on each host.

## Consequences

- A host that cannot write the index still runs, and its journal says so with `index-warning`.
- A record shows the state at the last write. A run that a kill stopped keeps its last record until a later write or a command forgets it.
- `index-warning` is written after the event that it follows. A warning after `run-end`, `paused` or `halted` does not continue the run, as `cleanup` after `run-end` does not.
- A second backend, for example a service, is another adapter of the same port. The engine does not change.
