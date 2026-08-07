/**
 * /whereiscar slash command.
 *
 * Replies with the latest parking record stored for the user who
 * invoked the command (we key on their Discord snowflake).
 */
import {
  SlashCommandBuilder,
  ChatInputCommandInteraction,
  EmbedBuilder,
  MessageFlags,
} from "discord.js";
import { parkingService } from "../../lib/service";
import { formatRelativeTime } from "../../lib/parking";

export const data = new SlashCommandBuilder()
  .setName("whereiscar")
  .setDescription("Look up the most recent parking record you saved.");

export async function execute(interaction: ChatInputCommandInteraction): Promise<void> {
  const userId = interaction.user.id;

  // Defer so we can do DB I/O without hitting the 3s interaction window.
  await interaction.deferReply({ flags: MessageFlags.Ephemeral });

  const record = await parkingService.getLatest(userId);
  if (!record) {
    await interaction.editReply("\u{1F937} No parking record found.");
    return;
  }

  const relative = formatRelativeTime(record.createdAt);
  const embed = new EmbedBuilder()
    .setTitle("\u{1F17F} Where is your car?")
    .setColor(0x2b6cb0)
    .setTimestamp(record.createdAt);

  if (record.isIndoor && record.locationNote) {
    embed.setDescription(
      `Your car is at **${record.locationNote}** (saved ${relative})`
    );
  } else if (!record.isIndoor && record.locationNote) {
    embed.setDescription(
      `Your car is at [Google Maps](${record.locationNote}) (saved ${relative})`
    );
  } else {
    embed.setDescription(`Saved ${relative} (no location data).`);
  }

  embed.addFields(
    {
      name: "Type",
      value: record.isIndoor ? "Indoor" : "Outdoor (GPS)",
      inline: true,
    },
    {
      name: "Saved at",
      value: record.createdAt.toUTCString(),
      inline: true,
    }
  );

  await interaction.editReply({ embeds: [embed] });
}
