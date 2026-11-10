import {
  createServer,
  type IncomingMessage,
  type ServerResponse,
} from "node:http";

const requests: { method: string; url: string; body: unknown }[] = [];

function runRecord(kind: string, body: unknown) {
  const input = (body ?? {}) as {
    concurrency?: number;
    evaluation_set?: string;
    tiny?: boolean;
  };
  return {
    run_id: 7,
    kind,
    concurrency: input.concurrency ?? 3,
    status: "in_progress",
    model_name: "openai/gpt-6-luna",
    reasoning_effort: "low",
    evaluation_set: input.evaluation_set ?? null,
    tiny: input.tiny ?? null,
    summary: null,
    created_at: "2026-11-10T12:00:00Z",
    completed_at: null,
    stopped_at: null,
    claims: [],
    model_cost: 0,
    prompt_tokens: 0,
    completion_tokens: 0,
    total_tokens: 0,
    active_seconds: 0,
    wall_seconds: 0,
  };
}

async function handleRequest(
  request: IncomingMessage,
  response: ServerResponse,
): Promise<void> {
  response.setHeader("Content-Type", "application/json");
  if (request.method === "GET" && request.url === "/health") {
    response.end(JSON.stringify({ status: "ok" }));
    return;
  }
  if (request.method === "GET" && request.url === "/requests") {
    response.end(JSON.stringify(requests));
    return;
  }
  if (request.method === "POST") {
    let raw = "";
    for await (const chunk of request) raw += String(chunk);
    const body = raw ? (JSON.parse(raw) as unknown) : null;
    const url = request.url ?? "";
    requests.push({ method: request.method, url, body });
    response.statusCode = 202;
    if (url === "/dispatches") {
      response.end(JSON.stringify(runRecord("dispatch", body)));
    } else if (url === "/evaluations") {
      response.end(JSON.stringify(runRecord("evaluation", body)));
    } else if (url.endsWith("/work")) {
      response.end(JSON.stringify({ result: "Accepted" }));
    } else {
      response.end(JSON.stringify({ status: "accepted" }));
    }
    return;
  }
  response.statusCode = 404;
  response.end(JSON.stringify({ detail: "Not found" }));
}

const server = createServer((request, response) => {
  void handleRequest(request, response);
});

server.listen(8003, "127.0.0.1");
for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => server.close());
}
