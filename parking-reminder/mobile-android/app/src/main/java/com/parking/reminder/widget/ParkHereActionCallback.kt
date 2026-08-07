package com.parking.reminder.widget

import android.content.Context
import android.content.Intent
import android.widget.Toast
import androidx.glance.GlanceId
import androidx.glance.action.ActionParameters
import androidx.glance.appwidget.GlanceAppWidgetManager
import androidx.glance.appwidget.action.ActionCallback
import androidx.glance.appwidget.updateAll
import com.parking.reminder.MainActivity
import com.parking.reminder.ParkingReminderApp
import com.parking.reminder.data.LocationProvider
import com.parking.reminder.data.RecentEntry
import com.parking.reminder.network.ParkRequest
import kotlinx.coroutines.flow.first
import java.util.UUID

/**
 * Action fired by the "Park Here" button in [ParkHereWidget].
 *
 * Decision tree:
 *   - Permission missing           → open MainActivity (which prompts the user).
 *   - GPS accuracy > 30m (indoor)  → open MainActivity (Quick Park UI).
 *   - GPS accuracy good            → POST to backend, save to recents, show
 *                                    "Saved!" toast, update widget.
 */
class ParkHereActionCallback : ActionCallback {

    override suspend fun onAction(
        context: Context,
        glanceId: GlanceId,
        parameters: ActionParameters,
    ) {
        val app = context.applicationContext as ParkingReminderApp
        val userId = parameters[KEY_USER_ID] ?: app.settingsRepository.userIdFlow.first()

        val locationProvider = LocationProvider(app)

        if (!locationProvider.hasFineLocationPermission()) {
            openMainActivity(context)
            return
        }

        val location = runCatching { locationProvider.currentLocation() }.getOrNull()
        if (location == null) {
            openMainActivity(context)
            return
        }

        val accuracyMeters = location.accuracy
        if (!accuracyMeters.isFinite() || accuracyMeters > LocationProvider.AccuracyThreshold.METERS) {
            openMainActivity(context)
            return
        }

        // Silent save.
        val outcome = runCatching {
            app.parkingApi.savePark(
                ParkRequest(
                    latitude = location.latitude,
                    longitude = location.longitude,
                    is_indoor = false,
                    user_id = userId,
                )
            )
        }
        outcome.fold(
            onSuccess = { resp ->
                app.recentEntriesRepository.add(
                    RecentEntry(
                        id = resp.id.ifBlank { UUID.randomUUID().toString() },
                        floor = "",
                        slot = "",
                        isIndoor = false,
                        latitude = location.latitude,
                        longitude = location.longitude,
                    )
                )
                Toast.makeText(context, "Saved!", Toast.LENGTH_SHORT).show()
            },
            onFailure = { t ->
                Toast.makeText(
                    context,
                    "Save failed: ${t.message ?: "network error"}",
                    Toast.LENGTH_LONG,
                ).show()
            },
        )

        // Refresh the widget so the "Last:" line shows the new entry.
        ParkHereWidget().updateAll(context)
    }

    private fun openMainActivity(context: Context) {
        val intent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        context.startActivity(intent)
    }

    companion object {
        val KEY_USER_ID = ActionParameters.Key<String>("user_id")

        fun buildParams(userId: String): ActionParameters =
            androidx.glance.action.ActionParameters.actionParametersOf(KEY_USER_ID to userId)
    }
}
