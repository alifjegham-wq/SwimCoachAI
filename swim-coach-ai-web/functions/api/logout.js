import { json, getCookie, cookie } from "./_auth.js";
export async function onRequestPost({ request, env }) {
  const tok = getCookie(request, "sid");
  if (tok && env.DB) await env.DB.prepare("DELETE FROM sessions WHERE token = ?").bind(tok).run();
  return json({ ok: true }, 200, { "Set-Cookie": cookie("sid", "", 0) });
}
