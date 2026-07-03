// Shared helpers for the Swim Coach AI Cloudflare Functions.
// Underscore-prefixed => NOT routed as an endpoint.

export const json = (obj, status = 200, headers = {}) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });

const enc = new TextEncoder();

export function randHex(bytes = 32) {
  const a = new Uint8Array(bytes);
  crypto.getRandomValues(a);
  return [...a].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// PBKDF2-SHA256 password hashing (Web Crypto, available in the Workers runtime).
export async function hashPassword(password, saltHex) {
  const salt = Uint8Array.from(saltHex.match(/.{2}/g).map((h) => parseInt(h, 16)));
  const key = await crypto.subtle.importKey("raw", enc.encode(password), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", salt, iterations: 120000, hash: "SHA-256" },
    key,
    256
  );
  return [...new Uint8Array(bits)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// Constant-time-ish string compare for hashes.
export function safeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let out = 0;
  for (let i = 0; i < a.length; i++) out |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return out === 0;
}

export function cookie(name, value, maxAge) {
  const parts = [`${name}=${value}`, "Path=/", "HttpOnly", "Secure", "SameSite=Lax"];
  if (maxAge != null) parts.push(`Max-Age=${maxAge}`);
  return parts.join("; ");
}

export function getCookie(req, name) {
  const c = req.headers.get("Cookie") || "";
  const m = c.match(new RegExp("(?:^|; )" + name + "=([^;]+)"));
  return m ? m[1] : null;
}

export async function currentUser(req, env) {
  if (!env.DB) return null;
  const tok = getCookie(req, "sid");
  if (!tok) return null;
  const row = await env.DB.prepare(
    "SELECT s.user_id, s.expires, u.email FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?"
  ).bind(tok).first();
  if (!row) return null;
  if (row.expires < Date.now()) {
    await env.DB.prepare("DELETE FROM sessions WHERE token = ?").bind(tok).run();
    return null;
  }
  return { id: row.user_id, email: row.email, token: tok };
}

export const SESSION_MS = 1000 * 60 * 60 * 24 * 30; // 30 days
