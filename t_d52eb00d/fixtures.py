#!/usr/bin/env python3
"""Run the committed docs/manual-testing fixtures against the E2E n8n stack (E2E_KEEP=1).

Imports each fixture as-is (for UI review) and executes each documented branch as a
variant workflow through `n8n execute`, mirroring the step-by-step reconnects that
AGENTS.md describes. Secrets are read from the environment and never printed.
"""
import copy, json, os, random, subprocess, sys, urllib.request, http.cookiejar, uuid

REPO = sys.argv[1]
N8N = os.environ.get("N8N_URL", "http://127.0.0.1:25678")
COMPOSE = ["docker", "compose", "-f", f"{REPO}/test/e2e/docker-compose.yml"]
OWNER = {"emailOrLdapLoginId": "e2e@example.com", "password": "Testpassw0rd!"}
WF_DIR = f"{REPO}/docs/manual-testing/workflows"

COOKIE = {"v": ""}


def api(path, method="GET", body=None):
    req = urllib.request.Request(
        N8N + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "browser-id": "fixture-runner",
                 **({"cookie": COOKIE["v"]} if COOKIE["v"] else {})},
    )
    with urllib.request.urlopen(req) as r:
        sc = r.headers.get_all("Set-Cookie") or []
        if sc:
            COOKIE["v"] = "; ".join(c.split(";")[0] for c in sc)
        d = json.loads(r.read() or b"null")
    return d.get("data", d) if isinstance(d, dict) else d


def execute(wf_id):
    for _ in range(3):
        port = 15000 + random.randint(0, 20000)
        p = subprocess.run(COMPOSE + ["exec", "-T", "-e", "N8N_RUNNERS_ENABLED=false",
                                      "-e", f"N8N_RUNNERS_BROKER_PORT={port}", "n8n", "n8n",
                                      "execute", f"--id={wf_id}", "--rawOutput"],
                           capture_output=True, text=True)
        i = p.stdout.find("{")
        if i >= 0:
            data, _ = json.JSONDecoder().raw_decode(p.stdout[i:])
            return data
    raise RuntimeError(f"n8n execute produced no JSON: {p.stderr[-400:]}")


def outputs(run):
    rd = run.get("data", {}).get("resultData", {})
    out = {}
    for node, runs in rd.get("runData", {}).items():
        r = runs[0]
        items = (r.get("data") or {}).get("main", [[]])
        out[node] = {"items": [i.get("json") for i in (items[0] or [])] if items else [],
                     "error": (r.get("error") or {}).get("message")}
    return out, (rd.get("error") or {}).get("message")


login = api("/rest/login", "POST", OWNER)
cb = api("/rest/credentials", "POST", {"name": "Couchbase Credentials", "type": "couchbaseApi", "data": {
    "couchbaseConnectionString": "couchbase://couchbase", "couchbaseUsername": "Administrator",
    "couchbasePassword": "password"}})
oa = api("/rest/credentials", "POST", {"name": "OpenAi account", "type": "openAiApi", "data": {
    "apiKey": os.environ["OPENAI_API_KEY"], "url": "https://api.openai.com/v1"}})
CREDS = {"couchbaseApi": {"id": cb["id"], "name": cb["name"]}, "openAiApi": {"id": oa["id"], "name": oa["name"]}}


def load(name):
    w = json.load(open(f"{WF_DIR}/{name}.workflow.json"))
    for n in w["nodes"]:
        for t in list((n.get("credentials") or {}).keys()):
            n["credentials"][t] = CREDS[t]
    return w


def create(name, w):
    body = {"name": name, "nodes": w["nodes"], "connections": w["connections"],
            "settings": w.get("settings", {}), "active": False}
    return api("/rest/workflows", "POST", body)["id"]


def variant(w, chain, extra_nodes=(), drop=("When chat message received",), tweak=None):
    """Keep every ai_* sub-node connection, replace all `main` wiring with `chain`."""
    v = copy.deepcopy(w)
    v["nodes"] = [n for n in v["nodes"] if n["name"] not in drop] + list(extra_nodes)
    conns = {}
    for src, c in v["connections"].items():
        if src in drop:
            continue
        kept = {k: o for k, o in c.items() if k != "main"}
        if kept:
            conns[src] = kept
    for a, b in zip(chain, chain[1:]):
        conns.setdefault(a, {})["main"] = [[{"node": b, "type": "main", "index": 0}]]
    v["connections"] = conns
    if tweak:
        tweak(v)
    return v


