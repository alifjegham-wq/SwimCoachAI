// Environment probe used by the client to detect hosted mode and account availability.
import { json, currentUser } from "./_auth.js";
export async function onRequestGet({ request, env }) {
  const u = await currentUser(request, env);
  return json({
    hosted: true,
    hasKey: false,            // BYO-key lives in the browser (localStorage), not on the server
    hasPasscode: false,       // online, the account login is the gate
    accounts: !!env.DB,       // true once a D1 database is bound
    user: u ? { email: u.email } : null,
  });
}
