# Add project specific ProGuard rules here.
# You can control the set of applied configuration files using the
# proguardFiles setting in build.gradle.

# Keep data classes used by Retrofit / Moshi
-keep class com.parking.reminder.network.** { *; }
-keepclassmembers class **.*JsonAdapter { *; }

# Glance widget classes
-keep class androidx.glance.** { *; }
