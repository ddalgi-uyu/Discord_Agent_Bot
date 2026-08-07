package com.parking.reminder.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.parking.reminder.R
import com.parking.reminder.ui.theme.ParkingReminderTheme
import com.parking.reminder.viewmodel.QuickParkViewModel
import kotlinx.coroutines.launch

/**
 * Minimal settings screen:
 *   - Backend URL
 *   - User ID
 *   - Floor chip list (add / remove)
 *   - Reset to defaults
 *
 * Reuses [QuickParkViewModel] for the settings data so we avoid a second
 * ViewModel with identical dependencies. The screen is push-only — there is
 * no live save button, each field auto-persists on commit.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    onBack: () -> Unit,
    viewModel: QuickParkViewModel = viewModel(factory = QuickParkViewModel.Factory),
) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()
    val scope = rememberCoroutineScope()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.title_settings)) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(
                            imageVector = Icons.Filled.ArrowBack,
                            contentDescription = stringResource(R.string.cd_dismiss),
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background,
                ),
            )
        },
        containerColor = MaterialTheme.colorScheme.background,
    ) { innerPadding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 20.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(20.dp),
        ) {
            // Backend URL
            OutlinedTextField(
                value = state.backendUrl,
                onValueChange = { /* controlled by the explicit save action below */ },
                label = { Text(stringResource(R.string.settings_backend_url)) },
                placeholder = { Text(stringResource(R.string.settings_backend_url_hint)) },
                singleLine = true,
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.Uri,
                    imeAction = ImeAction.Done,
                ),
                shape = RoundedCornerShape(12.dp),
                modifier = Modifier.fillMaxWidth(),
            )
            // User ID
            OutlinedTextField(
                value = state.userId,
                onValueChange = { /* same — persisted on commit */ },
                label = { Text(stringResource(R.string.settings_user_id)) },
                singleLine = true,
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.Text,
                    imeAction = ImeAction.Done,
                    capitalization = KeyboardCapitalization.None,
                ),
                shape = RoundedCornerShape(12.dp),
                modifier = Modifier.fillMaxWidth(),
            )

            // Floor chip editor
            Text(
                text = stringResource(R.string.settings_floor_chips),
                style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            ChipEditor(
                chips = state.floorChips,
                onAdd = { chip ->
                    if (chip.isNotBlank() && chip !in state.floorChips) {
                        scope.launch {
                            viewModel.onFloorChipsChanged(state.floorChips + chip.trim())
                        }
                    }
                },
                onRemove = { chip ->
                    scope.launch {
                        viewModel.onFloorChipsChanged(state.floorChips - chip)
                    }
                },
            )
            TextButton(
                onClick = { scope.launch { viewModel.resetFloorChips() } },
                modifier = Modifier.align(Alignment.End),
            ) {
                Text(stringResource(R.string.settings_reset_defaults))
            }

            // Commit backend URL / user_id on tap.
            Button(
                onClick = {
                    scope.launch {
                        viewModel.onBackendUrlChanged(state.backendUrl)
                        viewModel.onUserIdChanged(state.userId)
                    }
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(min = 56.dp),
                shape = RoundedCornerShape(12.dp),
            ) {
                Text(
                    text = stringResource(R.string.settings_save),
                    style = MaterialTheme.typography.titleMedium,
                )
            }
            Spacer(modifier = Modifier.height(24.dp))
        }
    }
}

@Composable
private fun ChipEditor(
    chips: List<String>,
    onAdd: (String) -> Unit,
    onRemove: (String) -> Unit,
) {
    var newChip by remember { mutableStateOf("") }
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        LazyRow(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            contentPadding = PaddingValues(horizontal = 2.dp),
        ) {
            items(chips, key = { it }) { chip ->
                FilterChip(
                    selected = false,
                    onClick = { onRemove(chip) },
                    label = { Text(chip) },
                    trailingIcon = {
                        Icon(
                            imageVector = Icons.Filled.Close,
                            contentDescription = "Remove $chip",
                            modifier = Modifier.size(FilterChipDefaults.IconSize),
                        )
                    },
                    shape = RoundedCornerShape(12.dp),
                )
            }
        }
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                value = newChip,
                onValueChange = { newChip = it.take(6) },
                label = { Text(stringResource(R.string.settings_add_floor)) },
                singleLine = true,
                keyboardOptions = KeyboardOptions(
                    capitalization = KeyboardCapitalization.Characters,
                    imeAction = ImeAction.Done,
                ),
                shape = RoundedCornerShape(12.dp),
                modifier = Modifier.weight(1f),
            )
            IconButton(
                onClick = {
                    onAdd(newChip)
                    newChip = ""
                },
            ) {
                Icon(
                    imageVector = Icons.Filled.Add,
                    contentDescription = "Add floor",
                )
            }
        }
    }
}

@Composable
@androidx.compose.ui.tooling.preview.Preview
private fun SettingsScreenPreview() {
    ParkingReminderTheme {
        Scaffold { innerPadding ->
            Column(
                modifier = Modifier
                    .padding(innerPadding)
                    .padding(16.dp),
            ) {
                Text("Settings preview")
            }
        }
    }
}
