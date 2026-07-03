// User-scoped swimmer profiles (mirrors the local helper's /api/profiles contract).
import { json, currentUser } from "./_auth.js";

export async function onRequestGet({ request, env }) {
  const u = await currentUser(request, env);
  if (!u) return json({ profiles: [], active: null }, 401);
  const row = await env.DB.prepare("SELECT profiles_json FROM user_data WHERE user_id = ?").bind(u.id).first();
  let obj = { profiles: [], active: null };
  try { obj = JSON.parse((row && row.profiles_json) || '{"profiles":[],"active":null}'); } catch {}
  return json(obj);
}

export async function onRequestPost({ request, env }) {
  const u = await currentUser(request, env);
  if (!u) return json({ error: "auth" }, 401);
  let body;
  try { body = await request.json(); } catch { return json({ error: "bad_json" }, 400); }
  const row = await env.DB.prepare("SELECT profiles_json FROM user_data WHERE user_id = ?").bind(u.id).first();
  let obj = { profiles: [], active: null };
  try { obj = JSON.parse((row && row.profiles_json) || '{"profiles":[],"active":null}'); } catch {}
  if (body.clear) obj = { profiles: [], active: null };
  else {
    if (Array.isArray(body.profiles)) obj.profiles = body.profiles.slice(0, 200);
    if ("active" in body) obj.active = body.active;
  }
  await env.DB.prepare(
    "INSERT INTO user_data (user_id, profiles_json, updated) VALUES (?,?,?) " +
    "ON CONFLICT(user_id) DO UPDATE SET profiles_json = excluded.profiles_json, updated = excluded.updated"
  ).bind(u.id, JSON.stringify(obj), Date.now()).run();
  return json({ ok: true, ...obj });
}
