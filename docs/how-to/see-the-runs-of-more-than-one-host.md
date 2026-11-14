# How to see the runs of more than one host

Use this page to see the [**runs**](../glossary.md#run) of each of your hosts in one view on one machine. The machine where you read the view is the viewer. Each run registers itself in the [**run index**](../glossary.md#run-index) of its own host, with no setting. The viewer reads each other host over `ssh`.

## Before you start

You have two or more hosts that start runs, and one viewer. The viewer can also start runs. This page uses two other hosts, `build-host` and `ci-runner`.

## 1. Install delegate on each host

Install `delegate` 1.22.0 or later on each host and on the viewer. An earlier version has no `delegate runs`.

```bash
delegate --version
```

## 2. Check that the viewer reaches each host

On the viewer, run this command for each host. It must print the version with no prompt for a password or a passphrase.

```bash
ssh -o BatchMode=yes build-host delegate --version
ssh -o BatchMode=yes ci-runner delegate --version
```

The name is the name that `ssh` knows, for example a `Host` entry of `~/.ssh/config`. The command runs in a shell that is not interactive, so `delegate` must be on the `PATH` of that shell.

## 3. Add the hosts to the configuration file of the viewer

Write the key `hosts` in the configuration file of the viewer only. A host that is read needs no setting.

```toml title="~/.config/delegate/config.toml"
hosts = ["build-host", "ci-runner"]
```

The file is `$XDG_CONFIG_HOME/delegate/config.toml`, or `~/.config/delegate/config.toml`. Do not put the name of the viewer in `hosts`. See [the configuration file](../reference/runs.md#the-configuration-file) for each key and each error.

## 4. Check the configuration

Run the dry run of a [**workflow**](../glossary.md#workflow) after each change of the configuration file. It shows the file, the index directory, and a write probe of the directory.

```bash
delegate run workflow.toml --dry-run
```

The last three lines are `config: <path>`, `run index: <directory> (from <source>)` and `run index write: ok`. An error of the file is a problem on stderr, and the dry run exits 1. See [the setup check of the dry run](../reference/workflow.md#the-setup-check-of-the-dry-run).

## 5. Read the view

```bash
delegate runs
```

The first line is the header line. It names the configuration file, the run index, and the hosts that the command read. For example:

```text
config /home/me/.config/delegate/config.toml · the index of this machine only · hosts build-host, ci-runner
```

The table has one group for each host. The runs of a host that answered are live, and they are in the sums. See [the other hosts](../reference/runs.md#the-other-hosts).

## When a host is unreachable

The row of the host says `unreachable`. The row under it gives the reason. Its runs are the runs of its last answer, dim, with `heard <duration> ago`. A host that never answered has only its row and its reason.

### ssh cannot reach the host

The reason is `ssh cannot reach <host>: <line>`. The line is the last line that `ssh` wrote. Make step 2 work with no prompt, for example with a key in the agent of `ssh`.

### The host has no delegate command

The reason is `<host> has no delegate command on its PATH`. Install `delegate` on the host. Put it on the `PATH` of a shell that is not interactive.

### delegate on the host is older

The reason is `delegate on <host> is older than 1.22.0`. Install `delegate` 1.22.0 or later on the host.

### delegate on the host is newer

The reason is `delegate on <host> is newer than this one (format <n>)`. Install the same version of `delegate` on the viewer.

### delegate runs on the host failed

The reason is `delegate runs on <host> exited with status <n>: <line>`. Run `delegate runs --local` on the host, and correct what it says. An error of the configuration file of the host is a usual cause.

### The host gives no answer in time

The reason is `no answer from <host> in 10 seconds`. Look at the network and at the load of the host.

### The host prints something else

The reason says that the host gave no document that this version can read. Run the command of step 2 with `runs --local --json` in place of `--version`. Remove what the login shell prints before the document.

## Use a shared directory in place of hosts

A directory that each host can read and write can hold one run index for all hosts. Set the key `index` on each host to that directory.

```toml title="~/.config/delegate/config.toml"
index = "/mnt/shared/delegate-index"
```

Each run writes its record there. `delegate runs` then reads each host that has a record, over `ssh`, as for `hosts`. You can use `index` and `hosts` together. A host that both name is read one time. See [the run index](../reference/run.md#the-run-index).

## What not to do

- Do not edit, copy or delete a file in an index or in the store of last answers. To forget a run, use `delegate clean`.
- Do not set `DELEGATE_INDEX` in one shell or one command to make a run show. Use the configuration file.
- Do not put `hosts` or `index` in a file of a repository or in `workflow.toml`. They are settings of a machine, not of a repository.
