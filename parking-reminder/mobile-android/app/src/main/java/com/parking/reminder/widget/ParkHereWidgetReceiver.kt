package com.parking.reminder.widget

import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetReceiver

/**
 * Broadcast receiver wired up in AndroidManifest.xml. Routes every widget
 * lifecycle event (added, enabled, updated, deleted, options-changed) to the
 * single [ParkHereWidget] instance.
 */
class ParkHereWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = ParkHereWidget()
}
