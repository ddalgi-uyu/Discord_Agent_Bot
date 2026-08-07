package com.parking.reminder

import android.app.Application
import com.parking.reminder.data.RecentEntriesRepository
import com.parking.reminder.data.SettingsRepository
import com.parking.reminder.network.ApiClient
import com.parking.reminder.network.ParkingApi
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob

/**
 * Application class — manual DI container.
 *
 * Keeps the project free of Hilt/KSP overhead while still allowing the
 * ViewModel layer to receive its dependencies through a constructor.
 */
class ParkingReminderApp : Application() {

    /** Application-wide coroutine scope for fire-and-forget background work. */
    val applicationScope: CoroutineScope = CoroutineScope(SupervisorJob())

    /** DataStore-backed settings (backend URL, user_id, floor chip list). */
    val settingsRepository: SettingsRepository by lazy {
        SettingsRepository(applicationContext)
    }

    /** DataStore-backed recent-entries history. */
    val recentEntriesRepository: RecentEntriesRepository by lazy {
        RecentEntriesRepository(applicationContext)
    }

    @Volatile
    private var _parkingApi: ParkingApi =
        ApiClient.build(SettingsRepository.DEFAULT_BACKEND_URL)

    /** Latest API client, rebuilt when the backend URL changes. */
    val parkingApi: ParkingApi
        get() = _parkingApi

    /** Rebuilds the Retrofit client after a URL change. */
    fun rebuildApi(backendUrl: String) {
        _parkingApi = ApiClient.build(backendUrl)
    }

    override fun onCreate() {
        super.onCreate()
        instance = this
    }

    companion object {
        @Volatile
        private var instance: ParkingReminderApp? = null

        /** Convenience accessor used by the widget & Activity. */
        fun get(): ParkingReminderApp = requireNotNull(instance) {
            "ParkingReminderApp not initialised yet"
        }
    }
}
