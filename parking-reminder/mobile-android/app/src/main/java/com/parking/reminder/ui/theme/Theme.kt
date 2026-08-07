package com.parking.reminder.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable

private val LightColors = lightColorScheme(
    primary = BrandAccent,
    onPrimary = BrandSurface,
    primaryContainer = BrandSurfaceVariant,
    onPrimaryContainer = BrandOnSurface,
    secondary = BrandAccentDark,
    onSecondary = BrandSurface,
    background = BrandSurface,
    onBackground = BrandOnSurface,
    surface = BrandSurface,
    onSurface = BrandOnSurface,
    surfaceVariant = BrandSurfaceVariant,
    onSurfaceVariant = BrandOnSurfaceMuted,
    error = BrandError,
    onError = BrandSurface,
)

private val DarkColors = darkColorScheme(
    primary = BrandAccent,
    onPrimary = BrandSurface,
    primaryContainer = BrandAccentDark,
    onPrimaryContainer = BrandSurface,
    secondary = BrandAccentDark,
    onSecondary = BrandSurface,
    background = BrandOnSurface,
    onBackground = BrandSurface,
    surface = BrandOnSurface,
    onSurface = BrandSurface,
    surfaceVariant = BrandSurfaceVariant,
    onSurfaceVariant = BrandOnSurfaceMuted,
    error = BrandError,
    onError = BrandSurface,
)

@Composable
fun ParkingReminderTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    MaterialTheme(
        colorScheme = if (darkTheme) DarkColors else LightColors,
        typography = AppTypography,
        content = content,
    )
}
