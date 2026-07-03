import { json, randHex, hashPassword, safeEqual, cookie, SESSION_MS } from "./_auth.js";
export async function onRequestPost({ request, env }) {
  if (!env.DB) return json({ error: "Accounts are not configured (no D1 database bound)." }, 501);
  let body;
  try { body = await request.json(); } catch { return json({ error: "bad_json" }, 400); }
  const email = (body.email || "").trim().toLowerCase();
  const password = body.password || "";
  const u = await env.DB.prepare("SELECT id, pass_hash, salt FROM users WHERE email = ?").bind(email).first();
  if (!u) return json({ error: "Incorrect email or password." }, 401);
  const ph = await hashPassword(password, u.salt);
  if (!safeEqual(ph, u.pass_hash)) return json({ error: "Incorrect email or password." }, 401);
  const token = randHex(32), now = Date.now();
  await env.DB.prepare("INSERT INTO sessions (token,user_id,created,expires) VALUES (?,?,?,?)")
    .bind(token, u.id, now, now + SESSION_MS).run();
  return json({ ok: true, email }, 200, { "Set-Cookie": cookie("sid", token, Math.floor(SESSION_MS / 1000)) });
}
