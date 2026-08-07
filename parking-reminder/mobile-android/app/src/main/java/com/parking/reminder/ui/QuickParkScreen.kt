package com.parking.reminder.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.InputChip
import androidx.compose.material3.InputChipDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.parking.reminder.R
import com.parking.reminder.data.RecentEntry
import com.parking.reminder.ui.theme.ParkingReminderTheme
import com.parking.reminder.viewmodel.GpsStatus
import com.parking.reminder.viewmodel.QuickParkUiState
import com.parking.reminder.viewmodel.QuickParkViewModel
import com.parking.reminder.viewmodel.SaveStatus

/**
 * Top-level entry point shown by [com.parking.reminder.MainActivity].
 *
 * Hosts the Quick Park UI directly (no menu, no home) so the user is one tap
 * away from saving a parking spot.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun QuickParkScreen(
    onOpenSettings: () -> Unit,
    onRequestLocationPermission: () -> Unit,
    viewModel: QuickParkViewModel = viewModel(factory = QuickParkViewModel.Factory),
) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }
    val keyboard = LocalSoftwareKeyboardController.current

    // Auto-show soft keyboard on first composition so the slot input is immediately tappable.
    LaunchedEffect(Unit) {
        keyboard?.show()
    }

    // Side-effects driven by the ViewModel's status transitions.
    LaunchedEffect(state.gpsStatus) {
        when (val s = state.gpsStatus) {
            GpsStatus.PermissionMissing -> {
                snackbarHostState.showSnackbar(
                    message = "Location permission needed for GPS mode",
                    duration = SnackbarDuration.Short,
                )
                onRequestLocationPermission()
            }
            is GpsStatus.Error -> {
                snackbarHostState.showSnackbar(
                    message = s.message,
                    duration = SnackbarDuration.Short,
                )
                viewModel.onGpsStatusConsumed()
            }
            else -> Unit
        }
    }
    LaunchedEffect(state.saveStatus) {
        when (val s = state.saveStatus) {
            is SaveStatus.Success -> {
                snackbarHostState.showSnackbar(
                    message = "Saved: ${s.entry.displayLabel}",
                    duration = SnackbarDuration.Short,
                )
                viewModel.onSaveStatusConsumed()
            }
            is SaveStatus.Error -> {
                snackbarHostState.showSnackbar(
                    message = s.message,
                    duration = SnackbarDuration.Short,
                )
                viewModel.onSaveStatusConsumed()
            }
            else -> Unit
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = stringResource(R.string.title_quick_park),
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.SemiBold,
                    )
                },
                actions = {
                    IconButton(onClick = onOpenSettings) {
                        Icon(
                            imageVector = Icons.Filled.Settings,
                            contentDescription = stringResource(R.string.cd_settings),
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background,
                ),
            )
        },
        snackbarHost = { SnackbarHost(snackbarHostState) },
        containerColor = MaterialTheme.colorScheme.background,
    ) { innerPadding ->
        QuickParkContent(
            state = state,
            innerPadding = innerPadding,
            onFloorChipTapped = viewModel::onFloorChipTapped,
            onCustomFloorConfirmed = viewModel::onCustomFloorConfirmed,
            onCustomFloorDialogDismissed = viewModel::onCustomFloorDialogDismissed,
            onSlotChanged = viewModel::onSlotChanged,
            onSaveClicked = {
                keyboard?.hide()
                viewModel.onSaveClicked()
            },
            onUseGpsClicked = {
                keyboard?.hide()
                viewModel.onUseGpsClicked()
            },
            onRecentTapped = viewModel::onRecentTapped,
            onRecentDismissed = viewModel::onRecentDismissed,
        )
    }
}

@Composable
private fun QuickParkContent(
    state: QuickParkUiState,
    innerPadding: PaddingValues,
    onFloorChipTapped: (String) -> Unit,
    onCustomFloorConfirmed: (String) -> Unit,
    onCustomFloorDialogDismissed: () -> Unit,
    onSlotChanged: (String) -> Unit,
    onSaveClicked: () -> Unit,
    onUseGpsClicked: () -> Unit,
    onRecentTapped: (RecentEntry) -> Unit,
    onRecentDismissed: (RecentEntry) -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(innerPadding)
            .imePadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        SectionLabel(text = stringResource(R.string.label_floor))
        FloorChipRow(
            chips = state.floorChips,
            selected = state.selectedFloor,
            onChipTapped = onFloorChipTapped,
        )

        SectionLabel(text = stringResource(R.string.label_slot))
        OutlinedTextField(
            value = state.slotInput,
            onValueChange = onSlotChanged,
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = 64.dp),
            placeholder = { Text(stringResource(R.string.hint_slot)) },
            singleLine = true,
            textStyle = MaterialTheme.typography.headlineMedium.copy(textAlign = TextAlign.Center),
            keyboardOptions = KeyboardOptions(
                keyboardType = KeyboardType.Number,
                imeAction = ImeAction.Done,
                capitalization = KeyboardCapitalization.Characters,
            ),
            shape = RoundedCornerShape(16.dp),
        )

        Spacer(modifier = Modifier.height(4.dp))

        Button(
            onClick = onSaveClicked,
            enabled = state.canSaveIndoor && state.saveStatus !is SaveStatus.Saving,
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = 64.dp),
            shape = RoundedCornerShape(16.dp),
            colors = ButtonDefaults.buttonColors(
                containerColor = MaterialTheme.colorScheme.primary,
                contentColor = MaterialTheme.colorScheme.onPrimary,
            ),
        ) {
            if (state.saveStatus is SaveStatus.Saving) {
                CircularProgressIndicator(
                    modifier = Modifier.size(24.dp),
                    strokeWidth = 3.dp,
                    color = MaterialTheme.colorScheme.onPrimary,
                )
            } else {
                Text(
                    text = stringResource(R.string.btn_save_park),
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                )
            }
        }

        TextButton(
            onClick = onUseGpsClicked,
            enabled = state.gpsStatus !is GpsStatus.Fetching,
            modifier = Modifier.fillMaxWidth(),
        ) {
            if (state.gpsStatus is GpsStatus.Fetching) {
                CircularProgressIndicator(
                    modifier = Modifier.size(20.dp),
                    strokeWidth = 2.dp,
                )
                Spacer(modifier = Modifier.size(8.dp))
            }
            Text(
                text = stringResource(R.string.btn_use_gps),
                style = MaterialTheme.typography.titleMedium,
            )
        }

        if (state.recentEntries.isNotEmpty()) {
            Spacer(modifier = Modifier.height(8.dp))
            SectionLabel(text = stringResource(R.string.recent_section_title))
            RecentEntriesRow(
                entries = state.recentEntries,
                onTapped = onRecentTapped,
                onDismissed = onRecentDismissed,
            )
        }

        Spacer(modifier = Modifier.height(24.dp))
    }

    if (state.showCustomFloorDialog) {
        CustomFloorDialog(
            onConfirm = onCustomFloorConfirmed,
            onDismiss = onCustomFloorDialogDismissed,
        )
    }
}

@Composable
private fun SectionLabel(text: String) {
    Text(
        text = text,
        style = MaterialTheme.typography.labelLarge,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

@Composable
private fun FloorChipRow(
    chips: List<String>,
    selected: String?,
    onChipTapped: (String) -> Unit,
) {
    LazyRow(
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        contentPadding = PaddingValues(horizontal = 2.dp),
    ) {
        items(chips, key = { it }) { chip ->
            FloorChip(
                label = chip,
                selected = chip == selected,
                onClick = { onChipTapped(chip) },
            )
        }
        // Trailing chip is always the custom-floor sentinel "…".
        item(key = QuickParkViewModel.CUSTOM_FLOOR_SENTINEL) {
            FloorChip(
                label = QuickParkViewModel.CUSTOM_FLOOR_SENTINEL,
                selected = false,
                onClick = { onChipTapped(QuickParkViewModel.CUSTOM_FLOOR_SENTINEL) },
            )
        }
    }
}

@Composable
private fun FloorChip(
    label: String,
    selected: Boolean,
    onClick: () -> Unit,
) {
    FilterChip(
        selected = selected,
        onClick = onClick,
        label = {
            Text(
                text = label,
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
            )
        },
        shape = RoundedCornerShape(12.dp),
        colors = FilterChipDefaults.filterChipColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant,
            selectedContainerColor = MaterialTheme.colorScheme.primary,
            selectedLabelColor = MaterialTheme.colorScheme.onPrimary,
        ),
        modifier = Modifier.heightIn(min = 48.dp),
    )
}

@Composable
private fun RecentEntriesRow(
    entries: List<RecentEntry>,
    onTapped: (RecentEntry) -> Unit,
    onDismissed: (RecentEntry) -> Unit,
) {
    LazyRow(
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        contentPadding = PaddingValues(horizontal = 2.dp),
    ) {
        items(entries, key = { it.id }) { entry ->
            InputChip(
                selected = false,
                onClick = { onTapped(entry) },
                label = { Text(entry.displayLabel, fontWeight = FontWeight.Medium) },
                trailingIcon = {
                    IconButton(
                        onClick = { onDismissed(entry) },
                        modifier = Modifier.size(InputChipDefaults.IconSize),
                    ) {
                        Icon(
                            imageVector = Icons.Filled.Close,
                            contentDescription = stringResource(R.string.cd_remove_recent),
                            tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                },
                shape = RoundedCornerShape(12.dp),
            )
        }
    }
}

@Composable
private fun CustomFloorDialog(
    onConfirm: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    var text by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(stringResource(R.string.dialog_custom_floor_title)) },
        text = {
            OutlinedTextField(
                value = text,
                onValueChange = { text = it.take(QuickParkViewModel.MAX_FLOOR_CHARS) },
                label = { Text(stringResource(R.string.dialog_custom_floor_label)) },
                singleLine = true,
                keyboardOptions = KeyboardOptions(
                    capitalization = KeyboardCapitalization.Characters,
                    imeAction = ImeAction.Done,
                ),
            )
        },
        confirmButton = {
            TextButton(onClick = { onConfirm(text) }) {
                Text(stringResource(R.string.dialog_ok))
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text(stringResource(R.string.dialog_cancel))
            }
        },
    )
}

@Preview(showBackground = true, widthDp = 360, heightDp = 720)
@Composable
private fun QuickParkScreenPreview() {
    ParkingReminderTheme {
        Surface(modifier = Modifier.fillMaxSize()) {
            // Preview is rendered with stub state for visual inspection only;
            // the actual UI is wired through the ViewModel.
            Box(
                modifier = Modifier.fillMaxSize(),
                contentAlignment = Alignment.Center,
            ) {
                Text("Quick Park UI")
            }
        }
    }
}
