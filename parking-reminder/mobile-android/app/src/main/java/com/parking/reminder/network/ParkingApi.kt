package com.parking.reminder.network

import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Query

/**
 * Backend REST contract. See /docs/api_spec.md (kept in the parking-reminder repo)
 * for the canonical definition.
 */
interface ParkingApi {

    /**
     * Record a parking spot — indoor (floor + slot) or outdoor (lat/lng).
     * Returns the server-assigned id.
     */
    @POST("api/park")
    suspend fun savePark(@Body request: ParkRequest): ParkResponse

    /**
     * Fetch the most recent record for a user. Returns 404 if none exists;
     * Retrofit will surface that as an HttpException the caller can handle.
     */
    @GET("api/park/latest")
    suspend fun getLatest(@Query("user_id") userId: String): LatestParkResponse
}
