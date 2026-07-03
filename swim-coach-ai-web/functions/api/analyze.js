// BYO-key proxy: forwards the analysis request to Anthropic using the caller's own key.
// The key is sent per-request (x-swimlens-key) and is never stored on the server.
export async function onRequestPost({ request }) {
  const key = (request.headers.get("x-swimlens-key") || "").trim();
  if (!key) {
    return new Response(
      JSON.stringify({ error: { type: "no_api_key", message: "Add your own Anthropic API key in the app first (Settings)." } }),
      { status: 401, headers: { "content-type": "application/json" } }
    );
  }
  const body = await request.text();
  let r;
  try {
    r = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
      },
      body,
    });
  } catch (e) {
    return new Response(
      JSON.stringify({ error: { type: "upstream_error", message: "Could not reach Anthropic: " + e } }),
      { status: 502, headers: { "content-type": "application/json" } }
    );
  }
  return new Response(r.body, { status: r.status, headers: { "content-type": "application/json" } });
}
