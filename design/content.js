// Shared docs content (verbatim excerpts from AlexanderNZ/delegate docs/) + render helper for the three theme mockups.
(function () {
  const h2 = (t) => ({ k: 'h2', t }), h3 = (t) => ({ k: 'h3', t }), p = (t) => ({ k: 'p', t });
  const ul = (items) => ({ k: 'ul', items }), ol = (items) => ({ k: 'ol', items });
  const code = (lang, text) => ({ k: 'code', lang, text });
  const table = (rows) => ({ k: 'table', rows });
  const note = (t) => ({ k: 'note', kind: 'note', title: 'Note', t });
  const warn = (t) => ({ k: 'note', kind: 'warn', title: 'Important', t });

  const DIAGRAM = `BRIEF    you: ticket in a workflow ──► engine: run branch, worktree, push guard, brief
            │
BUILD    specialist: code + test, commit, report ("gates green")
            │
         engine: hotspot check, then runs the gates itself
            │ green                            red ──► continuation, same worktree
            │
VERIFY   engine: rebase, gates again, temporary copy
            │
         blind verifier: ticket + diff, never the report
            │
            ├── REJECT ──► fix-up: new commit, gates, fresh verifier on the delta
            │                 │
            │◄──── ACCEPT ────┘          (at the limit: the ticket fails)
            │
MERGE    engine: run branch moves forward
         you:    git merge --ff-only run/demo`;

  const PAGES = {
    overview: {
      title: 'Start here', file: 'overview.md', section: null,
      man: 'DELEGATE(7)', manName: 'delegate — the kit at a glance',
      blocks: [
        p("Handing work to a coding agent is easy. Trusting what comes back is the hard part, and this kit exists for that part."),
        p("This page is the kit at a glance: the problem, the idea, and a picture of how one piece of work moves from start to merge. It is not a tutorial and it is not a reference. It links both at the end. Each term is in bold where it first appears, and it links its entry in the [glossary](glossary.md)."),
        h2('The problem'),
        p("You delegate implementation work to coding agents. Each agent runs in a [**harness**](glossary.md#harness): Claude Code, OpenCode, or the `agent` CLI of Cursor. In every harness you meet the same four problems, and each one costs you something."),
        h3('Trust arrives too late'),
        p("The common pattern runs many implementer agents in one [**session**](glossary.md#session) of a harness and reviews the result once, at the end. The reviewer reads one large diff. It takes the implementer's word that the tests pass, and only its prompt keeps it from changing the code. \"The tests pass\" is a claim, not evidence. The agent may have run a different command, or run the tests before its last edit."),
        p("The review has a quieter flaw. A reviewer that reads the implementer's account first looks where the account points. If the account says \"I fixed the empty-list case\", the reviewer checks the empty-list case. The case that nobody thought of gets no attention at all."),
        p("Then the review finds a defect, and the fix loop has no stop rule. It can go round for hours. What comes back is a draft, and you finish it yourself."),
        h3('The scripts belong to one harness'),
        p("An orchestration script written for one harness does not run in another harness. It names models, so a change of [**gateway**](glossary.md#gateway) or vendor means a change to every script. And the scripts fail in ways that the harness causes:"),
        ul([
          "a [**worktree**](glossary.md#worktree), the separate working tree that an agent builds in, starts from the wrong base, and the agent builds on the wrong commit;",
          "one agent leaves out its structured output, and the whole job stops, so one bad agent costs you hours of work;",
          "a restart after an interruption builds the finished work again, and you pay for it twice;",
          "nothing tells you when the job needs you, so you watch it by hand.",
        ]),
        h2('The idea'),
        p("`delegate` is a kit that hands implementation work to coding agents and gives you independent evidence at each merge. The rule under it is short: no claim that matters at a merge comes from the agent that wrote the code. Something other than that agent checks that the code builds and the tests pass. Something that never read the author's account checks that the change does what you asked for. The rule is the same whatever harness runs the agents, whatever models they use, and whatever you choose to spend. And the merge stays yours."),
        h2('What delegate is not'),
        ul([
          "*Not a coding agent, and not a harness.* It writes no code. It drives the harness that you already use, through its command line, and the agents in that harness write the code.",
          "*Not an issue tracker.* It reads each [**ticket**](glossary.md#ticket), one unit of work written as behaviour, from a local file or from inline text. It never contacts a tracker and never closes a ticket. You save the tickets to disk first.",
          "*Not a CI system.* It runs each [**gate**](glossary.md#gate) of your repository (a command that proves a change is good, such as the test suite) on your machine, to decide whether the work of one ticket can land. It never pushes, and it does not replace the checks that run after you push.",
          "*Not a sandbox.* An agent runs as you, with your permissions, in a worktree. If a policy needs a container, this kit is not enough by itself.",
        ]),
        h2('The mental model'),
        h3('The loop: Brief, Build, Verify, Merge'),
        p("Four verbs name the loop, and every ticket goes through all four."),
        ul([
          "*Brief.* You write a ticket, and list it in a [**workflow**](glossary.md#workflow) file. The engine turns the ticket into a [**brief**](glossary.md#brief), the prompt for one agent.",
          "*Build.* The specialist builds the ticket in its own worktree and commits. The engine then runs the gates itself.",
          "*Verify.* A [**blind**](glossary.md#blind) verifier checks the work. Blind means it gets the ticket and the diff, and never the specialist's own account of the work.",
          "*Merge.* Accepted work lands on the [**run branch**](glossary.md#run-branch), a branch that the engine owns. You merge the run branch.",
        ]),
        p("The same ticket, on one page:"),
        code('text', DIAGRAM),
        note("Look at where the evidence comes from. The gate result comes from the engine, not from the agent that wrote the code. The verdict comes from an agent that never saw the report. The merge comes from you. No arrow that carries evidence starts at the specialist."),
        h2('Where to go next'),
        ul([
          "[The tutorial](tutorial.md) runs the loop once, from install to one verified ticket. Start there.",
          "The reference gives every field and every flag: [the workflow file](reference/workflow.md), [`delegate run`](reference/run.md), [`delegate status` and `delegate watch`](reference/status-and-watch.md), and [`render`, `validate`, `bootstrap` and `brief`](reference/commands.md).",
          "The [glossary](glossary.md) holds every term, with the page that explains it in full.",
        ]),
      ],
      prev: null, next: { title: 'Tutorial', id: 'tutorial' },
    },
    tutorial: {
      title: 'Tutorial: from install to one verified ticket', navTitle: 'Tutorial', file: 'tutorial.md', section: null,
      man: 'DELEGATE-TUTORIAL(7)', manName: 'delegate-tutorial — from install to one verified ticket',
      blocks: [
        p("In this tutorial you run `delegate` once, from start to end. You make a small sample repository. You bootstrap an agent pair for it. You build one ticket in `assure` mode on Claude Code. You read the verdict and the journal."),
        p("The longest part is the wait for the agents."),
        h2('Before you start'),
        p("You need these things:"),
        ul(["Python 3.11 or later.", "`git`.", "`uv`, to install the kit.", "Claude Code, installed and logged in. The command `claude` must work in your terminal."]),
        note("The tutorial uses real agents, so the run uses tokens of your Claude Code account."),
        h2('1. Install the kit'),
        code('bash', 'uv tool install "git+https://github.com/AlexanderNZ/delegate#subdirectory=skills/agent-definitions/validator"'),
        p("This puts the commands `delegate`, `agent-definitions` and `verifier-brief` on your PATH. This tutorial uses only `delegate`."),
        p("Check that the install worked:"),
        code('bash', 'delegate --help'),
        p("The output lists the subcommands `render`, `validate`, `bootstrap`, `brief`, `run`, `status`, `watch` and `docs`."),
        h2('2. Get the sample files'),
        p("The sample files are in the repository of the kit. Clone it."),
        code('bash', 'git clone https://github.com/AlexanderNZ/delegate delegate-kit'),
        p("The sample files are in `delegate-kit/examples/tutorial`. There are six files:"),
        table([
          ['File', 'What it is'],
          ['`words.py` and `test_words.py`', 'A small Python function and its test.'],
          ['`docs/agents/delegation.md`', 'The gate command and the hotspots of the project.'],
          ['`.claude/skills/sample-style/SKILL.md`', 'A skill with the style rules of the project.'],
          ['`ticket.md`', 'The ticket that you will build.'],
          ['`workflow.toml`', 'The workflow file. It names the ticket, the mode, and the gate.'],
        ]),
        h2('3. Make the sample repository'),
        p("Make a new directory, start a git repository in it, and copy the sample files in."),
        code('bash', `mkdir sample
cd sample
git init -b main
git config user.name "Tutorial User"
git config user.email "tutorial@example.com"
cp -R ../delegate-kit/examples/tutorial/. .`),
        p("The `git config` lines set a name for the commits. Use your own name if you prefer."),
        h2('4. Bootstrap the agent pair'),
        p("The kit builds each ticket with a specialist agent and a verifier agent. The verifier is blind: it gets the ticket and the diff, and never the report of the specialist. The command `bootstrap` writes both agents for your repository."),
        code('bash', `delegate bootstrap --repo . --name python \\
  --domain "A small Python library with unit tests." \\
  --tier standard \\
  --skill sample-style \\
  --reference docs/agents/delegation.md \\
  --gate-command "python3 -m unittest"`),
        warn("The option `--gate-command` is important. The verifier may run your gate command, and it may run no other build command."),
        p("The command prints the files that it wrote:"),
        ul([
          "`.claude/skills/python-context/SKILL.md` and `.claude/skills/python-context/agents.toml`. These are the context skill and the declaration.",
          "`.claude/agents/python-specialist.md` and `.claude/agents/python-verifier.md`. These are the agent pair for Claude Code.",
          "The same pair for OpenCode, in `.opencode/agents/`.",
        ]),
        h2('8. Read the result'),
        p("Show the state of the run:"),
        code('bash', 'delegate status'),
        p("The output looks like this. Your run id and your times are different."),
        code('text', `run 20261009T031500Z-a1b2: built (mode assure, adapter claude-code)
journal /path/to/sample/.git/delegate/runs/20261009T031500Z-a1b2/journal.jsonl
ticket 1: state built, verdict ACCEPT, branch run/sample-1, last event 2026-10-09T03:19:02.512384+00:00`),
        p("The state `built` and the verdict `ACCEPT` mean that the ticket is done and checked."),
      ],
      prev: { title: 'Start here', id: 'overview' }, next: { title: 'Bootstrap a single-stack repository', id: null },
    },
    run: {
      title: 'Reference: delegate run', titleRuns: 'Reference: `delegate run`', navTitle: 'delegate run', file: 'reference/run.md', section: 'Reference',
      man: 'DELEGATE-RUN(1)', manName: 'delegate-run — build the tickets of a workflow', synopsis: 'delegate run [<workflow>] [--dry-run] [--resume <run-id>] [--break-lock] [--repo <repo>] [--tiers <tiers>]',
      blocks: [
        p("`delegate run <workflow>` builds the tickets of a workflow. For each ticket, the engine makes a worktree, spawns the specialist of the stack through a harness adapter, checks the specialist report, runs the gates itself, and records each event in a journal. In `assure` mode, the engine then verifies the branch with a blind verifier, and moves the run branch to the branch only on ACCEPT. A REJECT starts a fix-up round, up to two rounds."),
        p("To check a workflow file without a build, use `--dry-run`. See [the workflow reference](workflow.md)."),
        h2('Options'),
        table([
          ['Option', 'Meaning'],
          ['`[<workflow>]`', 'Path to the workflow TOML file; with --resume the default is the file that the run started from.'],
          ['`--dry-run`', 'Validate the workflow and print the plan; create nothing.'],
          ['`--resume <run-id>`', 'Go on with the run RUN_ID from its journal; build the steps that are not complete; it cannot go with --dry-run.'],
          ['`--break-lock`', 'Remove the lock of the run branch when its process no longer exists; a lock whose process is alive stays.'],
          ['`--repo <repo>`', 'The git repository to build in; default is the current directory.'],
          ['`--tiers <tiers>`', 'Path to a tiers.toml; default is the bundled table.'],
          ['`--opencode-model <tier=model>`', 'Use MODEL for TIER in the OpenCode column; repeat it.'],
          ['`--opencode-allow <model>`', 'Add MODEL to the OpenCode allowed set; repeat it. It extends the set, never replaces it.'],
        ]),
        h2('What a run does'),
        ol([
          "The engine validates the workflow. A problem exits 1 and creates nothing.",
          "The engine checks that no other run holds the lock of the run branch. See [the run lock](#the-run-lock). A held lock exits 1 and creates nothing.",
          "The engine checks that `base-branch` is a branch of the repository and that no ticket branch exists. A problem exits 1 and creates nothing.",
          "The engine takes the lock.",
          "The engine creates the run branch at `base-branch`, and writes `run-start` to a new journal.",
          "For each ticket in dependency order, the engine does the step below, or skips the ticket. See [the skip rule](#the-skip-rule). A failed step does not end the run.",
          "In `economy` mode, the engine verifies the chain. See [the chain verification](#the-chain-verification).",
          "The engine writes `run-end`, and releases the lock.",
        ]),
        note("The engine does not trust the report for the gates. A gate that is red is recorded red when the report says `gates_green` is true."),
        p("The engine never merges to `base-branch`, never pushes, and never closes a ticket. A worktree and a branch stay after a failed step."),
        h2('The continuation'),
        p("A specialist that ends `capped` or `failed`, or whose gates are red, continues in the same worktree. The commits that it made stay on the branch. The engine does not make a new worktree or a new branch."),
        p("One count covers the three triggers. Each mode has a continuation limit:"),
        table([['Mode', 'Limit'], ['`assure`', '2'], ['`economy`', '1']]),
        warn("When the specialist is not done after the limit, the step fails. The journal records `continuation-limit` with the count, and the reason of the step ends with `the continuation limit of <n> is reached`."),
        h2('Output and exit codes'),
        p("On stdout, the command prints `run <run id>` and `journal <path>`. On stderr, it prints one line `delegate run: ticket <id>: <reason>` for each failed step, and one line `delegate run: ticket <id>: skipped: <reason>` for each skipped ticket."),
        table([
          ['Code', 'Meaning'],
          ['0', 'Every ticket is built. In `assure` mode, the verifier accepted every ticket. In `economy` mode, the verifier accepted every stack of the chain.'],
          ['1', 'The workflow or the tier file is not valid, the run cannot start, a step failed, a ticket was skipped, or a verifier rejected a ticket. The message names the input. No traceback is shown.'],
          ['2', 'A usage error.'],
        ]),
        h2('The specialist report'),
        p("The specialist writes a JSON object to the report path. A field that this table does not list is ignored."),
        table([
          ['Field', 'Type', 'Meaning'],
          ['`ticket`', 'string', 'The ticket id. It must equal the id of the step.'],
          ['`status`', 'string', '`committed`, `blocked`, or `partial`.'],
          ['`branch`', 'string', 'The branch with the work.'],
          ['`head_sha`', 'string', 'The tip of the branch.'],
          ['`commits`', 'list of strings', 'The commits that the specialist added.'],
          ['`gates_green`', 'boolean', 'The claim of the specialist. The engine runs the gates itself.'],
          ['`summary`', 'string', 'A short account of the work.'],
        ]),
      ],
      prev: { title: 'The workflow file', id: null }, next: { title: 'delegate status and delegate watch', id: null },
    },
    glossary: {
      title: 'Glossary', file: 'glossary.md', section: null,
      man: 'DELEGATE-GLOSSARY(7)', manName: 'delegate-glossary — every term of these docs',
      blocks: [
        p("This page explains the terms that the docs use. The entries are in alphabetical order. Each entry says what the term is and why it is important. Then it links the page that explains the term in full. When the docs use more than one word for a concept, the entry has the preferred word, and a line \"Not:\" lists the other words. For the kit at a glance, read [the overview](overview.md) first."),
        h2('ACCEPT'),
        p("ACCEPT is the verdict that a verifier gives when the work does what the ticket asks. Only an ACCEPT moves the run branch, so work that nobody checked cannot reach it. See [the verifier step](reference/run.md#the-verifier-step)."),
        h2('Adapter'),
        p("An adapter is a Python object that drives one harness through the headless command line of that harness. The engine is the same for each harness, so a new harness needs only a new adapter. See [the adapter interface](reference/run.md#the-adapter-interface)."),
        h2('ADR'),
        p("An ADR (architecture decision record) is a short file that gives the context, the decision and the consequences of one design decision. A contributor can read the reason for a rule without access to a private tracker. See [the decision records](explanation/decision-records.md)."),
        h2('Agent pair'),
        p("An agent pair is a specialist and its verifier, which one declaration renders for each harness. The two halves come from one file, so they cannot drift apart."),
        p("Not: pair, specialist-verifier pair. Write \"agent pair\" in full."),
        p("See [why each specialist has a verifier twin](explanation/why-each-specialist-has-a-twin.md)."),
        h2('Anchoring'),
        p("Anchoring is the effect of a report on a reviewer: the reviewer looks where the report points, and not at what the report omits. The verifier is blind to prevent anchoring. See [why the verifier is blind](explanation/why-the-verifier-is-blind.md#the-report-is-a-claim)."),
        h2('`assure`'),
        p("`assure` is the mode that verifies each ticket branch at once, before the next ticket builds on it. It finds a defect early, and it costs more tokens and more time than `economy`. See [the mode trade-off](explanation/the-mode-trade-off.md#what-each-mode-gives-up)."),
        h2('Base branch'),
        p("The base branch is the branch that a run starts from, for example `main`. The engine never merges into it, because the merge is an action of the coordinator. See [the fields of a workflow](reference/workflow.md#top-level-fields)."),
        h2('Blind'),
        p("A blind verifier gets the ticket and the diff, and never the report of the specialist. The report is a claim, and a verifier that reads it looks only where it points. See [why the verifier is blind](explanation/why-the-verifier-is-blind.md)."),
        h2('Blocker'),
        p("A blocker is a ticket that must be built before another ticket can start. The engine builds the tickets in the order that the blockers set, and it skips a ticket whose blocker failed."),
        p("Not: blocking edge, dependency. Use `blocked-by` only for the field of a workflow."),
        p("See [the tickets of a workflow](reference/workflow.md#tickets)."),
        h2('Brief'),
        p("A brief is the prompt that an agent gets for one task: the ticket, the file boundary, the gates and the report path. The engine writes each brief, so no agent gets a brief that a person wrote by hand. See [the specialist brief](reference/run.md#the-specialist-brief)."),
      ],
      prev: { title: 'The opencode adapter', id: null }, next: null,
    },
  };

  const SEARCH = {
    query: 'verifier',
    results: [
      { title: 'Glossary', section: 'Verifier', id: 'glossary', file: 'glossary.md', snippet: "The ==verifier== is the agent that checks the work of a specialist and gives a verdict. It holds the same skills as its specialist, it is blind and read-only, and it runs on the tier `==verifier==` in every mode." },
      { title: 'Why the verifier is blind', section: null, id: null, file: 'explanation/why-the-verifier-is-blind.md', snippet: "The report is a claim, so the ==verifier== gets the task and the diff; the engine runs the gates, and the ==verifier== runs them in a copy that it may break." },
      { title: 'Reference: delegate run', section: 'The verifier step', id: 'run', file: 'reference/run.md', snippet: "The engine makes the ==verifier== brief with the brief generator (`full_brief`). The brief holds the task (the ticket text), the diff, the gates of the stack, the path of the copy, and the report path." },
      { title: 'Why each specialist has a verifier twin', section: null, id: null, file: 'explanation/why-each-specialist-has-a-twin.md', snippet: "A ==verifier== needs the skills of the specialist, so one declaration renders both halves of a pair, with a pair for each stack." },
      { title: 'Start here', section: 'The mental model', id: 'overview', file: 'overview.md', snippet: "The [**==verifier==**] is a second agent that checks the work of the specialist. It holds every skill of the specialist, the instructions for that kind of code, so it judges the work by the same standard. But it can only read." },
      { title: 'Tutorial: from install to one verified ticket', section: '4. Bootstrap the agent pair', id: 'tutorial', file: 'tutorial.md', snippet: "The kit builds each ticket with a specialist agent and a ==verifier== agent. The ==verifier== is blind: it gets the ticket and the diff, and never the report of the specialist." },
    ],
  };

  const NAV = [
    { title: 'Start here', id: 'overview' },
    { title: 'Tutorial', id: 'tutorial' },
    { title: 'How-to guides', children: ['Bootstrap a single-stack repository', 'Bootstrap a monorepo', 'Run an economy chain', 'Watch and resume a run', 'Point the tiers at a gateway', 'Add a harness adapter', 'Use the kit after to-spec and to-tickets'].map((t) => ({ title: t })) },
    { title: 'Explanation', children: ['Why the verifier is blind', 'Why each specialist has a verifier twin', 'The mode trade-off', 'Prior art', 'The enforcement model and its limits', 'The decision records'].map((t) => ({ title: t })) },
    { title: 'Reference', children: [{ title: 'The workflow file' }, { title: 'delegate run', id: 'run' }, { title: 'delegate status and delegate watch' }, { title: 'The commands' }, { title: 'delegate docs' }, { title: 'The claude-code adapter' }, { title: 'The opencode adapter' }] },
    { title: 'Glossary', id: 'glossary' },
  ];

  const slug = (s) => s.toLowerCase().replace(/`/g, '').replace(/[^a-z0-9 -]/g, '').trim().replace(/\s+/g, '-');
  const hrefToId = (href) => {
    if (!href || href[0] === '#' || /^https?:/.test(href)) return null;
    const base = href.split('#')[0].split('/').pop();
    return { 'overview.md': 'overview', 'tutorial.md': 'tutorial', 'run.md': 'run', 'glossary.md': 'glossary' }[base] || null;
  };
  const RX = /\[\*\*(.+?)\*\*\](?:\(([^)]+)\))?|\[([^\]]+)\]\(([^)]+)\)|`([^`]+)`|\*\*(.+?)\*\*|\*([^*]+)\*|==([^=]+)==/g;
  function parse(text, go) {
    const out = []; let last = 0, m;
    const push = (o) => out.push(Object.assign({ plain: false, code: false, bold: false, em: false, link: false, term: false, linkCode: false, hl: false, go: undefined }, o));
    const hlSplit = (t, extra) => t.split(/(==[^=]+==)/).forEach((s) => s && (s.startsWith('==') ? push(Object.assign({ t: s.slice(2, -2), hl: true }, extra || {})) : push(Object.assign({ t: s }, extra || { plain: true }))));
    RX.lastIndex = 0;
    while ((m = RX.exec(text))) {
      if (m.index > last) push({ t: text.slice(last, m.index), plain: true });
      if (m[1] != null) { const id = hrefToId(m[2]); if (m[1].includes('==')) push({ t: m[1].replace(/==/g, ''), hl: true }); else push({ t: m[1], term: true, go: id ? () => go(id) : undefined }); }
      else if (m[3] != null) { const id = hrefToId(m[4]); const isCode = /^`[^`]+`$/.test(m[3]); const g = id ? () => go(id) : undefined;
        if (isCode) push({ t: m[3].slice(1, -1), linkCode: true, go: g });
        else m[3].split(/(`[^`]+`)/).forEach((s) => s && (s[0] === '`' ? push({ t: s.slice(1, -1), linkCode: true, go: g }) : push({ t: s, link: true, go: g }))); }
      else if (m[5] != null) push({ t: m[5].replace(/==/g, ''), code: true });
      else if (m[6] != null) push({ t: m[6], bold: true });
      else if (m[7] != null) push({ t: m[7], em: true });
      else if (m[8] != null) push({ t: m[8], hl: true });
      last = RX.lastIndex;
    }
    if (last < text.length) push({ t: text.slice(last), plain: true });
    return out;
  }

  function build(comp, opts) {
    opts = opts || {};
    const st = comp.state;
    const go = (id) => { comp.setState({ page: id, hover: null }); try { localStorage.setItem(opts.key, id); } catch (e) {} if (opts.scroller && opts.scroller.current) opts.scroller.current.scrollTop = 0; window.scrollTo(0, 0); };
    const pageId = st.page || 'overview';
    const isSearch = pageId === 'search', isMobile = pageId === 'mobile';
    const docId = isSearch ? 'overview' : isMobile ? 'overview' : pageId;
    const P = PAGES[docId];
    let n2 = 0, n3 = 0;
    const blocks = P.blocks.map((b, i) => {
      const o = { isH2: b.k === 'h2', isH3: b.k === 'h3', isP: b.k === 'p', isUl: b.k === 'ul', isOl: b.k === 'ol', isCode: b.k === 'code', isTable: b.k === 'table', isNote: b.k === 'note' };
      if (o.isH2 || o.isH3) {
        const numbered = !/^\d/.test(b.t);
        if (o.isH2) { n2++; n3 = 0; } else n3++;
        o.num = numbered && opts.numbered ? (o.isH2 ? n2 + '.' : n2 + '.' + n3 + '.') : '';
        o.runs = parse(b.t, go); o.text = b.t.replace(/`/g, ''); o.id = slug(b.t);
        const key = docId + i;
        o.pil = st.hover === key ? 1 : 0;
        o.enter = () => comp.setState({ hover: key }); o.leave = () => comp.setState({ hover: null });
      }
      if (o.isP) o.runs = parse(b.t, go);
      if (o.isUl || o.isOl) o.items = b.items.map((t, j) => ({ runs: parse(t, go), n: j + 1 + '.' }));
      if (o.isCode) { o.text = b.text; o.lang = b.lang; }
      if (o.isTable) { o.head = b.rows[0].map((t) => ({ runs: parse(t, go) })); o.rows = b.rows.slice(1).map((r, j) => ({ odd: j % 2 === 0, bg: j % 2 === 0 ? opts.rowA : opts.rowB, cells: r.map((t, k) => ({ runs: parse(t, go), first: k === 0 })) })); }
      if (o.isNote) { const w = b.kind === 'warn'; o.runs = parse(b.t, go); o.title = b.title; o.label = b.title.toUpperCase(); o.warn = w; Object.assign(o, w ? opts.warnTone || {} : opts.noteTone || {}); }
      return o;
    });
    const toc = blocks.filter((b) => b.isH2 || (opts.tocH3 && b.isH3)).map((b) => ({ text: b.text, num: b.num, sub: b.isH3, go: () => {} }));
    const nav = NAV.map((s, i) => {
      const cur = s.id === docId && !isSearch;
      const children = (s.children || []).map((c, j) => ({ num: (i + 1) + '.' + (j + 1) + '.', title: c.title, current: c.id === docId && !isSearch, go: c.id ? () => go(c.id) : () => {}, live: !!c.id, toc: c.id === docId && !isSearch ? toc : [] }));
      return { num: (i + 1) + '.', title: s.title, id: s.id, current: cur, isSection: !!s.children, isPage: !s.children, go: s.id ? () => go(s.id) : () => {}, children, toc: cur ? toc : [] };
    });
    const flatPages = [];
    NAV.forEach((s) => (s.children ? s.children.forEach((c) => flatPages.push({ title: c.title, section: s.title, id: c.id })) : flatPages.push({ title: s.title, section: null, id: s.id })));
    const allPages = flatPages.map((f) => ({ title: f.title, section: f.section || '', current: f.id === docId && !isSearch, live: !!f.id, go: f.id ? () => go(f.id) : () => {} }));
    const tabs = [['overview', 'Start here'], ['tutorial', 'Tutorial'], ['run', 'Reference'], ['glossary', 'Glossary'], ['search', 'Search'], ['mobile', 'Mobile']].map(([id, label]) => ({ label, active: id === pageId, go: () => go(id), bg: id === pageId ? '#1f1f1f' : 'transparent', fg: id === pageId ? '#fff' : '#3a3a3a' }));
    const sr = SEARCH.results.map((r, i) => ({ title: r.title, section: r.section || '', hasSection: !!r.section, file: r.file, n: i + 1, snippet: parse(r.snippet, go), go: r.id ? () => go(r.id) : () => {} }));
    const link = (x) => (x ? { title: x.title, go: x.id ? () => go(x.id) : () => {} } : null);
    return {
      pageId, isSearch, isDoc: !isSearch, isMobile, isDesktop: !isMobile,
      title: P.title, titleRuns: parse(P.titleRuns || P.title, go), navTitle: isSearch ? 'Search' : (P.navTitle || P.title), file: P.file, sectionName: isSearch ? '' : (P.section || ''), hasSection: !isSearch && !!P.section,
      man: P.man, manName: P.manName, synopsis: P.synopsis || '', hasSynopsis: !!P.synopsis,
      blocks, toc, hasToc: toc.length > 0, nav, allPages, tabs,
      prev: link(P.prev), next: link(P.next), hasPrev: !isSearch && !!P.prev, hasNext: !isSearch && !!P.next,
      query: SEARCH.query, results: sr, resultCount: sr.length,
      dark: !!st.dark, toggleDark: () => comp.setState((s) => ({ dark: !s.dark })), darkLabel: st.dark ? 'Light' : 'Dark',
      goSearch: () => go('search'), goHome: () => go('overview'),
    };
  }

  window.DelegateDocs = { PAGES, NAV, SEARCH, parse, build };
})();
