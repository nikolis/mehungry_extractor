# How this project is wired together (explained for an Elixir/Mix person)

You already know Elixir, Mix, and Phoenix. This project is Python, so the *words* are different,
but many of the *ideas* rhyme. This doc walks through the setup piece by piece and puts the
Elixir equivalent right next to each one. We'll go slow and use small analogies.

> **The one-sentence version:** `pyproject.toml` is our `mix.exs`, `uv` is our `mix deps.get`,
> `FastAPI` is our Phoenix, `Starlette` is our Plug, and `uvicorn` is our Cowboy. There is **no
> OTP / no supervision tree** — that's the biggest difference, and we'll get to it.

---

## 1. The cheat-sheet (keep this handy)

| Elixir / BEAM world | This project (Python) | What it is |
|---|---|---|
| `mix.exs` | `pyproject.toml` | Project definition + dependency list |
| `deps` in `mix.exs` | `[project.dependencies]` in `pyproject.toml` | The libraries you depend on |
| `mix deps.get` | `uv pip install -e .` | Downloads + installs those libraries |
| `hex` | `PyPI` (pip's package registry) | The public package repository |
| `_build/` + `deps/` | `.venv/` (the "virtual environment") | Where installed code physically lives |
| `mix.lock` | `uv.lock` *(optional; not committed here)* | Pins exact versions |
| A `mix task` / escript | `[project.scripts]` entries | Named commands you can run in the terminal |
| `Phoenix` | `FastAPI` | The full web framework (routing, validation, docs) |
| `Plug` (the spec + conn) | `Starlette` + the `ASGI` spec | The composable request/response toolkit underneath |
| `Cowboy` | `uvicorn` | The actual HTTP server that listens on a port |
| `Plug.Router` / Phoenix Router | `@app.get(...)` decorators | Where routes are declared |
| `Application` behaviour + supervision tree | *(no direct equivalent)* | **Python has none of this built in** |
| A BEAM process (millions of them) | an `async` task **or** a thread | Unit of concurrency (very different!) |

---

## 2. How dependencies are managed — yes, there's a file

**The file is `pyproject.toml`.** It is this project's `mix.exs`. Open it and you'll see something
that should feel familiar:

```toml
[project]
name = "mehungry-extractor"
version = "0.3.0"
requires-python = ">=3.10"
dependencies = [          # <-- like `defp deps do [...] end` in mix.exs
    "requests>=2.31",
    "pydantic>=2.6",
    "spacy>=3.7",
    "sqlalchemy>=2.0",
]

[project.optional-dependencies]   # <-- like Mix "optional: true" / grouped deps
ner = ["scispacy>=0.5.4", "spacy>=3.7"]
api = ["fastapi>=0.110", "uvicorn[standard]>=0.29"]
```

Two things worth noticing:

1. **Version ranges** (`>=2.6`) are exactly like Mix's `"~> 2.6"` — "this version or newer".
2. **Optional dependency groups** (`[project.optional-dependencies]`). The web server libraries
   (`fastapi`, `uvicorn`) live in a group called `api`. They are *not* installed by default, so
   someone who only wants the library doesn't have to download a web server. You opt in:

   ```bash
   uv pip install -e '.[api]'   # "install this project PLUS the 'api' group"
   ```

   Think of it like `mix deps.get --only prod` picking a subset — except here *you* choose the
   group by name.

### What is `uv`? What is `.venv`?

- **`uv`** is the tool that reads `pyproject.toml` and downloads the libraries. It's the
  `mix deps.get` step. (It's a fast, modern replacement for the older `pip` tool.)
- **`.venv/`** is a **"virtual environment"** — a private folder that holds this project's Python
  and all its installed libraries, isolated from the rest of your computer.

  The closest BEAM analogy: imagine if `deps/` also contained *its own copy of the Erlang runtime*,
  and you had to "step inside" it before running anything. That "stepping inside" is:

  ```bash
  source .venv/bin/activate
  ```

  After that, `python` and `mehungry-api` refer to *this project's* versions, not your system's.
  (Unlike Mix, Python doesn't isolate projects automatically — the `.venv` is how we force it.)

### The `-e` flag ("editable install")

`uv pip install -e .` installs the project in **editable** mode: the installed package points back
at your source files, so when you edit the code the change is live — no reinstall. It's similar to
using a `path:` dependency in `mix.exs` that points at a folder you're editing.

---

## 3. What glues the pieces together — is there anything like `Application`?

Here's the big mental shift, so let's be blunt:

> **Python has no OTP.** There is no `Application` behaviour, no supervision tree, no
> `children = [...]` started in order, nothing that automatically restarts a crashed worker.

In Elixir, `Application.start/2` boots a tree of supervisors and workers, and the BEAM keeps them
alive. Python has nothing equivalent baked into the language. A Python program is just: *start at
one function, run top to bottom, exit when done.* If it crashes, it's gone (unless something
outside — like systemd, Docker, or Kubernetes — restarts the whole process).

So then, what plays the role of "the application"? **An object.** In `api/app.py`:

```python
app = FastAPI(title="Mehungry deterministic evidence API", ...)
```

That `app` object **is** the application. In the web world (the ASGI spec, more on that below),
"an application" literally means "a callable object that takes a request and produces a response."
`app` is that object. It is closest in spirit to your Phoenix `Endpoint` — the single thing that
represents "the whole web app" and that the server hands requests to.

### Modules and packages (the smaller glue)

- A `.py` file is a **module** — like a single `.ex` file with a `defmodule`.
- A folder with an `__init__.py` file is a **package** — like a namespace of modules. Our web code
  lives in the package `mehungry_extractor/knowledge/api/`, which contains `models.py`,
  `service.py`, and `app.py`.
- Importing is explicit: `from . import service` ≈ `alias MehungryExtractor.Knowledge.Api.Service`.

There's no automatic wiring: nothing runs until *some function calls another function*. The
"start button" is the console script, which we'll cover in section 6.

---

## 4. The fundamental server — our Cowboy, our Plug

A web request travels through **three layers**. From lowest (closest to the network socket) to
highest (closest to your code):

```
   the internet
        │
        ▼
┌──────────────────┐
│     uvicorn      │   ← the HTTP SERVER. Opens the TCP port, speaks HTTP.   ==  Cowboy
├──────────────────┤
│  Starlette/ASGI  │   ← the TOOLKIT + spec: request/response objects,       ==  Plug
│                  │      middleware, the router machinery.
├──────────────────┤
│     FastAPI      │   ← the FRAMEWORK: nice routing, validates JSON,        ==  Phoenix
│                  │      auto-generates docs.  (built on top of Starlette)
├──────────────────┤
│  your handlers   │   ← health() and analyze() in app.py                    ==  your controllers
└──────────────────┘
```

- **`uvicorn` = Cowboy.** It's the raw HTTP server. It binds to a port (default `8000`), accepts
  TCP connections, parses HTTP, and hands each request "up" to the application. (Under the hood it
  even uses `uvloop`, which is built on **libuv** — yes, the same C library that inspired a lot of
  event-loop designs.)

- **ASGI = the Plug *spec*, and `Starlette` = a Plug-like toolkit.** ASGI ("Asynchronous Server
  Gateway Interface") is a **contract**: "a web app is a callable that receives a request and a way
  to send a response." That's exactly the role of the `Plug` behaviour and the `%Plug.Conn{}`
  struct — a shared shape everything agrees on, so servers and frameworks are interchangeable.
  Because of this contract, you could swap `uvicorn` for another ASGI server without touching your
  code — just like swapping Cowboy for another Plug-compatible adapter.

- **`FastAPI` = Phoenix (the friendly framework).** It sits on top of Starlette and adds the
  ergonomic bits: declaring routes, automatically turning incoming JSON into validated objects, and
  generating live API docs at `/docs`.

So when you read "FastAPI + uvicorn," translate it in your head to **"Phoenix + Cowboy."**

---

## 5. How the routes are defined

In Phoenix you'd write routes in a router file:

```elixir
get  "/health",  HealthController, :index
post "/analyze", AnalyzeController, :create
```

In this project the routes live in `mehungry_extractor/knowledge/api/app.py`, and each route is a
plain function with a **decorator** on top (a decorator is the `@thing` line — think of it as an
attribute/macro that "registers" the function):

```python
@app.get("/health")            # register GET /health  → the function below
def health():
    return {"status": "ok", ...}

@app.post("/analyze", response_model=AnalyzeResponse)   # register POST /analyze
def analyze(req: AnalyzeRequest, ctx = Depends(get_context)):
    return service.analyze_batch(req.pmids, req.options, ...)
```

Reading that the Elixir way:

- `@app.post("/analyze")` = the router line `post "/analyze", ...`.
- The function `analyze(...)` = the controller action.
- `req: AnalyzeRequest` = "parse the request body into this shape **and validate it**." FastAPI
  does automatically what you'd do with an Ecto changeset or a params validation Plug. If the JSON
  is wrong, the caller gets a `422` error and your function never even runs.
- `Depends(get_context)` = **dependency injection**, very much like a `plug` that runs first and
  stuffs something into `conn.assigns`. Here `get_context` opens the database/corpus and hands it
  to the handler. (Bonus: in tests we swap this out to point at a fake, offline database — same
  trick as overriding a plug in tests.)
- The handler just **returns a value**, and FastAPI turns it into a JSON HTTP response — like a
  Phoenix action returning `json(conn, data)`.

---

## 6. How does it actually run? (the "start button")

Remember there's no OTP auto-start. Something has to explicitly call the start function. That
something is a **console script**, declared in `pyproject.toml`:

```toml
[project.scripts]
mehungry-api = "mehungry_extractor.knowledge.api.app:run"
```

This says: *"create a terminal command named `mehungry-api`; when someone runs it, call the `run`
function in `app.py`."* It's the equivalent of defining a `mix` task or an escript entry point.

And `run` is tiny — it just tells uvicorn to start serving the `app` object:

```python
def run():
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
```

So the full chain of "how it boots" is:

```
you type:   mehungry-api
   │
   ▼
runs:       app.py  →  run()
   │
   ▼
calls:      uvicorn.run(app)      # start Cowboy, hand it the Phoenix Endpoint
   │
   ▼
uvicorn opens port 8000 and waits for HTTP requests, forever.
```

`uvicorn.run(...)` **blocks** — it runs until you press Ctrl-C. That's the whole lifetime of the
program. (Compare: `mix phx.server` blocking in your terminal.)

---

## 7. One process? One thread? How is concurrency handled?

This is where Python and the BEAM differ the most, so let's build it up carefully.

### First, how the BEAM does it (your baseline)

On the BEAM, every request is its own **lightweight process**. You can have *millions*. The
scheduler runs them **preemptively** across **all CPU cores** — true parallelism, for free. If one
crashes, a supervisor restarts it. You basically never think about "blocking."

### Now, how *this* Python server does it — by default

Start with the honest baseline: **`mehungry-api` runs a single OS process with a single main
thread.** Inside that one thread runs an **event loop** (Python's `asyncio`, powered by uvloop).

An event loop is like **one very fast waiter in a restaurant**:

- The waiter (one thread) serves many tables (many connections).
- While table 5's food is cooking (waiting on the network or the database), the waiter doesn't
  stand there — they go take table 6's order, refill table 2's water, etc.
- The instant table 5's food is ready, they come back to it.

So a *single* thread can juggle *thousands* of connections — **as long as nobody makes the waiter
stand still.** "Standing still" = a slow operation that doesn't cooperate (a blocking network call,
a heavy computation). One rude table that demands the waiter stand and wait would freeze *everyone*.

### The catch in this project: our handlers are "blocking"

Our `analyze()` handler is a normal `def` (not `async def`), and it does slow, blocking work: it
downloads papers over the network and reads/writes SQLite. If that ran *on the waiter's own feet*,
it would freeze the whole event loop.

FastAPI/Starlette knows this and does something clever automatically: **any plain `def` handler is
run in a small pool of background helper threads** (a thread pool, ~40 threads by default). So:

- The **event loop thread** stays free to accept new connections and shuffle work around.
- Each slow `analyze()` call is handed to a **helper thread** so it can block all it wants without
  freezing the others.

Picture it as: one head waiter (the event loop) + a handful of assistants (the thread pool) who go
do the slow trips to the kitchen.

### The famous "GIL" — why threads aren't the same as BEAM cores

Python has a **Global Interpreter Lock (GIL)**: within one process, **only one thread can execute
Python code at a time.** Threads take turns.

- For **I/O-bound** work (network, disk, database) this is *fine*, because while a thread is waiting
  on the network it *releases* the GIL, letting another thread run. Our workload is mostly I/O
  (fetching papers, DB queries), so the thread pool genuinely helps.
- For **CPU-bound** work (crunching numbers in pure Python), the GIL means threads **don't** give
  you multi-core parallelism. This is the opposite of the BEAM, which really does run code on all
  cores at once.

### So how do you use multiple CPU cores? Run multiple processes.

Because one process = one GIL, the standard way to use all your cores in Python is to run
**several copies of the whole server as separate OS processes**, with something load-balancing
across them:

```bash
uvicorn mehungry_extractor.knowledge.api.app:app --workers 4   # 4 independent processes
```

Each worker is its own OS process with its own event loop and its own GIL. This is the closest
Python gets to "using all the schedulers," and notice how manual it is compared to the BEAM, which
spreads work across cores automatically without you asking.

### The summary table

| Question | BEAM / Elixir | This Python server (default) |
|---|---|---|
| Unit of concurrency | millions of cheap processes | async tasks + a small thread pool |
| Scheduling | preemptive, automatic | cooperative event loop; slow `def`s offloaded to threads |
| Uses all CPU cores? | yes, automatically | no — one process = one GIL; run `--workers N` for more cores |
| Good at I/O-bound load (network/DB)? | yes | **yes** (this is exactly our workload) |
| Good at CPU-bound parallelism? | yes | not within one process (GIL) |
| Crash handling | supervisors restart it | nothing built in; an outside tool (systemd/Docker/k8s) restarts the whole process |

**Bottom line for our little service:** it's one process, one event-loop thread, plus a pool of
worker threads that run the blocking request handlers. For a service that receives a few PMIDs and
spends most of its time waiting on PubMed and SQLite, that's a perfectly good fit — the waiting is
exactly what an event loop + threads handle well. If we ever needed to serve heavy traffic across
many cores, we'd launch multiple worker processes.

---

## 8. Putting the whole story in one picture

```
pyproject.toml   ── defines the project + deps        (≈ mix.exs)
      │  uv pip install -e '.[api]'                     (≈ mix deps.get)
      ▼
   .venv/         ── isolated copy of Python + libs    (≈ its own deps/ + runtime)
      │
      │  you run:  mehungry-api                          (≈ a mix task)
      ▼
  app.py:run()  ── calls uvicorn.run(app)
      │
      ▼
   uvicorn (HTTP server, the Cowboy)  ── opens port 8000
      │  each request goes up through…
      ▼
   Starlette/ASGI (the Plug spec + toolkit)
      │
      ▼
   FastAPI (the Phoenix framework)  ── matches the route, validates JSON
      │
      ▼
   your handler  health() / analyze()   ── runs in a worker thread if it's a blocking def
      │
      ▼
   service.analyze_batch(...)  ── the real work: ingest → extract → filter → synthesize
      │
      ▼
   returns a value → FastAPI turns it into a JSON response → back down to the caller
```

That's the whole machine. If you remember just four swaps — **`pyproject.toml`≈`mix.exs`**,
**`uv`≈`mix deps.get`**, **FastAPI/uvicorn ≈ Phoenix/Cowboy**, and **"no OTP; concurrency is an
event loop + a thread pool, not BEAM processes"** — you understand how this project is set up.
