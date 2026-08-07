package com.parking.reminder.network

import com.squareup.moshi.JsonClass

/**
 * Request body sent to `POST /api/park`.
 *
 * Either (floor + slot) for indoor parking, or (latitude + longitude) for outdoor/GPS.
 * The backend treats these two pairs as mutually exclusive in practice, but
 * the contract allows both to be present (e.g. approximate floor + GPS).
 */
@JsonClass(generateAdapter = false)
data class ParkRequest(
    val floor: String? = null,
    val slot: String? = null,
    val latitude: Double? = null,
    val longitude: Double? = null,
    val is_indoor: Boolean,
    val user_id: String,
)

/**
 * Response body for `POST /api/park`.
 */
@JsonClass(generateAdapter = false)
data class ParkResponse(
    val id: String,
    val status: String,
)

/**
 * Response body for `GET /api/park/latest`.
 * `floor`/`slot` are present for indoor records, `latitude`/`longitude` for GPS ones.
 */
@JsonClass(generateAdapter = false)
data class LatestParkResponse(
    val id: String,
    val floor: String? = null,
    val slot: String? = null,
    val latitude: Double? = null,
    val longitude: Double? = null,
    val timestamp: String? = null,
    val is_indoor: Boolean = true,
)
