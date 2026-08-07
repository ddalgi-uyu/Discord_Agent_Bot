/**
 * Discord bot entrypoint.
 *
 * - Registers slash commands with Discord (guild-scoped if DISCORD_GUILD_ID
 *   is set, otherwise global).
 * - Listens for `interactionCreate` events and dispatches them to the
 *   matching command's `execute` function.
 *
 * Designed to fail fast if DISCORD_TOKEN / DISCORD_CLIENT_ID are missing,
 * so misconfiguration is obvious during local development.
 */
import {
  Client,
  Events,
  GatewayIntentBits,
  REST,
  Routes,
  type ChatInputCommandInteraction,
  type SlashCommandBuilder,
} from "discord.js";
import * as path from "node:path";
import * as fs from "node:fs/promises";

interface CommandModule {
  data: SlashCommandBuilder;
  execute: (interaction: ChatInputCommandInteraction) => Promise<void> | void;
}

interface BotConfig {
  token: string;
  clientId: string;
  guildId: string | null;
}

function loadBotConfig(): BotConfig {
  const token = process.env.DISCORD_TOKEN;
  const clientId = process.env.DISCORD_CLIENT_ID;
  const guildId = process.env.DISCORD_GUILD_ID?.trim() || null;

  if (!token) {
    throw new Error("DISCORD_TOKEN is not set. Copy .env.example to .env first.");
  }
  if (!clientId) {
    throw new Error("DISCORD_CLIENT_ID is not set. Copy .env.example to .env first.");
  }

  return { token, clientId, guildId };
}

export async function loadCommands(): Promise<Map<string, CommandModule>> {
  const commandsDir = path.join(__dirname, "commands");
  const entries = await fs.readdir(commandsDir, { withFileTypes: true });
  const commands = new Map<string, CommandModule>();

  for (const entry of entries) {
    if (!entry.isFile()) continue;
    if (!entry.name.endsWith(".js") && !entry.name.endsWith(".ts")) continue;
    if (entry.name === "register-commands.js" || entry.name === "register-commands.ts") {
      continue;
    }

    const filePath = path.join(commandsDir, entry.name);
    // Bust require cache for hot-reload friendliness.
    delete require.cache[require.resolve(filePath)];
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const mod = require(filePath) as CommandModule;
    if (!mod?.data || !mod?.execute) {
      throw new Error(`Command module ${entry.name} is missing \`data\` or \`execute\``);
    }
    commands.set(mod.data.name, mod);
  }

  return commands;
}

async function registerSlashCommands(
  rest: REST,
  commands: Map<string, CommandModule>,
  config: BotConfig
): Promise<void> {
  const payload = Array.from(commands.values()).map((c) => c.data.toJSON());
  if (config.guildId) {
    await rest.put(
      Routes.applicationGuildCommands(config.clientId, config.guildId),
      { body: payload }
    );
    console.log(
      `[bot] Registered ${payload.length} guild command(s) for guild ${config.guildId}`
    );
  } else {
    await rest.put(Routes.applicationCommands(config.clientId), { body: payload });
    console.log(`[bot] Registered ${payload.length} global command(s)`);
  }
}

export async function startBot(): Promise<Client<true>> {
  const config = loadBotConfig();
  const commands = await loadCommands();

  const rest = new REST({ version: "10" }).setToken(config.token);
  await registerSlashCommands(rest, commands, config);

  const client = new Client({
    intents: [GatewayIntentBits.Guilds],
  });
  // Stash loaded commands on the client for the interaction dispatcher.
  (client as unknown as { commands: Map<string, CommandModule> }).commands =
    commands;

  client.once(Events.ClientReady, (c) => {
    console.log(`[bot] Logged in as ${c.user.tag} (id: ${c.user.id})`);
  });

  client.on(Events.InteractionCreate, async (interaction) => {
    if (!interaction.isChatInputCommand()) return;
    const commandMap = (client as unknown as {
      commands: Map<string, CommandModule>;
    }).commands;
    const command = commandMap.get(interaction.commandName);
    if (!command) {
      console.warn(`[bot] Unknown command received: ${interaction.commandName}`);
      return;
    }
    try {
      await command.execute(interaction);
    } catch (err) {
      console.error(`[bot] Error executing /${interaction.commandName}:`, err);
      const reply = {
        content: "There was an error while executing this command.",
        ephemeral: true,
      };
      try {
        if (interaction.deferred || interaction.replied) {
          await interaction.followUp(reply);
        } else {
          await interaction.reply(reply);
        }
      } catch {
        /* swallow — interaction may already be gone */
      }
    }
  });

  await client.login(config.token);
  return client as Client<true>;
}

if (require.main === module) {
  startBot().catch((err) => {
    console.error("[bot] Fatal error:", err);
    process.exit(1);
  });
}
