import { json, currentUser } from "./_auth.js";
export async function onRequestGet({ request, env }) {
  const u = await currentUser(request, env);
  return json({ user: u ? { email: u.email } : null });
}
