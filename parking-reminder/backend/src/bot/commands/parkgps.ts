/**
 * /parkgps slash command — record an outdoor GPS location.
 *
 * Usage: /parkgps latitude:<25.03> longitude:<121.57>
 *
 * Replies with the resulting Google Maps link.
 */
import {
  SlashCommandBuilder,
  ChatInputCommandInteraction,
  MessageFlags,
} from "discord.js";
import { parkingService } from "../../lib/service";

export const data = new SlashCommandBuilder()
  .setName("parkgps")
  .setDescription("Record an outdoor parking spot using GPS coordinates.")
  .addNumberOption((option) =>
    option
      .setName("latitude")
      .setDescription("Latitude (decimal degrees, -90 to 90)")
      .setRequired(true)
      .setMinValue(-90)
      .setMaxValue(90)
  )
  .addNumberOption((option) =>
    option
      .setName("longitude")
      .setDescription("Longitude (decimal degrees, -180 to 180)")
      .setRequired(true)
      .setMinValue(-180)
      .setMaxValue(180)
  );

export async function execute(interaction: ChatInputCommandInteraction): Promise<void> {
  const userId = interaction.user.id;
  const latitude = interaction.options.getNumber("latitude", true);
  const longitude = interaction.options.getNumber("longitude", true);

  await interaction.deferReply({ flags: MessageFlags.Ephemeral });

  try {
    const record = await parkingService.createOutdoor({
      latitude,
      longitude,
      userId,
    });
    await interaction.editReply(
      `\u2705 Saved! Your car is at [Google Maps](${record.locationNote})`
    );
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    await interaction.editReply(
      `\u274C Failed to save parking record: ${message}`
    );
  }
}
