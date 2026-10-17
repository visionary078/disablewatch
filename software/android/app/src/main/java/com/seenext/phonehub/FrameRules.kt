package com.seenext.phonehub

/** Same rules as software/android/hub_policy.py. */
object FrameRules {
    const val DROP = "drop"
    const val STORE = "store"
    const val FORWARD = "forward"

    fun frameAction(busy: Boolean, hasSpoken: Boolean, cameraOn: Boolean): String {
        if (busy) return DROP
        if (hasSpoken || cameraOn) return FORWARD
        return STORE
    }

    /** Same window as software/android/hub_policy.py fresh_tof_mm. */
    fun freshTofMm(mm: Int, ageMs: Long): String {
        if (mm <= 0 || mm > 8000) return ""
        if (ageMs < 0 || ageMs > 3000) return ""
        return mm.toString()
    }
}
