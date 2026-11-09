import { createServer } from "node:http";

const requests: { method: string; url: string }[] = [];

const server = createServer((request, response) => {
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
    requests.push({ method: request.method, url: request.url ?? "" });
    response.statusCode = 202;
    response.end(JSON.stringify({ status: "accepted" }));
    return;
  }
  response.statusCode = 404;
  response.end(JSON.stringify({ detail: "Not found" }));
});

server.listen(8003, "127.0.0.1");
for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => server.close());
}