def node(name, type_, params, pos=(0, 0), version=1):
    return {"id": str(uuid.uuid4()), "name": name, "type": type_, "typeVersion": version,
            "position": list(pos), "parameters": params}


def by_name(v, name):
    return next(n for n in v["nodes"] if n["name"] == name)


def n1ql(stmt):
    p = subprocess.run(COMPOSE + ["exec", "-T", "couchbase", "curl", "-s", "-u", "Administrator:password",
                                  "http://127.0.0.1:8093/query/service", "--data-urlencode", f"statement={stmt}"],
                       capture_output=True, text=True)
    return json.loads(p.stdout).get("results", [])


results = []


def record(fixture, step, run, check):
    out, err = outputs(run)
    ok, detail = check(out, err)
    results.append({"fixture": fixture, "step": step, "pass": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'} {fixture} :: {step} :: {detail}", flush=True)
    return out


TRIGGER = "When clicking ‘Execute workflow’"
ids = {}

# ---------------------------------------------------------------- core fixture (as committed)
core = load("core-nodes-test-all")
ids["core"] = create("Core Couchbase Nodes Test All", core)
run = execute(ids["core"])


def core_check(out, err):
    want = ["Seed KV Document", "Read KV Document", "Upsert KV Document", "Query SQL++ Document",
            "Create FTS Index", "Search Full Text", "Search Advanced Raw JSON",
            "Memory Manager Insert", "Memory Manager Load"]
    missing = [n for n in want if n not in out]
    # Documented: "The FTS index step continues when the index already exists" (pre-provisioned).
    errs = {n: o["error"] for n, o in out.items()
            if o["error"] and not (n == "Create FTS Index" and "index exists" in o["error"])}
    hits = len(out.get("Search Full Text", {}).get("items", []))
    mem = out.get("Memory Manager Load", {}).get("items", [])
    return (not missing and not errs and not err,
            f"ran={len(out)} nodes missing={missing} errors={errs or err} fts_items={hits} memory_load_items={len(mem)}")


record("core-nodes-test-all", "execute whole workflow (KV/SQL++/FTS/advanced search/chat memory)", run, core_check)

# ---------------------------------------------------------------- vector fixtures
SEED = [
    "The Eiffel Tower is a wrought-iron lattice tower in Paris, France, Europe.",
    "The Colosseum is an ancient amphitheatre in the centre of Rome, Italy, Europe.",
    "Sagrada Familia is a basilica designed by Antoni Gaudi in Barcelona, Spain, Europe.",
    "The Golden Gate Bridge is a suspension bridge in San Francisco, United States.",
]
seed_code = "return " + json.dumps([{"json": {"description": s}} for s in SEED]) + ";"

for kind, label in (("query", "QueryVectorStore"), ("search", "SearchVectorStore")):
    fx = f"{kind}-vector-store-test-all"
    w = load(fx)
    ids[kind] = create(w.get("name") or f"{label} Test All", w)
    ins, upd, many = f"Insert [{label}]", None, f"Get Many [{label}]"
    upd = next(n["name"] for n in w["nodes"] if n["name"].startswith(f"Update Documents [{label}]"))

    if kind == "query":
        # The POI collection starts empty; seed it through the plugin's own Insert mode.
        def poi(v):
            n = by_name(v, ins)
            for f, val in (("couchbaseBucket", "travel-agent"), ("couchbaseScope", "vectors"),
                           ("couchbaseCollection", "points-of-interest")):
                n["parameters"][f] = {"__rl": True, "value": val, "mode": "list", "cachedResultName": val}
            # The committed loader is a {"hello":"world"} placeholder; seed real text instead.
            by_name(v, "Default Data Loader")["parameters"]["jsonData"] = "={{ $json.description }}"
        n1ql("DELETE FROM `travel-agent`.vectors.`points-of-interest`")
        seed = variant(w, [TRIGGER, "Seed POI", ins],
                       [node("Seed POI", "n8n-nodes-base.code", {"jsCode": seed_code}, version=2)], tweak=poi)
        record(fx, "seed travel-agent POI via Insert mode",
               execute(create(f"{fx} :: seed", seed)),
               lambda o, e: (not e and not o.get(ins, {}).get("error"), f"error={e or o.get(ins, {}).get('error')}"))
        import time; time.sleep(15)

    record(fx, "Get Many (trigger -> Get Many)", execute(create(f"{fx} :: get many", variant(w, [TRIGGER, many]))),
           lambda o, e: (not e and len(o.get(many, {}).get("items", [])) > 0
                         and "Europe" in json.dumps(o.get(many, {}).get("items", [])),
                         f"items={len(o.get(many, {}).get('items', []))} top={str(o.get(many, {}).get('items', [{}])[0].get('document', {}).get('pageContent'))[:70]!r} error={e or o.get(many, {}).get('error')}"))

    before = {r["id"] for r in n1ql("SELECT META().id FROM `test-insert`.data.data")}
    record(fx, "Insert (trigger -> Code -> Insert)",
           execute(create(f"{fx} :: insert", variant(w, [TRIGGER, "Code in JavaScript", ins]))),
           lambda o, e: (not e and not o.get(ins, {}).get("error"), f"error={e or o.get(ins, {}).get('error')}"))
    after = {r["id"] for r in n1ql("SELECT META().id FROM `test-insert`.data.data")}
    new = sorted(after - before)
    doc_id = new[0] if new else None

    def set_id(v):
        by_name(v, upd)["parameters"]["id"] = doc_id
    record(fx, f"Update Documents (copied inserted id {doc_id})",
           execute(create(f"{fx} :: update", variant(w, [TRIGGER, "Code in JavaScript", upd], tweak=set_id))),
           lambda o, e: (doc_id and not e and not o.get(upd, {}).get("error"),
                         f"doc_id={doc_id} error={e or o.get(upd, {}).get('error')}"))

    q = "Which points of interest in Europe are in the database? Use your tool."
    chat = node("Chat Input", "n8n-nodes-base.set", {"mode": "raw", "jsonOutput": json.dumps({"chatInput": q, "sessionId": "fixture-session"})},
                version=3.4)
    retr = {"AI Agent": next(n["name"] for n in w["nodes"] if n["name"].startswith("Retrieve (As VS")),
            "Another AI Agent": next(n["name"] for n in w["nodes"] if n["name"].startswith("Retrieve (As Tool"))}
    for agent, step in (("AI Agent", "Vector Store QA Tool path (Retrieve as VS -> QA tool -> AI Agent)"),
                        ("Another AI Agent", "Retrieve As Tool path (Retrieve as tool -> AI Agent)")):
        c = copy.deepcopy(chat)
        run = execute(create(f"{fx} :: {agent}", variant(w, [TRIGGER, "Chat Input", agent], [c])))
        rd = run["data"]["resultData"]["runData"]
        # Pass = the plugin node returned the seeded landmarks into the agent's tool chain
        # (the QA tool path runs the vector store under "Answer questions with a vector store").
        plugin_out = json.dumps(rd.get(retr[agent], [])) + json.dumps(rd.get("Answer questions with a vector store", []))
        hit = any(k in plugin_out for k in ("Eiffel", "Colosseum", "Sagrada"))
        record(fx, step, run,
               lambda o, e, a=agent, h=hit: (not e and h and bool((o.get(a, {}).get("items") or [{}])[0].get("output")),
                                             f"plugin_returned_landmarks={h} sub_node_runs={len(rd.get(retr[a], []))} "
                                             f"llm_answer={str((o.get(a, {}).get('items') or [{}])[0].get('output'))[:120]!r} "
                                             f"error={e or o.get(a, {}).get('error')}"))

json.dump({"workflow_ids": ids, "results": results}, open(os.path.join(os.path.dirname(__file__), "fixtures-result.json"), "w"), indent=1)
print(f"{sum(r['pass'] for r in results)}/{len(results)} fixture steps passed")
