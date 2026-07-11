/**
 * Selvedge telemetry receiver — Cloudflare Worker.
 *
 * Accepts the opt-in heartbeat documented in docs/telemetry.md (payload
 * schema v1) and writes it to a Workers Analytics Engine dataset.
 * Stores nothing else: no IPs, no headers, no cookies, no KV.
 *
 * The sender is selvedge/telemetry.py. Keep the two in lockstep: a
 * schema bump there requires a matching change here and a
 * docs/telemetry.md update.
 */

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Bound every stored string so a hostile client can't stuff the dataset. */
function clamp(value, max) {
  return typeof value === "string" ? value.slice(0, max) : "";
}

function validate(body) {
  if (typeof body !== "object" || body === null) return null;
  if (body.schema !== 1) return null;
  if (!UUID_RE.test(String(body.install_id || ""))) return null;
  const source = body.source === "server" ? "server" : "cli";
  return {
    install_id: String(body.install_id).toLowerCase(),
    version: clamp(body.version, 32),
    python: clamp(body.python, 8),
    os: clamp(body.os, 16),
    arch: clamp(body.arch, 16),
    source,
  };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Transparency endpoint: humans who curl the root get the docs.
    if (request.method === "GET") {
      return new Response(
        "Selvedge telemetry receiver. Opt-in heartbeats only.\n" +
          "Sender + payload schema: https://github.com/masondelan/selvedge/blob/main/docs/telemetry.md\n" +
          "Receiver source: https://github.com/masondelan/selvedge/tree/main/infra/telemetry-worker\n",
        { status: 200, headers: { "content-type": "text/plain" } },
      );
    }

    if (request.method !== "POST" || url.pathname !== "/v1/ping") {
      return new Response(null, { status: 404 });
    }

    let parsed;
    try {
      parsed = validate(await request.json());
    } catch {
      parsed = null;
    }
    if (!parsed) {
      return new Response(null, { status: 400 });
    }

    // Blob order is load-bearing for the query recipes in README.md:
    // blob1=version blob2=python blob3=os blob4=arch blob5=source blob6=install_id
    env.PINGS.writeDataPoint({
      blobs: [
        parsed.version,
        parsed.python,
        parsed.os,
        parsed.arch,
        parsed.source,
        parsed.install_id,
      ],
      doubles: [],
      indexes: [parsed.install_id.slice(0, 32)],
    });

    return new Response(null, { status: 204 });
  },
};
