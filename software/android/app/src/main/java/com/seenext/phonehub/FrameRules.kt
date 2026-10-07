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
}
