// User-scoped progress history (mirrors the local helper's /api/history contract).
import { json, currentUser } from "./_auth.js";

export async function onRequestGet({ request, env }) {
  const u = await currentUser(request, env);
  if (!u) return json({ error: "auth", sessions: [] }, 401);
  const row = await env.DB.prepare("SELECT history_json FROM user_data WHERE user_id = ?").bind(u.id).first();
  let sessions = [];
  try { sessions = JSON.parse((row && row.history_json) || "[]"); } catch {}
  return json({ sessions });
}

export async function onRequestPost({ request, env }) {
  const u = await currentUser(request, env);
  if (!u) return json({ error: "auth" }, 401);
  let body;
  try { body = await request.json(); } catch { return json({ error: "bad_json" }, 400); }
  const row = await env.DB.prepare("SELECT history_json FROM user_data WHERE user_id = ?").bind(u.id).first();
  let rows = [];
  try { rows = JSON.parse((row && row.history_json) || "[]"); } catch {}
  if (body.clear) rows = [];
  else if (body.session) { rows.push(body.session); rows = rows.slice(-200); }
  await env.DB.prepare(
    "INSERT INTO user_data (user_id, history_json, updated) VALUES (?,?,?) " +
    "ON CONFLICT(user_id) DO UPDATE SET history_json = excluded.history_json, updated = excluded.updated"
  ).bind(u.id, JSON.stringify(rows), Date.now()).run();
  return json({ ok: true, sessions: rows });
}
