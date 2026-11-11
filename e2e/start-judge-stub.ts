import { createServer } from "node:http";

const server = createServer((request, response) => {
  response.setHeader("Content-Type", "application/json");
  if (request.url === "/health") {
    response.end(JSON.stringify({ status: "ok" }));
    return;
  }
  if (request.method === "POST" && request.url === "/api/alpha/decisions") {
    request.resume();
    response.end(
      JSON.stringify({
        model: "typesafe/jev-1.13",
        answers: {
          grade: {
            type: "score",
            score: 1.51,
            probabilities: { "0": 0.01, "1": 0.49, "2": 0.48, "3": 0.02 },
          },
          same_grounds: { type: "noul", noul: 0.75 },
          same_section: { type: "noul", noul: 0.25 },
        },
        usage: { input_tokens: 10, output_tokens: 1, cost: 0.001 },
      }),
    );
    return;
  }
  response.statusCode = 404;
  response.end(JSON.stringify({ message: "Not found" }));
});

server.listen(8006, "127.0.0.1");
for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => server.close());
}
