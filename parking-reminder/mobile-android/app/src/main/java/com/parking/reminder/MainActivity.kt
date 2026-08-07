package com.parking.reminder

import android.Manifest
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.parking.reminder.ui.QuickParkScreen
import com.parking.reminder.ui.SettingsScreen
import com.parking.reminder.ui.theme.ParkingReminderTheme
import com.parking.reminder.viewmodel.QuickParkViewModel

/**
 * Single activity — opens DIRECTLY to the Quick Park UI (no splash, no menu).
 *
 * Hosts a tiny two-screen NavHost: `quick_park` (start destination) and `settings`.
 *
 * Owns the OS-level location-permission launcher and bridges the result back to
 * [QuickParkViewModel] via its `onGpsPermissionGranted / Denied` hooks.
 */
class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            ParkingReminderTheme {
                ParkingReminderApp()
            }
        }
    }
}

/**
 * Top-level composable for the app. Kept at file scope so the permission
 * launcher can be remembered once and shared across recompositions.
 */
@Composable
private fun ParkingReminderApp() {
    val nav = rememberNavController()
    val viewModel: QuickParkViewModel = viewModel(factory = QuickParkViewModel.Factory)

    val permissionLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) viewModel.onGpsPermissionGranted() else viewModel.onGpsPermissionDenied()
    }

    val onRequestLocationPermission: () -> Unit = {
        permissionLauncher.launch(Manifest.permission.ACCESS_FINE_LOCATION)
    }

    NavHost(navController = nav, startDestination = "quick_park") {
        composable("quick_park") {
            QuickParkScreen(
                onOpenSettings = { nav.navigate("settings") },
                onRequestLocationPermission = onRequestLocationPermission,
                viewModel = viewModel,
            )
        }
        composable("settings") {
            SettingsScreen(
                onBack = {
                    if (!nav.popBackStack()) {
                        nav.navigate("quick_park") {
                            popUpTo("quick_park") { inclusive = true }
                        }
                    }
                },
                viewModel = viewModel,
            )
        }
    }
}
