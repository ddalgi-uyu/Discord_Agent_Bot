/**
 * /park slash command — quick indoor recording.
 *
 * Usage: /park floor:<B3> slot:<27>
 *
 * Replies ephemerally so channel noise is minimised.
 */
import {
  SlashCommandBuilder,
  ChatInputCommandInteraction,
  MessageFlags,
} from "discord.js";
import { parkingService } from "../../lib/service";

export const data = new SlashCommandBuilder()
  .setName("park")
  .setDescription("Record an indoor parking spot (floor + slot).")
  .addStringOption((option) =>
    option
      .setName("floor")
      .setDescription("Parking floor (e.g. B3, 1F, P)")
      .setRequired(true)
      .setMaxLength(16)
  )
  .addStringOption((option) =>
    option
      .setName("slot")
      .setDescription("Parking slot / bay number")
      .setRequired(true)
      .setMaxLength(16)
  );

export async function execute(interaction: ChatInputCommandInteraction): Promise<void> {
  const userId = interaction.user.id;
  const floor = interaction.options.getString("floor", true).trim();
  const slot = interaction.options.getString("slot", true).trim();

  if (!floor || !slot) {
    await interaction.reply({
      content: "Both `floor` and `slot` must be non-empty.",
      flags: MessageFlags.Ephemeral,
    });
    return;
  }

  await interaction.deferReply({ flags: MessageFlags.Ephemeral });

  try {
    const record = await parkingService.createIndoor({ floor, slot, userId });
    await interaction.editReply(
      `\u2705 Saved! Your car is at **${record.locationNote}**`
    );
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    await interaction.editReply(
      `\u274C Failed to save parking record: ${message}`
    );
  }
}
