package com.parking.reminder.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.parking.reminder.ParkingReminderApp
import com.parking.reminder.data.LocationProvider
import com.parking.reminder.data.RecentEntriesRepository
import com.parking.reminder.data.RecentEntry
import com.parking.reminder.data.SettingsRepository
import com.parking.reminder.network.ParkRequest
import com.parking.reminder.network.ParkingApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.UUID

/**
 * UI state for the Quick Park screen.
 *
 * Designed to be observed by Compose via [collectAsStateWithLifecycle].
 * Mutations go through the ViewModel so all writes stay centralised.
 */
data class QuickParkUiState(
    val floorChips: List<String> = SettingsRepository.DEFAULT_FLOOR_CHIPS,
    val selectedFloor: String? = null,
    val slotInput: String = "",
    val showCustomFloorDialog: Boolean = false,
    val saveStatus: SaveStatus = SaveStatus.Idle,
    val gpsStatus: GpsStatus = GpsStatus.Idle,
    val recentEntries: List<RecentEntry> = emptyList(),
    val backendUrl: String = SettingsRepository.DEFAULT_BACKEND_URL,
    val userId: String = SettingsRepository.DEFAULT_USER_ID,
) {
    val canSaveIndoor: Boolean
        get() = !selectedFloor.isNullOrBlank() && slotInput.isNotBlank() &&
            saveStatus !is SaveStatus.Saving
}

/** Status of an indoor or GPS save action. */
sealed interface SaveStatus {
    data object Idle : SaveStatus
    data object Saving : SaveStatus
    data class Success(val entry: RecentEntry) : SaveStatus
    data class Error(val message: String) : SaveStatus
}

/** Status of the GPS "Use GPS Instead" flow. */
sealed interface GpsStatus {
    data object Idle : GpsStatus
    data object Fetching : GpsStatus
    data object PermissionMissing : GpsStatus
    data class Error(val message: String) : GpsStatus
}

/**
 * Owns the state and side-effects for the Quick Park screen.
 *
 * Constructor injection keeps the project free of Hilt/KSP — a small factory
 * in [companion] passes the dependencies in from [ParkingReminderApp].
 */
