import {
  createServer,
  type IncomingMessage,
  type ServerResponse,
} from "node:http";
import { generateKeyPairSync, sign, verify } from "node:crypto";

const { privateKey, publicKey } = generateKeyPairSync("rsa", {
  modulusLength: 2048,
});
const keyId = "undercover-e2e";
const users = new Map(
  [
    ["HND-01", "dana.whitfield@undercover.example", "Dana Whitfield"],
    ["HND-02", "marcus.oyelaran@undercover.example", "Marcus Oyelaran"],
    ["HND-03", "priya.raman@undercover.example", "Priya Raman"],
  ].map(([id, email, name]) => [email, { id, email, name }]),
);

function encoded(value: unknown): string {
  return Buffer.from(JSON.stringify(value)).toString("base64url");
}

function token(email: string): string {
  const user = users.get(email);
  if (!user) throw new Error("Unknown test handler");
  const now = Math.floor(Date.now() / 1000);
  const header = encoded({ alg: "RS256", kid: keyId, typ: "JWT" });
  const payload = encoded({
    aud: "authenticated",
    email,
    exp: now + 3600,
    iat: now,
    iss: "http://127.0.0.1:8005/auth/v1",
    role: "authenticated",
    sub: user.id,
  });
  const input = `${header}.${payload}`;
  return `${input}.${sign("RSA-SHA256", input, privateKey).toString("base64url")}`;
}

function user(email: string) {
  const handler = users.get(email);
  if (!handler) throw new Error("Unknown test handler");
  return {
    id: handler.id,
    aud: "authenticated",
    role: "authenticated",
    email,
    email_confirmed_at: "2026-01-01T00:00:00Z",
    app_metadata: {},
    user_metadata: { name: handler.name },
    identities: [],
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function session(email: string) {
  return {
    access_token: token(email),
    token_type: "bearer",
    expires_in: 3600,
    expires_at: Math.floor(Date.now() / 1000) + 3600,
    refresh_token: `refresh-${email}`,
    user: user(email),
  };
}

function json(response: ServerResponse, body: unknown, status = 200) {
  response.statusCode = status;
  response.end(JSON.stringify(body));
}

function emailFromAuthorization(request: IncomingMessage): string | null {
  const value = request.headers.authorization ?? "";
  const supplied = value.replace(/^Bearer /, "");
  const parts = supplied.split(".");
  if (parts.length !== 3) return null;
  try {
    const payload = JSON.parse(
      Buffer.from(parts[1], "base64url").toString(),
    ) as { email?: string };
    if (
      !verify(
        "RSA-SHA256",
        `${parts[0]}.${parts[1]}`,
        publicKey,
        Buffer.from(parts[2], "base64url"),
      )
    )
      return null;
    return payload.email ?? null;
  } catch {
    return null;
  }
}

function handleUser(request: IncomingMessage, response: ServerResponse) {
  const email = emailFromAuthorization(request);
  if (!email || !users.has(email)) {
    json(response, { message: "Invalid token" }, 401);
    return;
  }
  json(response, user(email));
}

async function handleToken(request: IncomingMessage, response: ServerResponse) {
  let raw = "";
  for await (const chunk of request) raw += String(chunk);
  const body = JSON.parse(raw) as { email?: string; password?: string };
  const grant = new URL(
    request.url ?? "",
    "http://127.0.0.1:8005",
  ).searchParams.get("grant_type");
  const email = body.email;
  if (
    grant === "password" &&
    email &&
    users.has(email) &&
    body.password === "test-password"
  ) {
    json(response, session(email));
    return;
  }
  json(response, { message: "Invalid login credentials" }, 400);
}

async function handle(request: IncomingMessage, response: ServerResponse) {
  response.setHeader("Access-Control-Allow-Origin", "*");
  response.setHeader(
    "Access-Control-Allow-Headers",
    "apikey, authorization, content-type, x-client-info, x-supabase-api-version",
  );
  response.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  response.setHeader("Content-Type", "application/json");
  if (request.method === "OPTIONS") {
    response.statusCode = 204;
    response.end();
    return;
  }
  const path = new URL(request.url ?? "/", "http://127.0.0.1:8005").pathname;
  if (path === "/health") {
    json(response, { status: "ok" });
    return;
  }
  if (path === "/auth/v1/.well-known/jwks.json") {
    const exported: unknown = publicKey.export({ format: "jwk" });
    const jwk = exported as JsonWebKey;
    json(response, {
      keys: [{ ...jwk, alg: "RS256", kid: keyId, use: "sig" }],
    });
    return;
  }
  if (path === "/auth/v1/settings") {
    json(response, { disable_signup: true, external: {} });
    return;
  }
  if (path === "/auth/v1/user") {
    handleUser(request, response);
    return;
  }
  if (path === "/auth/v1/logout") {
    response.statusCode = 204;
    response.end();
    return;
  }
  if (path === "/auth/v1/token" && request.method === "POST") {
    await handleToken(request, response);
    return;
  }
  json(response, { message: "Not found" }, 404);
}

const server = createServer((request, response) => {
  void handle(request, response);
});
server.listen(8005, "127.0.0.1");
for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => server.close());
}
