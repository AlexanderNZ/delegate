# ADR 0008: The run index is derived from the journals

- Status: accepted, amended 2026-11-14 (#323), amended 2026-11-15 (#327)
- Date: 2026-11-14
- Applies to: the engine, the run index port, `delegate runs`, and the configuration file of the user

## Context

`delegate runs` lists the runs of one machine. It reads the journal of each run in the state root of that machine. A person who works on more than one host has no view of the runs of the other hosts. A host cannot read the state root of another host.

The view needs a list of the runs of each host, in a place that each host can read. The journal of a run is the record of the run: the engine, `delegate status`, `delegate watch` and `--resume` decide from it. A second record of the same facts can differ from the journal, and the two must not disagree.

## Decision

The run index is a list with one record for each run. The engine writes the record of its run. It does not read the record back, and no decision of the engine depends on it.

The index is derived from the journals. The record holds the values of the snapshot of the run at the write: the same values that `delegate status` gives from the journal. A record can therefore be wrong or late, but a journal cannot be wrong because of a record. The engine writes the record after `run-start`, `resume`, each `step-end`, `paused`, `halted`, `crashed`, `interrupted`, `setup-problem` and `run-end` (amended by #327).

A write of the index never changes the run. When a write fails, for any reason, the engine adds one `index-warning` event with the reason to the journal, and the run goes on with the same result. A run with no index configured writes nothing and adds no event. A failed write loses nothing, because the journal holds each fact, and a later write replaces the record.

The index is a port of the run context, `delegate.ports.index`. It uses the words of the domain: record a run, list the records, forget a run. It names no file system. The first backend is a shared directory, `DELEGATE_INDEX`. It keeps one JSON file for each run, named by the run id. The backend writes a temporary file in the same directory and renames it, so a reader never sees half a file.

The record holds `run_id`, `host`, `repo` (the name of the repository, not its path), `started`, `state`, `tickets_ended`, `tickets_total`, `eta` (or null) and `heard` (the time of the write).

## Rejected alternatives

- **The index as the record of the run.** Two records of one run can disagree, and a failed write would then lose a fact. The journal is already append-only and checked.
- **A write that fails ends the run.** The index is a view. A full disk or a shared directory that goes away must not stop work that is correct.
- **A path in the record.** A path is true on one host only. The name of the repository is true on each host.

## Consequences

- A host that cannot write the index still runs, and its journal says so with `index-warning`.
- A record shows the state at the last write. A run that a kill stopped keeps its last record until a later write or a command forgets it. A run that ends with `crashed`, `interrupted` or `setup-problem` writes its last record with the state `stopped` (#327).
- `index-warning` is written after the event that it follows. A warning after `run-end`, `paused` or `halted` does not continue the run, as `cleanup` after `run-end` does not.
- A second backend, for example a service, is another adapter of the same port. The engine does not change.

## Amendment (#323): the index is on by default

### Context

The engine wrote a record only when `DELEGATE_INDEX` was set in the environment of the process that started the run. A run that a shell, a service or an agent started without the variable wrote no record, and nothing told the person. The runs that an earlier version started had no record. Each of these was a step that a person can forget. A shared directory is also not on each pair of machines.

### Decision

- **The index is always on.** With no setting, the index is the directory `index/` in the state root of the machine, and each run writes its record there. A run never runs with no index.
- **One setting for each machine, in a file.** The configuration file of the user, `$XDG_CONFIG_HOME/delegate/config.toml` (`~/.config/delegate/config.toml`), has the key `index`, the directory of a shared index. Each `delegate` process reads the file, whatever its environment. `DELEGATE_INDEX`, when it is set and not empty, wins over the file. The order is `DELEGATE_INDEX`, the file, the default of the state root. A configuration manager can write the file once for each machine.
- **The key `hosts`.** The same file has `hosts`, a list of the ssh names of the other hosts that `delegate runs` reads on this machine. Only the file of the viewer needs the key: a host that is read needs no setting. `delegate runs` reads each host of `hosts` and each host of the records of the index, one time each, with `ssh -o BatchMode=yes <host> delegate runs --local --json` (#318). So the index of each host can stay on that host.
- **The store of last answers.** The viewer keeps the newest answer of each host that it read in its own state root, in `last-answers/<host>.json`, written to a temporary file and renamed, as the index is. When a host is unreachable, its rows are the runs of its last answer (or of its records, the newer of the two), dim, with `heard <duration> ago`, and its row says `unreachable` with the reason.
- **Records for the runs that have none.** Each command that opens the index (`delegate run`, `delegate run --resume`, `delegate runs`, and `delegate runs --local`) writes a record for each run of the state root of this machine that `delegate runs` lists and that has no record, or an older one. The record is the snapshot of the journal, so the write is safe at any time. So a read of a host over ssh also brings the records of that host up to date.
- **No write of a run goes to another host.** A command writes the records of the runs of its own machine only, to the index of its own process. It reads other hosts and never writes to them.
- **The configuration is checked before a run starts.** An error of the file (a file that cannot be read, a file that is not valid TOML, an unknown key, a value of the wrong type, or a host of `hosts` that names this machine) is one line that names the file, the key and the expected form. `delegate runs` exits 2 with it. `delegate run --dry-run` reports it as a problem of the setup check, with the index directory, where it comes from, and a write probe. `delegate run`, `--detach` and `--resume` exit 1 with it before they create a branch or write a journal event: nothing of the run exists yet, so a refusal loses nothing.
- **After the start, the rule of this ADR does not change.** A write of the index that fails adds `index-warning` to the journal, and the run goes on with the same result. A failure of the records of the other runs of the machine changes nothing in the run. A command that is not `delegate run` prints one warning line on stderr for a write that fails, and goes on.

### Rejected alternatives

- **A setting in the environment only.** Each shell, service and agent must then set it, and a run that does not set it is invisible with no warning.
- **A configuration error that only warns at the start of a run.** The fix is one line of the file, and the dry run shows it before each launch. A run that starts with the wrong index writes records that the person does not see.
- **Writes to the index of another host.** A host can be unreachable, and two writers of one record can disagree. Each host keeps the records of its own runs.

### Consequences

- `delegate runs` with no setting shows the runs of this machine, and the header line names the configuration file, the index and the hosts that it read, so a person sees the settings in use.
- A host with an earlier `delegate` is `unreachable` with the reason `delegate on <host> is older than 1.22.0`.
- The state root of a machine has two more entries, `index/` and `last-answers/`. A test that lists the state directories of the state root leaves them out.

## Amendment (#327): each stop of a run writes a record, and the backfill leaves a cleaned run out

### Context

The engine wrote the record at `run-start`, `resume`, `step-end`, `paused`, `halted` and `run-end`. A run that ended with `crashed`, `interrupted` or `setup-problem` kept a record with the state `running`. On a shared index the other hosts then listed the run as running for as long as its host was unreachable: a record in an open state never leaves the window. Also, `delegate clean` forgets the record of a run (#317) and keeps the journal, and the backfill of #323 found a listed run with no record and wrote it again, at the next `delegate runs` or `delegate run`. With `delegate runs --watch` open, the forget was undone in one second.

### Decision

- **The events that write a record are** `run-start`, `resume`, `step-end`, `paused`, `halted`, `crashed`, `interrupted`, `setup-problem` and `run-end`. After `crashed`, `interrupted` and `setup-problem` the process of the run ends, so the record is of a run with no live process: its state is `stopped`.
- **The backfill writes no record for a cleaned run.** A run is cleaned when it ended `abandoned`, or when its journal has a `cleanup` event after its newest `run-start`, `resume`, `run-end` or stop. The `cleanup` event that the engine writes before `run-end` does not count. A later `resume` makes the run not cleaned. `delegate clean` keeps the rule of #317: it forgets the record of each run that it cleans. It now also adds one `cleanup` event for the first clean of a run, also when the engine cleaned up the run already and nothing went, so that the journal says that the run was cleaned. A second clean of the same run adds no event.

### Rejected alternatives

- **A marker file for a forgotten run.** The journal is the record of the run, and a second place for the same fact can differ from it.
- **No backfill of an ended run.** A run that an earlier version wrote, or that started with no index, needs its record.
