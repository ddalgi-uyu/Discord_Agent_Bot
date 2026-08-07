package com.parking.reminder.widget

import android.content.Context
import androidx.compose.runtime.Composable
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.GlanceTheme
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.action.actionRunCallback
import androidx.glance.appwidget.cornerRadius
import androidx.glance.appwidget.provideContent
import androidx.glance.background
import androidx.glance.layout.Alignment
import androidx.glance.layout.Box
import androidx.glance.layout.Column
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.layout.width
import androidx.glance.state.GlanceStateDefinition
import androidx.glance.state.PreferencesGlanceStateDefinition
import androidx.glance.text.FontWeight
import androidx.glance.text.Text
import androidx.glance.text.TextStyle
import com.parking.reminder.ParkingReminderApp
import com.parking.reminder.data.RecentEntry
import kotlinx.coroutines.flow.first

/**
 * One-tap "Park Here" home-screen widget.
 *
 * Layout:
 *   ┌─────────────────────────────────────┐
 *   │ 📌 Parking                          │
 *   │                                     │
 *   │  [   📌 Park Here   ]               │
 *   │                                     │
 *   │ Last: B3-27                         │
 *   └─────────────────────────────────────┘
 *
 * Tapping the button runs [ParkHereActionCallback]:
 *  - If we have a good GPS fix → silently POST + show a Toast.
 *  - If GPS is poor / unavailable / no permission → launch MainActivity
 *    (which always opens the Quick Park UI).
 *
 * Note: `dp` / `sp` / `TextUnit` come from `androidx.compose.ui.unit` —
 * `androidx.glance.unit` only contains ColorProvider; everything dimensional
 * delegates to the Compose unit types.
 */
class ParkHereWidget : GlanceAppWidget() {

    override val stateDefinition: GlanceStateDefinition<*> = PreferencesGlanceStateDefinition

    override suspend fun provideGlance(context: Context, id: GlanceId) {
        // Pull the last saved entry from the DataStore-backed recents. Doing
        // this in provideGlance (rather than at composition time) means the
        // widget refreshes its copy each time the system asks for a snapshot.
        val app = context.applicationContext as ParkingReminderApp
        val lastEntry = app.recentEntriesRepository.recentFlow.first().firstOrNull()
        val userId = app.settingsRepository.userIdFlow.first()

        provideContent {
            GlanceTheme {
                WidgetContent(
                    lastEntry = lastEntry,
                    userId = userId,
                )
            }
        }
    }

    @Composable
    private fun WidgetContent(
        lastEntry: RecentEntry?,
        userId: String,
    ) {
        Box(
            modifier = GlanceModifier
                .fillMaxSize()
                .background(GlanceTheme.colors.background)
                .cornerRadius(20.dp)
                .padding(16.dp),
        ) {
            Column(
                modifier = GlanceModifier.fillMaxSize(),
                horizontalAlignment = Alignment.Start,
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text = "📌",
                        style = TextStyle(
                            color = GlanceTheme.colors.onBackground,
                            fontSize = 18.sp,
                        ),
                    )
                    Spacer(modifier = GlanceModifier.width(6.dp))
                    Text(
                        text = "Parking",
                        style = TextStyle(
                            color = GlanceTheme.colors.onBackground,
                            fontWeight = FontWeight.Bold,
                            fontSize = 16.sp,
                        ),
                    )
                }

                Spacer(modifier = GlanceModifier.height(12.dp))

                // The "Park Here" button. Tapping it runs the action callback
                // which decides between silent-save and opening Quick Park.
                Box(
                    modifier = GlanceModifier
                        .fillMaxWidth()
                        .height(56.dp)
                        .background(GlanceTheme.colors.primary)
                        .cornerRadius(16.dp)
                        .clickable(
                            actionRunCallback<ParkHereActionCallback>(
                                parameters = ParkHereActionCallback.buildParams(userId = userId)
                            )
                        ),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        text = "📌 Park Here",
                        style = TextStyle(
                            color = GlanceTheme.colors.onPrimary,
                            fontWeight = FontWeight.Bold,
                            fontSize = 16.sp,
                        ),
                    )
                }

                Spacer(modifier = GlanceModifier.height(8.dp))

                // "Last: …" line. Falls back to "Tap to save" if no history.
                Text(
                    text = lastEntry?.let { "Last: ${it.displayLabel}" }
                        ?: "Tap to save",
                    style = TextStyle(
                        color = GlanceTheme.colors.onSurfaceVariant,
                        fontSize = 12.sp,
                    ),
                    maxLines = 1,
                )
            }
        }
    }
}
