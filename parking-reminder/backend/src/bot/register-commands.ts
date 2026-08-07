/**
 * Stand-alone slash command registrar.
 *
 * Useful when you only want to push command definitions without spinning
 * up the REST server (e.g. CI deployments). Invoke via:
 *   `npm run bot:register`
 */
import * as dotenv from "dotenv";
import * as path from "node:path";
import * as fs from "node:fs";

const envPath = path.resolve(__dirname, "..", "..", ".env");
if (fs.existsSync(envPath)) {
  dotenv.config({ path: envPath });
} else {
  dotenv.config();
}

import { REST, Routes } from "discord.js";
import { loadCommands } from "./index";

async function main(): Promise<void> {
  const token = process.env.DISCORD_TOKEN;
  const clientId = process.env.DISCORD_CLIENT_ID;
  const guildId = process.env.DISCORD_GUILD_ID?.trim() || null;

  if (!token || !clientId) {
    console.error("DISCORD_TOKEN and DISCORD_CLIENT_ID must be set in .env");
    process.exit(1);
  }

  const commands = await loadCommands();
  const payload = Array.from(commands.values()).map((c) => c.data.toJSON());
  const rest = new REST({ version: "10" }).setToken(token);

  if (guildId) {
    const result = (await rest.put(
      Routes.applicationGuildCommands(clientId, guildId),
      { body: payload }
    )) as unknown[];
    console.log(
      `[register] Registered ${result.length} guild command(s) for ${guildId}`
    );
  } else {
    const result = (await rest.put(Routes.applicationCommands(clientId), {
      body: payload,
    })) as unknown[];
    console.log(`[register] Registered ${result.length} global command(s)`);
  }
}

main().catch((err) => {
  console.error("[register] Failed:", err);
  process.exit(1);
});
