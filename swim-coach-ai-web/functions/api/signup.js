import { json, randHex, hashPassword, cookie, SESSION_MS } from "./_auth.js";
export async function onRequestPost({ request, env }) {
  if (!env.DB) return json({ error: "Accounts are not configured (no D1 database bound)." }, 501);
  let body;
  try { body = await request.json(); } catch { return json({ error: "bad_json" }, 400); }
  const email = (body.email || "").trim().toLowerCase();
  const password = body.password || "";
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return json({ error: "Enter a valid email address." }, 400);
  if (password.length < 8) return json({ error: "Password must be at least 8 characters." }, 400);
  const exists = await env.DB.prepare("SELECT id FROM users WHERE email = ?").bind(email).first();
  if (exists) return json({ error: "An account with that email already exists." }, 409);
  const id = randHex(16), salt = randHex(16), created = Date.now();
  const ph = await hashPassword(password, salt);
  await env.DB.prepare("INSERT INTO users (id,email,pass_hash,salt,created) VALUES (?,?,?,?,?)")
    .bind(id, email, ph, salt, created).run();
  const token = randHex(32);
  await env.DB.prepare("INSERT INTO sessions (token,user_id,created,expires) VALUES (?,?,?,?)")
    .bind(token, id, created, created + SESSION_MS).run();
  return json({ ok: true, email }, 200, { "Set-Cookie": cookie("sid", token, Math.floor(SESSION_MS / 1000)) });
}