class QuickParkViewModel(
    application: Application,
    private val settings: SettingsRepository,
    private val recentEntries: RecentEntriesRepository,
    private val locationProvider: LocationProvider,
    private val apiProvider: () -> ParkingApi,
) : AndroidViewModel(application) {

    private val _uiState = MutableStateFlow(QuickParkUiState())
    val uiState: StateFlow<QuickParkUiState> = _uiState.asStateFlow()

    init {
        // Stream settings & recent-entries into the UI state. `combine` keeps
        // the emission rate low and predictable.
        viewModelScope.launch {
            combine(
                settings.floorChipsFlow,
                settings.backendUrlFlow,
                settings.userIdFlow,
                recentEntries.recentFlow,
            ) { chips, url, userId, recents ->
                Quad(chips, url, userId, recents)
            }.collect { (chips, url, userId, recents) ->
                _uiState.update {
                    it.copy(
                        floorChips = chips,
                        backendUrl = url,
                        userId = userId,
                        recentEntries = recents,
                    )
                }
            }
        }
    }

    // --- floor selection -------------------------------------------------

    fun onFloorChipTapped(label: String) {
        if (label == CUSTOM_FLOOR_SENTINEL) {
            _uiState.update { it.copy(showCustomFloorDialog = true) }
        } else {
            _uiState.update { it.copy(selectedFloor = label) }
        }
    }

    fun onCustomFloorConfirmed(label: String) {
        val trimmed = label.trim().take(MAX_FLOOR_CHARS)
        if (trimmed.isNotEmpty()) {
            _uiState.update {
                it.copy(
                    selectedFloor = trimmed,
                    showCustomFloorDialog = false,
                )
            }
        } else {
            _uiState.update { it.copy(showCustomFloorDialog = false) }
        }
    }

    fun onCustomFloorDialogDismissed() {
        _uiState.update { it.copy(showCustomFloorDialog = false) }
    }

    // --- slot input ------------------------------------------------------

    fun onSlotChanged(text: String) {
        // Keep uppercase letter prefixes (e.g. "A15") but cap length to a sane value.
        val sanitized = text.take(MAX_SLOT_CHARS)
        _uiState.update { it.copy(slotInput = sanitized) }
    }

    // --- save (manual indoor entry) --------------------------------------

    fun onSaveClicked() {
        val state = _uiState.value
        val floor = state.selectedFloor
        val slot = state.slotInput.trim()
        if (floor.isNullOrBlank() || slot.isBlank()) {
            _uiState.update {
                it.copy(
                    saveStatus = SaveStatus.Error(
                        if (floor.isNullOrBlank()) "Pick a floor first" else "Type a slot number"
                    )
                )
            }
            return
        }
        viewModelScope.launch {
            _uiState.update { it.copy(saveStatus = SaveStatus.Saving) }
            persistIndoor(floor = floor, slot = slot)
        }
    }

    private suspend fun persistIndoor(floor: String, slot: String) {
        val api = apiProvider()
        val userId = _uiState.value.userId
        val req = ParkRequest(
            floor = floor,
            slot = slot,
            is_indoor = true,
            user_id = userId,
        )
        val outcome = withContext(Dispatchers.IO) {
            runCatching { api.savePark(req) }
        }
        outcome.fold(
            onSuccess = { resp ->
                val entry = RecentEntry(
                    id = resp.id.ifBlank { UUID.randomUUID().toString() },
                    floor = floor,
                    slot = slot,
                    isIndoor = true,
                )
                recentEntries.add(entry)
                _uiState.update {
                    it.copy(
                        saveStatus = SaveStatus.Success(entry),
                        // Clear slot so the next save needs only a new number.
                        slotInput = "",
                    )
                }
            },
            onFailure = { t ->
                _uiState.update {
                    it.copy(saveStatus = SaveStatus.Error(t.message ?: "Network error"))
                }
            },
        )
    }

    // --- save (GPS) ------------------------------------------------------

    fun onUseGpsClicked() {
        if (!locationProvider.hasFineLocationPermission()) {
            _uiState.update { it.copy(gpsStatus = GpsStatus.PermissionMissing) }
            return
        }
        viewModelScope.launch {
            _uiState.update { it.copy(gpsStatus = GpsStatus.Fetching) }
            val outcome = withContext(Dispatchers.IO) {
                runCatching { locationProvider.currentLocation() }
            }
            outcome.fold(
                onSuccess = { loc ->
                    persistGps(loc.latitude, loc.longitude)
                },
                onFailure = { t ->
                    _uiState.update {
                        it.copy(
                            gpsStatus = GpsStatus.Error(
                                t.message ?: "Couldn't get location"
                            )
                        )
                    }
                },
            )
        }
    }

    fun onGpsPermissionGranted() {
        // Re-trigger the GPS flow now that the OS-level dialog succeeded.
        onUseGpsClicked()
    }

    fun onGpsPermissionDenied() {
        _uiState.update { it.copy(gpsStatus = GpsStatus.PermissionMissing) }
    }

    fun onGpsStatusConsumed() {
        _uiState.update { it.copy(gpsStatus = GpsStatus.Idle) }
    }

    private suspend fun persistGps(lat: Double, lng: Double) {
        val api = apiProvider()
        val userId = _uiState.value.userId
        val req = ParkRequest(
            latitude = lat,
            longitude = lng,
            is_indoor = false,
            user_id = userId,
        )
        val outcome = withContext(Dispatchers.IO) {
            runCatching { api.savePark(req) }
        }
        outcome.fold(
            onSuccess = { resp ->
                val entry = RecentEntry(
                    id = resp.id.ifBlank { UUID.randomUUID().toString() },
                    floor = "",
                    slot = "",
                    isIndoor = false,
                    latitude = lat,
                    longitude = lng,
                )
                recentEntries.add(entry)
                _uiState.update {
                    it.copy(
                        gpsStatus = GpsStatus.Idle,
                        saveStatus = SaveStatus.Success(entry),
                    )
                }
            },
            onFailure = { t ->
                _uiState.update {
                    it.copy(
                        gpsStatus = GpsStatus.Error(t.message ?: "Network error")
                    )
                }
            },
        )
    }

    // --- recent entries --------------------------------------------------

    fun onRecentTapped(entry: RecentEntry) {
        if (!entry.isIndoor) {
            // One-tap GPS repeat: re-save with the same coordinates.
            viewModelScope.launch {
                _uiState.update { it.copy(saveStatus = SaveStatus.Saving) }
                persistGps(
                    lat = entry.latitude ?: return@launch,
                    lng = entry.longitude ?: return@launch,
                )
            }
            return
        }
        // One-tap indoor repeat: just save with the same floor/slot.
        viewModelScope.launch {
            _uiState.update { it.copy(saveStatus = SaveStatus.Saving) }
            persistIndoor(floor = entry.floor, slot = entry.slot)
        }
    }

    fun onRecentDismissed(entry: RecentEntry) {
        viewModelScope.launch { recentEntries.remove(entry.id) }
    }

    fun onSaveStatusConsumed() {
        _uiState.update { it.copy(saveStatus = SaveStatus.Idle) }
    }

    // --- settings updates ------------------------------------------------

    suspend fun onBackendUrlChanged(url: String) {
        settings.setBackendUrl(url)
        // Rebuild API client so the next call uses the new base URL.
        (getApplication<Application>() as ParkingReminderApp).rebuildApi(url)
    }

    suspend fun onUserIdChanged(userId: String) {
        settings.setUserId(userId)
    }

    suspend fun onFloorChipsChanged(chips: List<String>) {
        settings.setFloorChips(chips)
    }

    suspend fun resetFloorChips() {
        settings.resetFloorChipsToDefaults()
    }

    companion object {
        const val CUSTOM_FLOOR_SENTINEL = "…"
        const val MAX_SLOT_CHARS = 8
        const val MAX_FLOOR_CHARS = 6

        /** Helper for `combine` of 4 sources — needed because Kotlin flow's
         *  built-in overloads only go up to 5 and use named args only from 5+. */
        data class Quad<A, B, C, D>(val a: A, val b: B, val c: C, val d: D)

        val Factory: ViewModelProvider.Factory = viewModelFactory {
            initializer {
                val app = ParkingReminderApp.get()
                QuickParkViewModel(
                    application = app,
                    settings = app.settingsRepository,
                    recentEntries = app.recentEntriesRepository,
                    locationProvider = LocationProvider(app),
                    apiProvider = { app.parkingApi },
                )
            }
        }
    }
}
